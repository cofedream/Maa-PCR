"""Offline confirmation timing, including successful results returned too late."""
from types import SimpleNamespace as NS

import pytest

from pcr_maa import native
from pcr_maa.labyrinth import autoplay, character_recognition as cr, opening, scene
from test_labyrinth_joined_confirmation import callback, receipt
from test_labyrinth_scene_budgets import SlowOwner, runtime, slow


@pytest.fixture
def confirmation(slow, monkeypatch):
    import time
    monkeypatch.setattr(time, 'monotonic', slow.monotonic)
    result = callback(monkeypatch)
    evidence = receipt(result.detail)
    state = NS(scene_seconds=1.852, read_seconds=1.198, stop=False, stop_on_read=False,
        budgets=[], inputs=[])

    def checkpoint():
        if state.stop:
            raise scene.BatchStopped('stopped')

    def observe(**kwargs):
        state.budgets.append(native.RUN_DEADLINE.get())
        slow.now += state.scene_seconds
        return scene.Scene('joined', evidence, {})

    view = runtime(NS())
    view.observe = observe
    view.perform = lambda *args, **kwargs: state.inputs.append(args)
    monkeypatch.setattr(scene, 'MaaSceneRuntime', lambda *args, **kwargs: view)
    characters = cr.MaaCharacters.__new__(cr.MaaCharacters)

    def read(suffix, params):
        assert suffix == 'Read' and params['page'] == 'joined'
        state.budgets.append(native.RUN_DEADLINE.get())
        slow.now += state.read_seconds
        state.stop = state.stop_on_read
        return result.detail, evidence

    characters.run = read
    state.port = opening.MaaOpening(NS(), lambda **kwargs: kwargs, characters=characters)
    state.batch = NS(checkpoint=checkpoint, stopped=lambda: state.stop,
        observation=lambda value, **kwargs: value)
    state.evidence = evidence
    return state


@pytest.mark.parametrize('scene_seconds,read_seconds', [(1.852, 1.198), (1.799, 1.260), (3.2, 5)])
def test_opening_accepts_slow_confirmed_identities(confirmation, slow, scene_seconds, read_seconds):
    value = confirmation
    value.scene_seconds, value.read_seconds = scene_seconds, read_seconds
    assert value.port._wait_invitation_joined(value.batch, 1, dict(operation_id='offline')) == value.evidence
    assert slow.now > 3 and value.budgets == [12, 12]
    assert value.inputs == [] and native.RUN_DEADLINE.get() == float('inf')


@pytest.mark.parametrize('read_seconds,stopped', [(12.1, False), (1.198, True), (12.1, True)])
def test_opening_rejects_late_result_and_stop_without_input(confirmation, read_seconds, stopped):
    value = confirmation
    value.read_seconds, value.stop_on_read = read_seconds, stopped
    with pytest.raises(scene.BatchStopped, match='^stopped$' if stopped else '^character_readback_unknown$'):
        value.port._wait_invitation_joined(value.batch, 1, dict(operation_id='offline'))
    assert value.inputs == [] and value.budgets == [12, 12]
    assert native.RUN_DEADLINE.get() == float('inf')


@pytest.mark.parametrize('parent', [.5, 2.5])
def test_opening_never_extends_parent_deadline(confirmation, parent):
    value = confirmation
    token = native.RUN_DEADLINE.set(parent)
    try:
        reason = 'opening_readback_unknown' if parent < value.scene_seconds else 'character_readback_unknown'
        with pytest.raises(scene.BatchStopped, match='^' + reason + '$'):
            value.port._wait_invitation_joined(value.batch, 1, dict(operation_id='offline'))
        assert value.budgets == [parent] * (1 if parent < value.scene_seconds else 2)
        assert value.inputs == [] and native.RUN_DEADLINE.get() == parent
    finally:
        native.RUN_DEADLINE.reset(token)


@pytest.mark.parametrize('parent', [2, float('inf')])
def test_opening_other_joined_read_uses_normal_identity_budget(confirmation, parent):
    value = confirmation
    value.read_seconds = 3.1
    value.port._install_joined = lambda: None
    token = native.RUN_DEADLINE.set(parent)
    try:
        if parent == 2:
            with pytest.raises(scene.BatchStopped, match='^character_readback_unknown$'):
                value.port.observe_joined(value.batch, 1)
        else:
            assert value.port.observe_joined(value.batch, 1)['layout'] == 'joined3'
        assert value.budgets == [min(parent, 12)] and value.inputs == []
        assert native.RUN_DEADLINE.get() == parent
    finally:
        native.RUN_DEADLINE.reset(token)


@pytest.mark.parametrize('seconds', [12.096, 19.8])
def test_runner_observation_uses_full_scene_budget(slow, seconds):
    owner = SlowOwner(slow, seconds=seconds, kind='map')
    runner = autoplay.AutoplayRunner.__new__(autoplay.AutoplayRunner)
    runner.runtime, runner.stamp = runtime(owner), lambda **kwargs: kwargs
    batch = NS(checkpoint=lambda: None, observation=lambda *args, **kwargs: None)
    assert runner._observe(batch, NS(enter_id=1)).kind == 'map'
    assert owner.observations == [(20, scene._probe_scope(set(scene._RULES)))]
    assert owner.inputs == [] and owner.stops == 0
    assert native.RUN_DEADLINE.get() == float('inf')


def test_native_delayed_opening_confirmation_has_zero_inputs(tmp_path, monkeypatch):
    import time
    import numpy as np
    from pcr_maa.replay import SavedImages
    result = callback(monkeypatch)

    def read_scene(self, context, argv):
        time.sleep(1.852)
        return self.AnalyzeResult([0, 0, 1280, 720], dict(kind='joined', payload={}))

    def read_characters(self, context, argv):
        time.sleep(1.198)
        return result

    monkeypatch.setattr(scene.SceneRecognition, 'analyze', read_scene)
    monkeypatch.setattr(cr.CharacterRecognition, 'analyze', read_characters)
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    resource = native.load_resource()
    controller = SavedImages([[np.zeros((720, 1280, 3), np.uint8)]])
    owner = native.bind(resource, controller)
    characters = cr.MaaCharacters(owner)
    port = opening.MaaOpening(owner, lambda **kwargs: kwargs, characters=characters)
    try:
        batch = NS(checkpoint=lambda: None, stopped=lambda: False)
        started = time.monotonic()
        actual = port._wait_invitation_joined(batch, 1, dict(operation_id='offline'))
        from pcr_maa.labyrinth.portrait_cache import joined_portrait_readback
        _, people = joined_portrait_readback(actual)
        assert [p.character_id for p in people] == ['1025', '1308', '1145']
        assert time.monotonic() - started >= 3.05
        assert controller.clicks == [] and controller.swipes == []
    finally:
        assert native.wait_job(owner.post_stop(), seconds=10).succeeded
