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


def load_snapshot(plan):
    data = _file(plan['snapshot'], 'path', 'sha256', limit=16 * 1024 * 1024)
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
    age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
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


def validate_plan(plan):
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
    load_snapshot(plan)
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
    return (reading.get('account_id') == plan['account_id']
        and reading.get('server') == plan['server'] and reading.get('period') == plan['period']
        and phase in (('B', 'C', 'BC') if plan['phase'] == 'BC' else (plan['phase'],))
        and type(reading.get('round')) is int and reading['round'] >= plan['round_min']
        and (plan['round_max'] is None or reading['round'] <= plan['round_max'])
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
    return state


def _settings(result, plan):
    evidence = result.get('settings_evidence')
    if type(evidence) is not dict or evidence.get('settings_verified') is not True:
        return False
    opening = _controls(evidence.get('opening_evidence'), plan)
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
                or type(capability.get('source')) is not str or not capability['source']):
            raise RuntimeError('clan_battle_timing_not_calibrated')
        auto, sets = plan['auto'], dict(plan['set_by_unit'])
        for switch in plan['switches']:
            next_auto = auto if switch['auto'] is None else switch['auto']
            changes = int(next_auto != auto) + sum(sets[uid] != value for uid, value in switch['set_by_unit'].items())
            # Budget includes each toggle's full readback and the clock reads surrounding it.
            window = ((2 + 2 * changes) * capability['max_observation_interval_ms']
                + changes * capability['max_action_latency_ms'])
            if window >= 1000:
                raise RuntimeError('clan_battle_timing_window_unproven')
            auto = next_auto
            sets.update(switch['set_by_unit'])


def execute_switches(port, plan, initial=None):
    """Read before each single toggle and verify the full team afterwards."""
    require_timing(port, plan)
    desired, auto, actions, switch_evidence = dict(plan['set_by_unit']), plan['auto'], [], []
    state = _controls(port.read_battle_settings(initial), plan)

    def check_window(second):
        if second is not None:
            tick = port.read_tick()
            if (type(tick) is not dict or type(tick.get('remaining_seconds')) is not int
                    or tick['remaining_seconds'] != second):
                raise RuntimeError('clan_battle_switch_window_missed')

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
    previous = 90
    deadline = time.monotonic() + 120
    for switch in plan['switches']:
        while True:
            port.checkpoint()
            if time.monotonic() > deadline:
                raise RuntimeError('clan_battle_clock_timeout')
            tick = port.read_tick()
            if type(tick) is not dict or tick.get('result'):
                raise RuntimeError('clan_battle_switch_not_reached')
            remaining = tick.get('remaining_seconds')
            if not _integer(remaining, 0, previous):
                raise RuntimeError('clan_battle_clock_unknown')
            previous = remaining
            if remaining < switch['remaining_seconds']:
                raise RuntimeError('clan_battle_switch_missed')
            if remaining == switch['remaining_seconds']:
                before = _controls(tick['state'] if 'state' in tick else port.read_battle_settings(), plan)
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
            known_not_sent = result.get('input_execution') == 'not_sent' and result.get('consumption_sent') is False
            if not known_not_sent and not (result.get('outcome') == 'simulated' and result.get('damage_verified') is True
                    and result.get('settings_verified') is True
                    and _integer(result.get('damage'), 0, 10**15) and result.get('simulation') is True):
                report['error'] = 'clan_battle_previous_result_unknown'
                return report
        intent = None
        try:
            support = support_requirement(plan, load_snapshot(plan))
            port.checkpoint()
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
        result = execute_simulation(NativeClanBattle(tasker, directory, calibration=calibration),
            Journal(ROOT / '.runtime/clan-battle-simulation-work/ledger'), plan)
    complete = (result['outcome'] == 'simulated' and result['damage_verified']
        and result['settings_verified'] and result['return_verified'] and not result.get('error'))
    report = dict(outcome='complete' if complete else 'stopped', daily_complete=False,
        supported_chain_complete=complete, selected_modules=[OPERATION], rewards_final=False,
        steps=[dict(name=OPERATION, status='complete' if complete else 'stopped', result=result)])
    _atomic_json(directory / 'report.json', report)
    return report
