"""Orchestration fakes prove routing only; replay is explicitly historical."""
import json
import os
from copy import deepcopy

import pytest

from pcr_maa import supported_daily as chain


@pytest.fixture(autouse=True)
def frozen_game_clock(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from pcr_maa import event, game_day, schedule_onekey
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2026, 9, 23, 14, tzinfo=timezone(timedelta(hours=8))))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            now = schedule_onekey.now_cn()
            return now.astimezone(tz) if tz else now
    for module in (chain, event, game_day):
        monkeypatch.setattr(module, 'datetime', Clock)


def config():
    return dict(version=1, start='home', device=dict(input_display=2, screenshot_display='123'),
                count_context=None, presets=[dict(kind=k, slot=s, label=k, target_per_stage=n)
                for k, s, n in [('hard', 6, 3), ('deep', 4, 10), ('very_hard', 2, 3)]])


def test_integration_event_partial_is_settled_and_continues(tmp_path, monkeypatch):
    event = success('event') | dict(goal_complete=False)
    report, calls = invoke(tmp_path, monkeypatch, {'event': event})
    assert calls == list(chain.ORDER)
    assert report['steps'][0]['status'] == 'partial'
    assert report['outcome'] == 'partial' and not report['supported_chain_complete']
    assert not report['daily_complete']
    assert '活动：已执行并结算，日目标未满' in chain.summary(report)
    assert '部分领取' not in chain.summary(report)


@pytest.mark.parametrize('partial', [True, False])
def test_integration_event_real_producer_contract(tmp_path, monkeypatch, partial):
    from test_event import Port, reading
    from pcr_maa.event import execute_event
    from pcr_maa.preset_execution import Journal
    port = Port()
    port.journal = Journal(tmp_path/'event-ledger')
    if partial:
        port.counts = [1]*5
        port.after = reading(2)
    else:
        old_quote = port.quote
        port.quote = lambda r: old_quote(r) | dict(insufficient='stamina')
    result = execute_event(port, port.journal, execute=True,
        context='user-reported-unreset',game_day='2026-09-23')
    report, calls = invoke(tmp_path/'run', monkeypatch, {'event':result})
    assert calls == list(chain.ORDER)
    assert result['consumption_sent'] is partial and result['goal_complete'] is False
    assert report['steps'][0]['status'] == ('partial' if partial else 'skipped_insufficient_stamina')
    assert report['outcome'] == ('partial' if partial else 'complete')
    assert not report['daily_complete']
    assert chain._read_report(tmp_path/'run') == report


def test_integration_already_complete_rejects_consumer_receipts():
    result = schedule_success() | dict(outcome='already_complete', schedule_executed=False,
        one_key_attempted=False, sent=[])
    assert chain._schedule_status(result, result['game_day'], result['device'],
                                  result['authorizations'], True) == 'stopped'


@pytest.mark.parametrize('fault', ['raw','ap','counter','missing_settlement'])
def test_integration_execution_evidence_rejects_internal_contradictions(fault):
    result = schedule_success() | dict(settings_assurance='user_attested_current_plan')
    if fault=='raw':
        one = next(r for r in result['receipts'] if r['entry']=='OneKeyDo_onekey')
        one['native']['nodes'][0]['action']['raw']={'point':[1,2]}
    elif fault=='ap': result['resources']['ap_delta']=999
    elif fault=='missing_settlement': result['journal_evidence']['end'].pop('settlement_verified')
    else:
        for receipt in result['receipts']:
            if receipt['reading'].get('capture_index',0)>=5:
                receipt['reading']['sections']={'delegated':{'completed':8,'total':9}}
    assert chain._schedule_status(result, result['game_day'], result['device'],
                                  result['authorizations'], True) == 'stopped'


def test_integration_claim_only_is_not_native_execution():
    result = schedule_success() | dict(settings_assurance='user_attested_current_plan')
    result.pop('journal_evidence', None)
    result.pop('receipts', None)
    assert chain._schedule_status(result, result['game_day'], result['device'],
                                  result['authorizations'], True) == 'stopped'


@pytest.mark.parametrize('display', [2, 3, 17])
def test_integration_v2_serial_through_real_component(tmp_path, monkeypatch, display):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import native, schedule_onekey, device
    from pcr_maa.preset_execution import Journal
    from test_schedule_onekey import attested_controller
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    adb = tmp_path / 'adb.exe'; adb.write_bytes(b'never executed')
    cfg = config() | dict(version=2, connection=dict(adb_path=str(adb), serial='127.0.0.1:5555'))
    cfg['device']['input_display'] = display
    assert device.connection_options(cfg, include_schedule_onekey=True) == {'connection': cfg['connection']}
    journal = Journal(tmp_path / 'isolated-ledger')
    original = schedule_onekey.schedule_onekey
    monkeypatch.setattr(schedule_onekey, 'schedule_onekey', lambda tasker, **kw: original(tasker, journal=journal, **kw))
    monkeypatch.setattr(chain, '_run_step', lambda tasker, name, *args, **kwargs: success(name))
    tasker, controller = attested_controller(native.load_resource(), monkeypatch)
    report = chain.execute_supported(tasker, cfg, execute=True, game_day='2027-09-24',
        context='user-reported-unreset', directory=tmp_path/'run', include_schedule_onekey=True,
        schedule_authorizations=schedule_onekey.ITEMS, schedule_attest_current_plan=True)
    result = report['steps'][0]['result']
    assert report['steps'][0]['status'] == 'executed', result
    assert result['device']['serial'] == '127.0.0.1:5555'
    assert result['device']['input_display'] == display
    assert controller.clicks.count([880, 640]) == 1
    for event in journal.events():
        assert event['device'] == result['device']
    actions = Journal(journal.directory/'actions'/journal.events()[0]['intent_id'])
    assert all(e['device'] == result['device'] for e in actions.events())
    from pcr_maa.onekey_history import read_history
    with journal.locked():
        assert read_history(journal, result['game_day'], result['device']) == (set(schedule_onekey.ITEMS), None, None)
        assert read_history(journal, '2027-09-25', result['device']) == (set(), None, None)
        with pytest.raises(ValueError):
            read_history(journal, result['game_day'], result['device'] | dict(input_display=18))
    for fault in ('no_receipts','no_proof','no_onekey','old_run','old_session','wrong_device',
                  'unsettled','jewels','rows','missing_native_action','action_inflight','wrong_task'):
        bad = deepcopy(result)
        if fault=='no_receipts': bad['receipts']=[]
        elif fault=='no_proof': bad.pop('journal_evidence')
        elif fault=='no_onekey': bad['receipts']=[r for r in bad['receipts'] if r['entry']!='OneKeyDo_onekey']
        elif fault=='old_run': bad['navigation_context']['invocation_id']='old-run'
        elif fault=='old_session': bad['receipts'][0]['reading']['session_id']='0'*32
        elif fault=='wrong_device': bad['journal_evidence']['start']['device']['serial']='127.0.0.1:9999'
        elif fault=='unsettled': bad['settlement_verified']=False
        elif fault=='jewels': bad['resources']['after']['jewels']-=1
        elif fault=='rows': bad['after_rows']['arena_coins']='pending'
        elif fault=='missing_native_action':
            r=next(r for r in bad['receipts'] if r['entry']=='OneKeyDo_onekey')
            r['native']['nodes']=[]
        elif fault=='action_inflight': bad['journal_evidence']['actions'][-1]['state']='in_flight'
        elif fault=='wrong_task': bad['journal_evidence']['actions'][-1]['native_task_id']=-1
        assert chain._schedule_status(bad, result['game_day'], result['device'],
            result['authorizations'], True, 'run') == 'stopped', fault
    assert chain._schedule_status(result, result['game_day'], result['device'],
        result['authorizations'], True, 'different-run') == 'stopped'


