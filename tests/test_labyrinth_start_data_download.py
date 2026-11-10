"""Startup 数据下载 prompt: confirm once, then wait for the client's own fetch.

The live 2026-11-08 failure: after 开始游戏 the B-client showed 数据下载 /
即将下载新增数据。(98.42 MB) / 取消 / 确认. LabyGameSessionAfterTitleObserved
reported no page for 75 s and `_root` ended the batch with
game_session_readback_unknown. The live probe then measured: 确认 -> in-dialog
download progress (25.79 MB ... 95.30 MB) -> ~3 s of 数据连接 black screen ->
通知 over HOME after ~33 s, no client restart, no second title START. The
startup wait therefore has to (a) report the prompt with four sampled words,
(b) click only the 确认 word box, (c) poll the same read-only endpoint through
the untracked download screen in bounded windows, and (d) keep the normal
startup budget for the page that comes back (通知 is closed by the existing
sampled popup path). No live-game acceptance is claimed.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from pcr_maa import native
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.game_session import (
    _DATA_DOWNLOAD_WAIT_SECONDS, _OPERATIONS, MaaGameSession, _WaitWindowElapsed,
    game_session_pipeline)
from pcr_maa.labyrinth.restart import _CLIENT_NAVIGATION
from pcr_maa.labyrinth.snapshots import OpeningSnapshot
from pcr_maa.labyrinth.timing import Timings
from pcr_maa.replay import SavedImages

ROOT = Path(__file__).resolve().parents[1]
FRAME = ROOT / 'tests/data/labyrinth-start-data-download-20261108.png'
META = FRAME.with_suffix('.json')
PROGRESS = ROOT / 'tests/data/labyrinth-start-data-download-progress-20261108.png'
NOTICE = ROOT / 'tests/data/labyrinth-start-data-download-notice-20261108.png'
BINDING = dict(account_id=1423539397179, device_id='127.0.0.1:16384')
ACCOUNT = BINDING['account_id']
WORDS = {
    'LabyClientDataDownloadTitle': ('^数据下载$', [470, 160, 340, 70]),
    'LabyClientDataDownloadPrompt': ('^即将下载新增数据[。.]?$', [400, 290, 480, 90]),
    'LabyClientDataDownloadConfirmLabel': ('^确认$', [660, 440, 290, 100]),
    'LabyClientDataDownloadCancelLabel': ('^取消$', [400, 440, 290, 100]),
}
NAMES = ['LabyDateChangedObserved', 'LabyDisconnect6002Observed',
         'LabyClientNoticeObserved', 'LabyClientAnnouncementObserved',
         'LabyClientAfterTitleHomeObserved', 'LabyClientDownloadErrorObserved',
         'LabyClientDataDownloadObserved']
WAIT = 'LabyGameSessionTitleStabilityWait'
CONFIRM = 'LabyClientDataDownloadConfirmOnce'


def test_prompt_nodes_use_all_four_sampled_words_and_click_only_confirm():
    nodes = game_session_pipeline(ACCOUNT)
    for name, (expected, roi) in WORDS.items():
        node = nodes[name]
        assert node['recognition'] == 'OCR' and node['expected'] == expected
        # Detector windows like the other startup-dialog words (no only_rec).
        assert node['threshold'] == .8 and node.get('only_rec') is None
        assert node['roi'] == roi and node['action'] == 'DoNothing' and node['next'] == []
        x, y, w, h = roi
        assert 0 <= x and 0 <= y and x + w <= 1280 and y + h <= 720
    observed = nodes['LabyClientDataDownloadObserved']
    assert observed['recognition'] == 'And' and observed['action'] == 'DoNothing'
    assert observed['all_of'] == list(WORDS)
    click = nodes[CONFIRM]
    assert click['recognition'] == 'And' and click['action'] == 'Click'
    assert click['target'] is True and click['max_hit'] == 1 and click['timeout'] == 3000
    # box_index defaults to 0: the action target must be 确认, never the title,
    # the prompt or 取消.
    assert click['all_of'] == ['LabyClientDataDownloadConfirmLabel',
                               'LabyClientDataDownloadTitle',
                               'LabyClientDataDownloadPrompt',
                               'LabyClientDataDownloadCancelLabel']
    # The retry dialog (下载错误 / 通信超时 / 重试) remains a different page.
    assert 'LabyClientDataDownloadTitle' not in nodes['LabyClientDownloadErrorObserved']['all_of']
    assert ('LabyClientDataDownloadObserved'
            not in nodes['LabyClientDownloadErrorObserved']['all_of'])


def test_startup_wait_reports_the_prompt_after_the_existing_pages():
    any_of = game_session_pipeline(ACCOUNT)['LabyGameSessionAfterTitleObserved']['any_of']
    assert any_of == NAMES


def test_confirm_is_a_bounded_client_navigation_with_its_own_operation():
    assert 'client_data_download_confirm' in _OPERATIONS
    assert 'client_data_download_confirm' in _CLIENT_NAVIGATION
    assert 90 <= _DATA_DOWNLOAD_WAIT_SECONDS <= 600


@pytest.fixture(scope='module')
def offline_resource(tmp_path_factory):
    # One native resource for all saved-frame tests in this module; no device.
    native.RUNTIME = tmp_path_factory.mktemp('start-download-native')
    return native.load_resource()


def test_saved_failure_frame_hits_the_prompt_and_clicks_confirm(offline_resource):
    source = native.read_image(FRAME)
    resource = offline_resource
    controller = SavedImages([[source]])
    tasker = native.bind(resource, controller)
    try:
        assert resource.override_pipeline(game_session_pipeline(ACCOUNT))
        # The old startup wait had no branch for this screen; the new one does.
        assert native.run(tasker, 'LabyGameSessionAfterTitleObserved')['native_ok']
        assert native.run(tasker, 'LabyClientDataDownloadObserved')['native_ok']
        # Not one of the other known startup pages.
        assert not native.run(tasker, 'LabyClientDownloadErrorObserved')['native_ok']
        assert not native.run(tasker, 'LabyClientAfterTitleHomeObserved')['native_ok']
        for name, (_, (x, y, w, h)) in WORDS.items():
            receipt = native.run(tasker, name)
            assert receipt['native_ok'], name
            row = next(node for node in receipt['nodes'] if node['name'] == name)
            word, = row['recognition']['filtered']
            assert word['score'] >= .99
            box = word['box']
            assert x <= box[0] and y <= box[1]
            assert box[0] + box[2] <= x + w and box[1] + box[3] <= y + h
        assert not controller.clicks
        assert native.run(tasker, CONFIRM)['native_ok']
        assert len(controller.clicks) == 1
        click_x, click_y = controller.clicks[0]
        # Inside the 确认 word box (756,476,61,35) and the dialog's right button.
        assert 756 <= click_x <= 817 and 476 <= click_y <= 511
    finally:
        native.stop_tasker(tasker)


def test_saved_frame_metadata_matches_the_shipped_png():
    assert META.is_file()
    value = json.loads(META.read_text(encoding='utf-8'))
    frame = value['frames'][FRAME.name]
    assert frame['sha256'] == hashlib.sha256(FRAME.read_bytes()).hexdigest()
    assert (frame['width'], frame['height']) == (1280, 720)
    assert '数据下载' in value['purpose']


class FakeBatch:
    binding = NS(payload=lambda: BINDING, account_id=ACCOUNT)
    def __init__(self):
        self.operations = []
        self.observations = []
        self.stop = False
    def checkpoint(self):
        if self.stop:
            raise BatchStopped('stopped')
    def observation(self, value, *, _trace=True, **expected):
        self.observations.append(value)
        return value
    def transaction(self, operation, expected, send):
        self.operations.append(operation)
        try:
            result = send('a' * 32)
        except native.NativeInputNotSent as error:
            raise BatchStopped('native_input_not_sent') from error
        self.observation(result, _trace=False, operation_id='a' * 32, **expected)
        return result


def receipt(entry, kind, *, task_id=1):
    upto = NAMES.index(kind) + 1
    return dict(tasker_inited=True, task_id=task_id, native_ok=True,
                nodes=[dict(name=entry, completed=True, recognition=[
                    dict(name=name, algorithm='And',
                         box=[10, 10, 20, 20] if name == kind else None)
                    for name in NAMES[:upto]])])


def miss_receipt(*, task_id=1, complete=True, recognition_failed=True, action_started=False):
    return dict(tasker_inited=True, task_id=task_id, native_ok=False, nodes=[],
                native_evidence=dict(source='native_task_events', task_id=task_id,
                    started=True, finished=True, complete=complete,
                    recognition_failed=recognition_failed, action_started=action_started))


def session(monkeypatch, pages, *, miss_seconds=1):
    """A non-channel session whose native.run follows a scripted page sequence.

    ``pages`` is a list of page names and/or ``'miss'`` entries. A clean miss
    advances the fake clock by ``miss_seconds`` so each bounded poll window can
    expire without sleeping.
    """
    clock = [0.]
    monkeypatch.setattr('pcr_maa.labyrinth.game_session.time.monotonic', lambda: clock[0])
    overrides = []
    resource = NS(override_pipeline=lambda value: overrides.append(value) or True)
    adapter = MaaGameSession(NS(stopping=False, resource=resource), lambda **v: dict(v),
                             timings=Timings(), server='cn-bilibili')
    adapter.opening_snapshot = OpeningSnapshot(ACCOUNT, 169, 1, 1, ())
    inputs = []
    adapter._input = lambda batch, node: inputs.append(node) or dict(native_ok=True, node=node)
    script = list(pages)

    def run(tasker, node, **_):
        if node == WAIT:
            return dict(tasker_inited=True, task_id=1, native_ok=True,
                        nodes=[dict(name=WAIT, completed=True, recognition=[])])
        kind = script.pop(0)
        if kind == 'miss':
            clock[0] += miss_seconds
            return miss_receipt()
        clock[0] += 1
        return receipt(node, kind)
    monkeypatch.setattr(native, 'run', run)
    return adapter, clock, overrides, inputs


def test_confirm_once_then_poll_through_download_screen_until_home(monkeypatch):
    batch = FakeBatch()
    adapter, clock, overrides, inputs = session(monkeypatch, [
        'LabyClientDataDownloadObserved', 'miss',
        'LabyClientAfterTitleHomeObserved', 'LabyClientAfterTitleHomeObserved'])
    token = native.RUN_DEADLINE.set(2000.)
    try:
        page, result = adapter._after_title(batch)
        assert page == 'client_home'
        assert result['title_stability']['consecutive_frames'] == 2
        assert native.RUN_DEADLINE.get() == 2000.
    finally:
        native.RUN_DEADLINE.reset(token)
    assert batch.operations == ['client_data_download_confirm']
    assert inputs == [CONFIRM]
    windows = [row['LabyGameSessionAfterTitleObserved']['timeout'] for row in overrides]
    assert all(1 <= window <= 90_000 for window in windows)
    assert any(window == 75_000 for window in windows)
    assert any(window == 30_000 for window in windows)
    assert not any(op in ('client_download_retry', 'client_title_start')
                   for op in batch.operations)


def test_repeated_prompt_is_waited_on_never_clicked_again(monkeypatch):
    batch = FakeBatch()
    adapter, clock, _, inputs = session(monkeypatch, [
        'LabyClientDataDownloadObserved', 'LabyClientDataDownloadObserved',
        'LabyClientAfterTitleHomeObserved', 'LabyClientAfterTitleHomeObserved'])
    token = native.RUN_DEADLINE.set(2000.)
    try:
        page, _ = adapter._after_title(batch)
    finally:
        native.RUN_DEADLINE.reset(token)
    assert page == 'client_home'
    assert batch.operations == ['client_data_download_confirm']
    assert inputs == [CONFIRM]


def test_download_budget_expiry_stops_with_unknown_readback(monkeypatch):
    batch = FakeBatch()
    adapter, clock, _, inputs = session(monkeypatch,
        ['LabyClientDataDownloadObserved', 'miss', 'miss', 'miss', 'miss', 'miss',
         'miss', 'miss', 'miss', 'miss', 'miss', 'miss'], miss_seconds=31)
    token = native.RUN_DEADLINE.set(float('inf'))
    try:
        with pytest.raises(BatchStopped, match='^game_session_readback_unknown$'):
            adapter._after_title(batch)
    finally:
        native.RUN_DEADLINE.reset(token)
    assert batch.operations == ['client_data_download_confirm']
    assert clock[0] >= _DATA_DOWNLOAD_WAIT_SECONDS
    assert inputs == [CONFIRM]


def test_broken_wait_receipt_stops_instead_of_polling(monkeypatch):
    batch = FakeBatch()
    adapter, clock, _, _ = session(monkeypatch, ['LabyClientDataDownloadObserved'])
    calls = {'n': 0}
    def run(tasker, node, **_):
        calls['n'] += 1
        clock[0] += 1
        if calls['n'] == 1:
            return receipt(node, 'LabyClientDataDownloadObserved')
        return miss_receipt(recognition_failed=False)
    monkeypatch.setattr(native, 'run', run)
    token = native.RUN_DEADLINE.set(float('inf'))
    try:
        with pytest.raises(BatchStopped, match='^game_session_readback_unknown$'):
            adapter._after_title(batch)
    finally:
        native.RUN_DEADLINE.reset(token)
    assert batch.operations == ['client_data_download_confirm']
    assert calls['n'] == 2


def test_stop_during_download_poll_is_obeyed(monkeypatch):
    batch = FakeBatch()
    adapter, clock, _, inputs = session(monkeypatch, ['LabyClientDataDownloadObserved'])
    calls = {'n': 0}
    def run(tasker, node, **_):
        calls['n'] += 1
        clock[0] += 31
        if calls['n'] == 1:
            return receipt(node, 'LabyClientDataDownloadObserved')
        batch.stop = True
        return miss_receipt()
    monkeypatch.setattr(native, 'run', run)
    token = native.RUN_DEADLINE.set(float('inf'))
    try:
        with pytest.raises(BatchStopped, match='^stopped$'):
            adapter._after_title(batch)
    finally:
        native.RUN_DEADLINE.reset(token)
    assert batch.operations == ['client_data_download_confirm']
    assert inputs == [CONFIRM]


def test_unsent_confirm_click_stops_without_retry(monkeypatch):
    batch = FakeBatch()
    adapter, clock, _, _ = session(monkeypatch, ['LabyClientDataDownloadObserved'])
    def unsent(batch, node):
        raise native.NativeInputNotSent(dict(input_execution='not_sent', action_started=False))
    adapter._input = unsent
    token = native.RUN_DEADLINE.set(float('inf'))
    try:
        with pytest.raises(BatchStopped, match='^native_input_not_sent$'):
            adapter._after_title(batch)
    finally:
        native.RUN_DEADLINE.reset(token)
    assert batch.operations == ['client_data_download_confirm']


NATIVE_DEADLINE = 'Native task exceeded 90 seconds or inspection deadline'


def channel_session(monkeypatch, clock, script, *, miss_seconds=10):
    """A channel session driving the real `_channel_observe` window loop.

    `script` entries are page names, ``miss`` (a clean empty window step) or
    ``deadline`` (the SDK reporting its own deadline because the task outlived
    the window it was dispatched in, which is what a live channel server does
    when the last 500 ms task of a window straddles its end).
    """
    monkeypatch.setattr('pcr_maa.labyrinth.game_session.time.monotonic', lambda: clock[0])
    resource = NS(override_pipeline=lambda value: True,
                  get_node_data=lambda name: dict(timeout=20000))
    adapter = MaaGameSession(NS(stopping=False, resource=resource), lambda **v: dict(v),
                             timings=Timings(), server='cn-xiaomi')
    adapter.opening_snapshot = OpeningSnapshot(ACCOUNT, 169, 1, 1, ())
    adapter.channel_prompts = NS(poll=lambda batch, fields: False)
    adapter._input = lambda batch, node: dict(native_ok=True, node=node)
    steps = list(script)
    calls = []

    def run(tasker, node, **_):
        if node == WAIT:
            return dict(tasker_inited=True, task_id=1, native_ok=True,
                        nodes=[dict(name=WAIT, completed=True, recognition=[])])
        step = steps.pop(0)
        calls.append(step)
        if step == 'miss':
            clock[0] += miss_seconds
            return miss_receipt()
        if step == 'bad':
            clock[0] += miss_seconds
            return miss_receipt(complete=False)
        if step == 'deadline':
            clock[0] += miss_seconds
            raise RuntimeError(NATIVE_DEADLINE)
        if step == 'fault':
            raise RuntimeError('Native tasker stopped or blocked; outcome unknown, no retry')
        clock[0] += 1
        return receipt(node, step)
    monkeypatch.setattr(native, 'run', run)
    return adapter, clock, calls


def test_channel_download_window_survives_the_native_deadline(monkeypatch):
    """The window end is a business boundary, not a native fault to crash on."""
    clock = [0.]
    adapter, clock, calls = channel_session(monkeypatch, clock, [
        'LabyClientDataDownloadObserved', 'miss', 'miss', 'deadline',
        'LabyClientAfterTitleHomeObserved', 'LabyClientAfterTitleHomeObserved'])
    token = native.RUN_DEADLINE.set(2000.)
    try:
        page, _ = adapter._after_title(FakeBatch())
    finally:
        native.RUN_DEADLINE.reset(token)
    assert page == 'client_home'
    # Two clean steps and the deadline came from the first download window.
    assert calls[:4] == ['LabyClientDataDownloadObserved', 'miss', 'miss', 'deadline']


def test_channel_incomplete_receipt_stops_instead_of_polling(monkeypatch):
    """An unproven receipt is not a window end: it must stop the wait."""
    clock = [0.]
    adapter, clock, calls = channel_session(monkeypatch, clock, [
        'LabyClientDataDownloadObserved', 'bad',
        'LabyClientAfterTitleHomeObserved', 'LabyClientAfterTitleHomeObserved'])

    batch = FakeBatch()
    token = native.RUN_DEADLINE.set(2000.)
    try:
        with pytest.raises(BatchStopped, match='^game_session_readback_unknown$'):
            adapter._after_title(batch)
    finally:
        native.RUN_DEADLINE.reset(token)
    assert batch.operations == ['client_data_download_confirm']


def test_channel_native_fault_is_not_swallowed_as_a_window_end(monkeypatch):
    """Only the SDK's own deadline is this window; real faults stay fatal."""
    clock = [0.]
    adapter, clock, calls = channel_session(monkeypatch, clock, [
        'LabyClientDataDownloadObserved', 'fault'])
    token = native.RUN_DEADLINE.set(2000.)
    try:
        with pytest.raises(RuntimeError, match='Native tasker stopped or blocked'):
            adapter._after_title(FakeBatch())
    finally:
        native.RUN_DEADLINE.reset(token)


