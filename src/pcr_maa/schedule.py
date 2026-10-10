"""Read only section counts. Navigation is confined to schedule.json."""
import hashlib
import json
import math
import re
from uuid import uuid4

import cv2
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.define import AlgorithmEnum

from . import native
from .native import run

LABELS = {'notifications': '来自可可萝的通知', 'delegated': '交给可可萝'}


def section_counts(words):
    """Associate a count only with a visible, same-line section heading."""
    if not words:
        raise ValueError('unreadable_schedule_list')
    found = {}
    for key, title in LABELS.items():
        headings = [w for w in words if re.sub(r'\s+', '', w['text']) == title
                    and 450 <= w['box'][0] <= 720 and w.get('score', 0) >= .8]
        if len(headings) > 1:
            raise ValueError('ambiguous_section')
        if not headings:
            continue
        h = headings[0]
        counts = [w for w in words if 960 <= w['box'][0] <= 1025
                  and abs((w['box'][1] + w['box'][3]/2) - (h['box'][1] + h['box'][3]/2)) <= 14]
        match = re.fullmatch(r'\s*(\d+)\s*[/／]\s*(\d+)\s*', counts[0]['text']) if len(counts) == 1 else None
        if not match or counts[0].get('score', 0) < .8:
            raise ValueError('unreadable_section_count')
        done, total = map(int, match.groups())
        if total <= 0 or done > total:
            raise ValueError('invalid_section_count')
        found[key] = {'completed': done, 'total': total, 'count_shows_complete': done == total,
                      'heading': h, 'count': counts[0]}
    # A detached count must never be assigned using the previous frame's heading.
    for w in words:
        if 960 <= w['box'][0] <= 1025 and re.fullmatch(r'\s*\d+\s*[/／]\s*\d+\s*', w['text']):
            if not any(w == item['count'] for item in found.values()):
                raise ValueError('count_without_section')
    return found


