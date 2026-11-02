"""Lost navigation clicks recover only on the same verified event home."""
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from pcr_maa import native
from pcr_maa.activity_navigation import absent_entry
from pcr_maa.event_native import NativeEvent, StoryEventNavigation
from pcr_maa.replay import SavedImages


ROOT = Path(__file__).resolve().parents[1]


def frame(path):
    path = ROOT / path
    if not path.is_file():
        pytest.skip('Private original frame missing; no live fallback')
    return native.read_image(path)


@pytest.mark.private_replay
@pytest.mark.parametrize('lost', [0, 1, 2])
def test_lost_home_click_reaches_list_without_repeating_list_click(lost):
    home = frame('.runtime/planning/event-mapgate-20261102/failed-event-home.png')
    map_ = frame('.runtime/pilot-readiness-20261002/live-event-navigation-20261002/04-event-map/after.png')
    list_ = frame('.runtime/pilot-readiness-20261002/live-event-preview-20261002/04-selection-view/after.png')
    controller = SavedImages([[home]] * (lost + 1) + [[map_], [list_]])
    resource = native.load_resource()
    assert resource.override_pipeline({'StoryEventMapGate': {'timeout': 4000}})
    tasker = native.bind(resource, controller)
    with absent_entry(tasker, 'event'):
        receipt = native.run(tasker, 'EventPageGate')
    names = [n['name'] for n in receipt['nodes'] if n['completed']]
    assert 'EventListReady' in names, receipt
    assert names.count('StoryEventRetryOpenQuests') == lost
    assert names.count('StoryEventOpenList') == 1
    assert len(controller.clicks) == lost + 2 and not controller.swipes
    port = NativeEvent(tasker, start='event')
    assert port._bind_story_identity(receipt)
    assert port.event_identity['period'] == '举办时间：10/3011:00~11/2204:59'


class HomeContext:
    def __init__(self, *, period='举办时间:10/30 11:00~11/22 04:59', hit=True):
        self.period, self.hit = period, hit

    def run_recognition(self, name, image):
        assert name == 'StoryEventHomeGuard'
        return SimpleNamespace(hit=self.hit, box=[92, 563, 122, 30], raw_detail={
            'all_of': [dict(name='StoryEventPeriod', detail=dict(filtered=[
                dict(text=self.period, score=.99)]))]})


def analyze(callback, context, kind):
    return callback.analyze(context, SimpleNamespace(image=None,
        custom_recognition_param=json.dumps(kind)))


def test_retry_requires_initial_same_identity_interval_and_finite_budget(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('pcr_maa.event_native.time.monotonic', lambda: clock[0])
    callback, context = StoryEventNavigation(), HomeContext()
    assert analyze(callback, context, 'retry') is None
    assert analyze(callback, context, 'initial') is not None
    assert analyze(callback, context, 'initial') is None
    assert analyze(callback, context, 'retry') is None
    for _ in range(2):
        clock[0] += 2
        assert analyze(callback, context, 'retry') is not None
    clock[0] += 2
    assert analyze(callback, context, 'retry') is None
    assert callback.attempts == 3


def test_changed_event_latches_rejection_even_if_original_period_returns(monkeypatch):
    callback, context = StoryEventNavigation(), HomeContext()
    assert analyze(callback, context, 'initial') is not None
    monkeypatch.setattr('pcr_maa.event_native.time.monotonic', lambda: callback.last_attempt + 3)
    assert analyze(callback, HomeContext(period='举办时间:11/30 11:00~12/22 04:59'), 'retry') is None
    assert analyze(callback, context, 'retry') is None
    assert callback.changed and callback.attempts == 1


@pytest.mark.parametrize('context', [HomeContext(hit=False), HomeContext(period='unreadable')])
def test_unverified_home_cannot_consume_a_retry(context, monkeypatch):
    callback = StoryEventNavigation()
    assert analyze(callback, HomeContext(), 'initial') is not None
    monkeypatch.setattr('pcr_maa.event_native.time.monotonic', lambda: callback.last_attempt + 3)
    assert analyze(callback, context, 'retry') is None
    assert callback.attempts == 1


@pytest.mark.private_replay
@pytest.mark.parametrize('borrow_context', [False, True])
@pytest.mark.parametrize('successor', ['same_home', 'unknown', 'popup', 'rejected_input'])
def test_stalled_or_unverified_navigation_never_exceeds_two_retries(borrow_context, successor):
    import numpy as np
    from test_activity_expiry import borrowed
    home = frame('.runtime/planning/event-mapgate-20261102/failed-event-home.png')
    image = home
    if successor == 'unknown':
        image = np.zeros_like(home)
    elif successor == 'popup':
        image = frame('.runtime/player-feedback-20261004-153502/logs/debug/on_error/2026.10.04-15.24.27.222_EventPageGate.png')
    class Controller(SavedImages):
        def click(self, x, y):
            super().click(x, y)
            return successor != 'rejected_input'
    controller = Controller([[home], [image]])
    resource = native.load_resource()
    assert resource.override_pipeline({'StoryEventMapGate': {'timeout': 3000}})
    tasker = native.bind(resource, controller)
    def run(owner):
        import gc
        with absent_entry(owner, 'event'):
            gc.collect()
            receipt = native.run(owner, 'EventPageGate')
        names = [n['name'] for n in receipt['nodes'] if n['completed']]
        assert 'EventListReady' not in names
        assert not set(names) & {'StoryEventOpenList', 'ExecConfirm', 'EventToggle'}
    if borrow_context:
        borrowed(tasker, run)
    else:
        run(tasker)
    assert len(controller.clicks) == (3 if successor == 'same_home' else 1)
    assert not controller.swipes


def test_pipeline_retry_is_only_a_guarded_home_navigation():
    pipeline = json.loads((native.RESOURCE / 'pipeline/event.json').read_text(encoding='utf-8'))
    assert pipeline['StoryEventOpenQuests']['all_of'] == ['StoryEventQuestsLabel', 'StoryEventNavigationInitial']
    assert pipeline['StoryEventMapGate']['next'] == ['StoryEventOpenList', 'StoryEventRetryOpenQuests']
    assert pipeline['StoryEventRetryOpenQuests'] == dict(recognition='And',
        all_of=['StoryEventQuestsLabel', 'StoryEventNavigationRetry'], action='Click',
        target=True, max_hit=2, post_delay=600, next=['StoryEventMapGate'],
        timeout=30000, on_error=['UnknownStop'])
