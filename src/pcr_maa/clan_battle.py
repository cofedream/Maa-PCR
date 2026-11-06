"""One explicitly confirmed clan-battle simulation, with an independent journal."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
from urllib.parse import urlparse
from uuid import uuid4

OPERATION = 'clan_battle_simulation'
HEX = re.compile(r'[0-9a-f]{64}')
USER_CONFIRMED_OPENING_RULE = 'user_confirmed_90s_before_1m24'
PLAN_KEYS = set('version server account_id content_version period phase round_min round_max boss boss_name package team_label unit_ids stars auto set_by_unit switches support_mode support_unit_id reference_damage source snapshot confirmed_for'.split())


def plan_hash(plan):
    value = {key: item for key, item in plan.items() if key != 'confirmed_for'}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _file(binding, path_key, hash_key, *, limit):
    path, digest = binding.get(path_key), binding.get(hash_key)
    if (type(path) is not str or path.startswith(('\\\\', '//'))
            or PureWindowsPath(path).drive.startswith('\\\\') or ':' in path[2:]
            or not Path(path).is_absolute() or type(digest) is not str
            or HEX.fullmatch(digest) is None):
        raise ValueError('clan_battle_source_binding_invalid')
    target = Path(path)
    if not target.is_file() or not 0 < target.stat().st_size <= limit:
        raise ValueError('clan_battle_source_file_missing')
    data = target.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('clan_battle_source_changed')
    return data


def load_snapshot(plan, *, now=None):
    try:
        data = _file(plan['snapshot'], 'path', 'sha256', limit=16 * 1024 * 1024)
    except ValueError as exc:
        if now is None or str(exc) not in ('clan_battle_source_changed', 'clan_battle_source_file_missing'):
            raise
        # Refreshed account caches retain the exact bytes needed by old intents.
        digest = plan['snapshot']['sha256']
        archived = Path(plan['snapshot']['path']).parent / '.snapshot-history' / (digest + '.json')
        data = _file(dict(path=str(archived), sha256=digest), 'path', 'sha256', limit=16 * 1024 * 1024)
    try:
        snapshot = json.loads(data)
        observed = datetime.fromisoformat(snapshot['observed_at'].replace('Z', '+00:00'))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError('clan_battle_snapshot_invalid') from exc
    if (type(snapshot) is not dict or snapshot.get('schema_version') != 1
            or snapshot.get('server') != plan['server'] or snapshot.get('account_id') != plan['account_id']
            or snapshot.get('content_version') != plan['content_version']):
        raise ValueError('clan_battle_snapshot_identity_mismatch')
    if observed.tzinfo is None:
        raise ValueError('clan_battle_snapshot_time_invalid')
    age = ((now if now is not None else datetime.now(timezone.utc)) - observed.astimezone(timezone.utc)).total_seconds()
    if not 0 <= age <= 24 * 3600:
        raise ValueError('clan_battle_snapshot_expired')
    if type(snapshot.get('ownership_complete')) is not bool:
        raise ValueError('clan_battle_ownership_unknown')
    units = snapshot.get('units')
    if (type(units) is not list or len(units) > 2000 or any(type(row) is not dict
            or not _integer(row.get('unit_id'), 1, 100_000_000) for row in units)
            or len({row['unit_id'] for row in units}) != len(units)):
        raise ValueError('clan_battle_snapshot_roster_invalid')
    unknown = snapshot.get('unknown_fields')
    if type(unknown) is not list or any(type(field) is not str for field in unknown):
        raise ValueError('clan_battle_snapshot_invalid')
    # An incomplete requested-unit row must never be interpreted as an absent unit.
    owned_required = set(plan['unit_ids']) - ({plan['support_unit_id']} if plan['support_mode'] == 'specified' else set())
    ownership_paths = {f'units.{uid}.ownership' for uid in owned_required} | {'ownership', 'units.ownership'}
    if any(field in ('units', 'unit_list') or field in {f'units.{uid}' for uid in owned_required}
            or any(field == key or field.startswith(key + '.') for key in ownership_paths) for field in unknown):
        raise ValueError('clan_battle_ownership_unknown')
    return snapshot


def account_battle_stars(plan):
    snapshot = load_snapshot(plan)
    if plan['support_mode'] != 'none' and (plan['support_mode'] != 'qsjs'
            or support_requirement(plan, snapshot) is not None):
        raise RuntimeError('clan_battle_account_stars_support_uncalibrated')
    units = {row['unit_id']: row for row in snapshot['units']}
    stars = {}
    for uid, wanted in zip(plan['unit_ids'], plan['stars']):
        path = f'units.{uid}.battle_rarity'
        value = units.get(uid, {}).get('battle_rarity')
        if (not _integer(value, 1, 6)
                or any(path == key or path.startswith(key + '.') for key in snapshot['unknown_fields'])):
            raise RuntimeError('clan_battle_account_stars_unknown')
        if value != wanted:
            raise RuntimeError('clan_battle_account_stars_mismatch')
        stars[uid] = value
    return stars


def validate_plan(plan, *, snapshot_at=None):
    if type(plan) is not dict or set(plan) != PLAN_KEYS:
        raise ValueError('clan_battle_plan_invalid')
    ids, stars = plan['unit_ids'], plan['stars']
    if (plan['version'] != 1 or type(plan['version']) is not int or plan['server'] != 'cn-bilibili'
            or type(plan['account_id']) is not str or re.fullmatch(r'[1-9][0-9]{0,19}', plan['account_id']) is None
            or type(plan['content_version']) is not str or re.fullmatch(r'[0-9]{12}', plan['content_version']) is None
            or type(plan['period']) is not str or re.fullmatch(r'[0-9]{4}-(?:0[1-9]|1[0-2])', plan['period']) is None
            or plan['phase'] not in ('B', 'C', 'BC', 'D', 'E')
            or not _integer(plan['round_min'], 1, 100_000)
            or plan['round_max'] is not None and not _integer(plan['round_max'], plan['round_min'], 100_000)
            or not _integer(plan['boss'], 1, 5)
            or any(type(plan[key]) is not str or len(plan[key]) > 500 for key in ('boss_name', 'package', 'team_label'))
            or type(ids) is not list or len(ids) != 5 or any(not _integer(uid, 1, 100_000_000) for uid in ids)
            or len(set(ids)) != 5 or type(stars) is not list or len(stars) != 5
            or any(not _integer(star, 1, 6) for star in stars) or type(plan['auto']) is not bool
            or type(plan['set_by_unit']) is not dict or set(plan['set_by_unit']) != {str(uid) for uid in ids}
            or any(type(value) is not bool for value in plan['set_by_unit'].values())
            or plan['support_mode'] not in ('none', 'qsjs', 'specified')
            or plan['support_unit_id'] is not None and plan['support_unit_id'] not in ids
            or plan['support_mode'] == 'specified' and plan['support_unit_id'] is None
            or plan['support_mode'] != 'specified' and plan['support_unit_id'] is not None
            or plan['reference_damage'] is not None and not _integer(plan['reference_damage'], 1, 10**15)):
        raise ValueError('clan_battle_plan_invalid')
    switches = plan['switches']
    if type(switches) is not list or len(switches) > 90:
        raise ValueError('clan_battle_switches_invalid')
    previous = 90
    for switch in switches:
        if (type(switch) is not dict or set(switch) != {'remaining_seconds', 'auto', 'set_by_unit'}
                or not _integer(switch['remaining_seconds'], 0, previous - 1)
                or switch['auto'] is not None and type(switch['auto']) is not bool
                or type(switch['set_by_unit']) is not dict
                or not set(switch['set_by_unit']) <= set(plan['set_by_unit'])
                or any(type(value) is not bool for value in switch['set_by_unit'].values())
                or switch['auto'] is None and not switch['set_by_unit']):
            raise ValueError('clan_battle_switches_invalid')
        previous = switch['remaining_seconds']
    source = plan['source']
    if (type(source) is not dict or set(source) != {'url', 'bvid', 'page', 'cid', 'at_seconds', 'image_path', 'image_sha256', 'job_id'}
            or type(source['url']) is not str or len(source['url']) > 2000
            or type(source['bvid']) is not str or re.fullmatch(r'BV[0-9A-Za-z]{10}', source['bvid']) is None
            or any(not _integer(source[key], 1, 2**63 - 1) for key in ('page', 'cid'))
            or not _integer(source['at_seconds'], 0, 86400)
            or type(source['job_id']) is not str or not 0 < len(source['job_id'].strip()) <= 500):
        raise ValueError('clan_battle_source_invalid')
    from .guide_source import video_reference
    url = urlparse(source['url'])
    if url.fragment or video_reference(source['url']) != (source['bvid'], source['page']):
        raise ValueError('clan_battle_source_invalid')
    if (type(plan['snapshot']) is not dict or set(plan['snapshot']) != {'path', 'sha256'}
            or type(plan['confirmed_for']) is not str or HEX.fullmatch(plan['confirmed_for']) is None
            or plan_hash(plan) != plan['confirmed_for']):
        raise ValueError('clan_battle_confirmation_changed')
    _file(source, 'image_path', 'image_sha256', limit=32 * 1024 * 1024)
    load_snapshot(plan, now=snapshot_at)
    return deepcopy(plan)


def support_requirement(plan, snapshot):
    owned = {row['unit_id'] for row in snapshot['units']}
    missing = [uid for uid in plan['unit_ids'] if uid not in owned]
    if plan['support_mode'] == 'specified':
        required = plan['support_unit_id']
        if any(uid != required for uid in missing):
            raise RuntimeError('clan_battle_more_than_one_missing' if snapshot['ownership_complete'] else 'clan_battle_ownership_unknown')
        return required
    if not missing:
        return None
    if not snapshot['ownership_complete']:
        raise RuntimeError('clan_battle_ownership_unknown')
    if plan['support_mode'] != 'qsjs' or len(missing) != 1:
        raise RuntimeError('clan_battle_more_than_one_missing' if len(missing) > 1 else 'clan_battle_owned_character_missing')
    return missing[0]


def select_support(plan, snapshot, candidates):
    uid = support_requirement(plan, snapshot)
    if uid is None:
        return None
    if type(candidates) is not list:
        raise RuntimeError('clan_battle_support_unknown')
    expected_star = dict(zip(plan['unit_ids'], plan['stars']))[uid]
    for row in candidates:
        if (type(row) is dict and row.get('unit_id') == uid and type(row.get('unit_id')) is int
                and row.get('available') is True and row.get('stars') == expected_star
                and type(row.get('stars')) is int and row.get('support_id') is not None):
            return deepcopy(row)
    raise RuntimeError('clan_battle_matching_support_unavailable')


def _target(reading, plan):
    phase = reading.get('phase')
    difficulty = reading.get('selected_difficulty')
    selected_difficulty = (reading.get('round') is None
        and reading.get('round_source') == 'selected_difficulty' and reading.get('simulation') is True
        and type(difficulty) is dict and set(difficulty) == {'phase', 'boss', 'simulation'}
        and difficulty['phase'] == phase and type(difficulty['boss']) is int
        and difficulty['boss'] == reading.get('boss') and difficulty['simulation'] is True)
    return (reading.get('account_id') == plan['account_id']
        and reading.get('server') == plan['server'] and reading.get('period') == plan['period']
        and phase in (('B', 'C', 'BC') if plan['phase'] == 'BC' else (plan['phase'],))
        and (selected_difficulty or type(reading.get('round')) is int and reading['round'] >= plan['round_min']
            and (plan['round_max'] is None or reading['round'] <= plan['round_max']))
        and type(reading.get('boss')) is int and reading['boss'] == plan['boss']
        and reading.get('simulation') is True)


def _team(reading, plan, support):
    ids, stars = reading.get('unit_ids'), reading.get('stars')
    return (type(ids) is list and len(ids) == 5 and all(type(uid) is int for uid in ids)
        and len(set(ids)) == 5 and set(ids) == set(plan['unit_ids'])
        and type(stars) is list and len(stars) == 5 and all(type(star) is int for star in stars)
        and dict(zip(ids, stars)) == dict(zip(plan['unit_ids'], plan['stars']))
        and reading.get('support_unit_id') == support and _target(reading, plan))


def _controls(state, plan):
    if (type(state) is not dict or type(state.get('auto')) is not bool
            or type(state.get('sets')) is not list or len(state['sets']) != 5
            or any(type(value) is not bool for value in state['sets'])
            or type(state.get('unit_ids')) is not list or len(state['unit_ids']) != 5
            or any(type(uid) is not int for uid in state['unit_ids'])
            or len(set(state['unit_ids'])) != 5 or set(state['unit_ids']) != set(plan['unit_ids'])):
        raise RuntimeError('clan_battle_controls_unknown')
    if plan['switches'] and (type(state.get('battle_speed')) is not int or state['battle_speed'] != 1):
        raise RuntimeError('clan_battle_speed_not_verified')
    return state


def _settings(result, plan):
    evidence = result.get('settings_evidence')
    if type(evidence) is not dict or evidence.get('settings_verified') is not True:
        return False
    opening = _controls(evidence.get('opening_evidence'), plan)
    if 'battle_speed' in evidence and (type(evidence['battle_speed']) is not int
            or evidence['battle_speed'] not in (1, 2, 4) or opening.get('battle_speed') != evidence['battle_speed']
            or type(opening.get('battle_speed')) is not int
            or type(evidence.get('observation', {}).get('battle_speed')) is not int
            or evidence.get('observation', {}).get('battle_speed') != evidence['battle_speed']):
        return False
    if (opening['auto'] != plan['auto']
            or {str(uid): value for uid, value in zip(opening['unit_ids'], opening['sets'])} != plan['set_by_unit']):
        return False
    auto, sets = plan['auto'], dict(plan['set_by_unit'])
    switches = evidence.get('switch_evidence')
    if type(switches) is not list or len(switches) != len(plan['switches']):
        return False
    for switch, actual in zip(plan['switches'], switches):
        if (type(actual) is not dict or type(actual.get('remaining_seconds')) is not int
                or type(actual.get('confirmed_remaining_seconds')) is not int
                or actual['remaining_seconds'] != switch['remaining_seconds']
                or actual['confirmed_remaining_seconds'] != switch['remaining_seconds']):
            return False
        before = _controls(actual.get('before'), plan)
        if (before['auto'] != auto or {str(uid): value for uid, value in zip(before['unit_ids'], before['sets'])} != sets):
            return False
        auto = auto if switch['auto'] is None else switch['auto']
        sets.update(switch['set_by_unit'])
        after = _controls(actual.get('after'), plan)
        if after['auto'] != auto or {str(uid): value for uid, value in zip(after['unit_ids'], after['sets'])} != sets:
            return False
    observed = _controls(evidence.get('observation'), plan)
    return (evidence.get('auto') == auto and evidence.get('set_by_unit') == sets
        and observed['auto'] == auto
        and {str(uid): value for uid, value in zip(observed['unit_ids'], observed['sets'])} == sets)


def require_timing(port, plan):
    if plan['switches']:
        capability = getattr(port, 'timing_capability', {})
        if (type(capability) is not dict or capability.get('validated') is not True
                or not _integer(capability.get('max_observation_interval_ms'), 1, 250)
                or not _integer(capability.get('max_action_latency_ms'), 1, 500)
                or type(capability.get('battle_speed')) is not int or capability['battle_speed'] != 1
                or not _integer(capability.get('min_game_second_ms'), 1, 10000)
                or type(capability.get('source')) is not str or not capability['source']):
            raise RuntimeError('clan_battle_timing_not_calibrated')
        auto, sets = plan['auto'], dict(plan['set_by_unit'])
        for switch in plan['switches']:
            next_auto = auto if switch['auto'] is None else switch['auto']
            changes = int(next_auto != auto) + sum(sets[uid] != value for uid, value in switch['set_by_unit'].items())
            # Budget includes each toggle's full readback and the clock reads surrounding it.
            window = ((2 + 2 * changes) * capability['max_observation_interval_ms']
                + changes * capability['max_action_latency_ms'])
            if window >= min(1000, capability['min_game_second_ms']):
                raise RuntimeError('clan_battle_timing_window_unproven')
            auto = next_auto
            sets.update(switch['set_by_unit'])


def _units_before_first_ub(evidence, token, *, state=None):
    if (type(evidence) is not dict or type(token) is not str or not token
            or evidence.get('start_token') != token
            or type(evidence.get('initial_frame_pixel_sha256')) is not str
            or re.fullmatch('[0-9a-f]{64}', evidence['initial_frame_pixel_sha256']) is None
            or type(evidence.get('not_fired_unit_ids')) is not list
            or any(type(uid) is not int for uid in evidence['not_fired_unit_ids'])):
        return set()
    if evidence.get('source') == 'controls_disabled_since_start':
        policy = evidence.get('control_policy')
        if (type(policy) is not dict or policy.get('auto_release_on_ub') is not False
                or policy.get('source') != 'owner_account_bound_settings_readback'
                or policy.get('same_frame') is not True or type(policy.get('frame_pixel_sha256')) is not str
                or re.fullmatch('[0-9a-f]{64}', policy['frame_pixel_sha256']) is None):
            return set()
    elif evidence.get('source') == USER_CONFIRMED_OPENING_RULE:
        if (type(state) is not dict or evidence.get('basis') != 'user_confirmed_game_rule'
                or evidence.get('owner_bound') is not True or evidence.get('first_pause') is not True
                or type(evidence.get('start_seconds')) is not int or evidence['start_seconds'] != 90
                or evidence.get('action_kind') not in ('simulation', 'regular')
                or type(evidence.get('remaining_seconds')) is not int or not 84 < evidence['remaining_seconds'] <= 90
                or state.get('paused') is not True or state.get('same_frame') is not True or state.get('source') != 'maa'
                or state.get('opening_owner_bound') is not True
                or type(state.get('opening_start_seconds')) is not int or state['opening_start_seconds'] != 90
                or state.get('opening_action_kind') != evidence['action_kind']
                or state.get('remaining_seconds') != evidence['remaining_seconds']
                or state.get('opening_initial_frame_pixel_sha256') != evidence['initial_frame_pixel_sha256']
                or type(state.get('frame_pixel_sha256')) is not str or HEX.fullmatch(state['frame_pixel_sha256']) is None
                or type(state.get('unit_ids')) is not list or len(state['unit_ids']) != 5
                or any(type(uid) is not int for uid in state['unit_ids']) or len(set(state['unit_ids'])) != 5
                or evidence.get('unit_ids') != state['unit_ids']
                or set(evidence['not_fired_unit_ids']) != set(state['unit_ids'])):
            return set()
    elif evidence.get('source') == 'continuous_opening_observation':
        calibration = evidence.get('calibration')
        if (evidence.get('coverage') != 'complete' or evidence.get('owner_bound') is not True
                or evidence.get('reasons') != [] or type(evidence.get('frame_count')) is not int
                or evidence['frame_count'] < 2 or type(calibration) is not dict
                or calibration.get('source') != 'adb_command_screencap'
                or calibration.get('pause_keeps_event_visible') is not True
                or type(evidence.get('max_gap_ns')) is not int or type(calibration.get('min_visible_ns')) is not int
                or not 0 <= evidence['max_gap_ns'] < calibration['min_visible_ns']
                or evidence.get('scope_sha256') != calibration.get('scope_sha256')
                or type(evidence.get('battle_speed')) is not int or type(calibration.get('speeds')) is not list
                or evidence['battle_speed'] not in calibration['speeds']
                or any(type(evidence.get(key)) is not str or re.fullmatch('[0-9a-f]{64}', evidence[key]) is None
                    for key in ('scope_sha256', 'before_start_frame_sha256', 'paused_frame_sha256'))
                or any(type(calibration.get(key)) is not str or re.fullmatch('[0-9a-f]{64}', calibration[key]) is None
                    for key in ('capture_calibration_sha256', 'marker_calibration_sha256'))):
            return set()
    else:
        return set()
    return set(evidence['not_fired_unit_ids'])


def require_first_ub(state, units):
    """Combine independent, bound per-role proofs; a witnessed UB always blocks that role."""
    if not units:
        return
    token, observed = state.get('opening_start_token'), state.get('first_ub_observation')
    if type(observed) is dict and observed.get('start_token') == token:
        fired = observed.get('fired_unit_ids')
        if type(fired) is not list or any(type(uid) is not int for uid in fired):
            raise RuntimeError('clan_battle_first_ub_unverified')
        if set(units).intersection(fired):
            raise RuntimeError('clan_battle_first_ub_already_fired')
    proven = _units_before_first_ub(state.get('first_ub_evidence'), token, state=state)
    proven.update(_units_before_first_ub(observed, token, state=state))
    if not set(units).issubset(proven):
        raise RuntimeError('clan_battle_first_ub_unverified')


def require_frozen_opening(state, previous, *, carryover=False):
    if carryover:
        if (state.get('paused') is not True or state.get('same_frame') is not True
                or state.get('opening_action_kind') != 'carryover'
                or previous.get('opening_action_kind') != 'carryover'
                or not state.get('opening_start_token')
                or state['opening_start_token'] != previous.get('opening_start_token')
                or state.get('unit_ids') != previous.get('unit_ids')):
            raise RuntimeError('clan_battle_opening_not_verified')
        return
    if (state.get('paused') is not True or state.get('same_frame') is not True
            or type(state.get('remaining_seconds')) is not int
            or state['remaining_seconds'] != previous.get('remaining_seconds')
            or state.get('opening_start_token') != previous.get('opening_start_token')
            or state.get('first_ub_evidence') != previous.get('first_ub_evidence')
            or state.get('first_ub_observation') != previous.get('first_ub_observation')):
        raise RuntimeError('clan_battle_opening_not_verified')


def execute_switches(port, plan, initial=None):
    """Read before each single toggle and verify the full team afterwards."""
    require_timing(port, plan)
    desired, auto, actions, switch_evidence = dict(plan['set_by_unit']), plan['auto'], [], []
    state = _controls(port.read_battle_settings(initial), plan)
    calibration = getattr(port, 'calibration', None)
    formal_opening = (calibration.get('daily', {}).get('execution', {}).get('opening')
        if type(calibration) is dict and getattr(port, 'formal_opening', False) is True else None)
    paused = (getattr(port, 'opening_paused', False) is True and not plan['switches']
        and type(calibration) is dict and bool(calibration.get('auto_battle') or formal_opening) and state.get('paused') is True)
    carryover = bool(formal_opening) and getattr(port, 'action', {}).get('kind') == 'carryover'
    if carryover and (not paused or state.get('opening_action_kind') != 'carryover'
            or not state.get('opening_start_token')):
        raise RuntimeError('clan_battle_opening_not_verified')
    opening_second = state.get('remaining_seconds')
    start_seconds = getattr(port, 'start_seconds', 90)
    if getattr(port, 'opening_paused', False) and (not paused or not _integer(start_seconds, 1, 90)
            or not carryover and not _integer(opening_second, 1, start_seconds)
            or type(state.get('battle_speed')) is not int or state['battle_speed'] not in ((1, 2) if formal_opening else (1, 2, 4))):
        raise RuntimeError('clan_battle_opening_not_verified')
    opening_speed = state.get('battle_speed')
    paused_origin = deepcopy(state)
    if paused and not carryover:
        affected = set(plan['unit_ids']) if state['auto'] != auto else set()
        affected.update(uid for uid, value in zip(state['unit_ids'], state['sets']) if value != desired[str(uid)])
        target_speed = getattr(port, 'battle_speed', state['battle_speed'])
        if target_speed != state['battle_speed']:
            affected.update(plan['unit_ids'])
        require_first_ub(state, affected)
    if type(calibration) is dict and calibration.get('version') == 2:
        start_seconds = getattr(port, 'start_seconds', 90)
        if (not _integer(start_seconds, 1, 90) or state.get('same_frame') is not True
                or not carryover and type(state.get('remaining_seconds')) is not int
                or not paused and state.get('opening_direct') is True
                    and not 1 <= state['remaining_seconds'] <= start_seconds
                or not paused and state.get('opening_direct') is not True
                    and state['remaining_seconds'] != start_seconds
                or not paused and state.get('opening_direct') is True
                    and (type(state.get('battle_speed')) is not int or state['battle_speed'] != 2
                        or type(getattr(port, 'battle_speed', None)) is not int or port.battle_speed != 2)
                or not paused and (state['auto'] != auto
                    or {str(uid): value for uid, value in zip(state['unit_ids'], state['sets'])} != desired)):
            raise RuntimeError('clan_battle_opening_not_verified')

    def check_window(second):
        if second is not None:
            tick = port.read_tick()
            if (type(tick) is not dict or type(tick.get('remaining_seconds')) is not int
                    or tick['remaining_seconds'] != second):
                raise RuntimeError('clan_battle_switch_window_missed')
            _controls(tick.get('state'), plan)

    def apply(state, target_auto, target_sets, second=None):
        state = _controls(state, plan)
        toggle_options = {'remaining_seconds': second} if second is not None else {}
        def apply_auto(state):
            if state['auto'] == target_auto:
                return state
            port.checkpoint()
            check_window(second)
            expected = dict(zip(state['unit_ids'], state['sets']))
            reading = _controls(port.toggle_auto(state['auto'], target_auto, **toggle_options), plan)
            if paused:
                require_frozen_opening(reading, paused_origin, carryover=carryover)
                if type(reading.get('battle_speed')) is not int or reading['battle_speed'] != opening_speed:
                    raise RuntimeError('clan_battle_opening_not_verified')
            if reading['auto'] != target_auto or dict(zip(reading['unit_ids'], reading['sets'])) != expected:
                raise RuntimeError('clan_battle_controls_not_applied')
            check_window(second)
            actions.append(dict(control='auto', previous=state['auto'], target=target_auto))
            return reading
        # Disable AUTO first; enable it only after every intended SET has been read back.
        if not target_auto:
            state = apply_auto(state)
        for uid in plan['unit_ids']:
            slot = state['unit_ids'].index(uid)
            if state['sets'][slot] == target_sets[str(uid)]:
                continue
            port.checkpoint()
            check_window(second)
            expected = dict(zip(state['unit_ids'], state['sets']))
            expected[uid] = target_sets[str(uid)]
            reading = _controls(port.toggle_set(slot, state['sets'][slot], target_sets[str(uid)], **toggle_options), plan)
            if paused:
                require_frozen_opening(reading, paused_origin, carryover=carryover)
                if type(reading.get('battle_speed')) is not int or reading['battle_speed'] != opening_speed:
                    raise RuntimeError('clan_battle_opening_not_verified')
            if reading['auto'] != state['auto'] or dict(zip(reading['unit_ids'], reading['sets'])) != expected:
                raise RuntimeError('clan_battle_controls_not_applied')
            check_window(second)
            actions.append(dict(control='set', unit_id=uid, previous=state['sets'][slot], target=target_sets[str(uid)]))
            state = reading
        if target_auto:
            state = apply_auto(state)
        return state

    state = apply(state, auto, desired)
    opening = deepcopy(state)
    previous = getattr(port, 'start_seconds', 90)
    deadline = time.monotonic() + 120
    for switch in plan['switches']:
        while True:
            port.checkpoint()
            if time.monotonic() > deadline:
                raise RuntimeError('clan_battle_clock_timeout')
            tick = port.read_tick()
            if type(tick) is not dict or tick.get('result'):
                raise RuntimeError('clan_battle_switch_not_reached')
            observed = _controls(tick.get('state'), plan)
            remaining = tick.get('remaining_seconds')
            if not _integer(remaining, 0, previous):
                raise RuntimeError('clan_battle_clock_unknown')
            previous = remaining
            if remaining < switch['remaining_seconds']:
                raise RuntimeError('clan_battle_switch_missed')
            if remaining == switch['remaining_seconds']:
                before = observed
                if (before['auto'] != auto
                        or {str(uid): value for uid, value in zip(before['unit_ids'], before['sets'])} != desired):
                    raise RuntimeError('clan_battle_controls_changed')
                desired.update(switch['set_by_unit'])
                auto = auto if switch['auto'] is None else switch['auto']
                state = apply(before, auto, desired, remaining)
                check_window(remaining)
                switch_evidence.append(dict(remaining_seconds=remaining, confirmed_remaining_seconds=remaining,
                    before=deepcopy(before), after=deepcopy(state)))
                break
    return dict(settings_verified=True, auto=auto, set_by_unit=desired,
                opening_evidence=opening, observation=state, actions=actions, switch_evidence=switch_evidence)


def is_simulation_user_exit_history(event, intent):
    if type(event) is not dict or type(intent) is not dict:
        return False
    confirmation, evidence = event.get('confirmation'), event.get('evidence')
    ready = intent.get('team_evidence')
    return (intent.get('state') == 'in_flight' and intent.get('operation') == OPERATION
        and type(intent.get('intent_id')) is str and re.fullmatch('[0-9a-f]{32}', intent['intent_id']) is not None
        and type(intent.get('account_id')) is str and re.fullmatch('[1-9][0-9]{4,19}', intent['account_id']) is not None
        and type(intent.get('confirmed_for')) is str and HEX.fullmatch(intent['confirmed_for']) is not None
        and type(ready) is dict and ready.get('simulation') is True and ready.get('account_id') == intent['account_id']
        and event.get('state') == 'historical_unknown'
        and event.get('recovery_scope') == 'clan_battle_simulation_user_exit'
        and event.get('resolution') == 'user_confirmed_exited'
        and event.get('original_result_unknown') is True and event.get('replay_forbidden') is True
        and event.get('outcome') == 'user_exited' and event.get('simulation') is True
        and event.get('simulation_start_sent') is True and event.get('result_verified') is False
        and event.get('damage') is None and event.get('damage_verified') is False
        and event.get('settings_verified') is False
        and all(event.get(key) == value for key, value in intent.items() if key != 'state')
        and type(confirmation) is dict and confirmation.get('source') == 'direct_user_message'
        and confirmation.get('exited') is True and confirmation.get('continue_remaining') is True
        and type(confirmation.get('text')) is str and 0 < len(confirmation['text'].strip()) <= 2000
        and type(evidence) is dict and evidence.get('scene') == 'CBPage_lobby'
        and evidence.get('source') == 'maa_observation' and evidence.get('verified') is True
        and evidence.get('intent_id') == intent['intent_id'] and evidence.get('start_sent') is True
        and type(evidence.get('observed_at')) in (float, int)
        and type(intent.get('at')) in (float, int) and evidence['observed_at'] >= intent['at']
        and type(event.get('resolved_at')) in (float, int) and event['resolved_at'] >= evidence['observed_at']
        and all(type(evidence.get(key)) is str and HEX.fullmatch(evidence[key]) is not None
                for key in ('frame_sha256', 'receipt_sha256', 'start_receipt_sha256')))


def record_simulation_user_exit(journal, *, intent_id, confirmation, evidence):
    """Record a direct user's exited simulation without inventing a result."""
    with journal.locked():
        events = journal.events()
        if not events or len(events) % 2 != 1 or events[-1].get('intent_id') != intent_id:
            raise ValueError('clan_battle_exit_target_not_pending')
        intent = events[-1]
        event = dict(intent, state='historical_unknown', recovery_scope='clan_battle_simulation_user_exit',
            resolution='user_confirmed_exited', original_result_unknown=True, replay_forbidden=True,
            outcome='user_exited', simulation=True, simulation_start_sent=True, result_verified=False,
            damage=None, damage_verified=False, settings_verified=False, resolved_at=time.time(),
            confirmation=deepcopy(confirmation), evidence=deepcopy(evidence))
        if not is_simulation_user_exit_history(event, intent):
            raise ValueError('clan_battle_exit_evidence_invalid')
        journal.append(event)
        return event


