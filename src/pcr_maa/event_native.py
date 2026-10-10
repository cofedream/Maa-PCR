"""Event-specific Maa identity and row reader, sharing confirmation/settlement."""
import json
import re
import traceback
from contextlib import contextmanager
from maa.custom_recognition import CustomRecognition
from . import native
from .preset_scan import Collector, checked_glyph
from .preset_confirmation import ConfirmationPage
from .preset_execution_native import NativePreset, completed
from .event import valid_event_identity, is_story_h_title


class EventPage(CustomRecognition):
    def __init__(self, event_identity=None):
        super().__init__()
        if event_identity is not None and not valid_event_identity(event_identity):
            raise ValueError('Invalid story event identity')
        self.event_identity = dict(event_identity) if event_identity is not None else None

    def analyze(self, context, argv):
        try:
            return self.read(context, argv)
        except Exception:
            traceback.print_exc()
            return None

    def read(self, context, argv):
        kind = json.loads(argv.custom_recognition_param)
        if kind == 'preset_guard':
            return self.read_preset_guard(context, argv)
        evidence = {}

        def reco(name, override=None):
            r = context.run_recognition(name, argv.image, override)
            evidence[name] = dict(hit=bool(r and r.hit), raw=r.raw_detail if r else None)
            return r if r and r.hit else None

        def hit(name):
            return reco(name) is not None

        if not hit('EventListGuard') or not hit('EventCheckedLabel'):
            return None
        story = valid_event_identity(self.event_identity)
        if kind == 'short' and not story:
            return None
        top = hit('EventBlueTop') and hit('EventGrayBottom')
        bottom = hit('EventGrayTop') and hit('EventBlueBottom')
        blue, gray = hit('EventRailBlue'), hit('EventRailGray')
        rail = blue and gray
        if kind == 'short':
            if top or bottom or blue or gray:
                return None
        elif not rail or not {'top': top, 'bottom': bottom, 'page': not top and not bottom}[kind]:
            return None
        raw = reco('EventRowsOCR')
        words = raw.raw_detail.get('filtered', []) if raw else []
        rows, partial = [], []
        gaps = False
        for w in words:
            title = re.sub(r'\s+', '', w['text']).replace('－', '-').replace('—', '-').replace('–', '-')
            x, y, width, height = w['box']
            accepted = is_story_h_title(title) if story else re.fullmatch(r'[^\d]+\d+-\d+', title)
            if not accepted or x > 530:
                continue
            if y <= 188 or y+64 > 501:
                partial.append(w)
                continue

            def value(name, pattern):
                roi = list(context.get_node_data(name)['attach']['row_roi'])
                roi[1] += y
                r = reco(name, {name: {'roi': roi}})
                values = r.raw_detail.get('filtered', []) if r else []
                if len(values) != 1 or values[0].get('score', 0) < .9:
                    return None
                s = values[0]['text'].strip()
                return s if re.fullmatch(pattern, s) else None

            category = value('EventRowCategory', '活动')
            difficulty = value('EventRowDifficulty', '困难')
            remaining = value('EventRowRemaining', r'\d+[/／]\d+')
            a, b = map(int, re.split('[/／]', remaining)) if remaining else (None, None)
            if difficulty is None and (a, b) == (0, 3):
                badge = reco('EventExhaustedHard', {
                    'EventExhaustedHardText': {'roi': [281, y+41, 40, 16]},
                    'EventExhaustedHardGray': {'roi': [550, y+45, 20, 10]},
                    'EventExhaustedHardDimBadge': {'roi': [239, y+43, 32, 10]}})
                notice = value('EventRowExhausted', '已无剩余挑战次数。?')
                if badge and notice:
                    difficulty = '困难'
            check_evidence = {}
            checked = checked_glyph(context, argv.image, [1098, max(188, y-8), 80, 76],
                                    evidence=check_evidence)
            evidence['ScanCheckGlyph'] = dict(hit=check_evidence['hit'], raw=check_evidence['raw'])
            rows.append(dict(title=title, category=category, difficulty=difficulty, remaining=a, limit=b,
                             checked=checked, title_score=w['score'], ocr_box=w['box']))
            gaps |= not (category and difficulty and remaining and checked) or w['score'] < .9
        rows.sort(key=lambda r: r['ocr_box'][1])
        gaps |= not rows or any(b['ocr_box'][1]-a['ocr_box'][1] > 100 for a, b in zip(rows, rows[1:]))
        if kind in ('top', 'short'):
            gaps |= not rows or rows[0]['ocr_box'][1] > 217
        if kind == 'bottom':
            gaps |= any(w['box'][1]+64 > 501 for w in partial)
            # A bottom rail alone cannot excuse a missing last row.
            gaps |= not rows or rows[-1]['ocr_box'][1] < 420
        # Only a uniquely anchored, genuinely clipped bottom row may defer its
        # counter to the next page. It is never a complete row on this page.
        deferred = []
        clipped = [w for w in partial if kind != 'bottom' and rows
                   and w.get('score', 0) >= .9
                   and 65 <= w['box'][0] and w['box'][0]+w['box'][2] <= 530
                   and w['box'][1]+64 > 501 and w['box'][1]+w['box'][3] <= 501
                   and 64 <= w['box'][1]-rows[-1]['ocr_box'][1] <= 100]
        # Visible counters without a title remain gaps, except the clipped top row.
        counters = reco('EventCountersOCR')
        counts = [w for w in counters.raw_detail.get('filtered', [])
                  if re.fullmatch(r'\d+[/／]\d+', w['text'].strip())] if counters else []
        for w in counts:
            if kind != 'short' and w['box'][1] <= 238:
                continue
            if any(20 <= w['box'][1]-r['ocr_box'][1] <= 62 for r in rows):
                continue
            anchors = [p for p in clipped if 20 <= w['box'][1]-p['box'][1] <= 62]
            if (len(anchors) != 1 or w.get('score', 0) < .9
                    or not (585 <= w['box'][0] and w['box'][0]+w['box'][2] <= 648
                            and w['box'][1]+w['box'][3] <= 503)
                    or sum(20 <= c['box'][1]-anchors[0]['box'][1] <= 62 for c in counts) != 1):
                gaps = True
                continue
            p = anchors[0]
            title = re.sub(r'\s+', '', p['text']).replace('－', '-').replace('—', '-').replace('–', '-')
            a, b = map(int, re.split('[/／]', w['text'].strip()))
            deferred.append(dict(title=title, remaining=a, limit=b, title_box=p['box'], counter=w))
        if kind == 'short':
            # No rail is only a candidate. The complete last row must be followed
            # by the actual continuous gray list surface, through the content end.
            if gaps or partial or deferred:
                return None
            tail = context.get_node_data('EventShortBlankTail')['attach']
            start = rows[-1]['ocr_box'][1] + tail['tail_offset']
            height = tail['tail_bottom'] - start
            if height < tail['min_tail_height']:
                return None
            roi = [tail['tail_x'], start, tail['tail_width'], height]
            if not reco('EventShortBlankTail', {'EventShortBlankTail': {
                    'roi': roi, 'count': roi[2] * roi[3]}}):
                return None
        detail = dict(kind=kind, rows=rows, partial=partial, deferred=deferred,
                      gaps=bool(gaps), evidence=evidence)
        if story:
            detail['event_identity'] = dict(self.event_identity)
        return self.AnalyzeResult([50, 188, 1185, 315], detail)


    def read_preset_guard(self, context, argv):
        """Locate all amount glyphs on this action frame; keep plain Maa OCR."""
        if not valid_event_identity(self.event_identity):
            return None
        evidence = {}
        def reco(name):
            found = context.run_recognition(name, argv.image)
            evidence[name] = dict(hit=bool(found and found.hit), raw=found.raw_detail if found else None)
            return found if found and found.hit else None
        guard = reco('EventListGuard')
        if not guard or not reco('EventCheckedLabel'):
            return None
        # Read the full original numeric domain afresh, including every digit.
        # Do not reuse a narrowed ROI left by a prior frame or exact-value check.
        glyph = reco('StoryEventStaminaAfterGlyph')
        regions = glyph.raw_detail.get('filtered', []) if glyph else []
        full = context.get_node_data('StoryEventStaminaAfterGlyph')['recognition']['param']['roi']
        x, y, width, height = full
        boxes = [region.get('box') for region in regions]
        if (not boxes or any(not isinstance(box, list) or len(box) != 4
                or box[2] <= 0 or box[3] <= 0 or box[0] < x or box[1] < y
                or box[0]+box[2] > x+width or box[1]+box[3] > y+height for box in boxes)):
            return None
        left = max(x, min(box[0] for box in boxes)-3)
        top = max(y, min(box[1] for box in boxes)-3)
        right = min(x+width, max(box[0]+box[2] for box in boxes)+3)
        bottom = min(y+height, max(box[1]+box[3] for box in boxes)+3)
        roi = [left, top, right-left, bottom-top]
        # Preserve current expected (including the inherited opening guard's
        # exact quoted value), threshold and OCR output schema.
        if not context.override_pipeline({'ExecStaminaAfter': {'roi': roi, 'only_rec': True}}):
            return None
        amount = reco('ExecStaminaAfter')
        words = amount.raw_detail.get('filtered', []) if amount else []
        if len(words) != 1 or words[0].get('score', 0) < .9 or not re.fullmatch(r'\d{1,9}', words[0]['text'].strip()):
            return None
        return self.AnalyzeResult(guard.box, dict(event_identity=dict(self.event_identity),
            numeric_roi=roi, evidence=evidence))


