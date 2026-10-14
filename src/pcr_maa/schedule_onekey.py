"""Bounded native one-key transaction; ordinary gacha is free by user report."""
import hashlib
import math
import re
import time
from contextlib import nullcontext
from contextvars import ContextVar
from datetime import date, datetime
from uuid import uuid4

import cv2
from maa.custom_recognition import CustomRecognition
from maa.define import AlgorithmEnum

from . import native
from .game_day import BEIJING, current_game_day
from .schedule import section_counts
from .onekey_flow import scan_rows, execute_pending
# Public compatibility alias; implementation lives in the history reader.
from .onekey_history import settled_history
# Keep legacy imports bound to the same recognizer and constants.
from .onekey_page import (OneKeyPage, ITEMS, ONEKEY_SCOPE_BLOCKER,
                         ROW_LABELS, HEADERS, SETTINGS, SETTINGS_PASSIVE)

BLOCKERS = ('single_saved_frame_is_not_current_full_scope_or_execution_permission',)
_HISTORY_DAY = ContextVar('onekey_historical_proof_day', default=None)


def observed_date(words, recognize, game_day, *, captured_at=None):
    """Optional same-image evidence; a credible contradiction is never absence."""
    from .schedule_house import words_from, text, one
    parts = [set(), set()]
    complete = set()

    def full(token):
        match = re.fullmatch(r'([0-9]{1,2})[/／]([0-9]{1,2})', token)
        if match:
            md = tuple(map(int, match.groups()))
            try:
                date(2000, *md)
            except ValueError:
                return None
            return md

    def normalized_word(word):
        # Maa may trim OCR text only in filtered/best. Ignore ASCII whitespace,
        # retaining every other field so geometry/confidence cannot drift.
        if (not isinstance(word, dict) or not isinstance(word.get('text'), str)
                or type(word.get('score')) not in (int, float)):
            return None
        return word | {'text': re.sub(r'[ \t\r\n]+', '', word['text'])}

    for word in words:
        x, y, w, h = word['box']
        if word['score'] < .9 or not (260 <= x < x+w <= 345 and 45 <= y < y+h <= 81):
            continue
        token = text(word)
        md = full(token)
        if md:
            complete.add(md)
            for index in (0, 1):
                parts[index].add(md[index])
        elif x+w <= 292 and x < 279 and re.fullmatch(r'[0-9]{1,2}', token):
            parts[0].add(int(token))
        elif x >= 279 and x+w > 292 and re.fullmatch(r'[/／]?[0-9]{1,2}', token):
            parts[1].add(int(token.lstrip('/／')))
    month = one(words, r'\d{1,2}', [260,45,32,36])
    day_word = one(words, r'[/／]\d{1,2}', [279,45,65,36])
    if month and day_word:
        md = full(text(month) + text(day_word))
        if md:
            complete.add(md)
    if not complete:
        # Only this optional recognition may be absent/malformed. Native read
        # failures and malformed full-page OCR remain fatal in the caller.
        try:
            result = recognize('OneKeyDate')
        except ValueError as exc:
            if str(exc) not in ('missing_recognition:OneKeyDate', 'recognition_missing:OneKeyDate'):
                raise
            result = None
        try:
            candidates = words_from(result) if result is not None else []
        except (ValueError, TypeError, AttributeError):
            candidates = []
        if result is not None and result.hit is True:
            for candidate in candidates:
                if candidate['box'] == [260,45,85,36] and candidate['score'] >= .85:
                    normalized = normalized_word(candidate)
                    md = full(normalized['text'])
                    if md:
                        # Even multiple candidates cannot hide a clear wrong day.
                        for index in (0, 1):
                            parts[index].add(md[index])
                        raw = result.raw_detail
                        filtered = raw.get('filtered')
                        if (len(candidates) == 1 and isinstance(filtered, list) and len(filtered) == 1
                                and normalized_word(filtered[0]) == normalized
                                and normalized_word(raw.get('best')) == normalized):
                            complete.add(md)
    requested = date.fromisoformat(game_day) if game_day else None
    compatible = {(requested.month, requested.day)} if requested else set()
    if requested and captured_at is not None and _HISTORY_DAY.get() is None:
        captured = datetime.fromisoformat(captured_at)
        if captured.tzinfo is None or current_game_day(captured) != game_day:
            raise ValueError('game_day_mismatch')
        calendar = captured.astimezone(BEIJING).date()
        # A fresh UI calendar label can differ from the current game day before
        # reset. This explains only this captured calendar date, not other days.
        compatible.add((calendar.month, calendar.day))
    for index, values in enumerate(parts):
        if len(values) > 1:
            raise ValueError('schedule_date_conflict')
        if requested and values:
            compatible = {md for md in compatible if values == {md[index]}}
            if not compatible:
                raise ValueError('game_day_mismatch')
    return list(next(iter(complete))) if len(complete) == 1 else None


