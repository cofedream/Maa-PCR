"""Bound daily-batch checks shared by Owner native execution and offline replay.

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
OBSERVATION_KEYS = BINDING_KEYS | {'observed_at', 'remaining_count', 'cp', 'cp_semantics', 'cp_evidence', 'used_unit', 'carry_stock_indices', 'bosses', 'reset_verified', 'reset_evidence'}
BOSS_KEYS = {'boss', 'round', 'phase', 'available', 'instance_id', 'appeared_at', 'evidence'}
ACTION_KEYS = {'kind', 'slot', 'stock_index', 'boss', 'round', 'phase', 'instance_id', 'start_seconds'}
READY_KEYS = BINDING_KEYS | ACTION_KEYS | {'slot_confirmed_for', 'verified', 'unit_ids', 'stars', 'support_unit', 'support_owner_viewer_id', 'evidence'}
GRANT_KEYS = BINDING_KEYS | {'slot_confirmed_for', 'source_slot', 'source_intent_id', 'stock_index', 'time', 'using_unit', 'support_unit', 'support_owner_viewer_id', 'source_instance_id', 'issued_at', 'evidence'}
RESULT_KEYS = {'ready', 'result_verified', 'consumption_verified', 'damage', 'settings_verified', 'settings_evidence', 'result_evidence', 'grant', 'after', 'return_verified'}
INTENT_KEYS = BINDING_KEYS | {'state', 'operation', 'intent_id', 'action', 'slot_confirmed_for', 'ready', 'observed_at', 'before'}
CP_PER_CHALLENGE = 300


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


def validate_daily_request(value, *, snapshot_at=None):
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
        job = simulation.validate_plan(value['slot_plans'][str(slot)], snapshot_at=snapshot_at)
        material = value['slot_materials'][str(slot)]
        _object(material, MATERIAL_KEYS, 'daily_material_invalid')
        if (ref['confirmed_for'] != job['confirmed_for'] or ref['job_id'] != job['source']['job_id']
                or _hash(material) != ref['material_sha256']
                or any(material[key] != job[key] for key in MATERIAL_KEYS - {'stars'})):
            raise ValueError('daily_job_reference_changed')
        snapshot = simulation.load_snapshot(job, now=snapshot_at)
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
    if (not _integer(row['cp'], 0, 900) or row['cp_semantics'] != 'daily_earned'
            or not _evidence(row['cp_evidence'])):
        raise ValueError('daily_cp_observation_unknown')
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
                or type(boss['available']) is not bool or not _evidence(boss['evidence'])
                or boss['round'] is not None and not _integer(boss['round'], 1, 100_000)
                or boss['phase'] is not None and boss['phase'] not in ('B', 'C', 'D', 'E')
                or boss['instance_id'] is not None and (type(boss['instance_id']) is not str or not 0 < len(boss['instance_id']) <= 200)
                or boss['appeared_at'] is not None and _timestamp(boss['appeared_at']) > observed
                or boss['available'] and any(boss[key] is None for key in ('round', 'phase', 'instance_id'))):
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


def _ready(row, action, request, binding, *, snapshot_at=None):
    _object(row, READY_KEYS, 'daily_ready_invalid')
    _action({key: row[key] for key in ACTION_KEYS})
    _bound(row, binding)
    job = request['slot_plans'][str(action['slot'])]
    support = simulation.support_requirement(job, simulation.load_snapshot(job, now=snapshot_at))
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
    _ready(value['ready'], intent['action'], request, binding, snapshot_at=_timestamp(intent['observed_at']))
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
    regular = intent['action']['kind'] == 'regular'
    earned = value['after']['cp'] // CP_PER_CHALLENGE - intent['before']['cp'] // CP_PER_CHALLENGE
    if (value['after']['cp'] < intent['before']['cp']
            or intent['before']['remaining_count'] + earned - value['after']['remaining_count'] != int(regular)):
        raise ValueError('daily_consumption_readback_mismatch')
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


def _not_sent(row, intent):
    extra = {'at', 'input_execution', 'consumption_sent', 'settlement_verified',
             'business_result_verified', 'native_evidence'}
    receipt = row.get('native_evidence')
    return (set(row) == INTENT_KEYS | extra and row.get('state') == 'settled'
        and all(row.get(key) == value for key, value in intent.items() if key != 'state')
        and row.get('input_execution') == 'not_sent' and row.get('consumption_sent') is False
        and row.get('settlement_verified') is False and row.get('business_result_verified') is False
        and type(receipt) is dict and receipt.get('input_execution') == 'not_sent'
        and receipt.get('clan_battle_phase') == intent['action']['kind'] + '_start'
        and receipt.get('daily_intent_id') == intent['intent_id'])



def _cancelled_carryover_confirmation(row, intent, grants, consumed, binding):
    """Accept only an audited confirmation cancel with the original stock intact."""
    from .result_checkpoint import action_completed
    error = 'daily_carryover_cancel_proof_invalid'
    _object(row, INTENT_KEYS | {'recovery'}, error)
    if (row['state'] != 'settled' or intent['action']['kind'] != 'carryover'
            or any(row[key] != value for key, value in intent.items() if key != 'state')):
        raise ValueError(error)
    recovery = row['recovery']
    _object(recovery, {'kind', 'observed_at', 'after', 'evidence'}, error)
    if recovery['kind'] != 'carryover_confirmation_cancelled':
        raise ValueError(error)
    after = recovery['after']
    _object(after, {'remaining_count', 'cp', 'carry_stock_indices', 'stock'}, error)
    stock_keys = {'stock_index', 'time', 'using_unit', 'support_unit', 'support_owner_viewer_id'}
    stock = after['stock']
    _object(stock, stock_keys, error)
    index = intent['action']['stock_index']
    grant = grants.get(index)
    selected = intent['ready']['evidence'].get('carry_selection')
    if (grant is None or index in consumed or type(selected) is not dict
            or not _integer(stock['stock_index'], 0, 1_000_000) or not _integer(stock['time'], 1, 90)
            or any(stock[key] != grant[key] or stock[key] != selected.get(key) for key in stock_keys)
            or stock['stock_index'] != index or stock['time'] != intent['action']['start_seconds']
            or stock['using_unit'] != intent['ready']['unit_ids']
            or stock['support_unit'] != intent['ready']['support_unit']
            or stock['support_owner_viewer_id'] != intent['ready']['support_owner_viewer_id']
            or type(after['remaining_count']) is not int or type(after['cp']) is not int
            or type(after['carry_stock_indices']) is not list
            or any(type(item) is not int for item in after['carry_stock_indices'])
            or any(after[key] != intent['before'][key] for key in ('remaining_count', 'cp', 'carry_stock_indices'))):
        raise ValueError(error)
    proof = recovery['evidence']
    _object(proof, {'source', 'cancelled_at', 'cancel_verified',
        'confirmation_input_count', 'start_receipt', 'artifacts'}, error)
    receipt = proof['start_receipt']
    nodes = receipt.get('nodes') if type(receipt) is dict else None
    if (proof['source'] != 'manual_confirmation_cancel_readback' or proof['cancel_verified'] is not True
            or type(proof['confirmation_input_count']) is not int or proof['confirmation_input_count'] != 0
            or type(receipt) is not dict or not _integer(receipt.get('task_id'), 1, 2**63 - 1)
            or type(nodes) is not list or any(type(node) is not dict for node in nodes)
            or any(node.get('action') is not None and
                (type(node['action']) is not dict or node['action'].get('name') != 'CBStart') for node in nodes)
            or sum(node.get('action') is not None for node in nodes) != 1
            or not action_completed(receipt, 'CBStart')):
        raise ValueError(error)
    artifacts = proof['artifacts']
    _object(artifacts, {'start_receipt', 'native_log', 'confirmation_frame',
        'cancel_input', 'inventory', 'lobby'}, error)
    for artifact in artifacts.values():
        _object(artifact, {'path', 'sha256'}, error)
        if (type(artifact['path']) is not str or not artifact['path']
                or type(artifact['sha256']) is not str or simulation.HEX.fullmatch(artifact['sha256']) is None):
            raise ValueError(error)
    observed, cancelled = _timestamp(recovery['observed_at']), _timestamp(proof['cancelled_at'])
    if (current_game_day(observed) != binding['game_day']
            or not _timestamp(intent['observed_at']) <= cancelled <= observed):
        raise ValueError(error)
    return recovery


def validate_external_regular_event(row, request=None, binding=None):
    """A confirmed program-external use is history, never an execution result."""
    from .alpha_owner import validate_clan_preview
    keys = BINDING_KEYS | {'state', 'operation', 'slot', 'slot_confirmed_for', 'unit_ids',
        'observed_at', 'before', 'after', 'confirmation'}
    _object(row, keys, 'daily_external_regular_record_invalid')
    if (row['state'] != 'observed_external' or row['operation'] != 'clan_battle_external_regular'
            or not _integer(row['slot'], 1, 3)):
        raise ValueError('daily_external_regular_record_invalid')
    binding = binding or {key: row[key] for key in BINDING_KEYS}
    _bound(row, binding)
    approval = row['confirmation']
    if (type(approval) is not dict or set(approval) != {'kind', 'request_id', 'root_task_id', 'controller_uuid',
            'invocation_id', 'daily_plan_hash', 'timeout_seconds', 'preview', 'ok', 'selected_slot'}
            or approval['kind'] != 'clan_consumption' or approval['ok'] is not True
            or type(approval['selected_slot']) is not int or approval['selected_slot'] != row['slot']
            or approval['daily_plan_hash'] != binding['daily_plan_hash'] or approval['timeout_seconds'] != 120
            or type(approval['request_id']) is not str or re.fullmatch('[0-9a-f]{32}', approval['request_id']) is None
            or type(approval['invocation_id']) is not str or re.fullmatch('[0-9a-f]{32}', approval['invocation_id']) is None
            or not _integer(approval['root_task_id'], 1, 2**63-1)
            or type(approval['controller_uuid']) is not str or not approval['controller_uuid']):
        raise ValueError('daily_external_regular_confirmation_invalid')
    preview = approval['preview']
    validate_clan_preview(preview)
    if (preview['kind'] != 'external_regular_baseline'
            or any(preview[key] != binding[key] for key in ('server', 'account_id', 'period', 'game_day', 'batch_id'))):
        raise ValueError('daily_external_regular_confirmation_invalid')
    selected = next(action for action in preview['actions'] if action['slot'] == row['slot'])
    _units(row['unit_ids'])
    if (row['unit_ids'] != selected['using_unit'] or row['slot_confirmed_for'] != binding['slot_confirmations'][str(row['slot'])]
            or request is not None and row['unit_ids'] != request['slot_plans'][str(row['slot'])]['unit_ids']):
        raise ValueError('daily_external_regular_team_mismatch')
    for observation in (row['before'], row['after']):
        _observation(observation, binding, _timestamp(observation['observed_at']))
        if (observation['reset_verified'] is not True or observation['cp'] != 900 or observation['remaining_count'] != 2 or observation['used_unit'] != []
                or observation['carry_stock_indices'] != [] or not _evidence(observation['reset_evidence'].get('inventory'))):
            raise ValueError('daily_external_regular_baseline_invalid')
    if (preview['observed_at'] != row['before']['observed_at'] or row['observed_at'] != row['after']['observed_at']
            or not 0 <= (_timestamp(row['observed_at']) - _timestamp(preview['observed_at'])).total_seconds() <= 120):
        raise ValueError('daily_external_regular_observation_changed')
    if any(_target_identity(row['before'], action) != _target_identity(row['after'], action) for action in preview['actions']):
        raise ValueError('daily_external_regular_observation_changed')
    if request is not None:
        if _external_candidates(request, row['before'], snapshot_at=_timestamp(row['before']['observed_at'])) != preview['actions']:
            raise ValueError('daily_external_regular_team_mismatch')
    return row


def _target_identity(observation, action):
    row = next((boss for boss in observation['bosses'] if boss['boss'] == action['boss']), None)
    return None if row is None else tuple(row[key] for key in ('boss', 'round', 'phase', 'instance_id', 'available'))


def _external_candidates(request, observation, *, snapshot_at=None):
    candidates = []
    for slot in (1, 2, 3):
        job = request['slot_plans'][str(slot)]
        if simulation.support_requirement(job, simulation.load_snapshot(job, now=snapshot_at)) is not None:
            raise ValueError('daily_external_regular_owned_team_required')
        target = _target(job, observation['bosses'])
        if target is None:
            raise ValueError('daily_external_regular_target_unknown')
        action = dict(kind='regular', slot=slot, stock_index=None, boss=target['boss'], round=target['round'],
            phase=target['phase'], instance_id=target['instance_id'], start_seconds=90)
        candidates.append(_consumption_action(action, request, {}))
    return candidates


def _history(events, request, binding, *, request_versions=None):
    regular, grants, consumed, external = {}, {}, set(), {}
    previous_after = None
    def context(row):
        original = (request_versions or {}).get(row.get('daily_plan_hash'), request)
        original_binding = _binding(original, _timestamp(row['observed_at']))
        if any(original_binding[key] != binding[key] for key in ('server', 'account_id', 'period', 'game_day')):
            raise ValueError('daily_batch_binding_mismatch')
        _bound(row, original_binding)
        return original, original_binding
    if events and events[0].get('state') == 'observed_external':
        original, original_binding = context(events[0])
        row = validate_external_regular_event(events[0], original, original_binding)
        external[row['slot']] = row
        previous_after = row['after']
        events = events[1:]
    for index, intent in enumerate(events[::2]):
        _object(intent, INTENT_KEYS, 'daily_ledger_invalid')
        original, original_binding = context(intent)
        _action(intent['action'])
        if (intent.get('state') != 'in_flight' or intent.get('operation') not in ('clan_battle_regular', 'clan_battle_carryover')
                or type(intent.get('intent_id')) is not str or not re.fullmatch(r'[0-9a-f]{64}', intent['intent_id'])
                or type(intent.get('action')) is not dict or set(intent['action']) != ACTION_KEYS
                or intent['operation'] != 'clan_battle_' + intent['action']['kind']
                or intent['intent_id'] != _hash(dict(binding=original_binding, action=intent['action']))
                or intent['slot_confirmed_for'] != original['slot_plans'][str(intent['action']['slot'])]['confirmed_for']):
            raise ValueError('daily_ledger_invalid')
        _ready(intent.get('ready'), intent['action'], original, original_binding, snapshot_at=_timestamp(intent['observed_at']))
        before = _observation(intent.get('before'), original_binding, _timestamp(intent['observed_at']))
        if before['observed_at'] != intent['observed_at']:
            raise ValueError('daily_ledger_observation_mismatch')
        if index == 0 and (before['reset_verified'] is not True or not _evidence(before['reset_evidence'])):
            raise ValueError('daily_actual_reset_readback_required')
        held = intent['ready']['evidence'].get('held_carryovers', {})
        if (type(held) is not dict or any(type(stock) is not str or not stock.isascii() or not stock.isdigit()
                or str(int(stock)) != stock or int(stock) not in set(grants) - consumed
                or reason not in ('carryover_capability_not_calibrated', 'carryover_opening_start_time_uncalibrated', 'carryover_not_authorized')
                for stock, reason in held.items())):
            raise ValueError('daily_ledger_carryover_hold_invalid')
        blocked = {int(stock): reason for stock, reason in held.items()}
        chosen = choose_action(original, original_binding, before, regular, grants, consumed,
            blocked_carryovers=blocked, external=external, snapshot_at=_timestamp(intent['observed_at']))
        if chosen.get('action') != intent['action']:
            raise ValueError('daily_ledger_action_mismatch')
        if previous_after is not None and _timestamp(before['observed_at']) < _timestamp(previous_after['observed_at']):
            raise ValueError('daily_observation_clock_rollback')
        if index * 2 + 1 >= len(events):
            return regular, grants, consumed, True, external
        settled = events[index * 2 + 1]
        if _not_sent(settled, intent):
            previous_after = before
            continue
        if 'recovery' in settled:
            previous_after = _cancelled_carryover_confirmation(settled, intent, grants, consumed, original_binding)
            continue
        if (any(settled.get(key) != value for key, value in intent.items() if key != 'state')
                or settled.get('state') != 'settled' or set(settled) != set(intent) | {'result'}):
            raise ValueError('daily_ledger_binding_mismatch')
        result = _result(settled['result'], intent, original, original_binding)
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
        _check_counts(result['after'], regular, grants, consumed, external=external)
        previous_after = result['after']
    return regular, grants, consumed, False, external


def _check_counts(observation, regular, grants, consumed, *, external=None):
    external = external or {}
    owned = {uid for row in external.values() for uid in row['unit_ids']}
    for row in regular.values():
        ready = row['intent']['ready']
        owned.update(set(ready['unit_ids']) - {ready['support_unit']})
    if observation['remaining_count'] != observation['cp'] // CP_PER_CHALLENGE - len(regular) - len(external):
        raise ValueError('daily_remaining_count_history_mismatch')
    if set(observation['used_unit']) != owned:
        raise ValueError('daily_used_units_history_mismatch')
    if set(observation['carry_stock_indices']) != set(grants) - consumed:
        raise ValueError('daily_carryover_inventory_history_mismatch')


def choose_action(request, binding, observation, regular, grants, consumed, *, blocked_carryovers=None, external=None,
        snapshot_at=None):
    """Pure bound candidate selection; this does not authorize any input."""
    external = external or {}
    _check_counts(observation, regular, grants, consumed, external=external)
    held, held_regular = [], []
    for stock, grant in sorted(grants.items(), key=lambda item: (item[1]['source_slot'], item[0])):
        if stock in consumed:
            continue
        slot = grant['source_slot']
        job = request['slot_plans'][str(slot)]
        target = _target(job, observation['bosses'])
        reason = None
        if (target is None or target['round'] <= regular[slot]['intent']['action']['round']
                or target['instance_id'] == grant['source_instance_id']):
            reason = 'same_boss_new_appearance_not_observed'
        elif job['switches']:
            reason = 'clan_battle_daily_opening_axis_unsupported'
        elif (blocked_carryovers or {}).get(stock):
            reason = blocked_carryovers[stock]
        if reason:
            held.append(dict(stock_index=stock, slot=slot, reason=reason))
            return dict(status='remind_carryover', held_carryovers=held, held_regular=held_regular)
        action = dict(kind='carryover', slot=slot, stock_index=stock, boss=target['boss'], round=target['round'],
            phase=target['phase'], instance_id=target['instance_id'], start_seconds=grant['time'])
        return dict(status='ready', action=action, held_carryovers=held, held_regular=held_regular)
    for slot in (1, 2, 3):
        if slot in regular or slot in (external or {}):
            continue
        job = request['slot_plans'][str(slot)]
        target = _target(job, observation['bosses'])
        if target is None:
            held_regular.append(dict(slot=slot, boss=job['boss'], reason='regular_target_not_available_for_job'))
            continue
        if observation['remaining_count'] == 0:
            held_regular.append(dict(slot=slot, boss=job['boss'], reason='regular_challenges_not_earned'))
            continue
        support = simulation.support_requirement(job, simulation.load_snapshot(job, now=snapshot_at))
        if (set(job['unit_ids']) - {support}) & set(observation['used_unit']):
            raise ValueError('daily_self_unit_already_used')
        action = dict(kind='regular', slot=slot, stock_index=None, boss=target['boss'], round=target['round'],
            phase=target['phase'], instance_id=target['instance_id'], start_seconds=90)
        return dict(status='ready', action=action, held_carryovers=held, held_regular=held_regular)
    pending = set(grants) - consumed
    status = ('remind_carryover' if pending else 'complete') if len(regular) + len(external) == 3 else 'no_available_target'
    if any(row['reason'] == 'regular_challenges_not_earned' for row in held_regular):
        status = 'insufficient_cp'
    return dict(status=status, held_carryovers=held, held_regular=held_regular)



def consumption_file_binding(request, *, root=None):
    from .alpha_owner import file_set_sha256
    from .clan_battle_calibration import INDEX_PATH, INDEX_SHA256, _read, _json
    root = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]
    _, data = _read(root, INDEX_PATH, INDEX_SHA256, limit=1024 * 1024)
    index, paths = _json(data), {INDEX_PATH}
    wanted = {(job['server'], job['content_version']) for job in request['slot_plans'].values()}
    entries = [entry for entry in index['entries'] if (entry['server'], entry['content_version']) in wanted]
    if len(entries) != len(wanted):
        raise ValueError('clan_consumption_calibration_binding_missing')
    def verified(relative, digest, limit):
        _read(root, relative, digest, limit=limit)
        paths.add(relative)
    def collect(value):
        if type(value) is dict:
            if value.get('recognition') == 'TemplateMatch':
                names, hashes = value['template'], value['sha256']
                if type(names) is not list:
                    names, hashes = [names], [hashes]
                if len(names) != len(hashes):
                    raise ValueError('clan_consumption_calibration_binding_invalid')
                for name, digest in zip(names, hashes):
                    verified('assets/resource/image/' + name, digest, 4 * 1024 * 1024)
            if type(value.get('samples')) is list:
                for sample in value['samples']:
                    verified(sample['path'], sample['sha256'], 32 * 1024 * 1024)
            for key, child in value.items():
                if key != 'timing_measurements':
                    collect(child)
        elif type(value) is list:
            for child in value:
                collect(child)
    for entry in entries:
        verified(entry['profile_path'], entry['profile_sha256'], 4 * 1024 * 1024)
        profile = _json((root / entry['profile_path']).read_bytes())
        collect(profile)
        if 'timed' in entry.get('capabilities', []):
            measured = profile['timing_measurements']
            verified(measured['path'], measured['sha256'], 1024 * 1024)
    source = root / 'src/pcr_maa'
    return dict(source_sha256=file_set_sha256(root, [path.relative_to(root).as_posix() for path in source.rglob('*.py')]),
        calibration_sha256=file_set_sha256(root, paths))


def _consumption_action(action, request, grants):
    job = request['slot_plans'][str(action['slot'])]
    grant = grants[action['stock_index']] if action['kind'] == 'carryover' else None
    return dict(action, source_intent_id=grant['source_intent_id'] if grant else None,
        source_instance_id=grant['source_instance_id'] if grant else None,
        using_unit=deepcopy(grant['using_unit'] if grant else job['unit_ids']),
        support_unit=grant['support_unit'] if grant else None,
        support_owner_viewer_id=grant['support_owner_viewer_id'] if grant else None)


def _consumption_candidates(action, request, observation, regular, grants, *, external=None):
    if action['kind'] == 'carryover':
        return [_consumption_action(action, request, grants)]
    actions = []
    for slot in (1, 2, 3):
        if slot in regular or slot in (external or {}):
            continue
        target = _target(request['slot_plans'][str(slot)], observation['bosses'])
        if target is not None:
            candidate = dict(kind='regular', slot=slot, stock_index=None, boss=target['boss'], round=target['round'],
                phase=target['phase'], instance_id=target['instance_id'], start_seconds=90)
            actions.append(_consumption_action(candidate, request, grants))
    return actions[:observation['remaining_count']]


def _fixed_plan(request):
    value = deepcopy(request)
    value.pop('confirmed_for')
    for job in value['slot_plans'].values():
        job.pop('snapshot')
        job.pop('confirmed_for')
    for ref in value['daily_plan']['slots']:
        ref.pop('confirmed_for')
    return value


def _request_versions(frozen_path, events, request, binding, now):
    versions = {request['confirmed_for']: request}
    if not frozen_path.exists():
        return versions
    frozen = _json(frozen_path.read_bytes())
    stored = [frozen]
    seen = {request['confirmed_for'], frozen.get('request', {}).get('confirmed_for')}
    for event in events:
        digest = event.get('daily_plan_hash')
        if type(digest) is not str or simulation.HEX.fullmatch(digest) is None:
            raise ValueError('daily_ledger_invalid')
        if digest not in seen:
            stored.append(_json(frozen_path.with_name('batch-' + digest + '.json').read_bytes()))
            seen.add(digest)
    for value in stored:
        _object(value, {'binding', 'request'}, 'daily_frozen_batch_invalid')
        original = value['request']
        if (value['binding'] != _binding(original, now) or _fixed_plan(original) != _fixed_plan(request)
                or original['confirmed_for'] != request_hash(original)
                or any(job['confirmed_for'] != simulation.plan_hash(job) for job in original['slot_plans'].values())):
            raise ValueError('daily_consumed_batch_plan_immutable')
        previous = next((row for row in events if row.get('daily_plan_hash') == original['confirmed_for']), None)
        if previous is not None:
            prepared_at = _timestamp(previous['observed_at'])
            validate_daily_request(original, snapshot_at=prepared_at)
            for slot, job in original['slot_plans'].items():
                current = request['slot_plans'][slot]
                if (simulation.support_requirement(job, simulation.load_snapshot(job, now=prepared_at))
                        != simulation.support_requirement(current, simulation.load_snapshot(current))):
                    raise ValueError('daily_consumed_batch_plan_immutable')
        versions[original['confirmed_for']] = original
    return versions


def execute_batch(port, journal, request, *, now=None, explicit_start=False):
    """Execute a reviewed batch through the Owner port and the durable Journal."""
    from .supported_daily import _atomic_json
    now = now or datetime.now(timezone.utc)
    request = validate_daily_request(request)
    binding = _binding(request, now)
    report = dict(outcome='stopped', operation=OPERATION, **binding, attempts=[], held_carryovers=[], held_regular=[],
        consumption_sent=False, regular_settled=0, external_regular_observed=0, carryover_settled=0)
    if explicit_start is not True:
        report['error'] = 'daily_explicit_start_required'
        return report
    identity = _hash({key: binding[key] for key in ('server', 'account_id', 'period')})
    day_journal = Journal(journal.directory / identity / binding['game_day'])
    frozen_path = day_journal.directory / 'batch.json'
    try:
        with day_journal.locked():
            events = day_journal.events()
            if events and not frozen_path.exists():
                raise ValueError('daily_frozen_batch_missing')
            versions = _request_versions(frozen_path, events, request, binding, now)
            regular, grants, consumed, pending, external = _history(events, request, binding, request_versions=versions)
            if pending:
                raise ValueError('daily_previous_result_unknown')
            report.update(regular_settled=len(regular), external_regular_observed=len(external), carryover_settled=len(consumed))
            last_observed = (_timestamp(events[-1]['result']['after']['observed_at'] if 'result' in events[-1]
                else events[-1].get('recovery', events[-1])['observed_at']) if events else None)
            # The native port supplies a live Owner callable; synthetic replay ports have no authority path.
            consent = getattr(port, 'request_consumption_consent', None)
            authorizations, reserved, denied_carryovers, authorization_binding = {}, set(), {}, None
            # Three main attacks, at most three grants/holds, and one safe 6002 recovery.
            for _ in range(8):
                port.checkpoint()
                current = _now(now) if not callable(getattr(port, 'now', None)) else _now(port.now())
                if current_game_day(current) != binding['game_day']:
                    raise ValueError('daily_game_day_changed')
                history = getattr(port, 'bind_history', None)
                if callable(history):
                    if external:
                        history(binding, regular, external=external)
                    else:
                        history(binding, regular)
                baseline_observe = getattr(port, 'observe_external_baseline', None)
                observation = (baseline_observe(deepcopy(binding)) if not events and callable(consent)
                    and callable(baseline_observe) else port.observe(deepcopy(binding)))
                current = current if not callable(getattr(port, 'now', None)) else _now(port.now())
                observation = _observation(observation, binding, current)
                stamp = _timestamp(observation['observed_at'])
                if last_observed is not None and stamp < last_observed:
                    raise ValueError('daily_observation_clock_rollback')
                last_observed = stamp
                if (not events and not regular and callable(consent)
                        and observation['cp'] == 900 and observation['remaining_count'] == 2
                        and observation['carry_stock_indices'] == [] and observation['used_unit'] == []):
                    fingerprint = port.consumption_binding()
                    preview = dict(server=binding['server'], account_id=binding['account_id'], period=binding['period'],
                        game_day=binding['game_day'], batch_id=binding['batch_id'],
                        **{key: fingerprint[key] for key in ('source_sha256', 'calibration_sha256', 'owner_core_sha256')},
                        kind='external_regular_baseline', max_count=1, observed_at=observation['observed_at'],
                        remaining_count=2, cp=900, actions=_external_candidates(request, observation))
                    approval = consent(request['confirmed_for'], deepcopy(preview))
                    if port.consumption_binding() != fingerprint:
                        raise ValueError('daily_external_regular_binding_changed')
                    if approval is None:
                        raise ValueError('daily_external_regular_not_confirmed')
                    if (type(approval) is not dict or approval.get('ok') is not True
                            or approval.get('preview') != preview or approval.get('daily_plan_hash') != request['confirmed_for']
                            or type(approval.get('selected_slot')) is not int or approval['selected_slot'] not in (1, 2, 3)):
                        raise ValueError('daily_external_regular_confirmation_invalid')
                    fresh = baseline_observe(deepcopy(binding)) if callable(baseline_observe) else port.observe(deepcopy(binding))
                    fresh_now = current if not callable(getattr(port, 'now', None)) else _now(port.now())
                    fresh = _observation(fresh, binding, fresh_now)
                    if port.consumption_binding() != fingerprint:
                        raise ValueError('daily_external_regular_binding_changed')
                    slot = approval['selected_slot']
                    event = dict(binding, state='observed_external', operation='clan_battle_external_regular', slot=slot,
                        slot_confirmed_for=request['slot_plans'][str(slot)]['confirmed_for'],
                        unit_ids=deepcopy(request['slot_plans'][str(slot)]['unit_ids']), observed_at=fresh['observed_at'],
                        before=deepcopy(observation), after=deepcopy(fresh), confirmation=deepcopy(approval))
                    validate_external_regular_event(event, request, binding)
                    _atomic_json(frozen_path, dict(binding=binding, request=request))
                    if _json(frozen_path.read_bytes()) != dict(binding=binding, request=request):
                        raise ValueError('daily_frozen_batch_readback_failed')
                    day_journal.append(event)
                    events = [event]
                    external = {slot: event}
                    report['external_regular_observed'] = 1
                    continue
                if not events and not regular and (observation['reset_verified'] is not True
                        or not _evidence(observation['reset_evidence'])):
                    raise ValueError('daily_actual_reset_readback_required')
                blocker = getattr(port, 'blocked_carryovers', None)
                blocked = (blocker(request, {stock: grant for stock, grant in grants.items() if stock not in consumed})
                    if callable(blocker) else {})
                blocked.update(denied_carryovers)
                chosen = choose_action(request, binding, observation, regular, grants, consumed, blocked_carryovers=blocked, external=external)
                report['remaining_count'] = observation['remaining_count']
                report['cp'] = observation['cp']
                report['held_carryovers'] = chosen['held_carryovers']
                report['held_regular'] = chosen['held_regular']
                if chosen['status'] != 'ready':
                    report['outcome'] = chosen['status']
                    return report
                action = chosen['action']
                from . import player_log
                player_log.text(f"正在核对第 {action['slot']} 刀：Boss {action['boss']}，第 {action['round']} 轮。")
                job = request['slot_plans'][str(action['slot'])]
                permission, action_key = None, None
                if callable(consent):
                    fingerprint = port.consumption_binding()
                    if authorization_binding != fingerprint:
                        authorizations.clear()
                        denied_carryovers.clear()
                        authorization_binding = deepcopy(fingerprint)
                    wire_action = _consumption_action(action, request, grants)
                    action_key = _hash(wire_action)
                    if action_key not in authorizations:
                        candidates = _consumption_candidates(action, request, observation, regular, grants, external=external)
                        preview = dict(server=binding['server'], account_id=binding['account_id'], period=binding['period'],
                            game_day=binding['game_day'], batch_id=binding['batch_id'],
                            **{key: fingerprint[key] for key in ('source_sha256', 'calibration_sha256', 'owner_core_sha256')},
                            kind=action['kind'], max_count=len(candidates), observed_at=observation['observed_at'],
                            remaining_count=observation['remaining_count'], cp=observation['cp'], actions=candidates)
                        approval = consent(request['confirmed_for'], deepcopy(preview))
                        if port.consumption_binding() != fingerprint:
                            raise ValueError('daily_consumption_authorization_binding_changed')
                        if approval is None:
                            if action['kind'] == 'carryover':
                                denied_carryovers[action['stock_index']] = 'carryover_not_authorized'
                                continue
                            report.update(outcome='authorization_required', error='daily_consumption_not_authorized')
                            return report
                        if (type(approval) is not dict or approval.get('ok') is not True
                                or approval.get('preview') != preview or approval.get('daily_plan_hash') != request['confirmed_for']
                                or type(approval.get('request_id')) is not str or re.fullmatch('[0-9a-f]{32}', approval['request_id']) is None):
                            raise ValueError('daily_consumption_authorization_invalid')
                        for candidate in candidates:
                            authorizations[_hash(candidate)] = deepcopy(approval)
                    permission = authorizations[action_key]
                    if action_key in reserved:
                        raise ValueError('daily_consumption_authorization_already_reserved')
                simulation.require_timing(port, job)
                ready = _ready(port.prepare(deepcopy(action), deepcopy(job), deepcopy(binding)), action, request, binding)
                player_log.text(f"第 {action['slot']} 刀编队和目标已核对，正在准备出刀。")
                if action['kind'] == 'carryover':
                    grant = grants[action['stock_index']]
                    if (ready['unit_ids'] != grant['using_unit'] or ready['support_unit'] != grant['support_unit']
                            or ready['support_owner_viewer_id'] != grant['support_owner_viewer_id']):
                        raise ValueError('daily_carryover_actual_team_mismatch')
                port.checkpoint()
                validate_daily_request(request)
                prepared_observation = getattr(port, 'prepared_regular_observation', None)
                retained = (action['kind'] == 'regular' and action['stock_index'] is None
                    and action['start_seconds'] == 90 and observation['carry_stock_indices'] == []
                    and ready['support_unit'] is None and callable(prepared_observation)
                    and prepared_observation(deepcopy(observation), deepcopy(ready)) is True)
                # A live ordinary empty-stock session retains its original observation time.
                fresh = deepcopy(observation) if retained else port.observe(deepcopy(binding))
                fresh_now = current if not callable(getattr(port, 'now', None)) else _now(port.now())
                if not retained:
                    fresh = _observation(fresh, binding, fresh_now)
                if (_timestamp(fresh['observed_at']) < stamp
                        or choose_action(request, binding, fresh, regular, grants, consumed, blocked_carryovers=blocked, external=external).get('action') != action):
                    raise ValueError('daily_target_changed_before_input')
                if not retained:
                    # Inventory observation can leave formation; only that path reenters it.
                    final_ready = _ready(port.prepare(deepcopy(action), deepcopy(job), deepcopy(binding)),
                        action, request, binding)
                    if any(final_ready[key] != ready[key] for key in READY_KEYS - {'evidence'}):
                        raise ValueError('daily_team_changed_before_input')
                    ready = final_ready
                if blocked:
                    ready['evidence']['held_carryovers'] = {str(stock): reason for stock, reason in blocked.items()}
                port.checkpoint()
                validate_daily_request(request)
                final_now = fresh_now if not callable(getattr(port, 'now', None)) else _now(port.now())
                if retained:
                    if prepared_observation(deepcopy(fresh), deepcopy(ready)) is not True:
                        raise ValueError('daily_prepared_observation_changed')
                else:
                    _observation(fresh, binding, final_now)
                intent_id = _hash(dict(binding=binding, action=action))
                if permission is not None:
                    current_authority = port.consumption_binding()
                    if current_authority != authorization_binding:
                        if (getattr(port, 'last_disconnect_recovery', {}).get('can_continue') is True
                                and all(current_authority.get(key) == authorization_binding.get(key)
                                    for key in ('source_sha256', 'calibration_sha256', 'owner_core_sha256'))):
                            authorizations.clear()
                            denied_carryovers.clear()
                            authorization_binding = None
                            continue
                        raise ValueError('daily_consumption_authorization_binding_changed')
                    if (_consumption_action(action, request, grants) not in permission['preview']['actions']
                            or sum(1 for key in reserved if authorizations.get(key, {}).get('request_id') == permission['request_id'])
                                >= permission['preview']['max_count']):
                        raise ValueError('daily_consumption_authorization_binding_changed')
                    reserved.add(action_key)  # Never released after a possibly sent input or an uncertain append.
                    ready['evidence']['consumption_authorization'] = dict(permission,
                        reserved_intent_id=intent_id, authorization_generation=authorization_binding['authorization_generation'])
                # Keep the first plan and every later snapshot binding for immutable history.
                request_path = (frozen_path if not frozen_path.exists()
                    or _json(frozen_path.read_bytes())['request']['confirmed_for'] == request['confirmed_for'] else
                    frozen_path.with_name('batch-' + request['confirmed_for'] + '.json'))
                if not request_path.exists():
                    _atomic_json(request_path, dict(binding=binding, request=request))
                    if _json(request_path.read_bytes()) != dict(binding=binding, request=request):
                        raise ValueError('daily_frozen_batch_readback_failed')
                elif _json(request_path.read_bytes()) != dict(binding=binding, request=request):
                    raise ValueError('daily_frozen_batch_readback_failed')
                intent = dict(state='in_flight', operation='clan_battle_' + action['kind'], **binding,
                    intent_id=intent_id, action=deepcopy(action),
                    slot_confirmed_for=job['confirmed_for'], ready=deepcopy(ready),
                    observed_at=fresh['observed_at'], before=deepcopy(fresh))
                day_journal.append(intent)
                previously_sent = report['consumption_sent']
                report['consumption_sent'] = True
                try:
                    player_log.text(f"第 {action['slot']} 刀开始执行；正在等待战斗与结算。")
                    value = port.execute(deepcopy(intent), deepcopy(job))
                except Exception as exc:
                    from .native import NativeInputNotSent
                    if (isinstance(exc, NativeInputNotSent) and getattr(port, 'battle_start', None) == 'not_sent'
                            and exc.receipt.get('clan_battle_phase') == action['kind'] + '_start'
                            and exc.receipt.get('daily_intent_id') == intent['intent_id']
                            and exc.receipt.get('input_execution') == 'not_sent'):
                        day_journal.append_not_sent(intent, exc.receipt)
                        report['consumption_sent'] = previously_sent
                    raise
                result_now = fresh_now if not callable(getattr(port, 'now', None)) else _now(port.now())
                result = _result(value, intent, request, binding, now=result_now)
                proposed = events + [intent, dict(intent, state='settled', result=deepcopy(result))]
                regular, grants, consumed, pending, external = _history(proposed, request, binding, request_versions=versions)
                day_journal.append(proposed[-1])
                player_log.text(f"第 {action['slot']} 刀已结算，伤害 {value.get('damage', '未知')}；记录已保存。")
                events = proposed
                report['attempts'].append(dict(slot=action['slot'], kind=action['kind'],
                    intent_id=intent['intent_id'], stock_index=action['stock_index'], damage=result['damage']))
                report.update(regular_settled=len(regular), external_regular_observed=len(external), carryover_settled=len(consumed),
                    remaining_count=result['after']['remaining_count'], cp=result['after']['cp'])
                last_observed = _timestamp(result['after']['observed_at'])
                if result['return_verified'] is not True:
                    raise ValueError('daily_return_unverified')
                after_settlement = getattr(port, 'after_settlement', None)
                if callable(after_settlement):
                    after_settlement(deepcopy(intent), deepcopy(result))
            raise ValueError('daily_batch_action_bound_exceeded')
    except Exception as exc:
        report['error'] = str(exc) or type(exc).__name__
        return report
    finally:
        recovered = getattr(port, 'last_disconnect_recovery', None)
        if type(recovered) is dict:
            report['disconnect_recovery'] = deepcopy(recovered)


def run_owned(tasker, config, directory, *, consumption_consent=None, owner_core_sha256=None):
    """Check the same bundled capability as the GUI before native daily work."""
    from .supported_daily import _atomic_json
    from .clan_battle_calibration import load_calibration
    from .native import ROOT
    request = validate_daily_request(config.get(OPERATION))
    try:
        calibrations = {}
        for job in request['slot_plans'].values():
            calibration = load_calibration(job, capability='formal')
            from .clan_battle_daily_native import NativeClanDailyAdapter, require_daily_execution
            require_daily_execution(calibration, job)
            calibrations[job['confirmed_for']] = calibration
        if (not callable(consumption_consent) or type(owner_core_sha256) is not str
                or re.fullmatch('[0-9a-f]{64}', owner_core_sha256) is None):
            raise ValueError('trusted_clan_consumption_consent_required')
        port = NativeClanDailyAdapter(tasker, directory, request=request, calibrations=calibrations,
            consumption_consent=consumption_consent, owner_core_sha256=owner_core_sha256)
        result = execute_batch(port, Journal(ROOT / '.runtime/clan-battle-daily-work/ledger'),
            request, explicit_start=True)
        result['daily_execution_available'] = True
    except (ValueError, RuntimeError, OSError) as exc:
        error = str(exc) or type(exc).__name__
        result = dict(outcome='stopped', operation=OPERATION, error=error,
            daily_plan_hash=request['confirmed_for'], consumption_sent=False, intents_created=0,
            daily_execution_available=False, held_regular=[], held_carryovers=[],
            regular_settled=0, carryover_settled=0)
    complete = result['outcome'] in ('complete', 'remind_carryover') and not result.get('error')
    report = dict(outcome='complete' if complete else 'stopped', daily_complete=False, supported_chain_complete=complete,
        selected_modules=[OPERATION], rewards_final=False,
        steps=[dict(name=OPERATION, status='complete' if complete else 'stopped', result=result)])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_json(directory / 'report.json', report)
    return report
