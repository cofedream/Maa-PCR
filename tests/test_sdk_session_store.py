import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest
from pcr_maa import sdk_session_store as storage
from pcr_maa.labyrinth.protocol import LabyrinthClient, ProtocolFailure


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Deterministic reversible cipher only for offline flow tests; DPAPI has a separate native test.
    monkeypatch.setattr(storage, '_dpapi', lambda data, **kw: bytes(b ^ 0x55 for b in data))
    return storage.SdkSessionStore(tmp_path, 'cn-bilibili', 123, 'user', 'password')


def stub_client(tmp_path, monkeypatch, *, fail=None, viewer=123):
    value = LabyrinthClient('user', 'password', cache_root=tmp_path)
    from autopcr_sync.core import readonly
    calls = []
    async def sdk(*args):
        calls.append('password')
        return dict(code=0, uid=2, access_key='new-key')
    async def request(req):
        name = type(req).__name__
        calls.append(name)
        if name == 'SourceIniIndexRequest': return NS(server=['l3-prod-all-gs-gzlj.bilibiligame.net'])
        if name == 'SourceIniGetMaintenanceStatusRequest': return NS(required_manifest_ver='202610141431', res_ver='10002200')
        if name == 'ToolSdkLoginRequest':
            if fail: fail(req)
            value.auth.viewer_id = viewer
            return NS(is_risk=False)
        if name == 'CheckGameStartRequest': return NS(now_tutorial=True)
        return NS()
    monkeypatch.setattr(readonly, 'login1', sdk)
    monkeypatch.setattr(value.auth, 'request', request)
    return value, calls


def test_save_and_reuse_across_new_clients(store, tmp_path, monkeypatch):
    first, calls = stub_client(tmp_path, monkeypatch)
    asyncio.run(first.login(123)); first.close()
    assert calls.count('password') == 1 and store.read() == ('2', 'new-key')
    second, calls = stub_client(tmp_path, monkeypatch)
    asyncio.run(second.login(123)); second.close()
    assert 'password' not in calls and calls.count('ToolSdkLoginRequest') == 1
    assert b'new-key' not in store.path.read_bytes() and b'password' not in store.path.read_bytes()


@pytest.mark.parametrize('always_reject', [False, True])
def test_explicit_sdk_rejection_refreshes_only_once(store, tmp_path, monkeypatch, always_reject):
    from autopcr_sync.core.readonly import SdkSessionRejected
    assert store.save('1', 'old-key')
    def reject(req):
        if req.uid == '1' or always_reject: raise SdkSessionRejected('sdk_session_rejected')
    client, calls = stub_client(tmp_path, monkeypatch, fail=reject)
    if always_reject:
        with pytest.raises(ProtocolFailure): asyncio.run(client.login(123))
        assert not store.path.exists()
    else:
        asyncio.run(client.login(123))
        assert store.read() == ('2', 'new-key')
    assert calls.count('password') == 1 and calls.count('ToolSdkLoginRequest') == 2
    client.close()


@pytest.mark.parametrize('reason', ['network_failed', 'server_maintenance', 'invalid_response', 'game_request_failed'])
def test_other_failures_keep_cache_and_never_send_password(store, tmp_path, monkeypatch, reason):
    from autopcr_sync.core.readonly import SyncFailure
    store.save('1', 'old-key'); original = store.path.read_bytes()
    def fail(req): raise SyncFailure(reason)
    client, calls = stub_client(tmp_path, monkeypatch, fail=fail)
    with pytest.raises(ProtocolFailure): asyncio.run(client.login(123))
    assert 'password' not in calls and store.path.read_bytes() == original


def test_wrong_identity_never_retries_or_overwrites_cache(store, tmp_path, monkeypatch):
    store.save('1', 'old-key'); original = store.path.read_bytes()
    client, calls = stub_client(tmp_path, monkeypatch, viewer=124)
    with pytest.raises(ProtocolFailure, match='account_mismatch'): asyncio.run(client.login(123))
    assert 'password' not in calls and store.path.read_bytes() == original