class Snapshot(CustomRecognition):
    def __init__(self, game_day):
        super().__init__()
        self.game_day = game_day
        self.observation = dict(page='unknown', reason='no_frame', sections={})
        self.attempted = False

    def analyze(self, context, argv):
        # Even recognition is bounded: never retry a failed/changed frame.
        if self.attempted:
            return None
        self.attempted = True
        try:
            self.read(context, argv.image)
        except Exception as exc:
            self.observation.update(page='unknown', reason=str(exc), sections={})
        return self.AnalyzeResult([0, 0, 1280, 720], self.observation)

    def read(self, context, image):
        path = native.RUNTIME / ('onekey-frame-' + uuid4().hex + '.png')
        if not native.write_png(path, image):
            raise ValueError('cannot_save_source_frame')
        self.observation.update(screenshot=str(path),
            image_sha256=hashlib.sha256(path.read_bytes()).hexdigest())

        def recognize(name):
            result = context.run_recognition(name, image)
            if result is None:
                raise ValueError('recognition_missing:' + name)
            self.observation.setdefault('evidence', {})[name] = dict(
                hit=result.hit, raw=result.raw_detail, reco_id=result.reco_id)
            return result

        result = recognize('OneKeyOCR')
        if result.algorithm != AlgorithmEnum.OCR or not isinstance(result.raw_detail, dict):
            raise ValueError('invalid_ocr_evidence')
        words = result.raw_detail.get('all')
        if not isinstance(words, list) or not words:
            raise ValueError('unreadable_frame')
        for word in words:
            if (not isinstance(word, dict) or not isinstance(word.get('text'), str)
                    or type(word.get('score')) not in (float, int)
                    or not math.isfinite(word['score'])
                    or not isinstance(word.get('box'), list) or len(word['box']) != 4
                    or any(type(n) is not int for n in word['box'])):
                raise ValueError('invalid_ocr_word')
        self.observation['words'] = words
        if recognize('HomeDataConnection').hit:
            raise ValueError('page_connection_in_progress')
        if (not recognize('ScheduleGuard').hit or not recognize('OneKeyLabel').hit
                or not recognize('OneKeyPanelBright').hit):
            raise ValueError('schedule_page_unverified')
        self.observation['visible_month_day'] = observed_date(words, recognize, self.game_day)
        # Reuse strict same-line section attribution; never mix notification 1/5
        # and delegated 9/9, nor carry a header from an earlier screenshot.
        self.observation.update(page='schedule', reason=None,
            sections=section_counts(words), one_key_label_visible=True)


def inspect_frame(tasker=None, *, execute=False, game_day=None, authorizations=(), device=None):
    report = dict(outcome='NOT_EXECUTABLE', executability='NOT_EXECUTABLE',
        blockers=list(BLOCKERS), execute_requested=execute, game_day=game_day,
        authorizations=list(authorizations), authorization_scope='read-only; execute requires fresh full scope and this invocation permission',
        device=device, schedule_executed=False, daily_complete=False,
        business_facts=dict(normal_gacha_free=True, source='user-reported via parent 2026-09-24 clarification',
                            grants_execution_permission=False),
        native_ok=False, observation_complete=False, home_verified=False,
        navigation_complete=False, settlement_verified=False,
        nine_rows_verified=False, row_states={key: 'unknown' for key in ITEMS},
        resources=dict(jewels=None, ap=None, normal_gacha_cost=None),
        action_limits=dict(onekey=0, confirmations=0, close=0, swipes=0),
        observation=dict(page='unknown', reason='not_observed', sections={}), receipts=[])
    if type(execute) is not bool:
        raise ValueError('execute must be boolean')
    if execute:
        # Before accessing tasker, image, device or any existing journal.
        report['blockers'].append('execution_disabled_before_any_device_or_journal_access')
        return report
    if game_day is not None and date.fromisoformat(game_day).isoformat() != game_day:
        raise ValueError('game-day must be YYYY-MM-DD')
    if device is not None or tasker is None:
        report['blockers'].append('saved_frame_required_live_entry_unavailable')
        return report
    reader = Snapshot(game_day)
    resource = tasker.resource
    if not resource.register_custom_recognition('OneKeySnapshot', reader):
        raise RuntimeError('Cannot register one-key snapshot reader')
    try:
        receipt = native.run(tasker, 'OneKeyPreflight')
        report['receipts'].append(receipt)
        report['native_ok'] = receipt['native_ok']
        report['observation_complete'] = receipt['native_ok'] and reader.observation['page'] == 'schedule'
        report['observation'] = reader.observation
    except Exception as exc:
        report['observation'] = reader.observation | dict(page='unknown', reason=str(exc), sections={})
    finally:
        if not resource.unregister_custom_recognition('OneKeySnapshot'):
            raise RuntimeError('Cannot release one-key snapshot reader')
    return report


def now_cn():
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=8)))


def check_day(game_day):
    if _HISTORY_DAY.get() is not None:
        if game_day != _HISTORY_DAY.get() or date.fromisoformat(game_day).isoformat() != game_day:
            raise ValueError('historical_proof_day_mismatch')
        return
    current = current_game_day(now_cn())
    if (not game_day or date.fromisoformat(game_day).isoformat() != game_day
            or current != game_day):
        raise ValueError('current_game_day_required')


