"""Startup progress, bounded authentication and Stop without any live device."""
import asyncio
import builtins
import io
import json
from pathlib import Path
import runpy
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace as NS

import pytest
from pcr_maa.labyrinth.config import Goal, Settings
from pcr_maa.labyrinth.owned_runtime import OwnedRequest, OwnedRuntime, StopSignal, run_owned
from pcr_maa.labyrinth import protocol


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows NumPy/stdin import ordering')
@pytest.mark.parametrize('stop_message', ['stop', 'eof'])
def test_cold_three_pipe_startup_keeps_stdin_open_then_obeys_stop(tmp_path, stop_message):
    script = tmp_path / 'cold-owner.py'
    script.write_text('''import json, sys
sys.path.insert(0, sys.argv[1])
from pcr_maa.labyrinth.owned_runtime import run_owned
assert 'numpy' not in sys.modules
output = sys.stdout
def emit(value):
    output.write(json.dumps(value) + '\\n')
    output.flush()
class Request:
    cleared = False
    def clear(self): self.cleared = True
class Runtime:
    def __init__(self, request, stop, emit):
        self.stop, self.emit = stop, emit
        self.callbacks = 0
        stop.on_stop(self.cancel)
    def cancel(self): self.callbacks += 1
    def run(self):
        import numpy as np
        assert int(np.array([1, 2]).sum()) == 3
        self.emit(dict(kind='progress', stage='numpy_ready'))
        assert self.stop.event.wait(5), 'Stop/EOF was not received'
        return dict(outcome='stopped', stop_callbacks=self.callbacks)
request = Request()
result = run_owned(request, sys.stdin, emit, runtime_factory=Runtime)
assert request.cleared
emit(dict(kind='result', cleared=request.cleared, **result))
''', encoding='utf-8')
    root = Path(__file__).resolve().parents[1]
    child = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', '-u', str(script), str(root / 'src')],
        cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW)
    lines = queue.Queue()
    reader = threading.Thread(target=lambda: lines.put(child.stdout.readline()), daemon=True)
    reader.start()
    try:
        try: ready = lines.get(timeout=10)
        except queue.Empty: pytest.fail('Cold NumPy startup blocked while stdin remained open')
        assert json.loads(ready) == dict(kind='progress', stage='numpy_ready')
        if stop_message == 'stop':
            child.stdin.write('{"kind":"stop"}\n')
            child.stdin.flush()
        else:
            child.stdin.close()
        child.wait(timeout=10)
        assert child.returncode == 0
        assert json.loads(child.stdout.readline()) == dict(kind='result', outcome='stopped',
            stop_callbacks=1, cleared=True)
        assert child.stderr.read() == ''
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        reader.join(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()


def request():
    return OwnedRequest(Settings((Goal(1, 1),)), 123,
        dict(adb_path='synthetic-adb.exe', serial='127.0.0.1:65530'),
        'synthetic-user', 'synthetic-password')


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows NumPy preload')
def test_preload_failure_clears_credentials_before_starting_stop_listener(monkeypatch, capsys):
    value = request()
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'numpy':
            print('synthetic import detail must stay private')
            raise ImportError('synthetic NumPy missing')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    monkeypatch.setattr(StopSignal, 'start', lambda _: pytest.fail('Stop listener started before failed preload'))
    factory = lambda *args: NS(run=lambda: pytest.fail('Runtime started with missing NumPy'))
    assert run_owned(value, io.StringIO(), lambda _: None, runtime_factory=factory) == dict(
        outcome='runtime_components_missing', batch_complete=False)
    assert value.username == value.password == ''
    assert capsys.readouterr().out == ''


def runtime(tmp_path, monkeypatch, *, resource_timeout=False, stop_on_connect=False, ocr_job=None, ocr_hook=None):
    from pcr_maa import native
    from pcr_maa.labyrinth import timing
    import maa.controller
    import maa.tasker
    events = []
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    stop = StopSignal(io.StringIO())
    owned = OwnedRuntime(request(), stop, events.append, root=tmp_path)
    calls = []
    def resource(_):
        calls.append('resource')
        if resource_timeout:
            raise TimeoutError('synthetic resource timeout')
        return object()
    class Controller:
        def __init__(self, *args, config):
            assert int(args[2]) == 6 and int(args[3]) == 1
            assert config == {'command': {
                'ScreencapEncode': ['{ADB}', '-s', '{ADB_SERIAL}', 'exec-out', 'screencap -p'],
                'ScreencapRawWithGzip': ['{ADB}', '-s', '{ADB_SERIAL}', 'exec-out', 'screencap | gzip -1']}}
            calls.append('controller')
        def set_screenshot_target_short_side(self, size): return size == 720
        def post_connection(self):
            calls.append('connect')
            if stop_on_connect: stop.request()
            return NS(succeeded=True)
        def post_shell(self, command, *, timeout):
            assert command == 'dumpsys package com.bilibili.priconne' and timeout == 5000
            return NS(done=True,succeeded=True,get=lambda:'Packages:\n  Package [com.bilibili.priconne] (abc):\n    versionName=11.7.2\n')
    class Tasker:
        inited = True
        def bind(self, *args): return True
        def post_recognition(self, kind, params, image):
            assert kind == 'OCR' and params.expected == ['^PCR$']
            assert params.only_rec is False and params.roi == (0, 0, 192, 64)
            assert image.shape == (64, 192, 3) and image.min() == 0 and image.max() == 255
            calls.append('ocr_prepare')
            if ocr_hook is not None: ocr_hook()
            return ocr_job if ocr_job is not None else NS(done=True, succeeded=True)
        def post_stop(self):
            calls.append('native_stop')
            return NS(succeeded=True)
    class Client:
        def __init__(self, username, password, **kwargs):
            assert events[-1] == dict(kind='progress', stage='authenticating')
            assert (username, password) == ('synthetic-user', 'synthetic-password')
            assert 'content_version' not in kwargs
            assert kwargs['app_version'] == '11.7.2'
            calls.append('client')
        def close(self): calls.append('client_close')
    monkeypatch.setattr(native, 'load_resource', resource)
    monkeypatch.setattr(native, 'wait_job', lambda job, **kwargs: job)
    monkeypatch.setattr(maa.controller, 'AdbController', Controller)
    monkeypatch.setattr(maa.tasker, 'Tasker', Tasker)
    monkeypatch.setattr(protocol, 'LabyrinthClient', Client)
    monkeypatch.setattr(timing, 'NativeTimings', lambda *args, **kwargs:
        NS(finish_native_trace=lambda: None, close=lambda: None))
    owned._authenticate = lambda binding: calls.append('authenticate')
    owned._prepare_restart = lambda journal: calls.append('restart_check')
    def batch(*args):
        assert events[-1] == dict(kind='progress', stage='ready')
        calls.append('batch')
        return dict(outcome='stopped', batch_complete=False)
    owned._run_batch = batch
    return owned, events, calls


def test_progress_reaches_ready_before_batch_and_never_contains_credentials(tmp_path, monkeypatch):
    owned, events, calls = runtime(tmp_path, monkeypatch)
    assert not (tmp_path / 'assets/labyrinth/protocol-version.json').exists()
    assert owned.run() == dict(outcome='stopped', batch_complete=False)
    assert [value['stage'] for value in events] == [
        'dependencies', 'resources', 'connecting', 'resources', 'authenticating', 'recovering', 'ready']
    assert calls.index('ocr_prepare') < calls.index('client') < calls.index('authenticate')
    assert calls.index('authenticate') < calls.index('restart_check') < calls.index('batch')
    assert calls.index('authenticate') < calls.index('batch')
    assert 'client_close' in calls and 'native_stop' in calls
    assert owned.request.username == owned.request.password == ''
    assert 'synthetic-user' not in json.dumps(events)
    assert 'synthetic-password' not in json.dumps(events)


def test_failed_ocr_preparation_stops_before_login_without_retry(tmp_path, monkeypatch):
    owned, events, calls = runtime(tmp_path, monkeypatch, ocr_job=NS(done=True, succeeded=False))
    assert owned.run() == dict(outcome='runtime_ocr_preparation_failed', batch_complete=False)
    assert calls.count('ocr_prepare') == 1 and 'native_stop' in calls
    assert not {'client', 'authenticate', 'batch'} & set(calls)
    assert events[-1] == dict(kind='progress', stage='resources')
    assert owned.request.username == owned.request.password == ''


@pytest.mark.parametrize('done', [False, True])
def test_stop_during_ocr_preparation_takes_priority_and_never_logs_in(tmp_path, monkeypatch, done):
    owned, events, calls = runtime(tmp_path, monkeypatch, ocr_job=NS(done=done, succeeded=False),
        ocr_hook=lambda: owned.stop.request())
    assert owned.run() == dict(outcome='stopped', batch_complete=False)
    assert calls.count('ocr_prepare') == 1 and 'native_stop' in calls
    assert not {'client', 'authenticate', 'batch'} & set(calls)
    assert owned.request.username == owned.request.password == ''


@pytest.mark.parametrize('completed_late', [False, True])
def test_ocr_preparation_has_own_bounded_budget_and_cleans_up(tmp_path, monkeypatch, completed_late):
    from pcr_maa.labyrinth import owned_runtime
    clock = [0.]
    class Job:
        succeeded = True
        @property
        def done(self):
            if completed_late: clock[0] = 31
            return completed_late
    owned, events, calls = runtime(tmp_path, monkeypatch, ocr_job=Job())
    original = owned._prepare_ocr
    def prepare():
        with monkeypatch.context() as patch:
            patch.setattr(owned_runtime.time, 'monotonic', lambda: clock[0])
            patch.setattr(owned_runtime.time, 'sleep', lambda _: clock.__setitem__(0, 31))
            original()
    owned._prepare_ocr = prepare
    assert owned.run() == dict(outcome='runtime_resources_timeout', batch_complete=False)
    assert calls.count('ocr_prepare') == 1 and 'native_stop' in calls
    assert not {'client', 'authenticate', 'batch'} & set(calls)
    assert owned.request.username == owned.request.password == ''


def test_expired_outer_deadline_prevents_ocr_submission(tmp_path, monkeypatch):
    from pcr_maa import native
    from pcr_maa.labyrinth.batch import BatchStopped
    owned, _, _ = runtime(tmp_path, monkeypatch)
    owned.tasker = NS(post_recognition=lambda *args: pytest.fail('expired preparation submitted native work'))
    token = native.RUN_DEADLINE.set(0)
    try:
        with pytest.raises(BatchStopped, match='^runtime_resources_timeout$'): owned._prepare_ocr()
        assert native.RUN_DEADLINE.get() == 0
    finally: native.RUN_DEADLINE.reset(token)


def test_stop_before_ocr_preparation_prevents_submission(tmp_path, monkeypatch):
    from pcr_maa.labyrinth.batch import BatchStopped
    owned, _, _ = runtime(tmp_path, monkeypatch)
    owned.stop.request()
    owned.tasker = NS(post_recognition=lambda *args: pytest.fail('stopped preparation submitted native work'))
    with pytest.raises(BatchStopped, match='^stopped$'): owned._prepare_ocr()


def test_missing_installed_version_stops_before_sdk_login_and_keeps_credentials_private(tmp_path,monkeypatch):
    from pcr_maa.labyrinth import app_version
    from pcr_maa.labyrinth.batch import BatchStopped
    owned,events,calls=runtime(tmp_path,monkeypatch)
    def missing(*args,**kw):raise BatchStopped('game_version_unavailable')
    monkeypatch.setattr(app_version,'read_installed_version',missing)
    assert owned.run()==dict(outcome='game_version_unavailable',batch_complete=False)
    assert not {'client','authenticate','batch'} & set(calls)
    assert owned.request.username==owned.request.password==''


def test_stop_after_version_read_does_not_allocate_login(tmp_path,monkeypatch):
    from pcr_maa.labyrinth import app_version
    owned,events,calls=runtime(tmp_path,monkeypatch)
    def version(*args,**kw):owned.stop.request();return '11.9.0'
    monkeypatch.setattr(app_version,'read_installed_version',version)
    assert owned.run()==dict(outcome='stopped',batch_complete=False)
    assert not {'client','authenticate','batch'} & set(calls)


def test_resource_timeout_is_reported_before_device_or_login(tmp_path, monkeypatch):
    owned, events, calls = runtime(tmp_path, monkeypatch, resource_timeout=True)
    assert owned.run() == dict(outcome='runtime_resources_timeout', batch_complete=False)
    assert calls == ['resource']
    assert [value['stage'] for value in events] == ['dependencies', 'resources']
    assert owned.request.username == owned.request.password == ''


def test_stop_while_connecting_does_not_allocate_login_or_start_batch(tmp_path, monkeypatch):
    owned, events, calls = runtime(tmp_path, monkeypatch, stop_on_connect=True)
    assert owned.run() == dict(outcome='stopped', batch_complete=False)
    assert calls == ['resource', 'controller', 'connect']
    assert [value['stage'] for value in events] == ['dependencies', 'resources', 'connecting']
    assert owned.request.username == owned.request.password == ''


def test_connection_timeout_never_allocates_login_or_starts_batch(tmp_path, monkeypatch):
    from pcr_maa import native
    owned, events, calls = runtime(tmp_path, monkeypatch)
    def expired(*args, **kwargs): raise TimeoutError('synthetic connection timeout')
    monkeypatch.setattr(native, 'wait_job', expired)
    assert owned.run() == dict(outcome='controller_connection_timeout', batch_complete=False)
    assert calls == ['resource', 'controller', 'connect']
    assert events[-1] == dict(kind='progress', stage='connecting')


def test_unknown_ledger_is_preserved_without_resource_device_or_login(tmp_path, monkeypatch):
    from pcr_maa.preset_execution import Journal
    owned, events, calls = runtime(tmp_path, monkeypatch)
    journal = Journal(tmp_path / '.runtime/labyrinth-work/ledger')
    pending = dict(state='in_flight', operation='enter', operation_id='synthetic-unknown')
    with journal.locked(): journal.append(pending)
    before = journal.path.read_bytes()
    assert owned.run() == dict(outcome='blocked_in_flight', batch_complete=False)
    assert journal.path.read_bytes() == before and not calls
    assert events == [dict(kind='progress', stage='dependencies')]


def client():
    return protocol.LabyrinthClient('synthetic-user', 'synthetic-password',
        content_version='123456789012')


def test_sdk_login_wall_timeout_closes_credentials_without_retry(monkeypatch):
    value = client()
    from autopcr_sync.core import readonly
    calls = []
    async def slow_login(*args):
        calls.append('login')
        await asyncio.Event().wait()
    monkeypatch.setattr(readonly, 'login1', slow_login)
    monkeypatch.setattr(protocol, 'SDK_LOGIN_TIMEOUT_SECONDS', .02)
    with pytest.raises(protocol.ProtocolFailure, match='^sdk_login_timeout$'):
        asyncio.run(value.login(123))
    assert calls == ['login'] and value.phase == 'closed'
    assert value.auth.username == value.auth.password == ''
    assert not value.logged_in and not value.auth.headers


def test_read_only_auth_wall_timeout_closes_session_without_retry(monkeypatch):
    value = client()
    calls = []
    async def slow_request(*args):
        calls.append('request')
        await asyncio.Event().wait()
    monkeypatch.setattr(value.auth, 'request', slow_request)
    monkeypatch.setattr(protocol, 'AUTH_REQUEST_TIMEOUT_SECONDS', .02)
    with pytest.raises(protocol.ProtocolFailure, match='^protocol_login_timeout$'):
        asyncio.run(value._auth_request(object(), 123))
    assert calls == ['request'] and value.phase == 'closed'
    assert value.auth.username == value.auth.password == ''
    assert not value.auth.headers


def test_stop_cancels_slow_sdk_login_without_retry(monkeypatch):
    value = client()
    from autopcr_sync.core import readonly
    calls = []
    async def exercise():
        entered = asyncio.Event()
        async def slow_login(*args):
            calls.append('login')
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(readonly, 'login1', slow_login)
        pending = asyncio.create_task(value.login(123))
        await entered.wait()
        value.close()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(pending, .5)
    asyncio.run(exercise())
    assert calls == ['login'] and value.phase == 'closed'
    assert value.auth.username == value.auth.password == ''


def stub_login(monkeypatch, value, maintenance):
    from autopcr_sync.core import readonly
    calls = []
    async def sdk_login(*args):
        calls.append('sdk_login')
        return dict(code=0, uid=1, access_key='synthetic-key')
    async def auth_request(request):
        calls.append(type(request).__name__)
        if calls[-1] == 'SourceIniIndexRequest':
            return NS(server=['l3-prod-all-gs-gzlj.bilibiligame.net'])
        assert calls[-1] == 'SourceIniGetMaintenanceStatusRequest'
        return maintenance
    async def game_login(uid, key, expected):
        assert value.auth.username == value.auth.password == ''
        calls.append('game_login')
    monkeypatch.setattr(readonly, 'login1', sdk_login)
    monkeypatch.setattr(value.auth, 'request', auth_request)
    monkeypatch.setattr(value, '_login_game', game_login)
    return calls


def test_server_manifest_updates_are_accepted_without_changing_local_pin(monkeypatch):
    for server_version in ('202610141431', '202610151200'):
        value = client()
        calls = stub_login(monkeypatch, value, NS(required_manifest_ver=server_version, res_ver='10002200'))
        try:
            asyncio.run(value.login(123))
            assert value.phase == 'protocol' and calls[-1] == 'game_login'
            assert value.auth.content_version == value.auth.headers['MANIFEST-VER'] == server_version
            assert calls == ['sdk_login', 'SourceIniIndexRequest',
                'SourceIniGetMaintenanceStatusRequest', 'game_login']
        finally:
            value.close()
        assert value.auth.username == value.auth.password == '' and not value.auth.headers


@pytest.mark.parametrize('server_version', [None, '', 202610151200, True, [], {},
    '２０２６１０１５１２００', '20261015120', '2026101512000', '20261015120a',
    '202610151200\n', ' 202610151200', '+202610151200'])
def test_invalid_manifest_closes_without_game_login_or_retry(monkeypatch, server_version):
    value = client()
    calls = stub_login(monkeypatch, value, NS(required_manifest_ver=server_version, res_ver='10002200'))
    with pytest.raises(protocol.ProtocolFailure, match='^protocol_version_invalid$'):
        asyncio.run(value.login(123))
    assert calls == ['sdk_login', 'SourceIniIndexRequest', 'SourceIniGetMaintenanceStatusRequest']
    assert value.phase == 'closed' and not value.logged_in
    assert value.auth.username == value.auth.password == '' and not value.auth.headers
    assert value._sdk_uid == value._sdk_access_key == ''


@pytest.mark.parametrize('field', ['required_manifest_ver', 'res_ver'])
@pytest.mark.parametrize('server_version', [202610151200, True, [], {}])
def test_wire_manifest_type_is_not_coerced_before_validation(monkeypatch, server_version, field):
    value = client()
    calls = stub_login(monkeypatch, value, None)
    from autopcr_sync.core import readonly
    monkeypatch.setattr(value.auth, 'request', readonly.ReadOnlyClient.request.__get__(value.auth))
    def post(url, **kwargs):
        calls.append(url.rsplit('/', 1)[-1])
        data = dict(server=['l3-prod-all-gs-gzlj.bilibiligame.net']) if 'index?' in url else dict(
            required_manifest_ver='202610151200', res_ver='10002200')
        if 'index?' not in url: data[field] = server_version
        return NS(status_code=200, content=json.dumps(dict(data_headers=dict(result_code=1), data=data)).encode())
    monkeypatch.setattr(readonly.requests, 'post', post)
    with pytest.raises(protocol.ProtocolFailure, match='^login_failed$'):
        asyncio.run(value.login(123))
    assert calls == ['sdk_login', 'index?format=json', 'get_maintenance_status?format=json']
    assert value.phase == 'closed' and not value.logged_in
    assert value.auth.username == value.auth.password == '' and not value.auth.headers


@pytest.mark.parametrize('version', ['1', '10002200', '10002300', '100000000', '9' * 20,
    None, '', 10002200, True, [], {}, '１０００２２００', '10002200\n', ' 10002200',
    '+10002200', '1.0', '9' * 21])
def test_resource_version_is_dynamic_and_invalid_reply_never_reaches_game_login(monkeypatch, version):
    value = client()
    calls = stub_login(monkeypatch, value, NS(required_manifest_ver='202610151200', res_ver=version))
    accepted = type(version) is str and version in ('1', '10002200', '10002300', '100000000', '9' * 20)
    if accepted:
        asyncio.run(value.login(123))
        assert value.auth.headers['RES-VER'] == version
        assert value.auth.headers['MANIFEST-VER'] == '202610151200'
        assert calls[-1] == 'game_login'
    else:
        with pytest.raises(protocol.ProtocolFailure, match='^protocol_version_invalid$'):
            asyncio.run(value.login(123))
        assert calls == ['sdk_login', 'SourceIniIndexRequest', 'SourceIniGetMaintenanceStatusRequest']
        assert value.phase == 'closed' and not value.logged_in
    value.close()
    assert value.auth.username == value.auth.password == '' and not value.auth.headers


@pytest.mark.parametrize('updates,accepted', [(1, True), (2, False)])
def test_app_version_updates_keep_existing_two_request_bound(monkeypatch, updates, accepted):
    value = protocol.LabyrinthClient('synthetic-user', 'synthetic-password')
    calls = stub_login(monkeypatch, value, None)
    from autopcr_sync.core import readonly
    monkeypatch.setattr(value.auth, 'request', readonly.ReadOnlyClient.request.__get__(value.auth))
    def post(url, **kwargs):
        name = url.rsplit('/', 1)[-1]
        calls.append(name)
        if 'index?' in name:
            data, header = dict(server=['l3-prod-all-gs-gzlj.bilibiligame.net']), dict(result_code=1)
        else:
            attempt = calls.count(name)
            data = dict(required_manifest_ver='202610151200', res_ver='10002300')
            header = dict(result_code=1)
            if attempt <= updates:
                header['store_url'] = 'https://game.bilibili.com/gzlj_11.8.0.apk'
        return NS(status_code=200, content=json.dumps(dict(data_headers=header, data=data)).encode())
    monkeypatch.setattr(readonly.requests, 'post', post)
    if accepted:
        asyncio.run(value.login(123))
        assert value.phase == 'protocol' and value.auth.headers['APP-VER'] == '11.8.0'
        assert value.auth.content_version == value.auth.headers['MANIFEST-VER'] == '202610151200'
        assert value.auth.headers['RES-VER'] == '10002300'
    else:
        with pytest.raises(protocol.ProtocolFailure, match='^login_failed$'):
            asyncio.run(value.login(123))
        assert value.phase == 'closed'
    assert calls.count('sdk_login') == 1
    assert calls.count('get_maintenance_status?format=json') == 2
    assert calls.count('game_login') == int(accepted)
    value.close()
    assert value.auth.username == value.auth.password == '' and not value.auth.headers


@pytest.mark.parametrize('captcha_wait,viewer,expected', [
    (False, 123, None), (True, 123, None), (False, 124, 'account_mismatch')])
def test_login_success_identity_and_human_wait_are_retained(monkeypatch, captcha_wait, viewer, expected):
    value = client()
    from autopcr_sync.core import readonly
    from autopcr_sync.sdk import bsgamesdk
    from pcr_maa.labyrinth import captcha
    calls = []
    async def sdk_login(*args):
        calls.append('sdk_login')
        return dict(code=200000) if captcha_wait else dict(code=0, uid=1, access_key='synthetic-key')
    async def sdk_login_after_proof(*args):
        calls.append('sdk_login_after_proof')
        return dict(code=0, uid=1, access_key='synthetic-key')
    challenge = captcha.Challenge('synthetic-gt', 'synthetic-challenge', 'synthetic-id')
    async def start_challenge(*args): return challenge
    async def human_wait(actual, stage):
        assert actual is challenge and stage == 'sdk'
        await asyncio.sleep(.04)  # Longer than network timeout; human wait keeps its own budget.
        return captcha.Proof(challenge.challenge, challenge.gt_user_id, 'synthetic-proof')
    value.manual_captcha = NS(solve=human_wait)
    async def auth_request(request):
        name = type(request).__name__
        calls.append(name)
        if name == 'SourceIniIndexRequest': return NS(server=['l3-prod-all-gs-gzlj.bilibiligame.net'])
        if name == 'SourceIniGetMaintenanceStatusRequest': return NS(required_manifest_ver='123456789012', res_ver='10002200')
        if name == 'ToolSdkLoginRequest': return NS(is_risk=False)
        if name == 'CheckGameStartRequest': return NS(now_tutorial=True)
        if name == 'HomeIndexRequest': value.auth.viewer_id = viewer
        return NS()
    monkeypatch.setattr(readonly, 'login1', sdk_login)
    monkeypatch.setattr(bsgamesdk, 'login2', sdk_login_after_proof)
    monkeypatch.setattr(captcha, 'start_challenge', start_challenge)
    monkeypatch.setattr(value.auth, 'request', auth_request)
    monkeypatch.setattr(protocol, 'SDK_LOGIN_TIMEOUT_SECONDS', .01)
    if expected:
        with pytest.raises(protocol.ProtocolFailure, match='^' + expected + '$'):
            asyncio.run(value.login(123))
        assert value.phase == 'closed' and not value.logged_in
    else:
        asyncio.run(value.login(123))
        assert value.phase == 'protocol' and value.logged_in and value.auth.viewer_id == 123
        assert value.session_generation is not None
    assert calls.count('sdk_login') == 1
    assert calls.count('sdk_login_after_proof') == int(captcha_wait)
    assert value.auth.username == value.auth.password == ''
    value.close()


@pytest.mark.parametrize('serial', ['127.0.0.1:65530', '192.168.1.4:5555'])
def test_launcher_emits_configuration_stage_before_runner(tmp_path, monkeypatch, serial):
    from pcr_maa.device import validate_connection
    # The normal request validator is retained; only a synthetic executable file is used.
    adb = tmp_path / 'adb.exe'
    adb.write_bytes(b'synthetic; never execute')
    connection = validate_connection(dict(adb_path=str(adb), serial=serial))
    messages = io.StringIO()
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(dict(version=1,
        settings=Settings((Goal(1, 1),)).payload(), account_id=123,
        connection=connection, credentials=dict(username='synthetic-user', password='synthetic-password'))) + '\n'))
    monkeypatch.setattr('sys.stdout', messages)
    launcher = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/start-labyrinth-owned-agent.py'))
    seen = []
    def runner(value, stream, emit):
        assert json.loads(messages.getvalue()) == dict(kind='progress', stage='validating')
        seen.append(value)
        return dict(outcome='stopped', batch_complete=False)
    assert launcher['main'](runner=runner) == 0
    assert seen[0].connection == connection and seen[0].connection['serial'] == serial
    assert seen[0].username == seen[0].password == ''
    assert 'synthetic-password' not in messages.getvalue()
    assert json.loads(messages.getvalue().splitlines()[-1]) == dict(
        kind='result', outcome='stopped', batch_complete=False)


