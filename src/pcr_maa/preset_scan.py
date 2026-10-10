"""Native page recognition and conservative OCR aggregation; no controller input."""
import json
import re
from maa.custom_recognition import CustomRecognition
from maa.custom_action import CustomAction
from .native import run

MAX_SWIPES = 8  # Must match ScanSwipe.max_hit in the native pipeline.


def identity_key(identity):
    """Main chapter-stage IDs are unique within category/difficulty.

    Keep full names for side stories, events and Deep regions, where numbered
    stages can repeat. Raw OCR titles remain untouched in every receipt.
    """
    title, category, difficulty = identity
    if category == '主线关卡' and difficulty in ('普通', '困难', '高难'):
        stage = re.search(r'(?<!\d)([1-9]\d{0,3}-[1-9]\d{0,2})$', title.strip())
        if stage:
            title = stage.group(1)
    return (title, category, difficulty)


def key(row):
    return identity_key((row['title'], row['category'], row['difficulty']))


def checked_glyph(context, image, roi, *, evidence=None):
    """One native blue component with the checked stroke's bounded geometry."""
    found = context.run_recognition('ScanCheckGlyph', image, {'ScanCheckGlyph': {'roi': roi}})
    raw = found.raw_detail if found else None
    if evidence is not None:
        evidence['raw'] = raw
        evidence['hit'] = bool(found and found.hit)
    return bool(found and found.hit and checked_glyph_detail(raw, roi))


def checked_glyph_detail(raw, roi):
    """The same geometry is checked for native frames and bound color receipts."""
    parts = raw.get('filtered', []) if isinstance(raw, dict) else []
    if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict):
        return False
    component = parts[0]
    if raw.get('best') != component:
        return False
    box, area = component.get('box'), component.get('count')
    if (not isinstance(box, list) or len(box) != 4
            or not all(type(v) is int for v in box) or type(area) is not int):
        return False
    x, y, width, height = box
    if not (roi[0] <= x and roi[1] <= y and x + width <= roi[0] + roi[2]
            and y + height <= roi[1] + roi[3] and 52 <= width <= 62
            and 36 <= height <= 47 and 850 <= area <= 1150):
        return False
    return 1.15 <= width / height <= 1.8 and .35 <= area / (width * height) <= .55