def validate_execution(game_day, device, authorizations, *, portable=False):
    from .device import fixed_config, validate_serial, SERIAL
    check_day(game_day)
    if portable is True and isinstance(device, dict) and ('mumu' in device or 'manager' in device):
        from .onekey_history import validate_stable_device
        validate_stable_device(device)
    else:
        validate_fixed_device(device, portable=portable)
    if (not isinstance(authorizations, (tuple, list)) or not authorizations
            or len(set(authorizations)) != len(authorizations) or set(authorizations) - set(ITEMS)):
        raise ValueError('explicit_unique_item_authorizations_required')


def validate_fixed_device(device, *, portable=False):
    from .device import fixed_config, validate_serial, SERIAL
    if (not isinstance(device, dict) or set(device) != {'serial', 'input_display', 'screenshot_display'}
            or type(device['input_display']) is not int or not 0 <= device['input_display'] <= 255
            or (not portable and device['input_display'] != 2)):
        raise ValueError('fixed_device_display_required')
    validate_serial(device['serial'])
    # Frozen incident verifiers call the original three-argument interface.
    # Its fixed serial/display remain unchanged; only current production opts in.
    if type(portable) is not bool or (not portable and device['serial'] != SERIAL):
        raise ValueError('historical_fixed_device_required')
    fixed_config(device['input_display'], device['screenshot_display'])


NONCONSUMING_TARGET = {'enter': 'schedule', 'settings_open': 'settings',
    'settings_down': 'settings', 'settings_close': 'schedule',
    'down': 'schedule', 'up': 'schedule', 'close': 'home'}


def observe_nonconsuming(reader, action, before, read_once, deadline):
    """Called after native input completion; only read until verified readiness."""
    base = action.split(':')[0]
    expected = NONCONSUMING_TARGET[base]
    source = {'enter': 'home', 'settings_open': 'schedule',
              'settings_close': 'settings', 'close': 'schedule'}.get(base, expected)
    limit = min(deadline, time.monotonic() + 12)
    last_index = max(before['capture_index'], getattr(reader, 'capture_index', 0))
    last_at = before.get('captured_at')
    latest = getattr(reader, 'last', None) or {}
    if latest.get('session_id') == reader.session_id and latest.get('capture_index') == last_index:
        last_at = latest.get('captured_at', last_at)
    transient = {'unverified:ScheduleGuard', 'unverified:OneKeyLabel',
                 'unverified:OneKeyPanelBright', 'unverified:OneKeySettingsTitle',
                 'unverified:OneKeySettingsClose', 'unverified:OneKeySettingsBright',
                 'schedule_date_unreadable', 'unreadable_home_resource'}
    for attempt in range(4):
        if time.monotonic() >= limit:
            raise ValueError('audit_transition_read_budget_exhausted')
        page = read_once(limit=limit)
        if time.monotonic() >= limit:
            raise ValueError('audit_transition_read_budget_exhausted')
        if (page.get('session_id') != reader.session_id
                or page.get('capture_index', 0) <= last_index
                or not page.get('image_sha256')):
            raise ValueError('audit_transition_stale_or_foreign_frame')
        captured_at = page.get('captured_at')
        if last_at is not None and (captured_at is None
                or datetime.fromisoformat(captured_at) < datetime.fromisoformat(last_at)):
            raise ValueError('audit_transition_stale_or_foreign_frame')
        last_at = captured_at
        last_index = page['capture_index']
        # Unchanged pixels are lack of progress, not stale capture identity.
        # Same-page scrolling still needs progress; its caller verifies rows.
        if page['state'] == expected and (source != expected
                or page['image_sha256'] != before['image_sha256']):
            return page
        if page['state'] == 'unknown':
            reason = page.get('reason')
            if reason not in transient:
                raise ValueError(reason or 'audit_transition_unknown')
        elif page['state'] != source or before.get('state') != source:
            raise ValueError('audit_transition_unexpected_page:' + action + ':' + page['state'])
        if attempt < 3:
            time.sleep(min(.2, max(0, limit - time.monotonic())))
    raise ValueError('audit_transition_read_budget_exhausted')


def post_consumer_targets(pending, *, after=None):
    """The next reachable batch page, derived from this invocation's inventory."""
    stages = [('dungeon_skip', 'dungeon'), ('mana_skip', 'mana'), ('exp_skip', 'exp')]
    if after is not None:
        stages = stages[next(i for i, (_, stage) in enumerate(stages) if stage == after) + 1:]
    for item, stage in stages:
        if item in pending:
            return (stage + '_confirm',)
    if 'guild_like' in pending:
        return ('like_result',)
    # Reward presentation is optional; the subsequent full scan proves completion.
    return ('item_result', 'schedule')