def _manual_visual_audit(intent, result):
    evidence = result.get('result_evidence')
    target, team = intent.get('target'), intent.get('team_evidence')
    if type(evidence) is not dict or type(target) is not dict or type(team) is not dict:
        return False
    visual, review = evidence.get('manual_result'), evidence.get('continuity_review')
    if type(visual) is not dict or type(review) is not dict:
        return False
    ids, stars, phase = team.get('unit_ids'), team.get('stars'), target.get('phase')
    return (intent.get('state') == 'in_flight' and result.get('state') == 'settled'
        and result.get('resolution_source') == 'manual_visual_result'
        and all(result.get(key) == intent.get(key) for key in ('at', 'target', 'team_evidence'))
        and team.get('account_id') == intent.get('account_id') and team.get('server') == target.get('server')
        and team.get('period') == target.get('period') and type(team.get('boss')) is int and team.get('boss') == target.get('boss')
        and phase in ('B', 'C', 'BC', 'D', 'E') and _integer(target.get('boss'), 1, 5)
        and team.get('phase') in (('B', 'C', 'BC') if phase == 'BC' else (phase,))
        and team.get('simulation') is True and type(visual.get('boss')) is int and visual.get('boss') == target.get('boss')
        and visual.get('phase') == team.get('phase')
        and type(ids) is list and len(ids) == 5 and all(type(uid) is int for uid in ids) and len(set(ids)) == 5
        and type(stars) is list and len(stars) == 5 and all(_integer(star, 1, 6) for star in stars)
        and evidence.get('continuity_review_required') is False and review.get('reviewer') == 'root'
        and review.get('intent_id') == intent.get('intent_id')
        and evidence.get('damage_verification_source') == 'manual_visual_result_and_hp_difference'
        and evidence.get('native_result_verified') is False and evidence.get('native_readback') == 'unknown'
        and evidence.get('simulation') is True and evidence.get('damage') == result.get('damage')
        and type(evidence.get('manual_battle_intervention')) is bool and evidence.get('manual_return_observed') is True
        and result.get('settings_verified') is False and result.get('normal_owner_acceptance') is False
        and result.get('first_ub_verified') is False and result.get('normal_return_verified') is False
        and evidence.get('settings_verified') is False and evidence.get('first_ub_verified') is False
        and evidence.get('normal_return_verified') is False
        and all(type(evidence.get(key)) is str and HEX.fullmatch(evidence[key]) is not None
                for key in ('result_frame_sha256', 'returned_frame_sha256', 'result_return_manifest_sha256'))
        and _integer(visual.get('hp_maximum'), 1, 10**15)
        and _integer(visual.get('hp_current'), 0, visual['hp_maximum'])
        and _integer(visual.get('value'), 0, 10**15)
        and visual['hp_maximum'] - visual['hp_current'] == visual['value'] == result.get('damage'))


