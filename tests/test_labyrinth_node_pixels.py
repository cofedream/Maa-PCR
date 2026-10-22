"""Original map pixels and independent node guards; no device or live game."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import json

import cv2
import numpy as np
import pytest

from pcr_maa import native
from pcr_maa.labyrinth.autoplay import NodePixels, MaaNodeNavigator, bind_route_target
from pcr_maa.labyrinth.routes import Node, Route
from pcr_maa.labyrinth.scene import MaaSceneRuntime
from pcr_maa.replay import SavedImages
from test_labyrinth_character_port import offline


DATA = Path(__file__).with_name('data')
MANIFEST = json.loads((DATA / 'labyrinth-map-ex-20261022.json').read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def pixels():
    return NodePixels()


def original_frame(name):
    encoded = (DATA / name).read_bytes()
    assert sha256(encoded).hexdigest() == MANIFEST['frames'][name]['sha256']
    image = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (720, 1280, 3)
    return image


@pytest.fixture(scope='module', params=tuple(MANIFEST['frames']))
def original_map(request, pixels):
    image = original_frame(request.param)
    return image, pixels.collect(image)


def map_route(target_id=20502):
    # Constructed protocol topology exercises the visible rows, not a recovered run.
    current = Node(20401, 2, 4, 1, 6, (20501, 20502))
    column = (Node(20503, 2, 5, 3, 2, ()), Node(20502, 2, 5, 2, 3, ()),
              Node(20501, 2, 5, 1, 2, ()))
    following = (Node(20603, 2, 6, 3, 4, ()), Node(20602, 2, 6, 2, 5, ()),
                 Node(20601, 2, 6, 1, 5, ()))
    target = next(n for n in column if n.block_id == target_id)
    return Route(274, (current, target), (current, *column, *following), 'protocol')


def test_original_map_keeps_visible_ex_and_other_nodes(original_map):
    _, columns = original_map
    nodes = [n for column in columns for n in column]
    extremes = [n for n in nodes if n['block_type'] == 3 and n['active']]
    assert len(extremes) == 1
    x, y, w, h = extremes[0]['box']
    assert 610 < x + w / 2 < 680 and 320 < y + h / 2 < 380
    assert len([n for n in nodes if n['block_type'] == 2 and n['active']]) == 1
    assert {n['block_type'] for n in nodes if not n['active']} >= {2, 4, 5}


@pytest.mark.parametrize('target_id, kind, y_min, y_max', [(20502, 3, 320, 380), (20501, 2, 490, 560)])
def test_original_map_binds_each_reachable_row_without_type_coercion(original_map, target_id, kind, y_min, y_max):
    _, columns = original_map
    route = map_route(target_id)
    target = bind_route_target(route, route.nodes[0], columns)
    assert target is not None and target['target_block_id'] == target_id
    assert target['block_type'] == target['detected_block_type'] == kind
    x, y, w, h = target['box']
    assert 610 < x + w / 2 < 680 and y_min < y + h / 2 < y_max


def test_original_map_never_promotes_inactive_or_conflicting_target(original_map):
    _, columns = original_map
    route = map_route(20503)
    current = replace(route.nodes[0], next_ids=(20503,))
    route = replace(route, nodes=(current, route.nodes[1]), full_map=(current, *route.full_map[1:]))
    assert bind_route_target(route, current, columns) is None
    route = map_route()
    target = replace(route.nodes[1], block_type=8)
    route = replace(route, nodes=(route.nodes[0], target),
                    full_map=tuple(target if n.block_id == target.block_id else n for n in route.full_map))
    assert bind_route_target(route, route.nodes[0], columns) is None


@pytest.mark.parametrize('color', [(0, 0, 0), (255, 0, 255), (180, 40, 220)])
def test_plain_dark_or_purple_background_cannot_become_active_ex(pixels, color):
    image = np.empty((720, 1280, 3), np.uint8)
    image[:] = color
    assert not any(n['block_type'] == 3 and n['active'] for c in pixels.collect(image) for n in c)


@pytest.mark.parametrize('name, kind', [('node_normal_battle_active.png', 2),
    ('node_link_active.png', 4), ('node_event_active.png', 5), ('node_relic_active.png', 6),
    ('node_shop_active.png', 7), ('node_boss_yellow.png', 8)])
def test_other_active_node_types_remain_independently_identified(pixels, name, kind):
    path = DATA.parents[1] / 'assets/resource/image/labyrinth/node' / name
    sprite = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
    h, w = sprite.shape[:2]
    frame = np.full((720, 1280, 3), 25, np.uint8)
    x, y = (1280 - w) // 2, (720 - h) // 2
    alpha = sprite[:, :, 3:4].astype(np.float32) / 255 if sprite.shape[2] == 4 else 1
    frame[y:y+h, x:x+w] = (sprite[:, :, :3] * alpha + 25 * (1 - alpha)).astype(np.uint8)
    active = [n for c in pixels.collect(frame) for n in c if n['active']]
    assert any(n['block_type'] == kind for n in active)
    assert not any(n['block_type'] == 3 for n in active)


@pytest.mark.parametrize('bands', [((550, 260, 190, 85),), ((540, 380, 210, 80),),
    ((535, 225, 55, 180), (710, 225, 55, 180))],
    ids=['missing-monster', 'missing-platform', 'missing-side-rails'])
def test_original_ex_still_needs_independent_monster_platform_and_rails(pixels, bands):
    frame = original_frame('labyrinth-map-ex-20261022-target.png')
    for x, y, w, h in bands:
        frame[y:y+h, x:x+w] = 0
    assert not any(n['block_type'] == 3 and n['active'] for c in pixels.collect(frame) for n in c)


def test_original_map_native_locate_is_read_only(tmp_path, monkeypatch, offline):
    image = original_frame('labyrinth-map-ex-20261022-target.png')
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    resource = native.load_resource()
    controller = SavedImages([[image]])
    tasker = native.bind(resource, controller)
    try:
        runtime = MaaSceneRuntime(tasker)
        assert runtime.observe().kind == 'map'
        navigator = MaaNodeNavigator(tasker)
        route = map_route()
        detail, receipt = navigator.locate(route, route.nodes[0])
        assert detail['status'] == 'target_observed'
        assert detail['target']['target_block_id'] == 20502
        assert detail['target']['detected_block_type'] == 3
        assert receipt['native_ok'] and receipt['tasker_inited']
        assert not controller.clicks and not controller.swipes
    finally:
        assert native.wait_job(tasker.post_stop(), seconds=10).succeeded