def observe_post_consumer(reader, action, before, read_once, deadline, targets):
    """Read only after one completed dispatch; never retry a consumer or dismiss UI.

    Connection overlays, the unchanged source page, or unverified:ScheduleGuard
    after a completed mana/exp confirmation may wait without new input. Other
    unknowns and modal pages fail closed. The existing total deadline and a
    12-second/four-read sub-budget also cover native work, not just sleeps.
    """
    limit = min(deadline, time.monotonic() + 12)
    last_index = max(before['capture_index'], reader.capture_index)
    latest = reader.last or before
    last_at = latest.get('captured_at')
    for attempt in range(4):
        if time.monotonic() >= limit:
            raise ValueError('post_consumer_read_budget_exhausted')
        page = read_once(limit=limit)
        if time.monotonic() >= limit:
            raise ValueError('post_consumer_read_budget_exhausted')
        captured_at = page.get('captured_at')
        if (page.get('session_id') != reader.session_id
                or page.get('acquisition') != 'current_tasker_capture'
                or type(page.get('capture_index')) is not int
                or page['capture_index'] <= last_index or not page.get('image_sha256')
                or not captured_at or not last_at
                or datetime.fromisoformat(captured_at) < datetime.fromisoformat(last_at)):
            raise ValueError('post_consumer_stale_or_foreign_frame')
        last_index, last_at = page['capture_index'], captured_at
        if page['state'] in targets and (page['state'] != before['state']
                or reader.signature(page) != reader.signature(before)):
            return page
        if page['state'] == 'unknown':
            if (page.get('reason') != 'page_connection_in_progress'
                    and not (action in ('mana_confirm', 'exp_confirm') and before['state'] == action
                             and page.get('reason') == 'unverified:ScheduleGuard')):
                raise ValueError(page.get('reason') or 'post_consumer_unknown')
        elif reader.signature(page) != reader.signature(before):
            raise ValueError('post_consumer_unexpected_page:' + action + ':' + page['state'])
        if attempt < 3:
            time.sleep(min(.2, max(0, limit - time.monotonic())))
    raise ValueError('post_consumer_read_budget_exhausted')


def collect_settings(page, reader, send, game_day, device, report):
    """Collect current-session radio proof and return to the exact schedule page."""
    if page['state'] != 'schedule':
        raise ValueError('current_schedule_required_before_settings')
    schedule_signature = reader.signature(page)
    page = send('settings_open', page)
    audit = dict(session_id=reader.session_id, game_day=game_day, device=device,
                 rows={}, frames=[], verified=False)
    report['settings_audit'] = audit
    for index in range(26):
        if (page['state'] != 'settings' or page['session_id'] != reader.session_id
                or (index == 0 and not page['settings_top'])):
            raise ValueError('fresh_settings_top_or_session_unverified')
        if any(f['image_sha256'] == page['image_sha256'] for f in audit['frames']):
            raise ValueError('settings_no_progress_or_frozen_capture')
        audit['frames'].append({k: page[k] for k in
            ('screenshot', 'image_sha256', 'session_id', 'capture_index', 'captured_at')})
        for label, value in page['settings'].items():
            if label in audit['rows'] and audit['rows'][label] != value:
                raise ValueError('settings_changed_during_scan')
            audit['rows'][label] = value
        if set(audit['rows']) == set(SETTINGS):
            break
        if index == 25:
            raise ValueError('settings_coverage_incomplete:' + ','.join(sorted(set(SETTINGS)-set(audit['rows']))))
        page = send('settings_down:' + str(index), page)
    page = send('settings_close', page)
    if reader.signature(page) != schedule_signature:
        raise ValueError('schedule_changed_after_settings_return')
    audit['verified'] = True
    return page, audit


