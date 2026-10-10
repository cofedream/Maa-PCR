"""Maa same-frame OCR and bounded Pipeline input for abyss outposts only."""
from contextlib import contextmanager
from copy import deepcopy
import json
import hashlib
import re
import traceback
import time
from uuid import uuid4

from maa.custom_recognition import CustomRecognition
from . import native
from .abyss import DIFFICULTIES, SCOPE, assess_home, check_quote, check_ticket, normalize_period, same_quote
from .schedule_house import completed


def _detail(receipt, name):
    nodes = [node for node in receipt['nodes'] if node['name'] == name and node['completed']]
    if not receipt['native_ok'] or len(nodes) != 1:
        return None
    result = (nodes[0].get('recognition') or {}).get('best')
    detail = result.get('detail') if isinstance(result, dict) else None
    if isinstance(detail, str):
        detail = json.loads(detail)
    return detail if isinstance(detail, dict) else None


class _NoMatch(ValueError):
    """A readable frame did not provide an unambiguous required field."""


class _Words:
    def __init__(self, context, image):
        if image.shape != (720, 1280, 3) or image.dtype.name != 'uint8':
            raise _NoMatch('abyss_frame_layout_unknown')
        self.context, self.image, self.evidence = context, image, {}
        self._recognitions = {}

    def recognize(self, name, override=None):
        cache_key = (name, json.dumps(override, sort_keys=True, separators=(',', ':'), allow_nan=False))
        if cache_key not in self._recognitions:
            self._recognitions[cache_key] = self.context.run_recognition(name, self.image, override)
        result = self._recognitions[cache_key]
        self.evidence[name] = dict(hit=bool(result and result.hit),
                                   reco_id=result.reco_id if result else None,
                                   raw=result.raw_detail if result else None)
        return result

    def word(self, name, override=None):
        result = self.recognize(name, override)
        words = result.raw_detail.get('filtered', []) if result and result.hit else []
        if len(words) != 1 or words[0].get('score', 0) < .9:
            raise _NoMatch('ambiguous_or_missing_abyss_word:' + name)
        return words[0]['text'].strip()

    def integer(self, name, override=None):
        text = self.word(name, override)
        if not re.fullmatch(r'\d{1,9}', text):
            raise _NoMatch('abyss_number_unknown:' + name)
        return int(text)

    def fraction(self, name, override=None):
        match = re.fullmatch(r'(\d{1,3})\s*[/／]\s*(\d{1,3})', self.word(name, override))
        if match is None:
            raise _NoMatch('abyss_counter_unknown:' + name)
        remaining, limit = map(int, match.groups())
        if not 0 <= remaining <= limit <= 999 or limit == 0:
            raise _NoMatch('abyss_counter_contradiction:' + name)
        return remaining, limit


def _quote_rows(words, reading, *, ticket=False):
    planned = [row for row in reversed(reading['rows']) if row['remaining'] > 0]
    result = words.recognize('AbyssQuoteTitles')
    titles = result.raw_detail.get('filtered', []) if result and result.hit else []
    if not 1 <= len(titles) == len(planned) <= 3:
        raise _NoMatch('abyss_quote_row_count_changed')
    titles = sorted(titles, key=lambda word: word['box'][1])
    rows, row_evidence = [], {}
    for title, expected in zip(titles, planned):
        if title.get('score', 0) < .9 or title['text'].strip() != '前哨关卡':
            raise _NoMatch('abyss_quote_title_unknown')
        y = title['box'][1]
        if rows and y - titles[len(rows)-1]['box'][1] < 65:
            raise _NoMatch('abyss_quote_rows_overlap')
        def override(field):
            name = 'AbyssQuoteRow1' + field
            roi = list(words.context.get_node_data(name)['recognition']['param']['roi'])
            roi[1] += y - 147
            if roi[1] < 132 or roi[1] + roi[3] > 425:
                raise _NoMatch('abyss_quote_row_layout_unknown')
            return name, {name: dict(roi=roi, **({'expected': '^' + expected['difficulty'] + '$'}
                                              if field == 'Difficulty' else {'only_rec': False}
                                              if field == 'Remaining' else {}))}
        def word(field):
            return words.word(*override(field))
        if word('Mode') != '深渊讨伐战' or word('Difficulty') != expected['difficulty']:
            raise _NoMatch('abyss_quote_row_identity_changed')
        row = dict(title='前哨关卡', mode='深渊讨伐战', difficulty=expected['difficulty'],
                   times=words.integer(*override('Times')), fields_verified=True)
        if not ticket:
            word('RemainingLabel'); word('TimesLabel')
            row['remaining'], row['limit'] = words.fraction(*override('Remaining'))
        fields = ('Mode', 'Difficulty', 'Times') if ticket else (
            'Mode', 'Difficulty', 'Times', 'RemainingLabel', 'TimesLabel', 'Remaining')
        row_evidence.update({'AbyssQuoteRow' + str(len(rows)+1) + field:
            deepcopy(words.evidence['AbyssQuoteRow1' + field]) for field in fields})
        rows.append(row)
    words.evidence.update(row_evidence)
    return list(reversed(rows))


