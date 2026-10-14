"""Close an old run before a fresh start; never replay its unknown operations."""
from datetime import datetime, timezone
import re

from .batch import Batch, BatchStopped
from ..preset_execution import Journal


def _valid_intent(intent):
    if (type(intent) is not dict or intent.get('state') != 'in_flight'
            or type(intent.get('operation_id')) is not str
            or re.fullmatch('[0-9a-f]{32}', intent['operation_id']) is None
            or type(intent.get('operation')) is not str
            or re.fullmatch('[a-z][a-z0-9_]{0,95}', intent['operation']) is None):
        return False
    if intent['operation'] == 'enter':
        return all(type(intent.get(key)) is int and 1 <= intent[key] <= 5
                   for key in ('guild_id', 'difficulty'))
    return type(intent.get('enter_id')) is int and intent['enter_id'] > 0


def pending_intent(journal, binding):
    try:
        events = journal.events()
    except (ValueError, OSError) as error:
        raise BatchStopped('restart_records_invalid') from error
    if any(any(a.get(key) != b.get(key) for key in ('operation', 'operation_id', 'binding'))
           for a, b in zip(events[::2], events[1::2])):
        raise BatchStopped('restart_records_invalid')
    pending = events[-1] if events and events[-1]['state'] == 'in_flight' else None
    if pending is not None:
        if not _valid_intent(pending):
            raise BatchStopped('blocked_in_flight')
        if pending.get('binding') != binding.payload():
            raise BatchStopped('restart_target_mismatch')
    return pending


def is_closed_run_history(event, intent):
    evidence = event.get('evidence')
    return (_valid_intent(intent) and event.get('state') == 'historical_unknown'
        and event.get('recovery_scope') == 'labyrinth_restart'
        and event.get('resolution') == 'protocol_run_closed'
        and event.get('original_result_unknown') is True and event.get('replay_forbidden') is True
        and all(event.get(key) == value for key, value in intent.items() if key != 'state')
        and type(intent.get('binding')) is dict
        and type(intent['binding'].get('account_id')) is int and intent['binding']['account_id'] > 0
        and type(intent['binding'].get('device_id')) is str and bool(intent['binding']['device_id'])
        and type(evidence) is dict and evidence.get('source') == 'authenticated_protocol_top'
        and evidence.get('binding') == intent['binding'] and evidence.get('verified') is True
        and type(evidence.get('enter_id')) is int and evidence['enter_id'] == 0
        and evidence.get('fresh_start') is True
        and type(evidence.get('frame')) is int and evidence['frame'] > 0)


def _top(port, binding):
    value = port.top()
    if (type(value) is not dict or value.get('verified') is not True
            or value.get('binding') != binding.payload()
            or type(value.get('frame')) is not int or value['frame'] <= 0
            or type(value.get('enter_id')) is not int or value['enter_id'] < 0
            or value.get('fresh_start') is not (value['enter_id'] == 0)):
        raise BatchStopped('restart_state_unknown')
    return value


def _archive(journal, pending, top):
    if pending is None:
        return
    event = dict(pending, state='historical_unknown', recovery_scope='labyrinth_restart',
        resolution='protocol_run_closed', original_result_unknown=True, replay_forbidden=True,
        resolved_at=datetime.now(timezone.utc).isoformat(),
        evidence=dict(top, source='authenticated_protocol_top'))
    if not is_closed_run_history(event, pending) or journal.events()[-1] != pending:
        raise BatchStopped('restart_records_invalid')
    journal.append(event)


def prepare_restart(request, port, journal, stop, progress):
    if stop():
        raise BatchStopped('stopped')
    binding = port.binding
    recovery = Journal(journal.directory / 'restart')
    with journal.locked(), recovery.locked():
        pending = pending_intent(journal, binding)
        retiring = pending_intent(recovery, binding)
        if retiring is not None and retiring['operation'] != 'restart_retire':
            raise BatchStopped('restart_records_invalid')
        top = _top(port, binding)
        if top['enter_id']:
            # A lost RETIRE response never authorizes another RETIRE of that run.
            if retiring is not None or (pending is not None and pending['operation'] == 'retire'
                                       and pending['enter_id'] == top['enter_id']):
                raise BatchStopped('restart_retire_unknown')
            if request.keep_old_run:
                if pending is not None:
                    raise BatchStopped('kept_run_unknown')
                return
            progress(dict(stage='retiring'))
            batch = Batch(request.settings, port, recovery, binding, stopped=stop)
            enter = top['enter_id']
            try:
                batch.transaction('restart_retire', dict(enter_id=enter),
                                  lambda oid: port.retire(enter, oid))
            except Exception as error:
                if isinstance(error, BatchStopped) and error.args == ('stopped',):
                    raise
                raise BatchStopped('restart_retire_unknown') from error
            batch.checkpoint()
            top = _top(port, binding)
            if top['enter_id']:
                raise BatchStopped('restart_state_unknown')
        if stop():
            raise BatchStopped('stopped')
        _archive(recovery, retiring, top)
        _archive(journal, pending, top)
