"""Offline SDK contract tests; run with --noconftest (no production OCR/images)."""
import json
import time
from types import SimpleNamespace

import numpy as np
import pytest
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.resource import Resource
from maa.tasker import Tasker

from pcr_maa import native
from pcr_maa.replay import SavedImages


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    # This test process owns these globals. The adapter must never set them.
    from maa.controller import AdbController
    from maa.toolkit import Toolkit

    def forbidden(*args, **kwargs):
        raise AssertionError('No device discovery/connection allowed')

    monkeypatch.setattr(AdbController, '__init__', forbidden)
    monkeypatch.setattr(Toolkit, 'find_adb_devices', forbidden)
    Tasker.set_log_dir(tmp_path / 'maa')
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    bundle = tmp_path / 'bundle'
    (bundle / 'pipeline').mkdir(parents=True)
    pipeline = {
        'Outer': {'action': 'Custom', 'custom_action': 'Borrow', 'post_delay': 0},
        'Nested': {'action': 'Click', 'target': [10, 20, 1, 1], 'post_delay': 0},
        'PresetsReached': {'post_delay': 0},
        'UnknownStop': {'action': 'StopTask', 'post_delay': 0},
    }
    (bundle / 'pipeline/test.json').write_text(json.dumps(pipeline), encoding='utf-8')
    resource = Resource()
    assert resource.post_bundle(bundle).wait().succeeded
    controller = SavedImages([[np.zeros((720, 1280, 3), dtype=np.uint8)]])
    tasker = native.bind(resource, controller)
    yield tasker, resource, controller, tmp_path
    assert not tasker.running


def invoke(sdk, callback):
    tasker, resource, _, _ = sdk
    errors = []

    class Borrow(CustomAction):
        def run(self, context, argv):
            try:
                callback(context)
                return True
            except BaseException as exc:
                errors.append(exc)
                return False

    assert resource.register_custom_action('Borrow', Borrow())
    result = native.run(tasker, 'Outer')
    if errors:
        raise errors[0]
    return result


def test_sdk_nested_override_receipt_and_borrowed_lifetime(sdk, monkeypatch):
    from pcr_maa.context_backend import ContextTasker
    tasker, resource, controller, tmp_path = sdk
    before = resource.get_node_data('Nested')
    receipts = []

    def callback(context):
        from maa.controller import Controller
        controller_init = Controller.__init__

        def forbidden(*args, **kwargs):
            raise AssertionError('Adapter must not create owners, post_task, or change logs')

        def borrowed_controller_only(self, *args, **kwargs):
            assert not args and kwargs.get('handle')
            controller_init(self, **kwargs)
            assert not self._own

        # SDK controller property creates a NON-owning Python handle view, not
        # a native controller. Do not prohibit that official borrowed accessor.
        with monkeypatch.context() as patch:
            patch.setattr(Tasker, 'post_task', forbidden)
            patch.setattr(Resource, '__init__', forbidden)
            patch.setattr(Tasker, '__init__', forbidden)
            patch.setattr(Controller, '__init__', borrowed_controller_only)
            patch.setattr(Tasker, 'set_log_dir', forbidden)
            patch.setattr(Tasker, 'set_stdout_level', forbidden)
            with ContextTasker(context) as facade:
                assert facade.inited and facade.running
                assert facade.controller.post_screencap().wait().succeeded
                assert facade.controller.cached_image.shape == (720, 1280, 3)
                assert facade.resource.override_pipeline({
                    'Nested': {'target': [30, 40, 1, 1], 'next': ['PresetsReached']}})
                assert facade.resource.get_node_data('Nested') != before
                receipts.append(native.run(facade, 'Nested'))
                assert resource.get_node_data('Nested') == before
            with pytest.raises(RuntimeError, match='closed'):
                native.run(facade, 'Nested')

    assert invoke(sdk, callback)['native_ok']
    result = receipts[0]
    assert set(result) == {'native_ok', 'tasker_inited', 'task_id', 'nodes',
                           'navigation_complete', 'daily_complete'}
    assert result['native_ok'] and result['navigation_complete']
    assert not result['daily_complete'] and result['task_id'] > 0
    assert [n['name'] for n in result['nodes']] == ['Nested', 'PresetsReached']
    assert result['nodes'][0]['action']['success']
    assert controller.clicks == [[30, 40]]
    assert resource.get_node_data('Nested') == before
    # Original CLI native.run(Tasker) path, same owners after facade exit.
    direct = native.run(tasker, 'Nested')
    assert direct['native_ok'] and not direct['navigation_complete']
    assert controller.clicks == [[30, 40], [10, 20]]
    (tmp_path / 'receipts.json').write_text(json.dumps({'nested': result, 'direct': direct},
                                                    indent=2), encoding='utf-8')


