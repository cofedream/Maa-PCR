"""The on-demand check dials only fixed hosts and reports fixed codes/timings."""
import json
import socket
import ssl
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from pcr_maa.labyrinth import network_check as check
from pcr_maa.labyrinth.servers import SERVERS


class FakeSocket:
    def __init__(self):
        self.timeout = None
        self.closed = False
        self.connect_error = None

    def settimeout(self, value):
        self.timeout = value

    def connect(self, address):
        if self.connect_error is not None:
            raise self.connect_error

    def close(self):
        self.closed = True


class FakeTls:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.error = None
        self.calls = []

    def wrap_socket(self, sock, server_hostname):
        self.calls.append(server_hostname)
        if self.error is not None:
            raise self.error
        return FakeTls()


@pytest.fixture
def dial(monkeypatch):
    """Install a deterministic resolver/socket/context and expose their state."""
    state = dict(sockets=[], context=FakeContext(), connect_error=None)
    monkeypatch.setattr(check.socket, 'getaddrinfo',
                        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 443))])
    def factory(family, kind, proto):
        sock = FakeSocket()
        sock.connect_error = state['connect_error']
        state['sockets'].append(sock)
        return sock
    monkeypatch.setattr(check.socket, 'socket', factory)
    monkeypatch.setattr(check, '_context', lambda: state['context'])
    return state


def test_success_records_each_phase(dial):
    row = check._attempt('example.invalid')
    assert row['ok'] is True and row['error'] is None
    assert all(type(row[key]) is int and row[key] >= 0 for key in ('dns_ms', 'tcp_ms', 'tls_ms'))
    assert dial['context'].calls == ['example.invalid']
    assert dial['sockets'][0].timeout == check.TIMEOUT_SECONDS


def test_dns_failure_is_a_fixed_code(monkeypatch):
    def broken(*args, **kwargs):
        raise socket.gaierror(11001, 'synthetic')
    monkeypatch.setattr(check.socket, 'getaddrinfo', broken)
    row = check._attempt('example.invalid')
    assert (row['ok'], row['error'], row['dns_ms']) == (False, 'dns_failed', None)


@pytest.mark.parametrize('error,code', [
    (TimeoutError(), 'tcp_timeout'),
    (ConnectionRefusedError(), 'tcp_refused'),
    (OSError(), 'unreachable'),
])
def test_tcp_failures_are_fixed_codes(dial, error, code):
    dial['connect_error'] = error
    row = check._attempt('example.invalid')
    assert (row['ok'], row['error']) == (False, code)
    assert dial['sockets'][0].closed is True


@pytest.mark.parametrize('error,code', [
    (TimeoutError(), 'tls_timeout'),
    (ssl.SSLError('synthetic'), 'tls_failed'),
])
def test_tls_failures_are_fixed_codes(dial, error, code):
    dial['context'].error = error
    row = check._attempt('example.invalid')
    assert (row['ok'], row['error']) == (False, code)
    assert dial['sockets'][0].closed is True


def test_probe_host_retries_once_and_keeps_the_success(monkeypatch):
    rows = [dict(dns_ms=1, tcp_ms=2, tls_ms=None, ok=False, error='tls_timeout'),
            dict(dns_ms=1, tcp_ms=2, tls_ms=3, ok=True, error=None)]
    calls = []
    def attempt(host, **kwargs):
        calls.append(host)
        return rows[len(calls) - 1]
    monkeypatch.setattr(check, '_attempt', attempt)
    assert check.probe_host('example.invalid') == dict(rows[-1], attempts=2)
    assert calls == ['example.invalid', 'example.invalid']


def test_probe_host_stops_after_the_first_success(monkeypatch):
    calls = []
    def attempt(host, **kwargs):
        calls.append(host)
        return dict(dns_ms=1, tcp_ms=2, tls_ms=3, ok=True, error=None)
    monkeypatch.setattr(check, '_attempt', attempt)
    assert check.probe_host('example.invalid')['attempts'] == 1
    assert len(calls) == 1


@pytest.mark.parametrize('rows,summary', [
    ([dict(dns_ms=1, tcp_ms=100, tls_ms=200, ok=True, error=None)] * 3, 'ok'),
    ([dict(dns_ms=1, tcp_ms=1000, tls_ms=2000, ok=True, error=None)] * 3, 'slow'),
    ([dict(dns_ms=1, tcp_ms=100, tls_ms=200, ok=True, error=None),
      dict(dns_ms=None, tcp_ms=None, tls_ms=None, ok=False, error='dns_failed'),
      dict(dns_ms=1, tcp_ms=100, tls_ms=200, ok=True, error=None)], 'failed'),
])
def test_snapshot_summary(monkeypatch, rows, summary):
    iterator = iter(rows)
    monkeypatch.setattr(check, 'probe_host', lambda host, **kwargs: dict(next(iterator), attempts=1))
    report = check.snapshot('cn-bilibili')
    assert report['summary'] == summary
    assert [row['target'] for row in report['results']] == ['game', 'sdk', 'database']
    assert all(set(row) == {'target', 'host', 'dns_ms', 'tcp_ms', 'tls_ms', 'ok', 'error', 'attempts'}
               for row in report['results'])


def test_target_hosts_are_derived_from_the_known_channels():
    assert check.target_hosts('cn-bilibili')[0] == (
        'game', 'l3-prod-all-gs-gzlj.bilibiligame.net')
    assert check.target_hosts('cn-mumu')[0] == (
        'game', 'l1-prod-uo-gs-gzlj.bilibiligame.net')
    assert check.target_hosts('cn-bilibili', database=False) == (
        ('game', 'l3-prod-all-gs-gzlj.bilibiligame.net'), ('sdk', check.SDK_HOST))
    with pytest.raises(ValueError, match='^unknown_game_server$'):
        check.target_hosts('cn-not-a-channel')


def test_request_rejects_unknown_fields_and_values():
    assert check.parse_request(dict(version=1, server='cn-bilibili', database=True)) == (
        'cn-bilibili', True)
    for value in (dict(version=2, server='cn-bilibili', database=True),
                  dict(version=1, server='cn-bilibili', database=1),
                  dict(version=1, server='cn-bilibili', database=True, extra=1),
                  dict(version=1, server='not-a-channel', database=True),
                  dict(version=True, server='cn-bilibili', database=True)):
        with pytest.raises(ValueError, match='^invalid_network_check_request$'):
            check.parse_request(value)


def test_sdk_and_database_hosts_match_the_runtime_sources(monkeypatch):
    from pcr_maa.labyrinth.database import MANIFEST_URL
    assert check.DATABASE_HOST == urlsplit(MANIFEST_URL).hostname == 'github.com'
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'vendor' / 'autopcr_sync'))
    from autopcr_sync.util.aiorequests import SDK_ROOT
    assert check.SDK_HOST == urlsplit(SDK_ROOT).hostname
    assert {urlsplit(server.root).hostname for server in SERVERS.values()} == {
        'l1-prod-uo-gs-gzlj.bilibiligame.net', 'l3-prod-all-gs-gzlj.bilibiligame.net'}


def test_encode_is_one_line_without_free_text():
    report = dict(kind='network_report', version=1, summary='ok', results=[])
    encoded = check.encode(report)
    assert '\n' not in encoded
    assert json.loads(encoded) == report
