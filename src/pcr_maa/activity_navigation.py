"""Known adventure entry absence is a normal, non-consuming route."""
from contextlib import contextmanager, ExitStack
import re
import traceback
from maa.custom_recognition import CustomRecognition
from . import native


EVENT_LABELS = {'current': '剧情活动', 'revival': '复刻'}


class ActivityEventEntry(CustomRecognition):
    def __init__(self, target=None):
        super().__init__()
        self.label = EVENT_LABELS[target] if target is not None else None

    def analyze(self, context, argv):
        try:
            guard = context.run_recognition('ActivityAdventureGuard', argv.image)
            if not guard or not guard.hit:
                return None
            screen = context.run_recognition('ScreenOCR', argv.image)
            words = (screen.raw_detail or {}).get('all', []) if screen and screen.hit else []
            candidates = {}
            for word in words:
                text = re.sub(r'\s+', '', word.get('text', ''))
                x, y, w, h = word['box']
                # Exclude the title and persistent bottom navigation, not moving tiles.
                if ((text == self.label if self.label else text in EVENT_LABELS.values()) and w > 0 and h > 0
                        and 0 <= x < x + w <= 1280 and 80 <= y < y + h <= 660):
                    candidates[(text, x, y, w, h)] = word
            if not candidates and self.label:
                node = 'EventRevivalLabel' if self.label == '复刻' else 'EventStoryActivityLabel'
                local = context.run_recognition(node, argv.image)
                if local and local.hit:
                    for word in (local.raw_detail or {}).get('filtered', []):
                        if word.get('text') == self.label:
                            candidates[(self.label, *word['box'])] = word
            if len(candidates) != 1:
                return None
            word = next(iter(candidates.values()))
            if not .9 <= word.get('score', 0) <= 1:
                return None
            return self.AnalyzeResult(word['box'], dict(entry=re.sub(r'\s+', '', word['text']),
                score=word['score'], layout='adventure_content'))
        except Exception:
            traceback.print_exc()
            return None


class ActivityEntryAbsent(CustomRecognition):
    def __init__(self, module, target=None):
        super().__init__()
        self.module = module
        self.target = target

    def analyze(self, context, argv):
        try:
            guard = context.run_recognition('ActivityAdventureGuard', argv.image)
            if not guard or not guard.hit:
                return None
            if self.module == 'event':
                closed = context.run_recognition('ClosedAdventurePageGuard', argv.image)
                if not closed or not closed.hit:
                    if not self.target:
                        return None
                    # In the single-event layout, 驾车游 sits immediately left of the only event.
                    other = 'EventRevivalLabel' if self.target == 'current' else 'EventStoryActivityLabel'
                    label = context.run_recognition(other, argv.image, {other: {'roi': [490, 557, 120, 55]}})
                    car = context.run_recognition('EventSingleAdventureCar', argv.image)
                    if not (label and label.hit and car and car.hit):
                        return None
                    screen = context.run_recognition('ScreenOCR', argv.image)
                    words = (screen.raw_detail or {}).get('all', []) if screen and screen.hit else []
                    if not words or any(EVENT_LABELS[self.target] in re.sub(r'\s+', '', w['text']) for w in words):
                        return None
                    return self.AnalyzeResult(guard.box, dict(module='event', target=self.target,
                        availability='closed', closed_verified=True, layout='single_other_event'))
                return self.AnalyzeResult(guard.box, dict(module=self.module,
                    availability='closed', closed_verified=True, layout='ordinary_adventure'))
            screen = context.run_recognition('ScreenOCR', argv.image)
            words = (screen.raw_detail or {}).get('all', []) if screen and screen.hit else []
            if not words:
                return None
            # Presence at any confidence prevents an absence claim. Available entry
            # routes run first; an unreadable entry remains unknown, never clicked.
            for word in words:
                if re.search('深渊|讨伐', word.get('text', '')):
                    return None
            return self.AnalyzeResult(guard.box, dict(module=self.module,
                availability='closed', closed_verified=True, layout='ordinary_adventure'))
        except Exception:
            traceback.print_exc()
            return None


class ActivityHomePage(CustomRecognition):
    def analyze(self, context, argv):
        try:
            # Keep the existing home OCR isolated from adventure's overlapping ROIs.
            result = context.run_recognition('ObserveHome', argv.image)
            if result and result.hit:
                return self.AnalyzeResult(result.box, dict(page='home'))
            return None
        except Exception:
            traceback.print_exc()
            return None


@contextmanager
def _reader(tasker, name, callback):
    resource = tasker.resource
    if not resource.register_custom_recognition(name, callback):
        raise RuntimeError('Cannot register activity availability reader')
    try:
        yield
    finally:
        resource.unregister_custom_recognition(name)


@contextmanager
def absent_entry(tasker, module, target=None):
    with ExitStack() as readers:
        readers.enter_context(_reader(tasker, 'ActivityEntryAbsent', ActivityEntryAbsent(module, target)))
        if module == 'event':
            from .event_native import ClosedAdventurePage, StoryEventNavigation
            readers.enter_context(_reader(tasker, 'ClosedAdventurePage', ClosedAdventurePage()))
            readers.enter_context(_reader(tasker, 'ActivityEventEntry', ActivityEventEntry(target)))
            readers.enter_context(_reader(tasker, 'StoryEventNavigation', StoryEventNavigation()))
        yield


def prepare_home(tasker):
    with _reader(tasker, 'ActivityHomePage', ActivityHomePage()):
        return native.run(tasker, 'ActivityPrepareHome', reset_hits=('ActivityAdventureHome',))


def return_home(tasker):
    return native.run(tasker, 'ActivityAdventureHome', reset_hits=('ActivityAdventureHome',))
