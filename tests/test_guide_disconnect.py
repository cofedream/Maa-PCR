from types import SimpleNamespace as NS
from pathlib import Path
import json
import pytest
from pcr_maa import native
from pcr_maa.guide_trial_native import NativeGuideTrial
from pcr_maa.labyrinth.profile import PROFILE_NODE


DISCONNECT = 'LabyDisconnect6002Observed'


@pytest.mark.parametrize('names', [[PROFILE_NODE], ['GTFireMap'], ['GTPaused'],
    ['GTWinObserved', 'GTDefeat', DISCONNECT, 'LabyClientTitleObserved']])
def test_every_wait_stops_on_6002_before_reporting_a_page(tmp_path, monkeypatch, names):
    pipeline, calls = {}, []
    tasker = NS(resource=NS(override_pipeline=lambda nodes: pipeline.update(nodes)), controller=NS(cached_image=None))
    port = NativeGuideTrial(tasker, tmp_path)
    port.checkpoint = lambda: None
    clock = iter([0, 0, 2])
    monkeypatch.setattr('pcr_maa.guide_trial_native.time.monotonic', lambda: next(clock))
    monkeypatch.setattr('pcr_maa.guide_trial_native.time.sleep', lambda _: None)
    def run(tasker, name, *, reset_hits=()):
        calls.append((name, reset_hits))
        hit = DISCONNECT if DISCONNECT in pipeline['GTChoose']['next'] else 'GTUnknown'
        return dict(native_ok=True, nodes=[dict(name=name), dict(name=hit)])
    monkeypatch.setattr(native, 'run', run)
    original = names.copy()
    with pytest.raises(RuntimeError, match='^guide_trial_disconnected_6002:battle_start=not_sent$'):
        port.wait(names, seconds=1)
    assert names == original and pipeline['GTChoose']['next'] == [DISCONNECT, *(name for name in names if name != DISCONNECT), 'GTUnknown']
    assert calls == [('GTChoose', ())]


@pytest.mark.parametrize('state', ['not_sent', 'sent', 'unknown', 'previously_sent'])
def test_direct_choice_also_reports_disconnect_and_start_fact(tmp_path, state):
    nodes = {}
    port = NativeGuideTrial(NS(resource=NS(override_pipeline=lambda update: nodes.update(update))), tmp_path)
    port.battle_start = state
    calls = []
    port.run = lambda name: calls.append(name) or dict(nodes=[dict(name=DISCONNECT)])
    with pytest.raises(RuntimeError, match='^guide_trial_disconnected_6002:battle_start=' + state + '$'):
        port.choose(['GTFireMap'])
    assert calls == ['GTChoose'] and nodes['GTChoose']['next'][0] == DISCONNECT


@pytest.mark.parametrize('fault,expected', [('success', 'sent'), ('post_click_failure', 'sent'),
    ('unproven_click', 'unknown'), ('dispatch_error', 'unknown'), ('proven_not_sent', 'not_sent')])
def test_start_status_uses_native_input_proof(tmp_path, monkeypatch, fault, expected):
    port = NativeGuideTrial(NS(controller=NS(cached_image=None)), tmp_path)
    port.checkpoint = lambda: None
    def run(*args, **kwargs):
        if fault == 'dispatch_error':
            raise RuntimeError('synthetic_native_dispatch_error')
        if fault == 'proven_not_sent':
            raise native.NativeInputNotSent(dict(input_execution='not_sent'))
        completed = fault != 'unproven_click'
        return dict(native_ok=fault != 'post_click_failure', tasker_inited=True,
            nodes=[dict(name='GTBattleStart', completed=completed,
                action=dict(name='GTBattleStart', success=completed))])
    monkeypatch.setattr(native, 'run', run)
    if fault == 'success':
        port.run('GTBattleStart', click=True)
    else:
        with pytest.raises(RuntimeError):
            port.run('GTBattleStart', click=True)
    assert port.battle_start == expected


def test_rejected_start_receipt_keeps_native_not_sent_proof(tmp_path, monkeypatch):
    port = NativeGuideTrial(NS(controller=NS(cached_image=None)), tmp_path)
    port.checkpoint = lambda: None
    receipt = dict(native_ok=False, tasker_inited=True, task_id=1, nodes=[],
        native_evidence=dict(source='native_task_events', complete=True, task_id=1,
            started=True, finished=True, recognition_failed=True, action_started=False))
    monkeypatch.setattr(native, 'run', lambda *args, **kwargs: receipt)
    with pytest.raises(native.NativeInputNotSent):
        port.run('GTBattleStart', click=True)
    assert port.battle_start == 'not_sent' and receipt['input_execution'] == 'not_sent'


