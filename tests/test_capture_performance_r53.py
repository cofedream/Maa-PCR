"""Fixed capture bindings and non-authoritative connection-window evidence."""
import hashlib
import json

import pytest

from pcr_maa.capture_performance import CapturePerformance, finish_capture_report, selected_backend
from pcr_maa.device import fixed_config


def create(requested=6, inputs=1, pid=42, serial='emulator-5554'):
    return (f'[2026-10-16 01:02:03.001][DBG][Px{pid}][Tx1][MaaFramework.cpp][L20]'
        f'[MaaAdbControllerCreate] [address={serial}] [screencap_methods={requested}] '
        f'[input_methods={inputs}] [config={{}}] | enter\n')


def selection(method='RawWithGzip', pid=42):
    return (f'[2026-10-16 01:02:03.002][INF][Px{pid}][Tx2][ScreencapAgent.cpp][L251]'
        '[MaaNS::CtrlUnitNs::ScreencapAgent::speed_test] '
        f'The fastest method is {method} [cost=23ms]\n')


def test_cli_both_lossless_commands_bind_the_explicit_physical_display():
    commands = fixed_config(2, '4619827767814508545', preset_list=True)['command']
    assert commands['ScreencapEncode'] == ['{ADB}', '-s', '{ADB_SERIAL}', 'exec-out',
        'screencap -p -d 4619827767814508545']
    assert commands['ScreencapRawWithGzip'] == ['{ADB}', '-s', '{ADB_SERIAL}', 'exec-out',
        'screencap -d 4619827767814508545 | gzip -1']
    assert commands['Click'][-1] == 'input -d 2 tap {X} {Y}'
    assert commands['Swipe'][-1] == 'input -d 2 swipe {X1} {Y1} {X2} {Y2} {DURATION}'
    # CLI's explicit display remains explicit even for zero; GUI has its own Android 9 path.
    assert fixed_config(0, '0')['command']['ScreencapRawWithGzip'][-1] == 'screencap -d 0 | gzip -1'


@pytest.mark.parametrize('method,expected', [('Encode', 2), ('RawWithGzip', 4)])
def test_request_mask_is_distinct_from_selected_backend(method, expected):
    assert selected_backend([create(), selection(method)], requested=6, input_methods=1,
        serial='emulator-5554') == expected


@pytest.mark.parametrize('lines', [[], [create()], [selection()],
    [create(), selection('6')], [create(), selection(), selection()],
    [create(), create(), selection()], [create(2), selection()],
    [create(), selection(pid=43)], [create(serial='emulator-5556'), selection()],
    [create(), selection().replace('ScreencapAgent::speed_test', 'Other::speed_test')],
    [selection(), create()]])
def test_incomplete_ambiguous_or_other_run_backend_is_not_inferred(lines):
    with pytest.raises(ValueError):
        selected_backend(lines, requested=6, input_methods=1, serial='emulator-5554')


@pytest.mark.parametrize('requested,method,expected', [(2, 'Encode', 2), (4, 'RawWithGzip', 4)])
def test_read_only_ab_scope_requires_null_input_and_matching_method(requested, method, expected):
    assert selected_backend([create(requested, 0), selection(method)], requested=requested,
        input_methods=0, serial='emulator-5554') == expected
    with pytest.raises(ValueError):
        selected_backend([create(requested, 1), selection(method)], requested=requested,
            input_methods=1, serial='emulator-5554')
    with pytest.raises(ValueError):
        selected_backend([create(requested, 0), selection('RawWithGzip' if requested == 2 else 'Encode')],
            requested=requested, input_methods=0, serial='emulator-5554')


def report(tmp_path):
    path = tmp_path / 'run/native/maa/maafw.log'
    path.parent.mkdir(parents=True)
    (tmp_path / 'package-manifest.json').write_bytes(b'{"revision":53}')
    return path, CapturePerformance(tmp_path, 'synthetic-run', path,
        options={'command': {'ScreencapEncode': ['synthetic']}}, serial='emulator-5554',
        display_binding=dict(basis='default_screen_only', logical=0, physical=None))