def _channel_adapter(monkeypatch, clock):
    adapter = MaaGameSession(NS(stopping=False, resource=NS(override_pipeline=lambda _: True)),
        lambda **v: dict(v), timings=Timings(), server='cn-xiaomi')
    adapter.opening_snapshot = OpeningSnapshot(ACCOUNT, 169, 1, 1, ())
    adapter._input = lambda batch, node: dict(native_ok=True, node=node)
    monkeypatch.setattr(native, 'run', lambda tasker, node, **_: dict(
        tasker_inited=True, task_id=1, native_ok=True,
        nodes=[dict(name=node, completed=True, recognition=[])]))
    return adapter


def test_channel_download_window_expiry_is_retried(monkeypatch):
    clock = [0.]
    monkeypatch.setattr('pcr_maa.labyrinth.game_session.time.monotonic', lambda: clock[0])
    adapter = _channel_adapter(monkeypatch, clock)
    batch = FakeBatch()
    outcomes = iter(['LabyClientDataDownloadObserved', 'expire', 'expire',
                     'LabyClientAfterTitleHomeObserved', 'LabyClientAfterTitleHomeObserved'])
    def observe(batch, entry, *, seconds):
        kind = next(outcomes)
        # A real _channel_observe returns as soon as a page matches; only an
        # expired window consumes the whole requested window.
        clock[0] += 1 if kind != 'expire' else seconds
        if kind == 'expire':
            raise _WaitWindowElapsed()
        return receipt(entry, kind)
    monkeypatch.setattr(adapter, '_channel_observe', observe)
    token = native.RUN_DEADLINE.set(float('inf'))
    try:
        page, _ = adapter._after_title(batch)
    finally:
        native.RUN_DEADLINE.reset(token)
    assert page == 'client_home'
    assert batch.operations == ['client_data_download_confirm']


