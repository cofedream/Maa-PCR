"""HOME keeps complete paired layouts and rejects visible notice tabs."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from maa.define import AlgorithmEnum

from pcr_maa import home_guard, native
from pcr_maa.labyrinth.game_session import game_session_pipeline
from pcr_maa.replay import SavedImages


def test_home_layouts_require_three_complete_words_at_one_layout():
    nodes = game_session_pipeline(123)
    assert nodes['HomeTextLayout']['any_of'] == ['HomeDefaultTextLayout', 'HomeEdgeTextLayout']
    assert nodes['HomeDefaultTextLayout']['all_of'] == ['HomeAdventureText', 'HomeScheduleText', 'HomeGiftText']
    assert nodes['HomeEdgeTextLayout']['all_of'] == ['HomeAdventureText', 'HomeEdgeScheduleText', 'HomeEdgeGiftText']
    for names in (nodes['HomeDefaultTextLayout']['all_of'], nodes['HomeEdgeTextLayout']['all_of']):
        for name, word in zip(names, ('冒险', '日程表', '礼物')):
            assert nodes[name]['expected'] == '^' + word + '$'
            assert nodes[name]['threshold'] == .8 and nodes[name]['only_rec'] is True
    for entry in ('LabyClientGameHomeObserved', 'LabyClientHomeToAdventure', 'LabyClientAfterTitleHomeObserved'):
        assert nodes[entry]['all_of'] == ['HomeTextLayout', 'HomeNoKnownLargePanel']
    assert nodes['LabyClientHomeToAdventure']['max_hit'] == 1
    assert nodes['LabyClientHomeToAdventure']['target'] is True


def response(algorithm, hit, detail):
    return SimpleNamespace(algorithm=algorithm, hit=hit, raw_detail=detail, reco_id=1)


class Context:
    def __init__(self, notice):
        self.notice = notice
        self.calls = []

    def run_recognition(self, name, image):
        self.calls.append(name)
        if name == 'HomeKnownLargePanel': return response(AlgorithmEnum.And, False, [])
        if name == 'HomeNoticeTabs': return self.notice
        if name == 'HomeDataConnection':
            return response(AlgorithmEnum.OCR, False, dict(all=[dict(box=[980,15,285,65],text='',score=0.)], filtered=[], best=None))
        raise AssertionError(name)


def notice_miss():
    return response(AlgorithmEnum.Or,False,[dict(name=name,algorithm='OCR',box=None)
        for name in ('HomeNoticeUpdatesText','HomeNoticeCalendarText')])


@pytest.mark.parametrize('notice', [None, response(AlgorithmEnum.OCR,False,[]),
    response(AlgorithmEnum.Or,False,None), response(AlgorithmEnum.Or,True,[]),
    response(AlgorithmEnum.Or,False,[]), response(AlgorithmEnum.Or,False,[{}])])
def test_notice_hit_or_missing_native_evidence_stops_before_connection_check(notice):
    context = Context(notice)
    assert home_guard.HomeNoKnownLargePanel().analyze(context, SimpleNamespace(image=np.zeros((720,1280,3),np.uint8))) is None
    assert context.calls == ['HomeKnownLargePanel', 'HomeNoticeTabs']


def test_home_guard_does_not_read_exp_bar():
    context = Context(notice_miss())
    assert home_guard.HomeNoKnownLargePanel().analyze(context, SimpleNamespace(image=np.zeros((720,1280,3),np.uint8))) is not None
    assert context.calls == ['HomeKnownLargePanel','HomeNoticeTabs','HomeDataConnection']


@pytest.mark.parametrize('busy', [None, response(AlgorithmEnum.OCR,True,{}),
    response(AlgorithmEnum.OCR,False,{}),
    response(AlgorithmEnum.OCR,False,dict(all=[],filtered=[],best=None))])
def test_connection_hit_or_missing_evidence_still_blocks_home(busy):
    context = Context(notice_miss())
    original = context.run_recognition
    context.run_recognition = lambda name, image: busy if name == 'HomeDataConnection' else original(name, image)
    assert home_guard.HomeNoKnownLargePanel().analyze(context, SimpleNamespace(image=np.zeros((720,1280,3),np.uint8))) is None


@pytest.mark.private_replay
@pytest.mark.parametrize('empty_exp', [False, True])
@pytest.mark.parametrize('entry', ['ObserveHome', 'RewardsHomeRead',
    'LabyClientGameHomeObserved', 'EnterAdventure'])
def test_level_up_home_is_accepted_with_short_or_empty_exp(entry, empty_exp, monkeypatch):
    path = os.environ.get('PCR_HOME_LEVEL_UP_FRAME')
    if not path: pytest.skip('private level-up HOME frame is not distributed')
    import socket
    def deny(*args, **kwargs): raise AssertionError('offline saved frames only')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    image = native.read_image(Path(path))
    if empty_exp: image[22:62,90:250] = 0
    controller = SavedImages([[image]])
    resource = native.load_resource();tasker = native.bind(resource,controller)
    try:
        assert resource.override_pipeline(game_session_pipeline(123))
        assert resource.override_pipeline({entry:dict(next=['ObserveHome'] if entry == 'RewardsHomeRead' else [],
            on_error=[],timeout=1,pre_delay=0,post_delay=0)})
        receipt = native.run(tasker,entry)
        assert receipt['native_ok']
        if entry == 'RewardsHomeRead':
            assert any(n['name'] == 'ObserveHome' and n['completed'] for n in receipt['nodes'])
        assert not controller.swipes
        assert controller.clicks == ([[718,681]] if entry == 'EnterAdventure' else [])
    finally:
        native.stop_tasker(tasker)


@pytest.mark.private_replay
@pytest.mark.parametrize('frame_id,expected', [('real-001',True),('real-065',True),
    ('real-001-notice-blend-0.35',False),('real-001-notice-blend-0.6',False),
    ('extra-notice-updates-hidden',False),('extra-notice-calendar-hidden',False),
    ('extra-default-schedule-edge-gift',False),('extra-edge-schedule-default-gift',False)])
@pytest.mark.parametrize('entry', ['LabyClientGameHomeObserved','ObserveHome','LabyClientHomeToAdventure','EnterAdventure'])
def test_saved_layout_and_translucent_notice_at_observe_and_input(frame_id, expected, entry, monkeypatch):
    manifest = os.environ.get('PCR_HOME_GUARD_CASES')
    if not manifest: pytest.skip('private saved frames are not distributed')
    import socket
    from maa.controller import AdbController
    from maa.toolkit import Toolkit
    def deny(*args, **kwargs): raise AssertionError('offline saved frames only')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr(AdbController, '__init__', deny)
    monkeypatch.setattr(Toolkit, 'find_adb_devices', deny)
    image = native.read_image(json.loads(Path(manifest).read_text(encoding='utf-8'))[frame_id])
    controller = SavedImages([[image]])
    resource = native.load_resource();tasker = native.bind(resource,controller)
    try:
        assert resource.override_pipeline(game_session_pipeline(123))
        assert resource.override_pipeline({entry:dict(next=[],on_error=[],timeout=1,pre_delay=0,post_delay=0)})
        assert native.run(tasker,entry)['native_ok'] is expected
        if expected and entry in ('LabyClientHomeToAdventure','EnterAdventure'):
            assert len(controller.clicks)==1 and not controller.swipes
            x,y=controller.clicks[0]
            if entry == 'EnterAdventure': assert (x,y)==(718,681)
            else: assert 693<=x<=746 and 685<=y<=718
        else:
            assert not controller.clicks and not controller.swipes
    finally:
        native.stop_tasker(tasker)
