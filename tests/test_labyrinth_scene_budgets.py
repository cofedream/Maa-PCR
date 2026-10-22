"""Slow offline recognition uses production deadlines, without device input."""
from copy import deepcopy
from inspect import signature
from types import SimpleNamespace as NS

import numpy as np
import pytest

from pcr_maa import native
from pcr_maa.labyrinth import autoplay, scene, shop_execution as shops
from pcr_maa.labyrinth.timing import Timings
from test_labyrinth_recognition_perf_r50 import SceneContext
from test_labyrinth_reward_shop_upstream import harness
from test_native_stop_bounds import Clock, Job, Tasker


class Resource:
    def __init__(self):
        self.nodes = {scene.NODE: dict(recognition=dict(param={}))}

    def get_node_data(self, name):
        return deepcopy(self.nodes[name])

    def override_pipeline(self, nodes):
        self.nodes.update(deepcopy(nodes))
        return True


class SlowOwner(Tasker):
    def __init__(self, clock, *, seconds=12.096, kind='shop', confirmation=False):
        super().__init__()
        self.clock, self.seconds, self.kind = clock, seconds, kind
        self.confirmation = confirmation
        self.resource = self._resource_holder = Resource()
        self.observations, self.inputs = [], []

    def post_task(self, entry):
        clock = self.clock
        if entry == scene.NODE:
            params = self.resource.get_node_data(entry)['recognition']['param']
            scope = params.get('custom_recognition_param', {}).get('probe_kinds')
            self.observations.append((native.RUN_DEADLINE.get() - clock.now, scope))
            elapsed = self.seconds if scope is None or set(scene._RULES) <= set(scope) else min(self.seconds, 3.2)
            key = 'SHOP_CLOSE' if self.kind == 'shop' else 'SHOP_EXIT_CONFIRM_BUTTON'
            payload = dict(area=3, controls={key: dict(anchor=key,
                box=[100, 100, 40, 30], score=.99)})
            if scope is not None:
                payload['probe_kinds'] = scope
            result = dict(native_ok=True, tasker_inited=True, task_id=41,
                nodes=[dict(name=entry, completed=True, action=None,
                    recognition=dict(kind=self.kind, payload=payload))])
        else:
            assert entry == 'LabySceneActionOnce'
            guard = self.resource.nodes['LabySceneActionGuard']['custom_recognition_param']
            self.inputs.append(guard)
            self.kind = ('shop_exit_confirmation' if self.confirmation else 'map') if self.kind == 'shop' else 'map'
            elapsed = .2
            result = dict(native_ok=True, tasker_inited=True, task_id=41,
                nodes=[dict(name=entry, completed=True, action=dict(success=True))])
        due = clock.now + elapsed

        class Delayed(Job):
            @property
            def done(self):
                return clock.now >= due

        self.job = Delayed(done=False, detail=result)
        return super().post_task(entry)


