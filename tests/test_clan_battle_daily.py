"""Synthetic daily receipts only; no game inputs or live-rule acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta
import builtins
import socket

import pytest

from pcr_maa.clan_battle_daily import POLICY, preview_daily_plan
from pcr_maa.game_day import BEIJING

NOW = datetime(2026, 10, 20, 6, 0, tzinfo=BEIJING)
IDENTITY = dict(server='cn-bilibili', account_id='123456789', period='2026-10', game_day='2026-10-20')


@pytest.fixture
def case():
    refs, jobs = [], {}
    for slot in (1, 2, 3):
        ref = dict(slot=slot, bundle_path='C:/synthetic-only/bundle.json', bundle_sha256='a' * 64,
            job_id=f'synthetic-team-{slot}', material_sha256=str(slot) * 64, confirmed_for=str(slot + 3) * 64)
        refs.append(ref)
        jobs[str(slot)] = dict({key: ref[key] for key in ('bundle_sha256', 'job_id', 'material_sha256', 'confirmed_for')},
            server=IDENTITY['server'], account_id=IDENTITY['account_id'], period=IDENTITY['period'],
            phase='BC', round_min=4, round_max=10, boss=slot, unit_ids=list(range(slot * 100 + 1, slot * 100 + 6)),
            support_mode='none', support_unit_id=None, resolved_support_unit_id=None)
    return dict(plan=dict(version=1, policy=POLICY, slots=refs),
        resolved_jobs=jobs, now=NOW, settlements=[], grants=[],
        observation=dict(IDENTITY, observed_at=NOW.isoformat(), remaining_count=3, used_unit=[],
            carry_stock_indices=[], bosses=[dict(boss=slot, round=5, phase='B', available=True) for slot in (1, 2, 3)]))


def run(case):
    result = preview_daily_plan(**case)
    assert result['offline_only'] is True
    assert result['executable'] is False
    return result


def settle(case, slot, *, kind='regular', stock_index=None):
    job = case['resolved_jobs'][str(slot)]
    row = dict(IDENTITY, kind=kind, operation=f'clan_battle_{kind}', slot=slot, intent_id=f'synthetic-{kind}-{slot}', status='settled',
        consumption_verified=True, boss=job['boss'], round=4 if kind == 'regular' else 5, phase='B', unit_ids=list(job['unit_ids']),
        support_unit=job['resolved_support_unit_id'], support_owner_viewer_id='987654321' if job['resolved_support_unit_id'] else None,
        stock_index=stock_index)
    case['settlements'].append(row)
    if kind == 'regular':
        case['observation']['remaining_count'] -= 1
        case['observation']['used_unit'].extend(uid for uid in row['unit_ids'] if uid != row['support_unit'])
    else:
        case['observation']['carry_stock_indices'].remove(stock_index)
    return row


def grant(case, source):
    row = dict(IDENTITY, source_slot=source['slot'], source_intent_id=source['intent_id'], boss=source['boss'],
        stock_index=source['slot'] - 1, time=34, using_unit=list(source['unit_ids']),
        support_unit=source['support_unit'], support_owner_viewer_id=source['support_owner_viewer_id'], change_phase=0)
    case['grants'].append(row)
    case['observation']['carry_stock_indices'].append(row['stock_index'])
    return row


def test_three_targets_only_suggests_first_regular(case):
    original = deepcopy(case)
    assert run(case) == dict(status='suggest_regular', offline_only=True, executable=False,
        held_carryovers=[], slot=1, boss=1, round=5)
    assert case == original


@pytest.mark.parametrize('count', [0, 1, 2])
def test_partial_target_configuration_is_a_draft(case, count):
    case['plan']['slots'] = case['plan']['slots'][:count]
    case['resolved_jobs'] = {key: job for key, job in case['resolved_jobs'].items() if int(key) <= count}
    case['observation'] = None
    assert run(case)['status'] == 'draft'


def test_missing_job_resolution_requires_original_review(case):
    del case['resolved_jobs']['3']
    assert run(case)['status'] == 'needs_review'


@pytest.mark.parametrize('field', ['bundle_sha256', 'job_id', 'material_sha256', 'confirmed_for'])
def test_changed_source_material_or_confirmation_is_not_resolved(case, field):
    case['resolved_jobs']['1'][field] = 'changed'
    with pytest.raises(ValueError, match='job_reference_changed'):
        run(case)


@pytest.mark.parametrize('field,value', [('account_id', '999'), ('period', '2026-09')])
def test_different_account_or_period_cannot_form_one_daily_plan(case, field, value):
    case['resolved_jobs']['2'][field] = value
    with pytest.raises(ValueError, match='plan_identity_mismatch'):
        run(case)


def test_observed_same_boss_new_round_is_prioritized_before_second_main(case):
    stock = grant(case, settle(case, 1))
    result = run(case)
    assert result['status'] == 'suggest_carryover'
    assert result['slot'] == 1 and result['stock_index'] == stock['stock_index']
    assert result['time'] == 34


@pytest.mark.parametrize('fault', ['absent', 'unavailable', 'same_appearance', 'wrong_phase', 'outside_round', 'phase_change'])
def test_unavailable_same_boss_keeps_carry_and_continues_next_main(case, fault):
    stock = grant(case, settle(case, 1))
    boss = case['observation']['bosses'][0]
    if fault == 'absent':
        case['observation']['bosses'].pop(0)
    elif fault == 'unavailable':
        boss['available'] = False
    elif fault == 'same_appearance':
        boss['round'] = 4
    elif fault == 'wrong_phase':
        boss['phase'] = 'D'
    elif fault == 'outside_round':
        boss['round'] = 11
    else:
        stock['change_phase'] = 1
    result = run(case)
    assert result['status'] == 'suggest_regular' and result['slot'] == 2
    assert len(result['held_carryovers']) == 1
    assert case['grants'] == [stock]


def test_same_boss_can_appear_later_after_held_preview_without_state_mutation(case):
    grant(case, settle(case, 1))
    case['observation']['bosses'][0]['available'] = False
    assert run(case)['status'] == 'suggest_regular'
    case['observation']['bosses'][0]['available'] = True
    assert run(case)['status'] == 'suggest_carryover'


def test_three_consumed_main_targets_and_actual_zero_remind_held_carry(case):
    grant(case, settle(case, 1))
    settle(case, 2)
    settle(case, 3)
    case['observation']['bosses'][0]['available'] = False
    result = run(case)
    assert result['status'] == 'remind_carryover'
    assert result['remaining_count'] == 0
    assert [row['slot'] for row in result['held_carryovers']] == [1]


def test_three_actual_consumed_targets_without_carry_are_complete(case):
    for slot in (1, 2, 3):
        settle(case, slot)
    assert run(case)['status'] == 'complete'


def test_carry_consumption_does_not_consume_a_fourth_regular_or_create_a_grant(case):
    stock = grant(case, settle(case, 1))
    settle(case, 1, kind='carryover', stock_index=stock['stock_index'])
    result = run(case)
    assert result['status'] == 'suggest_regular' and result['slot'] == 2
    assert case['observation']['remaining_count'] == 2
    assert len(case['grants']) == 1


@pytest.mark.parametrize('kind,status,verified', [
    ('regular', 'unknown', False), ('regular', 'in_flight', False), ('regular', 'settled', False),
    ('carryover', 'unknown', False), ('carryover', 'in_flight', False), ('carryover', 'settled', False)])
def test_any_unknown_or_unverified_consumption_stops_without_resend(case, kind, status, verified):
    source = settle(case, 1)
    if kind == 'carryover':
        stock = grant(case, source)
        source = settle(case, 1, kind='carryover', stock_index=stock['stock_index'])
    source.update(status=status, consumption_verified=verified)
    case['observation'] = None
    assert run(case)['reason'] == 'result_or_consumption_unknown'


@pytest.mark.parametrize('fault', ['missing', 'stale', 'future', 'previous_day', 'clock_day_disagrees'])
def test_without_current_fresh_observation_does_not_generate_three_attempts(case, fault):
    if fault == 'missing':
        case['observation'] = None
    elif fault == 'stale':
        case['observation']['observed_at'] = (NOW - timedelta(seconds=61)).isoformat()
    elif fault == 'future':
        case['observation']['observed_at'] = (NOW + timedelta(seconds=1)).isoformat()
    elif fault == 'previous_day':
        case['observation']['game_day'] = '2026-10-19'
    else:
        case['now'] = NOW.replace(hour=4)
        case['observation']['observed_at'] = case['now'].isoformat()
    assert run(case)['status'] == 'awaiting_observation'


def test_beijing_five_am_boundary_reuses_original_game_day_helper(case):
    case['now'] = NOW.replace(hour=4, minute=59)
    case['observation']['observed_at'] = case['now'].isoformat()
    case['observation']['game_day'] = '2026-10-19'
    assert run(case)['status'] == 'suggest_regular'
    case['now'] += timedelta(minutes=1)
    assert run(case)['status'] == 'awaiting_observation'


def test_naive_now_has_same_explicit_beijing_semantics(case):
    case['now'] = NOW.replace(tzinfo=None)
    assert run(case)['status'] == 'suggest_regular'


def test_unknown_result_stays_blocked_across_day_boundary(case):
    row = settle(case, 1)
    row.update(status='unknown', consumption_verified=False, game_day='2026-10-19')
    assert run(case)['status'] == 'blocked'


def test_previous_day_settled_history_is_not_counted_as_today(case):
    settle(case, 1)['game_day'] = '2026-10-19'
    with pytest.raises(ValueError, match='result_day_mismatch'):
        run(case)


@pytest.mark.parametrize('remaining', [0, 1, 2])
def test_manual_or_missing_regular_history_is_not_assumed_unconsumed(case, remaining):
    case['observation']['remaining_count'] = remaining
    assert run(case)['reason'] == 'remaining_count_history_mismatch'


@pytest.mark.parametrize('fault', ['source_intent', 'source_slot', 'boss', 'using_unit', 'support', 'wrong_day', 'duplicate_stock', 'duplicate_source'])
def test_carry_grant_requires_bound_regular_result_and_actual_team(case, fault):
    source = settle(case, 1)
    stock = grant(case, source)
    if fault == 'source_intent':
        stock['source_intent_id'] = 'not-observed'
    elif fault == 'source_slot':
        stock['source_slot'] = 2
    elif fault == 'boss':
        stock['boss'] = 2
    elif fault == 'using_unit':
        stock['using_unit'][-1] = 999
    elif fault == 'support':
        stock.update(support_unit=source['unit_ids'][0], support_owner_viewer_id='987654321')
    elif fault == 'wrong_day':
        stock['game_day'] = '2026-10-19'
    else:
        other = deepcopy(stock)
        if fault == 'duplicate_source':
            other['stock_index'] = 99
        case['grants'].append(other)
    with pytest.raises(ValueError):
        run(case)


def test_grant_cannot_be_created_from_a_carry_result(case):
    stock = grant(case, settle(case, 1))
    carry = settle(case, 1, kind='carryover', stock_index=stock['stock_index'])
    stock['source_intent_id'] = carry['intent_id']
    with pytest.raises(ValueError, match='carryover_source_mismatch'):
        run(case)


def test_same_appearance_carry_result_cannot_be_counted_as_consumed(case):
    stock = grant(case, settle(case, 1))
    settle(case, 1, kind='carryover', stock_index=stock['stock_index'])['round'] = 4
    with pytest.raises(ValueError, match='carryover_consumption_mismatch'):
        run(case)


def test_actual_simulation_result_cannot_count_as_a_regular_attack(case):
    settle(case, 1)['operation'] = 'clan_battle_simulation'
    with pytest.raises(ValueError, match='daily_result_invalid'):
        run(case)


def test_own_account_cannot_be_recorded_as_support_owner(case):
    job = case['resolved_jobs']['1']
    job.update(support_mode='specified', support_unit_id=job['unit_ids'][0], resolved_support_unit_id=job['unit_ids'][0])
    settle(case, 1)['support_owner_viewer_id'] = IDENTITY['account_id']
    with pytest.raises(ValueError, match='daily_support_invalid'):
        run(case)


def test_boolean_specified_support_id_cannot_alias_integer_unit_id(case):
    job = case['resolved_jobs']['1']
    job['unit_ids'][0] = 1
    job.update(support_mode='specified', support_unit_id=True)
    with pytest.raises(ValueError, match='daily_resolved_job_invalid'):
        run(case)


def test_no_boss_death_or_formula_fields_are_accepted(case):
    case['plan']['boss_dead'] = True
    with pytest.raises(ValueError, match='daily_plan_invalid'):
        run(case)


@pytest.mark.parametrize('fault', ['missing_stock', 'unknown_stock', 'consumed_still_present'])
def test_actual_carry_inventory_must_match_grants_and_consumption(case, fault):
    stock = grant(case, settle(case, 1))
    if fault == 'missing_stock':
        case['observation']['carry_stock_indices'] = []
    elif fault == 'unknown_stock':
        case['observation']['carry_stock_indices'].append(99)
    else:
        settle(case, 1, kind='carryover', stock_index=stock['stock_index'])
        case['observation']['carry_stock_indices'].append(stock['stock_index'])
    assert run(case)['reason'] == 'carryover_inventory_history_mismatch'


def test_specified_support_keeps_actual_owner_and_is_excluded_from_used_self(case):
    job = case['resolved_jobs']['1']
    job.update(support_mode='specified', support_unit_id=job['unit_ids'][0], resolved_support_unit_id=job['unit_ids'][0])
    source = settle(case, 1)
    grant(case, source)
    assert run(case)['status'] == 'suggest_carryover'
    assert source['support_unit'] not in case['observation']['used_unit']
    case['grants'][0]['support_owner_viewer_id'] = '111'
    with pytest.raises(ValueError, match='carryover_actual_team_mismatch'):
        run(case)


def test_qsjs_can_only_keep_actual_recorded_borrow_not_guess_a_new_one(case):
    job = case['resolved_jobs']['1']
    job.update(support_mode='qsjs', resolved_support_unit_id=job['unit_ids'][0])
    source = settle(case, 1)
    grant(case, source)
    assert run(case)['status'] == 'suggest_carryover'
    case['resolved_jobs']['2']['unit_ids'][0] = source['unit_ids'][1]
    case['observation']['bosses'][0]['available'] = False
    assert run(case)['reason'] == 'self_unit_already_used_or_qsjs_unresolved'


def test_qsjs_support_resolution_unknown_requires_original_snapshot_review(case):
    job = case['resolved_jobs']['1']
    job['support_mode'] = 'qsjs'
    del job['resolved_support_unit_id']
    assert run(case)['reason'] == 'support_resolution_required'


def test_qsjs_borrowing_wrong_character_cannot_match_actual_result(case):
    job = case['resolved_jobs']['1']
    job.update(support_mode='qsjs', resolved_support_unit_id=job['unit_ids'][0])
    row = settle(case, 1)
    row['support_unit'] = job['unit_ids'][1]
    with pytest.raises(ValueError, match='actual_support_mismatch'):
        run(case)


def test_qsjs_all_five_owned_cannot_record_an_unrequested_borrow(case):
    case['resolved_jobs']['1']['support_mode'] = 'qsjs'
    row = settle(case, 1)
    row.update(support_unit=row['unit_ids'][0], support_owner_viewer_id='987654321')
    with pytest.raises(ValueError, match='actual_support_mismatch'):
        run(case)


def test_qsjs_exact_missing_character_is_excluded_from_next_self_conflicts(case):
    first = settle(case, 1)
    job = case['resolved_jobs']['2']
    job['unit_ids'][0] = first['unit_ids'][0]
    job.update(support_mode='qsjs', resolved_support_unit_id=job['unit_ids'][0])
    assert run(case)['status'] == 'suggest_regular'
    assert run(case)['slot'] == 2


def test_unobserved_used_self_units_do_not_claim_normal_result_was_applied(case):
    settle(case, 1)
    case['observation']['used_unit'] = []
    assert run(case)['reason'] == 'used_units_history_mismatch'


def test_duplicate_result_intent_or_slot_never_resends(case):
    row = settle(case, 1)
    case['settlements'].append(deepcopy(row))
    with pytest.raises(ValueError, match='daily_result_invalid'):
        run(case)
    case['settlements'][-1]['intent_id'] = 'different-intent-same-slot'
    with pytest.raises(ValueError, match='regular_slot_duplicate'):
        run(case)


@pytest.mark.parametrize('location,field,value', [
    ('plan', 'version', True), ('observation', 'remaining_count', True),
    ('observation', 'used_unit', [True]), ('observation', 'carry_stock_indices', [True]),
    ('observation', 'observed_at', '2026-10-20T06:00:00')])
def test_bool_integer_and_ambiguous_time_inputs_are_rejected(case, location, field, value):
    case[location][field] = value
    with pytest.raises(ValueError):
        run(case)


def test_missing_regular_target_waits_without_substitution(case):
    case['observation']['bosses'][0]['available'] = False
    result = run(case)
    assert result['status'] == 'waiting_target'
    assert result['slot'] == 1 and result['boss'] == 1


def test_preview_has_no_file_network_or_device_operations(case, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('offline scheduler attempted I/O')
    with monkeypatch.context() as guarded:
        guarded.setattr(builtins, 'open', forbidden)
        guarded.setattr(socket, 'socket', forbidden)
        result = run(case)
    assert result['status'] == 'suggest_regular'
