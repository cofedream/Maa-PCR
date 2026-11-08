"""Sampled HOME popup: the activity-ending announcement is closed on its own anchors.

ROIs were measured on an actual 1280x720 client frame (word boxes: 活动结束预告
556,43,166,28; 前往活动 732,624,108,28; 关闭 465,623,57,30). The popup appears
over HOME right after the 通知 panel, so it must be reported by the startup wait
and closed as its own durable intent before HOME counts again.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from pcr_maa import native
from pcr_maa.labyrinth import game_session
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.game_session import (
    _OPERATIONS, _STARTUP_POPUP_CLOSE, MaaGameSession, game_session_pipeline)
from pcr_maa.labyrinth.snapshots import OpeningSnapshot
from pcr_maa.labyrinth.timing import Timings
from pcr_maa.replay import SavedImages

ROOT = Path(__file__).resolve().parents[1]
BINDING = dict(account_id=1423539397179, device_id='127.0.0.1:16384')
ANCHORS = (
    ('LabyClientAnnouncementTitle', '^活动结束预告$', [531, 33, 216, 48], True),
    ('LabyClientAnnouncementActivityLabel', '^前往活动$', [700, 600, 175, 70], True),
    ('LabyClientAnnouncementCloseLabel', '^关闭$', [440, 608, 107, 51], False),
)


def test_announcement_nodes_keep_sampled_anchors_and_close_the_close_label():
    nodes = game_session_pipeline(123)
    for name, expected, roi, only_rec in ANCHORS:
        assert nodes[name]['recognition'] == 'OCR'
        assert nodes[name]['expected'] == expected
        assert nodes[name]['threshold'] == .8 and nodes[name]['only_rec'] is only_rec
        assert nodes[name]['action'] == 'DoNothing' and nodes[name]['next'] == []
        assert nodes[name]['roi'] == roi
        x, y, w, h = roi
        assert 0 <= x and 0 <= y and x + w <= 1280 and y + h <= 720
    close = nodes['LabyClientAnnouncementCloseOnce']
    assert close['recognition'] == 'And' and close['action'] == 'Click'
    assert close['target'] is True and close['max_hit'] == 1 and close['timeout'] == 3000
    # box_index defaults to 0: the action target must be 关闭, never the title
    # or 前往活动.
    assert close['all_of'] == ['LabyClientAnnouncementCloseLabel',
                               'LabyClientAnnouncementTitle',
                               'LabyClientAnnouncementActivityLabel']
    assert close['all_of'][0] == 'LabyClientAnnouncementCloseLabel'
    observed = nodes['LabyClientAnnouncementObserved']
    assert observed['recognition'] == 'And' and observed['action'] == 'DoNothing'
    assert observed['all_of'] == ['LabyClientAnnouncementTitle',
                                  'LabyClientAnnouncementActivityLabel',
                                  'LabyClientAnnouncementCloseLabel']


def test_announcement_is_reported_by_the_startup_wait_and_guarded_by_title():
    nodes = game_session_pipeline(123)
    any_of = nodes['LabyGameSessionAfterTitleObserved']['any_of']
    assert any_of[:4] == ['LabyDateChangedObserved', 'LabyDisconnect6002Observed',
                          'LabyClientNoticeObserved', 'LabyClientAnnouncementObserved']
    assert any_of[4:] == ['LabyClientAfterTitleHomeObserved', 'LabyClientDownloadErrorObserved',
                          'LabyClientDataDownloadObserved']
    shared = json.loads((native.RESOURCE / 'pipeline/presets.json').read_text(encoding='utf-8'))
    assert '活动结束预告' in shared['HomeModalTitle']['expected']
    assert shared['HomeKnownLargePanel']['all_of'] == ['HomePaleCenter', 'HomeModalTitle']


def test_announcement_close_is_a_known_client_navigation_with_its_own_operation():
    from pcr_maa.labyrinth.restart import _CLIENT_NAVIGATION
    assert 'client_announcement_close' in _OPERATIONS
    assert 'client_announcement_close' in _CLIENT_NAVIGATION
    assert _STARTUP_POPUP_CLOSE == {
        'notice': ('client_notice_close', 'LabyClientNoticeCloseOnce'),
        'announcement': ('client_announcement_close', 'LabyClientAnnouncementCloseOnce'),
    }


class FakeBatch:
    stopped = staticmethod(lambda: False)
    binding = NS(payload=lambda: BINDING, account_id=BINDING['account_id'])
    def __init__(self):
        self.operations = []
        self.observations = []
        self.last_frame = 0
        self.invalidated = False
    def checkpoint(self):
        if self.stopped(): raise BatchStopped('stopped')
    def invalidate_game_session(self):
        self.invalidated = True
    def observation(self, value, *, _trace=True, **expected):
        assert value.get('verified') is True and value['binding'] == BINDING
        assert value['frame'] > self.last_frame
        for key, wanted in expected.items(): assert value.get(key) == wanted, (key, value.get(key), wanted)
        self.last_frame = value['frame']
        self.observations.append(value)
        return value
    def transaction(self, operation, expected, send):
        self.operations.append(operation)
        try:
            result = send('a' * 32)
        except native.NativeInputNotSent as error:
            self.invalidated = False  # positive not-sent evidence keeps the session
            raise BatchStopped('native_input_not_sent') from error
        self.observation(result, _trace=False, operation_id='a' * 32, **expected)
        return result


def session(batch, pages):
    frames = [0]
    def stamp(**fields):
        frames[0] += 1
        return dict(verified=True, binding=BINDING, frame=frames[0], **fields)
    adapter = MaaGameSession(NS(stopping=False, resource=NS(override_pipeline=lambda _: True)),
        stamp, server='cn-bilibili')
    adapter.binding = batch.binding
    adapter.opening_snapshot = OpeningSnapshot(BINDING['account_id'], 169, 1, 1, ())
    adapter._title = lambda batch, expected: (BINDING['account_id'], dict(native_ok=True, title=True))
    queue = list(pages)
    adapter._after_title = lambda batch: (queue.pop(0), dict(native_ok=True, page='after'))
    adapter._wait = lambda batch, page: dict(native_ok=True, page=page)
    return adapter, queue


def snapshot():
    return OpeningSnapshot(BINDING['account_id'], 169, 1, 1, ())


def test_startup_closes_notice_then_announcement_before_accepting_home():
    batch = FakeBatch()
    adapter, _ = session(batch, ['notice', 'announcement', 'client_home'])
    inputs = []
    adapter._input = lambda batch, node: inputs.append(node) or dict(native_ok=True, node=node)
    facts = adapter._from_title(batch, snapshot(), 'title')
    assert facts.page == 'labyrinth_home' and facts.title_player_id == BINDING['account_id']
    assert batch.operations == ['client_title_start', 'client_notice_close',
                                'client_announcement_close', 'client_open_adventure',
                                'client_open_labyrinth']
    assert inputs == ['LabyClientTitleStartOnce', 'LabyClientNoticeCloseOnce',
                      'LabyClientAnnouncementCloseOnce', 'LabyClientHomeToAdventure',
                      'LabyClientOpenLabyrinthOnce']
    assert [row['session_page'] for row in batch.observations if 'session_page' in row] == [
        'title', 'notice', 'announcement', 'client_home', 'adventure', 'labyrinth_home']


def test_unsent_home_click_retries_once_after_closing_a_sampled_popup():
    batch = FakeBatch()
    adapter, _ = session(batch, ['client_home', 'announcement', 'client_home'])
    failed = [False]
    def input_node(batch, node):
        if node == 'LabyClientHomeToAdventure' and not failed[0]:
            failed[0] = True
            raise native.NativeInputNotSent(dict(input_execution='not_sent', action_started=False))
        return dict(native_ok=True, node=node)
    adapter._input = input_node
    facts = adapter._from_title(batch, snapshot(), 'title')
    assert facts.page == 'labyrinth_home'
    assert batch.operations == ['client_title_start', 'client_open_adventure',
                                'client_announcement_close', 'client_open_adventure',
                                'client_open_labyrinth']
    # The first HOME click produced no observation; only the retry settled it.
    assert [row['session_page'] for row in batch.observations if 'session_page' in row] == [
        'title', 'client_home', 'client_home', 'adventure', 'labyrinth_home']


def test_repeated_popups_stop_after_the_capped_closings():
    batch = FakeBatch()
    adapter, _ = session(batch, ['notice', 'announcement', 'notice'])
    adapter._input = lambda batch, node: dict(native_ok=True, node=node)
    with pytest.raises(BatchStopped, match='^game_session_readback_unknown$'):
        adapter._from_title(batch, snapshot(), 'title')
    assert batch.operations == ['client_title_start', 'client_notice_close',
                               'client_announcement_close']


@pytest.mark.private_replay
def test_saved_announcement_frame_completes_two_frame_startup_stability(monkeypatch):
    source = os.environ.get('PCR_LABYRINTH_ANNOUNCEMENT_FRAME')
    if not source: pytest.skip('private saved announcement frame is not distributed')
    import socket
    def deny(*args, **kwargs): raise AssertionError('saved-frame replay only')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    image = native.read_image(Path(source))
    resource = native.load_resource()
    controller = SavedImages([[image]])
    tasker = native.bind(resource, controller)
    class Checkpoint:
        stopped = staticmethod(lambda: False)
        def checkpoint(self): pass
    try:
        assert resource.override_pipeline(game_session_pipeline(BINDING['account_id']))
        adapter = MaaGameSession(tasker, lambda **fields: dict(**fields), timings=Timings())
        page, receipt = adapter._after_title(Checkpoint())
        assert page == 'announcement'
        stability = receipt['title_stability']
        assert stability['page'] == 'announcement' and stability['consecutive_frames'] == 2
        assert stability['minimum_interval_ms'] == 500
        assert not controller.clicks and not controller.swipes
    finally:
        native.stop_tasker(tasker)


@pytest.mark.private_replay
def test_saved_announcement_frame_is_detected_and_only_its_close_is_clicked(monkeypatch):
    source = os.environ.get('PCR_LABYRINTH_ANNOUNCEMENT_FRAME')
    if not source: pytest.skip('private saved announcement frame is not distributed')
    import socket
    def deny(*args, **kwargs): raise AssertionError('saved-frame replay only')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    image = native.read_image(Path(source))
    resource = native.load_resource()
    controller = SavedImages([[image]])
    tasker = native.bind(resource, controller)
    assert resource.override_pipeline(game_session_pipeline(BINDING['account_id']))
    try:
        title = native.run(tasker, 'LabyClientAnnouncementTitle')
        assert title['native_ok']
        words = next(node for node in title['nodes']
                     if node['name'] == 'LabyClientAnnouncementTitle')['recognition']['filtered']
        assert [word['text'] for word in words] == ['活动结束预告']
        assert native.run(tasker, 'LabyClientAnnouncementObserved')['native_ok']
        # The startup wait reports the popup instead of timing out.
        assert native.run(tasker, 'LabyGameSessionAfterTitleObserved')['native_ok']
        # The sampled title now vetoes HOME through the shared large-panel guard.
        assert native.run(tasker, 'HomeKnownLargePanel')['native_ok']
        assert not native.run(tasker, 'HomeNoKnownLargePanel')['native_ok']
        assert not native.run(tasker, 'LabyClientGameHomeObserved')['native_ok']
        assert not native.run(tasker, 'LabyClientHomeToAdventure')['native_ok']
        assert not controller.clicks
        assert native.run(tasker, 'LabyClientAnnouncementCloseOnce')['native_ok']
        assert len(controller.clicks) == 1 and not controller.swipes
        x, y = controller.clicks[0]
        assert 465 <= x <= 522 and 622 <= y <= 653, controller.clicks
    finally:
        native.stop_tasker(tasker)
