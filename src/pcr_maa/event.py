"""One bounded event-hard transaction; no slots, controller input or retry API."""
import re
import time
from datetime import date, datetime, timezone
from .game_day import current_game_day
from .preset_scan import key

SCOPE = 'event-hard'


def is_story_h_title(title):
    return isinstance(title, str) and re.fullmatch(r'活动关卡H-[1-9]\d*', title) is not None


def valid_event_identity(value):
    # The game supplies no year here. Preserve the observed period; never infer
    # a refresh, change the Journal namespace, or reinterpret an old unknown.
    if (not isinstance(value, dict) or set(value) != {'layout', 'period'}
            or value['layout'] != 'story_event_h' or not isinstance(value['period'], str)):
        return False
    match = re.fullmatch(r'举办时间：(\d{2}/\d{2})(\d{2}:\d{2})~(\d{2}/\d{2})(\d{2}:\d{2})', value['period'])
    if not match:
        return False
    try:
        # Leap year is only a calendar-field validator, not an identity field.
        for month_day, clock in ((match[1], match[2]), (match[3], match[4])):
            datetime.strptime('2000/' + month_day + clock, '%Y/%m/%d%H:%M')
    except ValueError:
        return False
    return True


def assess_event(reading, context):
    if (context in ('same-day-unreset', 'user-reported-unreset', 'gui-current-state')
            and reading.get('availability') == 'closed' and reading.get('closed_verified') is True
            and reading.get('native_ok') is True and reading.get('rows') == []):
        return 'skipped_event_unavailable'
    rows = reading.get('rows', [])
    if (context not in ('same-day-unreset', 'user-reported-unreset', 'gui-current-state')
            or reading.get('native_ok') is not True or reading.get('event_verified') is not True
            or reading.get('list_complete') is not True or reading.get('incomplete_reason')
            or reading.get('view') != 'checked' or not rows):
        return 'reading_unknown'
    if len({key(r) for r in rows}) != len(rows):
        return 'identity_mismatch'
    modern = reading.get('event_identity') is not None
    if modern and not valid_event_identity(reading['event_identity']):
        return 'identity_mismatch'
    for r in rows:
        if (r.get('category') != '活动' or r.get('difficulty') != '困难'
                or type(r.get('limit')) is not int or r['limit'] != 3
                or type(r.get('remaining')) is not int or not 0 <= r['remaining'] <= 3
                or r.get('checked') is not True or r.get('title_score', 0) < .9
                or not (is_story_h_title(r.get('title')) if modern else
                        re.fullmatch(r'[^\d]+\d+-\d+', r.get('title', '')))
                or re.search('首领|BOSS|普通|高难', r['title'], re.I)):
            return 'identity_mismatch'
    return 'skipped_goal_met' if all(r['remaining'] == 0 for r in rows) else 'needs_execution'


def check_event_quote(q, reading):
    if (q.get('scope') != SCOPE or q.get('identity_verified') is not True
            or q.get('identities') != [list(key(r)) for r in reading['rows']]
            or q.get('event_identity') != reading.get('event_identity')
            or q.get('event_identity') is not None and not valid_event_identity(q['event_identity'])):
        return 'quote_identity_mismatch'
    if q.get('insufficient') in ('stamina', 'tickets'):
        return 'skipped_insufficient_' + q['insufficient']
    if q.get('verified') is not True:
        return 'quote_unknown'
    counts = q.get('counts', [])
    needed = [r['remaining'] for r in reading['rows']]
    # Accept only the actual quote, bounded by each row's remaining daily goal.
    # Never adjust the saved count/selection or infer a resource-based refill.
    if (not counts or len(counts) != len(needed) or any(type(n) is not int for n in counts)
            or any(not 0 < n <= remaining for n, remaining in zip(counts, needed))):
        return 'count_does_not_match_full_goal'
    fields = ('stamina_before', 'stamina_after')
    if any(type(q.get(f)) is not int or q[f] < 0 for f in fields):
        return 'quote_unknown'
    if q['stamina_before'] <= q['stamina_after']:
        return 'quote_contradiction'
    return 'ready'


