"""Bounded on-demand connectivity check for the labyrinth's fixed hosts.

Only the selected channel's game root, the fixed SDK login host and the fixed
database host can be dialled. One attempt performs DNS, then a bounded TCP/TLS
handshake against the resolved addresses; no HTTP request, credential, response
body or certificate text leaves this module. Callers receive fixed reason codes
and millisecond values.
"""
import json
import socket
import ssl
import time
from queue import Empty, Queue
from threading import Thread
from urllib.parse import urlsplit

from .database import MANIFEST_URL
from .servers import SERVERS, get_server

TIMEOUT_SECONDS = 10
REPORT_SECONDS = 60
ATTEMPTS = 2
ADDRESS_LIMIT = 3
SLOW_MILLIS = 2500
SDK_HOST = 'line1-sdk-center-login-sh.biligame.net'
DATABASE_HOST = urlsplit(MANIFEST_URL).hostname
TARGETS = ('game', 'sdk', 'database')
ERRORS = frozenset(('dns_failed', 'tcp_refused', 'tcp_timeout', 'tls_timeout',
                    'tls_failed', 'unreachable', 'deadline_exceeded'))


def _elapsed_ms(start):
    return max(0, int((time.monotonic() - start) * 1000))


def parse_request(value):
    """Fixed request shape: version, a known channel and one boolean extra."""
    if type(value) is not dict or set(value) != {'version', 'server', 'database'}:
        raise ValueError('invalid_network_check_request')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('invalid_network_check_request')
    server = value['server']
    if type(server) is not str or server not in SERVERS:
        raise ValueError('invalid_network_check_request')
    if type(value['database']) is not bool:
        raise ValueError('invalid_network_check_request')
    return server, value['database']


def target_hosts(server='cn-bilibili', *, database=True):
    game = get_server(server)
    hosts = [('game', urlsplit(game.root).hostname), ('sdk', SDK_HOST)]
    if database:
        hosts.append(('database', DATABASE_HOST))
    return tuple(hosts)


def _context():
    # Match requests: verify against the bundled CA store, then fall back to the
    # platform store. A certificate the protocol would reject must fail here too.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _attempt(host, *, timeout=TIMEOUT_SECONDS, deadline=None):
    if deadline is None:
        deadline = time.monotonic() + REPORT_SECONDS
    row = dict(dns_ms=None, tcp_ms=None, tls_ms=None, ok=False, error=None)
    try:
        started = time.monotonic()
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        row['error'] = 'dns_failed'
        return row
    row['dns_ms'] = _elapsed_ms(started)
    addresses = []
    seen = set()
    for family, kind, proto, _, address in infos:
        key = (family, address)
        if key in seen:
            continue
        seen.add(key)
        addresses.append((family, kind, proto, address))
        if len(addresses) == ADDRESS_LIMIT:
            break
    if not addresses:
        row['error'] = 'unreachable'
        return row
    # A hostname may resolve to several addresses (for example an unusable IPv6
    # first). Dial each in resolver order; one reachable address is enough.
    last_error = 'unreachable'
    for family, kind, proto, address in addresses:
        sock = tls = None
        tcp_ms = tls_ms = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = 'deadline_exceeded'
                break
            sock = socket.socket(family, kind, proto)
            sock.settimeout(min(timeout, remaining))
            started = time.monotonic()
            sock.connect(address)
            tcp_ms = _elapsed_ms(started)
            started = time.monotonic()
            context = _context()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = 'deadline_exceeded'
                break
            sock.settimeout(min(timeout, remaining))
            tls = context.wrap_socket(sock, server_hostname=host)
            tls_ms = _elapsed_ms(started)
            row.update(tcp_ms=tcp_ms, tls_ms=tls_ms, ok=True, error=None)
            return row
        except ConnectionRefusedError:
            last_error = 'tcp_refused'
        except TimeoutError:
            last_error = 'tls_timeout' if tcp_ms is not None else 'tcp_timeout'
        except ssl.SSLError:
            last_error = 'tls_failed'
        except OSError:
            last_error = 'unreachable'
        finally:
            row.update(tcp_ms=tcp_ms, tls_ms=tls_ms)
            if tls is not None:
                try: tls.close()
                except OSError: pass
            elif sock is not None:
                try: sock.close()
                except OSError: pass
    row['error'] = last_error
    return row


def probe_host(host, *, attempts=ATTEMPTS, deadline=None):
    """One host, at most two attempts; a later success replaces the failure."""
    if deadline is None:
        deadline = time.monotonic() + REPORT_SECONDS
    row = dict(dns_ms=None, tcp_ms=None, tls_ms=None, ok=False, error='deadline_exceeded')
    count = 0
    for index in range(attempts):
        if time.monotonic() >= deadline:
            break
        row = _attempt(host, deadline=deadline)
        count += 1
        if row['ok']:
            break
        if index + 1 < attempts:
            time.sleep(min(.3, max(0, deadline - time.monotonic())))
    return dict(row, attempts=count)


def snapshot(server='cn-bilibili', *, database=True):
    hosts = target_hosts(server, database=database)
    deadline = time.monotonic() + REPORT_SECONDS
    completed = Queue()
    results = [dict(target=target, host=host, dns_ms=None, tcp_ms=None, tls_ms=None,
                    ok=False, error='deadline_exceeded', attempts=0) for target, host in hosts]

    def probe(index, host):
        try: row = probe_host(host, deadline=deadline)
        except Exception: row = dict(dns_ms=None, tcp_ms=None, tls_ms=None,
                                     ok=False, error='unreachable', attempts=0)
        completed.put((index, row))

    # OS name resolution has no portable timeout. A stuck resolver must not hold this CLI open.
    for index, (_, host) in enumerate(hosts):
        Thread(target=probe, args=(index, host), daemon=True).start()
    for _ in hosts:
        try: index, row = completed.get(timeout=max(0, deadline - time.monotonic()))
        except Empty: break
        results[index].update(row)
    failed = any(not row['ok'] for row in results)
    slow = any(row['ok'] and (row['tcp_ms'] or 0) + (row['tls_ms'] or 0) >= SLOW_MILLIS
               for row in results)
    return dict(kind='network_report', version=1,
                summary='failed' if failed else 'slow' if slow else 'ok', results=results)


def encode(report):
    return json.dumps(report, ensure_ascii=False, separators=(',', ':'))