def success(name):
    if name in ('rewards', 'rewards_final'):
        return dict(outcome='complete', home_verified=True, items={k: dict(
            status='claimed', home_verified=True, claim_attempted=True, result_verified=True)
            for k in ('missions', 'gifts')})
    return dict(outcome='executed', home_verified=True, consumption_sent=True,
                settlement_verified=True, count_fit=dict(attempted=True, restored=True))


def invoke(tmp_path, monkeypatch, overrides=None):
    calls = []
    def run(tasker, name, cfg, day, context, *, exit_page='home'):
        calls.append(name)
        value = (overrides or {}).get(name, success(name))
        if isinstance(value, BaseException):
            raise value
        return deepcopy(value)
    monkeypatch.setattr(chain, '_run_step', run)
    result = chain.execute_supported(None, config(), execute=True, game_day='2026-09-23',
        context='user-reported-unreset', directory=tmp_path)
    return result, calls


def schedule_success(*, outcome='executed', invocation_id='synthetic-invocation'):
    from pcr_maa.schedule_onekey import ITEMS, ROW_LABELS
    executed = outcome == 'executed'
    result = dict(outcome=outcome, game_day='2027-09-24',
        device=dict(serial='127.0.0.1:16384', input_display=2, screenshot_display='123'),
        authorizations=list(ITEMS),
        nine_rows_verified=True, after_rows={item: 'complete' for item in ITEMS},
        home_verified=True, settlement_verified=True, schedule_executed=executed,
        one_key_attempted=executed, sent=['onekey'] if executed else [],
        resources=dict(before=dict(jewels=100,ap=100), after=dict(jewels=100,ap=100), jewels_delta=0,ap_delta=0))
    if executed:
        # Explicit synthetic contract fixture; native correctness has separate
        # official CustomController tests using actual Maa receipts.
        intent, session = 'a'*32, 'b'*32
        common = dict(game_day=result['game_day'], device=result['device'], intent_id=intent, session_id=session)
        result['navigation_context'] = dict(run_id=intent, session_id=session,
            game_day=result['game_day'], device=result['device'], invocation_id=invocation_id)
        receipts, actions = [], []
        for i, (entry, state) in enumerate([
            ('OneKeyExecRead','home'), ('OneKeyDo_enter','home'), ('OneKeyExecRead','schedule'),
            ('OneKeyDo_onekey','schedule'), ('OneKeyExecRead','schedule'),
            ('OneKeyDo_close','schedule'), ('OneKeyExecRead','home')], 1):
            # A guard capture sees the same pixels as the preceding snapshot.
            sha = str(i if i % 2 else i-1)*64
            page = dict(state=state, session_id=session, acquisition='current_tasker_capture',
                capture_index=i, image_sha256=sha,
                resources=dict(jewels=100,ap=100) if state=='home' else {},
                sections={'delegated':dict(completed=9 if i>=5 else 0,total=9)} if state=='schedule' else {},
                rows={k:dict(state='complete' if i >= 5 else 'pending', label=ROW_LABELS[k], y=120+j*45)
                      for j,k in enumerate(ITEMS)} if state=='schedule' else {})
            name = 'OneKeyExecSnapshot' if entry=='OneKeyExecRead' else entry.replace('OneKeyDo_', 'OneKeyInput_')
            receipt = dict(entry=entry, reading=page, native=dict(native_ok=True, task_id=i,
                nodes=[dict(name=name, completed=True, action=dict(name=name,success=True,
                    raw=dict(point=[880,640],contact=0,pressure=1)))]))
            receipts.append(receipt)
            if entry!='OneKeyExecRead':
                op=entry[len('OneKeyDo_'):]
                actions += [common | dict(state='in_flight',operation=op,image_sha256=sha),
                            common | dict(state='settled',operation=op,image_sha256=str(i+1)*64,native_task_id=i)]
        start = common | dict(state='in_flight', operation='schedule_onekey', invocation_id=invocation_id,
                              authorizations=result['authorizations'])
        end = common | dict(state='settled', operation='schedule_onekey', invocation_id=invocation_id,
            outcome='executed', rows=result['after_rows'], resources=result['resources'], receipts=receipts, image_sha256='7'*64,
            home_verified=True,settlement_verified=True,nine_rows_verified=True)
        result.update(receipts=receipts, journal_evidence=dict(start=start,end=end,actions=actions))
    return result


@pytest.mark.parametrize('audit', [None, dict(verified=True)])
def test_player_managed_legacy_execution_proof_cannot_release_chain(audit):
    report = schedule_success()
    if audit is not None:
        report['settings_audit'] = audit
    assert chain._schedule_status(report, report['game_day'], report['device'],
                                  report['authorizations']) == 'stopped'


@pytest.mark.parametrize('schedule_outcome', ['executed', 'already_complete'])
def test_schedule_optin_first_then_original_order(tmp_path, monkeypatch, schedule_outcome):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import schedule_onekey
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    calls = []
    tasker = object()
    def run_schedule(t, day, device, authorizations, attest_current_plan, invocation_id):
        assert attest_current_plan is False
        calls.append(('schedule_onekey', t, day, device, tuple(authorizations)))
        return schedule_success(outcome=schedule_outcome)
    def run_step(t, name, cfg, day, context, *, exit_page='home'):
        calls.append((name, t, day, context))
        return success(name)
    monkeypatch.setattr(chain, '_run_schedule', run_schedule, raising=False)
    monkeypatch.setattr(chain, '_run_step', run_step)
    result = chain.execute_supported(tasker, config(), execute=True, game_day='2027-09-24',
        context='user-reported-unreset', directory=tmp_path,
        include_schedule_onekey=True, schedule_authorizations=schedule_onekey.ITEMS)
    assert [call[0] for call in calls] == (['schedule_onekey'] if schedule_outcome == 'executed'
                                         else ['schedule_onekey', *chain.ORDER])
    assert all(call[1] is tasker for call in calls)
    assert result['steps'][0]['result'] == schedule_success(outcome=schedule_outcome)
    assert result['steps'][0]['status'] == ('stopped' if schedule_outcome == 'executed' else 'complete')
    assert result['supported_chain_complete'] is (schedule_outcome == 'already_complete')
    assert not result['daily_complete']
    assert 'schedule' not in result['unsupported']


