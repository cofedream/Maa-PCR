"""Supported single-stage guide trials; the existing Owner supplies all live authority."""
from copy import deepcopy
import re
import time
from uuid import uuid4
from .guide_identity import SOURCE_HASHES

TEAM = (129701, 116701, 134701, 135501, 104701)
STARS = (5, 5, 5, 5, 6)
STAGE = '红焰深域 7-3'
LIMIT = 3


def is_user_exit_history(event, intent):
    confirmation = event.get('confirmation')
    evidence = event.get('evidence')
    return (intent.get('state') == 'in_flight' and intent.get('operation') == 'guide_trial'
        and intent.get('stage') == STAGE and type(intent.get('attempt')) is int
        and 1 <= intent['attempt'] <= LIMIT
        and type(intent.get('account_id')) is str and re.fullmatch('[1-9][0-9]{4,19}', intent['account_id'])
        and type(intent.get('intent_id')) is str and re.fullmatch('[0-9a-f]{32}', intent['intent_id'])
        and event.get('state') == 'historical_unknown'
        and event.get('recovery_scope') == 'guide_trial_user_exit'
        and event.get('resolution') == 'user_confirmed_exited'
        and event.get('original_result_unknown') is True and event.get('replay_forbidden') is True
        and event.get('outcome') == 'user_exited' and event.get('result_verified') is False
        and all(event.get(key) == value for key, value in intent.items() if key != 'state')
        and type(confirmation) is dict and confirmation.get('source') == 'direct_user_message'
        and confirmation.get('exited') is True and confirmation.get('continue_remaining') is True
        and type(confirmation.get('text')) is str and 0 < len(confirmation['text'].strip()) <= 2000
        and type(evidence) is dict and evidence.get('scene') == 'GTFireMap'
        and evidence.get('source') == 'maa_observation' and evidence.get('verified') is True
        and evidence.get('account_id') == intent.get('account_id')
        and type(evidence.get('observed_at')) in (float, int)
        and type(intent.get('at')) in (float, int) and evidence['observed_at'] >= intent['at']
        and type(event.get('resolved_at')) in (float, int) and event['resolved_at'] >= evidence['observed_at']
        and all(type(evidence.get(key)) is str and re.fullmatch('[0-9a-f]{64}', evidence[key])
                for key in ('frame_sha256', 'receipt_sha256')))


def record_user_exit(journal, *, intent_id, confirmation, evidence):
    """Explicit maintenance only: preserve an exited attempt, never invent its result."""
    with journal.locked():
        events = journal.events()
        if not events or len(events) % 2 != 1 or events[-1].get('intent_id') != intent_id:
            raise ValueError('guide_trial_exit_target_not_pending')
        intent = events[-1]
        event = dict(intent, state='historical_unknown', recovery_scope='guide_trial_user_exit',
            resolution='user_confirmed_exited', original_result_unknown=True, replay_forbidden=True,
            outcome='user_exited', result_verified=False, resolved_at=time.time(),
            confirmation=deepcopy(confirmation), evidence=deepcopy(evidence))
        if not is_user_exit_history(event, intent):
            raise ValueError('guide_trial_exit_evidence_invalid')
        journal.append(event)
        return event


def correct_team(unit_ids, expected=TEAM):
    return (type(unit_ids) is list and len(unit_ids) == 5 and all(type(i) is int for i in unit_ids)
        and len(set(unit_ids)) == 5 and set(unit_ids) == set(expected))


def planned_sets(plan):
    return {str(uid): True for uid in plan['unit_ids']} if plan['version'] == 2 else deepcopy(plan['set_by_unit'])


def settings_plan(plan):
    return dict(all_set=True, auto=True) if plan['version'] == 2 else dict(set_by_unit=planned_sets(plan), auto=True)


def team_matches(reading, plan):
    ids, stars = reading.get('unit_ids'), reading.get('stars')
    return (correct_team(ids, plan['unit_ids']) and type(stars) is list and len(stars) == 5
        and all(type(star) is int for star in stars) and dict(zip(ids, stars)) == dict(zip(plan['unit_ids'], plan['stars'])))


