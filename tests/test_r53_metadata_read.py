"""The metadata adapter returns the old receipt fields or fails as a whole."""
import json
from types import SimpleNamespace

import pytest
from maa.define import TaskDetail
from maa.library import Library
from maa.tasker import Tasker

from pcr_maa import player_log, recognition_metadata as metadata


@pytest.fixture
def reader(monkeypatch):
    owner = Tasker.__new__(Tasker)
    owner._handle, owner._own = 1, False
    detail = TaskDetail(11, 'Read', [101], None)
    calls = []
    raw = {'all': [{'text': '123', 'score': .99}], 'best': {'text': '123'}}
    state = dict(node_ok=True, reco_ok=True, raw=json.dumps(raw), rid=202, aid=303)
    def text(handle, value):
        encoded = value.encode()
        assert Library.framework().MaaStringBufferSetEx(handle, encoded, len(encoded))
    class API:
        def MaaTaskerGetNodeDetail(self, handle, node_id, name, rid, aid, done):
            calls.append(('node', node_id.value))
            text(name, 'Read')
            rid.contents.value = state['rid']
            aid.contents.value = state['aid']
            done.contents.value = True
            return state['node_ok']
        def MaaTaskerGetRecognitionDetail(self, handle, rid, name, algorithm, hit, box, raw, image, draws):
            calls.append(('recognition', rid.value))
            assert image is None and draws is None
            text(name, 'Read')
            text(algorithm, 'OCR')
            text(raw, state['raw'])
            return state['reco_ok']
    monkeypatch.setattr(metadata, '_api', lambda:API())
    def action(self, aid):
        calls.append(('action', aid))
        return SimpleNamespace(name='Read', success=True, raw_detail={'action':'DoNothing'})
    monkeypatch.setattr(Tasker, 'get_action_detail', action)
    return owner, detail, calls, state, raw


def test_raw_receipt_fields_equal_without_image_outputs(reader):
    owner, detail, calls, _, raw = reader
    checks=[]
    result=metadata.read_receipt_nodes(owner, detail, check=lambda:checks.append(True))
    assert result == [dict(name='Read', completed=True,
        action=dict(name='Read',success=True,raw={'action':'DoNothing'}),recognition=raw)]
    assert calls == [('node',101),('recognition',202),('action',303)]
    assert len(checks)==7
    assert detail._nodes is None  # SDK's full image-bearing cache is not populated.


@pytest.mark.parametrize('failure', ['node','recognition','action','bad_json'])
def test_missing_or_malformed_detail_never_returns_partial_receipt(reader, monkeypatch, failure):
    owner, detail, calls, state, _=reader
    if failure=='node': state['node_ok']=False
    elif failure=='recognition': state['reco_ok']=False
    elif failure=='action': monkeypatch.setattr(Tasker,'get_action_detail',lambda *args:None)
    elif failure=='bad_json': state['raw']='{'
    with pytest.raises((RuntimeError, json.JSONDecodeError)):
        metadata.read_receipt_nodes(owner,detail,check=lambda:None)
    assert calls.count(('node',101))==1
    assert calls.count(('recognition',202))<=1


@pytest.mark.parametrize('raw',[None,[],[{'reco_id':42,'detail':{'hit':True}}]])
def test_direct_hit_and_composite_raw_json_are_preserved(reader,raw):
    owner,detail,_,state,_=reader
    state['raw']=json.dumps(raw)
    assert metadata.read_receipt_nodes(owner,detail,check=lambda:None)[0]['recognition']==raw


@pytest.mark.parametrize('failure_check',range(1,8))
def test_stop_expiry_or_revocation_before_after_every_call_aborts(reader,failure_check):
    owner,detail,calls,_,_=reader
    checks=0
    def check():
        nonlocal checks
        checks+=1
        if checks==failure_check: raise RuntimeError('scope stopped/expired/revoked')
    with pytest.raises(RuntimeError,match='scope stopped'):
        metadata.read_receipt_nodes(owner,detail,check=check)
    assert calls.count(('node',101))<=1
    assert calls.count(('recognition',202))<=1
    assert calls.count(('action',303))<=1


def test_no_recognition_or_action_is_preserved(reader):
    owner,detail,calls,state,_=reader
    state.update(rid=0,aid=0)
    assert metadata.read_receipt_nodes(owner,detail,check=lambda:None)==[
        dict(name='Read',completed=True,recognition=None,action=None)]
    assert calls==[('node',101)]


@pytest.mark.parametrize('node_id',[0,-1,True,'101'])
def test_invalid_native_identity_refuses_before_query(reader,node_id):
    owner,detail,calls,_,_=reader
    detail.node_id_list=[node_id]
    with pytest.raises(RuntimeError,match='identity'):
        metadata.read_receipt_nodes(owner,detail,check=lambda:None)
    assert calls==[]


def test_version_drift_leaves_optimized_reader_unavailable(monkeypatch):
    metadata._api.cache_clear()
    monkeypatch.setattr(Library,'version',lambda:'v5.14.0')
    try:
        assert metadata._api() is None
    finally:
        metadata._api.cache_clear()


_PATH_MARKER = object()


@pytest.mark.parametrize(('loaded', 'versions', 'message'), [
    ('v5.16.1', (), 'DLL v5.16.1，包版本 无，缺少有效的包版本元数据'),
    ('v5.16.1', (None,), 'DLL v5.16.1，包版本 无，缺少有效的包版本元数据'),
    ('v5.16.1', ('5.13.0', '5.16.1'), 'DLL v5.16.1，包版本 5.13.0、5.16.1，发现多个包版本'),
    ('v5.16.1', ('5.13.0',), 'DLL v5.16.1，包版本 5.13.0，DLL 与包版本不一致'),
    ('v5.16.1', ('5.16.1', _PATH_MARKER),
     'DLL v5.16.1，包版本 5.16.1，包元数据含无效版本字段'),
    (_PATH_MARKER, ('5.16.1',), 'DLL 未知，包版本 5.16.1，DLL 版本不受支持'),
    ('v5.16.1', PermissionError,
     'DLL v5.16.1，包版本 未知，包元数据无法读取'),
    ('v5.16.1', ('5.16.1', None), None),
])
def test_package_metadata_logs_only_sanitized_fallback_reason(monkeypatch, tmp_path, loaded, versions, message):
    rows = []
    player_log.configure(tmp_path, 'b' * 32, 'labyrinth', rows.append)
    unexpected_value = str(tmp_path / 'unexpected-metadata')

    def version(value):
        return unexpected_value if value is _PATH_MARKER else value

    monkeypatch.setattr(Library, 'version', lambda: version(loaded))

    def packages(*, name):
        if versions is PermissionError:
            raise PermissionError(unexpected_value)
        return [SimpleNamespace(version=version(value)) for value in versions]

    monkeypatch.setattr(metadata, 'distributions', packages)
    metadata._api.cache_clear()
    try:
        assert (metadata._api() is None) == (message is not None)
        metadata._api()
    finally:
        player_log.close()
        metadata._api.cache_clear()
    if message is None:
        assert rows == []
    else:
        assert rows == [dict(kind='player_log', run_id='b' * 32, function='labyrinth', event='text',
                            text=f'MaaFramework 回执使用常规 SDK（{message}）。')]


def test_unrelated_facade_refuses(reader):
    _,detail,calls,_,_=reader
    with pytest.raises(TypeError):
        metadata.read_receipt_nodes(SimpleNamespace(),detail,check=lambda:None)
    assert calls==[]
