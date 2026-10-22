"""Explicit observation of progressed game state; no battle or victory authority."""
import hashlib
import json
import math
from pathlib import Path
import re
import time


def stage_order(stage):
    match = re.fullmatch(r'红焰深域 ([1-9][0-9]?)-(10|[1-9])', stage) if type(stage) is str else None
    if match is None:
        raise ValueError('guide_progress_stage_invalid')
    return tuple(int(value) for value in match.groups())


def _observation_binding(journal, events, plan):
    intent = events[-1] if events else {}
    if (len(events) % 2 != 1 or intent.get('state') != 'in_flight'
            or intent.get('operation') != 'guide_trial'
            or any(intent.get(key) != plan.get(key) for key in ('account_id', 'stage', 'source_hash'))
            or type(intent.get('intent_id')) is not str
            or re.fullmatch('[0-9a-f]{32}', intent['intent_id']) is None):
        raise ValueError('guide_progress_pending_binding_invalid')
    return dict(version=1, intent_id=intent['intent_id'], plan=plan,
                ledger_sha256=hashlib.sha256(journal.path.read_bytes()).hexdigest())


def _valid_confirmation(value, stage):
    if (type(value) is not dict or set(value) != {'source', 'text', 'navigation_only', 'reported_completed_stage'}
            or value.get('source') != 'direct_user_message' or value.get('navigation_only') is not True
            or type(value.get('text')) is not str or not 1 <= len(value['text'].strip()) <= 4000):
        return False
    try:
        return stage_order(value['reported_completed_stage']) >= stage_order(stage)
    except ValueError:
        return False


def authorize_progress_observation(journal, plan, *, text, reported_completed_stage):
    """Maintenance request only; the existing Start/Owner must perform navigation."""
    from .guide_trial import validate_plan
    from .guide_trial_resume import _write_once
    plan = validate_plan(plan)
    confirmation = dict(source='direct_user_message', text=text, navigation_only=True,
                        reported_completed_stage=reported_completed_stage)
    if not _valid_confirmation(confirmation, plan['stage']):
        raise ValueError('guide_progress_confirmation_invalid')
    with journal.locked():
        binding = _observation_binding(journal, journal.events(), plan)
        value = dict(binding, confirmation=confirmation)
        _write_once(journal.directory / f"progress-observation-{binding['intent_id']}.json", value)
        return value


def claim_progress_observation(journal, events, plan):
    from .guide_trial_resume import _write_once
    intent_id = events[-1].get('intent_id', '')
    if type(intent_id) is not str or re.fullmatch('[0-9a-f]{32}', intent_id) is None:
        raise ValueError('guide_progress_pending_binding_invalid')
    request = journal.directory / f'progress-observation-{intent_id}.json'
    claimed = journal.directory / f'progress-observation-{intent_id}-claimed.json'
    if not request.exists() or claimed.exists():
        return None
    if request.stat().st_size > 16384:
        raise ValueError('guide_progress_request_invalid')
    value = json.loads(request.read_text(encoding='utf-8'))
    expected = _observation_binding(journal, events, plan)
    if (type(value) is not dict or set(value) != set(expected) | {'confirmation'}
            or any(value.get(key) != field for key, field in expected.items())
            or not _valid_confirmation(value['confirmation'], plan['stage'])):
        raise ValueError('guide_progress_request_invalid')
    _write_once(claimed, dict(intent_id=intent_id, claimed_at=time.time(),
                request_sha256=hashlib.sha256(request.read_bytes()).hexdigest()))
    return value