class ClosedAdventurePage(CustomRecognition):
    """Accept only the observed ordinary adventure layout with no event entrance."""

    # Coordinates are in Maa's 1280x720 frame. Missing/replaced tiles fail closed.
    ANCHORS = (
        ('冒险', 60, 0, 190, 80), ('我的主页', 40, 670, 180, 720),
        ('主线关卡', 670, 245, 840, 320), ('特别', 920, 190, 1050, 255),
        ('关卡', 920, 225, 1050, 290), ('追忆的战场', 1080, 205, 1260, 285),
        ('探险', 700, 390, 800, 470), ('深域关卡', 910, 390, 1060, 470),
        ('团队战', 1100, 390, 1250, 470), ('竞技场', 670, 535, 800, 620),
        ('公主竞技场', 860, 535, 1020, 620), ('黎明界迷宫', 1060, 535, 1260, 620),
        ('驾车游', 490, 545, 610, 625),
    )

    def analyze(self, context, argv):
        try:
            result = context.run_recognition('ScreenOCR', argv.image)
            words = (result.raw_detail or {}).get('all', []) if result and result.hit else []
            if not words:
                return None

            def anchored(label, left, top, right, bottom):
                return any(w.get('text') == label and w.get('score', 0) >= .9
                           and left <= w['box'][0] and top <= w['box'][1]
                           and w['box'][0] + w['box'][2] <= right
                           and w['box'][1] + w['box'][3] <= bottom for w in words)

            if (not all(anchored(*anchor) for anchor in self.ANCHORS)
                    or any(w.get('score', 0) >= .9 and
                           re.search('活动|复刻|首领|困难', w.get('text', '')) for w in words)):
                return None
            return self.AnalyzeResult([45, 675, 120, 42],
                                      dict(layout='ordinary_adventure', anchors=len(self.ANCHORS),
                                           entry='驾车游', event_cue_found=False))
        except Exception:
            traceback.print_exc()
            return None




