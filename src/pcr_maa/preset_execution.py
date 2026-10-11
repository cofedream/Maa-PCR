"""One preset transaction, never a scheduler. No controller input here."""
from copy import deepcopy
import json
import os
import time
from datetime import datetime, timezone
from .game_day import current_game_day
from contextlib import contextmanager
from pathlib import Path

from .daily_goal import evaluate_daily_goal
from .preset_scan import key, identity_key


def identity_ok(rows, kind):
    if kind not in ('deep', 'hard', 'very_hard', 'normal') or not rows or len({key(r) for r in rows}) != len(rows):
        return False
    for row in rows:
        if not row.get('title') or row.get('title_score', 1) < .8:
            return False
        if kind == 'normal':
            if (row.get('category') != '主线关卡' or row.get('difficulty') != '普通'
                    or row.get('unlimited') is not True or row.get('remaining') is not None
                    or row.get('limit') is not None):
                return False
        elif kind == 'deep':
            if row.get('category') != '深域关卡' or row.get('difficulty') is not None or row.get('limit') != 10:
                return False
        elif (row.get('category') not in ('主线关卡', '外传')
              or row.get('difficulty') != {'hard': '困难', 'very_hard': '高难'}.get(kind)
              or row.get('limit') != (5 if row.get('category') == '外传' else 3)):
            return False
    return True


def assess(reading, preset, context):
    if preset['kind'] == 'normal':
        # No invented finite counters or daily usage assertion for Normal.
        target = preset['target_per_stage']
        goal = dict(rows=[dict(needed=target, remaining=None, limit=None) for r in reading.get('rows', [])],
                    preset_goal_met=None, count_context_source='not-applicable',
                    target_per_selected_stage=target, target_scope='this-run', unknown_reasons=[])
        if not reading.get('rows'):
            goal['unknown_reasons'] = ['空选集或列表未识别，请在游戏核对已勾选关卡；本次不提交']
            return 'empty_or_unreadable_selection', goal
        observed = reading.get('observation') or {}
        if (type(target) is not int or not 1 <= target <= 999
                or reading.get('native_ok') is not True or reading.get('list_complete') is not True
                or reading.get('incomplete_reason') or reading.get('view') != 'checked'
                or reading.get('requested_slot') != preset['slot'] or reading.get('selected_slot') != preset['slot']
                or reading.get('ocr_receipt', {}).get('native_ok') is not True
                or observed.get('page') != 'presets' or observed.get('selected_slot') != preset['slot']):
            return 'reading_unknown', goal
        if not identity_ok(reading.get('rows', []), 'normal') or any(r.get('checked') is not True for r in reading['rows']):
            return 'identity_mismatch', goal
        return 'needs_execution', goal
    goal = evaluate_daily_goal(reading, preset['target_per_stage'], count_context=context)
    if goal['unknown_reasons'] or reading.get('requested_slot') != preset['slot']:
        return 'reading_unknown', goal
    if not identity_ok(reading['rows'], preset['kind']):
        return 'identity_mismatch', goal
    if goal['preset_goal_met']:
        return 'skipped_goal_met', goal
    return 'needs_execution', goal


def check_quote(quote, reading, goal, *, max_stamina=None):
    """No implicit min(saved count, remaining): every actual row must fit."""
    if not quote:
        return 'quote_unknown'
    if quote.get('slot') != reading['requested_slot']:
        return 'quote_identity_mismatch'
    if [list(identity_key(i)) for i in quote.get('identities', [])] != [list(key(r)) for r in reading['rows']]:
        return 'quote_identity_mismatch'
    if quote.get('identity_verified') is True and quote.get('insufficient') in ('stamina', 'tickets'):
        return 'skipped_insufficient_' + quote['insufficient']
    if quote.get('verified') is not True:
        return 'quote_unknown'
    counts = quote.get('counts')
    if not isinstance(counts, list) or len(counts) != len(goal['rows']):
        return 'quote_unknown'
    if any(type(n) is not int or n < 0 for n in counts) or not any(counts):
        return 'quote_unknown'
    if goal.get('target_scope') == 'this-run' and counts != [r['needed'] for r in goal['rows']]:
        return 'count_fit_quote_mismatch'
    if any(n > r['needed'] or r['remaining'] is not None and n > r['remaining'] for n, r in zip(counts, goal['rows'])):
        return 'preset_would_exceed_goal'
    for field in ('stamina_before', 'stamina_after'):
        if type(quote.get(field)) is not int or quote[field] < 0:
            return 'quote_unknown'
    if quote.get('insufficient') in ('stamina', 'tickets'):
        return 'skipped_insufficient_' + quote['insufficient']
    if quote['stamina_before'] <= quote['stamina_after']:
        return 'quote_contradiction'
    if max_stamina is not None:
        if type(max_stamina) is not int or max_stamina < 0:
            return 'stamina_budget_invalid'
        if quote['stamina_before'] - quote['stamina_after'] > max_stamina:
            return 'stamina_budget_exceeded'
    return 'ready'