@pytest.mark.parametrize('outcome', ['unknown', 'already_recorded_not_executed',
                                    'already_complete', 'executed'])
def test_schedule_optin_requires_all_settlement_proof(tmp_path, monkeypatch, outcome):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import schedule_onekey
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    calls = []
    report = schedule_success(outcome=outcome)
    if outcome == 'unknown':
        report['reason'] = 'unsafe_or_changed_page'
    elif outcome == 'already_complete':
        report['resources']['after']['jewels'] = 101
    elif outcome == 'executed':
        report['settlement_verified'] = False
    monkeypatch.setattr(chain, '_run_schedule', lambda *args: calls.append('schedule') or report,
                        raising=False)
    monkeypatch.setattr(chain, '_run_step', lambda *args, **kwargs: calls.append('later') or success(args[1]))
    result = chain.execute_supported(object(), config(), execute=True, game_day='2027-09-24',
        context='user-reported-unreset', directory=tmp_path,
        include_schedule_onekey=True, schedule_authorizations=schedule_onekey.ITEMS)
    assert calls == ['schedule']
    assert result['steps'][0]['result'] == report
    assert all(step['status'] == 'not_run' for step in result['steps'][1:])
    assert not result['supported_chain_complete'] and not result['daily_complete']


@pytest.mark.parametrize('failure', [dict(home_verified=False), dict(settlement_verified=False)])
def test_schedule_optin_all_complete_then_downstream_failure(tmp_path, monkeypatch, failure):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import schedule_onekey
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    calls = []
    monkeypatch.setattr(chain, '_run_schedule',
                        lambda *args: calls.append('schedule_onekey') or schedule_success(outcome='already_complete'),
                        raising=False)
    def later(tasker, name, config, game_day, context, *, exit_page='home'):
        calls.append(name)
        return success(name) | (failure if name == 'deep' else {})
    monkeypatch.setattr(chain, '_run_step', later)
    result = chain.execute_supported(object(), config(), execute=True, game_day='2027-09-24',
        context='user-reported-unreset', directory=tmp_path,
        include_schedule_onekey=True, schedule_authorizations=schedule_onekey.ITEMS)
    assert calls == ['schedule_onekey', 'event', 'deep']
    assert result['steps'][0]['status'] == 'complete'
    assert [step['status'] for step in result['steps'][3:]] == ['not_run'] * 4
    assert not result['supported_chain_complete'] and not result['daily_complete']


def test_schedule_optin_rejects_orphan_missing_duplicate_and_completed_day(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import schedule_onekey
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    cases = [dict(schedule_authorizations=['arena_coins']),
             dict(include_schedule_onekey=True),
             dict(include_schedule_onekey=True, schedule_authorizations=['arena_coins', 'arena_coins']),
             dict(include_schedule_onekey=True, schedule_authorizations=schedule_onekey.ITEMS,
                  game_day='2026-09-24')]
    for case in cases:
        day = case.pop('game_day', '2027-09-24')
        with pytest.raises(ValueError):
            chain.execute_supported(None, config(), execute=True, game_day=day,
                context='user-reported-unreset', directory=tmp_path, **case)
    assert not list(tmp_path.iterdir())


def test_schedule_optin_rejects_wrong_game_day_and_wrong_display_before_report(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import schedule_onekey
    clock = [datetime(2027, 9, 24, 4, 59, tzinfo=timezone(timedelta(hours=8)))]
    monkeypatch.setattr(schedule_onekey, 'now_cn', lambda: clock[0])
    options = dict(execute=True, game_day='2027-09-24', context='user-reported-unreset',
                   directory=tmp_path, include_schedule_onekey=True,
                   schedule_authorizations=schedule_onekey.ITEMS)
    with pytest.raises(ValueError, match='current_game_day_required'):
        chain.execute_supported(None, config(), **options)
    clock[0] = clock[0].replace(hour=14)
    bad = config()
    bad['device']['screenshot_display'] = 'wrong'
    with pytest.raises(ValueError):
        chain.execute_supported(None, bad, **options)
    assert not list(tmp_path.iterdir())


def test_schedule_adapter_uses_same_tasker_and_original_journal(monkeypatch):
    from pcr_maa import schedule_onekey
    calls = []
    tasker = object()
    monkeypatch.setattr(schedule_onekey, 'schedule_onekey',
                        lambda t, **kw: calls.append((t, kw)) or schedule_success())
    result = chain._run_schedule(tasker, '2027-09-24',
        dict(serial='127.0.0.1:16384', input_display=2, screenshot_display='123'),
        schedule_onekey.ITEMS)
    assert result == schedule_success()
    assert calls == [(tasker, dict(execute=True, game_day='2027-09-24',
        device=dict(serial='127.0.0.1:16384', input_display=2, screenshot_display='123'),
        authorizations=schedule_onekey.ITEMS, attest_current_plan=False, invocation_id=None))]


def test_schedule_attestation_default_is_explicitly_forwarded(monkeypatch):
    from pcr_maa import schedule_onekey
    calls = []
    monkeypatch.setattr(schedule_onekey, 'schedule_onekey',
                        lambda tasker, **kw: calls.append(kw) or schedule_success())
    chain._run_schedule(None, '2027-09-24', schedule_success()['device'], schedule_onekey.ITEMS)
    assert calls[0].get('attest_current_plan') is False


def test_schedule_attestation_executed_contract(tmp_path, monkeypatch):
    from inspect import signature
    from datetime import datetime, timezone, timedelta
    from pcr_maa import schedule_onekey
    assert 'schedule_attest_current_plan' in signature(chain.execute_supported).parameters
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    calls = []
    receipt = schedule_success(invocation_id=tmp_path.name) | dict(settings_assurance='user_attested_current_plan')
    def component(tasker, **kw):
        assert kw['attest_current_plan'] is True
        calls.append('schedule_onekey')
        return deepcopy(receipt)
    monkeypatch.setattr(schedule_onekey, 'schedule_onekey', component)
    monkeypatch.setattr(chain, '_run_step',
                        lambda tasker, name, *args, **kwargs: calls.append(name) or success(name))
    result = chain.execute_supported(None, config(), execute=True, game_day='2027-09-24',
        context='user-reported-unreset', directory=tmp_path,
        include_schedule_onekey=True, schedule_authorizations=schedule_onekey.ITEMS,
        schedule_attest_current_plan=True)
    assert calls == ['schedule_onekey', *chain.ORDER]
    assert result['steps'][0]['status'] == 'executed'
    assert result['supported_chain_complete'] is True
    assert result['daily_complete'] is False
    assert chain._read_report(tmp_path) == result


def test_schedule_attestation_device_envelope_reaches_validation(tmp_path, monkeypatch):
    from pcr_maa import device, native
    calls = []
    def stop_at_validation(**kw):
        calls.append(kw)
        raise ValueError('synthetic_validation_boundary')
    monkeypatch.setattr(chain, 'validate', stop_at_validation)
    monkeypatch.setattr(native, 'load_resource', lambda: pytest.fail('resource constructed'))
    request = dict(config=config(), execute=True, game_day='2027-09-24',
        context='user-reported-unreset', directory=tmp_path,
        include_schedule_onekey=True, schedule_authorizations=['arena_coins'],
        schedule_attest_current_plan=True)
    with pytest.raises(ValueError) as error:
        device.navigate(2, '123', supported=request)
    assert str(error.value) == 'synthetic_validation_boundary'
    assert calls[0]['schedule_attest_current_plan'] is True


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}])
def test_schedule_attestation_requires_strict_bool_before_report(tmp_path, value):
    with pytest.raises(ValueError):
        chain.execute_supported(None, config(), execute=True, game_day='2027-09-24',
            context='user-reported-unreset', directory=tmp_path,
            schedule_attest_current_plan=value)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('execute,include', [(False, False), (False, True), (True, False)])
