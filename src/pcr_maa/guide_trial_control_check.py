"""One authorized control check and exit, through the original trial Owner."""
from copy import deepcopy
import hashlib
import json
import os
import re
import time
from .guide_trial_resume import _write_once

SCOPE = 'guide_trial_control_check'
OUTCOME = 'control_checked_exit'


def _path(journal, plan):
    attempts = sum(e.get('state') == 'in_flight' and e.get('stage') == plan['stage']
        and e.get('account_id') == plan['account_id'] for e in journal.events())
    suffix = f'-attempt{attempts+1}' if attempts else ''
    return journal.directory / ('control-check-' + plan['source_hash'].lower() + suffix + '.json')


def authorize_control_check(journal, plan, unit_id, text, *, opening_only=False):
    from .guide_trial import validate_plan, planned_sets
    from .guide_identity import require_source_team
    plan = validate_plan(plan)
    require_source_team(plan)
    if (type(opening_only) is not bool or plan['version'] != 3 or not all(planned_sets(plan).values())
            or type(unit_id) is not int or unit_id not in plan['unit_ids']
            or type(text) is not str or not 1 <= len(text.strip()) <= 2000):
        raise ValueError('guide_control_check_request_invalid')
    with journal.locked():
        if len(journal.events()) % 2:
            raise ValueError('guide_trial_previous_result_unknown')
        value = dict(version=2 if opening_only else 1, plan=plan, unit_id=unit_id,
            ledger_sha256=hashlib.sha256(journal.path.read_bytes() if journal.path.exists() else b'').hexdigest(),
            created_at=time.time(), steps=[] if opening_only else [False,True], auto=True, exit_after=True, max_new_starts=1,
            confirmation=dict(source='direct_user_message', text=text))
        _write_once(_path(journal, plan), value)
        return value


def claim_control_check(journal, plan):
    path = _path(journal, plan)
    if not path.exists():
        if any(e.get('control_check') and e.get('source_hash') == plan['source_hash']
                and e.get('stage') == plan['stage'] and e.get('account_id') == plan['account_id'] for e in journal.events()):
            raise RuntimeError('guide_control_check_already_claimed')
        return None
    claimed = path.with_name(path.stem + '-claimed.json')
    raw, value, request = _read_request(journal, plan, path)
    if claimed.exists():
        continuation = path.with_name(path.stem + '-continue.json')
        continued = path.with_name(path.stem + '-continue-claimed.json')
        if not continuation.exists() or continued.exists():
            raise RuntimeError('guide_control_check_already_claimed')
        if continuation.stat().st_size > 65536:
            raise ValueError('guide_control_continuation_invalid')
        data = continuation.read_bytes(); grant = json.loads(data)
        expected = _continuation_binding(journal, plan, grant.get('stopped_result') if type(grant) is dict else None)
        confirmation = grant.get('confirmation') if type(grant) is dict else None
        if (type(grant) is not dict or set(grant) != set(expected) | {'created_at','confirmation'}
                or any(grant.get(k) != v or type(grant.get(k)) is not type(v) for k,v in expected.items())
                or type(grant['created_at']) not in (int,float) or not 0 <= time.time()-grant['created_at'] <= 86400
                or type(confirmation) is not dict or set(confirmation) != {'source','text'}
                or confirmation['source'] != 'direct_user_message'
                or type(confirmation['text']) is not str or not 1 <= len(confirmation['text'].strip()) <= 2000):
            raise ValueError('guide_control_continuation_invalid')
        request['request_sha256'] = hashlib.sha256(data).hexdigest()
        _write_once(continued, dict(request, claimed_at=time.time(), original_request_sha256=expected['request_sha256']))
        return request
    _write_once(claimed, dict(request, claimed_at=time.time()))
    return request


