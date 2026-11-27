"""Start the client without trusting `monkey` or an ADB exit code.

MaaFramework's default `StartApp` action is `adb shell monkey -p <package> 1`.
Recent Android emulator images may not ship `monkey`. Resolve the actual
launcher component on the device and use the same intent as a launcher icon.
Channel packages name their entry differently.

`am start` also exits 0 when the component does not exist, so the exit code is
not evidence either way. Only its stdout separates a real start from an error.
"""
import re

_MAX_OUTPUT = 64 * 1024
_LAUNCHER_INTENT = '-a android.intent.action.MAIN -c android.intent.category.LAUNCHER'
# Launcher3 uses NEW_TASK | RESET_TASK_IF_NEEDED for app icons.
_LAUNCHER_FLAGS = '0x10200000'
# `resolve-activity --brief` prints the short form: `pkg/.Activity` or `pkg/pkg.Activity`.
_ACTIVITY = re.compile(r'\.?[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)*\Z')


def _lines(value):
    if type(value) is not str or not value or len(value) > _MAX_OUTPUT or '\x00' in value:
        return None
    return [line.strip() for line in value.splitlines() if line.strip()]


def resolve_launcher_command(package):
    return f'cmd package resolve-activity --brief {_LAUNCHER_INTENT} -p {package}'


def launch_component(output, package):
    """Return the single MAIN/LAUNCHER component reported for this package.

    An ambiguous, foreign or malformed answer returns None so the run stops
    instead of launching something else.
    """
    lines = _lines(output)
    if lines is None or type(package) is not str or not package:
        return None
    prefix = package + '/'
    found = [line for line in lines if line.startswith(prefix)]
    if len(found) != 1:
        return None
    return found[0] if _ACTIVITY.match(found[0][len(prefix):]) else None


def launch_command(component):
    """`-W` makes `am` report the launch outcome; the receipt carries stdout."""
    return f'am start -W {_LAUNCHER_INTENT} -f {_LAUNCHER_FLAGS} -n {component}'


def launch_accepted(output):
    """True only when `am start -W` printed `Status: ok` and no `Error` line."""
    lines = _lines(output)
    if lines is None or 'Status: ok' not in lines:
        return False
    return not any(line.startswith('Error') for line in lines)
