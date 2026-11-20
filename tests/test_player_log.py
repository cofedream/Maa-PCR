import json
from types import SimpleNamespace as NS

import numpy as np
import pytest
import threading
import time

from pcr_maa import player_log


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(player_log, '_state', None)
    yield
    player_log.close()


def test_disabled_diagnostics_do_not_touch_controller_or_stdout(capsys):
    player_log.text('disabled')
    player_log.observe({'nodes': []}, 'ObserveHome')
    player_log.capture(NS(), 'failed')
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('function', ['daily', 'clan', 'labyrinth'])
def test_safe_cached_picture_and_bounded_display_packet(tmp_path, function):
    rows = []
    run = 'a' * 32
    player_log.configure(tmp_path, run, function, rows.append)
    player_log.text('开始核对目标')
    player_log.observe({'nodes': [{'name': 'CBPage_battle'}], 'native_ok': True, 'task_id': 1}, 'CBPage_battle')
    frame = np.zeros((720, 1280, 3), np.uint8)
    player_log.capture(NS(controller=NS(cached_image=frame)), 'failed')
    player_log.flush()
    if function == 'labyrinth':
        assert [r['event'] for r in rows] == ['text', 'native', 'text', 'image']
        assert '供对照，最后节点 CBPage_battle，读取于 ' in rows[-2]['text']
        assert rows[-1]['name'] in rows[-2]['text']
    else:
        assert [r['event'] for r in rows] == ['text', 'native', 'image']
    assert all(r['run_id'] == run and r['function'] == function for r in rows)
    saved = tmp_path / '.runtime/player-logs' / function / run / rows[-1]['name']
    assert saved.is_file()
    assert sum(p.stat().st_size for p in saved.parent.iterdir()) <= player_log.QUOTA
    assert frame.shape == (720, 1280, 3)


@pytest.mark.parametrize('page', ['LabyClientTitleObserved', 'LabyClientProfileObserved', 'Captcha', 'Login'])
def test_login_and_identity_frames_are_not_saved(tmp_path, page):
    rows = []
    player_log.configure(tmp_path, 'a' * 32, 'daily', rows.append)
    player_log.observe({'nodes': [{'name': page}], 'native_ok': True}, page)
    player_log.capture(NS(controller=NS(cached_image=np.zeros((720, 1280, 3), np.uint8))), 'failed')
    player_log.flush()
    assert not list(tmp_path.rglob('*.png'))
    assert rows[-1]['event'] == 'text'


def test_diagnostic_sink_failure_does_not_change_business_execution(tmp_path):
    def broken(_): raise OSError('synthetic closed output')
    player_log.configure(tmp_path, 'b' * 32, 'clan', broken)
    player_log.text('info')
    player_log.observe({'nodes': [{'name': 'CBPage_battle'}]}, 'CBPage_battle')
    player_log.capture(NS(), 'failed')


@pytest.mark.parametrize('run,function', [('../escape', 'daily'), ('a'*32, 'other'), ('A'*32, 'clan')])
def test_identity_cannot_select_an_arbitrary_directory(tmp_path, run, function):
    with pytest.raises(ValueError, match='identity'):
        player_log.configure(tmp_path, run, function)


def test_alpha_packet_is_display_only_single_json_line(tmp_path, capsys):
    player_log.configure(tmp_path, 'c' * 32, 'daily')
    player_log.text('一行\n二行')
    player_log.flush()
    output = capsys.readouterr().out
    assert output.count('\n') == 1
    row = json.loads(output[len(player_log.PREFIX):])
    assert row['text'] == '一行\n二行'
    assert not {'ok', 'execute', 'grant', 'consumption_verified'} & row.keys()