def execute_event(port, journal, *, context=None, game_day, execute=False, preview=False):
    report = dict(scope=SCOPE, outcome='unknown', daily_complete=False, consumption_sent=False,
                  settlement_verified=False, home_verified=False, goal_complete=False, game_day=game_day,
                  mode='execute' if execute else 'preview-confirm' if preview else 'observe',
                  count_context=context, count_context_source='current-game-observation' if context == 'gui-current-state' else 'user-reported' if
                  context == 'user-reported-unreset' else 'caller_assertion' if context else 'unconfirmed')
    from . import result_checkpoint as checkpoints
    checkpoint = None
    try:
        if execute and preview or date.fromisoformat(game_day).isoformat() != game_day:
            raise ValueError('Invalid mode or game day')
        with journal.locked():
            events = journal.events()
            if any(e.get('scope') != SCOPE for e in events):
                raise ValueError('Foreign journal namespace')
            events = checkpoints.recover(journal, events, 'event')
            if events and events[-1]['state'] == 'in_flight':
                report['outcome'] = 'blocked_in_flight'
                return report
            if any(e.get('scope') != SCOPE for e in events):
                raise ValueError('Foreign journal namespace')
            start = time.monotonic()
            last_wall = datetime.now(timezone.utc)
            if current_game_day(last_wall) != game_day:
                raise ValueError('current_game_day_required')

            def fresh():
                nonlocal last_wall
                now = datetime.now(timezone.utc)
                reason = ('wall_clock_rollback_need_new_observation' if now < last_wall else
                          'game_day_changed_need_new_observation' if current_game_day(now) != game_day else
                          'reading_expired' if time.monotonic()-start > 180 else None)
                last_wall = now
                if reason:
                    report['observation_invalid_reason'] = reason
                return 'observation_invalid_reason' not in report

            pending = next((e for e in reversed(events) if e.get('state') == 'settled'), None)
            if pending and pending.get('disposition') != checkpoints.DISPOSITION:
                pending = None
            if pending and callable(getattr(port, 'recover_result', None)):
                index = events.index(pending)
                prefix = b''.join(journal.path.read_bytes().splitlines(keepends=True)[:index])
                recovery = checkpoints.ResultCheckpoint(journal, kind='event', original=events[index-1], prefix=prefix).read()
                report['result_recovery'] = port.recover_result(recovery)
                if not report['result_recovery'].get('recovery_verified'):
                    report.update(outcome='result_verified_followup_incomplete', recovery_available=True, retry_permitted=False)
                    return report
            r = port.read()
            report['reading'] = r
            if not fresh():
                report['outcome'] = 'need_new_observation'
                return report
            status = assess_event(r, context)
            previous = next((e for e in reversed(events) if e['state'] == 'settled' and e.get('input_execution') != 'not_sent'
                             and e['game_day'] == game_day), None)
            if previous and previous.get('disposition') == checkpoints.DISPOSITION:
                if (status != 'skipped_goal_met' or [list(key(x)) for x in r['rows']] != previous['identities']
                        or previous.get('event_identity') != r.get('event_identity')):
                    report.update(outcome='blocked_post_counts_unverified', retry_permitted=False)
                    return report
                previous = None
            if previous and status != 'skipped_event_unavailable' and ([list(key(x)) for x in r['rows']] != previous['identities']
                             or any(x['remaining'] > n for x, n in zip(r['rows'], previous['remaining']))
                             or previous.get('event_identity') != r.get('event_identity')):
                status = 'counter_reset_or_identity_change'
            report['outcome'] = status
            if status == 'skipped_event_unavailable':
                report['home_verified'] = port.home()
                if not report['home_verified']:
                    report['outcome'] = 'closed_home_unverified'
                return report
            if status == 'skipped_goal_met':
                report['goal_complete'] = True
                report['home_verified'] = port.home()
                return report
            if status != 'needs_execution':
                return report
            # Completed rows stay in the full before/after evidence; they are
            # excluded only from this transaction's expected confirmation rows.
            planned = dict(r, rows=[row for row in r['rows'] if row['remaining'] > 0])
            q = port.quote(planned)
            report['quote'] = q
            if not fresh():
                report['outcome'] = 'need_new_observation'
                return report
            report['outcome'] = check_event_quote(q, planned)
            if report['outcome'].startswith('skipped_insufficient'):
                report['home_verified'] = port.home()
            if report['outcome'] != 'ready':
                return report
            if not execute and not preview:
                report['outcome'] = 'ready_not_executed'
                return report
            if not fresh():
                report['outcome'] = 'reading_expired'
                return report
            c = port.confirm(planned, q)
            report['confirmation'] = c
            if not fresh():
                report['outcome'] = 'need_new_observation'
                return report
            if preview or c.get('verified') is not True:
                report['cancel_verified'] = port.cancel_confirmation()
                if report['cancel_verified']:
                    report['home_verified'] = port.home()
                report['outcome'] = ('preview_complete' if preview and c.get('verified') is True
                                     and report['home_verified'] else 'confirmation_unverified_or_expired')
                return report
            intent = dict(state='in_flight', scope=SCOPE, game_day=game_day,
                                quote=q, context=context, at=time.time())
            journal.append(intent)
            report['consumption_sent'] = True
            checkpoint = checkpoints.bind(port, journal, 'event', r, q)
            from .native import NativeInputNotSent
            try:
                result = port.submit(q)
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
                    and result.get('preset_return_verified') and result.get('result_images')):
                report['outcome'] = 'unknown_settlement'
                return report
            after = port.read()
            report['after'] = after
            after_status = assess_event(after, context)
            counts = {key(row): n for row, n in zip(planned['rows'], q['counts'])}
            if (not fresh() or after_status not in ('skipped_goal_met', 'needs_execution')
                    or [key(x) for x in after['rows']] != [key(x) for x in r['rows']]
                    or after.get('event_identity') != r.get('event_identity')
                    or any(a['remaining'] != b['remaining']-counts.get(key(b), 0)
                           for a, b in zip(after['rows'], r['rows']))):
                report['outcome'] = 'unknown_post_counts'
                return report
            journal.append(dict(state='settled', scope=SCOPE, game_day=game_day, at=time.time(),
                                identities=[list(key(x)) for x in r['rows']],
                                remaining=[x['remaining'] for x in after['rows']],
                                result_receipt=result.get('result_receipt'), after_receipt=after.get('reading_receipt'),
                                **({'event_identity': r['event_identity']} if r.get('event_identity') is not None else {})))
            report.update(outcome='executed', settlement_verified=True,
                          goal_complete=after_status == 'skipped_goal_met')
            report['home_verified'] = port.home()
            if not report['home_verified']:
                report['outcome'] = 'executed_home_failed'
    except (Exception, KeyboardInterrupt) as exc:
        if hasattr(exc, 'native_stop'):
            report['native_stop'] = exc.native_stop
        if hasattr(exc, 'receipt'):
            report['native_evidence'] = exc.receipt
        report.update(outcome='executed_home_failed' if report['settlement_verified'] else
                      'unknown_settlement' if report['consumption_sent'] else 'blocked_or_failed',
                      error=str(exc) or 'interrupted')
    finally:
        if checkpoint is not None and callable(getattr(port, 'bind_result_checkpoint', None)):
            port.bind_result_checkpoint(None)
        checkpoints.annotate(report, checkpoint)
    return report
