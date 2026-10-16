"""HOME guards keep exact words and reject missing anchors or large panels."""
import os
from pathlib import Path
import socket

import cv2
import numpy as np
import pytest

from pcr_maa import native
from pcr_maa.labyrinth.game_session import game_session_pipeline
from pcr_maa.replay import SavedImages


def test_home_observation_and_input_share_layout_and_obstruction_guards():
    nodes = game_session_pipeline(123)
    guards = ['HomeTextLayout', 'HomeNoKnownLargePanel']
    assert nodes['LabyClientGameHomeObserved']['all_of'] == guards
    assert nodes['LabyClientHomeToAdventure']['all_of'] == guards
    for name, word in zip(('LabyClientAdventureLabel','LabyClientScheduleLabel','LabyClientGiftLabel'), ('冒险', '日程表', '礼物')):
        assert nodes[name]['expected'] == '^' + word + '$'
        assert nodes[name]['threshold'] == .8 and nodes[name]['only_rec'] is True
    assert nodes['LabyClientAfterTitleGiftLabel'] == nodes['LabyClientGiftLabel']
    assert nodes['LabyClientHomeToAdventure']['action'] == 'Click'
    assert nodes['LabyClientHomeToAdventure']['max_hit'] == 1
    assert nodes['LabyGameSessionWait_client_home']['timeout'] == 5000


def saved_home():
    path = os.environ.get('PCR_LABYRINTH_HOME_FRAME')
    if not path: pytest.skip('private saved HOME frame is not distributed')
    return native.read_image(Path(path))


@pytest.fixture
def offline(monkeypatch):
    def deny(*args, **kwargs): raise AssertionError('saved-frame replay only')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)


def setup(image):
    controller = SavedImages([[image]])
    resource = native.load_resource()
    tasker = native.bind(resource, controller)
    assert resource.override_pipeline(game_session_pipeline(123))
    return tasker, controller


@pytest.mark.private_replay
@pytest.mark.parametrize('shift', [-1, 0, 1])
def test_saved_home_across_all_endpoints_and_input_guard(offline, shift):
    image = saved_home()
    if shift:
        image = cv2.warpAffine(image, np.float32([[1, 0, shift], [0, 1, shift]]), (1280, 720))
    tasker, controller = setup(image)
    try:
        for entry in ('LabyClientGameHomeObserved', 'LabyGameSessionWait_client_home',
                      'LabyGameSessionAfterTitleObserved', 'LabyGameSessionEntryObserved',
                      'LabyGameSessionRecoveryObserved', 'LabyClientHomeToAdventure'):
            assert native.run(tasker, entry)['native_ok'], entry
        assert len(controller.clicks) == 1 and not controller.swipes
        x, y = controller.clicks[0]
        assert 693 <= x <= 746 and 685 <= y <= 718
    finally:
        native.wait_job(tasker.post_stop(), seconds=10)


@pytest.mark.private_replay
@pytest.mark.parametrize('case', ['adventure_missing', 'schedule_missing', 'gift_missing', 'known_panel', 'loading', 'blank'])
def test_missing_words_or_overlay_cannot_pass_home_or_click(offline, case):
    image = saved_home()
    if case == 'blank': image[:] = 0
    elif case == 'known_panel':
        path = os.environ.get('PCR_LABYRINTH_PANEL_FRAME')
        if not path: pytest.skip('private saved announcement frame is not distributed')
        image = native.read_image(Path(path))
    elif case == 'loading': image[10:65, 95:260] = 0
    else:
        names = {'adventure_missing':['HomeAdventureText'],
                 'schedule_missing':['HomeScheduleText','HomeEdgeScheduleText'],
                 'gift_missing':['HomeGiftText','HomeEdgeGiftText']}[case]
        for name in names:
            x, y, w, h = game_session_pipeline(123)[name]['roi']
            image[y:y+h, x:x+w] = 0
    tasker, controller = setup(image)
    try:
        assert not native.run(tasker, 'LabyGameSessionWait_client_home')['native_ok']
        assert not native.run(tasker, 'LabyClientHomeToAdventure')['native_ok']
        assert not controller.clicks and not controller.swipes
    finally:
        native.wait_job(tasker.post_stop(), seconds=10)
