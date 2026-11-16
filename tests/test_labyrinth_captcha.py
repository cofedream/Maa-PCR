"""Human captcha round trips and SDK forms; synthetic proofs, no game requests."""
import asyncio
import hashlib
import json
from types import SimpleNamespace as NS
from urllib.error import HTTPError
from urllib.parse import parse_qs
from urllib.request import Request, urlopen

import pytest

from pcr_maa.labyrinth.captcha import CaptchaFailure, CaptchaPage, Challenge, Proof
from pcr_maa.labyrinth.protocol import LabyrinthClient, ProtocolFailure


def post(page, body, *, origin=None, action='complete'):
    request = Request(page.url + action, json.dumps(body).encode(), headers={
        'Content-Type': 'application/json', 'Origin': origin or page.origin})
    return urlopen(request, timeout=2)


def test_captcha_forwards_browser_challenge_and_remains_one_use():
    with CaptchaPage(Challenge('synthetic-gt', 'initial-challenge', 'synthetic-user')) as page:
        with urlopen(page.url, timeout=2) as response:
            html = response.read().decode()
        assert 'challenge:result.geetest_challenge' in html
        with post(page, {'challenge': 'completed-challenge', 'validate': 'synthetic-proof'}) as response:
            assert response.status == 200
        proof = page.proof()
        assert proof.challenge == 'completed-challenge'
        assert proof.gt_user_id == 'synthetic-user' and proof.validate == 'synthetic-proof'
        with pytest.raises(HTTPError) as error:
            post(page, {'challenge': 'second-challenge', 'validate': 'second-proof'})
        assert error.value.code == 409
    assert page.value is None and page.challenge is None and not page.thread.is_alive()


@pytest.mark.parametrize('body', [
    {'validate': 'proof'}, {'challenge': '', 'validate': 'proof'},
    {'challenge': '<script>', 'validate': 'proof'}, {'challenge': 123, 'validate': 'proof'},
    {'challenge': 'a' * 513, 'validate': 'proof'}, {'challenge': 'final', 'validate': ''},
    {'challenge': 'final', 'validate': 'proof', 'gt_user_id': 'another-user'},
])
def test_invalid_completion_does_not_consume_captcha(body):
    with CaptchaPage(Challenge('gt', 'initial', 'user')) as page:
        with pytest.raises(HTTPError) as error:
            post(page, body)
        assert error.value.code == 400 and not page.ready.is_set()
        with post(page, {}, action='cancel') as response:
            assert response.status == 200
        with pytest.raises(CaptchaFailure, match='^captcha_cancelled$'):
            page.proof()


def test_captcha_rejects_other_origin_and_other_session():
    with CaptchaPage(Challenge('gt', 'initial', 'user')) as page:
        with pytest.raises(HTTPError) as error:
            post(page, {'challenge': 'final', 'validate': 'proof'}, origin='http://example.invalid')
        assert error.value.code == 403 and not page.ready.is_set()
        with pytest.raises(CaptchaFailure, match='^captcha_proof_invalid$'):
            Proof('final', 'another-user', 'proof').checked(page.challenge)


@pytest.mark.parametrize('stage', ['sdk', 'game'])
def test_completed_challenge_reaches_both_login_paths(monkeypatch, stage):
    client = LabyrinthClient('synthetic-user', 'synthetic-password')
    from autopcr_sync.core import readonly
    from autopcr_sync.sdk import bsgamesdk
    from pcr_maa.labyrinth import captcha
    challenge = Challenge('gt', 'initial', 'user')
    async def start(*args): return challenge
    async def solve(actual, actual_stage):
        assert actual is challenge and actual_stage == stage
        return Proof('completed', 'user', 'proof')
    monkeypatch.setattr(captcha, 'start_challenge', start)
    client.manual_captcha = NS(solve=solve)
    calls = []
    async def login1(*args):
        calls.append('login1')
        return {'code': 200000}
    async def login2(username, password, challenge, user, validate):
        calls.append('login2')
        assert (challenge, user, validate) == ('completed', 'user', 'proof')
        return {'code': 0, 'uid': 1, 'access_key': 'synthetic-key'}
    async def auth(request, expected):
        name = type(request).__name__
        calls.append(name)
        if name == 'ToolSdkLoginRequest':
            if calls.count(name) == 1: return NS(is_risk=True)
            assert request.challenge == 'completed' and request.validate_ == 'proof'
            assert request.seccode == 'proof|jordan'
            return NS(is_risk=False)
        if name == 'CheckGameStartRequest': return NS(now_tutorial=True)
        if name == 'HomeIndexRequest': client.auth.viewer_id = expected
        return NS()
    monkeypatch.setattr(readonly, 'login1', login1)
    monkeypatch.setattr(bsgamesdk, 'login2', login2)
    monkeypatch.setattr(client, '_auth_request', auth)
    try:
        if stage == 'sdk':
            assert asyncio.run(client._password_login('synthetic-user', 'synthetic-password')) == ('1', 'synthetic-key')
            assert calls == ['login1', 'login2']
        else:
            asyncio.run(client._login_game('1', 'synthetic-key', 123))
            assert calls.count('ToolSdkLoginRequest') == 2
    finally:
        client.close()