def validate_plan(plan):
    keys = {'version', 'server', 'stage', 'account_id', 'unit_ids', 'stars', 'auto', 'max_attempts', 'source_hash', 'source_image_sha256'}
    if type(plan) is not dict or type(plan.get('version')) is not int or plan['version'] not in (2, 3):
        raise ValueError('guide_trial_plan_invalid')
    keys |= {'all_set'} if plan['version'] == 2 else {'set_by_unit', 'source_material_sha256', 'source_confirmations'}
    if (set(plan) != keys
            or type(plan['stage']) is not str or plan['stage'] not in SOURCE_HASHES
            or plan['source_image_sha256'] != SOURCE_HASHES[plan['stage']]
            or plan['server'] != 'cn-bilibili'
            or type(plan['account_id']) is not str or re.fullmatch(r'[1-9][0-9]{4,19}', plan['account_id']) is None
            or not correct_team(plan['unit_ids'], plan['unit_ids']) or any(i <= 0 for i in plan['unit_ids'])
            or type(plan['stars']) is not list or len(plan['stars']) != 5
            or any(type(i) is not int or not 1 <= i <= 6 for i in plan['stars'])
            or plan['auto'] is not True
            or type(plan['max_attempts']) is not int or plan['max_attempts'] != LIMIT
            or type(plan['source_hash']) is not str or re.fullmatch(r'[0-9a-fA-F]{64}', plan['source_hash']) is None):
        raise ValueError('guide_trial_plan_invalid')
    if plan['version'] == 2:
        if (tuple(plan['unit_ids']) != TEAM or tuple(plan['stars']) != STARS or plan['all_set'] is not True
                or plan['stage'] in ('红焰深域 8-3', '红焰深域 8-4', '红焰深域 8-5', '红焰深域 8-6', '红焰深域 8-8')):
            raise ValueError('guide_trial_plan_invalid')
    elif (type(plan['set_by_unit']) is not dict or set(plan['set_by_unit']) != {str(i) for i in plan['unit_ids']}
            or any(type(value) is not bool for value in plan['set_by_unit'].values())
            or type(plan['source_material_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', plan['source_material_sha256'])
            or type(plan['source_confirmations']) is not list or len(plan['source_confirmations']) > 5
            or any(type(c) is not dict or set(c) != {'unit_id','icon_unit_id','binding'} or type(c['unit_id']) is not int
                or type(c['icon_unit_id']) is not int or type(c['binding']) is not str or not re.fullmatch('[0-9a-f]{64}',c['binding']) for c in plan['source_confirmations'])):
        raise ValueError('guide_trial_plan_invalid')
    return deepcopy(plan)


def execute_trial(port, journal, plan):
    """Reserve before start; never retry an unresolved start, even after restart."""
    plan = validate_plan(plan)
    stage = plan['stage']
    report = dict(outcome='stopped', stage=stage, attempts_used=0, attempts_limit=LIMIT,
                  consumption_sent=False, settlement_verified=False, home_verified=False,
                  daily_complete=False, battles=[])
    with journal.locked():
        events = journal.events()
        # One fixed ledger, independent of plan hash, day, process or selected team.
        if any(e.get('operation') != 'guide_trial' or e.get('stage') not in SOURCE_HASHES for e in events):
            raise ValueError('guide_trial_ledger_invalid')
        stage_events = [e for e in events if e.get('stage') == stage]
        starts = [e for e in stage_events[::2] if e.get('account_id') == plan['account_id']]
        from .guide_trial_authorization import read_authorization
        grant = read_authorization(journal, stage_events, plan) if stage == STAGE else None
        prior = grant['historical_attempts'] if grant else 0
        report['attempts_used'] = len(starts) - prior
        report['historical_attempts'] = prior
        report['attempts_total'] = len(starts)
        if grant:
            report['authorization_id'] = grant['authorization_id']
        if [e.get('attempt') for e in starts] != list(range(1, len(starts) + 1)) or not prior <= len(starts) <= prior + LIMIT:
            raise ValueError('guide_trial_ledger_attempts_invalid')
        cleared = None
        progressed = None
        from .guide_trial_progress import is_progress_history, stage_order
        from .guide_trial_reconcile import is_not_started, SCOPE as NOT_STARTED_SCOPE
        from .guide_trial_control_check import is_control_check_exit, SCOPE as CONTROL_CHECK_SCOPE
        for index, (intent, result) in enumerate(zip(events[::2], events[1::2])):
            if any(result.get(k) != intent.get(k) for k in ('intent_id', 'account_id', 'stage', 'attempt')):
                raise ValueError('guide_trial_ledger_binding_invalid')
            if is_user_exit_history(result, intent):
                continue
            if result.get('recovery_scope') == CONTROL_CHECK_SCOPE:
                if not is_control_check_exit(result, intent):
                    report.update(error='guide_trial_previous_result_unknown')
                    return report
                continue
            if result.get('recovery_scope') == NOT_STARTED_SCOPE:
                prefix = b''.join(journal.path.read_bytes().splitlines(keepends=True)[:2*index+1])
                if not is_not_started(result, intent, prefix=prefix):
                    report.update(error='guide_trial_previous_result_unknown')
                    return report
                continue
            if is_progress_history(result, intent):
                if (intent['account_id'] == plan['account_id']
                        and stage_order(stage) <= stage_order(result['evidence']['map_review']['completed_stage'])):
                    progressed = result
                continue
            if result.get('outcome') not in ('victory', 'defeat') or result.get('result_verified') is not True:
                report.update(error='guide_trial_previous_result_unknown')
                return report
            if intent['account_id'] == plan['account_id'] and intent['stage'] == stage and result['outcome'] == 'victory':
                cleared = result
        pending = events[-1] if len(events) % 2 else None
        resume = None
        recovery = None
        control_exit = None
        if pending:
            from .guide_trial_progress import claim_progress_observation
            observation = claim_progress_observation(journal, events, plan)
            if observation:
                try:
                    report['progress_observation'] = port.observe_progress(plan)
                    report.update(outcome='progress_observed', observed_intent_id=pending['intent_id'],
                                  original_result_unknown=True, settings_verified=False)
                except Exception as exc:
                    report['error'] = str(exc) or type(exc).__name__
                return report  # Observation never grants a battle or settles the pending intent.
            from .guide_trial_control_check import claim_control_exit
            try:
                control_exit = claim_control_exit(journal,events,plan)
            except Exception as exc:
                report['error'] = str(exc) or type(exc).__name__
                return report
            from .guide_trial_resume import claim_resume, claim_result_recovery
            recovery = claim_result_recovery(journal, events, plan) if not control_exit else None
            resume = claim_resume(journal, events, plan) if recovery is None and not control_exit and stage == STAGE and pending['stage'] == stage else None
            if resume is None and recovery is None and control_exit is None:
                report.update(error='guide_trial_previous_result_unknown')
                if (pending.get('control_check') and pending.get('source_hash') == plan['source_hash']
                        and pending.get('stage') == stage and pending.get('account_id') == plan['account_id']):
                    try:
                        report['control_check_observation'] = port.observe_control_check(plan)
                    except Exception as exc:
                        report['observation_error'] = str(exc) or type(exc).__name__
                return report
        if progressed and not cleared:
            report.update(outcome='completed_externally', original_result_unknown=True,
                          settings_verified=False, progress_evidence=progressed['evidence'])
            return report
        if cleared:
            report.update(outcome='already_cleared', settlement_verified=True)
            from .guide_trial_resume import claim_completed_return
            if claim_completed_return(journal, cleared, plan):
                try:
                    port.finish_completed_return(plan)
                    report['stage_return_verified'] = True
                except Exception as exc:
                    report['error'] = str(exc) or type(exc).__name__
            return report
        if not pending and report['attempts_used'] >= LIMIT:
            report.update(outcome='attempt_limit')
            return report
        try:
            from .guide_identity import require_source_team
            require_source_team(plan)
            from .guide_trial_control_check import claim_control_check, result_matches, OUTCOME as CONTROL_CHECK_OUTCOME
            control_check = claim_control_check(journal, plan) if not pending else None
            if control_exit:
                result = port.recover_control_exit(plan,control_exit)
                if not result_matches(result,pending['control_check'],pending['team_evidence']):
                    raise RuntimeError('guide_control_check_result_unknown')
                journal.append(dict(pending,state='settled',outcome=CONTROL_CHECK_OUTCOME,
                    result_verified=False,result_evidence=result,recovery_scope=CONTROL_CHECK_SCOPE,at=time.time()))
                report['battles'].append(dict(attempt=pending['attempt'],**result))
                report.update(outcome=CONTROL_CHECK_OUTCOME,control_check_verified=True,exit_verified=True,
                    stage_return_verified=True,recovered_intent_id=pending['intent_id'],opening_evidence_reused=True)
                return report
            if recovery:
                outcome = recovery.get('outcome', 'victory')
                result = port.recover_victory(plan, recovery) if outcome == 'victory' else port.recover_result(plan, recovery)
                if (type(result) is not dict or result.get('outcome') != outcome
                        or result.get('result_verified') is not True):
                    raise RuntimeError('guide_trial_result_unknown')
                journal.append(dict(pending, state='settled', outcome=outcome, result_verified=True,
                    result_evidence=result, recovery_scope='guide_trial_same_'+outcome, at=time.time()))
                report.update(outcome=outcome, settlement_verified=True,
                              recovered_intent_id=pending['intent_id'])
                report['battles'].append(dict(attempt=report['attempts_used'], **result))
                port.return_to_stage()
                report['stage_return_verified'] = True
                return report
            if pending:
                port.prepare_resume(plan, resume)
            else:
                port.prepare(plan)
            while pending or report['attempts_used'] < LIMIT:
                port.checkpoint()
                if pending:
                    intent, pending = pending, None
                    attempt = report['attempts_used']
                    report['consumption_sent'] = True
                    report['resumed_intent_id'] = intent['intent_id']
                    result = port.resume_and_read(plan)
                else:
                    ready = port.verify_team(plan)
                    if (type(ready) is not dict or ready.get('stage') != stage
                            or ready.get('account_id') != plan['account_id']
                            or not team_matches(ready, plan)
                            or ready.get('settings_plan') != settings_plan(plan)):
                        raise RuntimeError('guide_trial_team_or_settings_unknown')
                    port.checkpoint()
                    attempt = report['attempts_used'] + 1
                    intent = dict(state='in_flight', operation='guide_trial', intent_id=uuid4().hex,
                        account_id=plan['account_id'], stage=stage, attempt=prior + attempt,
                        source_hash=plan['source_hash'], at=time.time(), team_evidence=ready)
                    if grant:
                        intent.update(authorization_id=grant['authorization_id'], authorization_attempt=attempt)
                    if control_check:
                        intent['control_check'] = control_check
                    journal.append(intent)
                    report['attempts_used'] = attempt
                    report['attempts_total'] = prior + attempt
                    report['consumption_sent'] = True  # Conservatively unresolved from this point.
                    result = port.start_control_check(plan, control_check) if control_check else port.start_and_read(plan)
                if control_check:
                    if not result_matches(result, control_check, intent['team_evidence']):
                        raise RuntimeError('guide_control_check_result_unknown')
                    journal.append(dict(intent, state='settled', outcome=CONTROL_CHECK_OUTCOME,
                        result_verified=False, result_evidence=result, recovery_scope=CONTROL_CHECK_SCOPE, at=time.time()))
                    report['battles'].append(dict(attempt=attempt, **result))
                    report.update(outcome=CONTROL_CHECK_OUTCOME, control_check_verified=True,
                        exit_verified=True, stage_return_verified=True, settlement_verified=False)
                    return report
                if (type(result) is not dict or result.get('result_verified') is not True
                        or result.get('outcome') not in ('victory', 'defeat')):
                    raise RuntimeError('guide_trial_result_unknown')
                if plan['version'] == 3 and (result.get('settings_verified') is not True or result.get('auto') is not True
                        or result.get('set_by_unit') != planned_sets(plan)):
                    raise RuntimeError('guide_trial_settings_result_unknown')
                journal.append(dict(intent, state='settled', outcome=result['outcome'],
                    result_verified=True, result_evidence=result, at=time.time()))
                report['battles'].append(dict(attempt=attempt, **result))
                report['settlement_verified'] = True
                report['outcome'] = result['outcome']
                break  # One explicit Start runs one battle; the journal keeps the cumulative limit.
            port.return_to_stage()
            report['stage_return_verified'] = True
        except Exception as exc:
            report['error'] = str(exc) or type(exc).__name__
            if report['outcome'] not in ('victory', 'defeat', 'attempt_limit'):
                report['outcome'] = 'stopped'
            if len(journal.events()) % 2:
                report['settlement_verified'] = False
        return report


def run_owned(tasker, config, directory):
    from pathlib import Path
    from .native import ROOT
    from .preset_execution import Journal
    from .supported_daily import _atomic_json
    from .guide_trial_native import NativeGuideTrial
    plan = validate_plan(config.get('guide_trial'))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = execute_trial(NativeGuideTrial(tasker, directory),
        Journal(ROOT / '.runtime/guide-trial-work/ledger'), plan)
    complete = (result['outcome'] in ('victory', 'already_cleared', 'attempt_limit', 'control_checked_exit')
        or result['outcome'] == 'defeat' and result.get('settlement_verified') is True and result.get('stage_return_verified') is True) and not result.get('error')
    report = dict(outcome='complete' if complete else 'stopped', daily_complete=False,
        supported_chain_complete=complete, selected_modules=['guide_trial'], rewards_final=False,
        steps=[dict(name='guide_trial', status='complete' if complete else 'stopped', result=result)])
    _atomic_json(directory / 'report.json', report)
    return report