def test_timing_read_failure_preserves_business_result_and_cleanup(tmp_path, monkeypatch):
    from pcr_maa.labyrinth import timing
    owned, events, calls = runtime(tmp_path, monkeypatch)
    def unreadable():
        raise OSError('synthetic trace read failure')
    monkeypatch.setattr(timing, 'NativeTimings', lambda *args, **kwargs:
        NS(finish_native_trace=unreadable, close=lambda: calls.append('timings_closed')))
    expected = dict(outcome='completed', batch_complete=True)
    owned._run_batch = lambda *args: dict(expected)
    assert owned.run() == expected
    assert owned.timing_collection == 'incomplete'
    assert owned.timings.phase is None
    assert {'timings_closed', 'native_stop', 'client_close'} <= set(calls)
    assert owned.request.username == owned.request.password == ''


def test_timing_sink_close_failure_preserves_result_and_client_cleanup(tmp_path, monkeypatch):
    from pcr_maa.labyrinth import timing
    owned, events, calls = runtime(tmp_path, monkeypatch)
    def broken_close():
        raise OSError('synthetic sink cleanup failure')
    monkeypatch.setattr(timing, 'NativeTimings', lambda *args, **kwargs:
        NS(finish_native_trace=lambda: None, close=broken_close))
    expected = dict(outcome='completed', batch_complete=True)
    owned._run_batch = lambda *args: dict(expected)
    assert owned.run() == expected
    assert owned.timing_collection == 'incomplete'
    assert {'native_stop', 'client_close'} <= set(calls)
    assert owned.request.username == owned.request.password == ''