@pytest.mark.parametrize('failed_step,reason', [
    ('sdk_proof', '未取得可用响应'), ('sdk_proof', 'sdk_request_failed'),
    ('game_session', 'server_maintenance'), ('game_proof', 'sdk_session_rejected')])
def test_login_failure_after_manual_proof_records_only_safe_request_details(monkeypatch, failed_step, reason):
    from pcr_maa import player_log
    client = LabyrinthClient('private-user', 'private-password')
    from autopcr_sync.core import readonly
    from autopcr_sync.sdk import bsgamesdk
    lines = []
    monkeypatch.setattr(player_log, 'text', lines.append)
    async def login1(*args):
        if failed_step == 'game_proof':
            return {'code': 0, 'uid': 1, 'access_key': 'private-key'}
        return {'code': 200000}
    async def verify(stage):
        assert stage == ('game' if failed_step == 'game_proof' else 'sdk')
        return Proof('private-challenge', 'private-user', 'private-proof')
    async def login2(*args):
        if failed_step == 'sdk_proof':
            if reason == 'sdk_request_failed':
                raise RuntimeError('sdk_request_failed')
            raise ValueError('private-response')
        return {'code': 0, 'uid': 1, 'access_key': 'private-key'}
    async def request(req):
        if type(req).__name__ == 'SourceIniIndexRequest':
            return NS(server=['l3-prod-all-gs-gzlj.bilibiligame.net'])
        if type(req).__name__ == 'SourceIniGetMaintenanceStatusRequest':
            return NS(required_manifest_ver='202610141431', res_ver='10002200')
        assert type(req).__name__ == 'ToolSdkLoginRequest'
        if failed_step == 'game_proof':
            if req.validate_ is None:
                return NS(is_risk=True)
            raise readonly.SdkSessionRejected('sdk_session_rejected')
        raise readonly.SyncFailure('server_maintenance')
    monkeypatch.setattr(readonly, 'login1', login1)
    monkeypatch.setattr(bsgamesdk, 'login2', login2)
    monkeypatch.setattr(client, '_verify_captcha', verify)
    monkeypatch.setattr(client.auth, 'request', request)
    outcome = 'sdk_login_failed' if failed_step == 'game_proof' else 'login_failed'
    with pytest.raises(ProtocolFailure, match=f'^{outcome}$'):
        asyncio.run(client.login(123))
    expected = {'sdk_proof': '提交账号验证码', 'game_proof': '提交游戏验证码', 'game_session': 'ToolSdkLoginRequest'}[failed_step]
    assert any(expected in line and reason in line for line in lines)
    assert 'private-' not in repr(lines) and client.phase == 'closed'