def test_schedule_attestation_requires_execute_and_include(tmp_path, execute, include):
    with pytest.raises(ValueError):
        chain.execute_supported(None, config(), execute=execute, game_day='2027-09-24',
            context='user-reported-unreset', directory=tmp_path,
            include_schedule_onekey=include, schedule_attest_current_plan=True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('patch', [
    dict(outcome='unknown'), dict(outcome='blocked_in_flight'),
    dict(outcome='unknown', phase='navigation', consumer_state='not_started', native_input_state='unknown'),
    dict(outcome='unknown', phase='consumer', consumer_state='in_flight', native_input_state='unknown'),
    dict(reason='paid_gacha'), dict(error='unverified'),
    dict(settings_assurance='unattested'), dict(settings_assurance=None),
    dict(settings_assurance='machine_settings_audit'),
    dict(one_key_attempted=False), dict(one_key_attempted=1),
    dict(schedule_executed=False), dict(schedule_executed=1),
    dict(sent=[]), dict(sent=['onekey', 'onekey']), dict(sent='onekey'), dict(sent=None),
    dict(nine_rows_verified=False), dict(nine_rows_verified=1),
    dict(after_rows={}), dict(after_rows=[]),
    dict(home_verified=False), dict(home_verified=1), dict(settlement_verified=False),
    dict(game_day='2027-09-23'), dict(device={}), dict(authorizations=['arena_coins']),
    dict(resources={}), dict(resources=[]), dict(resources='unknown'),
    dict(resources=dict(before=dict(jewels=100), after=dict(jewels=99), jewels_delta=-1)),
    dict(resources=dict(before=dict(jewels=100), after=dict(jewels=101), jewels_delta=0)),
    dict(resources=dict(before=dict(jewels=1), after=dict(jewels=True), jewels_delta=0)),
    dict(resources=dict(before=dict(jewels=100), after=dict(jewels=100), jewels_delta=False)),
])
def test_schedule_attestation_bad_receipt_stops_following_steps(tmp_path, monkeypatch, patch):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import schedule_onekey
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    receipt = schedule_success() | dict(settings_assurance='user_attested_current_plan') | patch
    calls = []
    monkeypatch.setattr(chain, '_run_schedule', lambda *a: calls.append(a[4]) or receipt)
    monkeypatch.setattr(chain, '_run_step', lambda *a, **kw: pytest.fail('unsafe continuation'))
    report = chain.execute_supported(None, config(), execute=True, game_day='2027-09-24',
        context='user-reported-unreset', directory=tmp_path, include_schedule_onekey=True,
        schedule_authorizations=schedule_onekey.ITEMS, schedule_attest_current_plan=True)
    assert calls == [True] and report['outcome'] == 'stopped'
    assert report['steps'][0]['result'] == receipt
    assert all(s['status'] == 'not_run' for s in report['steps'][1:])
    assert report['supported_chain_complete'] is report['daily_complete'] is False


def test_schedule_attestation_receipt_cannot_supply_invocation_permission():
    receipt = schedule_success() | dict(settings_assurance='user_attested_current_plan')
    assert chain._schedule_status(receipt, receipt['game_day'], receipt['device'],
                                  receipt['authorizations']) == 'stopped'
    for field in ('settings_assurance', 'one_key_attempted', 'schedule_executed', 'sent',
                  'nine_rows_verified', 'after_rows', 'home_verified', 'settlement_verified',
                  'game_day', 'device', 'authorizations', 'resources'):
        missing = {k: v for k, v in receipt.items() if k != field}
        assert chain._schedule_status(missing, receipt['game_day'], receipt['device'],
                                      receipt['authorizations'], True) == 'stopped', field


@pytest.mark.parametrize('patch', [dict(one_key_attempted=True), dict(schedule_executed=True),
                                  dict(sent=['onekey'])])
def test_schedule_attestation_already_complete_cannot_hide_onekey(patch):
    receipt = schedule_success(outcome='already_complete') | patch
    assert chain._schedule_status(receipt, receipt['game_day'], receipt['device'],
                                  receipt['authorizations'], True) == 'stopped'


@pytest.mark.parametrize('fault', ['shape', 'unknown', 'orphan', 'missing_authorizations',
    'int', 'str', 'none', 'include_int', 'execute_int', 'no_execute', 'no_include', 'conflict', 'display'])
def test_schedule_attestation_device_rejects_before_resource(tmp_path, monkeypatch, fault):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import device, native, schedule_onekey
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    monkeypatch.setattr(native, 'load_resource', lambda: pytest.fail('resource constructed'))
    request = dict(config=config(), execute=True, game_day='2027-09-24',
        context='user-reported-unreset', directory=tmp_path, include_schedule_onekey=True,
        schedule_authorizations=['arena_coins'], schedule_attest_current_plan=True)
    extra = {}
    if fault == 'shape': request = list(request)
    elif fault == 'unknown': request['arbitrary'] = True
    elif fault == 'orphan':
        del request['include_schedule_onekey'], request['schedule_authorizations']
    elif fault == 'missing_authorizations': del request['schedule_authorizations']
    elif fault in ('int', 'str', 'none'):
        request['schedule_attest_current_plan'] = {'int': 1, 'str': 'true', 'none': None}[fault]
    elif fault == 'include_int': request['include_schedule_onekey'] = 1
    elif fault == 'execute_int': request['execute'] = 1
    elif fault == 'no_execute': request['execute'] = False
    elif fault == 'no_include': request['include_schedule_onekey'] = False
    elif fault == 'conflict': extra['slot'] = 1
    elif fault == 'display': request['config']['device']['screenshot_display'] = '456'
    with pytest.raises(ValueError):
        device.navigate(2, '123', supported=request, **extra)


@pytest.mark.parametrize('shape', ['base', 'legacy_schedule', 'attest_false', 'attest_true'])
def test_schedule_attestation_device_valid_shapes_finish_validation(tmp_path, monkeypatch, shape):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import device, native, schedule_onekey
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    class Validated(Exception):
        pass
    def stop():
        raise Validated()
    monkeypatch.setattr(native, 'load_resource', stop)
    request = dict(config=config(), execute=True, game_day='2027-09-24',
        context='user-reported-unreset', directory=tmp_path)
    if shape != 'base':
        request.update(include_schedule_onekey=True, schedule_authorizations=['arena_coins'])
    if shape.startswith('attest_'):
        request['schedule_attest_current_plan'] = shape == 'attest_true'
    with pytest.raises(Validated):
        device.navigate(2, '123', supported=request)


@pytest.mark.parametrize('inflight', [False, True])
def test_schedule_attestation_real_component_synthetic_journal(tmp_path, monkeypatch, inflight):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import native, schedule_onekey
    from pcr_maa.preset_execution import Journal
    from test_schedule_onekey import attested_controller
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    journal = Journal(tmp_path / 'synthetic-ledger')
    if inflight:
        with journal.locked():
            journal.append(dict(state='in_flight', operation='schedule_onekey', game_day='2026-09-25'))
        before = journal.path.read_bytes()
    original = schedule_onekey.schedule_onekey
    monkeypatch.setattr(schedule_onekey, 'schedule_onekey',
                        lambda tasker, **kw: original(tasker, journal=journal, **kw))
    calls = []
    monkeypatch.setattr(chain, '_run_step', lambda tasker, name, *a, **kw: calls.append(name) or success(name))
    tasker, controller = attested_controller(native.load_resource(), monkeypatch)
    report = chain.execute_supported(tasker, config(), execute=True, game_day='2027-09-24',
        context='user-reported-unreset', directory=tmp_path / 'report', include_schedule_onekey=True,
        schedule_authorizations=schedule_onekey.ITEMS, schedule_attest_current_plan=True)
    assert report['daily_complete'] is False
    if inflight:
        assert report['outcome'] == 'stopped' and calls == controller.clicks == []
        assert report['steps'][0]['result']['reason'] == 'unsettled_intent_any_day_no_resend'
        assert journal.path.read_bytes() == before
    else:
        assert report['steps'][0]['status'] == 'executed' and calls == list(chain.ORDER)
        assert report['supported_chain_complete'] is True
        assert controller.clicks.count([880, 640]) == 1


def test_fixed_order_and_scope(tmp_path, monkeypatch):
    r, calls = invoke(tmp_path, monkeypatch)
    assert calls == ['event', 'deep', 'rewards', 'very_hard', 'hard', 'rewards_final']
    assert r['supported_chain_complete'] and not r['daily_complete']
    assert r['unsupported'] == {'schedule': 'unsupported', 'abyss_boss': 'unsupported'}
    assert all(s['started_at'] <= s['finished_at'] and s['receipt'] for s in r['steps'])
    assert json.loads((tmp_path/'report.json').read_text(encoding='utf-8')) == r


@pytest.mark.parametrize('outcome', ['skipped_goal_met', 'skipped_insufficient_stamina', 'skipped_insufficient_tickets'])
def test_safe_skip(tmp_path, monkeypatch, outcome):
    r, calls = invoke(tmp_path, monkeypatch, {'deep': dict(outcome=outcome, home_verified=True)})
    assert len(calls) == 6 and r['supported_chain_complete']
    assert r['steps'][1]['status'] == outcome


@pytest.mark.parametrize('patch', [dict(home_verified=False), dict(settlement_verified=False),
    dict(outcome='executed_home_failed', home_verified=False),
    dict(count_fit=dict(attempted=True, restored=False)), dict(outcome='ready_not_executed'),
    dict(outcome='possible_day_boundary'), dict(outcome='counter_reset_or_identity_change'),
    dict(outcome='skipped_goal_met', home_verified=False)])
def test_stop_without_resend_or_cleanup(tmp_path, monkeypatch, patch):
    r, calls = invoke(tmp_path, monkeypatch, {'deep': success('deep') | patch})
    assert calls == ['event', 'deep']
    assert not r['supported_chain_complete']
    assert [s['status'] for s in r['steps'][2:]] == ['not_run']*4
    assert r['steps'][1]['result'] == success('deep') | patch


def test_remaining_is_partial_once(tmp_path, monkeypatch):
    reward = success('rewards')
    reward['items']['gifts']['status'] = 'remaining'
    r, calls = invoke(tmp_path, monkeypatch, {'rewards': reward})
    assert len(calls) == 6 and calls.count('rewards') == calls.count('rewards_final') == 1
    assert r['outcome'] == 'partial' and not r['supported_chain_complete']
    assert r['steps'][2]['status'] == 'partial'


@pytest.mark.parametrize('error', [RuntimeError('failed'), KeyboardInterrupt()])
def test_exception_keeps_evidence(tmp_path, monkeypatch, error):
    r, calls = invoke(tmp_path, monkeypatch, {'deep': error})
    assert calls == ['event', 'deep']
    assert r['steps'][0]['result'] == success('event')
    assert r['steps'][1]['error']
    assert all(s['status'] == 'not_run' for s in r['steps'][2:])
    assert not r['daily_complete'] and not r['supported_chain_complete']


def test_no_execute_zero_operations(tmp_path, monkeypatch, capsys):
    from pcr_maa.cli import main
    from pcr_maa import device
    monkeypatch.setattr(device, 'navigate', lambda *a, **k: pytest.fail('device operation'))
    monkeypatch.setattr(chain, '_run_step', lambda *a, **kw: pytest.fail('component operation'))
    assert main(['execute-supported', '--config', 'missing', '--game-day', '2026-09-23',
                 '--count-context', 'user-reported-unreset']) == 2
    output = capsys.readouterr().out
    assert 'inspect-daily' in output and '安全拒绝' in output and '零设备操作' in output
    with pytest.raises(ValueError):
        chain.execute_supported(None, config(), game_day='2026-09-23',
            context='user-reported-unreset', directory=tmp_path)
    assert not list(tmp_path.iterdir())


def test_refresh_boundary_stops_before_next_component(tmp_path, monkeypatch):
    from datetime import datetime
    from test_game_day_policy import freeze
    clock = freeze(monkeypatch, chain, '2026-09-24T04:59:59+08:00')
    original = success
    def crossed(name):
        clock[0] = datetime.fromisoformat('2026-09-24T05:00:00+08:00')
        return original(name)
    monkeypatch.setitem(globals(), 'success', crossed)
    r, calls = invoke(tmp_path, monkeypatch)
    assert calls == ['event'] and not r['supported_chain_complete']
    assert r['steps'][1]['status'] == 'not_run'


def test_native_adapters_delegate_original_journals_each_run(tmp_path, monkeypatch):
    from pcr_maa import event, event_native, preset_execution, preset_execution_native, rewards
    paths, calls = [], []
    # Do not instantiate or read the real ledger. Only capture requested paths.
    monkeypatch.setattr(preset_execution, 'Journal', lambda p: paths.append(p) or p)
    monkeypatch.setattr(event_native, 'NativeEvent', lambda tasker, **kw: ('event', tasker, kw))
    monkeypatch.setattr(preset_execution_native, 'NativePreset', lambda tasker: ('preset', tasker))
    def event_call(port, journal, **kw):
        calls.append(('event', kw)); return success('event')
    def preset_call(port, preset, context, journal, **kw):
        calls.append((preset['kind'], kw | dict(slot=preset['slot'], context=context)))
        return success(preset['kind'])
    monkeypatch.setattr(event, 'execute_event', event_call)
    monkeypatch.setattr(preset_execution, 'execute_one', preset_call)
    monkeypatch.setattr(rewards, 'claim_rewards', lambda tasker, **kw: success('rewards'))
    for i in range(2):
        chain.execute_supported(None, config(), execute=True, game_day='2026-09-23',
            context='user-reported-unreset', directory=tmp_path/str(i))
    assert paths[:4] == paths[4:]
    assert paths[0].as_posix().endswith('.runtime/event-work/ledger')
    assert all(p.as_posix().endswith('.runtime/preset-execution-work/ledger') for p in paths[1:4])
    assert [c[1]['slot'] for c in calls[:4] if c[0] != 'event'] == [4, 2, 6]
    assert all(c[1]['execute'] and c[1]['game_day'] == '2026-09-23' and
               c[1]['context'] == 'user-reported-unreset' for c in calls)
    assert all(c[1]['fit_remaining'] for c in calls if c[0] != 'event')


@pytest.mark.skipif(os.environ.get('PCR_ALLOW_PRIVATE_REPLAY') != '1',
                    reason='private historical frames require explicit parent opt-in')
def test_custom_controller_historical_rewards_wiring(tmp_path, monkeypatch):
    from pathlib import Path
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    live = (Path(__file__).resolve().parents[1] / '.runtime/private-fixtures/hermes-pcr/live')
    names = ['missions-claimed-home', 'missions-before-claim', 'missions-claim-result',
             'missions-after-claim', 'missions-claimed-home', 'gifts-before-claim',
             'gifts-claim-dialog', 'gifts-claim-result', 'gifts-after-claim', 'claimed-final-home']
    controller = SavedImages([[native.read_image(live/('rewards-'+n+'.png'))] for n in names])
    tasker = native.bind(native.load_resource(), controller)
    original = chain._run_step
    def run(tasker, name, *args, **kwargs):
        return original(tasker, name, *args, **kwargs) if name == 'rewards' else success(name)
    monkeypatch.setattr(chain, '_run_step', run)
    r = chain.execute_supported(tasker, config(), execute=True, game_day='2026-09-23',
        context='user-reported-unreset', directory=tmp_path, source='historical rewards replay; other steps fake')
    assert r['supported_chain_complete'] and len(controller.clicks) == 9
    assert all(x['status'] == 'claimed' for x in r['steps'][2]['result']['items'].values())


def test_cli_optin_wiring_and_device_validation(tmp_path, monkeypatch, capsys):
    from pcr_maa import device
    from pcr_maa.cli import main
    cfg = tmp_path/'config.json'
    cfg.write_text(json.dumps(config()), encoding='utf-8')
    calls = []
    def navigate(**kw):
        calls.append(kw)
        return dict(outcome='complete', supported_chain_complete=True, daily_complete=False, steps=[])
    original = device.navigate
    monkeypatch.setattr(device, 'navigate', navigate)
    assert main(['execute-supported', '--config', str(cfg), '--game-day', '2026-09-23',
                 '--count-context', 'user-reported-unreset', '--execute']) == 0
    assert calls[0]['supported']['execute'] is True
    assert [p['slot'] for p in calls[0]['supported']['config']['presets']] == [4, 2, 6]
    out = capsys.readouterr().out
    assert out.count('日程执行：unsupported') == 2 and 'daily_complete=false' in out
    with pytest.raises(ValueError):
        original(2, '123', supported=dict(config=config(), execute=False, game_day='2026-09-23',
                 context='user-reported-unreset', directory=tmp_path))
    with pytest.raises(ValueError):
        original(2, '123', slot=1, supported=dict(config=config(), execute=True, game_day='2026-09-23',
                 context='user-reported-unreset', directory=tmp_path))


def test_schedule_cli_flags_and_pretasker_rejections(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import device, native, schedule_onekey
    from pcr_maa.cli import main
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps(config()), encoding='utf-8')
    monkeypatch.setattr(native, 'setup', lambda: pytest.fail('native setup before validation'))
    monkeypatch.setattr(device, 'navigate', lambda *a, **k: pytest.fail('device construction'))
    base = ['execute-supported', '--config', str(cfg), '--game-day', '2027-09-24',
            '--count-context', 'user-reported-unreset']
    for flags in (['--include-schedule-onekey'],
                  ['--execute', '--schedule-authorize', 'arena_coins'],
                  ['--execute', '--include-schedule-onekey'],
                  ['--execute', '--include-schedule-onekey',
                   '--schedule-authorize', 'arena_coins', '--schedule-authorize', 'arena_coins']):
        assert main(base + flags) in (1, 2)
    assert main(base[:-3] + ['2026-09-24', '--count-context', 'user-reported-unreset',
        '--execute', '--include-schedule-onekey', '--schedule-authorize', 'arena_coins']) == 1
    with pytest.raises(SystemExit) as exc:
        main(base + ['--execute', '--include-schedule-onekey', '--schedule-authorize', 'paid_gacha'])
    assert exc.value.code == 2


def test_schedule_cli_routes_explicit_authorizations_and_same_device(tmp_path, monkeypatch, capsys):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import device, schedule_onekey
    from pcr_maa.cli import main
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps(config()), encoding='utf-8')
    calls = []
    monkeypatch.setattr(device, 'navigate', lambda **kw: calls.append(kw) or
        dict(outcome='complete', supported_chain_complete=True, daily_complete=False, steps=[]))
    flags = ['--execute', '--include-schedule-onekey', '--schedule-authorize', 'arena_coins',
             '--schedule-authorize', 'normal_gacha_free']
    assert main(['execute-supported', '--config', str(cfg), '--game-day', '2027-09-24',
                 '--count-context', 'user-reported-unreset', *flags]) == 0
    assert len(calls) == 1
    assert calls[0]['supported']['include_schedule_onekey'] is True
    assert calls[0]['supported']['schedule_authorizations'] == ['arena_coins', 'normal_gacha_free']
    assert calls[0]['input_display'] == 2 and calls[0]['screenshot_display'] == '123'
    assert '显式可选日程一键' in capsys.readouterr().out


