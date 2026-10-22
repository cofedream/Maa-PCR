"""Explicit, single-use recovery of the first paused Phoenix attempt."""
import hashlib
import json
import os
import re
import time

FILE_NAME = 'paused-resume-authorization.json'


def _victory_prefix(intent_id):
    if type(intent_id) is not str or re.fullmatch('[0-9a-f]{32}', intent_id) is None:
        raise ValueError('guide_trial_victory_target_invalid')
    return 'victory-recovery-' + intent_id


def _legacy_victory_claimed(journal, intent_id):
    path = journal.directory / 'victory-recovery-claimed.json'
    if not path.exists():
        return False
    if path.stat().st_size > 16384:
        raise ValueError('guide_trial_victory_authorization_invalid')
    value = json.loads(path.read_text(encoding='utf-8'))
    if type(value) is not dict:
        raise ValueError('guide_trial_victory_authorization_invalid')
    return value.get('intent_id') == intent_id


def _victory_binding(journal, events, plan):
    from .guide_trial import team_matches
    intent = events[-1] if events else {}
    ready = intent.get('team_evidence', {})
    if (len(events) % 2 != 1 or intent.get('state') != 'in_flight'
            or intent.get('operation') != 'guide_trial' or intent.get('stage') != plan['stage']
            or intent.get('account_id') != plan['account_id']
            or intent.get('source_hash') != plan['source_hash']
            or not team_matches(ready, plan)
            or ready.get('stage') != plan['stage'] or ready.get('account_id') != plan['account_id']):
        raise ValueError('guide_trial_victory_target_invalid')
    return dict(version=1, intent_id=intent['intent_id'], plan=plan,
                ledger_sha256=hashlib.sha256(journal.path.read_bytes()).hexdigest())


def authorize_victory_recovery(journal, plan, frame, text):
    return authorize_result_recovery(journal, plan, frame, text, outcome='victory')


def authorize_result_recovery(journal, plan, frame, text, *, outcome):
    """Explicit maintenance for an observed result; never authorizes a start."""
    from .guide_trial import validate_plan
    from .guide_identity import require_source_team
    plan = validate_plan(plan)
    require_source_team(plan)
    if outcome not in ('victory','defeat') or type(text) is not str or not 1 <= len(text.strip()) <= 2000:
        raise ValueError('guide_trial_victory_confirmation_required')
    with journal.locked():
        binding = _victory_binding(journal, journal.events(), plan)
        if outcome == 'victory' and _legacy_victory_claimed(journal, binding['intent_id']):
            raise FileExistsError('guide_trial_victory_already_claimed')
        raw = frame.read_bytes()
        if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('guide_trial_victory_frame_invalid')
        prefix = _victory_prefix(binding['intent_id'])
        if outcome == 'defeat':
            prefix = prefix.replace('victory-', 'defeat-', 1)
            binding['outcome'] = outcome
        reference = journal.directory / (prefix + '-reference.png')
        with reference.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        value = dict(binding, frame_sha256=hashlib.sha256(raw).hexdigest(),
                     confirmation=dict(source='direct_user_message', text=text,
                         same_battle=True, no_new_battle=True, preserve_attempts=True))
        _write_once(journal.directory / (prefix + '-authorization.json'), value)
        return value


def claim_victory_recovery(journal, events, plan):
    return _claim_result_recovery(journal, events, plan, outcome='victory')


def claim_result_recovery(journal, events, plan):
    prefix = _victory_prefix(events[-1].get('intent_id')).replace('victory-', 'defeat-', 1)
    if (journal.directory / (prefix + '-authorization.json')).exists():
        return _claim_result_recovery(journal, events, plan, outcome='defeat')
    return claim_victory_recovery(journal, events, plan)