@pytest.mark.parametrize('method,args', [('prepare_resume', ({}, {})),
    ('finish_completed_return', ({},)), ('recover_result', ({}, {})), ('recover_control_exit', ({}, {}))])
def test_existing_battle_entries_do_not_claim_a_new_start(tmp_path, method, args):
    port = NativeGuideTrial(None, tmp_path)
    def stop(_):
        raise RuntimeError('synthetic_stop_before_native')
    port.configure = stop
    with pytest.raises(RuntimeError, match='synthetic_stop_before_native'):
        getattr(port, method)(*args)
    assert port.battle_start == 'previously_sent'


def test_unknown_wait_still_times_out_without_input(tmp_path, monkeypatch):
    port = NativeGuideTrial(None, tmp_path)
    clock = iter([0, 0, 2])
    monkeypatch.setattr('pcr_maa.guide_trial_native.time.monotonic', lambda: next(clock))
    monkeypatch.setattr('pcr_maa.guide_trial_native.time.sleep', lambda _: None)
    port.choose = lambda _: 'GTUnknown'
    with pytest.raises(RuntimeError, match='^guide_trial_page_timeout$'):
        port.wait(['GTFireMap'], seconds=1)
    assert port.battle_start == 'not_sent'


def test_stop_is_checked_before_disconnect_poll_dispatch(tmp_path, monkeypatch):
    port = NativeGuideTrial(NS(resource=NS(override_pipeline=lambda _: None)), tmp_path)
    def stop():
        raise RuntimeError('stopped')
    port.checkpoint = stop
    monkeypatch.setattr(native, 'run', lambda *args, **kwargs: pytest.fail('native dispatch after Stop'))
    with pytest.raises(RuntimeError, match='^stopped$'):
        port.wait([PROFILE_NODE], seconds=1)
    assert port.battle_start == 'not_sent'


def test_prepare_map_disconnect_does_not_return_title_or_login(tmp_path):
    nodes = {}
    port = NativeGuideTrial(NS(resource=NS(override_pipeline=lambda update: nodes.update(update))), tmp_path)
    port.configure = lambda _: None
    calls = []
    port.run = lambda name, **kwargs: calls.append(name) or dict(nodes=[dict(name=DISCONNECT)])
    with pytest.raises(RuntimeError, match='guide_trial_disconnected_6002:battle_start=not_sent'):
        port.prepare_map(dict(account_id='123456789'))
    assert calls == ['GTChoose']


def test_disconnect_reuses_original_five_part_recognition():
    from pcr_maa.labyrinth.game_session import game_session_pipeline
    from pcr_maa.labyrinth.session import reconnect_pipeline
    original = reconnect_pipeline()
    pipeline = game_session_pipeline(123456789)
    for node in [DISCONNECT, *original[DISCONNECT]['all_of']]:
        assert pipeline[node] == original[node] and pipeline[node]['action'] == 'DoNothing'


@pytest.mark.private_replay
def test_original_fire86_profile_6002_frame_stops_without_input(tmp_path, monkeypatch):
    from pcr_maa.labyrinth.game_session import game_session_pipeline
    from pcr_maa.replay import SavedImages
    source = Path(__file__).resolve().parents[1] / (
        '.runtime/guide-acquisition-20261018/candidate/player/.runtime/supported-daily-work/'
        'run-183164dedfac49c2983df0a555474b46/020-GTChoose.png')
    if not source.is_file():
        pytest.skip('private original 6002 frame excluded from public source')
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    controller = SavedImages([[native.read_image(source)]])
    resource = native.load_resource()
    assert resource.override_pipeline(game_session_pipeline(123456789))
    tasker = native.bind(resource, controller)
    port = NativeGuideTrial(tasker, tmp_path)
    try:
        with pytest.raises(RuntimeError, match='^guide_trial_disconnected_6002:battle_start=not_sent$'):
            port.wait([PROFILE_NODE], seconds=15)
        receipt = json.loads((tmp_path / '001-GTChoose.json').read_text(encoding='utf-8'))
        assert receipt['native_ok'] and receipt['nodes'][-1]['name'] == DISCONNECT
        assert not controller.clicks and not controller.swipes
    finally:
        assert native.wait_job(tasker.post_stop(), seconds=10).succeeded