def test_cached_session_still_uses_manual_game_captcha(store, tmp_path, monkeypatch):
    from pcr_maa.labyrinth.captcha import Proof
    store.save('1', 'old-key')
    client, calls = stub_client(tmp_path, monkeypatch)
    original = client.auth.request
    async def request(req):
        result = await original(req)
        if type(req).__name__ == 'ToolSdkLoginRequest': result.is_risk = not req.dict(by_alias=True).get('validate')
        return result
    async def proof(stage):
        assert stage == 'game'
        calls.append('manual-game')
        return Proof('synthetic-challenge','synthetic-user','synthetic-proof')
    monkeypatch.setattr(client.auth, 'request', request)
    monkeypatch.setattr(client, '_verify_captcha', proof)
    asyncio.run(client.login(123)); client.close()
    assert 'password' not in calls and calls.count('manual-game') == 1
    assert calls.count('ToolSdkLoginRequest') == 2 and store.read() == ('1','old-key')


def test_bindings_and_corrupt_ciphertext_are_cache_misses(store, tmp_path):
    store.save('1', 'old-key')
    for server, account, user, password in [('cn-huawei',123,'user','password'),
            ('cn-bilibili',124,'user','password'), ('cn-bilibili',123,'other','password'),
            ('cn-bilibili',123,'user','changed')]:
        assert storage.SdkSessionStore(tmp_path, server, account, user, password).read() is None
    store.path.write_bytes(b'corrupt')
    assert store.read() is None


def test_unavailable_encryption_does_not_write_plaintext(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs): raise OSError('unavailable')
    monkeypatch.setattr(storage, '_dpapi', unavailable)
    store = storage.SdkSessionStore(tmp_path, 'cn-bilibili', 123, 'user', 'password')
    assert store.save('1', 'private-key') is False and not store.path.exists()
    client, calls = stub_client(tmp_path, monkeypatch)
    asyncio.run(client.login(123)); client.close()
    assert calls.count('password') == 1 and not store.path.exists()


def test_stop_during_cached_login_never_refreshes(store, tmp_path, monkeypatch):
    store.save('1', 'old-key'); original = store.path.read_bytes()
    client, calls = stub_client(tmp_path, monkeypatch)
    async def exercise():
        entered = asyncio.Event()
        async def wait(*args):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(client, '_login_game', wait)
        task = asyncio.create_task(client.login(123))
        await entered.wait(); client.close()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(exercise())
    assert 'password' not in calls and store.path.read_bytes() == original


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_real_dpapi_cross_process_and_ciphertext_integrity(tmp_path):
    store = storage.SdkSessionStore(tmp_path, 'cn-bilibili', 123, 'synthetic-user', 'synthetic-password')
    assert store.save('123', 'synthetic-sdk-token')
    encrypted = store.path.read_bytes()
    assert all(v not in encrypted for v in (b'synthetic-sdk-token', b'synthetic-password', b'access_key'))
    code = "from pcr_maa.sdk_session_store import SdkSessionStore; import sys; s=SdkSessionStore(sys.argv[1], 'cn-bilibili', 123, 'synthetic-user', 'synthetic-password'); assert s.read()==('123','synthetic-sdk-token'); print('cross_process_ok')"
    env = dict(os.environ, PYTHONPATH=str(__import__('pathlib').Path(storage.__file__).parents[1]))
    result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', code, str(tmp_path)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'cross_process_ok'
    store.path.write_bytes(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
    assert store.read() is None


@pytest.mark.parametrize('request_name,message,expected', [
    ('ToolSdkLoginRequest', '令牌已失效', 'sdk_session_rejected'),
    ('ToolSdkLoginRequest', '维护中', 'server_maintenance'),
    ('LoadIndexRequest', '令牌已失效', 'game_request_failed')])
def test_wire_classifies_only_explicit_sdk_failure(monkeypatch, request_name, message, expected):
    client = LabyrinthClient('user','password')
    from autopcr_sync.core import readonly
    from autopcr_sync.model.sdkrequests import ToolSdkLoginRequest
    from autopcr_sync.model.requests import LoadIndexRequest
    request = ToolSdkLoginRequest(uid='1',access_key='secret') if request_name == 'ToolSdkLoginRequest' else LoadIndexRequest()
    payload = dict(data_headers=dict(result_code=2), data=dict(server_error=dict(message=message)))
    monkeypatch.setattr(client.auth, 'unpack', lambda _: payload)
    monkeypatch.setattr(readonly.requests, 'post', lambda *a, **kw: NS(status_code=200,content=b'response'))
    with pytest.raises(readonly.SyncFailure, match='^'+expected+'$') as error:
        asyncio.run(client.auth.request(request))
    assert isinstance(error.value, readonly.SdkSessionRejected) == (expected == 'sdk_session_rejected')
    client.close()
