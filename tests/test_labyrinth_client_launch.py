"""Client start: the resolved component, and the refusal that still ACKs."""
from types import SimpleNamespace as NS

import pytest

from pcr_maa import native
from pcr_maa.labyrinth import app_version
from pcr_maa.labyrinth.batch import Batch, BatchStopped
from pcr_maa.labyrinth.game_port import MaaGamePort
from pcr_maa.labyrinth.snapshots import OpeningSnapshot
from pcr_maa.preset_execution import Journal
from test_labyrinth_restart_policy import Port, request

PACKAGE = 'com.bilibili.priconne'
COMPONENT = PACKAGE + '/com.bilibili.permission.PermissionActivity'
BRIEF = ('priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false\n'
         + COMPONENT + '\n')
STARTED = ('Starting: Intent { cmp=' + COMPONENT + ' }\nStatus: ok\n'
           'LaunchState: COLD\nActivity: ' + COMPONENT + '\nTotalTime: 464\n'
           'WaitTime: 466\nComplete\n')
# The same component, run after it was uninstalled or renamed: adb still exits 0.
REFUSED = ('Starting: Intent { cmp=' + COMPONENT + ' }\nError type 3\n'
           'Error: Activity class {' + COMPONENT + '} does not exist.\n')
STOP_NODE = 'LabyGamePortStopClientOnce'
START_NODE = 'LabyGamePortStartClientOnce'


def drive(monkeypatch, tmp_path, *, brief=BRIEF, output=STARTED):
    journal = Journal(tmp_path / 'ledger')
    binding = Port.binding
    port = Port(169)
    port.binding = binding
    batch = Batch(request().settings, port, journal, binding)
    snapshot = OpeningSnapshot(binding.account_id, 169, 1, 1, ())
    nodes, calls = {}, []
    tasker = NS(stopping=False, controller=NS(),
        resource=NS(override_pipeline=lambda value: nodes.update(value) or True))
    shell = app_version.controller_shell
    monkeypatch.setattr(app_version, 'controller_shell',
        lambda controller, command, **kwargs: brief
            if command.startswith('cmd package resolve-activity')
            else shell(controller, command, **kwargs))

    def run(_, node, **kwargs):
        calls.append(node)
        if node == STOP_NODE:
            return dict(tasker_inited=True, task_id=1, native_ok=True, nodes=[dict(
                name=node, completed=True, action=dict(name=node, success=True,
                    raw=dict(package=PACKAGE)))])
        return dict(tasker_inited=True, task_id=2, native_ok=True, nodes=[dict(
            name=node, completed=True, action=dict(name=node, success=True,
                raw=dict(cmd='am start -W -n ' + COMPONENT, shell_timeout=20000,
                         success=True, output=output)))])

    monkeypatch.setattr(native, 'run', run)
    game = MaaGamePort(tasker, port.stamp)
    game._active_creation_oid = 'c' * 32
    game.session = NS(_entry=lambda batch, recovery: ('title', {'native': 1}))
    return game, batch, journal, nodes, calls, snapshot


def test_start_launches_the_resolved_component_instead_of_monkey(monkeypatch, tmp_path):
    game, batch, journal, nodes, calls, snapshot = drive(monkeypatch, tmp_path)
    with journal.locked():
        result = game._restart_client(batch, snapshot)
    # Maa's default StartApp runs `monkey -p`, absent on recent emulator images.
    assert nodes[START_NODE]['action'] == 'Shell'
    assert nodes[START_NODE]['cmd'] == 'am start -W -n ' + COMPONENT
    assert 'package' not in nodes[START_NODE]
    assert nodes[STOP_NODE]['action'] == 'StopApp'
    assert calls == [STOP_NODE, START_NODE]
    assert result['component'] == COMPONENT
    assert result['client_ready_verified'] is True
    # The ledger names the component that was actually started.
    settled = [event for event in journal.events() if event['state'] == 'settled']
    assert [event['operation'] for event in settled] == [
        'client_stop_after_protocol', 'client_start_after_protocol']
    assert all(event['component'] == COMPONENT for event in settled)


def test_a_refused_launch_stops_with_its_intent_unsettled(monkeypatch, tmp_path):
    game, batch, journal, nodes, calls, snapshot = drive(monkeypatch, tmp_path, output=REFUSED)
    with journal.locked():
        with pytest.raises(BatchStopped, match='^game_port_restart_input_unknown$'):
            game._restart_client(batch, snapshot)
    # am ACKed the refusal: only the printed error keeps it from being evidence.
    assert calls == [STOP_NODE, START_NODE]
    assert journal.events()[-1]['operation'] == 'client_start_after_protocol'
    assert journal.events()[-1]['state'] == 'in_flight'


def test_an_unresolvable_launcher_stops_before_any_input(monkeypatch, tmp_path):
    game, batch, journal, nodes, calls, snapshot = drive(monkeypatch, tmp_path,
        brief='No activity found\n')
    with journal.locked():
        with pytest.raises(BatchStopped, match='^game_port_launch_component_unknown$'):
            game._restart_client(batch, snapshot)
    assert calls == [] and nodes == {} and journal.events() == []