@pytest.fixture
def slow(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(native, 'time', clock)
    monkeypatch.setattr(scene, 'time', clock)
    monkeypatch.setattr(native, '_STOP_SLEEP', clock.sleep)
    monkeypatch.setattr(native, '_BLOCKED_TASKERS', [])
    monkeypatch.setattr(autoplay, '_poll_pause', lambda *args: None)

    def receipt(detail, *, check=None, **kwargs):
        if check is not None:
            check()
        return detail

    monkeypatch.setattr(native, '_receipt', receipt)
    token = native.RUN_DEADLINE.set(float('inf'))
    yield clock
    native.RUN_DEADLINE.reset(token)


def runtime(owner):
    value = scene.MaaSceneRuntime.__new__(scene.MaaSceneRuntime)
    value.tasker, value.stopped, value.timings = owner, None, Timings()
    value.observer = NS()
    return value


def test_default_budget_matches_pipeline_and_offline_runtime_helpers():
    from test_labyrinth_autoplay import Runtime
    from test_labyrinth_upstream_scene_reuse import Runtime as UpstreamRuntime
    budget = signature(scene.observe_scene).parameters['timeout'].default
    assert budget == 20
    assert signature(scene.MaaSceneRuntime.observe).parameters['timeout'].default == budget
    assert scene.scene_pipeline()[scene.NODE]['timeout'] == budget * 1000
    for helper in (Runtime, UpstreamRuntime):
        assert signature(helper.observe).parameters['timeout'].default == budget


@pytest.mark.parametrize('seconds', [12.096, 14.4, 19.8])
def test_slow_full_recognition_completes_under_production_default(slow, seconds):
    owner = SlowOwner(slow, seconds=seconds)
    assert runtime(owner).observe().kind == 'shop'
    assert owner.observations[0][0] == 20
    assert owner.inputs == [] and owner.stops == 0
    assert native.RUN_DEADLINE.get() == float('inf')


@pytest.mark.parametrize('timeout', [None, 3, 10, 12, 15, 30])
def test_recognition_over_actual_budget_stops_without_retry(slow, timeout):
    budget = 20 if timeout is None else timeout
    owner = SlowOwner(slow, seconds=budget + 1)
    with pytest.raises(RuntimeError, match='inspection deadline'):
        runtime(owner).observe(**({} if timeout is None else dict(timeout=timeout)))
    assert budget <= slow.now < budget + .1
    assert owner.posts == [scene.NODE] and owner.stops == 1 and owner.inputs == []
    assert native.RUN_DEADLINE.get() == float('inf')


@pytest.mark.parametrize('parent', [3, 12, 15])
def test_default_observation_never_extends_parent_deadline(slow, parent):
    owner = SlowOwner(slow, seconds=parent + 1)
    token = native.RUN_DEADLINE.set(parent)
    try:
        with pytest.raises(RuntimeError, match='inspection deadline'):
            runtime(owner).observe()
        assert owner.observations == [(parent, None)]
        assert native.RUN_DEADLINE.get() == parent
        assert owner.inputs == [] and owner.stops == 1
    finally:
        native.RUN_DEADLINE.reset(token)


def test_transition_polls_share_one_deadline_and_restore_scope(slow):
    owner = SlowOwner(slow, seconds=3.2, kind='unknown')
    view = runtime(owner)
    original = owner.resource.get_node_data(scene.NODE)
    with pytest.raises(scene.BatchStopped, match='^slow_transition$'):
        view.wait_for(NS(checkpoint=lambda: None), {'map'}, timeout=10, reason='slow_transition')
    budgets = [remaining for remaining, _ in owner.observations]
    assert len(budgets) >= 3 and budgets[0] == 10
    assert all(before > after for before, after in zip(budgets, budgets[1:]))
    assert 10 <= slow.now < 10.1
    assert owner.inputs == [] and owner.stops == 1
    assert owner.resource.get_node_data(scene.NODE) == original
    assert view._scene_probe_kinds is None and native.RUN_DEADLINE.get() == float('inf')


@pytest.mark.parametrize('stopped', [False, True])
def test_transition_checks_time_and_stop_after_acceptance(slow, stopped):
    owner = SlowOwner(slow, kind='map')
    stop = [False]

    def checkpoint():
        if stop[0]:
            raise scene.BatchStopped('stopped')

    def accept(value):
        slow.now = 11
        stop[0] = stopped
        return True

    with pytest.raises(scene.BatchStopped, match='^stopped$' if stopped else '^late_transition$'):
        runtime(owner).wait_for(NS(checkpoint=checkpoint), {'map'}, timeout=10,
            accept=accept, reason='late_transition')
    assert owner.inputs == [] and native.RUN_DEADLINE.get() == float('inf')


@pytest.mark.parametrize('stopped', [False, True])
def test_character_feedback_predicate_shares_deadline_and_rejects_late_result(slow, monkeypatch, stopped):
    import time
    from pcr_maa.labyrinth.character_recognition import MaaCharacters
    monkeypatch.setattr(time, 'monotonic', slow.monotonic)
    characters = MaaCharacters.__new__(MaaCharacters)
    characters.read = lambda *args, **kwargs: (dict(stable=True), {})
    seen = []
    stop = [False]

    def checkpoint():
        if stop[0]:
            raise scene.BatchStopped('stopped')

    def predicate(value):
        seen.append(native.RUN_DEADLINE.get())
        slow.now = 13
        stop[0] = stopped
        return True

    with pytest.raises(scene.BatchStopped, match='^stopped$' if stopped else '^character_feedback_unknown$'):
        characters.wait_for(NS(checkpoint=checkpoint), 'battle', predicate)
    assert seen == [12] and native.RUN_DEADLINE.get() == float('inf')


@pytest.mark.parametrize('stopped', [False, True])
def test_encounter_rejects_capture_after_deadline_or_stop(slow, monkeypatch, stopped):
    from pcr_maa.labyrinth import encounters
    monkeypatch.setattr(encounters, 'time', slow)
    value = encounters.MaaEncounter.__new__(encounters.MaaEncounter)
    stop = [False]

    def checkpoint():
        if stop[0]:
            raise scene.BatchStopped('stopped')

    def capture(*args, **kwargs):
        assert native.RUN_DEADLINE.get() == 3
        slow.now = 4
        stop[0] = stopped
        return dict(page='challenge'), {}

    value._run = capture
    with pytest.raises(scene.BatchStopped, match='^stopped$' if stopped else '^encounter_readback_unknown$'):
        value._read(NS(checkpoint=checkpoint), 'challenge', 3)
    assert native.RUN_DEADLINE.get() == float('inf')


@pytest.mark.parametrize('parent', [20, float('inf')])
@pytest.mark.parametrize('on_time', [False, True])
def test_startup_download_retry_never_outlives_business_deadline(slow, monkeypatch, parent, on_time):
    from pcr_maa.labyrinth import game_session
    monkeypatch.setattr(game_session, 'time', slow)
    adapter = game_session.MaaGameSession.__new__(game_session.MaaGameSession)
    adapter.tasker = NS(resource=NS(override_pipeline=lambda nodes: True))
    adapter.timings, adapter.opening_snapshot = Timings(), NS()
    adapter._checkpoint = lambda batch: batch.checkpoint()
    adapter._fields = lambda *args, **kwargs: kwargs
    adapter.stamp = lambda **kwargs: kwargs
    inputs = []
    adapter._input = lambda *args: inputs.append('retry') or {}
    names = ['LabyDateChangedObserved', 'LabyDisconnect6002Observed',
        'LabyClientNoticeObserved', 'LabyClientAfterTitleHomeObserved', 'LabyClientDownloadErrorObserved']
    captures = []

    def capture(*args):
        assert native.RUN_DEADLINE.get() == min(parent, 85)
        slow.now = min(parent, 75) + (-1 if on_time and not captures else 1)
        captures.append(1)
        return dict(recognition=[dict(name=name, algorithm='And',
            box=[1, 1, 20, 20] if name == names[-1] else None) for name in names])

    monkeypatch.setattr(native, 'run', capture)
    monkeypatch.setattr(game_session, '_root', lambda value, entry: value)
    batch = NS(checkpoint=lambda: None, transaction=lambda op, fields, send: send('offline'))
    token = native.RUN_DEADLINE.set(parent)
    try:
        with pytest.raises(scene.BatchStopped, match='^game_session_readback_unknown$'):
            adapter._after_title(batch)
        assert inputs == (['retry'] if on_time else [])
        assert native.RUN_DEADLINE.get() == parent
    finally:
        native.RUN_DEADLINE.reset(token)


@pytest.mark.parametrize('confirmation', [False, True])
def test_slow_shop_exit_scopes_both_observations_without_replaying_purchases(slow, confirmation):
    owner = SlowOwner(slow, confirmation=confirmation)
    batch, stamp = harness()
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.tasker, shop.stamp, shop.scene = owner, stamp, runtime(owner)
    shop.observer, shop.blocked = NS(), False
    shop._capture = lambda: (dict(state='shop', items=[], balance=390), {})
    result = shop.run(batch, 309, 30601, buy_relics=False)
    assert result['page'] == 'map' and result['purchases'] == []
    assert len(owner.inputs) == 1 + confirmation
    expected = scene._probe_scope(shops._EXIT_PAGES)
    assert owner.observations[0] == (20, expected)
    if confirmation:
        assert owner.observations[2] == (20, expected)
    assert all(scope is not None for _, scope in owner.observations)
    assert all(row['operation'] in ('shop_leave', 'shop_leave_confirm') for row in batch.journal.events())
    assert owner.stops == 0


@pytest.mark.parametrize('kind', ['shop_confirmation', 'shop_complete', 'joined', 'free_role_reward', 'item_reward'])
def test_shop_exit_refuses_pending_modal_without_input(slow, kind):
    owner = SlowOwner(slow, kind=kind)
    batch, stamp = harness()
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.tasker, shop.stamp, shop.scene = owner, stamp, runtime(owner)
    shop.observer, shop.blocked = NS(), False
    shop._capture = lambda: (dict(state='shop', items=[], balance=390), {})
    with pytest.raises(scene.BatchStopped, match='native_input_not_sent'):
        shop.run(batch, 309, 30601, buy_relics=False)
    assert shop.blocked and owner.inputs == []


@pytest.mark.parametrize('kind', sorted(scene._RULES) + ['date_changed', 'disconnected'])
def test_shop_scope_retains_modal_and_interruption_classification(kind):
    keys = set(scene._RULES['shop'][0])
    keys.update(scene._RULES[kind][0] if kind in scene._RULES else
        ({'DATE'} if kind == 'date_changed' else {'DISCONNECT'}))
    image = np.zeros((720, 1280, 3), np.uint8)
    full = SceneContext(keys)
    expected = full.observer.analyze(full, NS(image=image, custom_recognition_param={})).detail
    scoped = SceneContext(keys)
    actual = scoped.observer.analyze(scoped, NS(image=image,
        custom_recognition_param=dict(probe_kinds=shops._EXIT_PAGES))).detail
    assert actual['kind'] == expected['kind']
    assert actual['payload']['same_frame'] and not actual['payload']['clear_verified']
    assert 'LabySceneWord_DATE' in scoped.calls and 'LabySceneWord_DISCONNECT' in scoped.calls
    assert len(scoped.calls) < len(full.calls)


def test_native_delayed_callback_uses_actual_default_budget(tmp_path, monkeypatch):
    import time
    from pcr_maa.replay import SavedImages

    class DelayedRecognition(scene.SceneRecognition):
        def analyze(self, context, argv):
            time.sleep(12.096)
            return self.AnalyzeResult([0, 0, 1280, 720], dict(kind='shop', payload={}))

    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    observer = DelayedRecognition()
    resource = native.load_resource()
    assert resource.override_pipeline(scene.scene_pipeline())
    assert resource.register_custom_recognition(scene.NAME, observer)
    resource._labyrinth_scene_observer = observer
    controller = SavedImages([[np.zeros((720, 1280, 3), np.uint8)]])
    owner = native.bind(resource, controller)
    try:
        started = time.monotonic()
        assert scene.MaaSceneRuntime(owner).observe().kind == 'shop'
        assert time.monotonic() - started >= 12.096
        assert controller.clicks == [] and controller.swipes == []
    finally:
        assert native.wait_job(owner.post_stop(), seconds=10).succeeded