class EventCollector(Collector):
    """Keep partial-row obligations local to the event reader."""
    def run(self, context, argv):
        page = argv.reco_detail.raw_detail['best']['detail']
        if isinstance(page, str):
            page = json.loads(page)
        for pending in self.pages[-1].get('deferred', []) if self.pages else []:
            matches = [r for r in page['rows'] if r['title'] == pending['title']]
            if (len(matches) != 1
                    or matches[0]['ocr_box'][1] >= pending['title_box'][1]
                    or (matches[0]['remaining'], matches[0]['limit']) != (pending['remaining'], pending['limit'])):
                self.pages.append(page)
                self.complete = False
                self.reason = 'deferred_row_missing_or_conflicting'
                return False
        return super().run(context, argv)


class NativeEvent(NativePreset):
    def __init__(self, tasker, *, start):
        super().__init__(tasker)
        if start not in ('home', 'event'):
            raise ValueError('Event start must be home or event')
        self.start = start
        self.entered = False
        self._navigation_attempted = False
        self.map_name = None
        self.original_view = None
        self.closed_adventure_verified = False
        self.closed_home_notice = None
        self.event_identity = None
        self._event_identity_lost = False
        self._story_guard_active = False

    @contextmanager
    def _confirmation_scope(self):
        """Keep event confirmation nodes local to one synchronous port operation."""
        resource = self.tasker.resource
        names = ('ExecPresetGuard', 'ExecCancelled', 'ExecRowCategory',
                 'ExecOpenSweep', 'ExecReturnedPreset', 'ExecCount')
        story = valid_event_identity(self.event_identity) and not self._event_identity_lost
        if story:
            names += ('ExecStaminaAfter',)
        saved = {name: resource.get_node_data(name) for name in names}
        if any(not isinstance(value, dict) for value in saved.values()):
            raise RuntimeError('Cannot snapshot event confirmation scope')
        footer = saved['ExecCount']['attach']
        overrides = {
            # A previous normal preset uses a narrower numeric crop. Event
            # binds its declared default for this scope, then restores the
            # complete prior node even for nested scopes and failures.
            'ExecCount': {'roi': footer['default_roi'],
                          'only_rec': footer['default_only_rec']},
            'ExecPresetGuard': {'all_of': ['EventListGuard', 'EventCheckedLabel']},
            'ExecCancelled': {'all_of': ['EventListGuard', 'EventCheckedLabel']},
            'ExecRowCategory': {'expected': '^活动$'},
            'ExecOpenSweep': {'all_of': ['EventSweepButton', 'ExecPresetGuard', 'ExecOpenCount',
                                        'ExecOpenStaminaBefore', 'ExecOpenStaminaAfter']},
        }
        registered = False
        if story:
            overrides['ExecPresetGuard'] = dict(recognition='Custom',
                custom_recognition='EventPage', custom_recognition_param='preset_guard')
        try:
            # Confirmation may nest another event scope. Borrow the outer
            # binding, because the Context registry forbids duplicate names.
            if story and not self._story_guard_active:
                if not resource.register_custom_recognition('EventPage', EventPage(self.event_identity)):
                    raise RuntimeError('Cannot register event numeric guard')
                registered = self._story_guard_active = True
            if not resource.override_pipeline(overrides):
                raise RuntimeError('Cannot bind event confirmation scope')
            yield
        finally:
            # A stopped/blocked borrowed Context may refuse restoration. Keep
            # its original input gate; raise explicitly and abandon that root.
            try:
                try:
                    restored = resource.override_pipeline(saved)
                except BaseException as exc:
                    raise RuntimeError('Cannot restore event confirmation scope; root must stop') from exc
                if not restored:
                    raise RuntimeError('Cannot restore event confirmation scope; root must stop')
            finally:
                if registered:
                    self._story_guard_active = False
                    resource.unregister_custom_recognition('EventPage')

    def confirmation_page(self, *, stable_short=False):
        return ConfirmationPage(stable_short=stable_short, event=True, event_identity=self.event_identity)

    def return_guard(self, quote):
        return ['EventListGuard', 'EventCheckedLabel']

    def _bind_story_identity(self, navigation):
        nodes = [n for n in navigation['nodes']
                 if n['name'] == 'StoryEventOpenQuests' and n['completed']]
        if not nodes:
            return False
        words = []
        def find(value):
            if isinstance(value, dict):
                if value.get('name') == 'StoryEventPeriod':
                    words.extend((value.get('detail') or {}).get('filtered', []))
                for child in value.values():
                    find(child)
            elif isinstance(value, list):
                for child in value:
                    find(child)
        if len(nodes) != 1:
            raise RuntimeError('Ambiguous story event home evidence')
        find(nodes[0].get('recognition'))
        if len(words) != 1 or words[0].get('score', 0) < .9:
            raise RuntimeError('Story event period is not verified')
        period = re.sub(r'\s+', '', words[0]['text']).replace('～', '~')
        period = re.sub(r'^举办时间[:：]', '举办时间：', period)
        identity = dict(layout='story_event_h', period=period)
        if not valid_event_identity(identity):
            raise RuntimeError('Story event period is invalid')
        if self.event_identity is not None and self.event_identity != identity:
            self._event_identity_lost = True
            raise RuntimeError('Story event period changed; root must stop')
        self.event_identity = identity
        return True

    def _identity_matches(self, reading, quote=None):
        if self._event_identity_lost or reading.get('event_identity') != self.event_identity:
            return False
        if quote is not None and quote.get('event_identity') != self.event_identity:
            return False
        if self.event_identity is not None:
            return valid_event_identity(self.event_identity)
        return not any(is_story_h_title(row.get('title')) for row in reading.get('rows', []))

    def read(self):
        if self._event_identity_lost:
            return dict(native_ok=False, event_verified=False, list_complete=False,
                        rows=[], incomplete_reason='event_identity_changed', daily_complete=False)
        if not self.entered:
            if self._navigation_attempted:
                return dict(native_ok=False, event_verified=False, list_complete=False,
                            rows=[], incomplete_reason='event_entry_already_attempted_no_retry',
                            navigation=getattr(self, 'navigation', None), daily_complete=False)
            self._navigation_attempted = True
            from .activity_navigation import absent_entry, prepare_home
            if self.start == 'home':
                self.preparation = prepare_home(self.tasker)
                if not completed(self.preparation, 'ObserveHome'):
                    return dict(native_ok=False, rows=[], incomplete_reason='activity_start_unknown')
            with absent_entry(self.tasker, 'event'):
                navigation = native.run(self.tasker, 'EventFromHome' if self.start == 'home' else 'EventFromEvent',
                    reset_hits=('EventAdventure', 'EventEnter', 'EventLoginRewardClose',
                                'EventOpenQuests', 'EventOpenList', 'EventSwitchHard',
                                'StoryEventOpenQuests', 'StoryEventOpenList'))
            self.navigation = navigation
            if completed(navigation, 'EventUnavailable'):
                self.closed_adventure_verified = True
                return dict(native_ok=True, event_verified=False, list_complete=False, rows=[],
                            availability='closed', closed_verified=True, navigation=navigation,
                            daily_complete=False)
            if not completed(navigation, 'EventListReady'):
                return dict(native_ok=False, event_verified=False, list_complete=False, rows=[],
                            incomplete_reason='entry_unknown_or_not_supported', navigation=navigation,
                            daily_complete=False)
            if not self._bind_story_identity(navigation):
                node = next((n for n in navigation['nodes'] if n['name'] == 'EventMapName' and n['completed']), None)
                words = (node.get('recognition') or {}).get('filtered', []) if node else []
                if len(words) != 1 or words[0].get('score', 0) < .9:
                    return dict(native_ok=False, rows=[], event_verified=False, list_complete=False,
                                incomplete_reason='event_map_identity_unknown', navigation=navigation)
                self.map_name = re.sub(r'\s+', '', words[0]['text'])
            self.entered = True
        result = self.read_list()
        result['navigation'] = self.navigation
        matches = (all(is_story_h_title(r['title']) for r in result['rows'])
                   if valid_event_identity(self.event_identity) else
                   all(re.fullmatch(re.escape(self.map_name)+r'\d+-\d+', r['title']) for r in result['rows']))
        if not result['rows'] or not matches:
            result.update(event_verified=False, list_complete=False, incomplete_reason='event_map_title_mismatch')
        result['reading_receipt'] = str(native.save_receipt(result, 'event-reading'))
        return result

    def read_list(self):
        if self._event_identity_lost:
            return dict(native_ok=False, event_verified=False, list_complete=False, rows=[], pages=[],
                        incomplete_reason='event_identity_changed', daily_complete=False)
        collector = EventCollector()
        resource = self.tasker.resource
        if not resource.register_custom_recognition('EventPage', EventPage(self.event_identity)):
            raise RuntimeError('Cannot register event reader')
        try:
            if not resource.register_custom_action('CollectEvent', collector):
                raise RuntimeError('Cannot register event collector')
            receipt = native.run(self.tasker, 'EventScanStart')
        finally:
            resource.unregister_custom_recognition('EventPage')
            resource.unregister_custom_action('CollectEvent')
        if self.original_view is None:
            self.original_view = ('all' if any(n['name'] == 'EventToggle' and n['completed']
                                             for n in receipt['nodes']) else 'checked')
        complete = receipt['native_ok'] and collector.complete
        result = dict(receipt, event_verified=bool(collector.pages), view='checked' if complete else 'unknown',
                      list_complete=complete, rows=collector.rows, pages=collector.pages,
                      incomplete_reason=None if complete else collector.reason,
                      source='native event list reading', daily_complete=False)
        if valid_event_identity(self.event_identity):
            result['event_identity'] = dict(self.event_identity)
        return result

    def quote(self, reading):
        if not self._identity_matches(reading):
            return dict(verified=False, identity_verified=False, incomplete_reason='event_identity_mismatch')
        identity = {'scope': 'event-hard'}
        if self.event_identity is not None:
            identity['event_identity'] = dict(self.event_identity)
        with self._confirmation_scope():
            return self.read_quote(reading, identity)

    def read_confirmation(self):
        if self._event_identity_lost:
            return dict(native_ok=False, list_complete=False, rows=[], pages=[],
                        incomplete_reason='event_identity_changed')
        with self._confirmation_scope():
            result = super().read_confirmation()
        if self.event_identity is not None:
            if (not valid_event_identity(self.event_identity) or not result['pages']
                    or any(page.get('event_identity') != self.event_identity for page in result['pages'])):
                result.update(native_ok=False, list_complete=False, incomplete_reason='event_identity_mismatch')
            else:
                result['event_identity'] = dict(self.event_identity)
        return result

    def confirm(self, reading, quote):
        if not self._identity_matches(reading, quote):
            return dict(verified=False, incomplete_reason='event_identity_mismatch')
        with self._confirmation_scope():
            return super().confirm(reading, quote)

    def cancel_confirmation(self):
        with self._confirmation_scope():
            return super().cancel_confirmation()

    def submit(self, quote):
        snapshot = getattr(self, '_confirmed', None)
        if (self._event_identity_lost or quote.get('event_identity') != self.event_identity
                or snapshot is not None and not self._identity_matches(snapshot[0], quote)):
            self._confirmed = None
            raise native.NativeInputNotSent(dict(input_execution='not_sent',
                reason='event_identity_mismatch', dispatch_attempted=False))
        with self._confirmation_scope():
            return super().submit(quote)

    def recover_result(self, record):
        self.event_identity = record['before'].get('event_identity')
        try:
            with self._confirmation_scope():
                return super().recover_result(record)
        finally:
            self.event_identity = None  # next read must independently bind the current event

    def home(self):
        if self._event_identity_lost:
            return False
        if self.closed_adventure_verified:
            from .activity_navigation import return_home
            self.home_receipt = return_home(self.tasker)
            return completed(self.home_receipt, 'ObserveHome')
        if self.original_view == 'all':
            if not completed(native.run(self.tasker, 'EventRestoreView'), 'EventAllReady'):
                return False
        return completed(native.run(self.tasker, 'EventReturnHome'), 'ObserveHome')

    def _closed_adventure(self, entry):
        resource = self.tasker.resource
        if not resource.register_custom_recognition('ClosedAdventurePage', ClosedAdventurePage()):
            raise RuntimeError('Cannot register closed adventure reader')
        try:
            return native.run(self.tasker, entry)
        finally:
            resource.unregister_custom_recognition('ClosedAdventurePage')
