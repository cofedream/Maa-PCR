"""One supported pass. Transactions and fresh reads belong to components."""
import json
import math
import os
import re
import stat
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from .game_day import current_game_day
from pathlib import Path
from uuid import uuid4

LEGACY_ORDER = ('event', 'deep', 'rewards', 'very_hard', 'hard')
ORDER = LEGACY_ORDER + ('rewards_final',)
MODULE_ORDER = ('schedule_onekey', 'event', 'abyss') + LEGACY_ORDER[1:] + ('normal', 'guide_trial', 'clan_battle_simulation', 'clan_battle_daily')
_DEFAULT_REWARDS_FINAL = object()
REWARD_STEPS = ('rewards', 'rewards_final')
PRESET_STEPS = ('deep', 'very_hard', 'hard', 'normal')
NOTICE = '【仅已支持项目串联：活动→深域→每日任务/礼物（早段）→高难→困难→每日任务/礼物（收尾）】\n日程执行：unsupported；深渊仅支持前哨；首领、购买重置和次数调整：unsupported。完整日常未交付，daily_complete=false。'
SCHEDULE_NOTICE = '【显式可选日程一键→活动→深域→每日任务/礼物（早段）→高难→困难→每日任务/礼物（收尾）】\n日程只限本次逐项授权并通过独立门禁；深渊仅支持前哨；首领、购买重置和次数调整：unsupported。完整日常未交付，daily_complete=false。'
CONTROL_ROOT = Path(__file__).resolve().parents[2] / '.runtime/supported-daily-work'
REPORT_LIMIT = 16 * 1024 * 1024
CONTROL_ERRORS = {'invalid_run_id', 'run_unavailable', 'unsafe_control_path', 'control_file_missing',
                  'control_file_too_large', 'control_snapshot_changed', 'invalid_control_json',
                  'invalid_report', 'invalid_stop_request', 'control_busy', 'cooperative_stop_unsupported'}
STEP_STATES = {'not_run', 'running', 'executed', 'complete', 'partial', 'stopped', 'failed',
               'interrupted', 'skipped_disabled', 'skipped_goal_met', 'skipped_insufficient_stamina',
               'skipped_insufficient_tickets', 'skipped_event_unavailable'}


def build_plan(*, selected_modules=None, rewards_final=_DEFAULT_REWARDS_FINAL,
               include_schedule_onekey=False):
    """Pure ordered step names; an empty explicit plan is legal until execution.

    Explicit selections retain their list/tuple order and default to no final rewards.
    Omission preserves the CLI's complete ORDER, including final rewards.
    A supplied rewards_final must be a bool, never None or a truthy substitute.
    """
    if type(include_schedule_onekey) is not bool:
        raise ValueError('include_schedule_onekey_must_be_boolean')
    if rewards_final is not _DEFAULT_REWARDS_FINAL and type(rewards_final) is not bool:
        raise ValueError('rewards_final_must_be_boolean')
    if selected_modules is None:
        if rewards_final is False:
            raise ValueError('rewards_final_false_requires_selected_modules')
        return ('schedule_onekey',) + ORDER if include_schedule_onekey else ORDER
    if include_schedule_onekey:
        raise ValueError('selection_conflicts_with_include_schedule_onekey')
    if type(selected_modules) not in (list, tuple):
        raise ValueError('selected_modules_must_be_list_or_tuple')
    if any(type(name) is not str or name not in MODULE_ORDER for name in selected_modules):
        raise ValueError('invalid_selected_module')
    if len(set(selected_modules)) != len(selected_modules):
        raise ValueError('duplicate_selected_module')
    ordered = tuple(selected_modules)
    if 'guide_trial' in ordered and (ordered != ('guide_trial',) or rewards_final is True):
        raise ValueError('guide_trial_must_run_alone')
    if 'clan_battle_simulation' in ordered and (ordered != ('clan_battle_simulation',) or rewards_final is True):
        raise ValueError('clan_battle_simulation_must_run_alone')
    if 'clan_battle_daily' in ordered and (ordered != ('clan_battle_daily',) or rewards_final is True):
        raise ValueError('clan_battle_daily_must_run_alone')
    return ordered + (('rewards_final',) if rewards_final is True else ())


def _preset_exit_page(plan, index, config):
    if plan[index] not in PRESET_STEPS:
        return 'home'
    for name in plan[index + 1:]:
        if name not in PRESET_STEPS:
            break
        if any(p['kind'] == name and p['target_per_stage'] > 0 for p in config['presets']):
            return 'preset'
    return 'home'


def _checked_directory(directory):
    # Reject symlinks and Windows junctions, including ancestors of the fixed root.
    for path in (*reversed(directory.parents), directory):
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ValueError('unsafe_control_path')