def test_schedule_device_dispatch_rejects_before_resource_or_controller(tmp_path, monkeypatch):
    from pcr_maa import device, native
    monkeypatch.setattr(native, 'load_resource', lambda: pytest.fail('resource constructed'))
    request = dict(config=config(), execute=True, game_day='2026-09-24',
                   context='user-reported-unreset', directory=tmp_path,
                   include_schedule_onekey=True, schedule_authorizations=['arena_coins'])
    with pytest.raises(ValueError, match='current_game_day_required'):
        device.navigate(2, '123', supported=request)
    with pytest.raises(ValueError, match='Invalid or conflicting'):
        device.navigate(2, '123', supported=request, onekey=dict(
            execute=True, game_day='2026-09-24', authorizations=['arena_coins']))
    request['schedule_authorizations'] = []
    request['game_day'] = '2027-09-24'
    with pytest.raises(ValueError):
        device.navigate(2, '123', supported=request)


def test_real_event_journal_reused_after_unknown(tmp_path, monkeypatch):
    from test_event import Port
    from pcr_maa import event_native, preset_execution
    journal = preset_execution.Journal(tmp_path/'isolated-ledger')
    port = Port()
    port.journal = journal
    port.result_ok = False
    monkeypatch.setattr(event_native, 'NativeEvent', lambda *a, **kw: port)
    monkeypatch.setattr(preset_execution, 'Journal', lambda path: journal)
    for i in range(2):
        r = chain.execute_supported(None, config(), execute=True, game_day='2026-09-23',
            context='user-reported-unreset', directory=tmp_path/str(i))
        assert all(s['status'] == 'not_run' for s in r['steps'][1:])
    assert r['steps'][0]['result']['outcome'] == 'blocked_in_flight'
    assert port.calls.count('submit') == 1 and 'home' not in port.calls


