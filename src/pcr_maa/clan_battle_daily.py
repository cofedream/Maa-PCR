"""Legacy pure previews; their unbound receipt shape never authorizes batch input."""
from datetime import datetime, timezone
import re

from .game_day import BEIJING, current_game_day

POLICY = 'same_boss_if_observed_else_hold'
REFERENCE_KEYS = set('slot bundle_path bundle_sha256 job_id material_sha256 confirmed_for'.split())
JOB_KEYS = set('bundle_sha256 job_id material_sha256 confirmed_for server account_id period phase round_min round_max boss unit_ids support_mode support_unit_id resolved_support_unit_id'.split())
IDENTITY_KEYS = {'server', 'account_id', 'period', 'game_day'}
RESULT_KEYS = IDENTITY_KEYS | set('kind operation slot intent_id status consumption_verified boss round phase unit_ids support_unit support_owner_viewer_id stock_index'.split())
GRANT_KEYS = IDENTITY_KEYS | set('source_slot source_intent_id boss stock_index time using_unit support_unit support_owner_viewer_id change_phase'.split())
OBSERVATION_KEYS = IDENTITY_KEYS | set('observed_at remaining_count used_unit bosses carry_stock_indices'.split())
HEX = re.compile(r'[0-9a-f]{64}')


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _object(value, keys, reason):
    if type(value) is not dict or set(value) != keys:
        raise ValueError(reason)


def _identity(value):
    if (value.get('server') != 'cn-bilibili'
            or type(value.get('account_id')) is not str
            or re.fullmatch(r'[1-9][0-9]{0,19}', value['account_id']) is None
            or type(value.get('period')) is not str
            or re.fullmatch(r'[0-9]{4}-(?:0[1-9]|1[0-2])', value['period']) is None):
        raise ValueError('daily_identity_invalid')
    return tuple(value[key] for key in ('server', 'account_id', 'period'))


def _day(value):
    try:
        if type(value) is not str or datetime.strptime(value, '%Y-%m-%d').date().isoformat() != value:
            raise ValueError()
    except ValueError as exc:
        raise ValueError('daily_game_day_invalid') from exc


def _units(value, *, team=True):
    if (type(value) is not list or (len(value) != 5 if team else len(value) > 2000)
            or any(not _integer(uid, 1, 100_000_000) for uid in value) or len(set(value)) != len(value)):
        raise ValueError('daily_units_invalid')


def _support(row, units):
    uid, owner = row['support_unit'], row['support_owner_viewer_id']
    if uid is None and owner is None:
        return
    if (not _integer(uid, 1, 100_000_000) or uid not in units or type(owner) is not str
            or re.fullmatch(r'[1-9][0-9]{0,19}', owner) is None or owner == row['account_id']):
        raise ValueError('daily_support_invalid')


def _job_team(row, job, units):
    if set(units) != set(job['unit_ids']):
        raise ValueError('daily_actual_team_mismatch')
    _support(row, units)
    if row['support_unit'] != job['resolved_support_unit_id']:
        raise ValueError('daily_actual_support_mismatch')


def _target(job, bosses):
    row = next((item for item in bosses if item['boss'] == job['boss']), None)
    phases = ('B', 'C', 'BC') if job['phase'] == 'BC' else (job['phase'],)
    if (row is None or not row['available'] or row['phase'] not in phases
            or row['round'] < job['round_min']
            or job['round_max'] is not None and row['round'] > job['round_max']):
        return None
    return row


def _reply(status, *, reason=None, held=(), **values):
    result = dict(status=status, offline_only=True, executable=False,
        held_carryovers=list(held), **values)
    if reason is not None:
        result['reason'] = reason
    return result


