"""Boss platform anchor: final_boss uses the tight icon-free 720p platform."""
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa.labyrinth.autoplay import NodePixels

NODE = ROOT / 'assets/resource/image/labyrinth/node'


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


def test_platform_is_also_collected_as_block_type_8():
    rgb, mask = read('node_boss_platform.png')
    frame = blank()
    paste(frame, rgb, mask, 900, 360)
    rows = [node for column in NodePixels().collect(frame) for node in column]
    assert len(rows) == 1
    assert rows[0]['block_type'] == 8 and rows[0]['template'] == 'node_boss_platform.png'


def test_two_platforms_are_rejected_as_ambiguous():
    rgb, mask = read('node_boss_platform.png')
    frame = blank()
    paste(frame, rgb, mask, 200, 360)
    paste(frame, rgb, mask, 900, 360)
    assert NodePixels().final_boss(frame) == []


def test_no_platform_returns_nothing():
    assert NodePixels().final_boss(blank()) == []


def test_missing_anchor_disables_final_boss(tmp_path):
    destination = tmp_path / 'node'
    destination.mkdir()
    for path in NODE.glob('node_*.png'):
        if path.name in ('node_boss_platform.png', 'node_boss_platform_reward.png'):
            continue
        shutil.copy2(path, destination / path.name)
    pixels = NodePixels(destination)
    assert pixels.boss_anchor is None and pixels.boss_check is None
    assert pixels.final_boss(blank()) == []