def test_slow_logging_never_waits_in_native_business_path(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def slow(_):
        entered.set()
        release.wait(5)
    player_log.configure(tmp_path, 'd'*32, 'clan', slow)
    player_log.text('first')
    assert entered.wait(1)
    start=time.monotonic()
    try:
        for _ in range(1000):
            player_log.observe({'nodes':[{'name':'CBPage_battle'}]}, 'CBPage_battle')
        assert time.monotonic()-start < .5
        assert player_log._state['pending'].qsize() <= 128
    finally:
        release.set()


def test_picture_rotation_preserves_business_files_and_atomic_images(tmp_path):
    rows=[]
    player_log.configure(tmp_path,'e'*32,'clan',rows.append)
    player_log.observe({'nodes':[{'name':'CBPage_battle'}], 'native_ok': True},'CBPage_battle')
    ledger=tmp_path/'.runtime/clan-battle-daily-work/ledger/transactions.jsonl'
    ledger.parent.mkdir(parents=True)
    ledger.write_text('original unknown operation\n',encoding='utf-8')
    tasker=NS(controller=NS(cached_image=np.random.default_rng(1).integers(0,256,(720,1280,3),dtype=np.uint8)))
    for _ in range(8): player_log.capture(tasker,'failed')
    player_log.flush()
    area=tmp_path/'.runtime/player-logs/clan'
    pictures=list(area.rglob('*.png'))
    assert 1 <= len(pictures) < 8
    assert sum(p.stat().st_size for p in pictures) <= player_log.QUOTA
    assert not list(area.rglob('*.tmp'))
    assert ledger.read_text(encoding='utf-8') == 'original unknown operation\n'


def test_failed_observation_cannot_reuse_previous_safe_page(tmp_path):
    rows=[]
    player_log.configure(tmp_path,'e'*32,'daily',rows.append)
    player_log.observe({'nodes':[{'name':'DailyHome'}], 'native_ok':True},'DailyHome')
    player_log.observe({'nodes':[{'name':'DailyHome'}], 'native_ok':False},'DailyHome')
    player_log.capture(NS(controller=NS(cached_image=np.zeros((720,1280,3),np.uint8))),'failed')
    player_log.flush()
    assert not list(tmp_path.rglob('*.png'))
    assert rows[-1]['event'] == 'text'


def test_feedback_preserves_actual_frame_size_and_explains_empty_cache(tmp_path):
    import cv2
    rows = []
    player_log.configure(tmp_path, 'f'*32, 'daily', rows.append)
    player_log.observe({'nodes': [{'name': 'DailyHome'}], 'native_ok': True}, 'DailyHome')
    frame = np.full((720, 1440, 3), 31, np.uint8)
    player_log.capture(NS(controller=NS(cached_image=frame)), 'stopped')
    saved, = tmp_path.rglob('*.png')
    assert np.array_equal(cv2.imdecode(np.fromfile(saved, np.uint8), cv2.IMREAD_COLOR), frame)
    player_log.capture(NS(controller=NS(cached_image=np.empty((0, 0, 3), np.uint8))), 'stopped')
    player_log.flush()
    assert '缓存为空' in rows[-1]['text']


def test_startup_miss_exports_notice_and_exact_failed_recognition(tmp_path, monkeypatch):
    import cv2
    from pathlib import Path
    from pcr_maa import native
    from pcr_maa.labyrinth.game_session import game_session_pipeline
    from pcr_maa.ocr_diagnostics import FailureDiagnostics
    from pcr_maa.replay import SavedImages

    monkeypatch.setattr(native, 'RUNTIME', tmp_path/'native')
    rows = []
    player_log.configure(tmp_path, 'a'*32, 'labyrinth', rows.append)
    data = Path(__file__).parent/'data'
    notice = native.read_image(data/'labyrinth-start-data-download-notice-20261108.png')
    missed = native.read_image(data/'labyrinth-start-data-download-progress-20261108.png')
    controller = SavedImages([[notice], [missed]])
    resource = native.load_resource()
    tasker = native.bind(resource, controller)
    diagnostics = FailureDiagnostics(tmp_path/'ocr', 'test', 'labyrinth', {}).attach(tasker)
    entry = 'LabyGameSessionAfterTitleObserved'
    try:
        assert resource.override_pipeline(game_session_pipeline(123))
        assert resource.override_pipeline({entry: dict(timeout=50)})
        assert native.run(tasker, 'LabyClientNoticeCloseOnce')['native_ok']
        failed = native.run(tasker, entry)
        assert not failed['native_ok']
        player_log.capture(NS(controller=NS(cached_image=notice)), 'stopped')
        player_log.flush()
        images = [r['name'] for r in rows if r['event'] == 'image']
        assert len(images) == 2
        for name, expected in zip(images, [notice, missed]):
            saved, = (tmp_path/'.runtime/player-logs').rglob(name)
            assert np.array_equal(cv2.imdecode(np.fromfile(saved, np.uint8), cv2.IMREAD_COLOR), expected)
        assert any(entry in r.get('text', '') and 'success=False' in r['text'] for r in rows)
        captions = [r['text'] for r in rows if r['event'] == 'text']
        assert any(images[0] in s and '关闭前' in s for s in captions)
        assert any(images[1] in s and '识别失败' in s and entry in s
                   and f"task={failed['task_id']}" in s for s in captions)
        assert len(controller.clicks) == 1
        player_log.observe({'nodes': [{'name': ''}], 'native_ok': False}, 'Login')
        player_log.capture(tasker, 'stopped')
        assert len(list((tmp_path/'.runtime/player-logs').rglob('*.png'))) == 2
    finally:
        diagnostics.finish(False)
        native.stop_tasker(tasker)


def test_failure_picture_requires_the_current_recognition_and_a_shareable_node(tmp_path):
    rows = []
    player_log.configure(tmp_path, 'c'*32, 'labyrinth', rows.append)
    node = 'LabyBusinessObserved'
    frame = np.zeros((12, 16, 3), np.uint8)
    def miss(task_id, entry, observations):
        player_log.observe(dict(nodes=[], native_ok=False, task_id=task_id), entry, observations)
        player_log.capture(NS(), 'stopped')
        player_log.flush()

    def record(task_id, entry, rid, image):
        return (dict(task_id=task_id, reco_id=rid, entry=entry, node=entry, hit=False), image)

    miss(4, node, [record(4, node, 7, frame)])
    miss(5, node, [record(4, node, 7, frame), record(5, node, 8, None)])
    miss(6, 'LabyClientProfileObserved', [record(6, 'LabyClientProfileObserved', 9, frame)])
    assert len(list(tmp_path.rglob('*.png'))) == 1
    assert any('失败原帧' in r.get('text', '') and '未取得' in r['text'] for r in rows)


def test_labyrinth_restart_exports_last_confirmed_business_frame(tmp_path, monkeypatch):
    import cv2
    from pathlib import Path
    from pcr_maa import native
    from pcr_maa.labyrinth.scene import MaaSceneRuntime
    from pcr_maa.ocr_diagnostics import FailureDiagnostics
    from pcr_maa.replay import SavedImages

    monkeypatch.setattr(native, 'RUNTIME', tmp_path/'native')
    rows = []
    player_log.configure(tmp_path, 'b'*32, 'labyrinth', rows.append)
    shop = native.read_image(Path(__file__).parent/'data/labyrinth-shop-relics-20261108.png')
    title = np.full_like(shop, 193)
    controller = SavedImages([[shop], [title]])
    resource = native.load_resource()
    tasker = native.bind(resource, controller)
    diagnostics = FailureDiagnostics(tmp_path/'ocr', 'test', 'labyrinth', {}).attach(tasker)
    entry = 'LabyClientTitleStartOnce'
    try:
        scene = MaaSceneRuntime(tasker).observe(probe_kinds=('shop',))
        assert scene.kind == 'shop'
        assert not list(tmp_path.rglob('*.png'))
        controller.stage = 1
        assert resource.override_pipeline({entry: dict(recognition='DirectHit', action='DoNothing',
                                                       next=[], pre_delay=0, post_delay=0)})
        assert native.run(tasker, entry)['native_ok']
        player_log.capture(NS(controller=NS(cached_image=title)), 'stopped')
        player_log.flush()
        saved, = (tmp_path/'.runtime/player-logs').rglob('*.png')
        assert np.array_equal(cv2.imdecode(np.fromfile(saved, np.uint8), cv2.IMREAD_COLOR), shop)
        assert any('前序业务画面（shop，LabySceneObserved' in log and saved.name in log
                   and f"task={scene.native['task_id']}" in log and '记录于' in log
                   for log in (r.get('text', '') for r in rows))
        assert not controller.clicks and not controller.swipes
    finally:
        diagnostics.finish(False)
        native.stop_tasker(tasker)


def _confirmed_scene(kind, frame, *, task_id=10, frame_task_id=None, reco_id=12):
    node = 'LabySceneObserved'
    receipt = dict(native_ok=True, tasker_inited=True, task_id=task_id, nodes=[
        dict(name=node, completed=True, recognition=dict(kind=kind, payload={}))])
    row = dict(task_id=task_id if frame_task_id is None else frame_task_id,
               entry=node, node=node, hit=True, reco_id=reco_id)
    player_log.observe(receipt, node, [(row, frame)])


@pytest.mark.parametrize('kind,task_id,reco_id', [
    ('unknown', 10, 12), ('home', 10, 12), ('shop', 9, 12), ('shop', 10, 0),
])
def test_labyrinth_unconfirmed_scene_cannot_supply_a_picture_at_login(tmp_path, kind, task_id, reco_id):
    rows = []
    player_log.configure(tmp_path, 'd'*32, 'labyrinth', rows.append)
    frame = np.zeros((12, 16, 3), np.uint8)
    _confirmed_scene(kind, frame, frame_task_id=task_id, reco_id=reco_id)
    node = 'LabyChannelBeforeLoginStopOnce'
    player_log.observe(dict(native_ok=True, nodes=[dict(name=node)]), node)
    player_log.capture(NS(controller=NS(cached_image=frame)), 'stopped')
    player_log.flush()
    assert not list(tmp_path.rglob('*.png'))
    assert '本次黎明界运行没有可分享的业务画面' in rows[-1]['text']


@pytest.mark.parametrize('kind', ['unknown', 'home'])
def test_labyrinth_unapproved_current_scene_falls_back_to_confirmed_picture(tmp_path, kind):
    import cv2
    rows = []
    player_log.configure(tmp_path, 'f'*32, 'labyrinth', rows.append)
    previous = np.full((12, 16, 3), 11, np.uint8)
    current = np.full_like(previous, 22)
    _confirmed_scene('shop', previous)
    _confirmed_scene(kind, current, task_id=11)
    player_log.capture(NS(controller=NS(cached_image=current)), 'stopped')
    player_log.flush()
    saved, = tmp_path.rglob('*.png')
    assert np.array_equal(cv2.imdecode(np.fromfile(saved, np.uint8), cv2.IMREAD_COLOR), previous)
    assert any('前序业务画面（shop' in r.get('text', '') and saved.name in r['text'] for r in rows)


@pytest.mark.parametrize('failure_available', [True, False])
def test_labyrinth_failure_frame_takes_precedence_over_previous_picture(tmp_path, failure_available):
    import cv2
    rows = []
    player_log.configure(tmp_path, 'e'*32, 'labyrinth', rows.append)
    previous = np.full((12, 16, 3), 10, np.uint8)
    failed_frame = np.full_like(previous, 20)
    _confirmed_scene('shop', previous)
    node = 'LabyBusinessObserved'
    row = dict(task_id=20, reco_id=21, entry=node, node=node, hit=False)
    player_log.observe(dict(nodes=[], native_ok=False, task_id=20), node,
                       [(row, failed_frame if failure_available else None)])
    player_log.capture(NS(), 'stopped')
    player_log.flush()
    saved, = tmp_path.rglob('*.png')
    expected = failed_frame if failure_available else previous
    assert np.array_equal(cv2.imdecode(np.fromfile(saved, np.uint8), cv2.IMREAD_COLOR), expected)
    caption = '识别失败原帧' if failure_available else '前序业务画面'
    assert any(caption in r.get('text', '') and saved.name in r['text'] for r in rows)