def schedule_settings_audit(tasker=None, *, audit=False, game_day=None, device=None,
                            start='home', source='offline SavedImages'):
    """Standalone, read-only settings audit; its proof cannot authorize one-key."""
    from .schedule_house import completed
    report = dict(outcome='unknown', source=source, game_day=game_day, device=device, start=start,
                  daily_complete=False, schedule_executed=False, one_key_attempted=False,
                  home_verified=False, settings_audit=dict(complete=False, grants_execution_permission=False),
                  sent=[], receipts=[])
    if start == 'schedule':
        report['home_before'] = None
    try:
        if audit is not True:
            raise ValueError('explicit_audit_required')
        if start not in ('home', 'schedule'):
            raise ValueError('audit_start_must_be_home_or_schedule')
        from .device import SERIAL, fixed_config
        check_day(game_day)
        if (not isinstance(device, dict) or set(device) != {'serial', 'input_display', 'screenshot_display'}
                or device['serial'] != SERIAL):
            raise ValueError('fixed_device_display_required')
        fixed_config(device['input_display'], device['screenshot_display'])
        device_signature = tuple(device[key] for key in ('serial', 'input_display', 'screenshot_display'))
    except (ValueError, TypeError) as exc:
        report.update(outcome='not_authorized', reason=str(exc))
        return report
    reader = OneKeyPage(game_day, allow_home_banner=True)
    deadline = time.monotonic() + 180
    registered = False

    def check():
        check_day(game_day)
        if tuple(device[key] for key in ('serial', 'input_display', 'screenshot_display')) != device_signature:
            raise ValueError('audit_device_binding_changed')
        if time.monotonic() >= deadline:
            raise ValueError('settings_audit_budget_exhausted')

    def run(entry, *, limit=None):
        check()
        read_deadline = min(deadline, limit) if limit is not None else deadline
        if time.monotonic() >= read_deadline:
            raise ValueError('audit_transition_read_budget_exhausted')
        token = native.RUN_DEADLINE.set(min(native.RUN_DEADLINE.get(), read_deadline))
        try:
            result = native.run(tasker, entry)
        finally:
            native.RUN_DEADLINE.reset(token)
        report['receipts'].append(dict(entry=entry, native=result, reading=reader.last))
        return result

    def read_once(*, limit=None):
        reader.last = None
        result = run('OneKeyExecRead', limit=limit)
        if not completed(result, 'OneKeyExecSnapshot') or not reader.last:
            raise ValueError((reader.last or {}).get('reason', 'capture_or_native_read_failed'))
        return reader.last

    def read():
        page = read_once()
        if page['state'] == 'unknown':
            raise ValueError(page.get('reason') or 'unverified_read')
        return page

    def send(action, before):
        check()
        base = action.split(':')[0]
        if base not in {'enter', 'settings_open', 'settings_down', 'settings_close', 'close'}:
            raise ValueError('audit_action_not_allowed')
        if action in report['sent']:
            raise ValueError('duplicate_action_blocked')
        report['sent'].append(action)
        reader.expected = reader.signature(before)
        reader.allowed_action = base
        if not completed(run('OneKeyDo_' + base), 'OneKeyInput_' + base):
            raise ValueError('input_unverified_no_retry:' + action)
        return observe_nonconsuming(reader, action, before, read_once, deadline)

    try:
        if tasker is None:
            raise ValueError('bound_tasker_required')
        if not tasker.resource.register_custom_recognition('OneKeyPage', reader):
            raise ValueError('cannot_register_onekey_reader')
        registered = True
        page = read()
        if start == 'home':
            if page['state'] != 'home':
                raise ValueError('verified_home_start_required')
            report['home_before'] = {k: page[k] for k in
                ('screenshot', 'image_sha256', 'capture_index', 'captured_at', 'resources')}
            page = send('enter', page)
        else:
            if page['state'] != 'schedule' or page.get('session_id') != reader.session_id:
                raise ValueError('verified_schedule_start_required')
        page, evidence = collect_settings(page, reader, send, game_day, device, report)
        report['settings_audit'] = evidence | dict(complete=True, grants_execution_permission=False)
        page = send('close', page)
        if page['state'] != 'home':
            raise ValueError('final_home_not_verified')
        report['home_after'] = {k: page[k] for k in
            ('screenshot', 'image_sha256', 'capture_index', 'captured_at', 'resources')}
        report.update(outcome='audited', home_verified=True)
    except (Exception, KeyboardInterrupt) as exc:
        report.update(outcome='unknown', reason=str(exc) or 'interrupted_no_retry')
    finally:
        report['settings_audit'].setdefault('complete', False)
        report['settings_audit'].setdefault('grants_execution_permission', False)
        if registered and not tasker.resource.unregister_custom_recognition('OneKeyPage'):
            report.update(outcome='unknown', reason='reader_release_failed')
    return report