def _plain_file(path, *, optional=False):
    _checked_directory(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return None
        raise ValueError('control_file_missing') from None
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('unsafe_control_path')
    return info


def _run_directory(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r'run-[0-9a-f]{32}', run_id):
        raise ValueError('invalid_run_id')
    directory = CONTROL_ROOT / run_id
    try:
        _checked_directory(directory)
    except OSError:
        raise ValueError('run_unavailable') from None
    return directory


def _atomic_json(path, value):
    _plain_file(path, optional=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    temporary = path.with_name('.' + path.name + '-' + uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    _plain_file(path, optional=True)
    os.replace(temporary, path)  # Readers see either complete snapshot; failures keep evidence.


@contextmanager
def _control_lock(directory):
    """Serialize stop publication with step admission; never held during a component."""
    path = directory / 'control.lock'
    if _plain_file(path, optional=True) is None:
        try:
            with path.open('xb') as handle:
                handle.write(b'0')
        except FileExistsError:
            pass
    info = _plain_file(path)
    with path.open('r+b') as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_ino, opened.st_dev, opened.st_nlink) != (info.st_ino, info.st_dev, 1):
            raise ValueError('unsafe_control_path')
        if os.name == 'nt':
            import msvcrt
            lock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            lock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        for attempt in range(100):
            try:
                handle.seek(0)
                lock()
                break
            except OSError:
                if attempt == 99:
                    raise ValueError('control_busy') from None
                time.sleep(.01)
        try:
            yield
        finally:
            handle.seek(0)
            unlock()


def _local_json(path):
    info = _plain_file(path)
    if info.st_size > REPORT_LIMIT:
        raise ValueError('control_file_too_large')
    with path.open('rb') as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_ino, opened.st_dev, opened.st_nlink) != (info.st_ino, info.st_dev, 1):
            raise ValueError('control_snapshot_changed')
        payload = handle.read(REPORT_LIMIT + 1)
    if len(payload) > REPORT_LIMIT:
        raise ValueError('control_file_too_large')
    def object_fields(pairs):
        fields = {}
        for key, value in pairs:
            if key in fields:
                raise ValueError('duplicate_json_field')
            fields[key] = value
        return fields
    try:
        return json.loads(payload.decode('utf-8'), object_pairs_hook=object_fields)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('invalid_control_json') from None


def _timestamp(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _read_report(directory):
    report = _local_json(directory / 'report.json')
    def require(condition):
        if not condition:
            raise ValueError('invalid_report')
    try:
        require(isinstance(report, dict) and report['daily_complete'] is False)
        require(type(report['supported_chain_complete']) is bool)
        require(report.get('run_id', directory.name) == directory.name)
        require(report.get('control_version') is None or type(report['control_version']) is int and report['control_version'] == 1)
        require(date.fromisoformat(report['game_day']).isoformat() == report['game_day'])
        require(_timestamp(report['started_at']))
        outcome = report['outcome']
        require(outcome in ('running', 'complete', 'partial', 'stopped', 'failed',
                           'interrupted', 'stopped_by_request'))
        if outcome == 'running':
            require('finished_at' not in report and not report['supported_chain_complete'])
        else:
            require(_timestamp(report['finished_at']) and report['finished_at'] >= report['started_at'])
        steps = report['steps']
        require(isinstance(steps, list))
        selection_fields = {'selection_version', 'selected_modules', 'rewards_final'}
        if selection_fields.intersection(report):
            require(selection_fields.issubset(report))
            require(type(report['selection_version']) is int and report['selection_version'] == 1)
            require(type(report['selected_modules']) is list)
            plan = build_plan(selected_modules=report['selected_modules'], rewards_final=report['rewards_final'])
            require(report['selected_modules'] == [n for n in plan if n != 'rewards_final'])
            require(bool(plan) and tuple(s['name'] for s in steps) == plan)
        else:
            require(tuple(s['name'] for s in steps) in (ORDER, ('schedule_onekey',) + ORDER,
                                                       LEGACY_ORDER, ('schedule_onekey',) + LEGACY_ORDER))
        tail = False
        for step in steps:
            require(step.get('exit_page', 'home') in ('home', 'preset'))
            if step.get('exit_page') == 'preset':
                index = steps.index(step)
                require(step['name'] in PRESET_STEPS and index + 1 < len(steps)
                        and steps[index + 1]['name'] in PRESET_STEPS)
            require(step['status'] in STEP_STATES)
            require(not tail or step['status'] == 'not_run')
            tail |= step['status'] in ('not_run', 'running', 'stopped', 'failed', 'interrupted')
            require('result' not in step or isinstance(step['result'], dict))
            if step['status'] == 'not_run':
                require('result' not in step and 'started_at' not in step and 'finished_at' not in step)
            if step['status'] != 'not_run':
                require(_timestamp(step['started_at']))
            if step['status'] not in ('not_run', 'running'):
                require(_timestamp(step['finished_at']))
                require(step['finished_at'] >= step['started_at'])
            if step['status'] in ('executed', 'complete', 'partial', 'skipped_goal_met', 'skipped_disabled',
                                  'skipped_insufficient_stamina', 'skipped_insufficient_tickets',
                                  'skipped_event_unavailable'):
                result = step['result']
                expected = (_schedule_status(result, report['game_day'], result.get('device'),
                                              result.get('authorizations', []),
                                              report.get('schedule_attest_current_plan', False),
                                              report.get('run_id', directory.name), report.get('schedule_gui_start_intent', False))
                            if step['name'] == 'schedule_onekey' else
                            _status(step['name'], result, exit_page=step.get('exit_page', 'home')))
                require(step['status'] == expected)
            if outcome != 'running':
                require(step['status'] != 'running')
        if outcome in ('complete', 'partial'):
            require(all(s['status'] not in ('not_run', 'running', 'stopped', 'failed', 'interrupted') for s in steps))
            require((outcome == 'partial') == any(s['status'] == 'partial' for s in steps))
        require(report['supported_chain_complete'] is (outcome == 'complete'))
        if outcome == 'stopped_by_request':
            require(report.get('stop_acknowledged') is True)
            pending = [s for s in steps if s['status'] == 'not_run']
            require(bool(pending) and pending[0]['name'] == report['stopped_before'])
            require(all(s['status'] not in ('stopped', 'failed', 'interrupted') for s in steps))
        else:
            require(report.get('stop_acknowledged') in (None, False))
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        raise ValueError('invalid_report') from None
    return report


def _stop_requested(directory):
    path = directory / 'stop-request.json'
    if _plain_file(path, optional=True) is None:
        return False
    marker = _local_json(path)
    if (not isinstance(marker, dict) or set(marker) != {'version', 'run_id', 'requested_at'}
            or type(marker['version']) is not int or marker['version'] != 1 or marker['run_id'] != directory.name
            or not _timestamp(marker['requested_at'])):
        raise ValueError('invalid_stop_request')
    return True


def inspect_run(run_id):
    """Read one report only. Never follow receipt paths, inspect a process, or clear a ledger."""
    directory = _run_directory(run_id)
    report = _read_report(directory)
    requested = _stop_requested(directory)
    steps = []
    for step in report['steps']:
        result = step.get('result', {})
        attempted = result.get('consumption_sent', result.get('one_key_attempted'))
        settled = result.get('settlement_verified')
        if step['name'] in REWARD_STEPS:
            items = result.get('items', {})
            if isinstance(items, dict) and set(items) == {'missions', 'gifts'} and all(isinstance(i, dict) for i in items.values()):
                attempted = any(i.get('claim_attempted') is True for i in items.values())
                settled = all(i.get('result_verified') is True for i in items.values() if i.get('claim_attempted'))
        if type(attempted) is not bool:
            attempted = False if step['status'] == 'not_run' else None
        settled = settled if type(settled) is bool else None
        unknown = (step['status'] in ('running', 'stopped', 'failed', 'interrupted')
                   or attempted is True and settled is not True)
        steps.append(dict(name=step['name'], status=step['status'], consumption_attempted=attempted,
                          settlement_verified=settled, unsettled_or_unknown=unknown))
    return dict(run_id=run_id, game_day=report['game_day'], reported_outcome=report['outcome'],
                process_state='unknown', report_finalized='finished_at' in report,
                stop_state=('acknowledged_at_boundary' if report.get('stop_acknowledged') is True
                            else 'requested_not_acknowledged' if requested else 'not_requested'),
                steps=steps, unsettled_or_unknown=any(s['unsettled_or_unknown'] for s in steps),
                supported_chain_complete=report['supported_chain_complete'], daily_complete=False,
                abyss='frontposts_only', abyss_boss='unsupported', settlement_source='selected_report_only',
                message='报告状态不证明进程存活；摘要不是账本核销或重试许可。')


def request_stop(run_id):
    directory = _run_directory(run_id)
    # Reject unreadable/legacy runs before creating a lock; recheck under lock for races.
    report = _read_report(directory)
    if report['outcome'] != 'running':
        return dict(run_id=run_id, outcome='already_finalized', process_state='unknown',
                    daily_complete=False, message='报告已结束；未请求停止，未改写报告。')
    if report.get('control_version') != 1:
        raise ValueError('cooperative_stop_unsupported')
    with _control_lock(directory):
        report = _read_report(directory)
        if report['outcome'] != 'running':
            return dict(run_id=run_id, outcome='already_finalized', process_state='unknown',
                        daily_complete=False, message='报告已结束；未请求停止，未改写报告。')
        if report.get('control_version') != 1:
            raise ValueError('cooperative_stop_unsupported')
        if not _stop_requested(directory):
            _atomic_json(directory / 'stop-request.json', dict(version=1, run_id=run_id, requested_at=time.time()))
    return dict(run_id=run_id, outcome='stop_requested', process_state='unknown', daily_complete=False,
                message='已请求，当前在途步骤可能尚未停止；仅在步骤边界确认。')


def validate(config, *, execute, game_day, context, include_schedule_onekey=False,
             schedule_authorizations=(), schedule_attest_current_plan=False,
             selected_modules=None, rewards_final=_DEFAULT_REWARDS_FINAL):
    from .daily import validate_config
    plan = build_plan(selected_modules=selected_modules, rewards_final=rewards_final,
                      include_schedule_onekey=include_schedule_onekey)
    if not plan:
        raise ValueError('empty_selection')
    has_schedule = 'schedule_onekey' in plan
    if execute is not True:
        raise ValueError('必须显式 --execute；只读请使用 inspect-daily')
    config = validate_config(config)
    if 'clan_battle_daily' in plan and 'clan_battle_daily' not in config:
        raise ValueError('clan_battle_daily_configuration_required')
    if config['version'] in (3, 4):
        if selected_modules is None:
            raise ValueError(f"v{config['version']}_requires_explicit_alpha_selection")
    has_sweep = any(name in plan for name in ('event', 'abyss')) or any(
        p['kind'] != 'normal' and p['kind'] in plan and p['target_per_stage'] > 0 for p in config['presets'])
    # Only explicit local GUI selections use current counters, not legacy CLI.
    # The derived date labels this run; it does not prove a game refresh.
    current_state = context == 'gui-current-state'
    if current_state:
        if config['version'] not in (3, 4) or selected_modules is None or game_day != '':
            raise ValueError('gui_current_state_requires_local_selection_and_derived_day')
        game_day = current_game_day()
        from .schedule_quick import gui_device, validate_gui_device
        validate_gui_device(gui_device(config))
    if config['start'] != 'home' or (has_sweep and context not in ('user-reported-unreset', 'gui-current-state')):
        raise ValueError('串联要求主页起点和显式 user-reported-unreset 语境')
    if has_sweep or has_schedule:
        if type(game_day) is not str or not game_day or date.fromisoformat(game_day).isoformat() != game_day:
            raise ValueError('game-day must be YYYY-MM-DD')
    # Player targets are checked against actual limits, remaining counts and quotes.
    if type(schedule_attest_current_plan) is not bool:
        raise ValueError('schedule_attest_current_plan_must_be_boolean')
    if schedule_attest_current_plan and not has_schedule:
        raise ValueError('schedule_attestation_requires_include_schedule_onekey')
    if has_schedule:
        if current_state:
            from .schedule_quick import gui_device, validate_gui_start
            validate_gui_start(game_day, gui_device(config), schedule_authorizations)
        else:
            from .onekey_history import schedule_device
            from .schedule_onekey import validate_execution
            validate_execution(game_day, schedule_device(config),
                               schedule_authorizations, portable=True)
    elif schedule_authorizations:
        raise ValueError('schedule_authorizations_require_include_schedule_onekey')
    return config


def _run_step(tasker, name, config, game_day, context, *, exit_page='home'):
    if name == 'normal' and not any(p['kind'] == 'normal' for p in config['presets']):
        return dict(outcome='skipped_disabled', consumption_sent=False,
                    settlement_verified=False, daily_complete=False)
    if name in ('deep', 'very_hard', 'hard', 'normal') and next(p for p in config['presets'] if p['kind'] == name)['target_per_stage'] == 0:
        return dict(outcome='skipped_disabled', consumption_sent=False,
                    settlement_verified=False, daily_complete=False)
    from .native import ROOT
    from .preset_execution import Journal, execute_one
    if name == 'event':
        from .event import execute_event
        from .event_native import NativeEvent
        return execute_event(NativeEvent(tasker, start='home'),
            Journal(ROOT / '.runtime/event-work/ledger'),
            game_day=game_day, context=context, execute=True)
    if name == 'abyss':
        from .abyss import execute_abyss
        from .abyss_native import NativeAbyss
        from .onekey_history import schedule_device
        from .schedule_quick import gui_device
        target = gui_device(config) if context == 'gui-current-state' else schedule_device(config)
        return execute_abyss(NativeAbyss(tasker, start='home'),
            Journal(ROOT / '.runtime/abyss-work/ledger'),
            game_day=game_day, context=context, execute=True, device=target)
    if name in REWARD_STEPS:
        from .rewards import claim_rewards
        return claim_rewards(tasker, execute=True, collect_stamina=config.get('collect_gift_stamina', False))
    from .preset_execution_native import NativePreset
    return execute_one(NativePreset(tasker), next(p for p in config['presets'] if p['kind'] == name),
        context, Journal(ROOT / '.runtime/preset-execution-work/ledger'),
        execute=True, fit_remaining=True, game_day=game_day, exit_page=exit_page)


def _run_schedule(tasker, game_day, device, authorizations, attest_current_plan=False, invocation_id=None, gui_start_intent=False, confirm_new_cycle=False):
    if gui_start_intent:
        from .schedule_quick import schedule_quick
        return schedule_quick(tasker, game_day=game_day, device=device,
                              authorizations=authorizations, invocation_id=invocation_id)
    from .schedule_onekey import schedule_onekey
    gui = dict(gui_start_intent=True) if gui_start_intent else {}
    return schedule_onekey(tasker, execute=True, game_day=game_day,
                           device=device, authorizations=authorizations,
                           attest_current_plan=attest_current_plan, invocation_id=invocation_id,
                           **(dict(confirm_new_cycle=True) if confirm_new_cycle else {}), **gui)


def _schedule_status(result, game_day, device, authorizations, attest_current_plan=False, invocation_id=None, gui_start_intent=False):
    from .schedule_onekey import ITEMS

    if not isinstance(result, dict):
        return 'stopped'
    if result.get('mode') == 'gui-onekey':
        from .schedule_quick import verified
        if gui_start_intent is not True or not verified(result, game_day=game_day,
                device=device, authorizations=authorizations, invocation_id=invocation_id):
            return 'stopped'
        return 'executed' if result['outcome'] == 'executed' else 'complete'
    if result.get('original_execution_unresolved'):
        return _readonly_schedule_status(result, game_day, device, authorizations, invocation_id, gui_start_intent)
    outcome = result.get('outcome')
    resources = result.get('resources') or {}
    if not isinstance(resources, dict):
        return 'stopped'
    before = resources.get('before') or {}
    after = resources.get('after') or {}
    rows = result.get('after_rows') or {}
    sent = result.get('sent')
    if (not all(isinstance(value, dict) for value in (before, after, rows))
            or not isinstance(sent, list) or any(not isinstance(action, str) for action in sent)):
        return 'stopped'
    if (outcome not in ('already_complete', 'executed')
            or result.get('error') or result.get('reason')
            or result.get('game_day') != game_day or result.get('device') != device
            or result.get('authorizations') != list(authorizations)
            or result.get('nine_rows_verified') is not True
            or set(rows) != set(ITEMS) or any(value != 'complete' for value in rows.values())
            or result.get('home_verified') is not True
            or result.get('settlement_verified') is not True
            or type(resources.get('jewels_delta')) is not int or resources['jewels_delta'] != 0
            or type(before.get('jewels')) is not int or before['jewels'] < 0
            or type(after.get('jewels')) is not int
            or before['jewels'] != after.get('jewels')):
        return 'stopped'
    if outcome == 'executed':
        from .onekey_history import verify_execution
        try:
            proof = result['journal_evidence']
            verify_execution(result, proof['start'], proof['end'], proof['actions'], invocation_id=invocation_id)
        except (KeyError, ValueError, TypeError, AttributeError, IndexError, StopIteration):
            return 'stopped'
        # Current explicit permission and component proof are both required.
        # Neither an old audit nor an unchanged balance supplies permission.
        return ('executed' if ((gui_start_intent is True and result.get('settings_assurance') == 'gui_selected_tasks_start')
                or (attest_current_plan is True and result.get('settings_assurance') == 'user_attested_current_plan'))
                and result.get('schedule_executed') is True
                and result.get('one_key_attempted') is True
                and sent.count('onekey') == 1 else 'stopped')
    # Zero-consumer observations cannot contradict attached native/Journal proof.
    consumers = {'onekey', 'dungeon_confirm', 'mana_confirm', 'exp_confirm'}
    try:
        if any(action.split(':')[0] in consumers for action in sent):
            return 'stopped'
        for receipt in result.get('receipts', []):
            if receipt['entry'] in {'OneKeyDo_' + op for op in consumers} or any(
                    node.get('name') in {'OneKeyInput_' + op for op in consumers}
                    for node in receipt['native']['nodes']):
                return 'stopped'
        if 'journal_evidence' in result:
            proof = result['journal_evidence']
            if (proof['end']['outcome'] != 'already_complete'
                    or any(e['operation'].split(':')[0] in consumers for e in proof['actions'])):
                return 'stopped'
    except (KeyError, TypeError, AttributeError):
        return 'stopped'
    return ('complete' if result.get('schedule_executed') is False
            and result.get('one_key_attempted') is False
            and 'onekey' not in sent else 'stopped')


def _readonly_schedule_status(result, game_day, device, authorizations, invocation_id, gui_start_intent):
    """A current skip is never proof of old settlement or permission for a tail."""
    from .schedule_onekey import ITEMS
    from .schedule_house import completed
    try:
        def require(ok):
            if not ok:
                raise ValueError('readonly_result_invalid')
        require(gui_start_intent is True and result['outcome'] == 'current_complete_readonly'
                and not result.get('reason') and not result.get('error')
                and result['original_execution_unresolved'] is True
                and result['retry_permitted'] is False and result['settlement_verified'] is False
                and result['schedule_executed'] is False and result['one_key_attempted'] is False
                and result['daily_complete'] is False and 'journal_evidence' not in result
                and result['home_verified'] is True and result['nine_rows_verified'] is True
                and result['current_observation_verified'] is True
                and result['game_day'] == game_day and result['device'] == device
                and result['authorizations'] == list(authorizations)
                and result['before_rows'] == result['after_rows'] == dict.fromkeys(ITEMS, 'complete'))
        context = result['navigation_context']
        require(context['invocation_id'] == invocation_id and context['device'] == device
                and context['game_day'] == game_day
                and re.fullmatch('[0-9a-f]{32}', result['original_intent_id'])
                and re.fullmatch('run-[0-9a-f]{32}', result['original_run_id'])
                and result['original_run_id'] != invocation_id)
        resources = result['resources']
        require(resources['before'] == resources['after']
                and all(type(resources['before'][k]) is int and resources['before'][k] >= 0
                        for k in ('ap', 'jewels'))
                and resources['ap_delta'] == resources['jewels_delta'] == 0)
        sent = result['sent']
        require(sent[0] == 'enter' and sent[-1] == 'close' and len(sent) == len(set(sent))
                and len(sent) <= 26
                and all(re.fullmatch('(up|down):([0-9]|1[0-9]|2[0-3])', s) for s in sent[1:-1]))
        receipts = result['receipts']
        require(receipts[0]['reading']['state'] == receipts[-1]['reading']['state'] == 'home')
        index = 0
        dispatched = []
        catalog = {}
        anchored = False
        inventory_verified = False
        for receipt in receipts:
            entry, page = receipt['entry'], receipt['reading']
            require(entry in ('OneKeyExecRead', 'OneKeyDo_enter', 'OneKeyDo_up', 'OneKeyDo_down', 'OneKeyDo_close'))
            require(completed(receipt['native'], 'OneKeyExecSnapshot' if entry == 'OneKeyExecRead'
                              else entry.replace('Do_', 'Input_')))
            require(page['acquisition'] == 'current_tasker_capture' and page['session_id'] == context['session_id']
                    and type(page['capture_index']) is int and page['capture_index'] > index
                    and current_game_day(datetime.fromisoformat(page['captured_at'])) == game_day)
            index = page['capture_index']
            require(all(not n.get('name', '').startswith('OneKeyInput_') or n['name'] in
                    ('OneKeyInput_enter','OneKeyInput_up','OneKeyInput_down','OneKeyInput_close')
                    for n in receipt['native']['nodes']))
            if entry != 'OneKeyExecRead':
                dispatched.append(entry.removeprefix('OneKeyDo_'))
            if page['state'] == 'schedule':
                day = date.fromisoformat(game_day)
                require(page['date'] == [day.month, day.day])
                if 'delegated' in page['sections']:
                    require(page['sections']['delegated'] == dict(completed=9, total=9))
                    anchored = True
                if anchored:
                    for key, row in sorted(page['rows'].items(), key=lambda item: item[1]['y']):
                        require(key in ITEMS and row['state'] == 'complete')
                        catalog[key] = row['state']
                    if page['bottom']:
                        require(list(catalog) == list(ITEMS))
                        inventory_verified = True
        require(inventory_verified and dispatched == [s.split(':')[0] for s in sent])
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return 'stopped'
    return 'skipped_goal_met'


def _status(name, result, *, exit_page='home'):
    if name in ('deep', 'very_hard', 'hard', 'normal') and result == dict(outcome='skipped_disabled', consumption_sent=False, settlement_verified=False, daily_complete=False):
        return 'skipped_disabled'
    if result.get('consumption_sent') and result.get('settlement_verified') is not True:
        return 'stopped'
    fit = result.get('count_fit', {})
    if fit.get('attempted') and fit.get('restored') is not True:
        return 'stopped'
    boundary = result.get('boundary_readback') or {}
    preset = result.get('preset') or {}
    if not isinstance(boundary, dict) or not isinstance(preset, dict):
        return 'stopped'
    observed = boundary.get('observation') or {}
    if not isinstance(observed, dict) or not isinstance(boundary.get('ocr_receipt', {}), dict):
        return 'stopped'
    slot = preset.get('slot')
    preset_verified = (exit_page == 'preset' and name in PRESET_STEPS
        and result.get('exit_page') == 'preset' and result.get('home_verified') is False
        and result.get('boundary_page') == 'presets' and result.get('boundary_verified') is True
        and type(slot) is int and 1 <= slot <= 7
        and boundary.get('native_ok') is True and type(boundary.get('requested_slot')) is int
        and boundary.get('requested_slot') == slot
        and type(boundary.get('selected_slot')) is int and boundary.get('selected_slot') == slot
        and boundary.get('ocr_receipt', {}).get('native_ok') is True
        and observed.get('page') == 'presets' and type(observed.get('selected_slot')) is int
        and observed.get('selected_slot') == slot)
    if (result.get('home_verified') is not True and not preset_verified) or result.get('error'):
        return 'stopped'
    if name in REWARD_STEPS:
        items = result.get('items', {})
        if result.get('outcome') != 'complete' or set(items) != {'missions', 'gifts'}:
            return 'stopped'
        for kind, item in items.items():
            if (item.get('home_verified') is not True or item.get('status') not in
                    ('claimed', 'no_claimable', 'remaining', 'deferred_stamina') or
                    (item.get('claim_attempted') and item.get('result_verified') is not True)):
                return 'stopped'
            if item.get('status') == 'deferred_stamina' and (
                    kind != 'gifts' or item.get('stamina_deferred_verified') is not True
                    or item.get('claimable_after') is not False):
                return 'stopped'
        return 'partial' if any(i['status'] == 'remaining' for i in items.values()) else 'complete'
    outcome = result.get('outcome')
    if outcome == 'executed':
        if name == 'event' and result.get('goal_complete') is False and result.get('settlement_verified') is True:
            return 'partial'
        return 'executed' if result.get('settlement_verified') is True else 'stopped'
    if outcome in ('skipped_goal_met', 'skipped_insufficient_stamina', 'skipped_insufficient_tickets'):
        return outcome
    if (name in ('event', 'abyss') and outcome == 'skipped_event_unavailable'
            and result.get('consumption_sent') is False
            and result.get('settlement_verified') is False
            and result.get('goal_complete') is False
            and result.get('daily_complete') is False
            and result.get('reading', {}).get('availability') == 'closed'
            and result['reading'].get('closed_verified') is True):
        return outcome
    return 'stopped'


def execute_supported(tasker, config, *, game_day, context, directory, execute=False,
                      source='live supported chain', include_schedule_onekey=False,
                      schedule_authorizations=(), schedule_attest_current_plan=False,
                      selected_modules=None, rewards_final=_DEFAULT_REWARDS_FINAL,
                      schedule_confirm_new_cycle=False):
    if selected_modules is not None and 'guide_trial' in selected_modules:
        raise ValueError('guide_trial_requires_alpha_owner_entry')
    if selected_modules is not None and 'clan_battle_simulation' in selected_modules:
        raise ValueError('clan_battle_simulation_requires_alpha_owner_entry')
    if selected_modules is not None and 'clan_battle_daily' in selected_modules:
        raise ValueError('clan_battle_daily_requires_alpha_owner_entry')
    if type(schedule_confirm_new_cycle) is not bool or (schedule_confirm_new_cycle and
            (execute is not True or context != 'gui-current-state')):
        raise ValueError('new_cycle_confirmation_requires_current_gui_start')
    config = validate(config, execute=execute, game_day=game_day, context=context,
        include_schedule_onekey=include_schedule_onekey,
        schedule_authorizations=schedule_authorizations,
        schedule_attest_current_plan=schedule_attest_current_plan,
        selected_modules=selected_modules, rewards_final=rewards_final)
    plan = build_plan(selected_modules=selected_modules, rewards_final=rewards_final,
                      include_schedule_onekey=include_schedule_onekey)
    has_schedule = 'schedule_onekey' in plan
    if schedule_confirm_new_cycle and not has_schedule:
        raise ValueError('new_cycle_confirmation_requires_schedule')
    has_sweep = any(name in plan for name in ('event', 'abyss')) or any(
        p['kind'] != 'normal' and p['kind'] in plan and p['target_per_stage'] > 0 for p in config['presets'])
    last_wall = datetime.now(timezone.utc)
    rewards_only = not has_sweep and not has_schedule
    current_state = context == 'gui-current-state'
    if rewards_only or current_state:
        game_day = current_game_day(last_wall)
    if not has_sweep:
        context = ''
    if current_game_day(last_wall) != game_day:
        raise ValueError('current_game_day_required')
    device = None
    execution_target = None
    if current_state:
        from .schedule_quick import gui_device
        execution_target = gui_device(config)
    if has_schedule:
        if current_state:
            device = execution_target
        else:
            from .onekey_history import schedule_device
            device = schedule_device(config)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    elapsed_start = time.monotonic()
    from .labyrinth.timing import Timings, NativeTimings
    timings = Timings()
    native_timings = None
    timing_collection = 'unavailable'
    report = dict(control_version=1, run_id=directory.name,
        schedule_attest_current_plan=schedule_attest_current_plan,
        schedule_gui_start_intent=current_state and has_schedule,
        schedule_confirm_new_cycle=schedule_confirm_new_cycle,
        scope=SCHEDULE_NOTICE if has_schedule else NOTICE,
        source=source, game_day=game_day, count_context=context,
        game_day_source='system-derived' if rewards_only or current_state else 'user-reported',
        count_context_source=('current-game-observation' if current_state else 'user-reported') if has_sweep else 'not-applicable',
        daily_complete=False, supported_chain_complete=False,
        unsupported=(dict(abyss_boss='unsupported') if has_schedule else
                     dict(schedule='unsupported', abyss_boss='unsupported')), outcome='running',
        started_at=time.time(), steps=[dict(name=n, status='not_run') for n in plan])
    for index, step in enumerate(report['steps']):
        if step['name'] in PRESET_STEPS:
            step['exit_page'] = _preset_exit_page(plan, index, config)
    if current_state:
        report.update(execution_target=execution_target,
                      execution_target_scope='current-owner-target')
    if selected_modules is not None:
        report.update(selection_version=1, selected_modules=[n for n in plan if n != 'rewards_final'],
                      rewards_final='rewards_final' in plan,
                      scope='【仅运行本次显式选集，按玩家排列顺序；收尾领奖独立选择】\n'
                            '深渊仅支持前哨；首领、购买重置和次数调整：unsupported。完整日常未交付，daily_complete=false。')
    def boundary_error():
        nonlocal last_wall
        now = datetime.now(timezone.utc)
        reason = ('wall_clock_rollback_need_new_observation' if now < last_wall else
                  'game_day_changed_need_new_observation' if current_game_day(now) != game_day else None)
        last_wall = now
        return reason

    def save():
        _atomic_json(directory / 'report.json', report)

    with _control_lock(directory):
        if _plain_file(directory / 'report.json', optional=True) is not None:
            raise ValueError('existing_report_cannot_restart')
        save()
    try:
        try:
            controller = getattr(tasker, 'controller', None)
            if controller is not None and callable(getattr(tasker, 'add_context_sink', None)):
                from . import native
                native_timings = NativeTimings(tasker, controller, timings,
                    trace_path=native.RUNTIME / 'maa/maafw.log', measure_page_wait=True,
                    collect_input_evidence=False)
                timing_collection = 'attached'
        except Exception:
            timing_collection = 'unavailable'
        for index, step in enumerate(report['steps']):
            reason = boundary_error()
            if reason:
                report.update(outcome='stopped', error=reason)
                break
            with _control_lock(directory):
                if _stop_requested(directory):
                    report.update(outcome='stopped_by_request', stop_acknowledged=True,
                                  stopped_before=step['name'], finished_at=time.time())
                    save()
                    break
                step.update(status='running', started_at=time.time())
                save()
            step_elapsed_start = time.monotonic()
            try:
                result = (_run_schedule(tasker, game_day, device, schedule_authorizations,
                                        schedule_attest_current_plan, directory.name,
                                        **(dict(gui_start_intent=True) if current_state else {}),
                                        **(dict(confirm_new_cycle=True) if schedule_confirm_new_cycle else {}))
                          if step['name'] == 'schedule_onekey' else
                          _run_step(tasker, step['name'], config, game_day, context,
                                    **(dict(exit_page='preset') if step.get('exit_page') == 'preset' else {})))
                step['result'] = result
                step['status'] = (_schedule_status(result, game_day, device, schedule_authorizations,
                                                   schedule_attest_current_plan, directory.name, current_state)
                                  if step['name'] == 'schedule_onekey' else
                                  _status(step['name'], result, exit_page=step.get('exit_page', 'home')))
                reason = boundary_error()
                if reason:
                    step.update(status='stopped', error=reason)
            except (Exception, KeyboardInterrupt) as exc:
                step.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                            error=str(exc) or '用户中断')
            finally:
                step['finished_at'] = time.time()
                step['elapsed_seconds'] = time.monotonic() - step_elapsed_start
                path = directory / f'{index + 1}-{step["name"]}.json'
                step['receipt'] = str(path)
                _atomic_json(path, step)
                with _control_lock(directory):
                    save()
            if step['status'] in ('stopped', 'failed', 'interrupted'):
                report['outcome'] = step['status']
                break
            if step.get('result', {}).get('original_execution_unresolved'):
                report['original_execution_unresolved'] = True
                if step['status'] != 'skipped_goal_met' and index + 1 < len(report['steps']):
                    report.update(outcome='stopped', error='unresolved_schedule_blocks_other_modules')
                    break
        else:
            partial = any(s['status'] == 'partial' for s in report['steps'])
            report.update(outcome='partial' if partial else 'complete', supported_chain_complete=not partial)
    except (Exception, KeyboardInterrupt) as exc:
        report.update(outcome='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                      error='control_or_report_io_failed', supported_chain_complete=False,
                      stop_acknowledged=False)
        for step in report['steps']:
            if step['status'] == 'running':
                step.update(status=report['outcome'], finished_at=time.time(), error='control_or_report_io_failed')
    finally:
        if native_timings is not None:
            try:
                native_timings.finish_native_trace()
                timing_collection = 'collected'
            except Exception:
                timing_collection = 'incomplete'
            finally:
                try:native_timings.close()
                except Exception:timing_collection = 'incomplete'
        report['finished_at'] = time.time()
        elapsed = time.monotonic() - elapsed_start
        module_elapsed = sum(step.get('elapsed_seconds', 0) for step in report['steps'])
        report['performance'] = dict(clock='monotonic', elapsed_seconds=elapsed,
            module_elapsed_seconds=module_elapsed, orchestration_seconds=max(0, elapsed - module_elapsed),
            module_seconds={step['name']: step['elapsed_seconds'] for step in report['steps']
                            if 'elapsed_seconds' in step},
            native_timings=timings.payload(), native_timing_collection=timing_collection,
            totals_policy='module durations are exclusive; page_wait includes screenshot, recognition and input; swipe is an input subset; nested durations are not added')
        with _control_lock(directory):
            save()
    return report


def summary(report, *, player=False):
    """Share module facts while keeping CLI diagnostics out of the player heading."""
    if report.get('selected_modules') == ['clan_battle_daily']:
        result = report['steps'][0].get('result', {})
        reason = str(result.get('error') or '')
        if reason.startswith('clan_battle_daily_calibration_missing') or reason == 'production_daily_adapter_unavailable':
            return '正式三刀：会战页面与消费读回尚未校准，当前不可执行。未发出正式或补偿开战，实际剩余次数与尾刀库存仍未知。'
        finished = result.get('regular_settled') == 3 and result.get('remaining_count') == 0
        if result.get('outcome') == 'remind_carryover' and finished and result.get('held_carryovers'):
            return f"正式三刀：3 次主刀均已结算，普通剩余次数为 0。仍有 {len(result['held_carryovers'])} 份尾刀未出，已保留；目标可用后可再次 Start 核对接续。"
        if result.get('outcome') == 'no_available_target':
            slots = '、'.join(str(row['slot']) for row in result.get('held_regular', []) if type(row) is dict and type(row.get('slot')) is int)
            return '正式三刀：本轮没有可执行目标，已结束。' + (f'第 {slots} 刀目标已保留。' if slots else '') + '不会自动更换 BOSS、队伍或重发。'
        if result.get('outcome') == 'complete' and finished:
            return '正式三刀：本批已核实完成，记录已保存。'
        return '正式三刀：已停止。未核实的次数、结果或补偿不能视为完成；请保留记录，不要删账或重发。' + ('\n原因：' + reason if reason else '')
    if report.get('selected_modules') == ['clan_battle_simulation']:
        result = report['steps'][0].get('result', {})
        target = f"{result.get('phase', '')} 面 {result.get('boss', '?')} 号 BOSS"
        if result.get('outcome') == 'simulated' and result.get('damage_verified') is True:
            returned = ('已返回会战目标页。' if result.get('return_verified') is True
                else '返回会战目标页尚未核实，请手动核对页面；已知伤害记录保留，不会重开模拟。')
            return f"公会战模拟：{target} 已完成。实际伤害 {result['damage']:,}。{returned}本次只执行一局，未正式出刀。"
        if result.get('outcome') == 'unavailable':
            return '公会战模拟：当前未开放，未发出开战。已保留所选作业。'
        started = '本次模拟开战结果尚未确认，已保留未决记录，请勿直接重开。' if result.get('simulation_started') else '本次没有确认完成模拟战。'
        error = result.get('error')
        reason = str(error) if error else ''
        reasons = {
            'clan_battle_calibration_missing': '会战页面尚未完成真实校准，当前不可执行。',
            'clan_battle_timing_not_calibrated': '尚未核定秒数读取和操作耗时，此切换轴不可执行。',
            'clan_battle_timing_window_unproven': '同一秒内的全部操作和读回无法确认完成，此切换轴不可执行。',
            'clan_battle_previous_result_unknown': '上次模拟结果仍未决，已阻止再次开战。',
            'clan_battle_switch_missed': '已错过目标秒数，停止且不补点。',
            'clan_battle_controls_unknown': 'SET／AUTO 状态不明，停止且不补点。',
            'clan_battle_disconnected_6002': '检测到 6002 断线，请先手动重连并核对结果；程序不会补点或重开。',
        }
        for code, message in reasons.items():
            if reason == code or reason.startswith(code + ':'):
                reason = message
                break
        return '公会战模拟：已停止。' + started + ('\n原因：' + reason if reason else '')
    if report.get('selected_modules') == ['guide_trial']:
        result = report['steps'][0].get('result', {})
        label = {'victory': '已通关', 'already_cleared': '已有通关记录，未重打',
                 'defeat': '本次未通关，已停止，不自动重试',
                 'control_checked_exit': '开场设置核对完成并已退出，未记通关',
                 'progress_observed': '账号和地图已读取，旧战斗结果仍未决',
                 'completed_externally': '地图已完成，原战斗结果保留未知，未重打',
                 'attempt_limit': '已用完三次尝试，停止', 'stopped': '已停止'}.get(result.get('outcome'), '结果未知，停止')
        scope = '本次授权已尝试' if result.get('authorization_id') else '累计尝试'
        history = f"旧队伍 {result['historical_attempts']} 次记录保留。" if result.get('historical_attempts') else ''
        returned = ('已返回地图。' if result.get('stage_return_verified') else '返回地图尚未确认。') if result.get('outcome') in ('victory', 'defeat', 'already_cleared') else ''
        error = result.get('error') or ''
        if error.startswith('guide_trial_disconnected_6002:battle_start='):
            started = {'not_sent': '本次未发出开战。', 'sent': '本次已发出开战，战斗结果仍以原记录为准。',
                       'unknown': '本次开战是否发出无法确认，保留原未决。',
                       'previously_sent': '正在处理原战斗，本次未新增开战。'}.get(error.rsplit('=', 1)[-1], '开战状态无法确认。')
            error = '游戏已断线（6002），已停止。' + started + '请手动重连；本次不会自动登录或重发。\n诊断信息：' + error
        return f"{result.get('stage', '所选关卡')}：{label}。{returned}{scope} {result.get('attempts_used', 0)}/3。{history}" + (
            '\n原因：' + error if error else '')
    labels = dict(schedule_onekey='日程一键', event='活动', abyss='深渊前哨', deep='深域',
                  rewards='每日任务/礼物（早段）', rewards_final='每日任务/礼物（收尾）',
                  very_hard='高难', hard='困难', normal='普通（每关本次次数）')
    states = dict(executed='已执行并结算', complete='本步完成', partial=('仍有奖励未领取' if player else '部分领取，仍有 remaining'),
        skipped_disabled='0 次，不执行', skipped_goal_met='已达目标', skipped_insufficient_stamina='体力不足，安全跳过（日目标未满）',
        skipped_insufficient_tickets='券不足，安全跳过（日目标未满）', not_run='未运行',
        skipped_event_unavailable='限时活动未开放，已安全跳过',
        stopped='停止，结果或收尾未验证', failed='异常停止', interrupted='中断',
        running='报告记为运行中，进程存活未知')
    def detail(step):
        result = step.get('result', {})
        if (step['name'] in REWARD_STEPS and step['status'] == 'complete'
                and result.get('items', {}).get('gifts', {}).get('status') == 'deferred_stamina'):
            if result.get('collect_stamina') is False:
                return '；已按设置保留体力礼物，继续后续任务。'
            return '；体力礼物暂不可领取，已取消并继续后续任务。'
        if result.get('mode') == 'gui-onekey':
            reason = result.get('reason') or result.get('error') or 'unknown'
            if result.get('outcome') == 'unavailable' and step['status'] == 'complete':
                return '；一键自动不可用，已跳过并返回主页。'
            if step['status'] in ('stopped', 'failed', 'interrupted'):
                if (reason in ('fixed_device_display_required', 'schedule_stable_instance_identity_required')
                        or str(reason).startswith('schedule_manager_')):
                    return '\n日程在设备身份核验阶段停止，尚未进入日程或点击一键自动。\n诊断信息：' + str(reason)
                message = ('日程流程未能确认结束，已停止后续任务。请核对游戏当前画面并保留日志。'
                           if result.get('one_key_attempted') else
                           '未能确认日程的一键自动按钮状态，本次未点击一键自动。请核对游戏当前画面。')
                return '\n' + message + '\n诊断信息：' + str(result.get('reason') or result.get('error') or 'unknown')
        if result.get('original_execution_unresolved') and step['status'] == 'skipped_goal_met':
            return '；本次只读核验九项已完成并返回主页，无新消费。原执行仍未决，未结案，不授予重试；其他模块不运行。'
        reason = result.get('reason') or result.get('error') or step.get('error') or (result.get('outcome') if player else None)
        if step['status'] not in ('stopped', 'failed', 'interrupted'):
            return ''
        if step['name'] != 'schedule_onekey':
            messages = []
            if player:
                message = {
                    'count_fit_mixed_gap': '各关卡距离每日目标的差额不同，无法用整组次数完成本次目标。请在游戏内核对各关卡进度。',
                    'count_fit_exceeds_available': '每日目标超过所选关卡当前可执行次数。请降低该模块的每日目标。',
                    'count_fit_original_unknown': '未能读取游戏内当前的扫荡次数。请核对预设页面和次数显示。',
                }.get(reason)
                if message:
                    messages.append(message)
            items = result.get('items')
            if step['name'] in REWARD_STEPS and isinstance(items, dict):
                for key, label in (('missions', '每日任务'), ('gifts', '礼物')):
                    item = items.get(key)
                    if not isinstance(item, dict):
                        continue
                    if item.get('claim_attempted') is False:
                        messages.append(label + '：未尝试领取。')
                    elif item.get('claim_attempted') is True:
                        if item.get('result_verified') is False:
                            message = '已尝试领取，结果尚未确认。'
                        elif item.get('result_verified') is True:
                            message = ('领取结果已确认，返回主页尚未确认。'
                                       if item.get('home_verified') is False else '领取结果已确认。')
                        else:
                            message = '已尝试领取。'
                        messages.append(label + '：' + message)
            if result.get('consumption_sent') is True and result.get('settlement_verified') is not True:
                messages.append('本步已尝试发送消费，结算结果未确认。')
            elif result.get('consumption_sent') is False:
                messages.append('本步未发送消费操作。')
            fit = result.get('count_fit')
            if isinstance(fit, dict) and fit.get('attempted') is True and fit.get('restored') is not True:
                messages.append('本次次数调整尚未确认恢复；请保留当前设置和记录，交维护端核对原次数。')
            messages.append('请核对游戏当前画面并保留本次记录；结果未确认时不要直接重跑、删账或换日，请交维护端核查。')
            if player:
                return '\n' + '\n'.join(messages + (['诊断信息：' + str(reason)] if reason else []))
            return ('；原因：' + str(reason) if reason else '') + '\n' + '\n'.join(messages)
        recovery = result.get('recovery', {})
        message = recovery.get('message')
        if reason == 'new_cycle_refresh_confirmation_required':
            message = ('旧日程结果仍未知，本次仅重新观察，未发送消费。请先在游戏中重新登录并确认今日任务已刷新，'
                       '再展开“日程恢复”，勾选仅本次确认后开始。程序仍会核对游戏日期、九项状态和实际报价；旧记录保留。')
        if (reason == 'unresolved_trusted_same_day_required'
                and result.get('one_key_attempted') is False
                and result.get('consumer_state') == 'not_started'):
            message = ('本次未发送一键自动或消费确认。跨日恢复时未能确认游戏日程日期，旧结果仍保留为未知。'
                       '请重新登录游戏，确认今日任务刷新并回到主页，再勾选“日程恢复”后开始；'
                       '若仍出现此提示，请导出诊断，不需要删除记录。')
        if not message and reason == 'unsettled_intent_any_day_no_resend':
            message = ('本次未发送日程输入；上次日程仍有未决记录，禁止直接重跑。'
                       '重开软件或换日不会解锁，请交维护端做只读恢复检查；不要删账。')
        sent = result.get('sent')
        if (not message and result.get('one_key_attempted') is False
                and result.get('consumer_state') == 'not_started'
                and result.get('original_execution_unresolved') is not True
                and isinstance(sent, list) and all(isinstance(action, str)
                    and action.split(':')[0] in ('enter', 'up', 'down', 'close') for action in sent)):
            message = ('本次仅尝试日程导航，未发送一键自动或消费确认。' if sent else
                       '本次未发送一键自动或消费确认。') + '请核对游戏当前画面后再开始。'
        if not message and sent:
            message = '本次已尝试发送日程输入，结果未知时禁止直接重跑；请交维护端做只读恢复检查。'
        if player:
            return ('\n' + message if message else '') + ('\n诊断信息：' + str(reason) if reason else '')
        return ('；原因：' + str(reason) if reason else '') + ('\n' + message if message else '')
    report_detail = []
    if report.get('error'):
        message = {
            'game_day_changed_need_new_observation': '游戏日标记已变化，需要重新观察游戏当前状态；电脑日期不证明游戏已刷新。',
            'wall_clock_rollback_need_new_observation': '电脑时间出现回退，需要核对时间并重新观察游戏当前状态。',
        }.get(report['error'], '本次已停止，请保留日志和记录交维护端核查。')
        report_detail = ['停止原因：' + str(report['error']), message,
                         '结果未确认时不要直接重跑、删账或换日。']
    heading = report.get('scope', NOTICE)
    footer = ['supported_chain_complete=' + str(report.get('supported_chain_complete', False)).lower(),
              '完整日常未完成；daily_complete=false。']
    if player:
        heading = dict(partial='本次所选任务仍有未完成项。', stopped='本次任务已停止。',
            failed='本次任务异常停止。', interrupted='本次任务已中断。',
            stopped_by_request='本次任务已按请求停止。',
            running='记录显示任务仍在运行，请确认程序当前状态。').get(
                report.get('outcome'), '本次任务结果尚未确认。')
        if report.get('outcome') == 'complete' and report.get('supported_chain_complete') is True:
            heading = '本次所选任务已结束。'
        footer = []
        if report_detail:
            report_detail = [message, '结果未确认时不要直接重跑、删账或换日。',
                             '诊断信息：' + str(report['error'])]
    return '\n'.join([heading, *[labels[s['name']] + '：' +
        ('未找到已选关卡或列表未识别，请先在游戏核对勾选（未提交）'
         if s['name'] == 'normal' and s.get('result', {}).get('outcome') == 'empty_or_unreadable_selection'
         else '已执行并结算，日目标未满' if s['name'] == 'event' and s['status'] == 'partial'
         else '深渊前哨未开放，已安全跳过' if s['name'] == 'abyss' and s['status'] == 'skipped_event_unavailable'
         else states.get(s['status'], s['status'])) + detail(s)
        for s in report.get('steps', [])], *report_detail,
        *(['已在步骤边界响应停止请求，后续未运行。'] if report.get('outcome') == 'stopped_by_request' else []),
        *footer])
