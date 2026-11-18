"""Best-effort client-side switch for the Labyrinth character-join animation.

The option is a local Unity PlayerPrefs entry, not a game-server setting.  The
client stores it in ``/data/data/<package>/shared_prefs/<package>.v2.playerprefs.xml``
under an obfuscated key and two opaque values.  This module only touches the
Labyrinth dialog entry (the second same-prefix entry is left alone), never
starts the game, and treats every failure as a warning: the Labyrinth task
must continue even when the device is not rooted or the client version changes
the entry.
"""
import os
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import unquote
from uuid import uuid4

# The preference path is package-specific, so every GameServer (Bilibili and
# channel clients) is read/written under its own package.  The key/value pair
# below is the game's own Unity setting, verified on the Bilibili client 11.7.2
# on MuMu 12 (2026-11-17).  A channel build that changes the wrapper is reported
# as unknown and is never written.
KEY = ('KXlybzR%2FK2x4aSFzMWdleC9iOnl%2Bfyt3MXF%2FeDllLnFg'
       'B1IEVg0DD1UPUgkHDw%3D%3D')
ON = 'TEFCWQUCAGnZ1gg%3D'
OFF = 'TUFCWQUCAJN2u%2FM%3D'
ROOT_HINT = ('未获得 root 权限，无法修改黎明界角色加入动画；建议在黎明界迷宫右上角'
             '“菜单 → 设定”中手动关闭“角色加入动画”，任务继续。')
UNKNOWN_HINT = ('无法识别黎明界角色加入动画配置；建议在黎明界迷宫右上角'
                '“菜单 → 设定”中手动关闭该选项，任务继续。')
STOP_FAILED_HINT = ('客户端未完全关闭，跳过修改黎明界角色加入动画；建议在黎明界迷宫右上角'
                    '“菜单 → 设定”中手动关闭，任务继续。')
STOP_QUERY_FAILED_HINT = ('无法确认客户端是否已关闭（进程查询超时或失败）；已跳过修改黎明界角色加入动画；'
                          '建议在黎明界迷宫右上角“菜单 → 设定”中手动关闭，任务继续。')
WRITE_FAILED_HINT = ('修改黎明界角色加入动画失败；建议在黎明界迷宫右上角'
                     '“菜单 → 设定”中手动关闭，任务继续。')
WRITE_INTERRUPTED_HINT = ('写入黎明界角色加入动画时被中断，已恢复原配置；建议在黎明界迷宫右上角'
                          '“菜单 → 设定”中手动关闭，任务继续。')
WRITE_UNRESTORED_HINT = ('写入黎明界角色加入动画失败且未能恢复原配置；请在黎明界迷宫右上角'
                         '“菜单 → 设定”中确认设置，任务继续。')
WRITE_BACKUP_FAILED_HINT = ('无法备份客户端配置，已跳过修改黎明界角色加入动画；建议在黎明界迷宫右上角'
                            '“菜单 → 设定”中手动关闭，任务继续。')
ERROR_HINT = ('检查黎明界角色加入动画时出错；建议在黎明界迷宫右上角'
              '“菜单 → 设定”中手动关闭，任务继续。')
STOP_STOPPED = 'stopped'
STOP_RUNNING = 'running'
STOP_QUERY_FAILED = 'query_failed'
WRITE_WRITTEN = 'written'
WRITE_FAILED = 'failed'
WRITE_BACKUP_FAILED = 'backup_failed'
WRITE_INTERRUPTED_RESTORED = 'interrupted_restored'
WRITE_INTERRUPTED_UNRESTORED = 'interrupted_unrestored'


def pref_path(package):
    return f'/data/data/{package}/shared_prefs/{package}.v2.playerprefs.xml'


def state(xml):
    """Return on/off/missing/unknown for the Labyrinth dialog entry."""
    if type(xml) is not str:
        return 'unknown'
    match = re.search(r'<string name="' + re.escape(KEY) + r'">([^<]*)</string>', xml)
    if match is None:
        return 'missing'
    value = unquote(match.group(1))
    if value == unquote(ON):
        return 'on'
    if value == unquote(OFF):
        return 'off'
    return 'unknown'


