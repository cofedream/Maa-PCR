"""Exercise the custom recognition route with real Maa, an offline controller and no game."""
import numpy as np
import pytest
from types import SimpleNamespace

from pcr_maa import native
from pcr_maa.agent_callbacks import RECOGNITIONS
from pcr_maa.optional_sweep_native import NativeOptionalSweep
from pcr_maa.replay import SavedImages


def test_offline_same_frame_click_and_changed_frame_rejection():
    native.setup()
    resource = native.load_resource(cpu_only=True)
    first = np.zeros((720, 1280, 3), dtype=np.uint8)
    first[200:220, 100:120] = 255
    changed = np.zeros_like(first)
    controller = SavedImages([[first], [changed]])
    tasker = native.bind(resource, controller)
    port = NativeOptionalSweep(tasker, 'temple_survey', 2)
    assert 'OptionalSweepPage' in RECOGNITIONS

    def guard(context, argv):
        if argv.image[205, 105].min() > 240:
            return [100, 200, 20, 20], {'probe': 'marker'}
        return None

    assert port.frame(guard)['probe'] == 'marker'
    assert controller.clicks == []
    assert port.frame(guard, click=True)['probe'] == 'marker'
    assert len(controller.clicks) == 1
    assert not controller.swipes
    def scroll_guard(context, argv):
        if argv.image[205, 105].max() == 0:
            return [970, 494, 8, 8], {'probe': 'list'}
    assert port.scroll_list(scroll_guard, down=True)['probe'] == 'list'
    assert len(controller.swipes) == 1
    sx, sy, ex, ey, duration = controller.swipes[0]
    assert 970 <= sx < 978 and 494 <= sy < 502 and 990 <= ex < 1002 and 178 <= ey < 186
    assert duration == 400
    x, y = controller.clicks[0]
    assert 100 <= x < 120 and 200 <= y < 220
    with pytest.raises(RuntimeError):
        port.frame(guard, click=True, timeout_ms=200)
    assert len(controller.clicks) == 1


@pytest.mark.parametrize(('module', 'stage'), [
    ('tower_cloister', 1), ('temple_survey', True), ('sanctum_survey', 0), ('other', 1),
])
def test_invalid_native_stage(module, stage):
    with pytest.raises(ValueError):
        NativeOptionalSweep(object(), module, stage)


def test_rejected_registration_never_runs_or_removes_another_callback(monkeypatch):
    owner = SimpleNamespace(override_pipeline=lambda value: True,
                            register_custom_recognition=lambda name, callback: False)
    def unexpected(*args, **kwargs):
        pytest.fail('a failed registration must preserve the existing binding')
    owner.unregister_custom_recognition = unexpected
    monkeypatch.setattr(native, 'run', unexpected)
    port = NativeOptionalSweep(SimpleNamespace(_resource_holder=owner), 'sanctum_survey', 1)
    with pytest.raises(RuntimeError, match='callback_unavailable'):
        port.frame(unexpected)
