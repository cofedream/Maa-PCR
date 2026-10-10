"""CN confirmation recognition; all recognition and movement use Maa Pipeline.

Coordinates are in the existing 1280x720 resource space. No image rescaling here.
"""
import json
import re
import traceback
import time
from maa.custom_recognition import CustomRecognition
from .preset_scan import Collector, key, identity_key, unlimited_glyph
from .event import is_story_h_title, valid_event_identity


def check_confirmation(result, reading, quote):
    if not result.get('list_complete') or not result.get('native_ok'):
        return False
    event_identity = reading.get('event_identity')
    if (quote.get('event_identity') != event_identity
            or result.get('event_identity') != event_identity
            or event_identity is not None and not valid_event_identity(event_identity)):
        return False
    rows = result['rows']
    if not rows or len(quote['counts']) != len(rows):
        return False
    identities = [list(key(r)) for r in rows]
    if identities != [list(identity_key(i)) for i in quote['identities']] or identities != [list(key(r)) for r in reading['rows']]:
        return False
    for row, original, count in zip(rows, reading['rows'], quote['counts']):
        if (row.get('count') != count or row.get('remaining') != original['remaining']
                or row.get('limit') != original['limit'] or row.get('title_score', 0) < .9
                or not row.get('fields_verified')):
            return False
        if original.get('difficulty') == '普通' and (row.get('unlimited') is not True or original.get('unlimited') is not True):
            return False
    s = result['summary']
    return (s['stages'] == len(rows) and s['times'] == sum(quote['counts'])
            and s['tickets_cost'] == s['times']
            and s['stamina_cost'] == quote['stamina_before'] - quote['stamina_after']
            # A single point may regenerate during the bounded fresh collection.
            and quote['stamina_before'] <= s['stamina_owned'] <= quote['stamina_before'] + 1
            and s['stamina_owned'] >= s['stamina_cost'])


class _ShortDeadline(TimeoutError):
    """The ordinary short-list acceptance window expired."""


