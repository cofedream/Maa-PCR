"""L2-6: the map-scroll guard classifies only MAP and keeps non-map zero-input.

Before this change `MaaNodeNavigator.scroll` overrode `LabyAutoplayMapGuard`
with `kind='map'` but no `probe_kinds`, so `SceneRecognition` ran the whole
scene (scope=None) on every pre-Swipe guard frame. The guard now passes
`probe_kinds=('map',)`: the scene reads the MAP rule plus the forced
interruption anchors (the plan's ≈14 sub-recognitions instead of ≈123), a
non-map frame still returns `hit=None` through the existing `kind='map'`
guard, and the `And` wrapper still sends no Swipe in that case.
"""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import numpy as np
import pytest

from pcr_maa import native
from pcr_maa.labyrinth import autoplay, scene
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.replay import SavedImages
from test_labyrinth_character_port import offline
from test_labyrinth_recognition_perf_r50 import SceneContext

DATA = Path(__file__).with_name('data')
MAP_FRAME = 'labyrinth-map-ex-20261022-target.png'
NON_MAP_FRAME = 'labyrinth-shop-relics-20261108.png'


def frame(name):
    image = cv2.imdecode(np.frombuffer((DATA / name).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (720, 1280, 3)
    return image


class FakeTasker:
    def __init__(self):
        self.resource = self

    def override_pipeline(self, nodes):
        self.nodes = nodes
        return True


def test_scroll_guard_declares_the_map_only_probe_scope(monkeypatch):
    navigator = autoplay.MaaNodeNavigator.__new__(autoplay.MaaNodeNavigator)
    navigator.tasker = FakeTasker()
    entries = []

    def fake_run(tasker, entry, reset_hits=()):
        entries.append(entry)
        return dict(native_ok=True, tasker_inited=True, task_id=1,
                    nodes=[dict(name=entry, completed=True,
                                action=dict(name='Swipe', success=True))])

    monkeypatch.setattr(native, 'run', fake_run)
    navigator.scroll(scene.Scene('map', {}, {}), 1)
    guard = navigator.tasker.nodes['LabyAutoplayMapGuard']
    assert guard['custom_recognition_param'] == dict(kind='map', probe_kinds=('map',))
    assert entries == ['LabyAutoplayMapScrollOnce']


def test_map_scope_reads_only_the_map_rule_and_keeps_interruption_anchors():
    keys = set(scene._RULES['map'][0]) | {'AREA'}
    image = np.zeros((720, 1280, 3), np.uint8)

    def read(**params):
        context = SceneContext(keys)
        result = context.observer.analyze(context, NS(
            image=image, custom_recognition_param=json.dumps(params)))
        return result, context.calls

    full, full_calls = read()
    scoped, scoped_calls = read(kind='map', probe_kinds=['map'])
    assert full.detail['kind'] == scoped.detail['kind'] == 'map'
    assert full.box is not None and scoped.box is not None
    # The full scene still pays for every anchor; the guard reads MAP plus the
    # forced DATE/DISCONNECT/base-rule anchors only (measured 133 -> 14
    # primitive calls here, 122 -> 14 native VisionNS lines on the same frame).
    assert len(full_calls) > 100 and len(scoped_calls) <= 20
    assert len(scoped_calls) * 5 < len(full_calls)
    scoped_scanned = set(scoped.detail['payload']['scanned_anchor_keys'])
    full_scanned = set(full.detail['payload']['scanned_anchor_keys'])
    assert scoped_scanned < full_scanned
    assert {'DATE', 'DISCONNECT'} <= scoped_scanned
    # Overlay pages that can cover the map are not silently scanned by the
    # guard; the guard's own `kind='map'` check remains the single acceptance.
    for overlay_anchor in ('ITEM_CLOSE', 'CHEST_TITLE', 'SHOP_CLOSE'):
        if overlay_anchor in full_scanned:
            assert overlay_anchor not in scoped_scanned


class ScopeSpy(scene.SceneRecognition):
    def __init__(self):
        super().__init__()
        self.params = []
        self.results = []

    def analyze(self, context, argv):
        raw = argv.custom_recognition_param
        self.params.append(json.loads(raw) if type(raw) is str else raw)
        result = super().analyze(context, argv)
        self.results.append(result)
        return result


@pytest.mark.parametrize('name,allowed', [(MAP_FRAME, True), (NON_MAP_FRAME, False)])
def test_native_scroll_guard_scope_and_input(name, allowed, tmp_path, monkeypatch, offline):
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    spy = ScopeSpy()
    resource = native.load_resource()
    assert resource.override_pipeline(scene.scene_pipeline())
    assert resource.register_custom_recognition(scene.NAME, spy)
    controller = SavedImages([[frame(name)]])
    tasker = native.bind(resource, controller)
    try:
        navigator = autoplay.MaaNodeNavigator(tasker)
        error = None
        try:
            navigator.scroll(scene.Scene('map', {}, {}), 1)
        except BatchStopped as failure:
            error = failure
        assert spy.params and all(
            params == dict(kind='map', probe_kinds=['map']) for params in spy.params)
        if allowed:
            assert error is None and controller.swipes == [[861, 418, 573, 418, 450]]
            assert not controller.clicks
        else:
            # Same rejection path as the old full-scene guard: no hit, no input.
            assert error is not None and str(error) == 'autoplay_native_unknown'
            assert not controller.swipes and not controller.clicks
            assert all(result.box is None for result in spy.results)
    finally:
        assert native.wait_job(tasker.post_stop(), seconds=10).succeeded