@pytest.mark.skipif(os.environ.get('PCR_ALLOW_PRIVATE_REPLAY') != '1',
                    reason='private historical frames require explicit parent opt-in')
def test_custom_controller_historical_event_wiring(tmp_path, monkeypatch):
    from test_event_native import frame
    from pcr_maa import native, preset_execution
    from pcr_maa.replay import SavedImages
    names = ['event-sweep-home', 'activity-scope-adventure', 'parent-event-partial-stop',
        'event-sweep-map-after', 'event-sweep-result-closed', 'parent-event-observe-stop',
        'event-sweep-after-bottom', 'event-sweep-map-after', 'event-sweep-home']
    controller = SavedImages([[frame(n)] for n in names])
    tasker = native.bind(native.load_resource(), controller)
    journal = preset_execution.Journal(tmp_path/'isolated-ledger')
    monkeypatch.setattr(preset_execution, 'Journal', lambda path: journal)
    original = chain._run_step
    monkeypatch.setattr(chain, '_run_step', lambda tasker, name, *a, **kw:
        original(tasker, name, *a, **kw) if name == 'event' else success(name))
    r = chain.execute_supported(tasker, config(), execute=True, game_day='2026-09-23',
        context='user-reported-unreset', directory=tmp_path/'chain',
        source='historical event original PNG combination; other steps fake; not fresh live')
    assert r['steps'][0]['result']['outcome'] == 'skipped_goal_met'
    assert r['supported_chain_complete'] and len(controller.clicks) == 6
    assert len(controller.swipes) == 2 and not journal.events()


