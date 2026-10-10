"""Known adventure entry absence is a normal, non-consuming route."""
from contextlib import contextmanager
import re
import traceback
from maa.custom_recognition import CustomRecognition
from . import native


class ActivityEntryAbsent(CustomRecognition):
    def __init__(self, module):
        super().__init__()
        self.module = module

    def analyze(self, context, argv):
        try:
            guard = context.run_recognition('ActivityAdventureGuard', argv.image)
            if not guard or not guard.hit:
                return None
            screen = context.run_recognition('ScreenOCR', argv.image)
            words = (screen.raw_detail or {}).get('all', []) if screen and screen.hit else []
            if not words:
                return None
            # Presence at any confidence prevents an absence claim. Available entry
            # routes run first; an unreadable entry remains unknown, never clicked.
            pattern = '深渊|讨伐' if self.module == 'abyss' else '活动|复刻|首领'
            for word in words:
                x, y, w, h = word['box']
                if (self.module == 'abyss' or 400 <= x <= 620 and 480 <= y <= 625):
                    if re.search(pattern, word.get('text', '')):
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


def absent_entry(tasker, module):
    return _reader(tasker, 'ActivityEntryAbsent', ActivityEntryAbsent(module))


def prepare_home(tasker):
    with _reader(tasker, 'ActivityHomePage', ActivityHomePage()):
        return native.run(tasker, 'ActivityPrepareHome', reset_hits=('ActivityAdventureHome',))


def return_home(tasker):
    return native.run(tasker, 'ActivityAdventureHome', reset_hits=('ActivityAdventureHome',))
