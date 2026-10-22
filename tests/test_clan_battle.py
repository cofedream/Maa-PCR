"""Synthetic port decisions; these tests provide no live-game acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import pytest
from pcr_maa.clan_battle import (execute_simulation, execute_switches, load_snapshot,
    plan_hash, select_support, support_requirement, validate_plan)
from pcr_maa.preset_execution import Journal


def bind(plan):
    plan['confirmed_for'] = plan_hash(plan)
    return plan


@pytest.fixture
def plan(tmp_path):
    image = tmp_path / 'source.png'
    image.write_bytes(b'synthetic source image, never a live frame')
    snapshot = tmp_path / 'snapshot.json'
    snapshot.write_text(json.dumps(dict(schema_version=1, server='cn-bilibili', account_id='123456789',
        content_version='202610141431', observed_at=datetime.now(timezone.utc).isoformat(),
        ownership_complete=True, units=[dict(unit_id=uid, battle_rarity=5) for uid in range(100101, 100106)],
        unknown_fields=['battle_settings'])), encoding='utf-8')
    return bind(dict(version=1, server='cn-bilibili', account_id='123456789', content_version='202610141431',
        period='2026-09', phase='BC', round_min=4, round_max=10, boss=3, boss_name='', package='套餐A',
        team_label='三王一队', unit_ids=list(range(100101, 100106)), stars=[5] * 5, auto=True,
        set_by_unit={str(uid): True for uid in range(100101, 100106)}, switches=[],
        support_mode='qsjs', support_unit_id=None, reference_damage=5000000,
        source=dict(url='https://www.bilibili.com/video/BV1Rbh86XEsq/', bvid='BV1Rbh86XEsq',
            page=1, cid=42138340822, at_seconds=2, image_path=str(image),
            image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(), job_id='synthetic-bc-team1'),
        snapshot=dict(path=str(snapshot), sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest())))


def snapshot_update(plan, update):
    path = __import__('pathlib').Path(plan['snapshot']['path'])
    value = json.loads(path.read_text(encoding='utf-8'))
    update(value)
    path.write_text(json.dumps(value), encoding='utf-8')
    plan['snapshot']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    bind(plan)


def reading(plan, **override):
    result = {key: deepcopy(plan[key]) for key in ('account_id', 'server', 'period', 'unit_ids', 'stars', 'boss')}
    result.update(phase='B', round=4, simulation=True, support_unit_id=None,
        native_evidence={'source': 'synthetic_port_readback'})
    result.update(override)
    return result


class Port:
    def __init__(self, plan, *, wrong=None, fail=None):
        self.plan, self.wrong, self.fail = plan, wrong or {}, fail
        self.prepared = self.starts = self.checks = 0
        self.returns = 0

    def checkpoint(self):
        self.checks += 1
        if self.fail == 'stop':
            raise RuntimeError('stopped')

    def prepare(self, plan):
        self.prepared += 1
        if self.fail == 'closed':
            raise RuntimeError('clan_battle_closed')

    def verify_team(self, plan):
        return reading(plan, **self.wrong)

    def start_and_read(self, plan):
        self.starts += 1
        if self.fail:
            raise RuntimeError(self.fail)
        return dict(reading(plan), result_verified=True, settings_verified=True, damage=12345678,
            settings_evidence=dict(settings_verified=True, auto=plan['auto'], set_by_unit=plan['set_by_unit'],
                switch_evidence=[], observation=dict(auto=plan['auto'], unit_ids=plan['unit_ids'], sets=list(plan['set_by_unit'].values())),
                opening_evidence=dict(auto=plan['auto'], unit_ids=plan['unit_ids'], sets=list(plan['set_by_unit'].values()))),
            result_evidence={'source': 'synthetic_result'})

    def return_to_target(self, plan):
        self.returns += 1
        return dict(reading(plan), return_verified=True,
            native_evidence={'source': 'synthetic_return_target'})


def test_accepts_any_period_source_with_confirmed_hash(plan):
    assert validate_plan(plan) == plan
    plan['period'] = '2027-01'
    assert validate_plan(bind(plan))['period'] == '2027-01'


@pytest.mark.parametrize('field,value', [('boss', 0), ('phase', 'unknown'), ('stars', [5]*4),
    ('unit_ids', [100101]*5), ('account_id', 'unknown'), ('auto', 1), ('support_mode', 'fallback'),
    ('round_max', 3), ('reference_damage', -1)])
def test_invalid_plan_stops(plan, field, value):
    plan[field] = value
    with pytest.raises(ValueError):
        validate_plan(bind(plan))


def test_confirmation_and_file_binding_are_both_required(plan):
    plan['auto'] = False
    with pytest.raises(ValueError, match='confirmation_changed'):
        validate_plan(plan)
    bind(plan)
    __import__('pathlib').Path(plan['source']['image_path']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='source_changed'):
        validate_plan(plan)


@pytest.mark.parametrize('path', ['\\\\server\\share\\source.png', '//server/share/source.png',
    'C:/source.png:hidden', '\\\\?\\C:\\source.png'])
def test_network_or_ads_paths_rejected_before_file_access(plan, path, monkeypatch):
    plan['source']['image_path'] = path
    def unexpected(*_):
        raise AssertionError('must not touch network path')
    monkeypatch.setattr('pathlib.Path.is_file', unexpected)
    with pytest.raises(ValueError, match='binding_invalid'):
        validate_plan(bind(plan))


@pytest.mark.parametrize('suffix,page', [('?p=2',1), ('?other=1',1), ('#fragment',1), ('?p=1&p=2',1)])
def test_source_link_must_match_exact_page_without_unknown_fields(plan, suffix, page):
    plan['source'].update(url='https://www.bilibili.com/video/BV1Rbh86XEsq/'+suffix, page=page)
    with pytest.raises(ValueError):
        validate_plan(bind(plan))


def test_second_page_link_is_accepted_only_when_bound(plan):
    plan['source'].update(url='https://www.bilibili.com/video/BV1Rbh86XEsq/?p=2', page=2)
    assert validate_plan(bind(plan))['source']['page'] == 2


@pytest.mark.parametrize('change,error', [
    (lambda s: s.update(account_id='999'), 'identity_mismatch'),
    (lambda s: s.update(content_version='202609141431'), 'identity_mismatch'),
    (lambda s: s.update(observed_at=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()), 'expired'),
    (lambda s: s.update(observed_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()), 'expired'),
    (lambda s: s.update(unknown_fields=['units.100101']), 'ownership_unknown'),
    (lambda s: s['units'].append(s['units'][0]), 'roster_invalid'),
])
def test_snapshot_must_prove_complete_recent_identity(plan, change, error):
    snapshot_update(plan, change)
    with pytest.raises(ValueError, match=error):
        validate_plan(plan)


def test_qsjs_uses_own_team_when_complete(plan):
    assert support_requirement(plan, load_snapshot(plan)) is None
    assert select_support(plan, load_snapshot(plan), []) is None


def test_partial_snapshot_is_enough_when_all_five_are_explicitly_owned(plan):
    snapshot_update(plan, lambda s: s.update(ownership_complete=False))
    assert validate_plan(plan) and support_requirement(plan, load_snapshot(plan)) is None


def test_partial_snapshot_does_not_infer_missing_unit(plan):
    snapshot_update(plan, lambda s: (s.update(ownership_complete=False), s['units'].pop()))
    with pytest.raises(RuntimeError, match='ownership_unknown'):
        support_requirement(plan, load_snapshot(plan))
    plan.update(support_mode='specified', support_unit_id=100105)
    assert support_requirement(plan, load_snapshot(plan)) == 100105


def test_specified_support_own_snapshot_unknown_does_not_block_other_four(plan):
    plan.update(support_mode='specified', support_unit_id=100105)
    snapshot_update(plan, lambda s: (s['units'].pop(), s.update(ownership_complete=False, unknown_fields=['units.100105'])))
    assert validate_plan(plan) and support_requirement(plan, load_snapshot(plan)) == 100105
    snapshot_update(plan, lambda s: s['unknown_fields'].append('units.100101'))
    with pytest.raises(ValueError, match='ownership_unknown'):
        validate_plan(plan)


@pytest.mark.parametrize('field,blocked', [('units.100101.ownership', True),
    ('units.100101.ownership.status', True), ('units.100101.battle_rarity', False),
    ('units.100101.level', False), ('units.100101.ex_equipment', False)])
def test_only_owned_identity_unknown_blocks_explicit_stars(plan, field, blocked):
    snapshot_update(plan, lambda s: s.update(unknown_fields=[field]))
    if blocked:
        with pytest.raises(ValueError, match='ownership_unknown'):
            validate_plan(plan)
    else:
        assert validate_plan(plan)


def test_specified_support_ownership_unknown_is_not_used_as_own_roster(plan):
    plan.update(support_mode='specified', support_unit_id=100105)
    snapshot_update(plan, lambda s: s.update(unknown_fields=['units.100105.ownership']))
    assert validate_plan(plan)
    assert support_requirement(plan, load_snapshot(plan)) == 100105


@pytest.mark.parametrize('field', ['ownership', 'ownership.status', 'units.ownership', 'units.ownership.status'])
@pytest.mark.parametrize('specified', [False, True])
def test_global_ownership_unknown_cannot_be_exempted_by_specified_support(plan, field, specified):
    if specified:
        plan.update(support_mode='specified', support_unit_id=100105)
    snapshot_update(plan, lambda s: s.update(unknown_fields=[field]))
    with pytest.raises(ValueError, match='ownership_unknown'):
        validate_plan(plan)


def test_one_missing_borrows_exact_unit_and_star_in_list_order(plan):
    snapshot_update(plan, lambda s: s['units'].pop())
    candidates = [dict(unit_id=100105, stars=4, available=True, support_id='wrong-star'),
        dict(unit_id=100105, stars=5, available=True, support_id='first'),
        dict(unit_id=100105, stars=5, available=True, support_id='second')]
    assert select_support(plan, load_snapshot(plan), candidates)['support_id'] == 'first'
    with pytest.raises(RuntimeError, match='unavailable'):
        select_support(plan, load_snapshot(plan), candidates[:1])


def test_two_missing_never_substitutes(plan):
    snapshot_update(plan, lambda s: s.update(units=s['units'][:3]))
    with pytest.raises(RuntimeError, match='more_than_one_missing'):
        support_requirement(plan, load_snapshot(plan))


def test_specified_support_applies_even_when_owned(plan):
    plan.update(support_mode='specified', support_unit_id=100101)
    bind(plan)
    assert support_requirement(plan, load_snapshot(plan)) == 100101
    plan['support_mode'] = 'none'
    with pytest.raises(ValueError):
        validate_plan(bind(plan))


@pytest.mark.parametrize('wrong', [dict(account_id='999'), dict(boss=2), dict(round=3),
    dict(phase='D'), dict(period='2026-10'), dict(simulation=False), dict(server='jp'),
    dict(support_unit_id=100101), dict(unit_ids=[100101]*5), dict(stars=[4]*5)])
def test_identity_or_formation_conflict_never_starts(plan, tmp_path, wrong):
    port = Port(plan, wrong=wrong)
    journal = Journal(tmp_path/'ledger')
    result = execute_simulation(port, journal, plan)
    assert result['outcome'] == 'stopped' and port.starts == 0 and journal.events() == []


def test_closed_and_stop_have_zero_battle_inputs(plan, tmp_path):
    for reason in ('closed', 'stop'):
        port = Port(plan, fail=reason)
        result = execute_simulation(port, Journal(tmp_path/reason), plan)
        assert port.starts == 0 and not result['simulation_started']
        assert result['outcome'] == ('unavailable' if reason == 'closed' else 'stopped')


@pytest.mark.parametrize('binding', ['source', 'snapshot'])
def test_prepare_file_change_is_rejected_before_intent_or_start(plan, tmp_path, binding):
    from pathlib import Path
    port = Port(plan)
    original_prepare = port.prepare

    def prepare(p):
        original_prepare(p)
        path = p['source']['image_path'] if binding == 'source' else p['snapshot']['path']
        Path(path).write_bytes(b'changed during synthetic preparation')

    port.prepare = prepare
    journal = Journal(tmp_path / 'ledger')
    result = execute_simulation(port, journal, plan)
    assert result['outcome'] == 'stopped'
    assert result['error'] == 'clan_battle_source_changed'
    assert port.prepared == 1 and port.starts == 0
    assert result['simulation_started'] is result['consumption_sent'] is False
    assert journal.events() == []


@pytest.mark.parametrize('failure', ['clan_battle_disconnected_6002:battle_start=sent', 'stopped', 'unknown_damage'])
def test_sent_unknown_is_kept_and_never_restarted(plan, tmp_path, failure):
    journal = Journal(tmp_path/'ledger')
    port = Port(plan, fail=failure)
    first = execute_simulation(port, journal, plan)
    assert first['outcome'] == 'stopped' and len(journal.events()) == 1 and port.starts == 1
    again = Port(plan)
    second = execute_simulation(again, journal, plan)
    assert second['error'] == 'clan_battle_previous_result_unknown'
    assert again.starts == again.prepared == 0


def test_verified_damage_settles_and_new_explicit_start_is_allowed(plan, tmp_path):
    journal = Journal(tmp_path/'ledger')
    for _ in range(2):
        port = Port(plan)
        result = execute_simulation(port, journal, plan)
        assert result['outcome'] == 'simulated' and result['damage'] == 12345678
        assert result['damage_verified'] and result['settings_verified'] and port.starts == 1
    assert len(journal.events()) == 4
    assert all(event['operation'] == 'clan_battle_simulation' for event in journal.events())


def test_owner_report_uses_independent_clan_ledger(plan, tmp_path, monkeypatch):
    from pcr_maa.clan_battle import run_owned
    old = tmp_path/'.runtime/guide-trial-work/ledger/transactions.jsonl'
    old.parent.mkdir(parents=True)
    original = b'preserved unrelated synthetic guide ledger\n'
    old.write_bytes(original)
    port = Port(plan)
    monkeypatch.setattr('pcr_maa.native.ROOT', tmp_path)
    monkeypatch.setattr('pcr_maa.clan_battle_calibration.load_calibration', lambda _: {'synthetic': True})
    monkeypatch.setattr('pcr_maa.clan_battle_native.NativeClanBattle', lambda *_, **kwargs: port)
    report = run_owned(None, {'clan_battle_simulation': plan}, tmp_path/'report')
    assert report['outcome'] == 'complete' and report['supported_chain_complete']
    assert report['selected_modules'] == ['clan_battle_simulation']
    assert report['daily_complete'] is report['rewards_final'] is False
    assert old.read_bytes() == original and port.starts == 1
    assert (tmp_path/'.runtime/clan-battle-simulation-work/ledger/transactions.jsonl').is_file()
    assert json.loads((tmp_path/'report/report.json').read_text(encoding='utf-8')) == report


def test_wrong_controls_evidence_does_not_settle(plan, tmp_path):
    port = Port(plan)
    original = port.start_and_read
    def wrong(p):
        result = original(p)
        result['settings_evidence']['opening_evidence']['auto'] = False
        return result
    port.start_and_read = wrong
    journal = Journal(tmp_path/'ledger')
    assert execute_simulation(port, journal, plan)['outcome'] == 'stopped'
    assert len(journal.events()) == 1


@pytest.mark.parametrize('phase,battle_start,settled', [('hud_toggle','sent',False),
    ('simulation_start','sent',False), ('simulation_start','not_sent',True)])
def test_not_sent_evidence_only_closes_unsent_start(plan, tmp_path, phase, battle_start, settled):
    from pcr_maa.native import NativeInputNotSent
    port = Port(plan)
    port.battle_start = battle_start
    def fail(p):
        port.starts += 1
        raise NativeInputNotSent(dict(input_execution='not_sent', clan_battle_phase=phase))
    port.start_and_read = fail
    journal = Journal(tmp_path/'ledger')
    result = execute_simulation(port, journal, plan)
    assert len(journal.events()) == (2 if settled else 1)
    assert result['simulation_started'] is (not settled)
    if not settled:
        after = Port(plan)
        assert execute_simulation(after, journal, plan)['error'] == 'clan_battle_previous_result_unknown'
        assert after.starts == after.prepared == 0


class Controls:
    timing_capability = dict(validated=True, max_observation_interval_ms=10,
        max_action_latency_ms=50, source='synthetic bounded clock')

    def __init__(self, plan, ticks=(), *, no_change=False):
        self.state = dict(unit_ids=list(reversed(plan['unit_ids'])), auto=plan['auto'], sets=[True]*5)
        self.ticks, self.actions, self.no_change = iter(ticks), [], no_change
        self.requested_seconds = []

    def checkpoint(self):
        pass

    def read_battle_settings(self, receipt=None):
        return deepcopy(self.state)

    def toggle_auto(self, previous, desired, *, remaining_seconds=None):
        self.requested_seconds.append(remaining_seconds)
        self.actions.append(('auto', previous, desired))
        if not self.no_change:
            self.state['auto'] = desired
        return deepcopy(self.state)

    def toggle_set(self, slot, previous, desired, *, remaining_seconds=None):
        self.requested_seconds.append(remaining_seconds)
        self.actions.append(('set', self.state['unit_ids'][slot], previous, desired))
        if not self.no_change:
            self.state['sets'][slot] = desired
        return deepcopy(self.state)

    def read_tick(self):
        return dict(remaining_seconds=next(self.ticks), state=deepcopy(self.state))


def test_correct_opening_controls_produces_zero_toggles(plan):
    port = Controls(plan)
    assert execute_switches(port, plan)['settings_verified'] and not port.actions


def test_timed_axis_uses_unit_identity_and_can_disable_auto(plan):
    plan['switches'] = [dict(remaining_seconds=85, auto=False, set_by_unit={'100101':False})]
    port = Controls(plan, [89, 86, 85, 85, 85, 85, 85, 85])
    result = execute_switches(port, plan)
    assert result['settings_verified'] and result['auto'] is False
    assert port.actions == [('auto', True, False), ('set', 100101, True, False)]
    assert port.requested_seconds == [85, 85]
    assert result['switch_evidence'][0]['remaining_seconds'] == 85


@pytest.mark.parametrize('target_auto', [False, True])
def test_opening_finishes_set_readback_before_enabling_auto(plan, target_auto):
    plan['auto'] = target_auto
    plan['set_by_unit']['100101'] = False
    port = Controls(plan)
    port.state['auto'] = not target_auto
    result = execute_switches(port, plan)
    set_action = ('set', 100101, True, False)
    auto_action = ('auto', not target_auto, target_auto)
    assert port.actions == ([set_action, auto_action] if target_auto else [auto_action, set_action])
    assert port.requested_seconds == [None, None]
    assert result['settings_verified'] and result['opening_evidence']['auto'] is target_auto
    assert dict(zip(result['observation']['unit_ids'], result['observation']['sets']))[100101] is False


def test_timed_axis_finishes_all_set_readbacks_before_enabling_auto(plan):
    plan['auto'] = False
    plan['switches'] = [dict(remaining_seconds=85, auto=True,
        set_by_unit={'100101': False, '100103': False})]
    port = Controls(plan, [85] * 9)
    result = execute_switches(port, plan)
    assert port.actions == [('set', 100101, True, False), ('set', 100103, True, False), ('auto', False, True)]
    assert port.requested_seconds == [85, 85, 85]
    evidence = result['switch_evidence'][0]
    assert evidence['before']['auto'] is False and evidence['after']['auto'] is True
    assert result['set_by_unit']['100101'] is result['set_by_unit']['100103'] is False


@pytest.mark.parametrize('failure,error', [('not_applied', 'not_applied'),
    ('unknown', 'controls_unknown'), ('stopped', 'stopped'), ('missed', 'window_missed')])
def test_failed_set_transition_never_enables_auto(plan, failure, error):
    plan['auto'] = False
    plan['switches'] = [dict(remaining_seconds=85, auto=True, set_by_unit={'100101': False})]
    port = Controls(plan, [85, 85, 84] if failure == 'missed' else [85] * 8,
        no_change=failure == 'not_applied')
    toggle_set = port.toggle_set
    def observed_set(*args, **kwargs):
        observed = toggle_set(*args, **kwargs)
        if failure == 'unknown':
            observed['sets'][0] = None
        return observed
    port.toggle_set = observed_set
    def checkpoint():
        if failure == 'stopped' and port.actions:
            raise RuntimeError('stopped')
    port.checkpoint = checkpoint
    with pytest.raises(RuntimeError, match=error):
        execute_switches(port, plan)
    assert port.actions == [('set', 100101, True, False)]
    assert port.state['auto'] is False


@pytest.mark.parametrize('sets,actual_seconds,actions', [({}, [84], []),
    ({'100101': False}, [84], []),
    ({'100101': False}, [85, 84], [('set', 100101, True, False)])])
def test_target_second_reaches_final_input_guard_before_each_toggle(plan, sets, actual_seconds, actions):
    plan['auto'] = False
    plan['switches'] = [dict(remaining_seconds=85, auto=True, set_by_unit=sets)]
    class SameFrameControls(Controls):
        def guard(self, remaining_seconds):
            if remaining_seconds is not None and next(frames) != remaining_seconds:
                raise RuntimeError('clan_battle_switch_window_missed')

        def toggle_auto(self, previous, desired, *, remaining_seconds=None):
            self.guard(remaining_seconds)
            return super().toggle_auto(previous, desired, remaining_seconds=remaining_seconds)

        def toggle_set(self, slot, previous, desired, *, remaining_seconds=None):
            self.guard(remaining_seconds)
            return super().toggle_set(slot, previous, desired, remaining_seconds=remaining_seconds)
    frames = iter(actual_seconds)
    port = SameFrameControls(plan, [85] * 12)
    with pytest.raises(RuntimeError, match='window_missed'):
        execute_switches(port, plan)
    assert port.actions == actions and port.state['auto'] is False
    assert port.requested_seconds == [85] * len(actions)


def test_correct_timed_controls_produce_zero_toggles(plan):
    plan['switches'] = [dict(remaining_seconds=85, auto=True, set_by_unit={'100101': True})]
    port = Controls(plan, [85, 85])
    result = execute_switches(port, plan)
    assert result['settings_verified'] and not port.actions
    assert len(result['switch_evidence']) == 1


@pytest.mark.parametrize('fault', ['final_unknown', 'final_wrong', 'missing_switch', 'wrong_second'])
def test_settings_result_requires_actual_final_and_all_switch_observations(plan, fault):
    from pcr_maa.clan_battle import _settings
    plan['switches'] = [dict(remaining_seconds=85, auto=False, set_by_unit={})]
    evidence = execute_switches(Controls(plan, [85, 85, 85, 85]), plan)
    if fault == 'final_unknown':
        evidence['observation'] = None
    elif fault == 'final_wrong':
        evidence['observation']['auto'] = True
    elif fault == 'missing_switch':
        evidence['switch_evidence'] = []
    else:
        evidence['switch_evidence'][0]['confirmed_remaining_seconds'] = 84
    try:
        assert _settings(dict(settings_evidence=evidence), plan) is False
    except RuntimeError as exc:
        assert fault == 'final_unknown' and str(exc) == 'clan_battle_controls_unknown'


def test_missed_second_stops_without_late_toggle(plan):
    plan['switches'] = [dict(remaining_seconds=85, auto=False, set_by_unit={})]
    port = Controls(plan, [86, 84])
    with pytest.raises(RuntimeError, match='switch_missed'):
        execute_switches(port, plan)
    assert not port.actions


def test_window_ends_after_first_toggle_does_not_send_second(plan):
    plan['switches'] = [dict(remaining_seconds=85, auto=False, set_by_unit={'100101':False})]
    port = Controls(plan, [85, 85, 84])
    with pytest.raises(RuntimeError, match='window_missed'):
        execute_switches(port, plan)
    assert port.actions == [('auto', True, False)]


def test_axis_unknown_observed_state_stops_without_second_read_or_toggle(plan):
    plan['switches'] = [dict(remaining_seconds=85, auto=False, set_by_unit={})]
    port = Controls(plan)
    port.read_tick = lambda: dict(remaining_seconds=85, state=None)
    with pytest.raises(RuntimeError, match='controls_unknown'):
        execute_switches(port, plan)
    assert not port.actions


def test_stalled_clock_has_bounded_wait_and_no_toggle(plan, monkeypatch):
    plan['switches'] = [dict(remaining_seconds=85, auto=False, set_by_unit={})]
    clock = iter([0, 121])
    monkeypatch.setattr('pcr_maa.clan_battle.time.monotonic', lambda: next(clock))
    port = Controls(plan)
    with pytest.raises(RuntimeError, match='clock_timeout'):
        execute_switches(port, plan)
    assert not port.actions


def test_toggle_not_applied_is_never_resent(plan):
    plan['set_by_unit']['100101'] = False
    port = Controls(plan, no_change=True)
    with pytest.raises(RuntimeError, match='not_applied'):
        execute_switches(port, plan)
    assert len(port.actions) == 1


def test_uncalibrated_timing_rejects_axis_without_input(plan):
    plan['switches'] = [dict(remaining_seconds=85, auto=False, set_by_unit={})]
    port = Controls(plan)
    port.timing_capability = dict(validated=False)
    with pytest.raises(RuntimeError, match='timing_not_calibrated'):
        execute_switches(port, plan)
    assert not port.actions


@pytest.mark.parametrize('latency,accepted', [(199, True), (200, False)])
def test_whole_switch_group_must_fit_before_the_second_ends(plan, latency, accepted):
    from pcr_maa.clan_battle import require_timing
    plan['switches'] = [dict(remaining_seconds=85, auto=False, set_by_unit={'100101':False})]
    port = Controls(plan)
    # Two changes: six clock/read intervals plus two complete input+readbacks.
    port.timing_capability = dict(validated=True, max_observation_interval_ms=100,
        max_action_latency_ms=latency, source='synthetic bounded clock')
    if accepted:
        require_timing(port, plan)
    else:
        with pytest.raises(RuntimeError, match='window_unproven'):
            require_timing(port, plan)
    assert not port.actions


def test_six_changes_do_not_pass_individually_valid_latency_limits(plan):
    from pcr_maa.clan_battle import require_timing
    plan['switches'] = [dict(remaining_seconds=85, auto=False,
        set_by_unit={str(uid):False for uid in plan['unit_ids']})]
    port = Controls(plan)
    port.timing_capability = dict(validated=True, max_observation_interval_ms=250,
        max_action_latency_ms=500, source='synthetic per-action limits only')
    with pytest.raises(RuntimeError, match='window_unproven'):
        require_timing(port, plan)


@pytest.mark.parametrize('patch', [dict(auto=None), dict(sets=[None]*5), dict(unit_ids=[100101]*5)])
def test_unknown_hud_never_toggles(plan, patch):
    port = Controls(plan)
    port.state.update(patch)
    with pytest.raises(RuntimeError, match='controls_unknown'):
        execute_switches(port, plan)
    assert not port.actions
