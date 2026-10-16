"""Read APP-VER from the already owned device's fixed Bilibili game package."""
import re
import time

from .batch import BatchStopped

PACKAGE = 'com.bilibili.priconne'
COMMAND = 'dumpsys package ' + PACKAGE


def validate_version(value):
    if type(value) is not str or re.fullmatch(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}', value) is None:
        raise ValueError('game_version_invalid')
    return value


def parse_package_version(output):
    if type(output) is not str or len(output) > 1024 * 1024 or '\x00' in output:
        raise ValueError('game_version_invalid')
    packages = list(re.finditer(r'^\s*Package \[([^\]\r\n]+)\][^\r\n]*:', output, re.MULTILINE))
    matches = [(index, match) for index, match in enumerate(packages) if match[1] == PACKAGE]
    if len(matches) != 1:
        raise ValueError('game_version_invalid')
    index, match = matches[0]
    block = output[match.end():packages[index+1].start() if index+1<len(packages) else len(output)]
    versions = re.findall(r'^[ \t]*versionName=([^\r\n]*)\r?$', block, re.MULTILINE)
    if len(versions) != 1:
        raise ValueError('game_version_invalid')
    return validate_version(versions[0].strip(' \t'))


def read_installed_version(controller, *, stopped):
    # Use this Owner's controller, not a second ADB connection or a config value.
    if stopped():
        raise BatchStopped('stopped')
    try:
        # shortcut: maafw 5.13 omits these signatures; remove after the pinned SDK fixes them.
        import ctypes
        from maa.define import MaaControllerHandle, MaaStringBufferHandle, MaaId, MaaBool
        from maa.library import Library
        api=Library.framework()
        api.MaaControllerPostShell.argtypes=[MaaControllerHandle,ctypes.c_char_p,ctypes.c_int64]
        api.MaaControllerPostShell.restype=MaaId
        api.MaaControllerGetShellOutput.argtypes=[MaaControllerHandle,MaaStringBufferHandle]
        api.MaaControllerGetShellOutput.restype=MaaBool
        job = controller.post_shell(COMMAND, timeout=5000)
        deadline = time.monotonic() + 6
        while not job.done:
            if stopped():
                raise BatchStopped('stopped')
            if time.monotonic() >= deadline:
                raise TimeoutError('game_version_timeout')
            time.sleep(.05)
        if stopped():
            raise BatchStopped('stopped')
        if not job.succeeded:
            raise ValueError('game_version_query_failed')
        version = parse_package_version(job.get())
        if stopped():
            raise BatchStopped('stopped')
        return version
    except BatchStopped:
        raise
    except Exception:
        raise BatchStopped('game_version_unavailable') from None