def test_channel_non_expiry_failure_is_not_retried(monkeypatch):
    clock = [0.]
    monkeypatch.setattr('pcr_maa.labyrinth.game_session.time.monotonic', lambda: clock[0])
    adapter = _channel_adapter(monkeypatch, clock)
    batch = FakeBatch()
    calls = {'n': 0}
    def observe(batch, entry, *, seconds):
        calls['n'] += 1
        if calls['n'] == 1:
            return receipt(entry, 'LabyClientDataDownloadObserved')
        raise BatchStopped('game_session_pipeline_unavailable')
    monkeypatch.setattr(adapter, '_channel_observe', observe)
    token = native.RUN_DEADLINE.set(float('inf'))
    try:
        with pytest.raises(BatchStopped, match='^game_session_pipeline_unavailable$'):
            adapter._after_title(batch)
    finally:
        native.RUN_DEADLINE.reset(token)
    assert batch.operations == ['client_data_download_confirm']


def _replay(resource, image_path, names):
    image = native.read_image(image_path)
    controller = SavedImages([[image]])
    tasker = native.bind(resource, controller)
    try:
        assert resource.override_pipeline(game_session_pipeline(ACCOUNT))
        # The live wait overrides this budget per call; one short window is all
        # a saved frame needs to prove a hit or a clean miss.
        assert resource.override_pipeline({'LabyGameSessionAfterTitleObserved': dict(timeout=3000)})
        return {name: native.run(tasker, name) for name in names}, controller
    finally:
        native.stop_tasker(tasker)


