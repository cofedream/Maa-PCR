"""Boss platform anchors: final_boss uses the tight icon-free 720p platform in
both the locked (grey-blue) and the unlocked (magenta-glowing) state."""
import shutil
import sys
from hashlib import sha256
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa.labyrinth.autoplay import NodePixels

NODE = ROOT / 'assets/resource/image/labyrinth/node'
BOSS_TEMPLATES = ('node_boss_platform.png', 'node_boss_platform_active.png',
                  'node_boss_platform_reward.png')


def read(name):
    image = cv2.imread(str(NODE / name), cv2.IMREAD_UNCHANGED)
    return image[:, :, :3], image[:, :, 3] >= 128


def paste(frame, rgb, mask, x, y):
    h, w = rgb.shape[:2]
    roi = frame[y:y + h, x:x + w]
    roi[:] = np.where(mask[..., None], rgb, roi)


def blank():
    return np.full((720, 1280, 3), 32, np.uint8)


def test_tight_anchor_locates_boss_platform_and_keeps_preview_click():
    rgb, mask = read('node_boss_platform.png')
    frame = blank()
    paste(frame, rgb, mask, 900, 360)
    pixels = NodePixels()
    assert pixels.boss_anchor is not None and pixels.boss_check is not None
    result = pixels.final_boss(frame)
    assert len(result) == 1 and len(result[0]) == 1
    row = result[0][0]
    assert row['box'] == [900, 360, 204, 119]
    assert row['block_type'] == 8 and row['active'] is True
    assert row['template'] == 'node_boss_platform.png'
    assert row['score'] >= .99 and row['color_score'] >= .95
    assert row['platform2_check_score'] is not None
    # Shared preview click: center x, 32% height of the node box.
    x, y, w, h = row['box']
    assert (x + w // 2, y + int(h * .32)) == (1002, 398)


def test_locked_platform_is_located_by_the_anchor_not_collected_as_a_node():
    # Boss platforms use final_boss's nameplate; their scenery also matches
    # portals, so the platform crop must never become a block-type-8 map node.
    rgb, mask = read('node_boss_platform.png')
    frame = blank()
    paste(frame, rgb, mask, 900, 360)
    rows = [node for column in NodePixels().collect(frame) for node in column]
    assert rows == []


def test_active_anchor_locates_unlocked_boss_platform():
    rgb, mask = read('node_boss_platform_active.png')
    frame = blank()
    paste(frame, rgb, mask, 900, 360)
    pixels = NodePixels()
    assert pixels.boss_anchor_active is not None
    result = pixels.final_boss(frame)
    assert len(result) == 1 and len(result[0]) == 1
    row = result[0][0]
    assert row['box'] == [900, 360, 203, 141]
    assert row['block_type'] == 8 and row['active'] is True
    assert row['template'] == 'node_boss_platform_active.png'
    assert row['score'] >= .99 and row['color_score'] >= .95


@pytest.mark.parametrize('number,digest', [
    (1, '56c48ab59d2536182b8b89e380a0782303491b86e50504fcaca7f5e3803901a3'),
    (2, 'bca1f15f3da2ca7db957bd4dfad4aaeb7c82fc74880141025ef74f6a9394f6d5'),
])
def test_dragon_feet_do_not_hide_the_final_boss(number, digest):
    from pcr_maa.labyrinth.autoplay import bind_route_target
    from pcr_maa.labyrinth.routes import Node, Route

    data = (ROOT / f'tests/data/labyrinth-boss-dragon-20261104-{number}.png').read_bytes()
    assert sha256(data).hexdigest() == digest
    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    columns = NodePixels().final_boss(frame)
    assert len(columns) == 1 and len(columns[0]) == 1
    assert columns[0][0]['box'] == [539, 366, 203, 141]
    current = Node(50701, 5, 7, 1, 7, (50801,))
    boss = Node(50801, 5, 8, 1, 8, ())
    route = Route(1, (current, boss), (current, boss), 'protocol')
    target = bind_route_target(route, current, columns)
    assert target is not None and target['target_block_id'] == 50801
    assert target['detected_block_type'] == 8


def test_active_platform_is_located_by_the_anchor_not_collected_as_a_node():
    rgb, mask = read('node_boss_platform_active.png')
    frame = blank()
    paste(frame, rgb, mask, 900, 360)
    rows = [node for column in NodePixels().collect(frame) for node in column]
    assert rows == []


def test_two_platforms_are_rejected_as_ambiguous():
    rgb, mask = read('node_boss_platform.png')
    frame = blank()
    paste(frame, rgb, mask, 200, 360)
    paste(frame, rgb, mask, 900, 360)
    assert NodePixels().final_boss(frame) == []


def test_locked_and_unlocked_platforms_are_rejected_as_ambiguous():
    locked_rgb, locked_mask = read('node_boss_platform.png')
    active_rgb, active_mask = read('node_boss_platform_active.png')
    frame = blank()
    paste(frame, locked_rgb, locked_mask, 200, 360)
    paste(frame, active_rgb, active_mask, 900, 360)
    assert NodePixels().final_boss(frame) == []


def test_no_platform_returns_nothing():
    assert NodePixels().final_boss(blank()) == []


def test_missing_anchors_disable_final_boss(tmp_path):
    destination = tmp_path / 'node'
    destination.mkdir()
    for path in NODE.glob('node_*.png'):
        if path.name in BOSS_TEMPLATES:
            continue
        shutil.copy2(path, destination / path.name)
    pixels = NodePixels(destination)
    assert pixels.boss_anchor is None and pixels.boss_anchor_active is None
    assert pixels.boss_check is None
    assert pixels.final_boss(blank()) == []
    active_rgb, active_mask = read('node_boss_platform_active.png')
    frame = blank()
    paste(frame, active_rgb, active_mask, 900, 360)
    assert pixels.final_boss(frame) == []


def test_missing_active_anchor_keeps_locked_detection(tmp_path):
    destination = tmp_path / 'node'
    destination.mkdir()
    for path in NODE.glob('node_*.png'):
        if path.name == 'node_boss_platform_active.png':
            continue
        shutil.copy2(path, destination / path.name)
    pixels = NodePixels(destination)
    assert pixels.boss_anchor is not None and pixels.boss_anchor_active is None
    locked_rgb, locked_mask = read('node_boss_platform.png')
    frame = blank()
    paste(frame, locked_rgb, locked_mask, 900, 360)
    assert len(pixels.final_boss(frame)) == 1
    active_rgb, active_mask = read('node_boss_platform_active.png')
    frame = blank()
    paste(frame, active_rgb, active_mask, 900, 360)
    assert pixels.final_boss(frame) == []