def unlimited_glyph(context, image, y, *, confirmation=False, evidence=None):
    """Native orange connected component: wide horizontal loop, never OCR '8'."""
    roi = list(context.get_node_data('NormalInfinity')['attach']['row_roi' if confirmation else 'scan_roi'])
    roi[1] += y
    found = context.run_recognition('NormalInfinity', image, {'NormalInfinity': {'roi': roi}})
    if evidence is not None:
        evidence['infinity'] = found.raw_detail if found else None
    parts = found.raw_detail.get('filtered', []) if found and found.hit else []
    if len(parts) != 1:
        return False
    x, top, width, height = parts[0]['box']
    if not (15 <= width <= 23 and 7 <= height <= 13 and 1.7 <= width / height <= 2.8):
        return False
    for fraction in (.25, .75):
        hole = [x + round(width * fraction) - 1, top + height // 2 - 1, 2, 2]
        check = context.run_recognition('NormalInfinityHole', image, {'NormalInfinityHole': {'roi': hole}})
        if evidence is not None:
            evidence.setdefault('holes', []).append(check.raw_detail if check else None)
        if not check or not check.hit:
            return False
    return True


class Page(CustomRecognition):
    def _checked(self, context, image, roi, *, evidence=None):
        return checked_glyph(context, image, roi, evidence=evidence)

    def analyze(self, context, argv):
        kind = json.loads(argv.custom_recognition_param)
        evidence = {}

        def recognize(name, override=None):
            result = context.run_recognition(name, argv.image, override)
            evidence[name] = {'hit': bool(result and result.hit),
                              'raw': result.raw_detail if result else None}
            return result

        def hit(name):
            result = recognize(name)
            return bool(result and result.hit)

        if not hit('ScanSlotGuard') or not hit('ScanCheckedLabel'):
            return None
        top = hit('ScanBlueTop') and hit('ScanGrayBottom')
        bottom = hit('ScanGrayTop') and hit('ScanBlueBottom')
        short = hit('ScanNoRail')
        rail = hit('ScanRailBlue') and hit('ScanRailGray')
        if not {'top': top and rail, 'bottom': bottom and rail,
                'short': short, 'page': rail and not short and not bottom}[kind]:
            return None
        raw = recognize('ScanOCR')
        # Maa's `all` includes rejected diagnostic candidates. Keep it in
        # evidence, but only use accepted OCR with the pipeline's 0.8 floor.
        words = [w for w in raw.raw_detail.get('filtered', [])
                 if w.get('score', 0) >= 0.8] if raw and raw.hit else []

        def inside(x1, y1, x2, y2):
            return [w for w in words if x1 <= w['box'][0] and w['box'][0] + w['box'][2] <= x2
                    and y1 <= w['box'][1] and w['box'][1] + w['box'][3] <= y2]

        def field(candidates, allowed):
            values = {v['text'].strip() for v in candidates}
            return next(iter(values)) if len(values) == 1 and values <= allowed else None

        rows, partial = [], []
        invalid_fields = False
        # Anchor on each actual title box, never on three fixed row positions.
        for word in inside(65, 228, 530, 503):
            title = word['text'].strip()
            if not re.search(r'\d+\s*[-－—–]\s*\d+', title):
                continue
            x, y, w, h = word['box']
            # 18-slot1 has a complete title at y=234. Only the OCR crop edge,
            # not the old fixed-row margin, establishes top truncation.
            if y <= 228 or y + 64 > 501:
                partial.append(word)
                continue
            counts = [v for v in inside(585, y + 22, 648, y + 61) if v.get('score', 0) >= 0.8]
            matches = [re.fullmatch(r'\s*(\d+)\s*[/／]\s*(\d+)\s*', v['text']) for v in counts]
            matches = [m for m in matches if m]
            # Vocabulary evidenced by saved main/side-story/deep OCR receipts;
            # unsupported labels and competing accepted candidates stay unknown.
            category = field(inside(75, y + 24, 227, y + 62), {'主线关卡', '外传', '深域关卡'})
            difficulty_words = inside(229, y + 24, 369, y + 62)
            difficulty = field(difficulty_words, {'困难', '高难', '普通'})
            infinity_evidence = {}
            unlimited = (category == '主线关卡' and difficulty == '普通' and not matches
                         and unlimited_glyph(context, argv.image, y, evidence=infinity_evidence))
            invalid_fields = invalid_fields or category is None or (
                bool(difficulty_words) if category == '深域关卡' else difficulty is None)
            check_evidence = {}
            checked = self._checked(context, argv.image, [1098, max(230, y-8), 80, 76],
                                    evidence=check_evidence)
            evidence['ScanCheckGlyph'] = dict(hit=check_evidence['hit'], raw=check_evidence['raw'])
            rows.append({'title': re.sub(r'\s+', '', title).replace('－', '-').replace('—', '-').replace('–', '-'),
                         'category': category, 'difficulty': difficulty,
                         'remaining': int(matches[0][1]) if len(matches) == 1 else None,
                         'limit': int(matches[0][2]) if len(matches) == 1 else None,
                         'unlimited': unlimited,
                         'infinity_evidence': infinity_evidence,
                         'checked': checked,
                         'check_evidence': check_evidence['raw'],
                         'title_score': word.get('score'), 'ocr_box': word['box']})
        rows.sort(key=lambda row: row['ocr_box'][1])
        # A visible count without a matching title is a recognition gap, not an absent row.
        orphan_counts = [v for v in inside(585, 250, 648, 501)
                         if re.fullmatch(r'\s*\d+\s*[/／]\s*\d+\s*', v['text'])
                         and not any(22 <= v['box'][1] - box[1] <= 61
                                     for box in ([r['ocr_box'] for r in rows]
                                                 + [w['box'] for w in partial if w['box'][1] <= 228]))]
        # A cropped top title may have been fully read on the preceding page.
        # Keep its visible fields as evidence; only Collector can verify continuity.
        top_edge_counts = []
        unresolved = []
        for word in orphan_counts:
            y = word['box'][1]
            category = field(inside(75, y - 3, 227, y + 35), {'主线关卡', '外传', '深域关卡'})
            difficulty_words = inside(229, y - 3, 369, y + 35)
            difficulty = field(difficulty_words, {'困难', '高难', '普通'})
            if (kind in ('page', 'bottom') and y <= 289 and category
                    and (not difficulty_words if category == '深域关卡' else difficulty)):
                remaining, limit = map(int, re.findall(r'\d+', word['text']))
                top_edge_counts.append(dict(category=category, difficulty=difficulty,
                                            remaining=remaining, limit=limit))
            else:
                unresolved.append(word)
        gaps = invalid_fields or bool(unresolved) or any((r['title_score'] or 0) < 0.8 for r in rows)
        gaps = gaps or any(b['ocr_box'][1] - a['ocr_box'][1] > 100
                                         for a, b in zip(rows, rows[1:]))
        if kind in ('top', 'short'):
            gaps = gaps or not rows or rows[0]['ocr_box'][1] > 252
        if kind == 'short':
            gaps = gaps or bool(partial)
        if kind == 'bottom':
            gaps = gaps or any(w['box'][1] + 64 > 501 for w in partial)
        detail = {'kind': kind, 'rows': rows, 'partial': partial, 'evidence': evidence, 'gaps': gaps,
                  'top_edge_counts': top_edge_counts, 'view': 'checked', 'top': top, 'bottom': bottom, 'short': short}
        return self.AnalyzeResult([50, 228, 1185, 275], detail)


class Collector(CustomAction):
    def __init__(self):
        super().__init__()
        self.pages = []
        self.rows = []
        self.reason = 'slot_view_or_position_unverified'
        self.complete = False
        self.no_new_pages = 0

    def run(self, context, argv):
        # Initial page plus at most eight post-swipe observations. Also bound
        # direct callbacks; native run retains its existing total deadline.
        if len(self.pages) >= MAX_SWIPES + 1:
            return False
        page = argv.reco_detail.raw_detail['best']['detail']
        if isinstance(page, str):
            page = json.loads(page)
        preceding = self.pages[-1]['rows'] if self.pages else []
        self.pages.append(page)
        current = page['rows']
        previous = {key(r) for r in self.rows}
        new = [r for r in current if key(r) not in previous]
        adjacent = {key(r): r for r in preceding}
        shared = [r for r in current if key(r) in adjacent]
        conflicts = any(key(a) == key(b) and (a['remaining'], a['limit']) != (b['remaining'], b['limit'])
                        for a in self.rows for b in current)
        # Two visible rows with the same full identity are ambiguous even in
        # the first frame; do not silently append or collapse either one.
        conflicts = conflicts or len({key(r) for r in current}) != len(current)
        self.rows.extend(new)
        if len(self.pages) == 1 and page['kind'] not in ('top', 'short'):
            self.reason = 'started_below_top'
            return False
        if page['gaps'] or not current or any((r['remaining'] is None or r['limit'] is None) and not r.get('unlimited') or not r['category']
                              or not r['checked'] or (r['category'] in ('主线关卡', '外传') and not r['difficulty'])
                              for r in current):
            self.reason = 'empty_or_unreadable_rows'
            return False
        edge = page.get('top_edge_counts', [])
        if edge:
            first = next((i for i, row in enumerate(preceding) if key(row) == key(current[0])), 0)
            if (len(edge) != 1 or first == 0
                    or any(preceding[first - 1].get(name) != value for name, value in edge[0].items())):
                self.reason = 'unverified_top_edge_row'
                return False
        if conflicts:
            self.reason = 'conflicting_rows'
            return False
        if len(self.pages) > 1 and not shared:
            self.reason = 'no_overlap'
            return False
        # OCR coordinates can jitter or remain still across several short
        # swipes. Neither motion nor repeated frames establish the bottom.
        # Spend only the existing native budget while preserving coverage.
        self.no_new_pages = 0 if new else self.no_new_pages + 1
        if page['kind'] in ('bottom', 'short'):
            self.complete = True
            self.reason = None
        elif len(self.pages) >= MAX_SWIPES + 1:
            self.reason = ('no_progress_without_bottom' if self.no_new_pages
                           else 'scroll_limit_or_bottom_unverified')
            return False
        else:
            self.reason = 'scroll_limit_or_bottom_unverified'
        return True


def scan(tasker, slot, source='offline replay'):
    if type(slot) is not int or not 1 <= slot <= 7:
        raise ValueError('slot must be 1..7')
    collector, page = Collector(), Page()
    resource = tasker.resource
    if not resource.register_custom_recognition('PresetPage', page):
        raise RuntimeError('Cannot register native preset page recognition')
    if not resource.register_custom_action('CollectPresetPage', collector):
        resource.unregister_custom_recognition('PresetPage')
        raise RuntimeError('Cannot register native preset collector')
    # Only fixed slot identity and the continuation of the accepted navigation vary.
    try:
        if not resource.override_pipeline({'PresetsReached': {'next': ['ScanSlotGate']},
                                'ScanSlotGuard': {'all_of': ['PresetsReached', f'SelectedSlot{slot}']},
                                'ScanSelectClick': {'target': [140 + (slot-1)*157, 110]}}):
            raise RuntimeError('Cannot configure fixed slot pipeline')
        result = run(tasker, 'InspectPreset')
    finally:
        restored = resource.override_pipeline({'PresetsReached': {'next': []}})
        removed_page = resource.unregister_custom_recognition('PresetPage')
        removed_collector = resource.unregister_custom_action('CollectPresetPage')
        if not (restored and removed_page and removed_collector):
            raise RuntimeError('Cannot release preset readers or restore navigation')
    names = [n['name'] for n in result['nodes'] if n['completed']]
    complete = collector.complete and result['native_ok']
    if not collector.pages and 'ScanResetToggle' in names:
        collector.reason = 'reset_top_unverified'
    result.update(source=source, requested_slot=slot,
                  selected_slot=slot if 'ScanSlotReady' in names else None,
                  view='checked' if complete else 'unknown',
                  last_verified_view='checked' if 'ScanChecked' in names else None,
                  view_evidence='pages and native node recognition; unknown stop has no fresh verification',
                  rows=collector.rows, pages=collector.pages,
                  visible_rows=collector.pages[-1]['rows'] if collector.pages else [],
                  list_complete=complete, incomplete_reason=None if complete else (
                      collector.reason or 'native_task_incomplete'),
                  all_selected_exhausted=all(r['remaining'] == 0 for r in collector.rows) if complete and collector.rows else None,
                  has_remaining=any(r.get('unlimited') or (r['remaining'] or 0) > 0 for r in collector.rows),
                  empty_list=False if collector.rows else None,
                  daily_complete=False, view_restored=False,
                  stop_node=names[-1] if names else None,
                  action_limits={'navigation_clicks': 3, 'slot_clicks': 1, 'view_clicks': 4, 'list_swipes': MAX_SWIPES})
    return result