@pytest.mark.parametrize('with_proof', [False, True])
def test_sdk_login_form_has_unique_encoded_values_and_original_value_signature(monkeypatch, with_proof):
    client = LabyrinthClient('synthetic-user', 'synthetic-password')
    from autopcr_sync.sdk import bsgamesdk
    calls = []
    async def send(url, data):
        fields = parse_qs(data.decode() if isinstance(data, bytes) else data, keep_blank_values=True)
        assert all(len(values) == 1 for values in fields.values())
        values = {key: items[0] for key, items in fields.items()}
        sign = values.pop('sign')
        expected = ''.join(values[key] for key in sorted(values)) + 'fe8aac4e02f845b8ad67c427d48bfaf1'
        assert sign == hashlib.md5(expected.encode()).hexdigest()
        calls.append(url.rsplit('/', 1)[-1])
        if calls[-1] == 'rsa': return {'rsa_key': 'synthetic-key', 'hash': 'synthetic-hash'}
        assert values['pwd'] == 'synthetic+/cipher=='
        assert values['user_id'] == '测试+name&field=value@example.invalid'
        assert values['validate'] == ('proof' if with_proof else '')
        assert values['seccode'] == ('proof|jordan' if with_proof else '')
        return {'code': 0, 'uid': 1, 'access_key': 'synthetic-key'}
    monkeypatch.setattr(bsgamesdk, 'sendpost', send)
    monkeypatch.setattr(bsgamesdk.rsacr, 'rsacreate', lambda *args: 'synthetic+/cipher==')
    try:
        args = ('测试+name&field=value@example.invalid', 'synthetic-password')
        operation = bsgamesdk.login2(*args, 'final', 'user', 'proof') if with_proof else bsgamesdk.login1(*args)
        assert asyncio.run(operation)['code'] == 0
        assert calls == ['rsa', 'login']
    finally:
        client.close()


@pytest.mark.parametrize('step,statuses,headers,success', [
    ('index', [502, 200], {}, True), ('maintenance', [503, 200], {}, True),
    ('maintenance', [504, 504], {}, False), ('index', [503], {'Retry-After': '60'}, False),
    ('index', [403], {}, False), ('game', [502], {}, False)])
def test_public_metadata_gateway_failure_after_captcha(monkeypatch, step, statuses, headers, success):
    import base64
    import msgpack
    from pcr_maa.labyrinth import captcha
    client = LabyrinthClient('fixture-user', 'fixture-password')
    from autopcr_sync.core import readonly
    from autopcr_sync.sdk import bsgamesdk
    calls = []
    async def login1(*args):
        calls.append('password')
        return {'code': 200000}
    async def start(_):
        return Challenge('fixture-gt', 'fixture-initial', 'fixture-user')
    async def solve(challenge, stage):
        assert stage == 'sdk'
        calls.append('manual')
        return Proof('fixture-completed', challenge.gt_user_id, 'fixture-proof')
    async def login2(*args):
        calls.append('submit')
        return {'code': 0, 'uid': '1', 'access_key': 'fixture-key'}
    paths = {'source_ini/index': 'index', 'source_ini/get_maintenance_status': 'maintenance',
             'tool/sdk_login': 'game', 'check/game_start': 'start', 'load/index': 'load', 'home/index': 'home'}
    def post(url, **kwargs):
        from urllib.parse import urlsplit
        name = paths[urlsplit(url).path.lstrip('/')]
        calls.append(name)
        status = statuses[calls.count(name)-1] if name == step else 200
        if status != 200:
            return NS(status_code=status, headers=headers, content=b'')
        data = {'index': {'server': ['l3-prod-all-gs-gzlj.bilibiligame.net']},
                'maintenance': {'required_manifest_ver': '202611131613', 'res_ver': '10002200'},
                'game': {'is_risk': False}, 'start': {'now_tutorial': True}, 'load': {}, 'home': {}}[name]
        payload = {'data_headers': {'result_code': 1, 'viewer_id': '123' if name == 'home' else '0'}, 'data': data}
        if name in ('index', 'maintenance'):
            content = json.dumps(payload).encode()
        else:
            content = base64.b64encode(client.auth.encrypt(msgpack.packb(payload), b'0'*32))
        return NS(status_code=200, headers={}, content=content)
    monkeypatch.setattr(readonly, 'login1', login1)
    monkeypatch.setattr(bsgamesdk, 'login2', login2)
    monkeypatch.setattr(captcha, 'start_challenge', start)
    monkeypatch.setattr(readonly.requests, 'post', post)
    client.manual_captcha = NS(solve=solve)
    try:
        if success:
            asyncio.run(client.login(123))
            assert client.logged_in and client.auth.viewer_id == 123
        else:
            with pytest.raises(ProtocolFailure, match='^login_failed$'):
                asyncio.run(client.login(123))
            assert client.phase == 'closed'
        assert calls[:3] == ['password', 'manual', 'submit']
        assert calls.count('password') == calls.count('manual') == calls.count('submit') == 1
        assert calls.count(step) == len(statuses)
        assert calls.count('game') == int(success or step == 'game')
    finally:
        client.close()