@pytest.mark.parametrize('failing_call', [1, 2])
def test_one_time_timing_transition_failure_preserves_result_and_cleanup(tmp_path, monkeypatch, failing_call):
    owned, events, calls = runtime(tmp_path, monkeypatch)
    expected = dict(outcome='complete', completed={1: 1}, batch_complete=True)
    owned._run_batch = lambda *args: dict(expected)
    normal_transition = owned.timings.transition
    end_calls = []
    def transition(phase):
        if phase is None:
            end_calls.append(phase)
            if len(end_calls) == failing_call:
                raise RuntimeError('synthetic one-time timing transition failure')
        return normal_transition(phase)
    monkeypatch.setattr(owned.timings, 'transition', transition)
    assert owned.run() == expected
    assert len(end_calls) == 2 and owned.timings.phase is None
    assert owned.timing_collection == 'incomplete' and owned.ocr_success
    assert calls.count('native_stop') == calls.count('client_close') == 1
    assert owned.request.username == owned.request.password == ''


def test_persistent_timing_transition_failure_preserves_result_and_all_cleanup(tmp_path, monkeypatch):
    from pcr_maa.labyrinth import timing
    owned, events, calls = runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(timing, 'NativeTimings', lambda *args, **kwargs: NS(
        finish_native_trace=lambda: calls.append('timings_finished'),
        close=lambda: calls.append('timings_closed')))
    expected = dict(outcome='complete', completed={1: 1}, batch_complete=True)
    owned._run_batch = lambda *args: dict(expected)
    normal_transition = owned.timings.transition
    end_calls = []
    def transition(phase):
        if phase is None:
            end_calls.append(phase)
            raise OSError('synthetic persistent timing transition failure')
        return normal_transition(phase)
    monkeypatch.setattr(owned.timings, 'transition', transition)
    assert owned.run() == expected
    assert len(end_calls) == 2
    assert owned.timing_collection == 'incomplete' and owned.ocr_success
    assert all(calls.count(name) == 1 for name in (
        'timings_finished', 'timings_closed', 'native_stop', 'client_close'))
    assert owned.request.username == owned.request.password == ''


@pytest.mark.parametrize('error_type', [KeyboardInterrupt, asyncio.CancelledError])
def test_timing_finalization_does_not_swallow_interruption(tmp_path, monkeypatch, error_type):
    owned, events, calls = runtime(tmp_path, monkeypatch)
    def interrupted(phase):
        raise error_type()
    monkeypatch.setattr(owned.timings, 'transition', interrupted)
    with pytest.raises(error_type):
        owned._finish_timings()