def _read_request(journal, plan, path):
    from .guide_trial import planned_sets
    if path.stat().st_size > 16384:
        raise ValueError('guide_control_check_request_invalid')
    raw = path.read_bytes()
    value = json.loads(raw)
    confirmation = value.get('confirmation') if type(value) is dict else None
    if (type(value) is not dict or set(value) != {'version','plan','unit_id','ledger_sha256','created_at',
            'steps','auto','exit_after','max_new_starts','confirmation'}
            or type(value['version']) is not int or value['version'] not in (1,2) or value['plan'] != plan
            or plan['version'] != 3 or not all(planned_sets(plan).values())
            or type(value['unit_id']) is not int or value['unit_id'] not in plan['unit_ids']
            or value['ledger_sha256'] != hashlib.sha256(journal.path.read_bytes() if journal.path.exists() else b'').hexdigest()
            or type(value['created_at']) not in (int,float) or not 0 <= time.time() - value['created_at'] <= 86400
            or value['steps'] != ([False,True] if value['version'] == 1 else []) or any(type(v) is not bool for v in value['steps'])
            or value['auto'] is not True or value['exit_after'] is not True
            or type(value['max_new_starts']) is not int or value['max_new_starts'] != 1
            or type(confirmation) is not dict or set(confirmation) != {'source','text'}
            or confirmation['source'] != 'direct_user_message' or type(confirmation['text']) is not str
            or not 1 <= len(confirmation['text'].strip()) <= 2000):
        raise ValueError('guide_control_check_request_invalid')
    request = dict(unit_id=value['unit_id'], request_sha256=hashlib.sha256(raw).hexdigest(),
                   original_sets=planned_sets(plan))
    if value['version'] == 2:
        request['steps'] = []  # Opening correction only; never force a toggle of a correct SET.
    return raw, value, request


def _continuation_binding(journal, plan, stopped):
    from .guide_trial import LIMIT
    path = _path(journal, plan)
    raw, value, request = _read_request(journal, plan, path)
    claimed = path.with_name(path.stem + '-claimed.json')
    if not claimed.exists() or claimed.stat().st_size > 16384:
        raise ValueError('guide_control_continuation_invalid')
    claim_raw = claimed.read_bytes(); claim = json.loads(claim_raw)
    events = journal.events()
    attempts = sum(e.get('state') == 'in_flight' and e.get('stage') == plan['stage']
        and e.get('account_id') == plan['account_id'] for e in events)
    required = dict(outcome='stopped',stage=plan['stage'],attempts_used=0,attempts_total=attempts,
                    attempts_limit=LIMIT,consumption_sent=False,battles=[])
    # The original executor appends an intent before any battle-start input.
    if (value['version'] != 2 or len(events) % 2 or type(claim) is not dict
            or set(claim) != set(request) | {'claimed_at'}
            or any(claim.get(k) != v for k,v in request.items())
            or type(claim['claimed_at']) not in (int,float) or not 0 <= time.time()-claim['claimed_at'] <= 86400
            or type(stopped) is not dict or any(stopped.get(k) != v or type(stopped.get(k)) is not type(v) for k,v in required.items())
            or type(stopped.get('error')) is not str or not stopped['error']):
        raise ValueError('guide_control_continuation_invalid')
    return dict(version=1,plan=plan,request_sha256=hashlib.sha256(raw).hexdigest(),
        claim_sha256=hashlib.sha256(claim_raw).hexdigest(),ledger_sha256=value['ledger_sha256'],stopped_result=stopped)


def authorize_control_continuation(journal, plan, stopped, text):
    """Explicit maintenance after a stop before intent creation; never called by retry."""
    from .guide_trial import validate_plan
    from .guide_identity import require_source_team
    plan = validate_plan(plan); require_source_team(plan)
    if type(text) is not str or not 1 <= len(text.strip()) <= 2000:
        raise ValueError('guide_control_continuation_confirmation_required')
    with journal.locked():
        value = dict(_continuation_binding(journal,plan,stopped),created_at=time.time(),
            confirmation=dict(source='direct_user_message',text=text))
        path = _path(journal,plan)
        _write_once(path.with_name(path.stem + '-continue.json'),value)
        return value