def schedule_onekey(tasker=None, *, execute=False, game_day=None, authorizations=(), device=None, journal=None,
                    attest_current_plan=False, invocation_id=None, gui_start_intent=False,
                    confirm_new_cycle=False):
    if type(confirm_new_cycle) is not bool or (confirm_new_cycle and not (gui_start_intent is True and execute is True)):
        raise ValueError('new_cycle_confirmation_requires_current_gui_start')
    if type(gui_start_intent) is not bool or (gui_start_intent and execute is not True):
        raise ValueError('gui_start_intent_requires_explicit_execute')
    if type(attest_current_plan) is not bool or (attest_current_plan and execute is not True):
        raise ValueError('current_plan_attestation_requires_explicit_execute_boolean')
    if not execute:
        return inspect_frame(tasker, game_day=game_day, authorizations=authorizations, device=device)
    from .preset_execution import Journal
    from .schedule_house import completed
    report = dict(outcome='unknown', daily_complete=False, schedule_executed=False,
        one_key_attempted=False, nine_rows_verified=False, home_verified=False,
        resources={}, receipts=[], sent=[], input_evidence='unknown', game_day=game_day, device=device,
        authorizations=list(authorizations), business_facts=dict(normal_gacha_free=True,
            source='gui-selected-free-task' if gui_start_intent else 'user-reported'),
        phase='navigation', consumer_state='not_started', native_input_state='not_started',
        navigation_attempts=[],
        settings_assurance=('gui_selected_tasks_start' if gui_start_intent else
                            'user_attested_current_plan' if attest_current_plan else 'unattested'))
    try:
        if type(execute) is not bool:
            raise ValueError('execute_must_be_boolean')
        validate_execution(game_day, device, authorizations, portable=True)
    except (ValueError, TypeError) as exc:
        report.update(outcome='not_authorized', reason=str(exc))
        return report
    requested_device = device
    from copy import deepcopy
    device = deepcopy(device)
    report['device'] = device
    journal = journal or Journal(native.ROOT / '.runtime/schedule-onekey-work/ledger')
    reader = OneKeyPage(game_day, attest_current_plan=attest_current_plan, gui_start_intent=gui_start_intent)
    deadline = time.monotonic() + 600
    registered = False
    root_started = False
    buffered_actions = []
    unresolved_evidence = None
    source = None
    last_capture = 0

    def observation_source():
        from .context_backend import ContextTasker
        if isinstance(tasker, ContextTasker):
            return tasker.observation_source()
        else:
            owner = tasker
        if owner.stopping:
            raise ValueError('unresolved_observation_stopped')
        return owner.resource._handle, owner.controller._handle

    def persist_root(before, *, consumer=False):
        nonlocal root_started
        if unresolved_evidence is not None:
            raise ValueError('unresolved_readonly_no_journal_write')
        if consumer:
            # Set before the write: an interrupted append/fsync is ambiguous.
            report.update(phase='consumer', consumer_state='in_flight')
        if root_started:
            return
        journal.append(dict(state='in_flight', operation='schedule_onekey', game_day=game_day,
            device=device, authorizations=list(authorizations), intent_id=intent_id,
            session_id=reader.session_id, invocation_id=invocation_id,
            image_sha256=before['image_sha256'], settings_assurance=report['settings_assurance']))
        root_started = True
        with actions.locked():
            for event in buffered_actions:
                actions.append(event)
        buffered_actions.clear()

    def record_action(event):
        event.update(device=device, intent_id=intent_id, session_id=reader.session_id)
        if root_started:
            actions.append(event)
        else:
            buffered_actions.append(event)

    def check():
        check_day(game_day)
        if (requested_device != device or any(type(requested_device.get(key)) is not type(value)
                                             for key, value in device.items())):
            raise ValueError('onekey_device_binding_changed')
        if time.monotonic() >= deadline:
            raise ValueError('onekey_budget_exhausted')
        if unresolved_evidence is not None:
            unresolved_evidence.recheck()
            if observation_source() != source:
                raise ValueError('unresolved_observation_source_changed')

    def run(entry, *, limit=None, reset_hits=()):
        nonlocal last_capture
        check()
        read_deadline = min(deadline, limit) if limit is not None else deadline
        if time.monotonic() >= read_deadline:
            raise ValueError('audit_transition_read_budget_exhausted')
        token = native.RUN_DEADLINE.set(min(native.RUN_DEADLINE.get(), read_deadline))
        try:
            result = native.run(tasker, entry, reset_hits=reset_hits) if reset_hits else native.run(tasker, entry)
        finally:
            native.RUN_DEADLINE.reset(token)
        report['receipts'].append(dict(entry=entry, native=result, reading=reader.last))
        if unresolved_evidence is not None:
            check()
            detail = reader.last or {}
            if (detail.get('acquisition') != 'current_tasker_capture'
                    or detail.get('session_id') != reader.session_id
                    or type(detail.get('capture_index')) is not int
                    or detail['capture_index'] <= last_capture
                    or current_game_day(datetime.fromisoformat(detail['captured_at'])) != game_day):
                raise ValueError('unresolved_observation_source_changed')
            last_capture = detail['capture_index']
        return result

    def read_once(*, limit=None):
        reader.last = None
        r = run('OneKeyExecRead', limit=limit)
        if not completed(r, 'OneKeyExecSnapshot') or not reader.last:
            raise ValueError((reader.last or {}).get('reason', 'capture_or_native_read_failed'))
        return reader.last

    def read():
        page = read_once()
        if page['state'] == 'unknown':
            raise ValueError(page.get('reason') or 'unverified_read')
        return page

    def send(action, before, *, targets=None):
        check()
        if action in report['sent']:
            raise ValueError('duplicate_action_blocked')
        base = action.split(':')[0]
        if (unresolved_evidence is not None and base not in ('enter', 'up', 'down', 'close')
                and base not in reader.recovery_result_actions):
            raise ValueError('unresolved_readonly_consumer_blocked')
        if base not in NONCONSUMING_TARGET and not targets:
            raise ValueError('post_consumer_target_required')
        if base == 'onekey' and not (attest_current_plan or gui_start_intent):
            # Fail before consumer intent, sent/attempted flags or native dispatch.
            # Neither historical audit nor a previous invocation supplies this
            # invocation's operator assertion of free, non-resetting scope.
            raise ValueError(ONEKEY_SCOPE_BLOCKER)
        report['native_input_state'] = 'not_started'
        report['input_evidence'] = 'unknown'
        consumer = base in ('onekey', 'dungeon_confirm', 'mana_confirm', 'exp_confirm')
        if consumer:
            persist_root(before, consumer=True)
        elif base not in NONCONSUMING_TARGET and not root_started and base not in reader.recovery_result_actions:
            raise ValueError('result_without_consumer_intent')
        # Root OS lock spans navigation, fresh observation and all consumers.
        # Before consumption, navigation receipts stay in this invocation only;
        # a crash cannot create a permanent consumer intent or an orphan ledger.
        attempt = None
        if base in NONCONSUMING_TARGET:
            attempt = dict(action=action, before_sha256=before['image_sha256'],
                           native_input_state='unknown')
            report['navigation_attempts'].append(attempt)
        with actions.locked() if root_started else nullcontext():
            intent = dict(state='in_flight', operation=action, game_day=game_day,
                          image_sha256=before['image_sha256'])
            if base == 'onekey':
                intent['settings_assurance'] = report['settings_assurance']
            record_action(intent)
            report['sent'].append(action)
            reader.expected = reader.signature(before)
            reader.allowed_action = base
            if base == 'onekey':
                report['one_key_attempted'] = True
            report['native_input_state'] = 'unknown'
            receipt = run('OneKeyDo_' + base, reset_hits=(
                ('OneKeyInput_' + base, 'OneKeySkipResultReady')
                if base in ('mana_skip_finish', 'exp_skip_finish') else ()))
            result_already_ready = (base in ('mana_skip_finish', 'exp_skip_finish')
                and completed(receipt, 'OneKeySkipResultReady')
                and (reader.last or {}).get('state') == 'sweep_result'
                and (reader.last or {}).get('evidence', {}).get('OneKeyGuard_sweep_result', {}).get('hit') is True
                and all(node['completed'] and (node.get('action') or {}).get('name') == node['name']
                        and (node.get('action') or {}).get('success') is True
                        and (node.get('action') or {}).get('raw') == {} for node in receipt['nodes']))
            try:
                if not result_already_ready:
                    native.require_native_input(receipt, ('OneKeyInput_' + base,))
            except native.NativeInputNotSent as exc:
                # Only this action was refused; earlier batch consumers remain unresolved.
                report.update(failed_input_operation=action, failed_input_receipt=exc.receipt,
                              native_input_state='not_sent', input_evidence='current_action_not_sent')
                if attempt is not None:
                    attempt['native_input_state'] = 'not_sent'
                raise
            except RuntimeError as exc:
                # Historical abort verifiers match this existing unknown reason.
                raise ValueError('input_unverified_no_retry:' + action) from exc
            if not result_already_ready and not completed(receipt, 'OneKeyInput_' + base):
                raise ValueError('input_unverified_no_retry:' + action)
            observed_before = before
            observed_targets = targets
            # A dedicated dungeon summary can replace the detail before its
            # one-shot guard. Wait from the page actually closed, not the old
            # detail, so a lingering summary cannot trigger a second close.
            if (base == 'dungeon_finish'
                    and reader.dungeon_summary_advanced(reader.signature(before), reader.signature(reader.last))):
                observed_before = reader.last
                observed_targets = tuple(state for state in targets if state != 'sweep_result')
            report['native_input_state'] = 'not_sent' if result_already_ready else 'completed'
            if attempt is not None:
                attempt['native_input_state'] = 'completed'
            report['input_evidence'] = ('native_input_not_needed_result_already_visible' if result_already_ready
                                        else 'native_input_completed_result_unconfirmed')
            after = (observe_nonconsuming(reader, action, before, read_once, deadline)
                     if base in NONCONSUMING_TARGET else
                     observe_post_consumer(reader, action, observed_before, read_once, deadline, observed_targets))
            record_action(dict(state='settled', operation=action, game_day=game_day,
                                image_sha256=after['image_sha256'], native_task_id=receipt['task_id'],
                                **({'input_execution': 'not_sent'} if result_already_ready else {})))
            return after

    try:
        with journal.locked():
            from .schedule_recovery import load_execution_history
            prior_complete, retry_of, prior_input_evidence, unresolved_evidence, original = (
                load_execution_history(journal, game_day, device, gui_start_intent, report))
            if unresolved_evidence is not None:
                reader.readonly_unresolved = True
            if prior_input_evidence == 'parent_assisted_completed':
                # The fixed historical completion is read-only on its game day.
                # No new Tasker registration, navigation, intent or consumer input.
                report.update(outcome='already_complete', completion_source='parent_assisted_completed',
                              before_rows=dict.fromkeys(prior_complete, 'complete'),
                              after_rows=dict.fromkeys(prior_complete, 'complete'))
                return report
            if prior_input_evidence:
                report['prior_input_evidence'] = prior_input_evidence
            if tasker is None:
                raise ValueError('bound_tasker_required')
            if unresolved_evidence is not None:
                source = observation_source()
            intent_id = uuid4().hex
            invocation_id = invocation_id or intent_id
            report['navigation_context'] = dict(run_id=intent_id, game_day=game_day, device=device,
                session_id=reader.session_id, invocation_id=invocation_id)
            actions = Journal(journal.directory / 'actions' / intent_id)
            if retry_of is not None:
                # Claim the only retry before any native work; even a failed
                # registration/read cannot reuse the parent-reviewed closure.
                journal.append(dict(state='in_flight', operation='schedule_onekey', game_day=game_day,
                    device=device, authorizations=list(authorizations), intent_id=intent_id,
                    session_id=reader.session_id, invocation_id=invocation_id,
                    retry_of=retry_of, settings_assurance=report['settings_assurance']))
                root_started = True
            if not tasker.resource.register_custom_recognition('OneKeyPage', reader):
                raise ValueError('cannot_register_onekey_reader')
            registered = True
            # Nested Agent runs share hit counters across this one Owner Context.
            # Direct Tasker jobs have fresh counters. Allow bounded observations
            # and the existing <=24 guarded scrolls in the borrowed Context only;
            # never rearm/reset any consumer node or an unknown dispatch.
            from .context_backend import ContextTasker
            if isinstance(tasker, ContextTasker):
                if not tasker.resource.override_pipeline({
                        'OneKeyExecSnapshot': {'max_hit': 512},
                        'OneKeyInput_up': {'max_hit': 24},
                        'OneKeyInput_down': {'max_hit': 24}}):
                    raise ValueError('context_observation_budget_setup_failed')
            page = read()
            if unresolved_evidence is not None and page['state'] == 'dungeon_finish':
                from .schedule_recovery import close_unresolved_dungeon
                page = close_unresolved_dungeon(page, journal, original, reader, send)
                report['result_recovery'] = dict(sent=list(report['sent']), receipts=list(report['receipts']),
                                                home_verified=True, original_settlement_verified=False)
                report['sent'].clear()
                report['receipts'].clear()
                report['navigation_attempts'].clear()
                buffered_actions.clear()
                # Start the ordinary fresh inventory from a new native capture.
                page = read()
            if page['state'] != 'home':
                raise ValueError('verified_home_start_required')
            report['resources']['before'] = page['resources']
            page = send('enter', page)
            # Settings are player-managed. Only the explicit diagnostic opens
            # them; this path observes fresh rows and requires this invocation's
            # explicit scope attestation before a pending batch can dispatch.
            before, page = scan_rows(page, send, report['sent'])
            report['before_rows'] = before
            if any(before[k] != 'complete' for k in prior_complete):
                raise ValueError('same_day_completed_row_regressed')
            pending = [k for k in ITEMS if before[k] == 'pending']
            if unresolved_evidence is not None and any(value != 'complete' for value in before.values()):
                if not confirm_new_cycle or original['game_day'] >= game_day or report.get('result_recovery'):
                    page = send('close', page)
                    report['home_verified'] = page['state'] == 'home'
                    if not report['home_verified']:
                        raise ValueError('recovery_return_home_failed')
                    raise ValueError('recovery_result_closed_start_again' if report.get('result_recovery') else
                                     'unresolved_current_targets_incomplete_no_consumer' if original['game_day'] == game_day
                                     else 'new_cycle_refresh_confirmation_required')
                from .schedule_recovery import retain_unknown_for_new_cycle
                check()
                renewal = retain_unknown_for_new_cycle(journal, original, report, confirmed=True)
                report.update(historical_execution_unresolved=True, original_execution_unresolved=False,
                              new_cycle_recovery=renewal, retry_permitted=False)
                report.pop('recovery', None)
                unresolved_evidence = None
                reader.readonly_unresolved = False
            if set(pending) - set(authorizations):
                raise ValueError('pending_items_not_authorized:' + ','.join(sorted(set(pending)-set(authorizations)))
                    + '；游戏原生一键不能跳过这些未授权项目。请补充本次对应项目的授权，或先在游戏内完成它们后重新开始；本次未发送一键。')
            if pending:
                page = execute_pending(page, pending, send, report)
                after, page = scan_rows(page, send, report['sent'], expected={key: 'complete' for key in ITEMS})
            else:
                after = before
            report.update(after_rows=after, nine_rows_verified=True)
            page = send('close', page)
            if page['state'] != 'home':
                raise ValueError('final_home_not_verified')
            report['resources']['after'] = page['resources']
            report['resources']['ap_delta'] = page['resources']['ap'] - report['resources']['before']['ap']
            report['resources']['jewels_delta'] = page['resources']['jewels'] - report['resources']['before']['jewels']
            if report['resources']['jewels_delta'] != 0:
                raise ValueError('jewel_balance_changed_parent_reconciliation')
            if unresolved_evidence is not None:
                if report['resources']['ap_delta'] != 0:
                    raise ValueError('unresolved_readonly_ap_changed')
                check()
                from .onekey_history import unresolved_observation_history
                unresolved_observation_history(journal, game_day, device)
                report.update(home_verified=True, outcome='current_complete_readonly',
                    current_observation_verified=True, input_evidence='current_readonly_observation',
                    completion_source='fresh_same_day_nine_rows', settlement_verified=False)
                # No root/action append, old settlement inference, or retry token.
                return report
            report.update(home_verified=True, settlement_verified=True, schedule_executed=bool(pending),
                          input_evidence='result_confirmed',
                          outcome='executed' if pending else 'already_complete')
            persist_root(page)  # Record verified no-consumer observations only after safe return.
            end = dict(state='settled', operation='schedule_onekey', game_day=game_day,
                device=device, intent_id=intent_id, outcome=report['outcome'], rows=after,
                image_sha256=page['image_sha256'], session_id=reader.session_id,
                invocation_id=invocation_id, resources=report['resources'], receipts=report['receipts'],
                **{key:report[key] for key in ('home_verified','settlement_verified','nine_rows_verified')})
            proof = dict(start=journal.events()[-1], end=end, actions=actions.events())
            if pending:
                from .onekey_history import verify_execution
                verify_execution(report, proof['start'], end, proof['actions'], invocation_id=invocation_id)
            journal.append(end)
            report['journal_evidence'] = proof
            if report['consumer_state'] == 'in_flight':
                report['consumer_state'] = 'settled'
    except (Exception, KeyboardInterrupt) as exc:
        report.update(outcome='unknown', reason=str(exc) or 'interrupted_no_retry')
        if report['consumer_state'] == 'in_flight' and 'recovery' not in report:
            from .onekey_reconcile import interrupted_recovery
            report['recovery'] = interrupted_recovery(report, invocation_id)
    finally:
        if registered and not tasker.resource.unregister_custom_recognition('OneKeyPage'):
            report.update(outcome='unknown', reason='reader_release_failed')
    return report