def preview_daily_plan(plan, observation, settlements, grants, *, resolved_jobs, now):
    """Preview synthetic receipts only; never creates attempts, grants or inputs.

    Resolved jobs must already pass the original source/snapshot/confirmation
    checks. This pure function cannot replace those checks or authorize a Start.
    """
    _object(plan, {'version', 'policy', 'slots'}, 'daily_plan_invalid')
    if (type(plan['version']) is not int or plan['version'] != 1 or plan['policy'] != POLICY
            or type(plan['slots']) is not list or len(plan['slots']) > 3):
        raise ValueError('daily_plan_invalid')
    slots = {}
    for row in plan['slots']:
        _object(row, REFERENCE_KEYS, 'daily_reference_invalid')
        if (not _integer(row['slot'], 1, 3) or row['slot'] in slots
                or any(type(row[key]) is not str or not 0 < len(row[key]) <= 32768
                    for key in ('bundle_path', 'job_id'))
                or any(type(row[key]) is not str or HEX.fullmatch(row[key]) is None
                    for key in ('bundle_sha256', 'material_sha256', 'confirmed_for'))):
            raise ValueError('daily_reference_invalid')
        slots[row['slot']] = row
    if type(resolved_jobs) is not dict or not set(resolved_jobs) <= {str(slot) for slot in slots}:
        raise ValueError('daily_resolved_jobs_invalid')
    if type(now) is not datetime:
        raise ValueError('daily_now_required')
    if type(settlements) is not list or len(settlements) > 16 or type(grants) is not list or len(grants) > 16:
        raise ValueError('daily_receipts_invalid')
    jobs, identity, unresolved_support = {}, None, False
    for key, job in resolved_jobs.items():
        if type(job) is not dict or set(job) not in (JOB_KEYS, JOB_KEYS - {'resolved_support_unit_id'}):
            raise ValueError('daily_resolved_job_invalid')
        ref = slots[int(key)]
        if any(job[field] != ref[field] for field in ('bundle_sha256', 'job_id', 'material_sha256', 'confirmed_for')):
            raise ValueError('daily_job_reference_changed')
        bound = _identity(job)
        if identity is not None and identity != bound:
            raise ValueError('daily_plan_identity_mismatch')
        identity = bound
        _units(job['unit_ids'])
        if (job['phase'] not in ('B', 'C', 'BC', 'D', 'E')
                or not _integer(job['round_min'], 1, 100_000)
                or job['round_max'] is not None and not _integer(job['round_max'], job['round_min'], 100_000)
                or not _integer(job['boss'], 1, 5) or job['support_mode'] not in ('none', 'specified', 'qsjs')
                or job['support_mode'] == 'specified' and (not _integer(job['support_unit_id'], 1, 100_000_000)
                    or job['support_unit_id'] not in job['unit_ids'])
                or job['support_mode'] != 'specified' and job['support_unit_id'] is not None):
            raise ValueError('daily_resolved_job_invalid')
        if 'resolved_support_unit_id' not in job:
            unresolved_support = True
        else:
            support = job['resolved_support_unit_id']
            if (support is not None and (not _integer(support, 1, 100_000_000) or support not in job['unit_ids'])
                    or job['support_mode'] == 'none' and support is not None
                    or job['support_mode'] == 'specified' and support != job['support_unit_id']):
                raise ValueError('daily_resolved_support_invalid')
        jobs[int(key)] = job
    if len(slots) != 3:
        return _reply('draft', reason='three_targets_not_configured')
    if set(jobs) != {1, 2, 3}:
        return _reply('needs_review', reason='job_resolution_required')
    if unresolved_support:
        return _reply('needs_review', reason='support_resolution_required')

    day, results, intents, regular = current_game_day(now), [], set(), {}
    for row in settlements:
        _object(row, RESULT_KEYS, 'daily_result_invalid')
        if _identity(row) != identity:
            raise ValueError('daily_result_identity_mismatch')
        _day(row['game_day'])
        if (row['kind'] not in ('regular', 'carryover') or not _integer(row['slot'], 1, 3)
                or row['operation'] != f"clan_battle_{row['kind']}"
                or type(row['intent_id']) is not str or not 0 < len(row['intent_id']) <= 200
                or row['intent_id'] in intents or row['status'] not in ('settled', 'unknown', 'in_flight')
                or type(row['consumption_verified']) is not bool or row['boss'] != jobs[row['slot']]['boss']
                or type(row['boss']) is not int
                or not _integer(row['round'], 1, 100_000) or row['phase'] not in ('B', 'C', 'D', 'E')
                or row['kind'] == 'regular' and row['stock_index'] is not None
                or row['kind'] == 'carryover' and not _integer(row['stock_index'], 0, 1_000_000)):
            raise ValueError('daily_result_invalid')
        intents.add(row['intent_id'])
        _units(row['unit_ids'])
        _job_team(row, jobs[row['slot']], row['unit_ids'])
        if _target(jobs[row['slot']], [dict(boss=row['boss'], round=row['round'], phase=row['phase'], available=True)]) is None:
            raise ValueError('daily_result_target_mismatch')
        results.append(row)
        if row['kind'] == 'regular':
            if row['slot'] in regular:
                raise ValueError('daily_regular_slot_duplicate')
            regular[row['slot']] = row
    # An unresolved old result is still a no-resend boundary across the day reset.
    if any(row['status'] != 'settled' or not row['consumption_verified'] for row in results):
        return _reply('blocked', reason='result_or_consumption_unknown')
    if observation is None:
        return _reply('awaiting_observation', reason='fresh_game_observation_required')
    _object(observation, OBSERVATION_KEYS, 'daily_observation_invalid')
    if _identity(observation) != identity:
        raise ValueError('daily_observation_identity_mismatch')
    _day(observation['game_day'])
    try:
        observed = datetime.fromisoformat(observation['observed_at'].replace('Z', '+00:00'))
        if observed.tzinfo is None:
            raise ValueError()
        current = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=BEIJING)
        age = (current - observed).total_seconds()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('daily_observation_time_invalid') from exc
    if observation['game_day'] != day or current_game_day(observed) != day or not 0 <= age <= 60:
        return _reply('awaiting_observation', reason='fresh_game_observation_required')
    if any(row['game_day'] != day for row in results):
        raise ValueError('daily_result_day_mismatch')
    if not _integer(observation['remaining_count'], 0, 3):
        raise ValueError('daily_remaining_count_invalid')
    _units(observation['used_unit'], team=False)
    inventory = observation['carry_stock_indices']
    if (type(inventory) is not list or len(inventory) > 16
            or any(not _integer(stock, 0, 1_000_000) for stock in inventory)
            or len(set(inventory)) != len(inventory)):
        raise ValueError('daily_carryover_inventory_invalid')
    bosses = observation['bosses']
    if type(bosses) is not list or len(bosses) > 5:
        raise ValueError('daily_boss_observation_invalid')
    seen = set()
    for boss in bosses:
        _object(boss, {'boss', 'round', 'phase', 'available'}, 'daily_boss_observation_invalid')
        if (not _integer(boss['boss'], 1, 5) or boss['boss'] in seen
                or not _integer(boss['round'], 1, 100_000) or boss['phase'] not in ('B', 'C', 'D', 'E')
                or type(boss['available']) is not bool):
            raise ValueError('daily_boss_observation_invalid')
        seen.add(boss['boss'])

    stocks, sources, pending = {}, set(), []
    for row in grants:
        _object(row, GRANT_KEYS, 'daily_carryover_invalid')
        if _identity(row) != identity or row['game_day'] != day:
            raise ValueError('daily_carryover_identity_or_day_mismatch')
        if (not _integer(row['source_slot'], 1, 3) or not _integer(row['stock_index'], 0, 1_000_000)
                or row['stock_index'] in stocks or type(row['source_intent_id']) is not str
                or row['source_intent_id'] in sources or not _integer(row['time'], 1, 90)
                or not _integer(row['change_phase'], 0, 1_000_000)):
            raise ValueError('daily_carryover_invalid')
        source = regular.get(row['source_slot'])
        if (source is None or source['intent_id'] != row['source_intent_id']
                or type(row['boss']) is not int or row['boss'] != source['boss']):
            raise ValueError('daily_carryover_source_mismatch')
        _units(row['using_unit'])
        _support(row, row['using_unit'])
        if (set(row['using_unit']) != set(source['unit_ids'])
                or any(row[key] != source[key] for key in ('support_unit', 'support_owner_viewer_id'))):
            raise ValueError('daily_carryover_actual_team_mismatch')
        stocks[row['stock_index']] = row
        sources.add(row['source_intent_id'])
    consumed_stocks = set()
    for row in results:
        if row['kind'] == 'carryover':
            grant = stocks.get(row['stock_index'])
            if (grant is None or row['stock_index'] in consumed_stocks or row['slot'] != grant['source_slot']
                    or row['round'] <= regular[grant['source_slot']]['round']
                    or set(row['unit_ids']) != set(grant['using_unit'])
                    or any(row[key] != grant[key] for key in ('support_unit', 'support_owner_viewer_id'))):
                raise ValueError('daily_carryover_consumption_mismatch')
            consumed_stocks.add(row['stock_index'])
    if observation['remaining_count'] != 3 - len(regular):
        return _reply('blocked', reason='remaining_count_history_mismatch')
    if set(inventory) != set(stocks) - consumed_stocks:
        return _reply('blocked', reason='carryover_inventory_history_mismatch')
    for row in regular.values():
        owned_used = set(row['unit_ids']) - {row['support_unit']}
        if not owned_used <= set(observation['used_unit']):
            return _reply('blocked', reason='used_units_history_mismatch')
    held, first_carryover = [], None
    for stock, grant in stocks.items():
        if stock in consumed_stocks:
            continue
        pending.append(stock)
        job = jobs[grant['source_slot']]
        target = _target(job, bosses)
        if (target is not None and target['round'] > regular[grant['source_slot']]['round']
                and grant['change_phase'] == 0):
            if first_carryover is None:
                first_carryover = dict(slot=grant['source_slot'], stock_index=stock,
                    boss=target['boss'], round=target['round'], time=grant['time'])
            continue
        held.append(dict(stock_index=stock, slot=grant['source_slot'], boss=grant['boss'],
            reason='phase_change_requires_readback' if grant['change_phase'] else 'same_boss_new_appearance_not_available_for_job'))
    if first_carryover is not None:
        return _reply('suggest_carryover', held=held, **first_carryover)
    if len(regular) == 3:
        return _reply('remind_carryover' if pending else 'complete', held=held,
            remaining_count=observation['remaining_count'])
    slot = next(slot for slot in (1, 2, 3) if slot not in regular)
    job, used = jobs[slot], set(observation['used_unit'])
    conflict = set(job['unit_ids']) & used - {job['resolved_support_unit_id']}
    if conflict:
        return _reply('blocked', held=held, slot=slot, reason='self_unit_already_used_or_qsjs_unresolved')
    target = _target(job, bosses)
    if target is None:
        return _reply('waiting_target', held=held, slot=slot, boss=job['boss'], reason='regular_target_not_available_for_job')
    return _reply('suggest_regular', held=held, slot=slot, boss=target['boss'], round=target['round'])