def readings_match(readings, check, ready):
    if (type(ready) is not dict or type(check) is not dict
            or set(check) not in ({'unit_id','request_sha256','original_sets'},
                                  {'unit_id','request_sha256','original_sets','steps'})
            or 'steps' in check and check['steps'] != []):
        return False
    original = check['original_sets']
    if (type(original) is not dict or len(original) != 5 or any(v is not True for v in original.values())
            or set(original) != {str(uid) for uid in ready.get('unit_ids', [])}
            or type(check['unit_id']) is not int or str(check['unit_id']) not in original
            or type(check['request_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}',check['request_sha256'])):
        return False
    if type(readings) is not list or len(readings) != (1 if 'steps' in check else 3):
        return False
    for index, reading in enumerate(readings):
        expected = dict(original)
        if index == 1:
            expected[str(check['unit_id'])] = False
        if (type(reading) is not dict or reading.get('auto') is not True or reading.get('set_by_unit') != expected
                or any(type(v) is not bool for v in reading.get('set_by_unit', {}).values())):
            return False
        if index and reading.get('actions') != [dict(node='GTBattleToggleSet', unit_id=check['unit_id'],
                previous=index == 1, target=index == 2)]:
            return False
    return True


def result_matches(result, check, ready):
    if (type(result) is not dict or not readings_match(result.get('control_readings'),check,ready)
            or result.get('outcome') != OUTCOME or result.get('result_verified') is not False
            or result.get('control_check_verified') is not True or result.get('settings_verified') is not True
            or result.get('exit_verified') is not True or result.get('auto') is not True
            or result.get('set_by_unit') != check['original_sets'] or result.get('unit_id') != check['unit_id']
            or result.get('request_sha256') != check['request_sha256']):
        return False
    exit = result.get('exit_evidence', {})
    return (type(exit) is dict and exit.get('scene') == 'GTFireMap' and exit.get('verified') is True
        and exit.get('stage') == ready.get('stage') and exit.get('account_id') == ready.get('account_id')
        and all(type(exit.get(k)) is str and re.fullmatch('[0-9a-f]{64}',exit[k]) for k in ('frame_sha256','receipt_sha256')))


def _exit_prefix(intent):
    from .guide_trial_resume import _victory_prefix
    return _victory_prefix(intent.get('intent_id')).replace('victory-', 'control-exit-', 1)


def _exit_readings(raw, intent):
    if len(raw) > 131072:
        raise ValueError('guide_control_exit_readings_invalid')
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {'check','readings'}
            or value['check'] != intent.get('control_check')
            or not readings_match(value['readings'],value['check'],intent.get('team_evidence'))):
        raise ValueError('guide_control_exit_readings_invalid')
    return value


def _exit_binding(journal, events, plan):
    from .guide_trial_resume import _victory_binding
    from .guide_trial import settings_plan, planned_sets
    binding = _victory_binding(journal,events,plan)
    if (events[-1]['team_evidence'].get('settings_plan') != settings_plan(plan)
            or events[-1].get('control_check',{}).get('original_sets') != planned_sets(plan)):
        raise ValueError('guide_control_exit_target_invalid')
    return binding