def rewrite_off(xml):
    """Replace only the dialog entry with OFF; None when it is not unique."""
    if type(xml) is not str:
        return None
    pattern = re.compile(r'(<string name="' + re.escape(KEY) + r'">)([^<]*)(</string>)')
    if len(pattern.findall(xml)) != 1:
        return None
    return pattern.sub(lambda match: match.group(1) + OFF + match.group(3), xml, count=1)


def _run(adb_path, serial, args, *, timeout=10.0, input=None):
    command = [str(adb_path), '-s', str(serial), *args]
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0
    return subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout, input=input, creationflags=flags)


def _call(run, args, **kwargs):
    try:
        return run(args, **kwargs)
    except Exception:
        # None is distinct from a completed command.  In particular, a timed-out
        # process query must never be mistaken for exit status 1 ("no process").
        return SimpleNamespace(returncode=None, stdout='', stderr='')


def _stdout(result):
    value = getattr(result, 'stdout', '')
    return value if type(value) is str else ''


def _rooted(run):
    result = _call(run, ['shell', 'id'], timeout=10)
    return result.returncode == 0 and re.search(r'\buid=0\b', _stdout(result)) is not None


def _ensure_root(run, stopped):
    if _rooted(run):
        return True
    if stopped():
        return False
    result = _call(run, ['root'], timeout=20)
    if result.returncode != 0:
        return False
    _call(run, ['wait-for-device'], timeout=20)
    return _rooted(run)


def _read(run, pref):
    # `adb exec-out` keeps the remote bytes but returns 0 even when its remote
    # `cat` failed (for example "Permission denied").  The prefs document always
    # has a <map> root, so an error line is not accepted as a readable file.
    result = _call(run, ['exec-out', 'cat', pref], timeout=20)
    if result.returncode != 0:
        return None
    value = _stdout(result)
    return value if '<map>' in value and '</map>' in value else None


def _stop(run, package, stopped):
    if stopped():
        return STOP_QUERY_FAILED
    _call(run, ['shell', 'am', 'force-stop', package], timeout=20)
    for _ in range(5):
        if stopped():
            return STOP_QUERY_FAILED
        result = _call(run, ['shell', 'pidof', package], timeout=5)
        if result.returncode is None:
            # Timeout/transport failure is not evidence that the client exited.
            return STOP_QUERY_FAILED
        output = _stdout(result).strip()
        stderr = getattr(result, 'stderr', '')
        if type(stderr) is not str:
            stderr = ''
        if result.returncode in (0, 1) and not output:
            # A completed pidof with no output is "not running" only when adb
            # itself did not report a transport/device error on stderr.
            return STOP_QUERY_FAILED if stderr.strip() else STOP_STOPPED
        if result.returncode == 127:
            # Very old images may not ship pidof; fall back to the process table.
            table = _call(run, ['shell', 'ps', '-A'], timeout=10)
            if table.returncode != 0:
                return STOP_QUERY_FAILED
            running = any(line.split() and line.split()[-1] == package
                          for line in _stdout(table).splitlines())
            return STOP_RUNNING if running else STOP_STOPPED
        if result.returncode not in (0, 1):
            return STOP_QUERY_FAILED
    return STOP_RUNNING


