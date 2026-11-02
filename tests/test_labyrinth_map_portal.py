"""Original area-four portal frame; local topology is constructed, not recovered."""
from hashlib import sha256
from pathlib import Path

import cv2
import numpy as np
import pytest

from pcr_maa.labyrinth.autoplay import NodePixels, bind_route_target
from pcr_maa.labyrinth.routes import Node, Route


def portal_frame():
    data = (Path(__file__).with_name('data') / 'labyrinth-map-portal-20261102.png').read_bytes()
    assert sha256(data).hexdigest() == 'f6b13d2827a37a8f318213d808169e08699b25d45e84456d89ef88dcb196aaa3'
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def portal_route():
    current = Node(40702, 4, 7, 2, 6, (40801,))
    other = Node(40701, 4, 7, 1, 4, (40801,))
    target = Node(40801, 4, 8, 1, 7, ())
    return Route(432, (current, target), (current, other, target), 'protocol')


def test_portal_background_is_not_a_boss_platform():
    pixels = NodePixels()
    frame = portal_frame()
    columns = pixels.collect(frame)
    assert not pixels.final_boss(frame)
    assert not any(node['block_type'] == 8 for column in columns for node in column)
    route = portal_route()
    target = bind_route_target(route, route.nodes[0], columns)
    assert target is not None and target['target_block_id'] == 40801
    assert target['detected_block_type'] == 7
    x, y, w, h = target['box']
    assert 610 < x + w / 2 < 680 and 320 < y + h / 2 < 390


@pytest.mark.parametrize('moving', [True, False])
def test_map_panning_uses_observed_progress_not_four_swipes(tmp_path, moving):
    from pcr_maa.labyrinth.autoplay import RunCursor
    from pcr_maa.labyrinth.batch import BatchStopped
    from pcr_maa.labyrinth.scene import Scene
    from test_labyrinth_autoplay import make, receipt, route_for
    route = route_for([1, 6])
    runner, batch, runtime, navigator, actions = make(tmp_path, route, [('map', dict(area=1))])
    runner.cursor = RunCursor()
    runner.moves = set()
    runner.scrolls = {}
    scene = Scene('map', receipt(), dict(area=1))
    calls = []
    navigator.locate = lambda *args: (dict(status='target_not_visible', viewport=dict(
        direction=1, columns={1: 1200 - len(calls) * (120 if moving else 0)})), receipt())
    navigator.scroll = lambda *args: calls.append(1) or receipt(action=True)
    runner._action_transition_readback = lambda *args: scene
    if moving:
        for _ in range(5):
            assert runner._move(batch, route, scene) is scene
        assert len(calls) == 5
    else:
        runner._move(batch, route, scene)
        with pytest.raises(BatchStopped, match='map_scroll_boundary'):
            runner._move(batch, route, scene)
        assert len(calls) == 1
