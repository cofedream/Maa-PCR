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


def runtime(tmp_path, monkeypatch, *, resource_timeout=False, stop_on_connect=False):
    from pcr_maa import native
    from pcr_maa.labyrinth import timing
    import maa.controller
    import maa.tasker
    events = []
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    stop = StopSignal(io.StringIO())
    owned = OwnedRuntime(request(), stop, events.append, root=tmp_path)
    metadata = tmp_path / 'assets/labyrinth/protocol-version.json'
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps(dict(content_version='123456789012')), encoding='utf-8')
    calls = []
    def resource(_):
        calls.append('resource')
        if resource_timeout:
            raise TimeoutError('synthetic resource timeout')
        return object()
    class Controller:
        def __init__(self, *args): calls.append('controller')
        def set_screenshot_target_short_side(self, size): return size == 720
        def post_connection(self):
            calls.append('connect')
            if stop_on_connect: stop.request()
            return NS(succeeded=True)
    class Tasker:
        inited = True
        def bind(self, *args): return True
        def post_stop(self):
            calls.append('native_stop')
            return NS(succeeded=True)
    class Client:
        def __init__(self, username, password, **kwargs):
            assert events[-1] == dict(kind='progress', stage='authenticating')
            assert (username, password) == ('synthetic-user', 'synthetic-password')
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
    assert owned.run() == dict(outcome='stopped', batch_complete=False)
    assert [value['stage'] for value in events] == [
        'dependencies', 'resources', 'connecting', 'authenticating', 'recovering', 'ready']
    assert calls.index('authenticate') < calls.index('restart_check') < calls.index('batch')
    assert calls.index('authenticate') < calls.index('batch')
    assert 'client_close' in calls and 'native_stop' in calls
    assert owned.request.username == owned.request.password == ''
    assert 'synthetic-user' not in json.dumps(events)
    assert 'synthetic-password' not in json.dumps(events)


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
        if name == 'SourceIniGetMaintenanceStatusRequest': return NS(required_manifest_ver='123456789012')
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


def test_launcher_emits_configuration_stage_before_runner(tmp_path, monkeypatch):
    from pcr_maa.device import validate_connection
    # The normal request validator is retained; only a synthetic executable file is used.
    adb = tmp_path / 'adb.exe'
    adb.write_bytes(b'synthetic; never execute')
    connection = validate_connection(dict(adb_path=str(adb), serial='127.0.0.1:65530'))
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
    assert seen[0].username == seen[0].password == ''
    assert 'synthetic-password' not in messages.getvalue()
    assert json.loads(messages.getvalue().splitlines()[-1]) == dict(
        kind='result', outcome='stopped', batch_complete=False)
