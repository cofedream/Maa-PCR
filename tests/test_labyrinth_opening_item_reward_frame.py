"""Saved guild-relic item page after the opening invitation.

The 2026-11-15 guild 9 (墨丘利财团) run stopped at
`LabyOpeningAfterInvitationObserved` because the guild's starting relic
"高贵紫藤庐" grants 3000 alpha coins and the game shows a "迷宫遗物效果"
item page over the fresh area-1 map. The opening transition only knew the
guild-5 1068 joined page, so every branch timed out. This replay drives the
real Maa OCR and the real close input against the saved failure frame and a
saved map successor; it does not claim live acceptance.
"""
from hashlib import sha256
import json
import socket
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from pcr_maa import native
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.opening import MaaOpening
from pcr_maa.replay import SavedImages

DATA = Path(__file__).with_name('data')
ITEM = DATA / 'labyrinth-opening-item-reward-20261115.png'
MAP = DATA / 'labyrinth-map-ex-20261022-target.png'
ITEM_MANIFEST = json.loads((DATA / 'labyrinth-opening-item-reward-20261115.json').read_text(encoding='utf-8'))
MAP_MANIFEST = json.loads((DATA / 'labyrinth-map-ex-20261022.json').read_text(encoding='utf-8'))


def _image(path, manifest):
    raw = path.read_bytes()
    meta = manifest['frames'][path.name]
    assert sha256(raw).hexdigest() == meta['sha256']
    image = native.read_image(path)
    assert image.shape[:2] == (meta['height'], meta['width'])
    return image


@pytest.fixture
def offline(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail('offline saved frame prohibits network/device')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    return native.load_resource()


def _port(resource, stages):
    controller = SavedImages(stages)
    tasker = native.bind(resource, controller)
    state = NS(frame=0, inputs=[], stamps=[])

    def stamp(**fields):
        state.frame += 1
        return dict(verified=True,
                    binding={'account_id': 1, 'device_id': 'offline-saved-image-replay'},
                    frame=state.frame, **fields)

    def transaction(operation, fields, send):
        state.inputs.append(operation)
        result = send(f'op{len(state.inputs)}')
        state.stamps.append(result)
        return result

    batch = NS(checkpoint=lambda: None, transaction=transaction, stopped=lambda: False)
    return MaaOpening(tasker, stamp), batch, controller, tasker, state


@pytest.mark.parametrize('delayed', [False, True], ids=['immediate-map', 'lingering-reward'])
def test_item_page_is_closed_once_then_the_map_is_read(offline, delayed):
    item = _image(ITEM, ITEM_MANIFEST)
    map_ = _image(MAP, MAP_MANIFEST)
    port, batch, controller, tasker, state = _port(offline, [[item], [item, map_] if delayed else [map_]])
    try:
        page, receipt = port._after_invitation(batch, 9, enter_id=13833)
    finally:
        native.stop_tasker(tasker)
    # One observed item page -> one guarded close -> the map is read on a new frame.
    assert page == 'map'
    assert state.inputs == ['opening_item_reward_close']
    assert len(controller.clicks) == 1
    x, y = controller.clicks[0]
    assert 500 <= x < 778 and 600 <= y < 680
    close = state.stamps[0]
    assert close['enter_id'] == 13833 and close['phase'] == 'opening_item_reward_close'
    assert close['verified'] is True and close['source'] == 'maa'
    close_node = next(n for n in close['native']['nodes']
                      if n['name'] == 'LabyOpeningItemRewardCloseOnce')
    assert close_node['completed'] is True and close_node['action']['success'] is True
    texts = [word['text'] for row in close_node['recognition']
             for word in row['detail']['filtered']]
    assert '关闭' in texts and '迷宫遗物效果' in texts
    assert receipt['native_ok'] is True
    map_node = next(n for n in receipt['nodes'] if n['name'] == 'LabyMapObserved')
    assert map_node['completed'] is True
    titles = [word['text'] for row in map_node['recognition']
              if row.get('name') == 'LabyHomeTitle'
              for word in row['detail']['filtered']]
    assert '黎明界迷宫' in titles


def test_stuck_item_page_stops_after_a_single_close(offline):
    item = _image(ITEM, ITEM_MANIFEST)
    port, batch, controller, tasker, state = _port(offline, [[item]])
    try:
        with pytest.raises(BatchStopped, match='^opening_readback_unknown$'):
            port._after_invitation(batch, 9, enter_id=13833)
    finally:
        native.stop_tasker(tasker)
    assert state.inputs == ['opening_item_reward_close']
    assert len(controller.clicks) == 1


def test_map_without_an_item_page_sends_no_input(offline):
    map_ = _image(MAP, MAP_MANIFEST)
    port, batch, controller, tasker, state = _port(offline, [[map_]])
    try:
        page, receipt = port._after_invitation(batch, 8, enter_id=13831)
    finally:
        native.stop_tasker(tasker)
    assert page == 'map'
    assert state.inputs == [] and controller.clicks == []
    map_node = next(n for n in receipt['nodes'] if n['name'] == 'LabyMapObserved')
    assert map_node['completed'] is True
