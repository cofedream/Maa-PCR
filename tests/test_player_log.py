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
