"""Owner entry + real config/business/journal/report; the game port is synthetic."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_clan_battle import Port, plan
from pcr_maa import agent_entry as entry, clan_battle_native, clan_battle_calibration, native, supported_daily
from pcr_maa.clan_battle_native import NativeClanBattle as RealNativeClanBattle
from pcr_maa.preset_execution import Journal


@pytest.fixture
def flow(plan, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('offline Owner flow attempted external I/O')

    monkeypatch.setattr('subprocess.Popen', forbidden)
    monkeypatch.setattr('socket.create_connection', forbidden)
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    adb = tmp_path / 'synthetic' / 'adb.exe'
    adb.parent.mkdir()
    adb.write_bytes(b'SYNTHETIC; NEVER EXECUTE')
    controller = dict(kind='adb', app_package='com.bilibili.priconne')
    config = dict(version=4, start='home', count_context=None,
        connection=dict(adb_path=str(adb), serial='emulator-5556'), controller=controller,
        presets=[dict(kind=k, slot=None, label='', target_per_stage=0)
                 for k in ('deep', 'very_hard', 'hard', 'normal')],
        clan_battle_simulation=deepcopy(plan))
    config_path = tmp_path / 'daily-config.json'
    config_path.write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setattr(entry, 'CONFIG_PATH', config_path)
    monkeypatch.setattr(entry, 'LOG_ROOT', tmp_path / 'entries')
    monkeypatch.setattr(supported_daily, 'CONTROL_ROOT', tmp_path / 'reports')
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    old_ledger = tmp_path / '.runtime/guide-trial-work/ledger/transactions.jsonl'
    old_ledger.parent.mkdir(parents=True)
    old_bytes = b'{"state":"in_flight","operation":"synthetic_old_guide"}\n'
    old_ledger.write_bytes(old_bytes)
    owner = SimpleNamespace(inited=True, stopping=False,
                            controller=SimpleNamespace(uuid='synthetic-controller'))
    context = SimpleNamespace(tasker=owner, get_task_job=lambda: SimpleNamespace(job_id=1))
    ports, events, results = [], [], []

    @contextmanager
    def scope(actual_context, *, deadline, owner_source):
        assert actual_context is context and deadline > time.monotonic()
        assert owner_source is None
        events.append('scope_enter')
        try:
            yield owner
        finally:
            events.append('scope_exit')

    def factory(tasker, directory, *, calibration=None):
        assert tasker is owner
        port = Port(plan)
        ports.append(port)
        return port

    monkeypatch.setattr(clan_battle_native, 'NativeClanBattle', factory)
    real_loader = clan_battle_calibration.load_calibration
    monkeypatch.setattr(clan_battle_calibration, 'load_calibration', lambda value: {'synthetic':True})

    def invoke(*, deny=False):
        selection = dict(version=1, selected_modules=['clan_battle_simulation'], rewards_final=False)

        def authorize(actual_context, actual_selection):
            assert actual_context is context and actual_selection == selection
            events.append('owner_authorize')
            if deny:
                raise ValueError('owner_denied')
            return entry.ExecutionContext(invocation_id=uuid4().hex, root_task_id=1,
                controller_uuid='synthetic-controller', serial='emulator-5556',
                combination=deepcopy(selection), execute=True, game_day='',
                count_context='gui-current-state', deadline=time.monotonic() + 30,
                controller=deepcopy(controller))

        action = entry.AlphaEntry(SimpleNamespace(scope=scope), execution_context=authorize)
        argv = SimpleNamespace(custom_action_name=entry.ACTION,
            task_detail=SimpleNamespace(task_id=1), custom_action_param=json.dumps(selection))
        success = action.run(context, argv)
        result = action.last_result
        assert old_ledger.read_bytes() == old_bytes
        assert json.loads(Path(result['entry_receipt']).read_text(encoding='utf-8')) == result
        report = (json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
                  if result['report_path'] else None)
        results.append((result, report))
        return success, result, report

    return SimpleNamespace(invoke=invoke, ports=ports, events=events, owner=owner,
        plan=plan, journal=Journal(tmp_path / '.runtime/clan-battle-simulation-work/ledger'),
        factory=factory, results=results, real_loader=real_loader)


def test_pure_auto_owner_entry_publishes_one_start_and_verified_damage(flow):
    success, result, report = flow.invoke()
    assert success and result['outcome'] == report['outcome'] == 'complete'
    assert report['selected_modules'] == ['clan_battle_simulation']
    assert report['daily_complete'] is report['rewards_final'] is False
    battle = report['steps'][0]['result']
    assert battle['damage'] == 12345678 and battle['damage_verified'] is True
    assert battle['settings_verified'] is battle['settlement_verified'] is True
    assert battle['return_verified'] is True and flow.ports[0].returns == 1
    assert flow.plan['switches'] == [] and flow.plan['auto'] is True
    assert flow.ports[0].prepared == flow.ports[0].starts == 1
    assert flow.events == ['owner_authorize', 'scope_enter', 'scope_exit']
    intent, settled = flow.journal.events()
    assert intent['operation'] == settled['operation'] == 'clan_battle_simulation'
    assert intent['intent_id'] == settled['intent_id']
    assert settled['damage'] == battle['damage'] and settled['simulation'] is True


@pytest.mark.parametrize('stop', [False, True])
def test_owner_denial_or_stop_prevents_scope_and_business(flow, stop):
    flow.owner.stopping = stop
    success, result, report = flow.invoke(deny=not stop)
    assert not success and report is None
    assert result['error'] == ('owner_stopped_or_uninitialized_outcome_unknown' if stop else 'owner_denied')
    assert flow.events == ['owner_authorize'] and flow.ports == []
    assert flow.journal.events() == []


def test_missing_calibration_reaches_real_port_guard_before_start(flow, monkeypatch):
    monkeypatch.setattr(clan_battle_calibration, 'load_calibration', flow.real_loader)
    monkeypatch.setattr(clan_battle_native, 'NativeClanBattle', RealNativeClanBattle)
    observations = []

    def choose(port, names, **kwargs):
        assert names == ['CBClosedReadOnly']
        observations.append('read_only_closed_page_probe')
        return 'GTUnknown'

    monkeypatch.setattr(RealNativeClanBattle, 'choose', choose)
    monkeypatch.setattr(RealNativeClanBattle, 'start_and_read',
        lambda *args: pytest.fail('missing calibration must never start'))
    success, result, report = flow.invoke()
    assert not success and result['outcome'] == report['outcome'] == 'stopped'
    battle = report['steps'][0]['result']
    assert battle['error'] == 'clan_battle_calibration_missing'
    assert battle['simulation_started'] is battle['consumption_sent'] is False
    assert observations == [] and flow.journal.events() == [] and flow.ports == []


@pytest.mark.parametrize('failure', ['stopped', 'clan_battle_disconnected_6002:battle_start=sent',
                                  'unknown_damage'])
def test_unknown_start_blocks_fresh_owner_before_prepare(flow, monkeypatch, failure):
    def factory(tasker, directory, *, calibration=None):
        port = flow.factory(tasker, directory, calibration=calibration)
        if len(flow.ports) == 1:
            if failure == 'unknown_damage':
                original = port.start_and_read

                def unreadable(plan):
                    result = original(plan)
                    result['damage'] = None
                    return result

                port.start_and_read = unreadable
            else:
                port.fail = failure
        return port

    monkeypatch.setattr(clan_battle_native, 'NativeClanBattle', factory)
    success, _, report = flow.invoke()
    assert not success and report['steps'][0]['result']['error'] == (
        'clan_battle_result_unknown' if failure == 'unknown_damage' else failure)
    assert flow.ports[0].starts == 1 and len(flow.journal.events()) == 1
    before = flow.journal.path.read_bytes()
    success, _, report = flow.invoke()
    assert not success and report['steps'][0]['result']['error'] == 'clan_battle_previous_result_unknown'
    assert flow.ports[1].prepared == flow.ports[1].starts == 0
    assert flow.journal.path.read_bytes() == before


@pytest.mark.parametrize('phase,battle_start,settled', [
    ('hud_toggle', 'not_sent', False), ('simulation_start', 'sent', False),
    ('simulation_start', 'not_sent', True)])
def test_only_this_unsent_simulation_start_can_close_owner_intent(
        flow, monkeypatch, phase, battle_start, settled):
    def factory(tasker, directory, *, calibration=None):
        port = flow.factory(tasker, directory, calibration=calibration)
        if len(flow.ports) == 1:
            port.battle_start = battle_start

            def not_sent(plan):
                port.starts += 1
                raise native.NativeInputNotSent(dict(input_execution='not_sent', clan_battle_phase=phase))

            port.start_and_read = not_sent
        return port

    monkeypatch.setattr(clan_battle_native, 'NativeClanBattle', factory)
    success, _, report = flow.invoke()
    battle = report['steps'][0]['result']
    assert not success and battle['simulation_started'] is (not settled)
    assert len(flow.journal.events()) == (2 if settled else 1)
    before = flow.journal.path.read_bytes()
    success, _, report = flow.invoke()
    assert success is settled
    assert flow.ports[1].prepared == flow.ports[1].starts == int(settled)
    if settled:
        assert flow.journal.path.read_bytes().startswith(before)
        assert len(flow.journal.events()) == 4
    else:
        assert report['steps'][0]['result']['error'] == 'clan_battle_previous_result_unknown'
        assert flow.journal.path.read_bytes() == before
