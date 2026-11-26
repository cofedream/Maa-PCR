import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pcr_maa import native
from pcr_maa.optional_sweep_native import NativeOptionalSweep
from pcr_maa.optional_sweep_navigation import _page, navigate, stage_frame, stage_identity
from pcr_maa.replay import SavedImages


def word(text, x=400, y=200, width=75):
    return dict(text=text, score=.98, box=[x,y,width,26])


def reading():
    return [word('圣迹调查关卡3级',80,52), word('剩余挑战次数',400,400,120),
            word('3/5',550,403), word('使用1张',820,430)]


def test_survey_identity_and_first_clear_are_independent():
    words = reading()
    assert stage_identity(words, 'sanctum_survey', 3)
    assert not stage_identity(words, 'temple_survey', 3)
    assert not stage_identity(words + [word('圣迹调查关卡4级')], 'sanctum_survey', 3)
    assert stage_frame(words, 'sanctum_survey', 3) is None
    assert stage_frame(words + [word('未通关')], 'sanctum_survey', 3)['state'] == 'locked'
    image = np.zeros((720,1280,3), dtype=np.uint8)
    for x in (1016,1100):
        image[54:57,x-1:x+2] = (98,228,245)
    assert stage_frame(words, 'sanctum_survey', 3, image) is None
    image[54:57,1184:1187] = (98,228,245)
    assert stage_frame(words, 'sanctum_survey', 3, image)['remaining'] == 3
    assert stage_frame(words + [word('扫荡确认')], 'sanctum_survey', 3, image) is None
    for title in ('扫荡券确认', '要消耗扫荡券和体力', '通关关卡吗？', '确认'):
        assert stage_frame(words + [word(title)], 'sanctum_survey', 3, image) is None


def test_counter_requires_one_value_beside_its_label():
    words = reading() + [word('已通关')]
    assert stage_frame(words + [word('3/5',750,405)], 'sanctum_survey', 3)['limit'] == 5
    assert stage_frame(words + [word('4/5',635,405)], 'sanctum_survey', 3) is None
    words[2] = word('6/5',550,403)
    assert stage_frame(words, 'sanctum_survey', 3) is None
    words[2] = word('0/5',550,403)
    assert stage_frame(words, 'sanctum_survey', 3)['remaining'] == 0


def test_cloister_requires_selected_card_and_usable_control_or_own_clear_badge():
    words = [word('露娜之塔',78,22),word('回廊探索',121,322),
             word('剩余挑战次数',952,409,116),word('2/5',1178,409),
             word('使用1张',1042,488,87),word('通关',183,495)]
    words.extend(word(f'wave{i}',459+(i-1)*110,125) for i in (1,2,3))
    image = np.zeros((720,1280,3), dtype=np.uint8)
    # Independently guarded indicators for the active row and its ticket control.
    image[343:369,349:371] = (115,196,232)
    assert stage_frame(words, 'tower_cloister', None, image) is None
    image[489:514,1029:1142] = (249,154,76)
    assert stage_frame(words, 'tower_cloister', None, image)['remaining'] == 2
    image[343:369,349:371] = 0
    assert stage_frame(words, 'tower_cloister', None, image) is None


def test_offscreen_stage_requires_scroll_progress_and_guarded_selection():
    def rows(*numbers):
        words = [word('圣迹调查',85,18)]
        for index, number in enumerate(numbers):
            y = 146 + 145*index
            words.extend([word(f'圣迹调查关卡{number}级',862,y),
                          word('剩余挑战次数',862,y+78),word('5/5',1110,y+78)])
        return words

    class Port:
        module,stage = 'sanctum_survey', 3
        def __init__(self):
            self.pages = [rows(9,8,7), rows(6,5,4), rows(3,2,1), reading()+[word('已通关')]]
            self.scrolls = self.clicks = 0
        def words(self, ctx, argv):
            return self.pages[0]
        def frame(self, guard, *, click=False):
            hit = guard(SimpleNamespace(run_recognition=lambda *_: None), SimpleNamespace(image=None))
            assert hit is not None
            if click:
                self.clicks += 1
                self.pages.pop(0)
            return hit[1]
        def scroll_list(self, guard, *, down):
            assert down
            evidence = self.frame(guard)
            assert evidence['page'] == 'scroll_list'
            self.scrolls += 1
            self.pages.pop(0)
    port = Port()
    assert navigate(port)['state'] == 'ready'
    assert (port.scrolls,port.clicks) == (2,1)