def is_progress_history(event, intent):
    """A later map can forbid replay; it cannot determine the old battle result."""
    evidence = event.get('evidence', {})
    review = evidence.get('map_review', {}) if type(evidence) is dict else {}
    try:
        completed = stage_order(review.get('completed_stage'))
        following = (completed[0], completed[1] + 1) if completed[1] < 10 else (completed[0] + 1, 1)
        return (intent.get('state') == 'in_flight' and intent.get('operation') == 'guide_trial'
            and type(intent.get('attempt')) is int and intent['attempt'] > 0
            and type(intent.get('account_id')) is str and re.fullmatch('[1-9][0-9]{4,19}', intent['account_id']) is not None
            and type(intent.get('intent_id')) is str and re.fullmatch('[0-9a-f]{32}', intent['intent_id']) is not None
            and event.get('state') == 'historical_unknown'
            and event.get('recovery_scope') == 'guide_trial_external_progress'
            and event.get('resolution') == 'later_map_progress_observed'
            and event.get('outcome') == 'unknown' and event.get('result_verified') is False
            and event.get('settings_verified') is False and event.get('settlement_verified') is False
            and event.get('original_result_unknown') is True and event.get('replay_forbidden') is True
            and all(event.get(key) == value for key, value in intent.items() if key != 'state')
            and _valid_confirmation(event.get('confirmation'), intent.get('stage'))
            and event['confirmation']['reported_completed_stage'] == review.get('completed_stage')
            and evidence.get('source') == 'maa_owner_map_observation'
            and evidence.get('account_id') == intent.get('account_id') and evidence.get('scene') == 'GTFireMap'
            and all(type(evidence.get(key)) is str and re.fullmatch('[0-9a-f]{64}', evidence[key]) is not None
                for key in ('frame_sha256', 'receipt_sha256', 'report_sha256', 'ledger_before_sha256',
                            'request_sha256', 'claim_sha256', 'account_receipt_sha256'))
            and review.get('source') == 'assistant_visual_review'
            and review.get('frame_sha256') == evidence['frame_sha256']
            and review.get('completion_marker') == '已完成' and review.get('next_marker') == 'NEXT'
            and stage_order(review.get('next_stage')) == following and completed >= stage_order(intent.get('stage'))
            and all(type(value) in (int, float) and math.isfinite(value) for value in
                (intent.get('at'), evidence.get('observed_at'), event.get('resolved_at')))
            and intent['at'] <= evidence['observed_at'] <= event['resolved_at'])
    except (ValueError, TypeError, AttributeError):
        return False


def _read_json(path, limit):
    if path.stat().st_size > limit:
        raise ValueError('guide_progress_evidence_too_large')
    raw = path.read_bytes()
    return json.loads(raw.decode('utf-8')), hashlib.sha256(raw).hexdigest()