def _simulation_report(plan):
    return dict(outcome='stopped', operation=OPERATION, account_id=plan['account_id'],
        period=plan['period'], phase=plan['phase'], round_min=plan['round_min'], round_max=plan['round_max'],
        boss=plan['boss'], team_label=plan['team_label'], simulation_started=False, consumption_sent=False,
        damage=None, damage_verified=False, settings_verified=False, settlement_verified=False,
        return_verified=False, return_error=None, return_evidence=None)


def execute_simulation(port, journal, plan):
    plan = validate_plan(plan)
    report = _simulation_report(plan)
    with journal.locked():
        events = journal.events()
        for index, intent in enumerate(events[::2]):
            if intent.get('operation') != OPERATION:
                raise ValueError('clan_battle_ledger_invalid')
            if 2 * index + 1 >= len(events):
                report['error'] = 'clan_battle_previous_result_unknown'
                return report
            result = events[2 * index + 1]
            if any(result.get(key) != intent.get(key) for key in ('intent_id', 'operation', 'account_id', 'confirmed_for')):
                raise ValueError('clan_battle_ledger_binding_invalid')
            if is_simulation_user_exit_history(result, intent):
                continue
            known_not_sent = result.get('input_execution') == 'not_sent' and result.get('consumption_sent') is False
            audited = (result.get('outcome') == 'manual_calibration_result'
                and (result.get('resolution_source') == 'manual_same_battle_saved_frame_replay'
                     or _manual_visual_audit(intent, result))
                and type(result.get('result_evidence')) is dict and bool(result['result_evidence'])
                and result['result_evidence'].get('intent_id') == intent['intent_id']
                and result['result_evidence'].get('damage_verified') is True
                and result['result_evidence'].get('normal_owner_acceptance') is False)
            # An audited finished simulation can precede a fresh request; its failed settings stay failed.
            if not known_not_sent and not ((result.get('outcome') == 'simulated' and result.get('settings_verified') is True or audited)
                    and result.get('damage_verified') is True
                    and _integer(result.get('damage'), 0, 10**15) and result.get('simulation') is True):
                report['error'] = 'clan_battle_previous_result_unknown'
                return report
        intent = None
        try:
            support = support_requirement(plan, load_snapshot(plan))
            port.checkpoint()
            from . import player_log
            player_log.text('公会战模拟：正在核对目标和队伍。')
            port.prepare(plan)
            ready = port.verify_team(plan)
            if (type(ready) is not dict or not _team(ready, plan, support)
                    or type(ready.get('native_evidence')) is not dict or not ready['native_evidence']):
                raise RuntimeError('clan_battle_team_or_target_unknown')
            require_timing(port, plan)
            port.checkpoint()
            # Re-read source bindings immediately before reserving the single start.
            validate_plan(plan)
            intent = dict(state='in_flight', operation=OPERATION, intent_id=uuid4().hex,
                account_id=plan['account_id'], confirmed_for=plan['confirmed_for'], at=time.time(),
                target={key: plan[key] for key in ('server', 'period', 'phase', 'round_min', 'round_max', 'boss')},
                team_evidence=deepcopy(ready))
            journal.append(intent)
            report.update(simulation_started=True, consumption_sent=True)
            player_log.text('公会战模拟：编队已核对，正在执行并等待结果。')
            result = port.start_and_read(plan)
            if (type(result) is not dict or not _team(result, plan, support)
                    or result.get('result_verified') is not True or result.get('settings_verified') is not True
                    or not _settings(result, plan)
                    or not _integer(result.get('damage'), 0, 10**15)
                    or any(type(result.get(key)) is not dict or not result[key] for key in ('native_evidence', 'result_evidence'))):
                raise RuntimeError('clan_battle_result_unknown')
            journal.append(dict(intent, state='settled', outcome='simulated', simulation=True,
                damage=result['damage'], damage_verified=True, settings_verified=True,
                result_evidence=deepcopy(result), resolved_at=time.time()))
            player_log.text(f"模拟战结果已确认，伤害 {result['damage']}。")
            report.update(outcome='simulated', damage=result['damage'], damage_verified=True,
                settings_verified=True, settlement_verified=True, result=result,
                intent_id=intent['intent_id'])
            try:
                returned = port.return_to_target(plan)
                if (type(returned) is not dict or returned.get('return_verified') is not True
                        or not _target(returned, plan) or type(returned.get('native_evidence')) is not dict
                        or not returned['native_evidence']):
                    raise RuntimeError('clan_battle_return_readback_unknown')
                report.update(return_verified=True, return_evidence=deepcopy(returned))
            except Exception as exc:
                report.update(error='clan_battle_return_unverified',
                    return_error=str(exc) or type(exc).__name__)
        except Exception as exc:
            report['error'] = str(exc) or type(exc).__name__
            if report['error'] == 'clan_battle_closed':
                report['outcome'] = 'unavailable'
            from .native import NativeInputNotSent
            if (intent is not None and isinstance(exc, NativeInputNotSent)
                    and exc.receipt.get('clan_battle_phase') == 'simulation_start'
                    and getattr(port, 'battle_start', None) == 'not_sent'):
                journal.append_not_sent(intent, exc.receipt)
                report.update(simulation_started=False, consumption_sent=False)
        finally:
            recovery = getattr(port, 'last_disconnect_recovery', None)
            if type(recovery) is dict:
                report['disconnect_recovery'] = dict(connection_verified=(recovery.get('account_id') == plan['account_id']
                    and type(recovery.get('account_evidence')) is dict and bool(recovery['account_evidence'])),
                    battle_result_verified=False)
            finish = getattr(port, 'finish_inheritance', None)
            if callable(finish):
                try:
                    restored = finish()
                    if restored is not None:
                        report['inheritance'] = restored
                        if restored.get('restored') is not True:
                            report.setdefault('error', 'clan_battle_inheritance_restore_pending')
                except Exception as exc:
                    report['inheritance'] = dict(restored=False, error=str(exc) or type(exc).__name__)
                    report.setdefault('error', 'clan_battle_inheritance_restore_pending')
        return report


def run_owned(tasker, config, directory):
    from .native import ROOT
    from .preset_execution import Journal
    from .supported_daily import _atomic_json
    from .clan_battle_native import NativeClanBattle
    from .clan_battle_calibration import load_calibration
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    plan = validate_plan(config.get(OPERATION))
    try:
        calibration = load_calibration(plan)
    except (ValueError, RuntimeError, OSError) as exc:
        result = dict(_simulation_report(plan), error=str(exc) or type(exc).__name__)
    else:
        options = {'battle_speed': config['clan_battle_speed']} if 'clan_battle_speed' in config else {}
        result = execute_simulation(NativeClanBattle(tasker, directory, calibration=calibration, **options),
            Journal(ROOT / '.runtime/clan-battle-simulation-work/ledger'), plan)
    complete = (result['outcome'] == 'simulated' and result['damage_verified']
        and result['settings_verified'] and result['return_verified'] and not result.get('error'))
    report = dict(outcome='complete' if complete else 'stopped', daily_complete=False,
        supported_chain_complete=complete, selected_modules=[OPERATION], rewards_final=False,
        steps=[dict(name=OPERATION, status='complete' if complete else 'stopped', result=result)])
    _atomic_json(directory / 'report.json', report)
    return report
