"""One explicit additional budget after the documented wrong-character trial."""
import hashlib
import json
import os
import re
import time
from uuid import uuid4

FILE_NAME = 'corrected-team-authorization.json'


def _prior_history(journal, events, account):
    from .guide_trial import STAGE, is_user_exit_history
    if len(events) < 6:
        raise ValueError('guide_trial_authorization_history_missing')
    old_team = {129701,116701,122601,135501,104701}
    for number, (intent, result) in enumerate(zip(events[:6:2], events[1:6:2]), 1):
        ids = intent.get('team_evidence', {}).get('unit_ids', [])
        if (intent.get('operation') != 'guide_trial' or intent.get('stage') != STAGE
                or intent.get('account_id') != account or intent.get('attempt') != number
                or type(ids) is not list or len(ids) != 5 or set(ids) != old_team
                or not (is_user_exit_history(result, intent) or result.get('state') == 'settled'
                    and result.get('result_verified') is True and result.get('outcome') == 'defeat'
                    and all(result.get(k) == intent.get(k) for k in ('intent_id','account_id','attempt','stage')))):
            raise ValueError('guide_trial_authorization_history_invalid')
    raw = b''.join(journal.path.read_bytes().splitlines(keepends=True)[:6])
    return hashlib.sha256(raw).hexdigest()


def read_authorization(journal, events, plan):
    from .guide_trial import LIMIT, TEAM, STAGE
    path = journal.directory / FILE_NAME
    if not path.exists():
        return None
    if path.stat().st_size > 8192:
        raise ValueError('guide_trial_authorization_invalid')
    grant = json.loads(path.read_text(encoding='utf-8'))
    expected = dict(version=1, account_id=plan['account_id'], server=plan['server'], stage=STAGE,
        unit_ids=list(TEAM), source_image_sha256=plan['source_image_sha256'], additional_attempts=LIMIT,
        historical_attempts=3, history_events=6, history_sha256=_prior_history(journal, events, plan['account_id']))
    if (type(grant) is not dict or set(grant) != set(expected) | {'authorization_id','created_at','confirmation'}
            or any(grant.get(k) != v or type(grant.get(k)) is not type(v) for k,v in expected.items())
            or type(grant['authorization_id']) is not str or re.fullmatch('[0-9a-f]{32}',grant['authorization_id']) is None
            or type(grant['created_at']) not in (int,float) or not 0 < grant['created_at'] <= time.time()
            or type(grant['confirmation']) is not dict
            or set(grant['confirmation']) != {'source','text','preserve_history','stop_on_unknown'}
            or grant['confirmation']['source'] != 'direct_user_message'
            or grant['confirmation']['preserve_history'] is not True or grant['confirmation']['stop_on_unknown'] is not True
            or type(grant['confirmation']['text']) is not str or not 1 <= len(grant['confirmation']['text'].strip()) <= 2000):
        raise ValueError('guide_trial_authorization_invalid')
    for number, intent in enumerate(events[6::2], 1):
        if (intent.get('account_id') != plan['account_id'] or intent.get('authorization_id') != grant['authorization_id']
                or intent.get('authorization_attempt') != number or intent.get('attempt') != number + 3 or number > LIMIT):
            raise ValueError('guide_trial_authorization_binding_invalid')
    return grant


def authorize_corrected_team(journal, plan, confirmation):
    """Maintenance only, after a new direct user grant; never called by Start or retry."""
    from .guide_trial import validate_plan, TEAM, LIMIT, STAGE
    from .guide_identity import require_source_team
    plan = validate_plan(plan)
    require_source_team(plan)
    with journal.locked():
        events = journal.events()
        if len(events) != 6:
            raise ValueError('guide_trial_authorization_requires_original_three')
        history = _prior_history(journal, events, plan['account_id'])
        grant = dict(version=1, authorization_id=uuid4().hex, created_at=time.time(), account_id=plan['account_id'],
            server=plan['server'], stage=STAGE, unit_ids=list(TEAM), source_image_sha256=plan['source_image_sha256'],
            additional_attempts=LIMIT, historical_attempts=3, history_events=6, history_sha256=history,
            confirmation=confirmation)
        # Validate the confirmation before any durable change; the grant itself is create-once.
        if (type(confirmation) is not dict or set(confirmation) != {'source','text','preserve_history','stop_on_unknown'}
                or confirmation['source'] != 'direct_user_message' or confirmation['preserve_history'] is not True
                or confirmation['stop_on_unknown'] is not True or type(confirmation['text']) is not str
                or not 1 <= len(confirmation['text'].strip()) <= 2000):
            raise ValueError('guide_trial_authorization_confirmation_required')
        with (journal.directory / FILE_NAME).open('x', encoding='utf-8') as stream:
            json.dump(grant, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        return read_authorization(journal, events, plan)
