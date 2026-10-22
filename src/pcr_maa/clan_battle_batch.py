"""Bound daily-batch business checks; the production native adapter stays closed.

The injected port is an internal test seam, never a player-configurable input path.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from . import clan_battle as simulation
from .clan_battle_daily import POLICY, REFERENCE_KEYS, _identity, _integer, _object, _units
from .game_day import BEIJING, current_game_day
from .preset_execution import Journal

OPERATION = 'clan_battle_daily'
MATERIAL_KEYS = simulation.PLAN_KEYS - {'version', 'server', 'account_id', 'content_version', 'snapshot', 'confirmed_for'}
BINDING_KEYS = {'server', 'account_id', 'period', 'game_day', 'batch_id', 'daily_plan_hash', 'slot_confirmations'}
OBSERVATION_KEYS = BINDING_KEYS | {'observed_at', 'remaining_count', 'used_unit', 'carry_stock_indices', 'bosses', 'reset_verified', 'reset_evidence'}
BOSS_KEYS = {'boss', 'round', 'phase', 'available', 'instance_id', 'appeared_at', 'evidence'}
ACTION_KEYS = {'kind', 'slot', 'stock_index', 'boss', 'round', 'phase', 'instance_id', 'start_seconds'}
READY_KEYS = BINDING_KEYS | ACTION_KEYS | {'slot_confirmed_for', 'verified', 'unit_ids', 'stars', 'support_unit', 'support_owner_viewer_id', 'evidence'}
GRANT_KEYS = BINDING_KEYS | {'slot_confirmed_for', 'source_slot', 'source_intent_id', 'stock_index', 'time', 'using_unit', 'support_unit', 'support_owner_viewer_id', 'source_instance_id', 'issued_at', 'evidence'}
RESULT_KEYS = {'ready', 'result_verified', 'consumption_verified', 'damage', 'settings_verified', 'settings_evidence', 'result_evidence', 'grant', 'after', 'return_verified'}
INTENT_KEYS = BINDING_KEYS | {'state', 'operation', 'intent_id', 'action', 'slot_confirmed_for', 'ready', 'observed_at', 'before'}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def request_hash(request):
    return _hash({key: value for key, value in request.items() if key != 'confirmed_for'})


def _json(data):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('daily_duplicate_json_key')
            value[key] = item
        return value
    return json.loads(data, object_pairs_hook=unique)


def validate_daily_request(value):
    """Validate three original reviewed jobs, including their raw material hashes."""
    _object(value, {'version', 'daily_plan', 'slot_plans', 'slot_materials', 'confirmed_for'}, 'daily_request_invalid')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('daily_request_invalid')
    plan = value['daily_plan']
    _object(plan, {'version', 'policy', 'slots'}, 'daily_plan_invalid')
    if (type(plan['version']) is not int or plan['version'] != 1 or plan['policy'] != POLICY
            or type(plan['slots']) is not list or len(plan['slots']) != 3
            or type(value['slot_plans']) is not dict or set(value['slot_plans']) != {'1', '2', '3'}
            or type(value['slot_materials']) is not dict or set(value['slot_materials']) != {'1', '2', '3'}):
        raise ValueError('daily_three_reviewed_targets_required')
    seen, identity, used = set(), None, set()
    for ref in plan['slots']:
        _object(ref, REFERENCE_KEYS, 'daily_reference_invalid')
        slot = ref['slot']
        if (not _integer(slot, 1, 3) or slot in seen
                or any(type(ref[key]) is not str or simulation.HEX.fullmatch(ref[key]) is None
                    for key in ('bundle_sha256', 'material_sha256', 'confirmed_for'))):
            raise ValueError('daily_reference_invalid')
        seen.add(slot)
        job = simulation.validate_plan(value['slot_plans'][str(slot)])
        material = value['slot_materials'][str(slot)]
        _object(material, MATERIAL_KEYS, 'daily_material_invalid')
        if (ref['confirmed_for'] != job['confirmed_for'] or ref['job_id'] != job['source']['job_id']
                or _hash(material) != ref['material_sha256']
                or any(material[key] != job[key] for key in MATERIAL_KEYS - {'stars'})):
            raise ValueError('daily_job_reference_changed')
        snapshot = simulation.load_snapshot(job)
        raw_stars = material['stars']
        if type(raw_stars) is not list or len(raw_stars) != 5:
            raise ValueError('daily_material_stars_invalid')
        known = {row['unit_id']: row for row in snapshot['units']}
        for uid, raw, actual in zip(job['unit_ids'], raw_stars, job['stars']):
            if raw is not None:
                if type(raw) is not int or raw != actual:
                    raise ValueError('daily_material_stars_invalid')
            else:
                path = f'units.{uid}.battle_rarity'
                if (uid not in known or known[uid].get('battle_rarity') != actual
                        or any(path == key or path.startswith(key + '.') for key in snapshot['unknown_fields'])):
                    raise ValueError('daily_material_stars_unknown')
        data = simulation._file(ref, 'bundle_path', 'bundle_sha256', limit=64 * 1024 * 1024)
        bundle = _json(data)
        if type(bundle) is not dict or type(bundle.get('jobs')) is not list:
            raise ValueError('daily_bundle_invalid')
        originals = [row for row in bundle['jobs'] if type(row) is dict
            and type(row.get('source')) is dict and row['source'].get('job_id') == ref['job_id']]
        if len(originals) != 1 or originals[0]['source'] != job['source']:
            raise ValueError('daily_bundle_source_changed')
        bound = _identity(job)
        if identity is not None and identity != bound:
            raise ValueError('daily_plan_identity_mismatch')
        identity = bound
        support = simulation.support_requirement(job, snapshot)
        owned = set(job['unit_ids']) - {support}
        if used & owned:
            raise ValueError('daily_self_units_repeated')
        used.update(owned)
    if (type(value['confirmed_for']) is not str or simulation.HEX.fullmatch(value['confirmed_for']) is None
            or value['confirmed_for'] != request_hash(value)):
        raise ValueError('daily_confirmation_changed')
    return deepcopy(value)


def _now(value):
    if type(value) is not datetime:
        raise ValueError('daily_now_required')
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=BEIJING).astimezone(timezone.utc)


def _timestamp(value):
    try:
        if type(value) is not str:
            raise ValueError()
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise ValueError('daily_observation_time_invalid') from exc


def _binding(request, now):
    first = request['slot_plans']['1']
    value = {key: first[key] for key in ('server', 'account_id', 'period')}
    value.update(game_day=current_game_day(now), daily_plan_hash=request['confirmed_for'],
        slot_confirmations={slot: job['confirmed_for'] for slot, job in request['slot_plans'].items()})
    value['batch_id'] = _hash(value)
    return value


def _bound(row, binding):
    if any(row.get(key) != value for key, value in binding.items()):
        raise ValueError('daily_batch_binding_mismatch')


def _evidence(value):
    return type(value) is dict and bool(value)


def _observation(row, binding, now):
    _object(row, OBSERVATION_KEYS, 'daily_observation_invalid')
    _bound(row, binding)
    observed = _timestamp(row['observed_at'])
    if (current_game_day(observed) != binding['game_day'] or current_game_day(_now(now)) != binding['game_day']
            or not 0 <= (_now(now) - observed).total_seconds() <= 60):
        raise ValueError('daily_fresh_observation_required')
    if not _integer(row['remaining_count'], 0, 3) or type(row['reset_verified']) is not bool or type(row['reset_evidence']) is not dict:
        raise ValueError('daily_observation_invalid')
    _units(row['used_unit'], team=False)
    stocks = row['carry_stock_indices']
    if (type(stocks) is not list or len(stocks) > 16 or len(set(stocks)) != len(stocks)
            or any(not _integer(stock, 0, 1_000_000) for stock in stocks)):
        raise ValueError('daily_carryover_inventory_invalid')
    if type(row['bosses']) is not list or len(row['bosses']) > 5:
        raise ValueError('daily_boss_observation_invalid')
    seen = set()
    for boss in row['bosses']:
        _object(boss, BOSS_KEYS, 'daily_boss_observation_invalid')
        if (not _integer(boss['boss'], 1, 5) or boss['boss'] in seen
                or not _integer(boss['round'], 1, 100_000) or boss['phase'] not in ('B', 'C', 'D', 'E')
                or type(boss['available']) is not bool or type(boss['instance_id']) is not str
                or not 0 < len(boss['instance_id']) <= 200 or not _evidence(boss['evidence'])
                or _timestamp(boss['appeared_at']) > observed):
            raise ValueError('daily_boss_observation_invalid')
        seen.add(boss['boss'])
    return row


def _target(job, bosses):
    boss = next((row for row in bosses if row['boss'] == job['boss']), None)
    if boss is None:
        # Absence is not evidence that a target is unavailable.
        raise ValueError('daily_target_observation_unknown')
    phases = ('B', 'C') if job['phase'] == 'BC' else (job['phase'],)
    if (not boss['available'] or boss['phase'] not in phases or boss['round'] < job['round_min']
            or job['round_max'] is not None and boss['round'] > job['round_max']):
        return None
    return boss


def _action(row):
    _object(row, ACTION_KEYS, 'daily_action_invalid')
    if (row['kind'] not in ('regular', 'carryover') or not _integer(row['slot'], 1, 3)
            or not _integer(row['boss'], 1, 5) or not _integer(row['round'], 1, 100_000)
            or row['phase'] not in ('B', 'C', 'D', 'E') or type(row['instance_id']) is not str
            or not 0 < len(row['instance_id']) <= 200 or not _integer(row['start_seconds'], 1, 90)
            or row['kind'] == 'regular' and (row['stock_index'] is not None or row['start_seconds'] != 90)
            or row['kind'] == 'carryover' and not _integer(row['stock_index'], 0, 1_000_000)):
        raise ValueError('daily_action_invalid')


def _ready(row, action, request, binding):
    _object(row, READY_KEYS, 'daily_ready_invalid')
    _action({key: row[key] for key in ACTION_KEYS})
    _bound(row, binding)
    job = request['slot_plans'][str(action['slot'])]
    support = simulation.support_requirement(job, simulation.load_snapshot(job))
    _units(row['unit_ids'])
    if (any(row[key] != value for key, value in action.items()) or row['verified'] is not True
            or row['slot_confirmed_for'] != job['confirmed_for'] or not _evidence(row['evidence'])
            or set(row['unit_ids']) != set(job['unit_ids']) or type(row['stars']) is not list
            or len(row['stars']) != 5 or any(not _integer(star, 1, 6) for star in row['stars'])
            or dict(zip(row['unit_ids'], row['stars'])) != dict(zip(job['unit_ids'], job['stars']))
            or row['support_unit'] != support or support is not None and type(row['support_unit']) is not int):
        raise ValueError('daily_team_or_target_unknown')
    owner = row['support_owner_viewer_id']
    if ((support is None and owner is not None) or support is not None and (type(owner) is not str
            or re.fullmatch(r'[1-9][0-9]{0,19}', owner) is None or owner == job['account_id'])):
        raise ValueError('daily_support_owner_unknown')
    return row


def _result(value, intent, request, binding, *, now=None):
    _object(value, RESULT_KEYS, 'daily_result_invalid')
    _ready(value['ready'], intent['action'], request, binding)
    if (value['ready'] != intent['ready'] or value['result_verified'] is not True
            or value['consumption_verified'] is not True or value['settings_verified'] is not True
            or type(value['return_verified']) is not bool or not _integer(value['damage'], 0, 10**15)
            or not _evidence(value['result_evidence'])
            or not simulation._settings(value, request['slot_plans'][str(intent['action']['slot'])])):
        raise ValueError('daily_result_or_consumption_unknown')
    after = _timestamp(value['after']['observed_at'])
    _observation(value['after'], binding, after if now is None else now)
    if after < _timestamp(intent['observed_at']):
        raise ValueError('daily_result_observation_clock_rollback')
    grant = value['grant']
    if grant is not None:
        _object(grant, GRANT_KEYS, 'daily_carryover_invalid')
        _bound(grant, binding)
        _units(grant['using_unit'])
        ready = intent['ready']
        if (intent['action']['kind'] != 'regular' or grant['source_intent_id'] != intent['intent_id']
                or not _integer(grant['source_slot'], 1, 3) or grant['source_slot'] != ready['slot']
                or grant['slot_confirmed_for'] != ready['slot_confirmed_for']
                or not _integer(grant['stock_index'], 0, 1_000_000) or not _integer(grant['time'], 1, 90)
                or grant['using_unit'] != ready['unit_ids'] or grant['support_unit'] != ready['support_unit']
                or grant['support_owner_viewer_id'] != ready['support_owner_viewer_id']
                or grant['source_instance_id'] != ready['instance_id'] or not _evidence(grant['evidence'])
                or not _timestamp(intent['observed_at']) <= _timestamp(grant['issued_at']) <= _timestamp(value['after']['observed_at'])):
            raise ValueError('daily_carryover_source_mismatch')
    return value


def _history(events, request, binding):
    regular, grants, consumed = {}, {}, set()
    previous_after = None
    for index, intent in enumerate(events[::2]):
        _object(intent, INTENT_KEYS, 'daily_ledger_invalid')
        _bound(intent, binding)
        _action(intent['action'])
        if (intent.get('state') != 'in_flight' or intent.get('operation') not in ('clan_battle_regular', 'clan_battle_carryover')
                or type(intent.get('intent_id')) is not str or not re.fullmatch(r'[0-9a-f]{64}', intent['intent_id'])
                or type(intent.get('action')) is not dict or set(intent['action']) != ACTION_KEYS
                or intent['operation'] != 'clan_battle_' + intent['action']['kind']
                or intent['intent_id'] != _hash(dict(binding=binding, action=intent['action']))
                or intent['slot_confirmed_for'] != request['slot_plans'][str(intent['action']['slot'])]['confirmed_for']):
            raise ValueError('daily_ledger_invalid')
        _ready(intent.get('ready'), intent['action'], request, binding)
        before = _observation(intent.get('before'), binding, _timestamp(intent['observed_at']))
        if before['observed_at'] != intent['observed_at']:
            raise ValueError('daily_ledger_observation_mismatch')
        if index == 0 and (before['reset_verified'] is not True or not _evidence(before['reset_evidence'])):
            raise ValueError('daily_actual_reset_readback_required')
        chosen = choose_action(request, binding, before, regular, grants, consumed)
        if chosen.get('action') != intent['action']:
            raise ValueError('daily_ledger_action_mismatch')
        if previous_after is not None and _timestamp(before['observed_at']) < _timestamp(previous_after['observed_at']):
            raise ValueError('daily_observation_clock_rollback')
        if index * 2 + 1 >= len(events):
            return regular, grants, consumed, True
        settled = events[index * 2 + 1]
        if (any(settled.get(key) != value for key, value in intent.items() if key != 'state')
                or settled.get('state') != 'settled' or set(settled) != set(intent) | {'result'}):
            raise ValueError('daily_ledger_binding_mismatch')
        result = _result(settled['result'], intent, request, binding)
        slot, stock = intent['action']['slot'], intent['action']['stock_index']
        if intent['action']['kind'] == 'regular':
            if slot in regular:
                raise ValueError('daily_regular_slot_duplicate')
            regular[slot] = dict(intent=intent, result=result)
        else:
            if stock not in grants or stock in consumed:
                raise ValueError('daily_carryover_consumption_mismatch')
            grant, ready = grants[stock], intent['ready']
            if (ready['unit_ids'] != grant['using_unit'] or ready['support_unit'] != grant['support_unit']
                    or ready['support_owner_viewer_id'] != grant['support_owner_viewer_id']):
                raise ValueError('daily_carryover_actual_team_mismatch')
            consumed.add(stock)
        if result['grant'] is not None:
            stock = result['grant']['stock_index']
            if stock in grants:
                raise ValueError('daily_carryover_stock_duplicate')
            grants[stock] = result['grant']
        _check_counts(result['after'], regular, grants, consumed)
        previous_after = result['after']
    return regular, grants, consumed, False


def _check_counts(observation, regular, grants, consumed):
    owned = set()
    for row in regular.values():
        ready = row['intent']['ready']
        owned.update(set(ready['unit_ids']) - {ready['support_unit']})
    if observation['remaining_count'] != 3 - len(regular):
        raise ValueError('daily_remaining_count_history_mismatch')
    if set(observation['used_unit']) != owned:
        raise ValueError('daily_used_units_history_mismatch')
    if set(observation['carry_stock_indices']) != set(grants) - consumed:
        raise ValueError('daily_carryover_inventory_history_mismatch')


def choose_action(request, binding, observation, regular, grants, consumed):
    """Pure bound candidate selection; this does not authorize any input."""
    _check_counts(observation, regular, grants, consumed)
    held, held_regular = [], []
    for stock, grant in sorted(grants.items(), key=lambda item: (item[1]['source_slot'], item[0])):
        if stock in consumed:
            continue
        slot = grant['source_slot']
        job = request['slot_plans'][str(slot)]
        target = _target(job, observation['bosses'])
        reason = None
        if (target is None or target['instance_id'] == grant['source_instance_id']
                or _timestamp(target['appeared_at']) <= _timestamp(grant['issued_at'])):
            reason = 'same_boss_new_appearance_not_observed'
        elif job['switches'] and grant['time'] != 90:
            reason = 'carryover_axis_start_time_incompatible'
        if reason:
            held.append(dict(stock_index=stock, slot=slot, reason=reason))
            continue
        action = dict(kind='carryover', slot=slot, stock_index=stock, boss=target['boss'], round=target['round'],
            phase=target['phase'], instance_id=target['instance_id'], start_seconds=grant['time'])
        return dict(status='ready', action=action, held_carryovers=held, held_regular=held_regular)
    for slot in (1, 2, 3):
        if slot in regular:
            continue
        job = request['slot_plans'][str(slot)]
        target = _target(job, observation['bosses'])
        if target is None:
            held_regular.append(dict(slot=slot, boss=job['boss'], reason='regular_target_not_available_for_job'))
            continue
        support = simulation.support_requirement(job, simulation.load_snapshot(job))
        if (set(job['unit_ids']) - {support}) & set(observation['used_unit']):
            raise ValueError('daily_self_unit_already_used')
        action = dict(kind='regular', slot=slot, stock_index=None, boss=target['boss'], round=target['round'],
            phase=target['phase'], instance_id=target['instance_id'], start_seconds=90)
        return dict(status='ready', action=action, held_carryovers=held, held_regular=held_regular)
    pending = set(grants) - consumed
    status = ('remind_carryover' if pending else 'complete') if len(regular) == 3 else 'no_available_target'
    return dict(status=status, held_carryovers=held, held_regular=held_regular)


def execute_batch(port, journal, request, *, now=None, explicit_start=False):
    """Exercise the real Journal with an internal port; no production caller injects it."""
    from .supported_daily import _atomic_json
    now = now or datetime.now(timezone.utc)
    request = validate_daily_request(request)
    binding = _binding(request, now)
    report = dict(outcome='stopped', operation=OPERATION, **binding, attempts=[], held_carryovers=[], held_regular=[],
        consumption_sent=False, regular_settled=0, carryover_settled=0)
    if explicit_start is not True:
        report['error'] = 'daily_explicit_start_required'
        return report
    identity = _hash({key: binding[key] for key in ('server', 'account_id', 'period')})
    day_journal = Journal(journal.directory / identity / binding['game_day'])
    frozen_path = day_journal.directory / 'batch.json'
    try:
        with day_journal.locked():
            if frozen_path.exists():
                frozen = _json(frozen_path.read_bytes())
                if frozen != dict(binding=binding, request=request):
                    raise ValueError('daily_consumed_batch_plan_immutable')
            events = day_journal.events()
            if events and not frozen_path.exists():
                raise ValueError('daily_frozen_batch_missing')
            regular, grants, consumed, pending = _history(events, request, binding)
            if pending:
                raise ValueError('daily_previous_result_unknown')
            report.update(regular_settled=len(regular), carryover_settled=len(consumed))
            last_observed = _timestamp(events[-1]['result']['after']['observed_at']) if events else None
            # Three main attacks and at most one grant per main attack, never an endless wait.
            for _ in range(7):
                port.checkpoint()
                current = _now(now) if not callable(getattr(port, 'now', None)) else _now(port.now())
                if current_game_day(current) != binding['game_day']:
                    raise ValueError('daily_game_day_changed')
                observation = _observation(port.observe(deepcopy(binding)), binding, current)
                stamp = _timestamp(observation['observed_at'])
                if last_observed is not None and stamp < last_observed:
                    raise ValueError('daily_observation_clock_rollback')
                last_observed = stamp
                if not events and not regular and (observation['reset_verified'] is not True
                        or not _evidence(observation['reset_evidence'])):
                    raise ValueError('daily_actual_reset_readback_required')
                chosen = choose_action(request, binding, observation, regular, grants, consumed)
                report['remaining_count'] = observation['remaining_count']
                report['held_carryovers'] = chosen['held_carryovers']
                report['held_regular'] = chosen['held_regular']
                if chosen['status'] != 'ready':
                    report['outcome'] = chosen['status']
                    return report
                action = chosen['action']
                job = request['slot_plans'][str(action['slot'])]
                simulation.require_timing(port, job)
                ready = _ready(port.prepare(deepcopy(action), deepcopy(job), deepcopy(binding)), action, request, binding)
                if action['kind'] == 'carryover':
                    grant = grants[action['stock_index']]
                    if (ready['unit_ids'] != grant['using_unit'] or ready['support_unit'] != grant['support_unit']
                            or ready['support_owner_viewer_id'] != grant['support_owner_viewer_id']):
                        raise ValueError('daily_carryover_actual_team_mismatch')
                port.checkpoint()
                validate_daily_request(request)
                fresh_now = current if not callable(getattr(port, 'now', None)) else _now(port.now())
                fresh = _observation(port.observe(deepcopy(binding)), binding, fresh_now)
                if (_timestamp(fresh['observed_at']) < stamp
                        or choose_action(request, binding, fresh, regular, grants, consumed).get('action') != action):
                    raise ValueError('daily_target_changed_before_input')
                port.checkpoint()
                validate_daily_request(request)
                if not frozen_path.exists():
                    _atomic_json(frozen_path, dict(binding=binding, request=request))
                    if _json(frozen_path.read_bytes()) != dict(binding=binding, request=request):
                        raise ValueError('daily_frozen_batch_readback_failed')
                intent = dict(state='in_flight', operation='clan_battle_' + action['kind'], **binding,
                    intent_id=_hash(dict(binding=binding, action=action)), action=deepcopy(action),
                    slot_confirmed_for=job['confirmed_for'], ready=deepcopy(ready),
                    observed_at=fresh['observed_at'], before=deepcopy(fresh))
                day_journal.append(intent)
                report['consumption_sent'] = True
                value = port.execute(deepcopy(intent), deepcopy(job))
                result_now = fresh_now if not callable(getattr(port, 'now', None)) else _now(port.now())
                result = _result(value, intent, request, binding, now=result_now)
                proposed = events + [intent, dict(intent, state='settled', result=deepcopy(result))]
                regular, grants, consumed, pending = _history(proposed, request, binding)
                day_journal.append(proposed[-1])
                events = proposed
                report['attempts'].append(dict(slot=action['slot'], kind=action['kind'],
                    intent_id=intent['intent_id'], stock_index=action['stock_index'], damage=result['damage']))
                report.update(regular_settled=len(regular), carryover_settled=len(consumed),
                    remaining_count=result['after']['remaining_count'])
                last_observed = _timestamp(result['after']['observed_at'])
                if result['return_verified'] is not True:
                    raise ValueError('daily_return_unverified')
            raise ValueError('daily_batch_action_bound_exceeded')
    except Exception as exc:
        report['error'] = str(exc) or type(exc).__name__
        return report


def run_owned(tasker, config, directory):
    """Fail closed until real formal/stock pages and a qualified adapter exist."""
    from .supported_daily import _atomic_json
    request = validate_daily_request(config.get(OPERATION))
    result = dict(outcome='stopped', operation=OPERATION,
        error='clan_battle_daily_calibration_missing:production_daily_adapter_unavailable',
        daily_plan_hash=request['confirmed_for'], consumption_sent=False, intents_created=0,
        daily_execution_available=False, held_regular=[], held_carryovers=[],
        regular_settled=0, carryover_settled=0)
    report = dict(outcome='stopped', daily_complete=False, supported_chain_complete=False,
        selected_modules=[OPERATION], rewards_final=False,
        steps=[dict(name=OPERATION, status='stopped', result=result)])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_json(directory / 'report.json', report)
    return report