ADVENTURE = native.RESOURCE / 'clan-battle-calibrations/cn-bilibili-202610141448/samples/adventure.png'


def test_saved_cn_adventure_routes_to_special_quests():
    controller = SavedImages([[native.read_image(ADVENTURE)]])
    resource = native.load_resource(cpu_only=True)
    tasker = native.bind(resource, controller)
    port = NativeOptionalSweep(tasker, 'sanctum_survey', 3)
    def guard(ctx, argv):
        page = _page(port, ctx, argv, port.words(ctx, argv))
        if page and page[0] == 'adventure':
            return page[1]['box'], dict(page=page[0])
    try:
        assert port.frame(guard, click=True)['native_ok']
        assert len(controller.clicks) == 1
        x, y = controller.clicks[0]
        assert 900 < x < 1070 and 100 < y < 277
    finally:
        native.wait_job(tasker.post_stop(), seconds=10)


@pytest.mark.private_replay
def test_saved_cn_special_quests_routes_and_closed_tower():
    path = os.environ.get('PCR_SPECIAL_QUESTS_FRAME')
    if not path:
        pytest.skip('private CN special-quests frame is not distributed')
    controller = SavedImages([[native.read_image(Path(path))]])
    resource = native.load_resource(cpu_only=True)
    tasker = native.bind(resource, controller)
    try:
        for module, left, right in [('temple_survey',940,1220), ('sanctum_survey',645,925)]:
            port = NativeOptionalSweep(tasker,module,1)
            def guard(ctx, argv):
                page = _page(port,ctx,argv,port.words(ctx,argv))
                if page and page[0] == 'special':
                    return page[1]['box'], dict(page='special')
            assert port.frame(guard,click=True)['native_ok']
            x,y = controller.clicks[-1]
            assert left < x < right and 343 < y < 438
        port = NativeOptionalSweep(tasker,'tower_cloister',None)
        assert navigate(port)['state'] == 'closed'
        assert len(controller.clicks) == 2
    finally:
        native.wait_job(tasker.post_stop(),seconds=10)


@pytest.mark.private_replay
@pytest.mark.parametrize('module,stage,stem,state', [
    ('sanctum_survey', 9, 'sanctum-9-detail', 'ready'),
    ('temple_survey', 4, 'temple-4-uncleared', 'locked'),
    ('temple_survey', 3, 'temple-3-cleared', 'ready'),
])
def test_current_cn_survey_home_route(module, stage, stem, state):
    location = os.environ.get('PCR_CN_OPTIONAL_SWEEP_FRAMES')
    if not location:
        pytest.skip('private CN survey quest frames are not distributed')
    folder = Path(location)
    prefix = 'temple' if module == 'temple_survey' else 'sanctum'
    scenes = ['observed', 'adventure', 'special', prefix+'-list', stem,
              prefix+'-list','home-ready']
    frames = [[native.read_image(folder/(name+'.png'))] for name in scenes]
    frames[-1].insert(0,native.read_image(folder/'home-after-readonly.png'))
    controller = SavedImages(frames)
    resource = native.load_resource(cpu_only=True)
    tasker = native.bind(resource, controller)
    try:
        port = NativeOptionalSweep(tasker,module,stage)
        reading = port.read()
        assert (reading['module'], reading['state'], reading['stage']) == (module,state,stage)
        assert (reading['remaining'], reading['limit']) == (5,5)
        assert len(controller.clicks) == 4 and not controller.swipes
        assert port.home()['native_ok']
        assert len(controller.clicks) == 6 and not controller.swipes
    finally:
        native.wait_job(tasker.post_stop(),seconds=10)


@pytest.mark.private_replay
def test_current_cn_summit_preserves_scene():
    location = os.environ.get('PCR_CN_OPTIONAL_SWEEP_FRAMES')
    if not location:
        pytest.skip('private CN summit frame is not distributed')
    controller = SavedImages([[native.read_image(Path(location)/'tower-main.png')]])
    resource = native.load_resource(cpu_only=True)
    tasker = native.bind(resource,controller)
    try:
        with pytest.raises(RuntimeError, match='未能确认回廊入口'):
            NativeOptionalSweep(tasker,'tower_cloister',None).read()
        assert not controller.clicks and not controller.swipes
    finally:
        native.wait_job(tasker.post_stop(),seconds=10)
