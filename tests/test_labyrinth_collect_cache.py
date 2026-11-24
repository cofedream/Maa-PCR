"""L1-2: locate/preview share one `NodePixels.collect` only on an identical frame.

`RouteTargetRecognition.analyze` used to run the 1.17-1.28 s node-body scan twice
per map step (`locate` then `preview`). The observer now memoizes the computed
columns under the exact sha256 of the frame pixels and the route-target
lifecycle: an identical frame reuses the scan, any different/missing hash or a
page/target change recomputes, and `preview` still derives its click box from its
own same-frame columns.
"""
from hashlib import sha256
from types import SimpleNamespace as NS
import json

import numpy as np
import pytest

from pcr_maa.labyrinth import autoplay, scene
from test_labyrinth_autoplay import route_for
from test_labyrinth_recognition_perf_r50 import SceneContext

MAP_KEYS = set(scene._RULES['map'][0]) | {'AREA'}
JOINED_KEYS = set(scene._RULES['joined'][0])
COLUMNS_A = [[dict(block_type=2, active=True, box=[400, 400, 100, 100])]]
COLUMNS_B = [[dict(block_type=2, active=True, box=[600, 400, 100, 100])]]


class Pixels:
    """Fake node-body scan; counts how often the expensive pass ran."""

    def __init__(self, columns=None):
        self.columns = columns or COLUMNS_A
        self.calls = 0

    def collect(self, image):
        self.calls += 1
        return self.columns


class FramePixels(Pixels):
    def __init__(self, by_digest):
        super().__init__()
        self.by_digest = by_digest

    def collect(self, image):
        self.calls += 1
        return self.by_digest[sha256(image.tobytes()).hexdigest()]


class FinalBossPixels(Pixels):
    def __init__(self, columns):
        super().__init__(columns)
        self.boss_calls = 0

    def final_boss(self, image):
        self.boss_calls += 1
        return self.columns


def frame(value=0):
    image = np.zeros((720, 1280, 3), np.uint8)
    image[..., 0] = value
    return image


def digest(image):
    return sha256(image.tobytes()).hexdigest()


def context(keys=None):
    return SceneContext(MAP_KEYS if keys is None else keys)


def argv(image, mode, **extra):
    return NS(image=image, custom_recognition_param=json.dumps(dict(mode=mode, **extra)))


def observer(pixels, route=None):
    route = route or route_for([1, 2])
    callback = autoplay.RouteTargetRecognition(pixels)
    callback.route = route
    callback.current = route.nodes[0]
    return callback, route


def test_identical_frame_reuses_one_collect_for_locate_and_preview():
    pixels = Pixels()
    callback, _ = observer(pixels)
    ctx = context()
    image = frame()
    located = callback.analyze(ctx, argv(image, 'read'))
    preview = callback.analyze(ctx, argv(image.copy(), 'preview'))
    assert pixels.calls == 1
    assert located.detail['status'] == preview.detail['status'] == 'target_observed'
    # The existing preview click point: center x, 32% down the same-frame box.
    assert preview.box == [450, 432, 1, 1]
    assert preview.detail['target'] == located.detail['target']
    assert preview.detail['columns'] == located.detail['columns']
    assert preview.detail['frame_pixel_sha256'] == digest(image) == located.detail['frame_pixel_sha256']


def test_different_frame_recomputes_and_preview_uses_its_own_columns():
    first, second = frame(0), frame(1)
    pixels = FramePixels({digest(first): COLUMNS_A, digest(second): COLUMNS_B})
    callback, _ = observer(pixels)
    ctx = context()
    located = callback.analyze(ctx, argv(first, 'read'))
    assert located.detail['target']['box'] == [400, 400, 100, 100]
    preview = callback.analyze(ctx, argv(second, 'preview'))
    assert pixels.calls == 2
    assert preview.box == [650, 432, 1, 1]
    assert preview.detail['frame_pixel_sha256'] == digest(second)
    assert preview.detail['target']['box'] == [600, 400, 100, 100]


def test_missing_frame_hash_always_recomputes(monkeypatch):
    pixels = Pixels()
    callback, _ = observer(pixels)
    ctx = context()
    image = frame()
    monkeypatch.setattr(callback, '_frame_sha', lambda image: None)
    callback.analyze(ctx, argv(image, 'read'))
    preview = callback.analyze(ctx, argv(image.copy(), 'preview'))
    assert pixels.calls == 2
    assert preview.detail['frame_pixel_sha256'] == digest(image)


def test_new_route_target_lifecycle_does_not_reuse_cross_page_columns():
    pixels = Pixels()
    callback, route = observer(pixels, route_for([1, 2, 2]))
    ctx = context()
    image = frame()
    callback.analyze(ctx, argv(image, 'read'))
    callback.current = route.nodes[1]
    callback.analyze(ctx, argv(image.copy(), 'preview'))
    assert pixels.calls == 2


def test_page_change_drops_the_memo_before_the_map_returns():
    pixels = Pixels()
    callback, _ = observer(pixels)
    ctx = context()
    changed = context(MAP_KEYS | JOINED_KEYS)
    image = frame()
    assert callback.analyze(ctx, argv(image, 'read')).detail['status'] == 'target_observed'
    seen = callback.analyze(changed, argv(image.copy(), 'preview'))
    assert seen.box is None and seen.detail['status'] == 'scene_changed'
    assert seen.detail['scene']['kind'] == 'joined'
    assert pixels.calls == 1
    again = callback.analyze(ctx, argv(image.copy(), 'preview'))
    assert again.detail['status'] == 'target_observed' and pixels.calls == 2


def test_non_map_rejections_still_read_and_click_nothing():
    pixels = Pixels()
    callback, _ = observer(pixels)
    changed = context(MAP_KEYS | JOINED_KEYS)
    image = frame()
    preview = callback.analyze(changed, argv(image, 'preview'))
    assert preview.box is None and preview.detail['status'] == 'scene_changed'
    read = callback.analyze(changed, argv(image.copy(), 'read'))
    assert read.box == [0, 0, 1, 1] and read.detail['scene']['kind'] == 'joined'
    assert pixels.calls == 0


def test_cache_never_crosses_observer_instances():
    pixels = Pixels()
    route = route_for([1, 2])
    ctx = context()
    image = frame()
    observer(pixels, route)[0].analyze(ctx, argv(image, 'read'))
    assert pixels.calls == 1
    observer(pixels, route)[0].analyze(ctx, argv(image.copy(), 'preview'))
    assert pixels.calls == 2


def test_final_boss_platform_scan_is_memoized_for_the_same_frame():
    pixels = FinalBossPixels([[dict(block_type=8, active=True, box=[500, 400, 100, 100])]])
    callback, _ = observer(pixels, route_for([1, 8]))
    ctx = context()
    image = frame()
    located = callback.analyze(ctx, argv(image, 'read'))
    preview = callback.analyze(ctx, argv(image.copy(), 'preview'))
    assert pixels.boss_calls == 1 and pixels.calls == 0
    assert located.detail['target']['block_type'] == 8
    assert preview.box == [550, 432, 1, 1]
