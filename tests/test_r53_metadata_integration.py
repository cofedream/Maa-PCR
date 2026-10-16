"""Only successful native receipts use image-free fields; errors never retry."""
from types import SimpleNamespace

import pytest
from maa.define import TaskDetail
from maa.tasker import Tasker

from pcr_maa import native, recognition_metadata
from pcr_maa.context_backend import ContextTasker


def owner():
    tasker = Tasker.__new__(Tasker)
    tasker._handle, tasker._own = 1, False
    return tasker


def test_success_receipt_uses_metadata_and_preserves_public_fields(monkeypatch):
    tasker = owner()
    detail = TaskDetail(17, 'Read', [101], None)
    nodes = [dict(name='PresetsReached', completed=True, action=None, recognition=None)]
    checks = []
    def read(actual_owner, actual_detail, *, check):
        assert actual_owner is tasker and actual_detail is detail
        check()
        return nodes
    monkeypatch.setattr(recognition_metadata, 'read_receipt_nodes', read)
    result = native._receipt(detail, native_ok=True, tasker_inited=True, task_id=17,
                             tasker=tasker, check=lambda: checks.append(True))
    assert result == dict(native_ok=True, tasker_inited=True, task_id=17, nodes=nodes,
                         navigation_complete=True, daily_complete=False)
    assert checks == [True] and detail._nodes is None


def test_metadata_error_does_not_read_sdk_nodes_or_return_partial_receipt(monkeypatch):
    detail = TaskDetail(17, 'Read', [101], None)
    def fail(*args, **kwargs):
        raise RuntimeError('disconnected')
    monkeypatch.setattr(recognition_metadata, 'read_receipt_nodes', fail)
    monkeypatch.setattr(TaskDetail, 'nodes', property(lambda self: pytest.fail('no fallback')))
    with pytest.raises(RuntimeError, match='disconnected'):
        native._receipt(detail, native_ok=True, tasker_inited=True, task_id=17,
                        tasker=owner(), check=lambda: None)


def test_failed_task_retains_full_sdk_receipt_path(monkeypatch):
    detail = TaskDetail(17, 'FailedRead', [101], None)
    detail._nodes = [SimpleNamespace(name='Read', completed=False, action=None,
                                    recognition=SimpleNamespace(raw_detail={'all': []}))]
    monkeypatch.setattr(recognition_metadata, 'read_receipt_nodes',
                        lambda *args, **kwargs: pytest.fail('failed evidence stays full SDK'))
    result = native._receipt(detail, native_ok=False, tasker_inited=True, task_id=17,
                             tasker=owner(), check=lambda: pytest.fail('already failed task is blocked'))
    assert result['nodes'] == [dict(name='Read', completed=False, action=None,
                                    recognition={'all': []})]
    assert not result['native_ok'] and not result['navigation_complete']


@pytest.mark.parametrize('stopping', [False, True])
def test_receipt_local_stop_check(stopping):
    tasker = SimpleNamespace(stopping=stopping)
    if stopping:
        with pytest.raises(RuntimeError, match='stopped or blocked'):
            native._check_receipt(tasker)
    else:
        native._check_receipt(tasker)


def test_receipt_deadline_refuses_before_borrowed_sdk_query():
    token = native.RUN_DEADLINE.set(0)
    try:
        with pytest.raises(RuntimeError, match='Receipt deadline'):
            native._check_receipt(object())
    finally:
        native.RUN_DEADLINE.reset(token)


@pytest.mark.parametrize('state', ['closed', 'stopping', 'blocked'])
def test_borrowed_receipt_rejects_revocation_or_stop(state):
    context = SimpleNamespace(tasker=SimpleNamespace(stopping=state == 'stopping', inited=True))
    facade = ContextTasker(context)
    facade._closed = state == 'closed'
    facade._blocked = state == 'blocked'
    with pytest.raises(RuntimeError):
        native._check_receipt(facade)


@pytest.mark.parametrize('input_action', [False, True])
def test_actual_native_metadata_failure_never_claims_not_sent_or_replays(tmp_path, monkeypatch, input_action):
    import json
    import numpy as np
    from maa.resource import Resource
    from pcr_maa.replay import SavedImages

    bundle = tmp_path / 'bundle'
    (bundle / 'pipeline').mkdir(parents=True)
    node = dict(action='Click' if input_action else 'DoNothing', pre_delay=0, post_delay=0)
    if input_action:
        node['target'] = [10, 20, 1, 1]
    (bundle / 'pipeline/test.json').write_text(json.dumps({'Once': node}), encoding='utf-8')
    resource = Resource()
    assert resource.post_bundle(bundle).wait().succeeded
    controller = SavedImages([[np.zeros((720, 1280, 3), dtype=np.uint8)]])
    tasker = native.bind(resource, controller)
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError('metadata unavailable after native completion')
    monkeypatch.setattr(recognition_metadata, 'read_receipt_nodes', fail)
    with pytest.raises(RuntimeError, match='metadata unavailable') as caught:
        native.run(tasker, 'Once')
    assert not isinstance(caught.value, native.NativeInputNotSent)
    assert not hasattr(caught.value, 'receipt')
    assert caught.value.native_stop['idle'] and not tasker.running
    assert attempts == [True]
    assert controller.clicks == ([[10, 20]] if input_action else [])