class ConfirmationPage(CustomRecognition):
    def __init__(self, *, stable_short=False, event=False, event_identity=None):
        super().__init__()
        self.stable_short = stable_short
        self.event = event
        if event_identity is not None and (not event or not valid_event_identity(event_identity)):
            raise ValueError('Unverified event identity for confirmation')
        self.event_identity = dict(event_identity) if event_identity is not None else None
        self._short_signature = None
        self.short_deadline = float('inf')

    def analyze(self, context, argv):
        # Exceptions must not cross Maa's ctypes callback (undefined return value).
        try:
            short = json.loads(argv.custom_recognition_param) == 'short'
            if short and time.monotonic() >= self.short_deadline:
                self._short_signature = None
                return None
            result = self._analyze(context, argv)
            if short:
                # Native retry timeout does not reject a late successful callback.
                if time.monotonic() >= self.short_deadline:
                    self._short_signature = None
                    return None
                if result is None:
                    self._short_signature = None
                elif self.stable_short:
                    detail = result.detail
                    if isinstance(detail, str):
                        detail = json.loads(detail)
                    fields = ('title', 'category', 'difficulty', 'remaining', 'limit',
                              'count', 'ocr_box', 'fields_verified')
                    signature = ([{f: r[f] for f in fields} for r in detail['rows']], detail['summary'])
                    previous, self._short_signature = self._short_signature, signature
                    if previous != signature:
                        return None
            return result
        except _ShortDeadline:
            self._short_signature = None
            return None
        except Exception:
            traceback.print_exc()
            self._short_signature = None
            return None

    def _analyze(self, context, argv):
        kind = json.loads(argv.custom_recognition_param)
        evidence = {}
        recognitions = {}

        def recognize(name, override=None):
            if kind == 'short' and time.monotonic() >= self.short_deadline:
                raise _ShortDeadline('Short confirmation acceptance deadline')
            cache_key = (name, json.dumps(override, sort_keys=True, separators=(',', ':'), allow_nan=False))
            if cache_key not in recognitions:
                recognitions[cache_key] = context.run_recognition(name, argv.image, override)
            r = recognitions[cache_key]
            evidence[name] = {'hit': bool(r and r.hit), 'raw': r.raw_detail if r else None}
            return r

        def hit(name):
            r = recognize(name)
            return bool(r and r.hit)

        def value(name, override=None, pattern=r'\d{1,9}'):
            r = recognize(name, override)
            words = r.raw_detail.get('filtered', []) if r and r.hit else []
            if len(words) != 1 or words[0].get('score', 0) < .9:
                return None
            text = words[0]['text'].strip()
            return text if re.fullmatch(pattern, text) else None

        # Reject the wrong layout before expensive OCR. No positive result is
        # accepted without the same danger/header/row/amount checks below.
        top = hit('ExecBlueTop') and hit('ExecGrayBottom')
        bottom = hit('ExecGrayTop') and hit('ExecBlueBottom')
        # Absence of rail evidence only permits a content-based candidate.
        # It cannot prove completeness (an occluded long list also lacks a rail).
        short = not (top or bottom or hit('ExecRailBlue') or hit('ExecRailGray'))
        matches = (not top and not bottom and hit('ExecRailBlue') and hit('ExecRailGray')
                   if kind == 'page' else {'top': top, 'bottom': bottom, 'short': short}[kind])
        if not matches:
            return None
        if hit('ExecUnsafeStop') or not hit('ExecConfirmHeader'):
            return None
        summary = {}
        for field, name in SUMMARY.items():
            v = value(name, pattern=r'\d+处' if field == 'stages' else r'\d{1,9}')
            if v is None and field == 'tickets_cost':
                v = value('NormalTickets')
            if v is None:
                return None
            summary[field] = int(v.removesuffix('处'))
        raw = recognize('ExecRowsOCR')
        words = raw.raw_detail.get('filtered', []) if raw and raw.hit else []
        rows, partial = [], []
        gaps = False
        for word in words:
            x, y, w, h = word['box']
            title = re.sub(r'\s+', '', word['text']).replace('－', '-').replace('—', '-').replace('–', '-')
            if not (is_story_h_title(title) if self.event_identity is not None else
                    re.fullmatch(r'[^\d]+\d+-\d+', title)) or x > 530:
                continue
            if y <= 135 or y + 66 > 448:
                partial.append(word)
                continue
            row_evidence = {}
            def row_value(name, pattern):
                # Only anchor translation; the relative field ROIs live in JSON.
                roi = list(context.get_node_data(name)['attach']['row_roi'])
                roi[1] += y
                found = value(name, {name: {'roi': roi}}, pattern)
                row_evidence[name] = evidence[name]
                return found
            category = row_value('ExecRowCategory', '活动' if self.event else '主线关卡|外传|深域关卡')
            difficulty = row_value('ExecRowDifficulty', '困难|高难|普通') if category != '深域关卡' else None
            count = row_value('ExecRowCount', r'\d{1,3}')
            if count is None and difficulty == '普通':
                count = row_value('NormalRowCount', r'\d{1,3}')
            remaining = row_value('ExecRowRemaining', r'\d+[/／]\d+|∞')
            if difficulty == '普通' and remaining is None and unlimited_glyph(context, argv.image, y, confirmation=True, evidence=row_evidence):
                remaining = '∞'
            label = row_value('ExecRowSkipLabel', '跳过次数')
            valid = bool(category and count and remaining and label and (category == '深域关卡' or difficulty))
            unlimited = remaining == '∞' and difficulty == '普通' and category == '主线关卡'
            valid = valid and (remaining != '∞' or unlimited)
            a, b = map(int, re.split('[/／]', remaining)) if remaining and remaining != '∞' else (None, None)
            rows.append(dict(title=title, title_score=word['score'], category=category,
                             difficulty=difficulty, remaining=a, limit=b, count=int(count) if count else None,
                             unlimited=unlimited,
                             checked=True, fields_verified=valid, ocr_box=word['box'],
                             field_evidence=row_evidence))
            gaps |= not valid or word['score'] < .9
        rows.sort(key=lambda row: row['ocr_box'][1])
        gaps |= any(b['ocr_box'][1] - a['ocr_box'][1] > 100 for a, b in zip(rows, rows[1:]))
        if kind in ('top', 'short'):
            gaps |= not rows or rows[0]['ocr_box'][1] > 160
        if kind in ('bottom', 'short'):
            gaps |= any(w['box'][1] > 380 for w in partial)
        if kind == 'short':
            gaps |= bool(partial)
            if (gaps or len(rows) != summary['stages']
                    or sum(r['count'] for r in rows) != summary['times']):
                # Retry animation/unreadable candidates within ExecScanStart's
                # native deadline; never hand an incomplete short page to Collector.
                return None
        return self.AnalyzeResult([55, 134, 1160, 315], dict(
            kind=kind, rows=rows, partial=partial, gaps=bool(gaps), summary=summary, evidence=evidence,
            **({'event_identity': self.event_identity} if self.event_identity is not None else {})))


SUMMARY = dict(stamina_cost='ExecConfirmStamina', tickets_cost='ExecConfirmTickets',
               stamina_owned='ExecOwnedStamina',
               stages='ExecStageTotal', times='ExecTimesTotal')


class ConfirmationCollector(Collector):
    def run(self, context, argv):
        try:
            return self._collect(context, argv)
        except Exception:
            traceback.print_exc()
            self.complete = False
            self.reason = 'confirmation_callback_failed'
            return False

    def _collect(self, context, argv):
        page = argv.reco_detail.raw_detail['best']['detail']
        if isinstance(page, str):
            page = json.loads(page)
        if self.pages:
            if self.pages[0].get('event_identity') != page.get('event_identity'):
                self.reason = 'event_identity_changed'
                return False
            first = self.pages[0]['summary']
            if any(v != first[k] for k, v in page['summary'].items() if k != 'stamina_owned') or not (
                    first['stamina_owned'] <= page['summary']['stamina_owned'] <= first['stamina_owned'] + 1):
                self.reason = 'summary_changed'
                return False
            if any(key(a) == key(b) and a['count'] != b['count'] for a in self.rows for b in page['rows']):
                self.reason = 'row_count_changed'
                return False
        ok = super().run(context, argv)
        if self.complete and (len(self.rows) != page['summary']['stages']
                              or sum(r['count'] for r in self.rows) != page['summary']['times']):
            self.complete = False
            self.reason = 'incomplete_or_inconsistent_totals'
            return False
        return ok
