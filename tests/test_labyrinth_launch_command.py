"""Launch evidence when the image has no `monkey`; am ACKs on every outcome."""
import pytest

from pcr_maa.labyrinth.launch_command import (launch_accepted, launch_command,
    launch_component)

PACKAGE = 'com.bilibili.priconne'
BRIEF = ('priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false\n'
         'com.bilibili.priconne/com.bilibili.permission.PermissionActivity\n')
STARTED = ('Starting: Intent { cmp=com.bilibili.priconne/.Main }\n'
           'Status: ok\nLaunchState: COLD\n'
           'Activity: com.bilibili.priconne/.Main\nTotalTime: 464\n'
           'WaitTime: 466\nComplete\n')
MISSING = ('Starting: Intent { cmp=com.bilibili.priconne/.Nope }\n'
           'Error type 3\n'
           "Error: Activity class {com.bilibili.priconne/com.bilibili.priconne.Nope}"
           ' does not exist.\n')


@pytest.mark.parametrize('output,expected', [
    (BRIEF, PACKAGE + '/com.bilibili.permission.PermissionActivity'),
    ('com.bilibili.priconne/.MainActivity\n', PACKAGE + '/.MainActivity'),
    ('  \n' + BRIEF + '\n', PACKAGE + '/com.bilibili.permission.PermissionActivity'),
])
def test_the_one_resolved_component_for_this_package_is_returned(output, expected):
    assert launch_component(output, PACKAGE) == expected


@pytest.mark.parametrize('output', [
    None, '', 'No activity found\n', 'priority=0 preferredOrder=0\n',
    # Two answers are ambiguous: stop instead of picking by order.
    BRIEF + 'com.bilibili.priconne/.Other\n',
    # Another package's component is never this server's launch target.
    'com.bilibili.priconne.mi/.Main\n',
    # Malformed activity half.
    'com.bilibili.priconne/\n', 'com.bilibili.priconne/main activity\n',
    # The raw input stays oversized; the id keeps PYTEST_CURRENT_TEST writable.
    pytest.param('x' * (64 * 1024 + 1), id='oversized-output'),
    'com.bilibili.priconne/.Main\x00\n',
])
def test_ambiguous_foreign_or_malformed_answers_never_become_a_launch(output):
    assert launch_component(output, PACKAGE) is None


@pytest.mark.parametrize('package', [None, '', 5, True])
def test_the_package_itself_must_be_a_name(package):
    assert launch_component(BRIEF, package) is None


def test_the_command_asks_am_to_report_the_launch_outcome():
    assert launch_command(PACKAGE + '/.Main') == 'am start -W -n ' + PACKAGE + '/.Main'


@pytest.mark.parametrize('output,expected', [
    (STARTED, True),
    (STARTED.replace('Status: ok', 'Warning: Activity not started'), False),
    (MISSING, False),
    # An error line next to a successful status is still a refusal.
    (STARTED + 'Error: Activity not started\n', False),
    (None, False), ('', False), ('Status: ok\x00\n', False),
    pytest.param('x' * (64 * 1024 + 1), False, id='oversized-output'),
])
def test_only_a_reported_status_without_an_error_counts_as_accepted(output, expected):
    assert launch_accepted(output) is expected


def test_the_real_refusal_is_not_launch_evidence():
    """am exits 0 on the refusal above, so the exit code proves nothing."""
    assert 'Status: ok' not in MISSING
    assert launch_accepted(MISSING) is False
