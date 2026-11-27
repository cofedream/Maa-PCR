"""Client start: the resolved component, and the refusal that still ACKs."""
from types import SimpleNamespace as NS

import pytest

from pcr_maa import native, recognition_metadata
from pcr_maa.labyrinth import app_version
from pcr_maa.labyrinth.batch import Batch, BatchStopped
from pcr_maa.labyrinth.game_port import MaaGamePort
from pcr_maa.labyrinth.game_session import MaaGameSession
from pcr_maa.labyrinth.snapshots import OpeningSnapshot
from pcr_maa.preset_execution import Journal
from test_labyrinth_restart_policy import Port, request as sample_request

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
RECOVERY_NODE = 'LabyGameSessionRecoveryObserved'
LAUNCH_COMMAND = ('am start -W -a android.intent.action.MAIN -c android.intent.category.LAUNCHER '
                  '-f 0x10200000 -n ' + COMPONENT)
RESOLVE_COMMAND = ('cmd package resolve-activity --brief -a android.intent.action.MAIN '
                   '-c android.intent.category.LAUNCHER -p ' + PACKAGE)
DESKTOP_RETURN = STARTED.replace('Activity: ' + COMPONENT, 'Activity: com.android.launcher3/.Launcher')


def drive(monkeypatch, tmp_path, *, brief=BRIEF, output=STARTED):
    journal = Journal(tmp_path / 'ledger')
    binding = Port.binding
    port = Port(169)
    port.binding = binding
    batch = Batch(sample_request().settings, port, journal, binding)
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
                raw=dict(cmd=nodes[START_NODE]['cmd'], shell_timeout=20000,
                         success=True, output=output)))])

    monkeypatch.setattr(native, 'run', run)
    game = MaaGamePort(tasker, port.stamp)
    game._active_creation_oid = 'c' * 32
    game.session = NS(_entry=lambda batch, recovery: ('title', {'native': 1}))
    return game, batch, journal, nodes, calls, snapshot


def test_start_launches_the_resolved_component_instead_of_monkey(monkeypatch, tmp_path):
    game, batch, journal, nodes, calls, snapshot = drive(monkeypatch, tmp_path)
    resolved = []
    def shell(controller, command, **kwargs):
        resolved.append(command)
        return BRIEF
    monkeypatch.setattr(app_version, 'controller_shell', shell)
    with journal.locked():
        result = game._restart_client(batch, snapshot)
    # Maa's default StartApp runs `monkey -p`, absent on recent emulator images.
    assert resolved == [RESOLVE_COMMAND]
    assert nodes[START_NODE]['action'] == 'Shell'
    assert nodes[START_NODE]['cmd'] == LAUNCH_COMMAND
    assert nodes[START_NODE]['pre_delay'] == 250
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


@pytest.mark.parametrize('native_timeout', [False, True], ids=['completed-miss', 'host-deadline'])
def test_desktop_return_stops_at_readback_without_repeating_launch(monkeypatch, tmp_path, native_timeout):
    game, batch, journal, nodes, calls, snapshot = drive(monkeypatch, tmp_path, output=DESKTOP_RETURN)
    game.session = MaaGameSession(game.tasker, game.stamp)
    run_launch = native.run

    def run(tasker, node, **kwargs):
        if node != RECOVERY_NODE:
            return run_launch(tasker, node, **kwargs)
        calls.append(node)
        if native_timeout:
            raise RuntimeError('Native task exceeded 90 seconds or inspection deadline')
        return dict(tasker_inited=True, task_id=3, native_ok=False, nodes=[])

    monkeypatch.setattr(native, 'run', run)
    with journal.locked(), pytest.raises(BatchStopped, match='^game_session_readback_unknown$'):
        game._restart_client(batch, snapshot)
    assert calls == [STOP_NODE, START_NODE, RECOVERY_NODE]
    last = journal.events()[-1]
    assert last['operation'] == 'client_start_after_protocol' and last['state'] == 'settled'
    assert last['result']['client_ready_verified'] is False


@pytest.fixture(params=[(), ('5.13.0', '5.16.1')], ids=['missing', 'conflicting'])
def restart_with_sdk(monkeypatch, tmp_path, request):
    import numpy as np
    from pcr_maa.replay import SavedImages

    class Controller(SavedImages):
        def stop_app(self, package):
            calls.append(('stop', package))
            return package == PACKAGE

        def shell(self, command, timeout):
            if command.startswith('cmd package resolve-activity'):
                return BRIEF
            calls.append(('start', command))
            return STARTED if command == LAUNCH_COMMAND else None

    monkeypatch.setattr(recognition_metadata, 'distributions',
        lambda *, name: [NS(version=version) for version in request.param])
    recognition_metadata._api.cache_clear()
    calls = []
    journal = Journal(tmp_path / 'ledger')
    port = Port(169)
    batch = Batch(sample_request().settings, port, journal, port.binding)
    snapshot = OpeningSnapshot(port.binding.account_id, 169, 1, 1, ())
    controller = Controller([[np.zeros((720, 1280, 3), dtype=np.uint8)]])
    tasker = native.bind(native.load_resource(cpu_only=True), controller)
    game = MaaGamePort(tasker, port.stamp)
    game._active_creation_oid = 'c' * 32
    game.session = NS(_entry=lambda batch, recovery: ('title', {'native': 1}))
    try:
        yield game, batch, journal, snapshot, calls
    finally:
        recognition_metadata._api.cache_clear()
        assert native.stop_tasker(tasker)['idle']


def test_restart_reads_real_sdk_receipts_with_unusable_package_metadata(restart_with_sdk):
    game, batch, journal, snapshot, calls = restart_with_sdk
    with journal.locked():
        result = game._restart_client(batch, snapshot)
    assert calls == [('stop', PACKAGE), ('start', LAUNCH_COMMAND)]
    assert result['client_ready_verified'] is True
    assert [event['operation'] for event in journal.events() if event['state'] == 'settled'] == [
        'client_stop_after_protocol', 'client_start_after_protocol']


def test_restart_does_not_start_when_sdk_stop_action_receipt_is_missing(restart_with_sdk, monkeypatch):
    game, batch, journal, snapshot, calls = restart_with_sdk
    monkeypatch.setattr(type(game.tasker), 'get_action_detail', lambda tasker, action_id: None)
    with journal.locked():
        with pytest.raises(BatchStopped, match='^game_port_restart_input_unknown$'):
            game._restart_client(batch, snapshot)
    assert calls == [('stop', PACKAGE)]
    assert journal.events()[-1]['operation'] == 'client_stop_after_protocol'
    assert journal.events()[-1]['state'] == 'in_flight'
