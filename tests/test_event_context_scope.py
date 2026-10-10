"""Official Context event override lifetime; no OCR/input/device qualification."""
import json
import numpy as np
import pytest
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.resource import Resource
from maa.tasker import Tasker

from pcr_maa import native
from pcr_maa.context_backend import ContextTasker
from pcr_maa.event_native import NativeEvent
from pcr_maa.replay import SavedImages


NODES = ('ExecPresetGuard', 'ExecCancelled', 'ExecRowCategory',
         'ExecOpenSweep', 'ExecReturnedPreset', 'ExecCount')


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    import maa.controller
    from maa.toolkit import Toolkit

    def forbidden(*args, **kwargs):
        raise AssertionError('No ADB, discovery or controller input')

    monkeypatch.setattr(maa.controller, 'AdbController', forbidden)
    monkeypatch.setattr(Toolkit, 'find_adb_devices', forbidden)
    Tasker.set_log_dir(tmp_path / 'maa')
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    bundle = tmp_path / 'bundle'
    (bundle / 'pipeline').mkdir(parents=True)
    # Canonical production nodes, but none of their recognizers/actions runs.
    # This small bundle exists solely to test official snapshot override APIs.
    source = json.loads((native.RESOURCE / 'pipeline/preset_execution.json').read_text(encoding='utf-8'))
    pipeline = {name: source[name] for name in NODES}
    for name in ('PresetsReached', 'ScanCheckedLabel', 'SelectedSlot4',
                 'EventListGuard', 'EventCheckedLabel', 'EventSweepButton',
                 'ExecSweepButton', 'ExecCount', 'ExecStaminaBefore',
                 'ExecStaminaAfter', 'ExecOpenCount', 'ExecOpenStaminaBefore',
                 'ExecOpenStaminaAfter', 'ExecWaitConfirm'):
        pipeline[name] = {'action': 'DoNothing', 'post_delay': 0}
    pipeline['Outer'] = {'action': 'Custom', 'custom_action': 'Borrow', 'post_delay': 0}
    pipeline['ExecCount'] = source['ExecCount']
    pipeline['UnknownStop'] = {'action': 'DoNothing', 'next': []}
    (bundle / 'pipeline/test.json').write_text(json.dumps(pipeline), encoding='utf-8')
    resource = Resource()
    assert resource.post_bundle(bundle).wait().succeeded

    class NoInput(SavedImages):
        click = swipe = forbidden

    controller = NoInput([[np.zeros((720, 1280, 3), dtype=np.uint8)]])
    tasker = native.bind(resource, controller)
    yield tasker, resource, controller
    assert not tasker.running
    assert not controller.clicks and not controller.swipes


def snapshot(resource):
    return {name: resource.get_node_data(name) for name in NODES}


def invoke(sdk, callback):
    tasker, resource, _ = sdk
    errors = []

    class Borrow(CustomAction):
        def run(self, context, argv):
            try:
                return callback(context) is not False
            except BaseException as exc:
                errors.append(exc)
                return False

    assert resource.register_custom_action('Borrow', Borrow())
    job = native.wait_job(tasker.post_task('Outer'), seconds=30)
    if errors:
        raise errors[0]
    return job.succeeded


def test_event_constructor_does_not_mutate_shared_context(sdk):
    original = snapshot(sdk[1])

    def callback(context):
        with ContextTasker(context) as facade:
            NativeEvent(facade, start='event')
            assert snapshot(facade.resource) == original

    assert invoke(sdk, callback)
    assert snapshot(sdk[1]) == original


def test_event_scope_restores_complete_nodes_and_result_guard(sdk):
    original = snapshot(sdk[1])

    def callback(context):
        with ContextTasker(context) as facade:
            port = NativeEvent(facade, start='event')
            with port._confirmation_scope():
                assert snapshot(facade.resource) != original
                assert facade.resource.override_pipeline({'ExecReturnedPreset': {
                    'all_of': ['EventListGuard', 'EventCheckedLabel']}})
            assert snapshot(facade.resource) == original

    assert invoke(sdk, callback)
    assert snapshot(sdk[1]) == original


def test_event_scope_restores_after_business_exception(sdk):
    original = snapshot(sdk[1])

    def callback(context):
        with ContextTasker(context) as facade:
            port = NativeEvent(facade, start='event')
            with pytest.raises(ValueError, match='business error'):
                with port._confirmation_scope():
                    raise ValueError('business error')
            assert snapshot(facade.resource) == original

    assert invoke(sdk, callback)
    assert snapshot(sdk[1]) == original


def test_event_unconfirmed_submit_still_has_zero_input(sdk):
    original = snapshot(sdk[1])

    def callback(context):
        with ContextTasker(context) as facade:
            port = NativeEvent(facade, start='event')
            with pytest.raises(native.NativeInputNotSent) as error:
                port.submit({})
            assert error.value.receipt['input_execution'] == 'not_sent'
            assert error.value.receipt['dispatch_attempted'] is False
            assert snapshot(facade.resource) == original

    assert invoke(sdk, callback)
    assert snapshot(sdk[1]) == original


def test_event_blocked_context_restore_raises_and_root_stays_failed(sdk):
    original = snapshot(sdk[1])

    def callback(context):
        with ContextTasker(context) as facade:
            port = NativeEvent(facade, start='event')
            with pytest.raises(RuntimeError, match='Cannot restore event confirmation scope; root must stop'):
                with port._confirmation_scope():
                    assert not native.run(facade, 'MissingEntry')['native_ok']
            assert facade._blocked
            assert snapshot(facade.resource) != original
            with pytest.raises(RuntimeError, match='stopped or blocked'):
                native.run(facade, 'ExecSubmit')
        return False

    assert not invoke(sdk, callback)
    assert snapshot(sdk[1]) == original


def test_normal_footer_then_event_scope_rebinds_default_and_restores_complete_node(sdk):
    def callback(context):
        with ContextTasker(context) as facade:
            default = facade.resource.get_node_data('ExecCount')
            attach = default['attach']
            assert facade.resource.override_pipeline({'ExecCount': {
                'roi': attach['normal_roi'], 'only_rec': True}})
            normal = facade.resource.get_node_data('ExecCount')
            port = NativeEvent(facade, start='event')
            with port._confirmation_scope():
                event = facade.resource.get_node_data('ExecCount')
                assert event['recognition']['param']['roi'] == attach['default_roi']
                assert event['recognition']['param']['only_rec'] is attach['default_only_rec']
                with port._confirmation_scope():
                    assert facade.resource.get_node_data('ExecCount') == event
                assert facade.resource.get_node_data('ExecCount') == event
            assert facade.resource.get_node_data('ExecCount') == normal
    assert invoke(sdk, callback)


def test_event_footer_restores_complete_node_after_business_error(sdk):
    def callback(context):
        with ContextTasker(context) as facade:
            original = facade.resource.get_node_data('ExecCount')
            assert facade.resource.override_pipeline({'ExecCount': {
                'roi': original['attach']['normal_roi'], 'only_rec': True}})
            normal = facade.resource.get_node_data('ExecCount')
            with pytest.raises(ValueError, match='footer-business-error'):
                with NativeEvent(facade, start='event')._confirmation_scope():
                    assert facade.resource.get_node_data('ExecCount')['recognition']['param']['roi'] == original['attach']['default_roi']
                    raise ValueError('footer-business-error')
            assert facade.resource.get_node_data('ExecCount') == normal
    assert invoke(sdk, callback)