def _claim_result_recovery(journal, events, plan, *, outcome):
    prefix = _victory_prefix(events[-1].get('intent_id'))
    if outcome == 'defeat':
        prefix = prefix.replace('victory-', 'defeat-', 1)
    if outcome == 'victory' and _legacy_victory_claimed(journal, events[-1]['intent_id']):
        return None
    path = journal.directory / (prefix + '-authorization.json')
    if not path.exists() and outcome == 'victory':
        prefix = 'victory-recovery'  # Preserve previously issued requests and claims in place.
        path = journal.directory / (prefix + '-authorization.json')
    claimed = journal.directory / (prefix + '-claimed.json')
    if not path.exists() or claimed.exists():
        return None
    if path.stat().st_size > 16384:
        raise ValueError('guide_trial_victory_authorization_invalid')
    value = json.loads(path.read_text(encoding='utf-8'))
    expected = _victory_binding(journal, events, plan)
    if outcome == 'defeat':
        expected['outcome'] = outcome
    reference = journal.directory / (prefix + '-reference.png')
    confirmation = value.get('confirmation', {}) if type(value) is dict else {}
    if (type(value) is not dict or set(value) != set(expected) | {'frame_sha256','confirmation'}
            or any(value.get(k) != v for k,v in expected.items())
            or value['frame_sha256'] != hashlib.sha256(reference.read_bytes()).hexdigest()
            or type(confirmation) is not dict
            or set(confirmation) != {'source','text','same_battle','no_new_battle','preserve_attempts'}
            or confirmation['source'] != 'direct_user_message'
            or any(confirmation[k] is not True for k in ('same_battle','no_new_battle','preserve_attempts'))
            or type(confirmation['text']) is not str or not 1 <= len(confirmation['text'].strip()) <= 2000):
        raise ValueError('guide_trial_victory_authorization_invalid')
    _write_once(claimed, dict(intent_id=value['intent_id'], claimed_at=time.time(),
                             authorization_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return dict(value, reference_path=str(reference), outcome=outcome)


def claim_completed_return(journal, result, plan):
    """Explicit maintenance request for reward navigation; never grants another battle."""
    intent_id = result.get('intent_id')
    if type(intent_id) is not str or re.fullmatch('[0-9a-f]{32}', intent_id) is None:
        raise ValueError('guide_trial_return_request_invalid')
    prefix = 'completed-return-' + intent_id
    if (journal.directory / (prefix + '-claimed.json')).exists():
        return False
    legacy_claimed = journal.directory / 'completed-return-claimed.json'
    if legacy_claimed.exists():
        if legacy_claimed.stat().st_size > 8192:
            raise ValueError('guide_trial_return_request_invalid')
        value = json.loads(legacy_claimed.read_text(encoding='utf-8'))
        if type(value) is not dict:
            raise ValueError('guide_trial_return_request_invalid')
        if value.get('intent_id') == intent_id:
            return False
    path = journal.directory / (prefix + '-request.json')
    if not path.exists():
        prefix = 'completed-return'  # Preserve previously issued requests and claims in place.
        path = journal.directory / (prefix + '-request.json')
    claimed = journal.directory / (prefix + '-claimed.json')
    if not path.exists() or claimed.exists():
        return False
    if path.stat().st_size > 8192:
        raise ValueError('guide_trial_return_request_invalid')
    request = json.loads(path.read_text(encoding='utf-8'))
    expected = dict(intent_id=result['intent_id'], account_id=plan['account_id'],
        source_hash=result['source_hash'], ledger_sha256=hashlib.sha256(journal.path.read_bytes()).hexdigest())
    if (type(request) is not dict or set(request) != set(expected) | {'confirmation'}
            or any(request.get(k) != v for k,v in expected.items())
            or plan['source_hash'] != result['source_hash']
            or result.get('account_id') != plan['account_id'] or result.get('stage') != plan['stage']
            or journal.events()[-1] != result
            or result.get('state') != 'settled' or result.get('outcome') != 'victory'
            or result.get('result_verified') is not True
            or type(request['confirmation']) is not dict
            or set(request['confirmation']) != {'source','text'}
            or request['confirmation']['source'] != 'direct_user_message'
            or type(request['confirmation']['text']) is not str
            or not 1 <= len(request['confirmation']['text'].strip()) <= 2000):
        raise ValueError('guide_trial_return_request_invalid')
    _write_once(claimed, dict(intent_id=result['intent_id'], claimed_at=time.time(),
        request_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return True


def _binding(journal, events, plan):
    from .guide_trial import team_matches
    from .guide_trial_authorization import read_authorization
    grant = read_authorization(journal, events, plan)
    intent = events[-1] if events else {}
    ready = intent.get('team_evidence', {})
    if (not grant or len(events) != 7 or intent.get('state') != 'in_flight'
            or intent.get('attempt') != 4 or intent.get('authorization_attempt') != 1
            or intent.get('source_hash') != plan['source_hash']
            or plan['version'] != 2 or not team_matches(ready, plan)
            or ready.get('account_id') != plan['account_id'] or ready.get('stage') != plan['stage']):
        raise ValueError('guide_trial_resume_target_invalid')
    return dict(version=1, intent_id=intent['intent_id'], authorization_id=grant['authorization_id'],
        plan=plan, ledger_sha256=hashlib.sha256(journal.path.read_bytes()).hexdigest())


def _write_once(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def authorize_resume(journal, plan, frame, text):
    """Maintenance only following explicit user confirmation of this same paused battle."""
    from .guide_trial import validate_plan
    from .guide_identity import require_source_team
    plan = validate_plan(plan)
    require_source_team(plan)
    if type(text) is not str or not 1 <= len(text.strip()) <= 2000:
        raise ValueError('guide_trial_resume_confirmation_required')
    with journal.locked():
        binding = _binding(journal, journal.events(), plan)
        raw = frame.read_bytes()
        if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('guide_trial_resume_frame_invalid')
        reference = journal.directory / 'paused-resume-reference.png'
        with reference.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        value = dict(binding, frame_sha256=hashlib.sha256(raw).hexdigest(), created_at=time.time(),
            confirmation=dict(source='direct_user_message', text=text, same_battle=True,
                preserve_attempts=True, stop_on_unknown=True))
        _write_once(journal.directory / FILE_NAME, value)
        return value


def claim_resume(journal, events, plan):
    """Called under the journal lock; a crash after claiming never repeats recovery."""
    path = journal.directory / FILE_NAME
    claimed = journal.directory / 'paused-resume-claimed.json'
    if not path.exists() or claimed.exists():
        return None
    if path.stat().st_size > 16384:
        raise ValueError('guide_trial_resume_authorization_invalid')
    value = json.loads(path.read_text(encoding='utf-8'))
    expected = _binding(journal, events, plan)
    reference = journal.directory / 'paused-resume-reference.png'
    confirmation = value.get('confirmation', {}) if type(value) is dict else {}
    if (type(value) is not dict or set(value) != set(expected) | {'frame_sha256','created_at','confirmation'}
            or any(value.get(k) != v for k,v in expected.items())
            or value['frame_sha256'] != hashlib.sha256(reference.read_bytes()).hexdigest()
            or type(value['created_at']) not in (int,float) or not 0 < value['created_at'] <= time.time()
            or type(confirmation) is not dict
            or set(confirmation) != {'source','text','same_battle','preserve_attempts','stop_on_unknown'}
            or confirmation['source'] != 'direct_user_message' or confirmation['same_battle'] is not True
            or confirmation['preserve_attempts'] is not True or confirmation['stop_on_unknown'] is not True
            or type(confirmation['text']) is not str or not 1 <= len(confirmation['text'].strip()) <= 2000):
        raise ValueError('guide_trial_resume_authorization_invalid')
    _write_once(claimed, dict(intent_id=value['intent_id'], claimed_at=time.time(),
        authorization_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return dict(value, reference_path=str(reference))