class AbyssHomePage(CustomRecognition):
    def __init__(self, expected=None, require_sweep=False):
        super().__init__()
        self.expected, self.require_sweep = deepcopy(expected), require_sweep

    def analyze(self, context, argv):
        try:
            words = _Words(context, argv.image)
            guard = words.recognize('AbyssHomeGuard')
            if not guard or not guard.hit:
                return None
            identity = dict(mode='深渊讨伐战', event_name=words.word('AbyssEventName'),
                            period=normalize_period(words.word('AbyssPeriod')))
            rows = []
            for key, difficulty in zip(('Normal', 'Hard', 'VeryHard'), DIFFICULTIES):
                if words.word('Abyss' + key + 'Label') != difficulty:
                    return None
                words.word('Abyss' + key + 'RemainingLabel')
                remaining, limit = words.fraction('Abyss' + key + 'Remaining')
                rows.append(dict(difficulty=difficulty, remaining=remaining, limit=limit,
                                 fields_verified=True))
            detail = dict(native_ok=True, abyss_page_verified=True, abyss_identity=identity,
                          rows=rows, list_complete=True, evidence=words.evidence)
            if assess_home(detail) == 'reading_unknown':
                return None
            if self.expected is not None and any(detail[key] != self.expected.get(key)
                                                  for key in ('abyss_identity', 'rows')):
                return None
            if self.require_sweep:
                words.word('AbyssHomeSweepButton')
            return self.AnalyzeResult([82, 563, 276, 40], detail)
        except _NoMatch:
            return None
        except Exception:
            traceback.print_exc()
            return None


class AbyssQuotePage(CustomRecognition):
    """The current one-to-three outpost confirmation; no count edits."""
    def __init__(self, reading, expected=None):
        super().__init__()
        self.reading, self.expected = deepcopy(reading), deepcopy(expected)

    def analyze(self, context, argv):
        try:
            words = _Words(context, argv.image)
            guard = words.recognize('AbyssConfirmGuard')
            if not guard or not guard.hit:
                return None
            rows = _quote_rows(words, self.reading)
            quote = dict(scope=SCOPE, verified=True, identity_verified=True,
                         abyss_identity=deepcopy(self.reading['abyss_identity']),
                         rows=rows, stages=words.integer('AbyssQuoteStages'),
                         total_times=words.integer('AbyssQuoteTotalTimes'),
                         stamina_cost=words.integer('AbyssQuoteStaminaCost'),
                         stamina_owned=words.integer('AbyssQuoteStaminaOwned'),
                         ticket_cost=words.integer('AbyssQuoteTicketCost'), evidence=words.evidence)
            # Inventory is optional. An actual observed shortage still blocks.
            try:
                quote['tickets_owned'] = words.integer('AbyssQuoteTicketsOwned')
            except _NoMatch:
                pass
            if self.expected is not None and not same_quote(self.expected, quote, self.reading):
                return None
            return self.AnalyzeResult([735, 608, 115, 60], quote)
        except _NoMatch:
            return None
        except Exception:
            traceback.print_exc()
            return None


