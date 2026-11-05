"""Boss nameplate anchors: final_boss locates the locked and unlocked 首领
platforms from the alpha-masked nameplate crops and expands them to the
measured platform boxes (so the shared preview click stays unchanged)."""
import shutil
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa.labyrinth.autoplay import NodePixels, RouteTargetRecognition
from pcr_maa.labyrinth.routes import Node, Route

NODE = ROOT / 'assets/resource/image/labyrinth/node'
ANCHORS = ('boss_nameplate.png', 'boss_nameplate_inactive.png')
ACTIVE_FRAME = ROOT / 'tests/data/labyrinth-boss-active-20261104.png'
LOCKED_FRAME = ROOT / 'tests/data/labyrinth-boss-locked-20261104.png'
LOCKED_BOX = [888, 370, 204, 119]
ACTIVE_BOX = [539, 366, 203, 141]


def frame(path):
    return cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)


def blank():
    return np.full((720, 1280, 3), 32, np.uint8)


def paste_patch(target, source, box, x, y):
    h, w = box[3], box[2]
    target[y:y + h, x:x + w] = source[box[1]:box[1] + h, box[0]:box[0] + w]


def test_locked_nameplate_locates_locked_boss_platform():
    data = LOCKED_FRAME.read_bytes()
    assert sha256(data).hexdigest() == '6e38486e714782a8249be05bb8456a1cc0f9c03ba9f673451d05c2802f7b3428'
    pixels = NodePixels()
    assert pixels.boss_anchor_active is not None and pixels.boss_anchor_inactive is not None
    result = pixels.final_boss(frame(LOCKED_FRAME))
    assert len(result) == 1 and len(result[0]) == 1
    row = result[0][0]
    assert row['box'] == LOCKED_BOX
    assert row['block_type'] == 8 and row['active'] is True
    assert row['template'] == 'boss_nameplate_inactive.png'
    assert row['score'] >= .98 and row['color_score'] >= .90
    x, y, w, h = row['box']
    assert (x + w // 2, y + int(h * .32)) == (990, 408)


def test_active_nameplate_locates_unlocked_boss_platform():
    data = ACTIVE_FRAME.read_bytes()
    assert sha256(data).hexdigest() == '44f15e206efe372bba99e3f2faea1a6d39b26453508641fcacc394b89f61be51'
    result = NodePixels().final_boss(frame(ACTIVE_FRAME))
    assert len(result) == 1 and len(result[0]) == 1
    row = result[0][0]
    # The 56x32 nameplate match is expanded back to the 203x141 platform box, so
    # the preview click is identical to the retired platform anchor.
    assert row['box'] == ACTIVE_BOX
    assert row['block_type'] == 8 and row['active'] is True
    assert row['template'] == 'boss_nameplate.png'
    assert row['score'] >= .98 and row['color_score'] >= .85
    x, y, w, h = row['box']
    assert (x + w // 2, y + int(h * .32)) == (640, 411)


def test_nameplate_anchors_are_not_map_node_templates():
    names = [name for name, *_ in NodePixels().templates]
    assert not any(name in ANCHORS for name in names)
    rows = [node for column in NodePixels().collect(frame(LOCKED_FRAME)) for node in column]
    assert not any(node['block_type'] == 8 for node in rows)


@pytest.mark.parametrize('number,digest', [
    (1, '56c48ab59d2536182b8b89e380a0782303491b86e50504fcaca7f5e3803901a3'),
    (2, 'bca1f15f3da2ca7db957bd4dfad4aaeb7c82fc74880141025ef74f6a9394f6d5'),
])
def test_dragon_feet_do_not_hide_the_final_boss(number, digest):
    from pcr_maa.labyrinth.autoplay import bind_route_target

    data = (ROOT / f'tests/data/labyrinth-boss-dragon-20261104-{number}.png').read_bytes()
    assert sha256(data).hexdigest() == digest
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    columns = NodePixels().final_boss(image)
    assert len(columns) == 1 and len(columns[0]) == 1
    assert columns[0][0]['box'] == ACTIVE_BOX
    assert columns[0][0]['template'] == 'boss_nameplate.png'
    current = Node(50701, 5, 7, 1, 7, (50801,))
    boss = Node(50801, 5, 8, 1, 8, ())
    route = Route(1, (current, boss), (current, boss), 'protocol')
    target = bind_route_target(route, current, columns)
    assert target is not None and target['target_block_id'] == 50801
    assert target['detected_block_type'] == 8


def test_two_locked_platforms_are_rejected_as_ambiguous():
    source = frame(LOCKED_FRAME)
    image = source.copy()
    paste_patch(image, source, LOCKED_BOX, 200, 350)
    assert NodePixels().final_boss(image) == []


def test_locked_and_unlocked_platforms_are_rejected_as_ambiguous():
    source = frame(ACTIVE_FRAME)
    image = frame(LOCKED_FRAME)
    paste_patch(image, source, ACTIVE_BOX, 180, 340)
    assert NodePixels().final_boss(image) == []


def test_no_platform_returns_nothing():
    assert NodePixels().final_boss(blank()) == []


def test_missing_anchors_disable_final_boss(tmp_path):
    destination = tmp_path / 'node'
    destination.mkdir()
    for path in NODE.glob('node_*.png'):
        shutil.copy2(path, destination / path.name)
    pixels = NodePixels(destination)
    assert pixels.boss_anchor_active is None and pixels.boss_anchor_inactive is None
    assert pixels.final_boss(blank()) == []
    assert pixels.final_boss(frame(LOCKED_FRAME)) == []
    assert pixels.final_boss(frame(ACTIVE_FRAME)) == []


def test_missing_inactive_anchor_keeps_active_detection(tmp_path):
    destination = tmp_path / 'node'
    destination.mkdir()
    for path in NODE.glob('node_*.png'):
        shutil.copy2(path, destination / path.name)
    shutil.copy2(NODE / 'boss_nameplate.png', destination / 'boss_nameplate.png')
    # boss_nameplate_inactive.png is intentionally not copied.
    pixels = NodePixels(destination)
    assert pixels.boss_anchor_active is not None and pixels.boss_anchor_inactive is None
    assert len(pixels.final_boss(frame(ACTIVE_FRAME))) == 1
    assert pixels.final_boss(frame(LOCKED_FRAME)) == []


def test_missing_active_anchor_keeps_locked_detection(tmp_path):
    destination = tmp_path / 'node'
    destination.mkdir()
    for path in NODE.glob('node_*.png'):
        shutil.copy2(path, destination / path.name)
    shutil.copy2(NODE / 'boss_nameplate_inactive.png', destination / 'boss_nameplate_inactive.png')
    # boss_nameplate.png is intentionally not copied.
    pixels = NodePixels(destination)
    assert pixels.boss_anchor_active is None and pixels.boss_anchor_inactive is not None
    assert len(pixels.final_boss(frame(LOCKED_FRAME))) == 1
    assert pixels.final_boss(frame(ACTIVE_FRAME)) == []


class _AnchorsOnlyPixels:
    """Records which maze reader a route target selects."""

    def __init__(self):
        self.calls = []

    def final_boss(self, image):
        self.calls.append('final_boss')
        return [[dict(box=[100, 200, 203, 141], block_type=8, active=True, score=.99,
                      color_score=.9, purple_glow=0.0,
                      template='boss_nameplate.png', activation_basis='boss_nameplate_anchor',
                      score_method='anchored masked gradient/luminance NCC')]]

    def collect(self, image):
        self.calls.append('collect')
        return []


class _MapScene:
    def __init__(self, detail):
        self.hit = True
        self.raw_detail = {'best': {'detail': detail}}


class _MapContext:
    def __init__(self):
        self.tasker = SimpleNamespace(stopping=False)

    def run_recognition(self, name, image, overrides):
        return _MapScene(dict(kind='map', payload=dict(area=5, frame_pixel_sha256='0' * 64)))


@pytest.mark.parametrize('block_type,reader', [
    (1, 'collect'), (2, 'collect'), (3, 'collect'), (4, 'collect'),
    (5, 'collect'), (6, 'collect'), (7, 'collect'), (8, 'final_boss'),
])
def test_boss_route_target_scans_the_boss_templates_only(block_type, reader):
    # Difficulty 5 has a type-8 boss on layers 3 and 5. Both are ordinary route
    # targets, so both take the nameplate anchor path: only the two nameplate
    # crops are scanned. Every non-boss target still needs the full node-body
    # scan for column/camera alignment.
    pixels = _AnchorsOnlyPixels()
    observer = RouteTargetRecognition(pixels)
    current = Node(1, 5, 7, 1, 7, (2,))
    target = Node(2, 5, 8, 1, block_type, ())
    observer.route = Route(1, (current, target), (current, target), 'protocol')
    observer.current = current
    argv = SimpleNamespace(image=np.zeros((720, 1280, 3), np.uint8),
                           custom_recognition_param='{}')
    result = observer.analyze(_MapContext(), argv)
    assert result is not None
    assert pixels.calls == [reader]
