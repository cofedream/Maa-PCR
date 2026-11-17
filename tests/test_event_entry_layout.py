"""Unique event text may move within adventure content; ambiguity never clicks."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcr_maa.activity_navigation import ActivityEventEntry, ActivityEntryAbsent, absent_entry
from pcr_maa.event_native import ClosedAdventurePage


def word(text='剧情活动', box=None, score=.99):
    return dict(text=text, box=box or [377, 573, 96, 29], score=score)


class Context:
    def __init__(self, words, *, adventure=True, closed=False):
        self.words, self.adventure, self.closed = words, adventure, closed
        self.calls = []

    def run_recognition(self, name, image):
        self.calls.append(name)
        hit = self.adventure if name == 'ActivityAdventureGuard' else (
            self.closed if name == 'ClosedAdventurePageGuard' else bool(self.words))
        return SimpleNamespace(hit=hit, box=[75, 13, 94, 49], raw_detail={'all': self.words})


@pytest.mark.parametrize('text', ['剧情活动', '剧情 活动', '复刻'])
@pytest.mark.parametrize('box', [[10, 100, 100, 30], [377, 573, 100, 30],
                                 [750, 150, 100, 30], [1150, 620, 100, 30]])
def test_unique_supported_text_is_located_outside_the_old_fixed_roi(text, box):
    result = ActivityEventEntry().analyze(Context([word(text, box)]), SimpleNamespace(image=None))
    assert result is not None and result.box == box


@pytest.mark.parametrize('words,adventure', [
    ([word()], False), ([], True), ([word('联动讨伐')], True),
    ([word('剧情活动即将举办')], True), ([word(score=.89)], True),
    ([word(box=[100, 20, 100, 30])], True),
    ([word(box=[100, 680, 100, 30])], True),
    ([word(box=[1250, 500, 100, 30])], True),
    ([word(), word(box=[700, 300, 100, 30])], True),
    ([word(), word('复刻')], True),
    ([word(), word(box=[700, 300, 100, 30], score=.1)], True),
])
def test_unknown_ambiguous_or_wrong_page_has_no_click_target(words, adventure):
    context = Context(words, adventure=adventure)
    assert ActivityEventEntry().analyze(context, SimpleNamespace(image=None)) is None
    if not adventure:
        assert context.calls == ['ActivityAdventureGuard']


def test_exact_duplicate_ocr_box_is_one_target():
    entry = word()
    assert ActivityEventEntry().analyze(Context([entry, dict(entry)]), SimpleNamespace(image=None)) is not None


@pytest.mark.parametrize('target,x', [('current', 500), ('revival', 400)])
def test_selected_event_uses_only_its_label(target, x):
    marks = [word('剧情活动', [500, 580, 90, 27]), word('复刻', [400, 580, 55, 27])]
    result = ActivityEventEntry(target).analyze(Context(marks), SimpleNamespace(image=None))
    assert result.box[0] == x
    other = [m for m in marks if m['box'][0] != x]
    assert ActivityEventEntry(target).analyze(Context(other), SimpleNamespace(image=None)) is None


@pytest.mark.private_replay
@pytest.mark.parametrize('frame_name,target,xrange', [
    ('daily-adventure-title-20261115.png', 'current', (495, 607)),
    ('daily-adventure-title-20261115.png', 'revival', (385, 475)),
    ('activity-scope-adventure.png', 'current', None),
    ('activity-scope-adventure.png', 'revival', (495, 607)),
])
def test_explicit_entry_on_original_adventure_frames(frame_name, target, xrange):
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    from test_player_feedback_navigation import PROJECT, frame
    path = PROJECT / '.runtime/private-fixtures/hermes-pcr/live' / frame_name
    resource = native.load_resource()
    assert resource.override_pipeline({'EventEnter': {'next': []}, 'EventEnterGate': {'timeout': 1400, 'on_error': []}})
    controller = SavedImages([[frame(path)]])
    tasker = native.bind(resource, controller)
    with absent_entry(tasker, 'event', target):
        receipt = native.run(tasker, 'EventEnterGate')
    names = [n['name'] for n in receipt['nodes'] if n['completed']]
    if xrange is None:
        assert 'EventUnavailable' in names and not controller.clicks, names
    else:
        assert 'EventEnter' in names and len(controller.clicks) == 1, names
        x, y = controller.clicks[0]
        scale = frame(path).shape[1] / 1280
        assert xrange[0] <= x / scale <= xrange[1] and 557 <= y / scale < 612


@pytest.mark.parametrize('closed', [False, True])
def test_missing_entry_needs_positive_closed_layout_evidence(closed):
    result = ActivityEntryAbsent('event').analyze(Context([], closed=closed), SimpleNamespace(image=None))
    assert (result is not None) is closed


@pytest.mark.parametrize('cue', ['', '剧情活动', '复刻', '联动讨伐'])
def test_closed_layout_checks_event_cues_across_the_whole_frame_even_at_low_confidence(cue):
    words = [word(text, [left + 1, top + 1, 10, 10])
             for text, left, top, right, bottom in ClosedAdventurePage.ANCHORS]
    if cue:
        words.append(word(cue, [50, 150, 100, 30], score=.1))
    result = ClosedAdventurePage().analyze(Context(words), SimpleNamespace(image=None))
    assert (result is not None) is (not cue)


@pytest.mark.parametrize('failure', [RuntimeError, KeyError])
def test_dynamic_entry_fault_is_a_miss_with_traceback(failure, capsys):
    class Broken:
        def run_recognition(self, *args):
            raise failure('dynamic_entry_fault')
    assert ActivityEventEntry().analyze(Broken(), SimpleNamespace(image=None)) is None
    assert 'dynamic_entry_fault' in capsys.readouterr().err


@pytest.mark.parametrize('rejected', ['ActivityEventEntry', 'StoryEventNavigation'])
def test_partial_callback_registration_releases_already_registered_owners(rejected):
    class Resource:
        def __init__(self):
            self.callbacks = {}
        def register_custom_recognition(self, name, callback):
            if name == rejected:
                return False
            self.callbacks[name] = callback
            return True
        def unregister_custom_recognition(self, name):
            del self.callbacks[name]
    resource = Resource()
    with pytest.raises(RuntimeError, match='Cannot register'):
        with absent_entry(SimpleNamespace(resource=resource), 'event'):
            pytest.fail('A rejected callback must not be used')
    assert resource.callbacks == {}


@pytest.mark.private_replay
@pytest.mark.parametrize('borrow_context', [False, True])
def test_current_story_entry_is_clicked_instead_of_collaboration_raid(borrow_context):
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    from test_activity_expiry import borrowed
    path = Path(__file__).resolve().parents[1] / '.runtime/event-entry-layout-20261101/adventure-current.png'
    if not path.is_file():
        pytest.skip('Private current adventure frame missing; no live fallback')
    resource = native.load_resource()
    assert resource.override_pipeline({'EventEnter': {'next': []},
                                       'EventEnterGate': {'timeout': 1000}})
    controller = SavedImages([[native.read_image(path)]])
    tasker = native.bind(resource, controller)
    def read(owner):
        with absent_entry(owner, 'event'):
            receipt = native.run(owner, 'EventEnterGate')
        assert receipt['native_ok'], receipt
        assert any(n['name'] == 'EventEnter' and n['completed'] for n in receipt['nodes'])
        assert not any(n['name'] == 'EventUnavailable' and n['completed'] for n in receipt['nodes'])
    if borrow_context:
        borrowed(tasker, read)
    else:
        read(tasker)
    assert len(controller.clicks) == 1 and not controller.swipes
    x, y = controller.clicks[0]
    assert 350 <= x < 480 and 557 <= y < 612