@pytest.mark.parametrize('method,args', [
    ('register_custom_recognition', ('OneKeyPage', object())),
    ('unregister_custom_recognition', ('OneKeyPage',)),
    ('register_custom_action', ('CollectPresetPage', object())),
    ('unregister_custom_action', ('CollectPresetPage',)),
])
def test_registration_fails_closed_without_owner_resource_access(method, args):
    from pcr_maa.context_backend import ContextTasker
    with ContextTasker(object()) as facade:
        with pytest.raises(NotImplementedError, match='Agent-side registration'):
            getattr(facade.resource, method)(*args)
        assert not hasattr(facade, 'post_task')
        assert not hasattr(facade.controller, 'post_click')
        assert not hasattr(facade.resource, 'post_bundle')


def test_sdk_failure_blocks_further_dispatch(sdk):
    from pcr_maa.context_backend import ContextTasker
    receipts = []

    def callback(context):
        with ContextTasker(context) as facade:
            result = native.run(facade, 'MissingEntry')
            receipts.append(result)
            assert not result['native_ok'] and not result['navigation_complete']
            assert not result['daily_complete']
            with pytest.raises(RuntimeError, match='blocked'):
                native.run(facade, 'Nested')

    invoke(sdk, callback)
    assert sdk[2].clicks == []
    (sdk[3] / 'failure.json').write_text(json.dumps(receipts, indent=2), encoding='utf-8')


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_sdk_deadline_no_retry(sdk, phase):
    from pcr_maa.context_backend import ContextTasker

    def callback(context):
        with ContextTasker(context) as facade:
            assert facade.resource.override_pipeline({'Nested': {'post_delay': 100}})
            token = native.RUN_DEADLINE.set(time.monotonic() + (0.05 if phase == 'after' else -1))
            try:
                with pytest.raises(RuntimeError, match='deadline exceeded'):
                    native.run(facade, 'Nested')
            finally:
                native.RUN_DEADLINE.reset(token)
            with pytest.raises(RuntimeError, match='blocked'):
                native.run(facade, 'Nested')

    invoke(sdk, callback)
    assert sdk[2].clicks == ([[10, 20]] if phase == 'after' else [])


def test_sdk_stop_is_request_not_callback_wait_or_result(sdk):
    from pcr_maa.context_backend import ContextTasker
    state = {}

    def callback(context):
        with ContextTasker(context) as facade:
            ticket = facade.post_stop()
            state['stop_requested'] = facade.stop_requested
            state['owner_running_in_callback'] = facade.running
            assert facade.post_stop() is ticket
            with pytest.raises(RuntimeError, match='Cannot wait'):
                ticket.wait()
            with pytest.raises(RuntimeError, match='stopped or blocked'):
                native.run(facade, 'Nested')

    invoke(sdk, callback)
    assert state == {'stop_requested': True, 'owner_running_in_callback': True}
    assert not sdk[0].running and sdk[2].clicks == []
    (sdk[3] / 'stop.json').write_text(json.dumps(state, indent=2), encoding='utf-8')


def test_sdk_stop_during_synthetic_input_does_not_confirm_or_resend(sdk, monkeypatch):
    from pcr_maa.context_backend import ContextTasker
    events = []
    active = []
    original_click = sdk[2].click

    def click(x, y):
        events.append('input_started')
        active[0].post_stop()
        assert active[0].stop_requested
        events.append('stop_requested_while_input_active')
        result = original_click(x, y)
        events.append('input_returned')
        return result

    monkeypatch.setattr(sdk[2], 'click', click)

    def callback(context):
        with ContextTasker(context) as facade:
            active.append(facade)
            with pytest.raises(RuntimeError, match='stop requested'):
                native.run(facade, 'Nested')
            with pytest.raises(RuntimeError, match='blocked'):
                native.run(facade, 'Nested')
            events.append('nested_returned_unknown')

    invoke(sdk, callback)
    assert events == ['input_started', 'stop_requested_while_input_active',
                      'input_returned', 'nested_returned_unknown']
    assert sdk[2].clicks == [[10, 20]]
    (sdk[3] / 'stop-during-input.json').write_text(json.dumps(events, indent=2), encoding='utf-8')