class AbyssResultPage(CustomRecognition):
    """Capture the current guarded action frame for this one dispatched sweep."""
    def __init__(self, quote, evidence_images, checkpoint=None, consumer=None):
        super().__init__()
        self.quote = deepcopy(quote)
        self.images = evidence_images
        self.checkpoint, self.consumer = checkpoint, consumer

    def analyze(self, context, argv):
        try:
            words = _Words(context, argv.image)
            for name in ('ExecResultTitle', 'ExecResultComplete', 'ExecResultButton'):
                words.word(name)
            # Maa reruns recognition while waiting. Evidence is written only
            # when the dedicated result anchors all match this action frame.
            if self.images:
                return None
            button = words.evidence['ExecResultButton']['raw']['filtered'][0]['box']
            path = native.RUNTIME / ('abyss-result-' + uuid4().hex + '.png')
            if not native.write_png(path, argv.image):
                return None
            item = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        kind='result', task_id=context.get_task_job().job_id,
                        source='current abyss transaction result action frame; native 720 short side',
                        scope=SCOPE, abyss_identity=self.quote['abyss_identity'],
                        counts=[row['times'] for row in self.quote['rows']],
                        evidence=words.evidence)
            if self.checkpoint is not None:
                self.checkpoint(self.consumer, item, {name: words.evidence[name]['raw'] for name in
                    ('ExecResultTitle', 'ExecResultComplete', 'ExecResultButton')})
            self.images.append(item)
            return self.AnalyzeResult(button, item)
        except _NoMatch:
            return None
        except Exception:
            traceback.print_exc()
            return None


class AbyssTicketPage(CustomRecognition):
    """The second modal, bound to the original remaining-outpost quote."""
    def __init__(self, reading, quote, require_ready=False):
        super().__init__()
        self.reading, self.quote = deepcopy(reading), deepcopy(quote)
        self.require_ready = require_ready

    def analyze(self, context, argv):
        try:
            if check_quote(self.quote, self.reading) != 'ready':
                return None
            words = _Words(context, argv.image)
            guard = words.recognize('AbyssTicketGuard')
            if not guard or not guard.hit:
                return None
            rows = _quote_rows(words, self.reading, ticket=True)
            detail = dict(native_ok=True, identity_verified=True, scope=SCOPE,
                          abyss_identity=deepcopy(self.reading['abyss_identity']),
                          background_counts=[row['times'] for row in rows],
                          stamina_cost=words.integer('AbyssTicketStaminaCost'),
                          stamina_owned=words.integer('AbyssTicketStaminaOwned'),
                          ticket_cost=words.integer('AbyssTicketCost'), evidence=words.evidence,
                          page='sweep_ticket_confirmation')
            try:
                detail['tickets_owned'] = words.integer('AbyssTicketOwned')
            except _NoMatch:
                pass
            status = check_ticket(detail, self.quote, self.reading)
            detail['status'] = status
            if self.require_ready and status != 'ready':
                return None
            button = words.recognize('AbyssTicketConfirmButton')
            if not button or not button.hit:
                return None
            return self.AnalyzeResult(button.box, detail)
        except _NoMatch:
            return None
        except Exception:
            traceback.print_exc()
            return None


@contextmanager
def _reader(resource, name, callback):
    if not resource.register_custom_recognition(name, callback):
        raise RuntimeError('Cannot register abyss reader:' + name)
    try:
        yield
    finally:
        resource.unregister_custom_recognition(name)


def read_abyss_home(tasker, expected=None):
    report = dict(scope=SCOPE, native_ok=False, abyss_page_verified=False, list_complete=False,
                  rows=[], outcome='reading_unknown', consumption_sent=False,
                  settlement_verified=False, home_verified=False, daily_complete=False,
                  execution_permitted=False)
    with _reader(tasker.resource, 'AbyssHomePage', AbyssHomePage(expected)):
        receipt = native.run(tasker, 'AbyssHomeSnapshot')
    report['reading_receipt'] = str(native.save_receipt(receipt, 'abyss-home-reading'))
    report['native_receipt'] = receipt
    detail = _detail(receipt, 'AbyssHomeSnapshot')
    if detail:
        report.update(detail)
        report['outcome'] = assess_home(report)
    return report