def authorize_control_exit(journal, plan, frame, readings, text):
    """Explicit same-battle recovery; copies existing evidence, never creates a new intent."""
    from .guide_trial import validate_plan
    from .guide_identity import require_source_team
    plan = validate_plan(plan); require_source_team(plan)
    if type(text) is not str or not 1 <= len(text.strip()) <= 2000:
        raise ValueError('guide_control_exit_confirmation_required')
    with journal.locked():
        events = journal.events(); binding = _exit_binding(journal,events,plan)
        snapshot = readings.read_bytes(); _exit_readings(snapshot,events[-1])
        raw = frame.read_bytes()
        if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('guide_control_exit_frame_invalid')
        prefix = _exit_prefix(events[-1])
        for suffix, data in (('-reference.png',raw),('-readings.json',snapshot)):
            with (journal.directory/(prefix+suffix)).open('xb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
        value = dict(binding,frame_sha256=hashlib.sha256(raw).hexdigest(),
            readings_sha256=hashlib.sha256(snapshot).hexdigest(),created_at=time.time(),
            confirmation=dict(source='direct_user_message',text=text,no_manual_intervention=True,
                same_battle=True,no_new_battle=True,preserve_attempts=True))
        _write_once(journal.directory/(prefix+'-authorization.json'),value)
        return value


def claim_control_exit(journal, events, plan):
    prefix = _exit_prefix(events[-1])
    path = journal.directory/(prefix+'-authorization.json')
    claimed = journal.directory/(prefix+'-claimed.json')
    if not path.exists() or claimed.exists():
        return None
    if path.stat().st_size > 16384:
        raise ValueError('guide_control_exit_authorization_invalid')
    raw = path.read_bytes(); value = json.loads(raw)
    expected = _exit_binding(journal,events,plan)
    reference = journal.directory/(prefix+'-reference.png')
    snapshot = (journal.directory/(prefix+'-readings.json')).read_bytes()
    confirmation = value.get('confirmation') if type(value) is dict else None
    if (type(value) is not dict or set(value) != set(expected) | {'frame_sha256','readings_sha256','created_at','confirmation'}
            or any(value.get(k) != v for k,v in expected.items())
            or value['frame_sha256'] != hashlib.sha256(reference.read_bytes()).hexdigest()
            or value['readings_sha256'] != hashlib.sha256(snapshot).hexdigest()
            or type(value['created_at']) not in (int,float) or not 0 <= time.time()-value['created_at'] <= 86400
            or type(confirmation) is not dict
            or set(confirmation) != {'source','text','no_manual_intervention','same_battle','no_new_battle','preserve_attempts'}
            or confirmation['source'] != 'direct_user_message'
            or any(confirmation[k] is not True for k in ('no_manual_intervention','same_battle','no_new_battle','preserve_attempts'))
            or type(confirmation['text']) is not str or not 1 <= len(confirmation['text'].strip()) <= 2000):
        raise ValueError('guide_control_exit_authorization_invalid')
    readings = _exit_readings(snapshot,events[-1])
    _write_once(claimed,dict(intent_id=value['intent_id'],claimed_at=time.time(),
        authorization_sha256=hashlib.sha256(raw).hexdigest()))
    return dict(value,reference_path=str(reference),**readings)


def is_control_check_exit(event, intent):
    return (intent.get('operation') == 'guide_trial' and intent.get('state') == 'in_flight'
        and event.get('state') == 'settled' and event.get('recovery_scope') == SCOPE
        and event.get('outcome') == OUTCOME and event.get('result_verified') is False
        and all(event.get(k) == v for k,v in intent.items() if k not in ('state','at'))
        and result_matches(event.get('result_evidence'), intent.get('control_check'), intent.get('team_evidence', {})))


def check_and_exit(port, plan, check, initial):
    from .guide_trial_battle import ensure_running_settings
    from .supported_daily import _atomic_json
    target = deepcopy(check['original_sets'])
    readings = [ensure_running_settings(port, initial=initial, set_by_unit=target)]
    _atomic_json(port.directory / 'control-check-readings.json', dict(check=check, readings=readings))
    for value in check.get('steps', (False, True)):
        port.checkpoint()
        receipt = port.run('GTBattleReadSettings')
        before = port.read_battle_settings(receipt)
        if any(before.get(k) != readings[-1]['observation'].get(k) for k in ('unit_ids','auto','sets')):
            raise RuntimeError('guide_control_check_state_changed')
        target[str(check['unit_id'])] = value
        reading = ensure_running_settings(port, initial=receipt, set_by_unit=target)
        if reading['actions'] != [dict(node='GTBattleToggleSet',unit_id=check['unit_id'],previous=not value,target=value)]:
            raise RuntimeError('guide_control_check_input_not_verified')
        readings.append(reading)
        _atomic_json(port.directory / 'control-check-readings.json', dict(check=check, readings=readings))
    port.checkpoint()
    exit = port.exit_control_check(plan)
    return control_exit_result(check,readings,exit)


def control_exit_result(check, readings, exit):
    return dict(outcome=OUTCOME, result_verified=False, control_check_verified=True,
        settings_verified=True, exit_verified=True, auto=True, all_set=True, set_by_unit=check['original_sets'],
        unit_id=check['unit_id'], request_sha256=check['request_sha256'],
        control_readings=readings, exit_evidence=exit)