def test_connection_report_uses_initial_offsets_and_rotation_identity(tmp_path):
    path, _ = report(tmp_path)
    path.write_text(create(2) + selection('Encode'), encoding='utf-8')
    capture = CapturePerformance(tmp_path, 'synthetic-run', path, options={}, serial='emulator-5554',
        display_binding=dict(basis='explicit_cli_display', logical=2, physical='42'))
    prior_size = path.stat().st_size
    with path.open('a', encoding='utf-8') as stream:
        stream.write(create())
    backup = path.with_name('maafw.bak.2026.10.16-01.02.03.1.log')
    path.rename(backup)
    path.write_text(selection(), encoding='utf-8')
    # Similar names are outside Maa's exact rotation family.
    path.with_name('maafw-other.log').write_text(create() + selection(), encoding='utf-8')
    path.with_name('maafw.bak.2026-10-16.log').write_text(create() + selection(), encoding='utf-8')
    result = capture.finish()
    assert result['evidence_version'] == 2 and result['observation'] == 'observed'
    assert result['requested_screencap_methods'] == 6 and result['actual_screencap_method'] == 4
    assert result['actual_backend'] == 'RawWithGzip' and result['observation_window'] == 'connection_only'
    assert result['source_manifest_sha256'] == hashlib.sha256(b'{"revision":53}').hexdigest()
    assert {item['file']: item['start_offset'] for item in result['native_log_segments']} == {
        backup.name: prior_size, path.name: 0}
    assert json.loads((path.parent.parent / 'capture-performance-v2.json').read_text(encoding='utf-8')) == result
    # Repeated analysis replaces the report and never accumulates sample counts.
    assert capture.finish() == result


@pytest.mark.parametrize('contents', ['', create(), create() + selection()[:-1]])
def test_missing_or_partial_log_remains_unobserved(tmp_path, contents):
    path, capture = report(tmp_path)
    path.write_text(contents, encoding='utf-8')
    result = capture.finish()
    assert result['observation'] == 'unknown' and result['actual_screencap_method'] is None


def test_report_failure_is_noncritical_and_has_no_business_or_retry_authority(tmp_path, monkeypatch):
    path, capture = report(tmp_path)
    path.write_text(create() + selection(), encoding='utf-8')
    monkeypatch.setattr(capture, 'finish', lambda: (_ for _ in ()).throw(OSError('synthetic')))
    assert finish_capture_report(capture) is None
    assert finish_capture_report(None) is None


@pytest.mark.parametrize('failure', ['truncated', 'oversized'])
def test_unreliable_connection_window_is_not_reported_as_a_backend(tmp_path, failure):
    path, _ = report(tmp_path)
    path.write_text('prior complete native log\n' * 30, encoding='utf-8')
    capture = CapturePerformance(tmp_path, 'synthetic-run', path, options={}, serial='emulator-5554',
        display_binding=dict(basis='default_screen_only', logical=0, physical=None))
    if failure == 'truncated':
        path.write_text(create() + selection(), encoding='utf-8')
    else:
        with path.open('ab') as stream:
            stream.truncate(path.stat().st_size + 32 * 1024 * 1024 + 1)
    result = capture.finish()
    assert result['observation'] == 'unknown' and result['actual_screencap_method'] is None


def test_cli_screenshot_failure_has_one_controller_and_no_reconnect_or_input(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    import maa.controller
    import maa.tasker
    from pcr_maa import device, native
    adb = tmp_path / 'adb.exe'
    adb.write_bytes(b'synthetic; never execute')
    calls = []
    class Controller:
        def __init__(self, adb_path, serial, methods, inputs, *, config):
            assert int(methods) == 6 and int(inputs) == 1
            assert config == fixed_config(2, '42')
            calls.append('controller')
        def set_screenshot_use_raw_size(self, value): return True
        def post_connection(self):
            calls.append('connect')
            return NS(succeeded=True)
        def post_screencap(self):
            calls.append('capture')
            return NS(succeeded=False)
    monkeypatch.setattr(maa.controller, 'AdbController', Controller)
    monkeypatch.setattr(maa.tasker, 'Tasker', lambda: NS(running=False))
    monkeypatch.setattr(native, 'load_resource', lambda: object())
    monkeypatch.setattr(native, 'wait_job', lambda value: value)
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'run/native')
    with pytest.raises(RuntimeError, match='Fresh raw frame unavailable'):
        device.navigate(2, '42', connection=dict(adb_path=str(adb), serial='emulator-5554'))
    assert calls == ['controller', 'connect', 'capture']