def record_external_progress(journal, *, plan, report_path, map_review):
    """Append maintenance evidence from the existing Owner, without any game port."""
    from .guide_trial import validate_plan
    from .labyrinth.profile import profile_player_id, _root, _word
    plan = validate_plan(plan)
    with journal.locked():
        events = journal.events()
        binding = _observation_binding(journal, events, plan)
        intent = events[-1]
        request_path = journal.directory / f"progress-observation-{binding['intent_id']}.json"
        request, request_hash = _read_json(request_path, 16384)
        claim, claim_hash = _read_json(request_path.with_name(request_path.stem + '-claimed.json'), 16384)
        if (type(request) is not dict or set(request) != set(binding) | {'confirmation'}
                or any(request.get(key) != value for key, value in binding.items())
                or not _valid_confirmation(request.get('confirmation'), plan['stage'])
                or claim.get('intent_id') != intent['intent_id'] or claim.get('request_sha256') != request_hash):
            raise ValueError('guide_progress_request_binding_invalid')
        path = Path(report_path).resolve()
        if (path.name != 'report.json' or re.fullmatch('run-[0-9a-f]{32}', path.parent.name) is None
                or path.parent.parent != (journal.directory.parents[1] / 'supported-daily-work').resolve()):
            raise ValueError('guide_progress_owner_report_required')
        report, report_hash = _read_json(path, 2 * 1024 * 1024)
        if (report.get('selected_modules') != ['guide_trial'] or len(report.get('steps', [])) != 1
                or report['steps'][0].get('name') != 'guide_trial'):
            raise ValueError('guide_progress_report_invalid')
        result = report['steps'][0].get('result', {})
        observation = result.get('progress_observation', {})
        if (result.get('outcome') != 'progress_observed' or result.get('error')
                or result.get('observed_intent_id') != intent['intent_id'] or result.get('stage') != plan['stage']
                or result.get('battles') != [] or result.get('consumption_sent') is not False
                or result.get('settlement_verified') is not False or result.get('settings_verified') is not False
                or result.get('original_result_unknown') is not True
                or observation.get('source') != 'maa_owner_map_observation' or observation.get('scene') != 'GTFireMap'
                or observation.get('account_id') != plan['account_id']
                or any(observation.get(key) is not False for key in ('battle_started', 'result_verified', 'settings_verified'))
                or str(profile_player_id(observation.get('account_evidence'))) != plan['account_id']):
            raise ValueError('guide_progress_observation_invalid')
        frame = Path(observation['frame_path']).resolve()
        receipt_path = Path(observation['receipt_path']).resolve()
        if (frame.parent != path.parent or receipt_path != frame.with_suffix('.json')
                or re.fullmatch(r'[0-9]{3}-GTFireMap\.png', frame.name) is None):
            raise ValueError('guide_progress_frame_binding_invalid')
        receipt, receipt_hash = _read_json(receipt_path, 1024 * 1024)
        if (receipt != observation.get('map_evidence') or receipt_hash != observation.get('receipt_sha256')
                or frame.stat().st_size > 8 * 1024 * 1024
                or hashlib.sha256(frame.read_bytes()).hexdigest() != observation.get('frame_sha256')):
            raise ValueError('guide_progress_frame_changed')
        words = _root(receipt, 'GTFireMap').get('recognition')
        fields = [('GTDeepTitle', [80, 8, 180, 65], '^深域关卡$'),
                  ('GTFireMapLabel', [118, 70, 145, 40], '^红焰深域$'),
                  ('GTMapChallengeCount', [638, 567, 178, 43], '^剩余挑战次数$')]
        if type(words) is not list or len(words) != len(fields):
            raise ValueError('guide_progress_map_unknown')
        for word, (name, roi, pattern) in zip(words, fields):
            _word(word, name, roi, pattern)
        account_files = list(path.parent.glob('[0-9][0-9][0-9]-LabyClientProfileObserved.json'))
        if len(account_files) != 1:
            raise ValueError('guide_progress_account_receipt_missing')
        account_receipt, account_hash = _read_json(account_files[0], 1024 * 1024)
        if account_receipt != observation['account_evidence']:
            raise ValueError('guide_progress_account_receipt_changed')
        observed_at = observation.get('observed_at')
        claimed_at = claim.get('claimed_at')
        if (type(observed_at) not in (int, float) or type(claimed_at) not in (int, float)
                or not math.isfinite(claimed_at) or not intent['at'] <= claimed_at <= observed_at <= time.time()):
            raise ValueError('guide_progress_observation_time_invalid')
        evidence = {key: observation[key] for key in ('source', 'account_id', 'scene', 'observed_at', 'frame_sha256', 'receipt_sha256')}
        evidence.update(report_path=str(path), report_sha256=report_hash, ledger_before_sha256=binding['ledger_sha256'],
                        request_sha256=request_hash, claim_sha256=claim_hash, account_receipt_sha256=account_hash,
                        map_review=map_review)
        event = dict(intent, state='historical_unknown', recovery_scope='guide_trial_external_progress',
            resolution='later_map_progress_observed', outcome='unknown', result_verified=False,
            settings_verified=False, settlement_verified=False, original_result_unknown=True, replay_forbidden=True,
            resolved_at=time.time(), confirmation=request['confirmation'], evidence=evidence)
        if not is_progress_history(event, intent):
            raise ValueError('guide_progress_review_invalid')
        journal.append(event)
        return event
