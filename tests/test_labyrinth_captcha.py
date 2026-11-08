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
from pcr_maa.labyrinth.protocol import LabyrinthClient


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