class Journal:
    """OS lock + append/fsync journal. No delete/reset/retry switch."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'transactions.jsonl'

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / 'transaction.lock').open('a+b') as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield self
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def events(self):
        if not self.path.exists():
            return []
        events = [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines()]
        if any(not isinstance(e, dict) or e.get('state') not in ('in_flight', 'settled', 'historical_unknown') for e in events):
            raise ValueError('Journal malformed; parent reconciliation required')
        for index, event in enumerate(events):
            if (event['state'] != ('in_flight' if index % 2 == 0 else 'settled')
                    and not (index % 2 and event['state'] == 'historical_unknown'
                             and event.get('operation') == 'schedule_onekey')):
                raise ValueError('Journal sequence invalid')
        return events

    def append_not_sent(self, intent, receipt):
        # Only close the intent created by this call; old unknowns stay untouched.
        if self.events()[-1] != intent or receipt.get('input_execution') != 'not_sent':
            raise ValueError('Current not-sent intent proof required')
        self.append(dict(intent, state='settled', at=time.time(),
                         input_execution='not_sent', consumption_sent=False,
                         settlement_verified=False, business_result_verified=False,
                         native_evidence=receipt))

    def append(self, event):
        with self.path.open('a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n')
            handle.flush()
            os.fsync(handle.fileno())


def execute_one(port, preset, context, journal, *, execute=False, preview=False, fit_remaining=False,
                game_day, exit_page='home'):
    """Port is the native Maa adapter; fake ports test business decisions only."""
    if exit_page not in ('home', 'preset'):
        raise ValueError('invalid_preset_exit_page')
    elapsed_start = time.monotonic()
    normal = preset['kind'] == 'normal'
    if normal:
        context = None
    report = dict(mode='preview-confirm' if preview else 'execute-preset', outcome='unknown', daily_complete=False,
                  consumption_sent=False, settlement_verified=False, home_verified=False,
                  exit_page=exit_page, boundary_page=None, boundary_verified=False,
                  preset=preset, game_day=game_day, count_context=context,
                  count_context_source=('not-applicable' if normal else 'current-game-observation' if context == 'gui-current-state' else 'user-reported' if context == 'user-reported-unreset' else
                                        'caller_assertion' if context == 'same-day-unreset' else 'unconfirmed'))
    fit = dict(enabled=fit_remaining, original=None, target=None, attempted=False,
               restored=None, restoration_attempted=False)
    if fit_remaining:
        report['count_fit'] = fit
    performance = dict(clock='monotonic', elapsed_seconds=None, port_calls={}, phase_seconds={},
                       native_counters=dict(screenshots=None, ocr=None, inputs=None),
                       native_counter_source='unavailable_from_port_protocol')
    report['performance'] = performance

    def call(name, *args):
        before = time.monotonic()
        performance['port_calls'][name] = performance['port_calls'].get(name, 0) + 1
        try:
            return getattr(port, name)(*args)
        finally:
            performance['phase_seconds'][name] = performance['phase_seconds'].get(name, 0) + time.monotonic() - before

    def restore():
        if not fit['attempted'] or fit['restored'] is True:
            return True
        if fit['restoration_attempted']:
            return False
        # Unknown consumption: no navigation, readback, adjustment or retry.
        if report['consumption_sent'] and not report['settlement_verified']:
            fit['restored'] = False
            fit['restore_reason'] = 'unknown_consumption_preserve_scene'
            return False
        if report.get('observation_invalid_reason'):
            fit['restored'] = False
            fit['restore_reason'] = 'need_new_observation_preserve_scene'
            return False
        fit['restoration_attempted'] = True
        try:
            # Adapter verifies original slot/page before every click, no navigation.
            fit['restoration'] = call('fit_count', preset['slot'], fit['original'])
            fit['restore_readback'] = call('read_count', preset['slot'])
            fit['restored'] = (fit['restoration'].get('verified') is True
                               and fit['restore_readback'] == fit['original'])
        except (Exception, KeyboardInterrupt) as exc:
            fit.update(restored=False, restore_error=str(exc) or 'interrupted')
        if not fit['restored']:
            report['outcome_before_restore_failure'] = report['outcome']
            report['outcome'] = ('executed_restore_failed' if report['settlement_verified']
                                 else 'count_restore_failed')
        return fit['restored']

    def finish_boundary():
        if not restore():
            return False
        if exit_page == 'home':
            report['home_verified'] = call('home')
            report['boundary_page'] = 'home' if report['home_verified'] else None
            report['boundary_verified'] = report['home_verified'] is True
        else:
            # Count restoration may change the scene; reuse requires a fresh read after it.
            method = 'verify_preset' if callable(getattr(port, 'verify_preset', None)) else 'read'
            boundary = call(method, preset['slot'])
            report['boundary_readback'] = boundary
            observed = boundary.get('observation') or {}
            report['boundary_verified'] = (
                boundary.get('native_ok') is True
                and type(boundary.get('requested_slot')) is int
                and boundary.get('requested_slot') == preset['slot']
                and type(boundary.get('selected_slot')) is int
                and boundary.get('selected_slot') == preset['slot']
                and boundary.get('ocr_receipt', {}).get('native_ok') is True
                and observed.get('page') == 'presets'
                and type(observed.get('selected_slot')) is int
                and observed.get('selected_slot') == preset['slot'])
            report['boundary_page'] = 'presets' if report['boundary_verified'] else None
        return report['boundary_verified']

    @contextmanager
    def cleanup_count():
        try:
            yield
        finally:
            restore()

    from . import result_checkpoint as checkpoints
    checkpoint = None
    try:
        if preset['target_per_stage'] == 0:
            report['outcome'] = 'skipped_disabled'
            return report
        if fit_remaining and not (preview or execute):
            raise ValueError('fit-remaining requires preview-confirm or execute')
        if preview and execute:
            raise ValueError('preview and execute are mutually exclusive')
        with journal.locked(), cleanup_count():
            events = checkpoints.recover(journal, journal.events(), 'preset')
            if events and events[-1]['state'] == 'in_flight':
                report['outcome'] = 'blocked_in_flight'
                return report
            from .preset_result_reconcile import DISPOSITION, verify_disposition
            from hashlib import sha256
            for index, event in enumerate(events):
                if event.get('disposition') == DISPOSITION:
                    prefix = b''.join(journal.path.read_bytes().splitlines(keepends=True)[:index])
                    if sha256(prefix).hexdigest() != event.get('previous_root_sha256'):
                        raise ValueError('preset_reconciled_history_changed')
                    verify_disposition(events[index - 1], event, journal_directory=journal.directory)
            started = time.monotonic()
            last_wall = datetime.now(timezone.utc)
            if current_game_day(last_wall) != game_day:
                raise ValueError('current_game_day_required')

            def fresh():
                nonlocal last_wall
                now = datetime.now(timezone.utc)
                reason = ('wall_clock_rollback_need_new_observation' if now < last_wall else
                          'game_day_changed_need_new_observation' if current_game_day(now) != game_day else
                          'reading_expired' if time.monotonic()-started > 180 else None)
                last_wall = now
                if reason:
                    report['observation_invalid_reason'] = reason
                return 'observation_invalid_reason' not in report

            pending = next((e for e in reversed(events) if e.get('state') == 'settled' and e.get('slot') == preset['slot']), None)
            if pending and pending.get('disposition') != checkpoints.DISPOSITION:
                pending = None
            if pending and callable(getattr(port, 'recover_result', None)):
                index = events.index(pending)
                prefix = b''.join(journal.path.read_bytes().splitlines(keepends=True)[:index])
                recovery = checkpoints.ResultCheckpoint(journal, kind='preset', original=events[index-1], prefix=prefix).read()
                report['result_recovery'] = call('recover_result', recovery)
                if not report['result_recovery'].get('recovery_verified'):
                    report.update(outcome='result_verified_followup_incomplete', recovery_available=True, retry_permitted=False)
                    return report
            reading = call('read', preset['slot'])
            report['reading'] = reading
            if not fresh():
                report['outcome'] = 'need_new_observation'
                return report
            if pending and pending['original'].get('count_fit'):
                old_fit = pending['original']['count_fit']
                if old_fit['original'] != old_fit['target']:
                    if ([list(key(r)) for r in reading.get('rows', [])] != pending['identities']
                            or not reading.get('native_ok') or not reading.get('list_complete')):
                        report['outcome'] = 'count_restore_original_page_unverified'
                        return report
                    value = call('read_count', preset['slot'])
                    if value not in (old_fit['original'], old_fit['target']):
                        report['outcome'] = 'count_restore_value_changed'
                        return report
                    fit.update(original=old_fit['original'], target=old_fit['target'], attempted=True)
                    report['count_fit'] = fit
                    if not restore():
                        return report
                    report['recovered_count_fit'] = deepcopy(fit)
            status, goal = assess(reading, preset, context)
            report['goal'] = goal
            # Same-day increases or changed contents require parent reconciliation.
            previous = next((e for e in reversed(events) if e['state'] == 'settled' and e.get('input_execution') != 'not_sent'
                             and e['game_day'] == game_day and e['slot'] == preset['slot']), None)
            if previous and previous.get('disposition') in (DISPOSITION, checkpoints.DISPOSITION):
                if (status != 'skipped_goal_met' or [list(key(r)) for r in reading['rows']] !=
                        [list(identity_key(i)) for i in previous['identities']]):
                    report.update(outcome='blocked_post_counts_unverified', retry_permitted=False)
                    return report
                previous = None
            if previous and not normal and ([list(key(r)) for r in reading['rows']] != [list(identity_key(i)) for i in previous['identities']]
                             or any(a['remaining'] > b for a, b in zip(reading['rows'], previous['remaining']))):
                status = 'counter_reset_or_identity_change'
            report['outcome'] = status
            if status == 'skipped_goal_met':
                finish_boundary()
                return report
            if status != 'needs_execution':
                return report
            if fit_remaining:
                if report.get('recovered_count_fit'):
                    fit = dict(enabled=True, original=None, target=None, attempted=False,
                               restored=None, restoration_attempted=False)
                    report['count_fit'] = fit
                if not fresh():
                    report['outcome'] = 'reading_expired'
                    return report
                needed = [r['needed'] for r in goal['rows']]
                if len(set(needed)) != 1 or not needed or needed[0] <= 0:
                    report['outcome'] = 'count_fit_mixed_gap'
                    return report
                target = needed[0]
                if not normal and any(target > r['remaining'] or target > r['limit'] for r in goal['rows']):
                    report['outcome'] = 'count_fit_exceeds_available'
                    return report
                fit['target'] = target
                original = call('read_count', preset['slot'])
                fit['original'] = original
                if not fresh():
                    report['outcome'] = 'need_new_observation'
                    return report
                if type(original) is not int or not 1 <= original <= (999 if normal else 10):
                    report['outcome'] = 'count_fit_original_unknown'
                    return report
                if original == target:
                    # No count edit to restore; fresh quote/confirmation still bind the actual value.
                    fit['adjustment'] = dict(verified=True, observed=original, steps=[], no_change=True,
                                             initial_evidence=getattr(port, '_count_receipt', None))
                    fit['readback'] = original
                else:
                    # Even a failed dispatch may have changed the value.
                    fit['attempted'] = True
                    fit['adjustment'] = call('fit_count', preset['slot'], target)
                    fit['readback'] = call('read_count', preset['slot'])
                if not fresh():
                    report['outcome'] = 'need_new_observation'
                    return report
                if not fit['adjustment'].get('verified') or fit['readback'] != target:
                    report['outcome'] = 'count_fit_readback_failed'
                    return report
            quote = call('quote', reading)
            report['quote'] = quote
            if not fresh():
                report['outcome'] = 'need_new_observation'
                return report
            status = check_quote(quote, reading, goal, max_stamina=preset.get('max_stamina'))
            if fit_remaining and status == 'ready' and quote.get('counts') != [fit['target']] * len(goal['rows']):
                status = 'count_fit_quote_mismatch'
            report['outcome'] = status
            if status.startswith('skipped_insufficient'):
                finish_boundary()
            if status != 'ready':
                return report
            if not execute and not preview:
                report['outcome'] = 'ready_not_executed'
                return report
            if not fresh():
                report['outcome'] = 'reading_expired'
                return report
            confirmation = call('confirm', reading, quote)
            report['confirmation'] = confirmation
            if not fresh():
                report['outcome'] = 'need_new_observation'
                return report
            if preview or not confirmation.get('verified'):
                report['cancel_verified'] = call('cancel_confirmation')
                if report['cancel_verified']:
                    finish_boundary()
                if fit['attempted'] and fit['restored'] is False:
                    return report
                report['outcome'] = ('preview_complete' if preview and confirmation.get('verified')
                                     and report['boundary_verified'] else 'confirmation_unverified_or_return_failed')
                return report
            # Opening/cancelling the evidenced confirmation is non-consuming.
            # Persist before dispatching the final guarded challenge only.
            intent = dict(state='in_flight', game_day=game_day, slot=preset['slot'],
                                quote={k: quote[k] for k in ('slot', 'identities', 'counts', 'stamina_before',
                                                            'stamina_after')},
                                context=context, at=time.time(),
                                **({'count_fit': {k: fit[k] for k in ('original', 'target', 'readback')}}
                                   if fit_remaining else {}))
            journal.append(intent)
            report['consumption_sent'] = True  # dispatch attempted, not proven game debit
            checkpoint = checkpoints.bind(port, journal, 'preset', reading, quote)
            from .native import NativeInputNotSent
            try:
                result = call('submit', quote)
            except NativeInputNotSent as exc:
                journal.append_not_sent(intent, exc.receipt)
                report.update(outcome='native_input_not_sent', consumption_sent=False,
                              input_execution='not_sent', native_evidence=exc.receipt,
                              settlement_verified=False, retry_permitted=False)
                return report
            report['submission'] = result
            if not fresh():
                report['outcome'] = 'unknown_post_context'
                return report
            if not (result.get('consumption_dispatched') and result.get('result_verified')
                    and result.get('preset_return_verified')):
                report['outcome'] = 'unknown_settlement'
                return report
            after = call('read', preset['slot'])
            report['after'] = after
            after_status, after_goal = assess(after, preset, context)
            report['after_goal'] = after_goal
            if not fresh():
                report['outcome'] = 'unknown_post_context'
                return report
            if (after_status not in ('needs_execution', 'skipped_goal_met')
                    or [key(r) for r in after['rows']] != [key(r) for r in reading['rows']]
                    or any(a['limit'] != b['limit'] or a['remaining'] != (None if normal else b['remaining'] - n)
                           for a, b, n in zip(after['rows'], reading['rows'], quote['counts']))):
                report['outcome'] = 'unknown_post_counts'
                return report
            journal.append(dict(state='settled', game_day=game_day, slot=preset['slot'],
                                identities=quote['identities'], remaining=[r['remaining'] for r in after['rows']],
                                context=context, verification_source='automatic-result-and-full-counts',
                                result_receipt=result.get('result_receipt'),
                                after_receipt=after.get('reading_receipt'), after_goal=after_goal,
                                at=time.time()))
            report.update(outcome='executed', settlement_verified=True, after_goal=after_goal)
            finish_boundary()
            if not report['boundary_verified'] and not (fit['attempted'] and fit['restored'] is False):
                report['outcome'] = 'executed_home_failed' if exit_page == 'home' else 'executed_boundary_failed'
    except (Exception, KeyboardInterrupt) as exc:
        if hasattr(exc, 'native_stop'):
            report['native_stop'] = exc.native_stop
        if hasattr(exc, 'receipt'):
            report['native_evidence'] = exc.receipt
        report.update(outcome=(report['outcome'] if fit['restoration_attempted'] and fit['restored'] is False else
                               ('executed_home_failed' if exit_page == 'home' else 'executed_boundary_failed') if report['settlement_verified'] else
                               'unknown_settlement' if report['consumption_sent'] else 'blocked_or_failed'),
                      error=str(exc) or 'interrupted')
    finally:
        if checkpoint is not None and callable(getattr(port, 'bind_result_checkpoint', None)):
            port.bind_result_checkpoint(None)
        checkpoints.annotate(report, checkpoint)
        performance['elapsed_seconds'] = time.monotonic() - elapsed_start
    return report
