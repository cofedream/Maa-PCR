"""Offline native stop and input-classification regressions; no controller."""
from types import SimpleNamespace

import pytest

from pcr_maa import native
from pcr_maa.context_backend import ContextTasker


class Clock:
    def __init__(self):
        self.now = 0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class Job:
    job_id = 41
    def __init__(self, *, done=True, succeeded=True, detail=None, failure=None):
        self._done, self.succeeded, self.detail, self.failure = done, succeeded, detail, failure
    @property
    def done(self):
        if self.failure:
            failure, self.failure = self.failure, None
            raise failure
        return self._done
    def wait(self):
        pytest.fail('unbounded SDK wait called')
    def get(self):
        return self.detail


class Tasker:
    inited = True
    stopping = False
    def __init__(self, *, job=None, stop_job=None, events=()):
        self.running = False
        self.job = job or Job(detail=SimpleNamespace(nodes=[]))
        self.stop_job = stop_job or Job()
        self.events = events
        self.posts, self.stops, self.task_sinks, self.context_sinks = [], 0, {}, {}
    def post_task(self, entry):
        self.posts.append(entry)
        self.running = not self.job._done
        for msg, detail in self.events:
            sinks = self.task_sinks if msg.startswith('Tasker.') else self.context_sinks
            for sink in list(sinks.values()):
                sink._on_raw_notification(None, msg, detail)
        return self.job
    def post_stop(self):
        self.stops += 1
        if self.stop_job._done and self.stop_job.succeeded:
            self.running = False
        return self.stop_job
    def add_sink(self, sink):
        self.task_sinks[1] = sink
        return 1
    def remove_sink(self, sid):
        del self.task_sinks[sid]
    def add_context_sink(self, sink):
        self.context_sinks[2] = sink
        return 2
    def remove_context_sink(self, sid):
        del self.context_sinks[sid]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, request):
    # Preserve the process's real quarantine; fake owners live only in this test.
    monkeypatch.setattr(native, '_BLOCKED_TASKERS', [])
    clock = Clock()
    if not request.node.name.startswith('test_sdk_'):
        monkeypatch.setattr(native.time, 'monotonic', clock.monotonic)
        monkeypatch.setattr(native.time, 'sleep', clock.sleep)
        monkeypatch.setattr(native, '_STOP_SLEEP', clock.sleep)
    token = native.RUN_DEADLINE.set(float('inf'))
    yield clock
    native.RUN_DEADLINE.reset(token)


def test_native_timeout_stop_never_finishes_is_bounded_and_not_reused(isolated):
    tasker = Tasker(job=Job(done=False), stop_job=Job(done=False))
    token = native.RUN_DEADLINE.set(.1)
    try:
        with pytest.raises(RuntimeError, match='Native task exceeded') as caught:
            native.run(tasker, 'Consumer')
    finally:
        native.RUN_DEADLINE.reset(token)
    assert .1 <= isolated.now <= 5.2
    assert caught.value.native_stop['stop_completed'] is False
    assert 'TimeoutError' in caught.value.native_stop['error']
    assert native.tasker_blocked(tasker)
    with pytest.raises(RuntimeError, match='blocked'):
        native.run(tasker, 'Consumer')
    assert native.stop_tasker(tasker) == caught.value.native_stop
    assert tasker.posts == ['Consumer'] and tasker.stops == 1
    assert not tasker.task_sinks and not tasker.context_sinks
    with ContextTasker(SimpleNamespace(tasker=tasker)) as facade:
        with pytest.raises(RuntimeError, match='blocked'):
            native.run(facade, 'Consumer')
    assert tasker.posts == ['Consumer']


def test_quarantined_owner_blocks_live_handle_proxy(isolated):
    tasker = Tasker(job=Job(done=False), stop_job=Job(done=False))
    tasker._handle = 81234
    native.stop_tasker(tasker, seconds=.1)
    proxy = SimpleNamespace(_handle=81234, inited=True, running=True, stopping=False)
    assert native.tasker_blocked(proxy)
    assert any(owner is tasker for owner in native._BLOCKED_TASKERS)
    with ContextTasker(SimpleNamespace(tasker=proxy,
            run_task=lambda _: pytest.fail('quarantined Context reused'))) as facade:
        with pytest.raises(RuntimeError, match='blocked'):
            native.run(facade, 'Consumer')