class SchedulePage(CustomRecognition):
    error = None

    @staticmethod
    def day_change_text(context, image, node, roi, pattern):
        result = context.run_recognition(node, image)
        if result is None or result.algorithm != AlgorithmEnum.OCR or result.hit is not True:
            return False
        detail = result.raw_detail
        if not isinstance(detail, dict) or not isinstance(detail.get('all'), list) or len(detail['all']) != 1:
            return False
        word = detail['all'][0]
        return (isinstance(word, dict) and word.get('box') == roi
                and isinstance(word.get('text'), str)
                and type(word.get('score')) in (int, float)
                and math.isfinite(word['score']) and word['score'] >= .8
                and re.fullmatch(pattern, re.sub(r'\s+', '', word['text'])) is not None)

    def day_change_detected(self, context, image):
        # Both fixed popup labels must be read from the failed guard's frame.
        return (self.day_change_text(context, image, 'ScheduleDayChangeTitle',
                                     [540, 170, 180, 65], r'日期变更')
                and self.day_change_text(context, image, 'ScheduleDayChangeBody',
                                         [530, 335, 190, 60], r'日期已变更[。.]?'))

    def analyze(self, context, argv):
        try:
            return self.read(context, argv)
        except Exception as exc:
            # Do not let exceptions cross the SDK's ctypes callback boundary.
            self.error = f'schedule_evidence_failed: {exc}'
            return None

    def read(self, context, argv):
        guard = context.run_recognition('ScheduleGuard', argv.image)
        if not guard or not guard.hit:
            if self.day_change_detected(context, argv.image):
                self.error = 'game_day_changed_requires_relogin'
            return None
        ocr = context.run_recognition('ScheduleOCR', argv.image)
        words = ocr.raw_detail.get('all', []) if ocr else []
        from .native import RUNTIME
        path = RUNTIME / ('schedule-frame-' + uuid4().hex + '.png')
        if not native.write_png(path, argv.image):
            raise RuntimeError('Cannot save schedule source image')
        error = None
        try:
            counts = section_counts(words)
        except ValueError as exc:
            counts, error = {}, str(exc)
        detail = {'sections': counts, 'error': error, 'words': words, 'screenshot': str(path),
                  'image_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'guard': guard.raw_detail}
        return self.AnalyzeResult([250, 118, 790, 478], detail)


class Collector(CustomAction):
    def __init__(self):
        super().__init__()
        self.pages = []
        self.sections = {}
        self.decision = 'unknown'
        self.reason = 'page_unverified'
        self.previous_words = None
        self.previous_direction = None

    def run(self, context, argv):
        self.decision = 'unknown'
        try:
            return self.collect(argv)
        except Exception as exc:
            self.reason = f'schedule_collection_failed: {exc}'
            return True

    def collect(self, argv):
        page = argv.reco_detail.raw_detail['best']['detail']
        if isinstance(page, str):
            page = json.loads(page)
        self.pages.append(page)
        self.decision = 'unknown'
        self.reason = page['error']
        if self.reason:
            return True
        for key, value in page['sections'].items():
            old = self.sections.get(key)
            if old and (old['completed'], old['total']) != (value['completed'], value['total']):
                self.reason = 'conflicting_section_count'
                return True
            self.sections[key] = value | {'screenshot': page['screenshot']}
        if len(self.sections) == 2:
            self.decision, self.reason = 'done', None
            return True
        direction = 'down' if 'notifications' in self.sections else 'up'
        signature = [(w['text'], w['box']) for w in page['words']]
        if self.previous_words == signature and self.previous_direction == direction:
            self.reason = 'no_progress'
            return True
        self.previous_words, self.previous_direction = signature, direction
        self.decision, self.reason = direction, 'section_unseen_or_scroll_limit'
        return True


class Decision(CustomRecognition):
    def __init__(self, collector):
        super().__init__()
        self.collector = collector

    def analyze(self, context, argv):
        try:
            if self.collector.decision != json.loads(argv.custom_recognition_param):
                return None
            guard = context.run_recognition('ScheduleGuard', argv.image)
            if not guard or not guard.hit:
                return None
            return self.AnalyzeResult([250, 118, 790, 478], {'decision': self.collector.decision})
        except Exception as exc:
            self.collector.decision = 'unknown'
            self.collector.reason = f'schedule_guard_failed: {exc}'
            return None


def inspect_schedule(tasker, *, start, source='offline replay'):
    if start not in ('home', 'schedule'):
        raise ValueError('start must be home or schedule')
    collector = Collector()
    page, decision = SchedulePage(), Decision(collector)
    resource = tasker.resource
    if not (resource.register_custom_recognition('SchedulePage', page)
            and resource.register_custom_recognition('ScheduleDecision', decision)
            and resource.register_custom_action('CollectSchedule', collector)):
        raise RuntimeError('Cannot register native schedule readers')
    try:
        result = run(tasker, 'InspectScheduleHome' if start == 'home' else 'InspectScheduleOpen')
    finally:
        removed_page = resource.unregister_custom_recognition('SchedulePage')
        removed_decision = resource.unregister_custom_recognition('ScheduleDecision')
        removed_collector = resource.unregister_custom_action('CollectSchedule')
        if not (removed_page and removed_decision and removed_collector):
            raise RuntimeError('Cannot release schedule readers')
    names = [n['name'] for n in result['nodes'] if n['completed']]
    complete = result['native_ok'] and 'ScheduleDone' in names and collector.decision == 'done' and not page.error
    def section(key):
        value = collector.sections.get(key)
        return {'status': 'observed' if complete and value else 'unknown',
                'completed': value['completed'] if value else None,
                'total': value['total'] if value else None,
                'count_shows_complete': value['count_shows_complete'] if complete and value else None,
                'evidence': value}
    result.update(source=source, start=start, pages=collector.pages,
                  notifications=section('notifications'), delegated=section('delegated'),
                  inspection_complete=complete, navigation_complete=bool(collector.pages),
                  incomplete_reason=None if complete else (page.error or collector.reason or 'native_stop_or_page_unverified'),
                  daily_complete=False, scope='section counts only; no task inventory or execution',
                  action_limits={'home_clicks': 1, 'up_swipes': 8, 'down_swipes': 8},
                  schedule_executed=False)
    return result
