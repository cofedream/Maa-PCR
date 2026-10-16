import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from pcr_maa.ocr_diagnostics import FailureDiagnostics


def detail(image):
    return NS(name='Word', algorithm='OCR', hit=False,
              raw_detail={'all': [{'text': '回程表', 'score': .9}]}, raw_image=image)


def collector(tmp_path, image):
    sink = FailureDiagnostics(tmp_path, 'run', 'labyrinth', {'rec.onnx': 'a'*64})
    tasker = NS(get_recognition_detail=lambda rid: detail(image),
                resource=NS(get_node_data=lambda name: {'roi': [1, 1, 2, 2]}))
    sink.event('Node.Recognition.Failed', {'task_id': 12, 'reco_id': 42})
    return sink, tasker


def test_callbacks_never_touch_sdk_and_success_writes_nothing(tmp_path):
    sink, tasker = collector(tmp_path, np.zeros((4, 4, 3), np.uint8))
    sink.capture(tasker, 12, 'Root')
    sink.finish(False)
    assert not list(tmp_path.iterdir())


def test_exact_frame_copy_crop_ids_and_words(tmp_path):
    import cv2
    image = np.full((4, 4, 3), 80, np.uint8)
    sink, tasker = collector(tmp_path, image)
    sink.capture(tasker, 12, 'Root')
    image[:] = 0
    sink.finish(True)
    row = json.loads(next(tmp_path.rglob('*.json')).read_text(encoding='utf-8'))
    assert row['task_id'] == 12 and row['reco_id'] == 42
    assert row['recognition']['all'][0]['text'] == '回程表'
    frame = cv2.imread(str(next(tmp_path.rglob('*-frame.png'))))
    assert np.all(frame == 80)
    assert cv2.imread(str(next(tmp_path.rglob('*-roi.png')))).shape == (2, 2, 3)


@pytest.mark.parametrize('reason', ['evicted', 'query'])
def test_missing_original_never_rescreenshots(tmp_path, reason):
    sink, tasker = collector(tmp_path, None)
    if reason == 'query':
        def fail(rid): raise OSError('synthetic')
        tasker.get_recognition_detail = fail
    sink.capture(tasker, 12, 'Root'); sink.finish(True)
    row = json.loads(next(tmp_path.rglob('*.json')).read_text(encoding='utf-8'))
    assert row['image_status'] == 'missing' and row['image_reason']
    assert not list(tmp_path.rglob('*.png'))


def test_bounded_ids_and_pending_frames(tmp_path):
    sink, tasker = collector(tmp_path, np.zeros((4, 4, 3), np.uint8))
    for i in range(100):
        sink.event('Node.Recognition.Failed', {'task_id': 12, 'reco_id': i})
    sink.capture(tasker, 12, 'Root')
    assert len(sink.ids) == 16 and len(sink.pending) == 2


def test_quota_keeps_metadata_without_deleting_history(tmp_path, monkeypatch):
    import pcr_maa.ocr_diagnostics as module
    monkeypatch.setattr(module, 'TOTAL_BYTES', 1)
    previous = tmp_path/'old.png'; previous.write_bytes(b'keep')
    sink, tasker = collector(tmp_path, np.zeros((4, 4, 3), np.uint8))
    sink.capture(tasker, 12, 'Root'); sink.finish(True)
    row = json.loads(next(tmp_path.rglob('*.json')).read_text(encoding='utf-8'))
    assert row['image_reason'] == 'quota_exhausted'
    assert previous.read_bytes() == b'keep' and not (tmp_path/'.quota-lock').exists()


def test_real_native_failed_job_retains_original(tmp_path):
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    frame = np.full((720, 1280, 3), 113, np.uint8)
    resource = native.load_resource()
    tasker = native.bind(resource, SavedImages([[frame]]))
    sink = FailureDiagnostics(tmp_path, 'native', 'labyrinth', {}).attach(tasker)
    try:
        assert resource.override_pipeline({'MissingWord': dict(recognition='OCR', expected='^NEVER$',
            roi=[1, 1, 100, 40], only_rec=True, timeout=1, next=[], action='DoNothing')})
        receipt = native.run(tasker, 'MissingWord')
        assert receipt['native_ok'] is False
        sink.finish(True)
        rows = [json.loads(p.read_text(encoding='utf-8')) for p in tmp_path.rglob('*.json')]
        assert rows and rows[-1]['image_status'] == 'saved', rows
        assert rows[-1]['roi'] == [1,1,100,40] and 'roi' in rows[-1]['images']
    finally:
        native.stop_tasker(tasker)


@pytest.mark.parametrize('fault', ['lock', 'encode'])
def test_image_faults_keep_explicit_missing_metadata(tmp_path, monkeypatch, fault):
    import cv2
    sink, tasker = collector(tmp_path, np.zeros((4,4,3),np.uint8))
    if fault=='lock': (tmp_path/'.quota-lock').write_bytes(b'other writer')
    else: monkeypatch.setattr(cv2,'imencode',lambda *a:(False,None))
    sink.capture(tasker,12,'Root');sink.finish(True)
    row=json.loads(next(tmp_path.rglob('*.json')).read_text(encoding='utf-8'))
    assert row['image_status']=='missing' and row['image_reason'].startswith('write_')
    if fault=='lock': assert (tmp_path/'.quota-lock').read_bytes()==b'other writer'