def test_download_progress_frame_is_not_a_known_startup_page(offline_resource):
    # 数据下载中 25.79MB/98.42MB: no page of ours is on screen while the client
    # fetches, so one bounded window must end as a clean miss, not a stop.
    receipts, _ = _replay(offline_resource, PROGRESS, ['LabyGameSessionAfterTitleObserved',
        'LabyClientDataDownloadObserved', 'LabyClientNoticeObserved',
        'LabyClientAfterTitleHomeObserved', 'LabyClientDownloadErrorObserved'])
    assert not any(receipt['native_ok'] for receipt in receipts.values())
    assert json.loads(PROGRESS.with_suffix('.json').read_text(encoding='utf-8'))[
        'expected']['startup_wait_hit'] is False


def test_post_download_notice_frame_returns_the_existing_popup_page(offline_resource):
    # After the fetch the client goes straight to HOME under the sampled 通知
    # activity; the existing close path must handle it before HOME counts.
    receipts, controller = _replay(offline_resource, NOTICE, ['LabyClientDataDownloadObserved',
        'LabyClientNoticeObserved', 'LabyClientAfterTitleHomeObserved',
        'LabyGameSessionAfterTitleObserved', 'LabyClientNoticeCloseOnce'])
    assert not receipts['LabyClientDataDownloadObserved']['native_ok']
    assert receipts['LabyClientNoticeObserved']['native_ok']
    assert not receipts['LabyClientAfterTitleHomeObserved']['native_ok']
    assert receipts['LabyGameSessionAfterTitleObserved']['native_ok']
    assert json.loads(NOTICE.with_suffix('.json').read_text(encoding='utf-8'))[
        'expected'] == {'data_download_observed': False, 'notice_observed': True,
                        'home_observed': False, 'startup_wait_hit': True,
                        'startup_page': 'notice'}
    assert receipts['LabyClientNoticeCloseOnce']['native_ok']
    assert len(controller.clicks) == 1
    x, y = controller.clicks[0]
    assert 505 <= x <= 790 and 610 <= y <= 673