@pytest.mark.parametrize('stop_failure', [None, RuntimeError('stop status broken'), KeyboardInterrupt('stop interrupted')])
def test_keyboard_interrupt_preserved_with_stop_evidence(stop_failure):
    interrupt = KeyboardInterrupt('primary interruption')
    tasker = Tasker(job=Job(done=False, failure=interrupt),
                    stop_job=Job(done=stop_failure is None, failure=stop_failure))
    with pytest.raises(KeyboardInterrupt) as caught:
        native.run(tasker, 'Consumer')
    assert caught.value is interrupt
    assert tasker.posts == ['Consumer'] and tasker.stops == 1
    assert caught.value.native_stop['idle'] is (stop_failure is None)
    assert native.tasker_blocked(tasker) is (stop_failure is not None)
    if stop_failure:
        assert type(stop_failure).__name__ in caught.value.native_stop['error']


def test_normal_completion_never_posts_stop():
    tasker = Tasker()
    receipt = native.run(tasker, 'Read')
    assert receipt['native_ok'] and tasker.posts == ['Read'] and tasker.stops == 0
    assert not native.tasker_blocked(tasker)


def test_stop_success_requires_idle_and_failed_stop_quarantines():
    tasker = Tasker(stop_job=Job(succeeded=False))
    tasker.running = True
    evidence = native.stop_tasker(tasker)
    assert evidence['stop_completed'] and not evidence['idle'] and native.tasker_blocked(tasker)
    assert tasker.stops == 1


def test_stop_post_exception_keeps_original_error(monkeypatch):
    tasker = Tasker(job=Job(done=False, failure=ValueError('primary job failure')))
    def broken_stop():
        tasker.stops += 1
        raise RuntimeError('stop request failed')
    monkeypatch.setattr(tasker, 'post_stop', broken_stop)
    with pytest.raises(ValueError, match='primary job failure') as caught:
        native.run(tasker, 'Consumer')
    assert 'stop request failed' in caught.value.native_stop['error']
    assert native.tasker_blocked(tasker) and tasker.stops == 1


def test_context_stop_only_posts_once_and_blocks():
    owner = Tasker(stop_job=Job(done=False))
    with ContextTasker(SimpleNamespace(tasker=owner)) as facade:
        evidence = native.stop_tasker(facade)
        assert evidence == dict(requested=True, stop_completed=False, idle=False)
        native.stop_tasker(facade)
        assert owner.stops == 1
        with pytest.raises(RuntimeError, match='blocked'):
            native.run(facade, 'Consumer')


def test_pre_dispatch_deadline_proves_not_sent():
    tasker = Tasker()
    token = native.RUN_DEADLINE.set(0)
    try:
        with pytest.raises(RuntimeError, match='before task input') as caught:
            native.run(tasker, 'Consumer')
    finally:
        native.RUN_DEADLINE.reset(token)
    assert not isinstance(caught.value, native.NativeInputNotSent)
    assert caught.value.receipt['input_execution'] == 'not_sent'
    assert caught.value.receipt['native_evidence']['source'] == 'pre_dispatch'
    assert not tasker.posts and not tasker.stops


def events(*, action=None, finish=True, unpaired=False):
    yield 'Tasker.Task.Starting', dict(task_id=41, entry='Consumer')
    if not unpaired:
        yield 'Node.Recognition.Starting', dict(task_id=41, reco_id=3, name='Guard')
    yield 'Node.Recognition.Failed', dict(task_id=41, reco_id=3, name='Guard')
    if action:
        name, kind = action
        yield 'Node.Action.Starting', dict(task_id=41, action_id=4, name=name)
        yield 'Node.Action.Succeeded', dict(task_id=41, action_id=4, name=name,
            action_details=dict(action_id=4, name=name, action=kind, success=True))
    if finish:
        yield 'Tasker.Task.Succeeded', dict(task_id=41, entry='Consumer')