@pytest.mark.parametrize('failure', ['exception', 'interrupt', 'none', 'failed', 'stopping', 'lazy-detail'])
def test_injected_faults_never_redispatch(failure):
    """Pure fault injection complements, and does not replace, the SDK tests."""
    from pcr_maa.context_backend import ContextTasker
    calls = []
    owner = SimpleNamespace(inited=True, running=True, stopping=False)

    class BrokenDetail:
        task_id = 8
        status = SimpleNamespace(succeeded=True)

        @property
        def nodes(self):
            raise RuntimeError('injected lazy detail failure')

    def run_task(entry):
        calls.append(entry)
        if failure == 'exception':
            raise RuntimeError('injected exception')
        if failure == 'interrupt':
            raise KeyboardInterrupt
        if failure == 'none':
            return None
        if failure == 'stopping':
            owner.stopping = True
        if failure == 'lazy-detail':
            return BrokenDetail()
        return SimpleNamespace(task_id=7, nodes=[], status=SimpleNamespace(succeeded=False))

    with ContextTasker(SimpleNamespace(tasker=owner, run_task=run_task)) as facade:
        if failure in ('none', 'failed'):
            result = native.run(facade, 'Once')
            assert not result['native_ok'] and not result['navigation_complete']
            assert not result['daily_complete']
            assert result['task_id'] == (None if failure == 'none' else 7)
        else:
            with pytest.raises(KeyboardInterrupt if failure == 'interrupt' else RuntimeError):
                native.run(facade, 'Once')
        with pytest.raises(RuntimeError, match='blocked'):
            native.run(facade, 'Never')
    assert calls == ['Once']


def test_injected_stop_exception_latches_before_dispatch():
    from pcr_maa.context_backend import ContextTasker
    calls = []

    def post_stop():
        calls.append('stop')
        raise RuntimeError('injected stop failure')

    owner = SimpleNamespace(inited=True, running=True, stopping=False, post_stop=post_stop)
    with ContextTasker(SimpleNamespace(tasker=owner)) as facade:
        with pytest.raises(RuntimeError, match='injected stop'):
            facade.post_stop()
        assert facade.stop_requested
        facade.post_stop()
        with pytest.raises(RuntimeError, match='blocked'):
            native.run(facade, 'Never')
    assert calls == ['stop']


def test_sdk_native_success_does_not_confirm_unknown_game_result(sdk):
    from pcr_maa.context_backend import ContextTasker
    results = []

    def callback(context):
        with ContextTasker(context) as facade:
            results.append(native.run(facade, 'UnknownStop'))

    invoke(sdk, callback)
    direct = native.run(sdk[0], 'UnknownStop')
    nested = results[0]
    assert nested['native_ok'] == direct['native_ok']
    assert not nested['navigation_complete'] and not nested['daily_complete']
    assert sdk[2].clicks == []


def test_sdk_direct_tasker_deadline_and_interrupt_regression(sdk, monkeypatch):
    token = native.RUN_DEADLINE.set(time.monotonic() - 1)
    try:
        with pytest.raises(RuntimeError, match='before task input'):
            native.run(sdk[0], 'Nested')
    finally:
        native.RUN_DEADLINE.reset(token)
    assert sdk[2].clicks == []
    assert sdk[1].override_pipeline({'PresetsReached': {'post_delay': 100}})

    def interrupt(_):
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(native.time, 'sleep', interrupt)
        with pytest.raises(KeyboardInterrupt):
            native.run(sdk[0], 'PresetsReached')
    assert not sdk[0].running and sdk[2].clicks == []
    assert native.run(sdk[0], 'PresetsReached')['native_ok']


def test_sdk_observed_versions(sdk):
    import platform
    from importlib.metadata import version
    from maa.library import Library
    versions = {'python': platform.python_version(), 'maafw_package': version('maafw'),
                'framework_runtime': Library.version(), 'pytest': version('pytest')}
    (sdk[3] / 'versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')
    assert versions['maafw_package'] == '5.13.0'
    assert versions['framework_runtime'] == 'v5.13.0'
