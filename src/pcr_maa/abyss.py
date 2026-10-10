"""One bounded abyss outpost transaction, independent of event/main slots."""
from copy import deepcopy
from datetime import date, datetime, timezone
import re
import time
from uuid import uuid4

from .game_day import current_game_day
from .onekey_history import validate_stable_device

SCOPE = 'abyss-outpost'
DIFFICULTIES = ('普通', '困难', '高难')
CONTEXTS = ('same-day-unreset', 'user-reported-unreset', 'gui-current-state')


def normalize_period(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r'\s+', '', value).replace('：', ':').replace('～', '~')
    match = re.fullmatch(r'举办时间:(\d{2}/\d{2})(\d{2}:\d{2})~(\d{2}/\d{2})(\d{2}:\d{2})', value)
    if not match:
        return None
    try:
        # The UI has no year. Validate its fields without claiming refresh.
        for day, clock in ((match[1], match[2]), (match[3], match[4])):
            datetime.strptime('2000/' + day + clock, '%Y/%m/%d%H:%M')
    except ValueError:
        return None
    return value


def valid_identity(value):
    return (isinstance(value, dict) and set(value) == {'mode', 'event_name', 'period'}
            and value['mode'] == '深渊讨伐战'
            and isinstance(value['event_name'], str)
            and re.fullmatch(r'.{1,40}深渊', value['event_name']) is not None
            and isinstance(value['period'], str)
            and normalize_period(value['period']) == value['period'])


def assess_home(reading):
    if (isinstance(reading, dict) and reading.get('native_ok') is True
            and reading.get('availability') == 'closed' and reading.get('closed_verified') is True
            and reading.get('rows') == []):
        return 'skipped_event_unavailable'
    if (not isinstance(reading, dict) or reading.get('native_ok') is not True
            or reading.get('abyss_page_verified') is not True
            or reading.get('list_complete') is not True
            or not valid_identity(reading.get('abyss_identity'))):
        return 'reading_unknown'
    rows = reading.get('rows')
    if not isinstance(rows, list) or len(rows) != len(DIFFICULTIES):
        return 'reading_unknown'
    for row, difficulty in zip(rows, DIFFICULTIES):
        if (not isinstance(row, dict) or row.get('difficulty') != difficulty
                or type(row.get('remaining')) is not int or type(row.get('limit')) is not int
                or row['limit'] != 4 or not 0 <= row['remaining'] <= row['limit']
                or row.get('fields_verified') is not True):
            return 'reading_unknown'
    if all(row['remaining'] == 0 for row in rows):
        return 'skipped_goal_met'
    return 'needs_execution'


def check_quote(quote, reading):
    if (not isinstance(quote, dict) or quote.get('scope') != SCOPE
            or quote.get('identity_verified') is not True
            or quote.get('abyss_identity') != reading.get('abyss_identity')
            or not valid_identity(quote.get('abyss_identity'))):
        return 'quote_identity_mismatch'
    if quote.get('insufficient') in ('stamina', 'tickets'):
        return 'skipped_insufficient_' + quote['insufficient']
    if quote.get('verified') is not True:
        return 'quote_unknown'
    planned = [row for row in reading['rows'] if row['remaining'] > 0]
    rows = quote.get('rows')
    if (not isinstance(rows, list) or len(rows) != len(planned)
            or [row.get('difficulty') for row in rows] != [row['difficulty'] for row in planned]):
        return 'quote_identity_mismatch'
    for row, before in zip(rows, planned):
        if (row.get('title') != '前哨关卡' or row.get('mode') != '深渊讨伐战'
                or row.get('remaining') != before['remaining'] or row.get('limit') != before['limit']
                or type(row.get('times')) is not int or row['times'] != before['remaining']
                or row.get('fields_verified') is not True):
            return 'count_does_not_match_full_goal'
    total = sum(row['times'] for row in rows)
    if (type(quote.get('total_times')) is not int or quote['total_times'] != total
            or type(quote.get('stages')) is not int or quote['stages'] != len(rows)
            or type(quote.get('ticket_cost')) is not int or quote['ticket_cost'] != total
            or type(quote.get('stamina_cost')) is not int or quote['stamina_cost'] <= 0
            or type(quote.get('stamina_owned')) is not int or quote['stamina_owned'] < 0):
        return 'quote_contradiction'
    if quote['stamina_owned'] < quote['stamina_cost']:
        return 'skipped_insufficient_stamina'
    tickets = quote.get('tickets_owned')
    if tickets is not None:
        if type(tickets) is not int or tickets < 0:
            return 'quote_unknown'
        if tickets < quote['ticket_cost']:
            return 'skipped_insufficient_tickets'
    return 'ready'


def same_quote(expected, actual, reading):
    if check_quote(actual, reading) != 'ready':
        return False
    fields = ('scope', 'abyss_identity', 'rows', 'stages', 'total_times', 'stamina_cost', 'ticket_cost')
    # Stamina may regenerate while this same modal remains open. It must still
    # cover the identical observed cost; no refills, count changes or retries.
    return all(actual.get(field) == expected.get(field) for field in fields)