class NativeAbyss:
    def __init__(self, tasker, start='home'):
        if start not in ('home', 'adventure', 'abyss'):
            raise ValueError('abyss_start_invalid')
        self.tasker, self.start = tasker, start
        self.entered = start == 'abyss'
        self.closed_adventure_verified = False
        self.identity = self.reading = self._confirmed = None
        self.navigation = None
        self._quote_opened = self._submitted = False
        self._result_returned = self._post_counts_verified = False
        self._challenge_opened = False
        self._opening_receipt = None

    def bind_result_checkpoint(self, callback):
        self._result_checkpoint = callback

    def read(self):
        if self._submitted and not self._result_returned:
            return dict(native_ok=False, abyss_page_verified=False, list_complete=False, rows=[],
                        incomplete_reason='abyss_unknown_consumption_preserve_scene')
        if not self.entered:
            from .activity_navigation import absent_entry, prepare_home
            if self.start == 'home':
                self.preparation = prepare_home(self.tasker)
                if not completed(self.preparation, 'ObserveHome'):
                    return dict(native_ok=False, rows=[], incomplete_reason='activity_start_unknown')
            entry = 'AbyssFromHome' if self.start == 'home' else 'AbyssAdventureGate'
            with absent_entry(self.tasker, 'abyss'):
                self.navigation = native.run(self.tasker, entry,
                    reset_hits=('AbyssFromHome', 'AbyssFromAdventure', 'AbyssAdventureEntryClickCandidate'))
            if completed(self.navigation, 'AbyssUnavailable'):
                self.closed_adventure_verified = True
                return dict(native_ok=True, abyss_page_verified=False, list_complete=False, rows=[],
                            availability='closed', closed_verified=True, navigation=self.navigation)
            if not completed(self.navigation, 'AbyssHomeArrived'):
                return dict(native_ok=False, abyss_page_verified=False, list_complete=False, rows=[],
                            navigation=self.navigation, incomplete_reason='abyss_entry_unverified')
            self.entered = True
        reading = read_abyss_home(self.tasker)
        reading['navigation'] = self.navigation
        if self.identity is not None and reading.get('abyss_identity') != self.identity:
            reading.update(native_ok=False, abyss_page_verified=False,
                           incomplete_reason='abyss_identity_changed')
        if assess_home(reading) != 'reading_unknown':
            self.identity = deepcopy(reading['abyss_identity'])
            self.reading = deepcopy(reading)
            if self._submitted:
                self._post_counts_verified = assess_home(reading) == 'skipped_goal_met'
        return reading

    def _quote_read(self, reading, expected=None):
        with _reader(self.tasker.resource, 'AbyssQuotePage', AbyssQuotePage(reading, expected)):
            receipt = native.run(self.tasker, 'AbyssQuoteSnapshot')
        quote = deepcopy(_detail(receipt, 'AbyssQuoteSnapshot')) or dict(verified=False, identity_verified=False)
        quote['quote_receipt'] = str(native.save_receipt(receipt, 'abyss-quote-reading'))
        quote['native_receipt'] = receipt
        return quote

    def quote(self, reading):
        if (self._quote_opened or assess_home(reading) != 'needs_execution'
                or reading.get('abyss_identity') != self.identity):
            return dict(verified=False, identity_verified=False, incomplete_reason='abyss_quote_binding')
        self._quote_opened = True
        with _reader(self.tasker.resource, 'AbyssOpenPage', AbyssHomePage(reading, require_sweep=True)):
            receipt = native.run(self.tasker, 'AbyssOpenSweep', reset_hits=('AbyssOpenSweep',))
        if not completed(receipt, 'AbyssOpenSweep'):
            return dict(verified=False, identity_verified=False, native_receipt=receipt)
        quote = self._quote_read(reading)
        quote['open_receipt'] = receipt
        return quote

    def confirm(self, reading, quote):
        self._confirmed = None
        if (self._submitted or self._challenge_opened or reading.get('abyss_identity') != self.identity
                or check_quote(quote, reading) != 'ready'):
            return dict(verified=False)
        actual = self._quote_read(reading, quote)
        if not same_quote(quote, actual, reading):
            return dict(verified=False, reading=actual, challenge_opened=False)
        # Challenge opens the small confirmation; it is not the consumer.
        self._challenge_opened = True
        with _reader(self.tasker.resource, 'AbyssConfirmedQuote', AbyssQuotePage(reading, quote)):
            with _reader(self.tasker.resource, 'AbyssTicketPage', AbyssTicketPage(reading, quote)):
                receipt = native.run(self.tasker, 'AbyssChallenge', reset_hits=('AbyssChallenge',))
        self._opening_receipt = receipt
        ticket = deepcopy(_detail(receipt, 'AbyssTicketSnapshot')) or dict(native_ok=False)
        status = check_ticket(ticket, quote, reading)
        verified = completed(receipt, 'AbyssChallenge') and status == 'ready'
        if verified:
            self._confirmed = (deepcopy(reading), deepcopy(quote), time.monotonic())
        from .result_checkpoint import action_completed
        return dict(verified=verified, reading=ticket, status=status,
                    challenge_opened=action_completed(receipt, 'AbyssChallenge'), opening_receipt=receipt)

    def cancel_confirmation(self):
        self._confirmed = None
        if self._submitted:
            return False
        if self._challenge_opened:
            ticket_cancel = native.run(self.tasker, 'AbyssTicketCancel', reset_hits=('AbyssTicketCancel',))
            self.ticket_cancel_receipt = ticket_cancel
            if not completed(ticket_cancel, 'AbyssQuoteReturned'):
                return False
            self._challenge_opened = False
        receipt = native.run(self.tasker, 'AbyssCancel', reset_hits=('AbyssCancel',))
        self.cancel_receipt = receipt
        return completed(receipt, 'AbyssCancelled')

    def submit(self, quote):
        snapshot, self._confirmed = self._confirmed, None
        if (self._submitted or snapshot is None or snapshot[1] != quote
                or time.monotonic() - snapshot[2] > 30):
            raise native.NativeInputNotSent(dict(input_execution='not_sent',
                reason='abyss_one_shot_snapshot_missing_or_expired', dispatch_attempted=False))
        self._submitted = True
        with _reader(self.tasker.resource, 'AbyssTicketPage', AbyssTicketPage(snapshot[0], quote, require_ready=True)):
            receipt = native.run(self.tasker, 'AbyssTicketCommit', reset_hits=('AbyssTicketCommit',))
        native.require_native_input(receipt, ('AbyssTicketCommit',))
        from .result_checkpoint import action_completed
        dispatched = action_completed(receipt, 'AbyssTicketCommit')
        report = dict(consumption_dispatched=dispatched, result_verified=False,
                      abyss_return_verified=False, result_images=[], native_receipt=receipt,
                      incomplete_reason='abyss_result_unverified',
                      result_receipt=str(native.save_receipt(receipt, 'abyss-submit')))
        if dispatched:
            images = []
            with _reader(self.tasker.resource, 'AbyssResultPage', AbyssResultPage(quote, images,
                    getattr(self, '_result_checkpoint', None), receipt)):
                result = native.run(self.tasker, 'AbyssResultClose',
                    reset_hits=('AbyssResultClose', 'AbyssLimitedShopCancel'))
            report.update(result_evidence=result, result_images=images,
                          result_verified=action_completed(result, 'AbyssResultClose') and len(images) == 1,
                          abyss_return_verified=completed(result, 'AbyssHomeArrived'))
            self._result_returned = report['result_verified'] and report['abyss_return_verified']
            if self._result_returned:
                report['incomplete_reason'] = None
            report['result_receipt'] = str(native.save_receipt(report, 'abyss-result'))
        return report

    def continue_open_final_intent(self, intent, before, opening_receipt, *, device):
        """Maintenance-only continuation of an already-opened, locked intent.

        The caller validates and holds the original Journal lock and provides
        its unchanged prefix. This method never opens Challenge, creates or
        edits a Journal, or provides a GUI/CLI retry route.
        """
        from .game_day import current_game_day
        from .onekey_history import validate_stable_device
        if (self._submitted or self._quote_opened or self._challenge_opened
                or not isinstance(intent, dict) or intent.get('state') != 'in_flight'
                or intent.get('scope') != SCOPE or intent.get('operation') != 'abyss_outpost'
                or not re.fullmatch(r'[0-9a-f]{32}', intent.get('intent_id', ''))
                or intent.get('game_day') != current_game_day()
                or intent.get('device') != device or assess_home(before) != 'needs_execution'):
            raise ValueError('abyss_original_open_final_intent_binding_invalid')
        validate_stable_device(device)
        quote = intent.get('quote')
        if (check_quote(quote, before) != 'ready'
                or intent.get('abyss_identity') != before['abyss_identity']
                or not completed(opening_receipt, 'AbyssChallenge')):
            raise ValueError('abyss_original_challenge_evidence_missing')
        proof = _detail(opening_receipt, 'AbyssChallenge')
        if not proof or not same_quote(quote, proof, before):
            raise ValueError('abyss_original_opening_quote_changed')
        self.entered = self._quote_opened = self._challenge_opened = True
        self.identity, self.reading = deepcopy(before['abyss_identity']), deepcopy(before)
        self._opening_receipt = deepcopy(opening_receipt)
        with _reader(self.tasker.resource, 'AbyssTicketPage', AbyssTicketPage(before, quote)):
            ticket_receipt = native.run(self.tasker, 'AbyssTicketSnapshot')
        ticket = deepcopy(_detail(ticket_receipt, 'AbyssTicketSnapshot')) or dict(native_ok=False)
        report = dict(scope=SCOPE, original_intent_id=intent['intent_id'],
                      game_day=intent['game_day'], device=deepcopy(device),
                      final_confirmation=ticket, final_read_receipt=ticket_receipt,
                      new_intent_created=False, challenge_dispatched=False,
                      consumption_sent=False, consumption_dispatched=False,
                      settlement_verified=False, post_counts_verified=False, home_verified=False,
                      goal_complete=False, daily_complete=False)
        if check_ticket(ticket, quote, before) != 'ready':
            report['outcome'] = 'original_final_confirmation_unverified'
            return report
        self._confirmed = (deepcopy(before), deepcopy(quote), time.monotonic())
        report['consumption_sent'] = True
        try:
            submitted = self.submit(quote)
            report.update(submission=submitted,
                          consumption_dispatched=submitted.get('consumption_dispatched') is True)
            if not (submitted.get('result_verified') and submitted.get('abyss_return_verified')):
                report['outcome'] = 'unknown_settlement'
                return report
            after = self.read()
            report['after'] = after
            verified = (assess_home(after) == 'skipped_goal_met'
                        and after['abyss_identity'] == before['abyss_identity']
                        and [r['limit'] for r in after['rows']] == [r['limit'] for r in before['rows']])
            report['post_counts_verified'] = verified
            if not verified:
                report['outcome'] = 'unknown_post_counts'
                return report
            report.update(settlement_verified=True, goal_complete=True)
            report['home_verified'] = self.home()
            report['outcome'] = 'executed' if report['home_verified'] else 'executed_home_failed'
        except (Exception, KeyboardInterrupt) as exc:
            report.update(outcome='executed_home_failed' if report['settlement_verified'] else 'unknown_settlement',
                          error=str(exc) or 'interrupted')
        return report

    def recover_result(self, record):
        images = []
        with _reader(self.tasker.resource, 'AbyssResultPage', AbyssResultPage(record['quote'], images)):
            receipt = native.run(self.tasker, 'AbyssRecoverResultStart',
                reset_hits=('AbyssResultClose', 'AbyssLimitedShopCancel'))
        at_abyss = completed(receipt, 'AbyssHomeArrived')
        if at_abyss:
            self.entered = True
        return dict(recovery_verified=at_abyss or completed(receipt, 'ObserveHome'), evidence=receipt)

    def home(self):
        if self.closed_adventure_verified:
            from .activity_navigation import return_home
            self.home_receipt = return_home(self.tasker)
            return completed(self.home_receipt, 'ObserveHome')
        if self._submitted and not self._post_counts_verified:
            return False
        with _reader(self.tasker.resource, 'AbyssHomePage', AbyssHomePage(
                self.reading if self.identity is not None else None)):
            receipt = native.run(self.tasker, 'AbyssReturnHome',
                reset_hits=('AbyssReturnHome', 'ReturnHomeGate'))
        self.home_receipt = receipt
        return completed(receipt, 'ObserveHome')