def _write(run, pref, original, new_xml, stopped):
    suffix = f'{os.getpid()}_{uuid4().hex[:8]}'
    remote = f'/data/local/tmp/pcr_maa_animation_{suffix}.xml'
    backup = f'/data/local/tmp/pcr_maa_animation_backup_{suffix}.xml'
    local = None

    def restore():
        result = _call(run, ['shell', f'cat {backup} > {pref}'], timeout=20)
        if result.returncode != 0:
            return WRITE_INTERRUPTED_UNRESTORED
        restored = _read(run, pref)
        return (WRITE_INTERRUPTED_RESTORED if restored == original
                else WRITE_INTERRUPTED_UNRESTORED)

    try:
        # Keep an on-device copy of the exact document before the in-place
        # overwrite.  A failed or interrupted write is restored from this copy.
        result = _call(run, ['shell', f'cat {pref} > {backup}'], timeout=20)
        if result.returncode != 0 or _read(run, backup) != original:
            return WRITE_BACKUP_FAILED
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='', suffix='.xml',
                                         delete=False) as stream:
            stream.write(new_xml)
            local = stream.name
        result = _call(run, ['push', local, remote], timeout=20)
        if result.returncode != 0:
            return WRITE_FAILED
        if stopped():
            return WRITE_FAILED
        result = _call(run, ['shell', f'cat {remote} > {pref}'], timeout=20)
        if result.returncode != 0:
            return restore()
        result = _call(run, ['exec-out', 'cat', pref], timeout=20)
        if result.returncode == 0 and state(_stdout(result)) == 'off':
            return WRITE_WRITTEN
        # The write command acknowledged, but the document is not the expected
        # one (partial/truncated).  Put the original bytes back before leaving.
        return restore()
    finally:
        if local is not None:
            try:
                os.unlink(local)
            except OSError:
                pass
        _call(run, ['shell', f'rm -f {remote} {backup}'], timeout=5)


def ensure_animation_off(adb_path, serial, package, *, log=lambda text: None,
                         stopped=lambda: False, runner=None):
    """Try to turn the Labyrinth join animation off; never fail the task.

    The client is read first.  Root is only requested when the setting is
    readable and ON, or when the pref cannot be read at all.  A rooted device
    is stopped before the file is replaced so Unity cannot overwrite it from
    memory.  The game is deliberately not restarted here: the Labyrinth client
    launch later in the same run starts it.
    """
    if stopped():
        return 'stopped'
    if runner is None:
        try:
            if (not os.path.isfile(adb_path)
                    or os.path.basename(str(adb_path)).lower() != 'adb.exe'):
                return 'adb_missing'
        except (TypeError, OSError):
            return 'adb_missing'
    run = ((lambda args, **kwargs: _run(adb_path, serial, args, **kwargs))
           if runner is None else runner)
    pref = pref_path(package)
    try:
        xml = _read(run, pref)
        if xml is not None:
            status = state(xml)
            if status == 'off':
                log('黎明界角色加入动画已是“不播放”。')
                return 'already_off'
            if status != 'on':
                log(UNKNOWN_HINT)
                return 'unknown'
            if not _ensure_root(run, stopped):
                if stopped():
                    return 'stopped'
                log(ROOT_HINT)
                return 'no_root'
        else:
            if not _ensure_root(run, stopped):
                if stopped():
                    return 'stopped'
                log(ROOT_HINT)
                return 'no_root'
            xml = _read(run, pref)
            if xml is None:
                log(UNKNOWN_HINT)
                return 'unknown'
            status = state(xml)
            if status == 'off':
                log('黎明界角色加入动画已是“不播放”。')
                return 'already_off'
            if status != 'on':
                log(UNKNOWN_HINT)
                return 'unknown'
        if stopped():
            return 'stopped'
        stop = _stop(run, package, stopped)
        if stop != STOP_STOPPED:
            if stopped():
                return 'stopped'
            if stop == STOP_QUERY_FAILED:
                log(STOP_QUERY_FAILED_HINT)
                return 'stop_unverified'
            log(STOP_FAILED_HINT)
            return 'stop_failed'
        new_xml = rewrite_off(xml)
        if new_xml is None:
            log(UNKNOWN_HINT)
            return 'unknown'
        write = _write(run, pref, xml, new_xml, stopped)
        if write == WRITE_WRITTEN:
            if stopped():
                return 'stopped'
            log('已关闭黎明界角色加入动画（设为“不播放”）。')
            return 'fixed'
        if stopped():
            return 'stopped'
        if write == WRITE_INTERRUPTED_RESTORED:
            log(WRITE_INTERRUPTED_HINT)
            return 'write_interrupted'
        if write == WRITE_INTERRUPTED_UNRESTORED:
            log(WRITE_UNRESTORED_HINT)
            return 'write_unrestored'
        if write == WRITE_BACKUP_FAILED:
            log(WRITE_BACKUP_FAILED_HINT)
            return 'write_failed'
        log(WRITE_FAILED_HINT)
        return 'write_failed'
    except Exception:
        log(ERROR_HINT)
        return 'error'
