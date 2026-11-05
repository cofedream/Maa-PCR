"""Start the client without trusting `monkey` or an ADB exit code.

MaaFramework's default `StartApp` action is `adb shell monkey -p <package> 1`.
Emulator images based on recent Android builds may not ship `monkey` at all
(LDPlayer 14 answers `/system/bin/sh: monkey: inaccessible or not found`), so
the action fails before anything starts. `am start -n <package>/<activity>`
needs the launcher component, which is resolved on the device and never
guessed: channel packages name their entry differently.

`am start` also exits 0 when the component does not exist, so the exit code is
not evidence either way. Only its stdout separates a real start from an error.
"""
import re

_MAX_OUTPUT = 64 * 1024
# `resolve-activity --brief` prints the short form: `pkg/.Activity` or `pkg/pkg.Activity`.
_ACTIVITY = re.compile(r'\.?[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)*\Z')


def _lines(value):
    if type(value) is not str or not value or len(value) > _MAX_OUTPUT or '\x00' in value:
        return None
    return [line.strip() for line in value.splitlines() if line.strip()]


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
    return 'am start -W -n ' + component


def launch_accepted(output):
    """True only when `am start -W` printed `Status: ok` and no `Error` line."""
    lines = _lines(output)
    if lines is None or 'Status: ok' not in lines:
        return False
    return not any(line.startswith('Error') for line in lines)
