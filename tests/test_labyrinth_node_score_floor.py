"""Inactive node-body score floor: scenery in the live false-positive band must
not be emitted as map nodes.

The two live area-5 background false positives were produced by
``node_normal_battle_inactive_bottom.png`` (.630 masked NCC) and
``node_link_inactive.png`` (.604): both cleared the inactive colour band (.84)
at only .60-.63 NCC and were accepted by the old .40 score floor. Inactive
non-EX templates now require the measured node-body floor, while EX keeps its
calibrated purple fallback and boss is active by definition.

The synthetic crops below reproduce that acceptance band deterministically
without shipping live game captures in the repository.
"""
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa.labyrinth import autoplay  # noqa: E402
from pcr_maa.labyrinth.autoplay import NodePixels  # noqa: E402

NODE = ROOT / 'assets/resource/image/labyrinth/node'
CROSS = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], np.uint8)
X, Y = 420, 280


def read(name):
    image = cv2.imdecode(np.fromfile(str(NODE / name), np.uint8), cv2.IMREAD_UNCHANGED)
    return image[:, :, :3], image[:, :, 3] >= 128


def scene(rgb, *, alpha=1.0, dx=0, dy=0):
    roll = np.roll(rgb, (dy, dx), axis=(0, 1))
    patch = (alpha * rgb.astype(np.float32)
             + (1 - alpha) * roll.astype(np.float32)).astype(np.uint8)
    frame = np.full((720, 1280, 3), 32, np.uint8)
    h, w = patch.shape[:2]
    frame[Y:Y + h, X:X + w] = patch
    return frame, [X, Y, w, h]


def masked_scores(frame, rgb, mask, box):
    x, y, w, h = box
    interior = cv2.erode(mask.astype(np.uint8), CROSS)
    light, gradient = NodePixels._features(frame[y:y + h, x:x + w])
    ref_light, ref_gradient = NodePixels._features(rgb)
    return (float(cv2.matchTemplate(light, ref_light, cv2.TM_CCOEFF_NORMED,
                                    mask=interior)[0, 0]),
            float(cv2.matchTemplate(gradient, ref_gradient, cv2.TM_CCOEFF_NORMED,
                                    mask=interior)[0, 0]))


def color_score(frame, rgb, mask, box):
    x, y, w, h = box
    crop = frame[y:y + h, x:x + w]
    band = slice(round(.30 * h), round(.78 * h))
    opaque = mask[band]
    return 1 - float(np.mean(np.abs(crop[band].astype(np.float32)
                                   - rgb[band].astype(np.float32))[opaque])) / 255


def nodes(frame):
    return [node for column in NodePixels().collect(frame) for node in column]


def test_inactive_floor_is_the_measured_live_threshold():
    assert autoplay._INACTIVE_NODE_SCORE == .75


def test_low_score_background_crop_is_not_emitted(monkeypatch):
    rgb, mask = read('node_link_inactive.png')
    frame, box = scene(rgb, alpha=.50, dx=16)
    score = max(masked_scores(frame, rgb, mask, box))
    color = color_score(frame, rgb, mask, box)
    # Mirrors the live background false positive: the old .40 acceptance and
    # the .84 inactive colour band both pass, the new node-body floor does not.
    assert .60 <= score < .75
    assert color >= .84
    assert nodes(frame) == []
    # Under the previous floor the same crop was emitted as an inactive node.
    monkeypatch.setattr(autoplay, '_INACTIVE_NODE_SCORE', .40)
    emitted = nodes(frame)
    assert len(emitted) == 1
    assert emitted[0]['block_type'] == 4 and emitted[0]['active'] is False
    assert emitted[0]['box'] == box


@pytest.mark.parametrize('name,kind', [('node_normal_battle_inactive.png', 2),
                                       ('node_link_inactive.png', 4)])
def test_clean_inactive_node_body_is_still_accepted(name, kind):
    rgb, mask = read(name)
    frame, box = scene(rgb)
    emitted = nodes(frame)
    assert len(emitted) == 1
    node = emitted[0]
    assert node['block_type'] == kind and node['active'] is False
    assert node['score'] >= .99
    # The node body may be matched through its _bottom (78%) alias, so the
    # reported height can differ while the visible anchor keeps its origin.
    assert node['box'][0] == box[0] and node['box'][1] == box[1]
    assert node['box'][2] == box[2]
