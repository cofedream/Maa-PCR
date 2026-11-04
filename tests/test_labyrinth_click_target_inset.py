"""Template click targets stay on the control art, not on template background.

MaaFramework samples a Click point inside the action target rect with a truncated
normal (Actuator::rand_point: rect centre, sigma = width/3 and height/3, samples
outside the rect rejected). A template cropped with page background around the
button therefore has a dead margin, and one unlucky sample taps beside the control:
the page never changes and a settlement readback can only time out. These checks pin
the inset that keeps every sampled point on the drawn control.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import numpy as np
import pytest

from pcr_maa import native
from pcr_maa.labyrinth import scene as s

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'assets/resource/image/labyrinth/scene-anchors.json').read_text(encoding='utf-8'))


def template_path(template):
    return s.ASSETS.parent / template


def read_template(template):
    """Read a shipped template the same way production does (path-safe bytes)."""
    raw = template_path(template).read_bytes()
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)


def art_bounds(template):
    """(left, top, right, bottom) of the drawn art inside a shipped template."""
    image = read_template(template)
    background = image[:, :, :3].astype('int16').min(axis=2) > 245
    if image.shape[2] == 4:
        background = background | (image[:, :, 3] < 8)
    art = ~background
    rows, columns = np.where(art.any(axis=1))[0], np.where(art.any(axis=0))[0]
    return int(columns[0]), int(rows[0]), int(columns[-1]), int(rows[-1])


def target_rect(key):
    """Rect Maa samples the click point from, in template coordinates."""
    template = SPEC[key]['template']
    image = read_template(template)
    height, width = image.shape[:2]
    offset = s._click_target_offset(template)
    x, y, delta_width, delta_height = offset
    return x, y, width + delta_width, height + delta_height


def test_every_shipped_anchor_target_stays_on_its_control_art():
    checked = 0
    for key, spec in SPEC.items():
        if not template_path(spec['template']).is_file():
            continue
        offset = s._click_target_offset(spec['template'])
        if offset is None:
            continue
        left, top, right, bottom = art_bounds(spec['template'])
        x, y, w, h = target_rect(key)
        assert w >= 3 and h >= 3, (key, offset)
        assert x >= left and y >= top, (key, offset, (left, top))
        assert x + w - 1 <= right and y + h - 1 <= bottom, (key, offset, (right, bottom))
        checked += 1
    assert checked > 20


def test_chest_confirm_target_clears_the_template_background_padding():
    """The 2026-11-02 stop: the sampled point landed on the white border below 确认."""
    key = 'RUN_CLEAR_CHEST_CONFIRM_BUTTON'
    left, top, right, bottom = art_bounds(SPEC[key]['template'])
    assert bottom < 71, 'the shipped chest-confirm template still carries bottom padding'
    x, y, w, h = target_rect(key)
    assert x == left + 3 and y == top + 3
    assert x + w - 1 == right - 3 and y + h - 1 == bottom - 3


def test_view_map_target_clears_its_larger_template_padding():
    key = 'BATTLE_CHALLENGE_VIEW_MAP_BUTTON'
    left, top, right, bottom = art_bounds(SPEC[key]['template'])
    assert (left, top, right, bottom) == (10, 8, 285, 78)
    x, y, w, h = target_rect(key)
    assert (x, y, x + w - 1, y + h - 1) == (left + 3, top + 3, right - 3, bottom - 3)


class Resource:
    def __init__(self):
        self.nodes = {}

    def get_node_data(self, name):
        return deepcopy(self.nodes[name])

    def override_pipeline(self, nodes):
        self.nodes.update(deepcopy(nodes))
        return True


class Tasker:
    def __init__(self):
        self.resource = self._resource_holder = Resource()


def install(monkeypatch, kind, control, action, *, target=None):
    """Run one production scene action offline and return its pipeline override."""
    tasker = Tasker()
    calls = []

    def run(owner, entry, reset_hits=()):
        calls.append((entry, deepcopy(owner.resource.nodes)))
        return dict(native_ok=True, tasker_inited=True, task_id=7,
                    nodes=[dict(name=entry, completed=True, action=dict(success=True))])

    monkeypatch.setattr(native, 'run', run)
    scene = s.Scene(kind, dict(native_ok=True), dict(controls={s._ACTIONS[action][1]: control}))
    receipt = s.perform_scene_action(tasker, scene, action, target=target)
    assert receipt['nodes'][0]['name'] == 'LabySceneActionOnce'
    return calls[0][1]


def test_template_control_insets_its_random_click_target(monkeypatch):
    key = 'RUN_CLEAR_CHEST_CONFIRM_BUTTON'
    nodes = install(monkeypatch, 'chest', dict(anchor=key, box=[497, 607, 287, 73], score=.95), 'settlement_next')
    once = nodes['LabySceneActionOnce']
    assert once['target'] is True
    left, top, right, bottom = art_bounds(SPEC[key]['template'])
    x, y, w, h = (497 + once['target_offset'][0], 607 + once['target_offset'][1],
                  287 + once['target_offset'][2], 73 + once['target_offset'][3])
    assert (x - 497, y - 607, x - 497 + w - 1, y - 607 + h - 1) == (left + 3, top + 3, right - 3, bottom - 3)


def test_page_text_control_keeps_its_tight_target(monkeypatch):
    nodes = install(monkeypatch, 'team', dict(anchor='START_WORD', box=[1035, 565, 205, 90], score=.92), 'start_battle')
    assert nodes['LabySceneActionControl']['recognition'] == 'OCR'
    assert 'target_offset' not in nodes['LabySceneActionOnce']


def test_pinned_animation_target_is_unchanged(monkeypatch):
    nodes = install(monkeypatch, 'unknown', dict(anchor='CONFIRM', box=[520, 165, 240, 70], score=.9),
                    'advance_final_unknown', target=[640, 520, 1, 1])
    once = nodes['LabySceneActionOnce']
    assert once['target'] == [640, 520, 1, 1] and 'target_offset' not in once


def test_background_border_inset_is_capped_and_never_collapses(tmp_path, monkeypatch):
    root = tmp_path / 'image'
    probe = root / 'probe'
    probe.mkdir(parents=True)
    monkeypatch.setattr(s, 'ASSETS', root / 'labyrinth')
    s._CLICK_INSET.clear()
    try:
        cv2.imwrite(str(probe / 'blank.png'), np.full((20, 20, 3), 255, np.uint8))
        assert s._click_target_offset('probe/blank.png') is None

        dot = np.full((20, 20, 3), 255, np.uint8)
        dot[8:12, 8:12] = 0
        cv2.imwrite(str(probe / 'dot.png'), dot)
        x, y, w, h = s._click_target_offset('probe/dot.png')
        assert (x, y) == (5, 5) and 20 + w >= 3 and 20 + h >= 3

        cv2.imwrite(str(probe / 'line.png'), np.zeros((1, 30, 3), np.uint8))
        assert s._click_target_offset('probe/line.png') is None
        assert s._click_target_offset('probe/missing.png') is None
    finally:
        s._CLICK_INSET.clear()
