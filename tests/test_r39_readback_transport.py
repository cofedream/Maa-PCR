"""Real native readback and official TCP transport, synthetic frames only."""
import importlib.util
import json
from pathlib import Path
import shutil
import time

import numpy as np
import pytest
from maa.controller import CustomController
from maa.define import LoggingLevelEnum
from maa.resource import Resource
from maa.tasker import Tasker

from pcr_maa import native
from pcr_maa.preset_execution_native import NativePreset


ROOT = Path(__file__).resolve().parents[1]
PRESETS = json.loads((ROOT / 'assets/resource/pipeline/presets.json').read_text(encoding='utf-8'))


class SyntheticPresets(CustomController):
    def __init__(self):
        super().__init__()
        self.slot = 2
        self.captures = []
        self.inputs = []

    def connect(self):
        return True

    def request_uuid(self):
        return 'SYNTHETIC-R39-PRESET-READBACK'

    def get_features(self):
        return 0

    def screencap(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[10:23, 10:26] = [255, 170, 0]
        x, y, width, height = PRESETS[f'SelectedSlot{self.slot}']['roi']
        image[y:y + height, x:x + width] = [255, 170, 0]
        self.captures.append(self.slot)
        return image

    def denied(self, *args):
        self.inputs.append(list(args))
        raise AssertionError('Readback must not send any input')

    click = swipe = touch_down = touch_move = touch_up = denied
    start_app = stop_app = input_text = click_key = key_down = key_up = denied


def until(predicate, seconds=10):
    end = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < end, 'bounded synthetic test deadline'
        time.sleep(0.02)


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    from maa.controller import AdbController
    from maa.toolkit import Toolkit

    def forbidden(*args, **kwargs):
        raise AssertionError('No device discovery or connection allowed')

    monkeypatch.setattr(AdbController, '__init__', forbidden)
    monkeypatch.setattr(Toolkit, 'find_adb_devices', forbidden)
    Tasker.set_log_dir(tmp_path / 'host-maa')
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    names = ('PresetReadbackStart', 'ObservePresets', 'ObserveUnknown', 'SelectionUnknown')
    pipeline = {name: PRESETS[name] for name in names}
    pipeline.update({f'SelectedSlot{i}': PRESETS[f'SelectedSlot{i}'] for i in range(1, 8)})
    # Only this isolated fixture substitutes the OCR page guard. Slot rules and
    # the production entry/routing remain unchanged, with native ColorMatch.
    pipeline['PresetsReached'] = dict(recognition='ColorMatch', roi=[10, 10, 16, 13],
        method=40, lower=[95, 100, 100], upper=[120, 255, 255], count=180,
        action='DoNothing', next=[])
    pipeline['Entry'] = dict(action='Custom', custom_action='SYNTHETIC_Readback', post_delay=0)
    bundle = tmp_path / 'bundle'
    (bundle / 'pipeline').mkdir(parents=True)
    (bundle / 'pipeline/test.json').write_text(json.dumps(pipeline), encoding='utf-8')
    resource, controller, tasker = Resource(), SyntheticPresets(), Tasker()
    assert resource.post_bundle(bundle).wait().succeeded
    assert controller.set_screenshot_target_short_side(720)
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    yield tasker, resource, controller, tmp_path
    assert not tasker.running and controller.inputs == []


def assert_readback(result, actual):
    assert result['native_ok'] is True and result['requested_slot'] == 2
    assert result['selected_slot'] == actual
    assert result['observation'] == dict(page='presets', selected_slot=actual,
                                       list_complete=False, daily_complete=False)
    receipt = result['ocr_receipt']
    completed = [node['name'] for node in receipt['nodes'] if node['completed']]
    assert 'ObservePresets' in completed and f'SelectedSlot{actual}' in completed
    assert [name for name in completed if name.startswith('SelectedSlot')] == [f'SelectedSlot{actual}']
    assert all(node['action'] is None or node['action']['success'] for node in receipt['nodes'])
    selected = next(node for node in receipt['nodes'] if node['name'] == f'SelectedSlot{actual}')
    assert selected['recognition']['best']['count'] == 208
    assert receipt['tasker_inited'] and not receipt['navigation_complete'] and not receipt['daily_complete']


@pytest.mark.parametrize('actual', [2, 3])
def test_standalone_native_readback_reports_actual_not_requested_slot(sdk, actual):
    tasker, _, controller, out = sdk
    controller.slot = actual
    result = NativePreset(tasker).verify_preset(2)
    assert_readback(result, actual)
    proof = result['ocr_receipt']['native_evidence']
    assert proof['complete'] and not proof['action_started']
    assert controller.captures and set(controller.captures) == {actual}
    (out / 'readback.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


def test_standalone_successive_readback_uses_fresh_controller_frame(sdk):
    tasker, _, controller, out = sdk
    adapter, rows = NativePreset(tasker), []
    for actual in (2, 3):
        before = len(controller.captures)
        controller.slot = actual
        rows.append(adapter.verify_preset(2))
        assert_readback(rows[-1], actual)
        assert len(controller.captures) > before
        assert set(controller.captures[before:]) == {actual}
    assert rows[0]['ocr_receipt']['task_id'] != rows[1]['ocr_receipt']['task_id']
    (out / 'fresh-readbacks.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')


def test_official_tcp_borrowed_readbacks_are_native_and_fresh(sdk, monkeypatch):
    from maa.agent_client import AgentClient
    from pcr_maa.agent_callbacks import ACTIONS, HOME, RECOGNITIONS, _MARKER

    tasker, resource, controller, out = sdk

    def forbidden(*args, **kwargs):
        raise AssertionError('Host must not register custom callbacks for the TCP test')

    for method in ('register_custom_action', 'register_custom_recognition',
                   'unregister_custom_action', 'unregister_custom_recognition'):
        monkeypatch.setattr(Resource, method, forbidden)
    before = resource.get_node_data('PresetReadbackStart')
    client = AgentClient.create_tcp(0)
    assert client.set_timeout(8000) and client.bind(resource)
    spec = importlib.util.spec_from_file_location('readback_server_process',
        Path(__file__).with_name('helpers') / 'server_process.py')
    process = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(process)
    # Reuse the existing suspended-process/Job owner without editing it. Its
    # fixed child basename is staged from this test's dedicated Agent entry.
    child = out / 'child'
    child.mkdir()
    shutil.copy2(Path(__file__).with_name('helpers') / 'preset_readback_server.py', child / 'tcp_server.py')
    process.BASE = child
    owner = process.ServerJob(out, 'readback-server')
    host_rows = []
    try:
        pid = owner.start(client.identifier)
        until(lambda: (out / 'ready').exists() or owner.poll(pid) is not None)
        assert owner.poll(pid) is None, (out / 'server.stderr.txt').read_text(encoding='utf-8')
        assert int((out / 'ready').read_text(encoding='utf-8')) in owner.snapshot()
        assert client.connect()
        assert set(client.custom_recognition_list) == RECOGNITIONS | {HOME}
        assert set(client.custom_action_list) == ACTIONS | {'SYNTHETIC_Readback'}
        for actual in (2, 3, 2):
            controller.slot = actual
            captures_before = len(controller.captures)
            job = tasker.post_task('Entry')
            until(lambda: job.done)
            until(lambda: not tasker.running)
            assert job.succeeded
            rows = json.loads((out / 'server-results.json').read_text(encoding='utf-8'))
            assert len(rows) == len(host_rows) + 1
            row = rows[-1]
            assert row['ok'] and row['borrowed'] and row['cleaned'], row
            assert row['root_task'] == job.job_id
            assert row['result']['ocr_receipt']['task_id'] > 0
            assert 'native_evidence' not in row['result']['ocr_receipt']
            assert_readback(row['result'], actual)
            assert len(controller.captures) > captures_before
            assert set(controller.captures[captures_before:]) == {actual}
            host_rows.append(dict(actual_slot=actual, root_task=job.job_id,
                captures=len(controller.captures) - captures_before, result=row['result']))
        assert len({row['root_task'] for row in host_rows}) == 3
        assert len({row['result']['ocr_receipt']['task_id'] for row in host_rows}) == 3
        assert resource.get_node_data('PresetReadbackStart') == before
        assert resource.get_node_data(_MARKER) is None
        assert controller.inputs == []
        (out / 'host-results.json').write_text(json.dumps(host_rows, indent=2), encoding='utf-8')
    finally:
        client.disconnect()
        (out / 'shutdown').write_text('shutdown', encoding='utf-8')
        cleanup = owner.cleanup(grace=5)
        (out / 'process.json').write_text(json.dumps(cleanup, indent=2), encoding='utf-8')
    assert cleanup['all_reaped'] and cleanup['query_job_empty'] and not cleanup['forced']
    assert all(row['returncode'] == 0 for row in cleanup['processes'])