@pytest.mark.parametrize('modules,tail', [(['rewards'], False), ([], True)])
def test_reward_only_system_date_no_unreset(tmp_path, monkeypatch, modules, tail):
    calls = []
    monkeypatch.setattr(chain, '_run_step', lambda t, n, c, d, ctx, **kw: calls.append((n,d,ctx)) or success(n))
    result = chain.execute_supported(None, config(), execute=True, game_day='', context='',
        selected_modules=modules, rewards_final=tail, directory=tmp_path)
    assert result['outcome'] == 'complete'
    assert result['game_day'] == '2026-09-23' and result['game_day_source'] == 'system-derived'
    assert result['count_context_source'] == 'not-applicable'
    assert calls == [(modules[0] if modules else 'rewards_final', '2026-09-23', '')]
    assert chain._read_report(tmp_path) == result


@pytest.mark.parametrize('missing', ['game_day', 'authorizations'])
def test_rewards_does_not_supply_schedule_permission(missing):
    from pcr_maa import schedule_onekey
    request = dict(execute=True, game_day='2026-09-23', context='',
        selected_modules=['schedule_onekey','rewards'],
        schedule_authorizations=['arena_coins'], schedule_attest_current_plan=True)
    if missing == 'game_day': request['game_day'] = ''
    else: request['schedule_authorizations'] = []
    with pytest.raises(ValueError): chain.validate(config(), **request)


def test_rewards_cannot_carry_orphan_schedule_permission():
    for extra in [dict(schedule_authorizations=['arena_coins']), dict(schedule_attest_current_plan=True)]:
        with pytest.raises(ValueError):
            chain.validate(config(), execute=True, game_day='', context='', selected_modules=['rewards'], **extra)


