"""Scalar-only profiling contracts; no game, network or account state required."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from pcr_maa.labyrinth import timing
from pcr_maa.labyrinth.protocol import Endpoint, LabyrinthClient, ProtocolFailure


def test_missing_metrics_differ_from_observed_zero_and_overlap_is_not_summed():
    timings = timing.Timings()
    assert timings.payload()['screenshot'] is None
    timings.observe('swipe')
    timings.record('page_wait', 30)
    timings.record('screenshot', 10)
    payload = timings.payload()
    assert payload['swipe']['count'] == 0
    assert payload['recognition'] is None
    assert payload['page_wait']['total_ms'] == 30
    assert payload['screenshot']['total_ms'] == 10
    assert not any('elapsed' in key for key in payload)


@pytest.mark.parametrize('error,outcome', [(None, 'succeeded'),
    (RuntimeError('synthetic secret'), 'failed'), (TimeoutError('synthetic secret'), 'unknown'),
    (asyncio.CancelledError('synthetic secret'), 'unknown')])
def test_measure_records_outcome_without_exception_text(error, outcome):
    timings = timing.Timings()
    def exercise():
        with timings.measure('protocol_request', label='labyrinth/top'):
            if error is not None:raise error
    if error is None:exercise()
    else:
        with pytest.raises(type(error)):exercise()
    payload = timings.payload()
    request = payload['protocol_request']['labels']['labyrinth/top']
    assert request['count'] == 1 and request['outcomes'] == {outcome: 1}
    assert 'secret' not in json.dumps(payload)


def test_metric_labels_cannot_turn_payloads_or_credentials_into_logs():
    timings = timing.Timings()
    with pytest.raises(ValueError, match='unknown_timing_label'):
        timings.record('protocol_request', 1, label='password=synthetic-secret')
    with pytest.raises(ValueError, match='invalid_elapsed_time'):
        timings.record('input', float('nan'))
    assert all(value is None for value in timings.payload().values())


def test_business_phase_transitions_reuse_one_scalar_collector(monkeypatch):
    moments = iter((1, 2, 4))
    monkeypatch.setattr(timing, 'perf_counter', lambda: next(moments))
    timings = timing.Timings()
    timings.transition('opening')
    timings.transition('playing')
    timings.transition(None)
    labels = timings.payload()['business_phase']['labels']
    assert labels['opening']['total_ms'] == 1000
    assert labels['playing']['total_ms'] == 2000


@pytest.mark.parametrize('error,outcome', [(None, 'succeeded'),
    (ProtocolFailure('request_failed'), 'failed'),
    (ProtocolFailure('network_or_protocol_failed'), 'unknown'),
    (asyncio.CancelledError(), 'unknown')])
def test_protocol_measurement_never_logs_fields_or_changes_send_count(error, outcome):
    client = LabyrinthClient.__new__(LabyrinthClient)
    client.timings = timing.Timings()
    calls = []
    async def wire(endpoint, fields):
        calls.append((endpoint, fields))
        if error is not None:raise error
        return {'accepted': True}
    client._request_wire = wire
    fields = {'secret': 'synthetic-password', 'token': 'synthetic-access-key'}
    if error is None:
        assert asyncio.run(client._request_locked(Endpoint.ENTER, fields)) == {'accepted': True}
    else:
        with pytest.raises(type(error)):
            asyncio.run(client._request_locked(Endpoint.ENTER, fields))
    assert calls == [(Endpoint.ENTER, fields)]
    encoded = json.dumps(client.timings.payload())
    assert 'synthetic-password' not in encoded and 'synthetic-access-key' not in encoded
    assert client.timings.payload()['protocol_request']['labels']['labyrinth/enter']['outcomes'] == {outcome: 1}


def test_native_swipe_is_input_subset_and_page_wait_is_optional(tmp_path):
    from maa.event_sink import NotificationType
    class Source:
        def add_sink(self, sink):return 1
        def add_context_sink(self, sink):return 2
        def remove_sink(self, number):pass
        def remove_context_sink(self, number):pass
    timings = timing.Timings()
    tasker, controller = Source(), Source()
    collector = timing.NativeTimings(tasker, controller, timings, measure_page_wait=True)
    context = SimpleNamespace(get_node_data=lambda name: {'action': 'Swipe'})
    task = SimpleNamespace(task_id=1, entry='synthetic')
    action = SimpleNamespace(task_id=1, action_id=2, name='synthetic')
    collector.task_sink.on_tasker_task(tasker, NotificationType.Starting, task)
    for notification in (NotificationType.Starting, NotificationType.Succeeded):
        collector.context_sink.on_node_action(context, notification, action)
    collector.task_sink.on_tasker_task(tasker, NotificationType.Succeeded, task)
    collector.close()
    payload = timings.payload()
    assert payload['input']['count'] == payload['swipe']['count'] == payload['page_wait']['count'] == 1


@pytest.mark.parametrize('notification,outcome', [('Succeeded', 'succeeded'), ('Failed', 'failed'), (None, 'unknown')])
def test_native_pending_duration_keeps_failure_or_unknown_instead_of_zero(notification, outcome):
    from maa.event_sink import NotificationType
    collector = timing.NativeTimings.__new__(timing.NativeTimings)
    collector.timings = timing.Timings()
    collector.lock, collector.pending, collector.closed = timing.RLock(), {}, False
    collector.controller_id = collector.context_id = collector.task_id = None
    collector.event(('synthetic', 1), 'input', NotificationType.Starting, NotificationType)
    if notification is not None:
        collector.event(('synthetic', 1), 'input', getattr(NotificationType, notification), NotificationType)
    collector.close()
    assert collector.timings.payload()['input']['outcomes'] == {outcome: 1}


def test_daily_profiling_keeps_success_failure_unknown_business_results(tmp_path, monkeypatch):
    from pcr_maa import supported_daily
    from test_r39_navigation import config, result_for
    collectors = []
    class Collector:
        def __init__(self, tasker, controller, timings, **kwargs):
            assert kwargs['measure_page_wait'] is True
            assert kwargs['collect_input_evidence'] is False
            self.timings = timings
            self.closed = False
            timings.observe('swipe')
            collectors.append(self)
        def finish_native_trace(self):self.timings.record('screenshot', 2)
        def close(self):self.closed = True
    monkeypatch.setattr(timing, 'NativeTimings', Collector)
    owner = SimpleNamespace(controller=object(), add_context_sink=lambda sink: 1)
    for index, outcome in enumerate(('executed', 'unknown_settlement', 'error')):
        def step(tasker, name, cfg, day, context, *, exit_page='home'):
            if outcome == 'error':raise RuntimeError('synthetic failure')
            result = result_for(name, cfg, exit_page)
            if outcome == 'unknown_settlement':result.update(outcome=outcome, settlement_verified=False)
            return result
        monkeypatch.setattr(supported_daily, '_run_step', step)
        report = supported_daily.execute_supported(owner, config(), execute=True,
            game_day=supported_daily.current_game_day(), context='user-reported-unreset',
            selected_modules=['deep'], rewards_final=False, directory=tmp_path / str(index))
        assert report['outcome'] == {'executed': 'complete', 'unknown_settlement': 'stopped', 'error': 'failed'}[outcome]
        assert report['performance']['native_timings']['screenshot']['count'] == 1
        assert report['performance']['native_timings']['swipe']['count'] == 0
        assert report['performance']['native_timings']['recognition'] is None
        assert report['performance']['native_timing_collection'] == 'collected'
        assert supported_daily._read_report(tmp_path / str(index)) == report
    assert all(collector.closed for collector in collectors)


@pytest.mark.parametrize('fault,status', [('attach', 'unavailable'), ('read_trace', 'incomplete')])
def test_daily_metrics_failure_cannot_change_settled_business_result(tmp_path, monkeypatch, fault, status):
    from pcr_maa import supported_daily
    from test_r39_navigation import config, result_for
    closed = []
    class Collector:
        def __init__(self, *args, **kwargs):
            if fault == 'attach':raise RuntimeError('synthetic profiling failure')
        def finish_native_trace(self):raise OSError('synthetic trace unreadable')
        def close(self):closed.append(True)
    monkeypatch.setattr(timing, 'NativeTimings', Collector)
    monkeypatch.setattr(supported_daily, '_run_step', lambda owner, name, cfg, *args:
        result_for(name, cfg, 'home'))
    owner = SimpleNamespace(controller=object(), add_context_sink=lambda sink: 1)
    report = supported_daily.execute_supported(owner, config(), execute=True,
        game_day=supported_daily.current_game_day(), context='user-reported-unreset',
        selected_modules=['deep'], rewards_final=False, directory=tmp_path)
    assert report['outcome'] == 'complete'
    assert report['performance']['native_timing_collection'] == status
    assert closed == ([] if fault == 'attach' else [True])


class EventOwner:
    """Typed profiling events are weaker than the paired native dispatch proof."""
    inited = True
    def __init__(self, *, paired=True, context_available=True):
        self.paired, self.context_available = paired, context_available
        self.tasks, self.contexts, self.removed = {}, {}, []
    def add_sink(self, sink):
        if type(sink).__name__ == 'Sink' and not self.paired:return None
        number = len(self.tasks) + 1
        self.tasks[number] = sink
        return number
    def add_context_sink(self, sink):
        if not self.context_available or type(sink).__name__ == 'Sink' and not self.paired:return None
        number = len(self.contexts) + 1
        self.contexts[number] = sink
        return number
    def remove_sink(self, number):self.removed.append(('task', number));self.tasks.pop(number)
    def remove_context_sink(self, number):self.removed.append(('context', number));self.contexts.pop(number)
    def post_task(self, entry):
        from maa.event_sink import NotificationType as NT
        task = dict(task_id=9, entry=entry)
        reco = dict(task_id=9, reco_id=4, name='Guard')
        for sink in self.tasks.values():
            if type(sink).__name__ == 'Sink':sink._on_raw_notification(None, 'Tasker.Task.Starting', task)
            else:sink.on_tasker_task(self, NT.Starting, SimpleNamespace(**task))
        for sink in self.contexts.values():
            if type(sink).__name__ == 'Sink':
                sink._on_raw_notification(None, 'Node.Recognition.Starting', reco)
                sink._on_raw_notification(None, 'Node.Recognition.Failed', reco)
            else:
                sink.on_node_recognition(None, NT.Starting, SimpleNamespace(**reco))
                sink.on_node_recognition(None, NT.Failed, SimpleNamespace(**reco))
        for sink in self.tasks.values():
            if type(sink).__name__ == 'Sink':sink._on_raw_notification(None, 'Tasker.Task.Failed', task)
            else:sink.on_tasker_task(self, NT.Failed, SimpleNamespace(**task))
        return SimpleNamespace(done=True, succeeded=False, job_id=9, get=lambda:SimpleNamespace(nodes=[]))


class EventController:
    def __init__(self):self.removed=[]
    def add_sink(self, sink):return 1
    def remove_sink(self, number):self.removed.append(number)


@pytest.mark.parametrize('paired', [False, True])
@pytest.mark.parametrize('existing', [False, True])
def test_daily_metrics_cannot_change_native_unknown_or_not_sent(paired, existing):
    from pcr_maa import native
    results = []
    for enabled in (False, True):
        owner, controller = EventOwner(paired=paired), EventController()
        saved = {'existing': {'unsettled': True}}
        if existing:owner._labyrinth_input_evidence = saved
        collector = (timing.NativeTimings(owner, controller, timing.Timings(),
            measure_page_wait=True, collect_input_evidence=False) if enabled else None)
        try:
            receipt = native.run(owner, 'Consumer', reset_hits=('Consumer',))
            native.require_native_input(receipt, ('Consumer',))
        except (RuntimeError, native.NativeInputNotSent) as error:
            results.append((type(error), error.receipt))
        finally:
            if collector is not None:
                assert collector.input_evidence == {}
                collector.close()
        if existing:
            assert owner._labyrinth_input_evidence is saved and saved == {'existing': {'unsettled': True}}
        else:assert not hasattr(owner, '_labyrinth_input_evidence')
    assert results[0] == results[1]
    assert results[0][0] is (native.NativeInputNotSent if paired else RuntimeError)
    assert results[0][1].get('input_execution') == ('not_sent' if paired else None)


@pytest.mark.parametrize('collect', [False, True])
def test_failed_metrics_attach_preserves_existing_input_evidence_identity(collect):
    owner, controller = EventOwner(context_available=False), EventController()
    saved = {'existing': {'unsettled': True}}
    owner._labyrinth_input_evidence = saved
    with pytest.raises(RuntimeError, match='native_timing_sink_unavailable'):
        timing.NativeTimings(owner, controller, timing.Timings(), collect_input_evidence=collect)
    assert owner._labyrinth_input_evidence is saved
    assert saved == {'existing': {'unsettled': True}}
    assert owner.removed == [('task', 1)] and controller.removed == [1]