def check_ticket(ticket, quote, reading):
    if (check_quote(quote, reading) != 'ready' or not isinstance(ticket, dict)
            or ticket.get('native_ok') is not True or ticket.get('identity_verified') is not True
            or ticket.get('scope') != SCOPE or ticket.get('abyss_identity') != reading['abyss_identity']
            or ticket.get('background_counts') != [row['times'] for row in quote['rows']]):
        return 'final_confirmation_unknown'
    if (type(ticket.get('stamina_cost')) is not int or ticket['stamina_cost'] != quote['stamina_cost']
            or type(ticket.get('ticket_cost')) is not int or ticket['ticket_cost'] != quote['ticket_cost']
            or type(ticket.get('stamina_owned')) is not int or ticket['stamina_owned'] < 0):
        return 'final_quote_changed'
    if ticket['stamina_owned'] < ticket['stamina_cost']:
        return 'skipped_insufficient_stamina'
    tickets = ticket.get('tickets_owned')
    if tickets is not None and (type(tickets) is not int or tickets < 0):
        return 'final_confirmation_unknown'
    if tickets is not None and tickets < ticket['ticket_cost']:
        return 'skipped_insufficient_tickets'
    return 'ready'


def execute_abyss(port, journal, *, game_day, context=None, execute=False, device=None, preview=False):
    report = dict(scope=SCOPE, outcome='unknown', daily_complete=False, consumption_sent=False,
                  settlement_verified=False, home_verified=False, goal_complete=False,
                  game_day=game_day, count_context=context, device=deepcopy(device),
                  mode='execute' if execute else 'preview-confirm' if preview else 'observe',
                  count_context_source='current-game-observation' if context == 'gui-current-state'
                  else 'user-reported' if context == 'user-reported-unreset' else 'caller_assertion')
    from . import result_checkpoint as checkpoints
    checkpoint = None
    try:
        if (type(execute) is not bool or type(preview) is not bool or execute and preview
                or date.fromisoformat(game_day).isoformat() != game_day or context not in CONTEXTS):
            raise ValueError('abyss_mode_day_or_context_invalid')
        if context == 'gui-current-state':
            # Same GUI Owner target as every other module. This marker does not
            # claim an account or cross-restart emulator instance identity.
            from .schedule_quick import validate_gui_device
            validate_gui_device(device)
        else:
            validate_stable_device(device)
        with journal.locked():
            events = journal.events()
            if (events and events[-1]['state'] == 'in_flight'
                    and checkpoints.ResultCheckpoint(journal, kind='abyss').read() is None):
                report.update(outcome='blocked_in_flight', retry_permitted=False, original_execution_unresolved=True)
                return report
            if any(e.get('scope') != SCOPE or e.get('device') != device for e in events):
                raise ValueError('abyss_journal_namespace_or_device_changed')
            events = checkpoints.recover(journal, events, 'abyss')
            # An unknown survives restart, date changes and emulator changes.
            # Check it before navigation; no recovery/resend token is provided.
            if events and events[-1]['state'] == 'in_flight':
                report.update(outcome='blocked_in_flight', retry_permitted=False,
                              original_execution_unresolved=True)
                return report
            if any(event.get('scope') != SCOPE or event.get('device') != device for event in events):
                raise ValueError('abyss_journal_namespace_or_device_changed')
            # Maintenance may prove the original consumer and result close
            # while explicitly retaining unknown post-counters/navigation.
            from .abyss_reconcile import DISPOSITION, verify_disposition
            for index, event in enumerate(events):
                if event.get('disposition') == DISPOSITION:
                    from hashlib import sha256
                    prefix = b''.join(journal.path.read_bytes().splitlines(keepends=True)[:index])
                    if sha256(prefix).hexdigest() != event.get('previous_root_sha256'):
                        raise ValueError('abyss_reconciled_history_changed')
                    verify_disposition(events[index - 1], event, device=device,
                                       journal_directory=journal.directory)
            started = time.monotonic()
            last_wall = datetime.now(timezone.utc)
            if current_game_day(last_wall) != game_day:
                raise ValueError('current_game_day_required')

            def fresh():
                nonlocal last_wall
                now = datetime.now(timezone.utc)
                reason = ('wall_clock_rollback_need_new_observation' if now < last_wall else
                          'game_day_changed_need_new_observation' if current_game_day(now) != game_day else
                          'reading_expired' if time.monotonic() - started > 180 else None)
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
                recovery = checkpoints.ResultCheckpoint(journal, kind='abyss', original=events[index-1], prefix=prefix).read()
                report['result_recovery'] = port.recover_result(recovery)
                if not report['result_recovery'].get('recovery_verified'):
                    report.update(outcome='result_verified_followup_incomplete', recovery_available=True, retry_permitted=False)
                    return report
            reading = port.read()
            report['reading'] = reading
            if not fresh():
                report['outcome'] = 'need_new_observation'
                return report
            status = assess_home(reading)
            previous = next((event for event in reversed(events)
                             if event['state'] == 'settled' and event.get('input_execution') != 'not_sent'
                             and event['game_day'] == game_day), None)
            if previous and status != 'skipped_event_unavailable' and previous.get('disposition') in (DISPOSITION, checkpoints.DISPOSITION):
                # No retry of a known consumed sweep in its original cycle.
                # A current complete zero observation can safely skip it;
                # other counts stay blocked until a later daily observation.
                if (status == 'needs_execution' or
                        reading.get('abyss_identity') != previous.get('abyss_identity')):
                    report.update(outcome='blocked_post_counts_unverified', retry_permitted=False,
                                  original_consumption_verified=True)
                    return report
                previous = None
            if previous and status in ('needs_execution', 'skipped_goal_met'):
                if (reading['abyss_identity'] != previous.get('abyss_identity')
                        or [row['difficulty'] for row in reading['rows']] != previous.get('difficulties')
                        or [row['limit'] for row in reading['rows']] != previous.get('limits')
                        or len(previous.get('remaining', [])) != len(DIFFICULTIES)
                        or any(row['remaining'] > count for row, count in
                               zip(reading['rows'], previous.get('remaining', [])))):
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
            quote = port.quote(reading)
            report['quote'] = quote
            if not fresh():
                report['outcome'] = 'need_new_observation'
                return report
            status = check_quote(quote, reading)
            report['outcome'] = status

            def cancel_home():
                report['cancel_verified'] = port.cancel_confirmation()
                if report['cancel_verified']:
                    report['home_verified'] = port.home()

            if status.startswith('skipped_insufficient'):
                cancel_home()
                return report
            if status != 'ready':
                # Only the dedicated same-frame Cancel path can return safely.
                cancel_home()
                return report
            if not execute and not preview:
                cancel_home()
                report['outcome'] = 'ready_not_executed'
                return report
            confirmed = port.confirm(reading, quote)
            report['confirmation'] = confirmed
            report['challenge_opened'] = confirmed.get('challenge_opened') is True
            if not fresh():
                report['outcome'] = 'need_new_observation'
                return report
            if preview or confirmed.get('verified') is not True:
                cancel_home()
                report['outcome'] = (confirmed['status'] if confirmed.get('status', '').startswith('skipped_insufficient')
                                     else 'preview_complete' if preview and confirmed.get('verified') is True
                                     and report['home_verified'] else 'confirmation_unverified_or_expired')
                return report
            intent_id = uuid4().hex
            intent = dict(state='in_flight', scope=SCOPE, operation='abyss_outpost',
                                intent_id=intent_id, game_day=game_day, device=deepcopy(device),
                                abyss_identity=reading['abyss_identity'], quote=quote,
                                final_confirmation=confirmed.get('reading'),
                                opening_receipt=confirmed.get('opening_receipt'),
                                context=context, at=time.time())
            journal.append(intent)
            report.update(consumption_sent=True, intent_id=intent_id, retry_permitted=False)
            checkpoint = checkpoints.bind(port, journal, 'abyss', reading, quote)
            from .native import NativeInputNotSent
            try:
                result = port.submit(quote)
            except NativeInputNotSent as exc:
                journal.append_not_sent(intent, exc.receipt)
                report.update(outcome='native_input_not_sent', consumption_sent=False,
                              input_execution='not_sent', native_evidence=exc.receipt,
                              settlement_verified=False, retry_permitted=False)
                return report
            report['submission'] = result
            report['consumer_dispatched'] = result.get('consumption_dispatched') is True
            if not fresh():
                report['outcome'] = 'unknown_post_context'
                return report
            if not (result.get('consumption_dispatched') is True
                    and result.get('result_verified') is True
                    and result.get('abyss_return_verified') is True
                    and result.get('result_images')):
                report.update(outcome='unknown_settlement', original_execution_unresolved=True)
                return report
            after = port.read()
            report['after'] = after
            if (not fresh() or assess_home(after) != 'skipped_goal_met'
                    or after['abyss_identity'] != reading['abyss_identity']
                    or [row['limit'] for row in after['rows']] != [row['limit'] for row in reading['rows']]):
                report.update(outcome='unknown_post_counts', original_execution_unresolved=True)
                return report
            journal.append(dict(state='settled', scope=SCOPE, operation='abyss_outpost',
                                intent_id=intent_id, game_day=game_day, device=deepcopy(device), at=time.time(),
                                abyss_identity=reading['abyss_identity'],
                                difficulties=[row['difficulty'] for row in after['rows']],
                                limits=[row['limit'] for row in after['rows']],
                                remaining=[row['remaining'] for row in after['rows']],
                                result_receipt=result.get('result_receipt'),
                                after_receipt=after.get('reading_receipt')))
            report.update(outcome='executed', settlement_verified=True, goal_complete=True)
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
        if report['consumption_sent'] and not report['settlement_verified']:
            report.update(original_execution_unresolved=True, retry_permitted=False)
    finally:
        if checkpoint is not None and callable(getattr(port, 'bind_result_checkpoint', None)):
            port.bind_result_checkpoint(None)
        checkpoints.annotate(report, checkpoint)
    return report