@pytest.mark.parametrize('fault', ['resources', 'unauthorized', 'missing_attestation'])
def test_rewards_mixed_schedule_preserves_native_budget_and_permission(tmp_path, monkeypatch, fault):
    from datetime import datetime, timezone, timedelta
    from pcr_maa import native, schedule_onekey
    from pcr_maa.preset_execution import Journal
    from test_schedule_onekey import attested_controller
    monkeypatch.setattr(schedule_onekey, 'now_cn',
        lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    journal = Journal(tmp_path/'synthetic-ledger')
    original = schedule_onekey.schedule_onekey
    monkeypatch.setattr(schedule_onekey, 'schedule_onekey',
        lambda tasker, **kw: original(tasker, journal=journal, **kw))
    calls = []
    monkeypatch.setattr(chain, '_run_step', lambda t, n, *a, **kw: calls.append(n) or success(n))
    tasker, controller = attested_controller(native.load_resource(), monkeypatch,
        pending=('memorial_rewards',), fault=fault if fault != 'missing_attestation' else None)
    report = chain.execute_supported(tasker, config(), execute=True, game_day='2027-09-24',
        context='', directory=tmp_path/'run', selected_modules=['schedule_onekey','rewards'],
        schedule_authorizations=['arena_coins'] if fault == 'unauthorized' else schedule_onekey.ITEMS,
        schedule_attest_current_plan=fault != 'missing_attestation')
    result = report['steps'][0]['result']
    assert 'onekey' not in result['sent'] and calls == []
    assert not journal.events() and not report['supported_chain_complete']
    assert result['authorizations'] == (['arena_coins'] if fault == 'unauthorized' else list(schedule_onekey.ITEMS))
    assert report['count_context_source'] == 'not-applicable'


@pytest.mark.parametrize('step_name', ['rewards', 'rewards_final'])
@pytest.mark.parametrize('item_name,label', [('missions', '每日任务'), ('gifts', '礼物')])
@pytest.mark.parametrize('receipt,expected', [
    pytest.param(dict(claim_attempted=True, result_verified=False, home_verified=False),
        '已尝试领取，结果尚未确认。', id='attempted-result-unconfirmed'),
    pytest.param(dict(claim_attempted=False, result_verified=False, home_verified=False),
        '未尝试领取。', id='explicitly-not-attempted'),
    pytest.param(dict(claim_attempted=True, result_verified=True, home_verified=False),
        '领取结果已确认，返回主页尚未确认。', id='result-confirmed-home-unconfirmed'),
    pytest.param(dict(claim_attempted=True, result_verified=True),
        '领取结果已确认。', id='result-confirmed-home-missing'),
    pytest.param(dict(claim_attempted=True), '已尝试领取。', id='result-missing'),
    pytest.param({}, None, id='all-fields-missing'),
    pytest.param(dict(result_verified=False, home_verified=False), None, id='attempt-field-missing'),
    pytest.param(dict(claim_attempted=None, result_verified=None, home_verified=None),
        None, id='null-is-not-false'),
    pytest.param(dict(claim_attempted=1, result_verified=False, home_verified=False),
        None, id='attempt-integer-is-not-true'),
    pytest.param(dict(claim_attempted=True, result_verified=0, home_verified=False),
        '已尝试领取。', id='result-integer-is-not-false'),
    pytest.param(dict(claim_attempted=True, result_verified=True, home_verified=0),
        '领取结果已确认。', id='home-integer-is-not-false'),
])
def test_summary_reward_failure_reports_only_known_item_facts(step_name, item_name, label, receipt, expected):
    report = dict(scope='test supported scope', steps=[
        dict(name='event', status='executed', result={}),
        dict(name=step_name, status='stopped', result=dict(
            reason='scope_failed:claim_unknown', items={item_name: receipt})),
    ], supported_chain_complete=False, daily_complete=False)
    before = deepcopy(report)
    text = chain.summary(report)
    facts = [line for line in text.splitlines() if line.startswith(('每日任务：', '礼物：'))]
    assert facts == ([] if expected is None else [label + '：' + expected])
    assert '活动：已执行并结算' in text
    assert '；原因：scope_failed:claim_unknown' in text
    assert '结果未确认时不要直接重跑、删账或换日' in text
    assert 'supported_chain_complete=false' in text and 'daily_complete=false' in text
    assert report == before


@pytest.mark.parametrize('step_name', ['rewards', 'rewards_final'])
@pytest.mark.parametrize('status', ['stopped', 'failed', 'interrupted'])
@pytest.mark.parametrize('result', [{}, {'items': {}}, {'items': None},
    {'items': {'missions': None, 'gifts': None}}])
def test_summary_reward_failure_missing_items_do_not_imply_unattempted(step_name, status, result):
    report = dict(steps=[dict(name=step_name, status=status, result=result)])
    before = deepcopy(report)
    text = chain.summary(report)
    assert not any(line.startswith(('每日任务：', '礼物：')) for line in text.splitlines())
    assert '未尝试领取' not in text and '已尝试领取' not in text
    assert '结果未确认时不要直接重跑、删账或换日' in text
    assert report == before


@pytest.mark.parametrize('player', [False, True])
@pytest.mark.parametrize('state,expected_navigation', [
    ({'sent': ['enter', 'down:0'], 'one_key_attempted': False,
      'consumer_state': 'not_started'}, True),
    ({'sent': ['enter', 'down:0'], 'one_key_attempted': False,
      'consumer_state': 'not_started', 'native_input_state': 'unknown'}, True),
    ({'sent': ['enter'], 'one_key_attempted': False,
      'consumer_state': 'in_flight'}, False),
    ({'sent': ['enter', 'onekey'], 'one_key_attempted': False,
      'consumer_state': 'not_started'}, False),
    ({'sent': ['enter'], 'one_key_attempted': True,
      'consumer_state': 'not_started'}, False),
    ({'sent': ['enter']}, False),
    ({'sent': ['enter'], 'one_key_attempted': False,
      'consumer_state': 'not_started', 'original_execution_unresolved': True}, False),
])
def test_summary_schedule_navigation_is_not_consumer_intent(player, state, expected_navigation):
    result = dict(outcome='unknown', reason='scope_mismatch', **state)
    report = dict(outcome='stopped', steps=[
        dict(name='schedule_onekey', status='stopped', result=result),
        dict(name='event', status='not_run')])
    before = deepcopy(report)
    text = chain.summary(report, player=player)
    if expected_navigation:
        assert '未发送一键自动或消费确认' in text
        assert '结果未知时禁止直接重跑' not in text
    else:
        assert '未发送一键自动或消费确认' not in text
        assert '结果未知时禁止直接重跑' in text
    assert '活动：未运行' in text
    assert report == before
