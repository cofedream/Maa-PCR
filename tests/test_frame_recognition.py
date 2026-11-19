from types import SimpleNamespace

import numpy as np
import pytest
from maa.custom_recognition import CustomRecognition

from pcr_maa import frame_recognition, native
from pcr_maa.replay import SavedImages


@pytest.mark.parametrize(('loaded', 'bindings', 'supported'), [
    ('v5.13.0', ('5.13.0',), True), ('v5.16.1', ('v5.16.1',), True),
    ('v5.16.1', (None, 'v5.16.1'), True),
    ('v5.16.1', (None,), False), ('v5.16.1', (), False),
    ('v5.16.1', ('5.13.0', '5.16.1'), False),
    ('v5.16.1', ('5.13.0',), False), ('v5.17.0', ('5.17.0',), False),
])
def test_native_binding_must_match(monkeypatch, loaded, bindings, supported):
    from pcr_maa import recognition_metadata as metadata
    monkeypatch.setattr(metadata.Library, 'version', lambda: loaded)
    monkeypatch.setattr(metadata, 'distributions',
                        lambda *, name: [SimpleNamespace(version=value) for value in bindings])
    assert metadata.supports_metadata_binding() is supported


def test_one_upload_per_callback_with_fresh_overrides_and_crops(monkeypatch):
    uploads = []

    class CountedBuffer(frame_recognition.ImageBuffer):
        def set(self, image):
            uploads.append(int(image[0, 0, 0]))
            return super().set(image)

    monkeypatch.setattr(frame_recognition, 'ImageBuffer', CountedBuffer)
    observed = []

    class Probe(CustomRecognition):
        def analyze(self, context, argv):
            frame = argv.image
            reader = frame_recognition.same_frame_context(context, frame)
            white = reader.run_recognition('WhitePixels', frame)
            black = reader.run_recognition('WhitePixels', frame, {
                'WhitePixels': dict(lower=[0, 0, 0], upper=[0, 0, 0])})
            crop = 255 - frame[:16, :16]
            other = reader.run_recognition('WhitePixels', crop)
            # The ordinary SDK receipt is still available under the same native ID.
            sdk = context.tasker.get_recognition_detail(white.reco_id)
            observed.append((white.hit, black.hit, other.hit,
                             white.reco_id != black.reco_id, white.raw_detail == sdk.raw_detail))
            return self.AnalyzeResult([0, 0, 1, 1], {})

    resource = native.load_resource(cpu_only=True)
    assert resource.register_custom_recognition('FrameProbe', Probe())
    assert resource.override_pipeline({
        'WhitePixels': dict(recognition='ColorMatch', roi=[0, 0, 8, 8], method=4,
                            lower=[255, 255, 255], upper=[255, 255, 255], count=16),
        'FrameProbe': dict(recognition='Custom', custom_recognition='FrameProbe', action='DoNothing',
                           timeout=0, pre_delay=0, post_delay=0, next=[]),
    })
    white = np.full((720, 1280, 3), 255, dtype=np.uint8)
    controller = SavedImages([[white], [255 - white]])
    owner = native.bind(resource, controller)
    try:
        for stage in (0, 1):
            controller.stage = stage
            assert native.run(owner, 'FrameProbe')['native_ok']
        assert uploads == [255, 0]
        assert observed == [(True, False, False, True, True), (False, True, True, True, True)]
        assert not controller.clicks and not controller.swipes
    finally:
        assert native.stop_tasker(owner)['idle']