def receipt_for(*, action=None, finish=True, unpaired=False):
    nodes = []
    if action:
        name, kind = action
        nodes.append(SimpleNamespace(name=name, completed=True, recognition=None,
            action=SimpleNamespace(name=name, success=True, raw_detail=dict(action=kind))))
    tasker = Tasker(job=Job(detail=SimpleNamespace(nodes=nodes)),
                    events=list(events(action=action, finish=finish, unpaired=unpaired)))
    return native.run(tasker, 'Consumer')


@pytest.mark.parametrize('action', [None, ('GuardStop', 'StopTask'), ('Entry', 'DoNothing')])
def test_failed_guard_with_complete_native_events_is_not_sent(action):
    receipt = receipt_for(action=action)
    with pytest.raises(native.NativeInputNotSent) as caught:
        native.require_native_input(receipt, ('ConsumerClick',))
    assert caught.value.receipt is receipt and receipt['input_execution'] == 'not_sent'
    assert receipt['native_evidence']['complete'] and not receipt['native_evidence']['action_started']


@pytest.mark.parametrize('variant', ['no_events', 'unfinished', 'unpaired', 'partial_input', 'unknown_action'])
def test_missing_or_partial_proof_remains_unknown(variant):
    if variant == 'no_events':
        receipt = native.run(Tasker(), 'Consumer')
    else:
        receipt = receipt_for(finish=variant != 'unfinished', unpaired=variant == 'unpaired',
            action=('OtherClick', 'Click') if variant == 'partial_input'
            else ('UnknownCustom', 'Custom') if variant == 'unknown_action' else None)
    with pytest.raises(RuntimeError, match='outcome unknown') as caught:
        native.require_native_input(receipt, ('ConsumerClick',))
    assert not isinstance(caught.value, native.NativeInputNotSent)
    assert caught.value.receipt is receipt and receipt.get('input_execution') != 'not_sent'


def test_intended_input_returns_receipt():
    receipt = receipt_for(action=('ConsumerClick', 'Click'))
    assert native.require_native_input(receipt, ('ConsumerClick',)) is receipt


def test_sdk_task_and_guard_stop_proof(tmp_path):
    # A native SavedImages controller verifies notification details, offline only.
    import json
    import numpy as np
    from maa.resource import Resource
    from maa.tasker import Tasker as NativeTasker
    from maa.define import LoggingLevelEnum
    from pcr_maa.replay import SavedImages
    NativeTasker.set_log_dir(tmp_path / 'maa')
    NativeTasker.set_stdout_level(LoggingLevelEnum.Off)
    bundle = tmp_path / 'bundle'
    (bundle / 'pipeline').mkdir(parents=True)
    pipeline = {
        'Consumer': {'next': ['RejectedInput'], 'on_error': ['GuardStop'], 'timeout': 1, 'post_delay': 0},
        'RejectedInput': {'recognition': 'ColorMatch', 'roi': [0,0,1,1],
                          'lower': [255,255,255], 'upper': [255,255,255],
                          'action': 'Click', 'target': [10,10,1,1], 'timeout': 1, 'post_delay': 0},
        'GuardStop': {'action': 'StopTask', 'post_delay': 0},
    }
    (bundle / 'pipeline/test.json').write_text(json.dumps(pipeline), encoding='utf-8')
    resource = Resource()
    assert resource.post_bundle(bundle).wait().succeeded
    controller = SavedImages([[np.zeros((720,1280,3), dtype=np.uint8)]])
    tasker = native.bind(resource, controller)
    receipt = native.run(tasker, 'Consumer')
    assert not controller.clicks and not controller.swipes
    assert not tasker.running
    with pytest.raises(native.NativeInputNotSent):
        native.require_native_input(receipt, ('RejectedInput',))
