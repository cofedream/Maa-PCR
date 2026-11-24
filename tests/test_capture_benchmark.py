"""Developer-only capture benchmark: validation, deterministic stats, never a device.

The CLI accepts an injected controller factory, so these tests prove the argument
contract, ordering, failure stop and P50/P90 math without ADB, a device or network.
"""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location('capture_benchmark', ROOT / 'scripts/capture_benchmark.py')
benchmark = module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class FakeJob:
    def __init__(self, succeeded=True):
        self._succeeded = succeeded

    def wait(self):
        return self

    @property
    def succeeded(self):
        return self._succeeded


class FakeController:
    def __init__(self, *, failures=(), connection_succeeds=True):
        self.failures = set(failures)
        self.connection_succeeds = connection_succeeds
        self.connection_calls = 0
        self.screencap_calls = 0

    def post_connection(self):
        self.connection_calls += 1
        return FakeJob(self.connection_succeeds)

    def post_screencap(self):
        index = self.screencap_calls
        self.screencap_calls += 1
        return FakeJob(index not in self.failures)


class RaisingController(FakeController):
    """A native adapter error (e.g. adb pipe closed) is a stop-the-run failure too."""

    def __init__(self, *, raise_at):
        super().__init__()
        self.raise_at = raise_at

    def post_screencap(self):
        if self.screencap_calls == self.raise_at:
            self.screencap_calls += 1
            raise OSError('synthetic adb pipe closed')
        return super().post_screencap()


class FakeFactory:
    def __init__(self, controller=None, *, fail=False):
        self.controller = controller if controller is not None else FakeController()
        self.fail = fail
        self.calls = []

    def __call__(self, adb, serial, screencap_methods, input_methods, config):
        self.calls.append(dict(adb=adb, serial=serial, screencap_methods=screencap_methods,
                               input_methods=input_methods, config=config))
        if self.fail:
            raise RuntimeError('synthetic native failure')
        return self.controller


def device_arguments(*extra):
    return ['--adb', '/usr/bin/adb', '--serial', '127.0.0.1:16384', *extra]


def scripted_clock(durations):
    """Perf-counter stub yielding one exact duration per sample: start, end, start, end..."""
    ticks, total = [], 0.0
    for duration in durations:
        ticks.extend([total, total + duration])
        total += duration
    return iter(ticks).__next__


def test_backend_masks_match_the_pinned_maafw_enum():
    define = pytest.importorskip('maa.define')
    assert benchmark.SCREENCAP_METHODS == {
        'encode': int(define.MaaAdbScreencapMethodEnum.Encode),
        'raw': int(define.MaaAdbScreencapMethodEnum.RawWithGzip),
        'netcat': int(define.MaaAdbScreencapMethodEnum.RawByNetcat),
        'extras': int(define.MaaAdbScreencapMethodEnum.EmulatorExtras),
    }
    assert benchmark.INPUT_METHOD == int(define.MaaAdbInputMethodEnum.Null)


def test_help_documents_the_developer_only_boundary(capsys):
    with pytest.raises(SystemExit) as raised:
        benchmark.main(['--help'])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert 'Developer-only' in output and '--yes-device' in output
    assert '--json' in output and '--backend' in output


@pytest.mark.parametrize('argv', [
    device_arguments('--backend', 'raw'),
    device_arguments('--backend', 'raw', '--json'),
    device_arguments('--backend', 'extras', '--mumu-path', '/opt/mumu', '--mumu-lib', '/opt/mumu/ext.dll',
                     '--mumu-index', '0', '--mumu-clone', '0'),
])
def test_without_yes_device_exits_before_the_controller_factory(argv):
    factory = FakeFactory(fail=True)
    with pytest.raises(SystemExit) as raised:
        benchmark.main(argv, controller_factory=factory)
    assert raised.value.code == 2
    assert factory.calls == []


@pytest.mark.parametrize('missing', ['--mumu-path', '--mumu-lib', '--mumu-index', '--mumu-clone'])
def test_extras_requires_every_mumu_binding_argument(missing):
    base = device_arguments('--backend', 'extras', '--yes-device')
    complete = ['--mumu-path', '/opt/mumu', '--mumu-lib', '/opt/mumu/ext.dll',
                '--mumu-index', '0', '--mumu-clone', '0']
    position = complete.index(missing)
    factory = FakeFactory()
    with pytest.raises(SystemExit) as raised:
        benchmark.main(base + complete[:position] + complete[position + 2:], controller_factory=factory)
    assert raised.value.code == 2
    assert factory.calls == []


@pytest.mark.parametrize('argv', [
    device_arguments('--backend', 'extras', '--yes-device', '--mumu-path', '', '--mumu-lib', '/opt/lib',
                     '--mumu-index', '0', '--mumu-clone', '0'),
    device_arguments('--backend', 'raw', '--yes-device', '--mumu-path', '/opt/mumu'),
    device_arguments('--backend', 'raw', '--yes-device', '--mumu-index', '0'),
])
def test_empty_or_mismatched_mumu_arguments_are_refused(argv):
    factory = FakeFactory()
    with pytest.raises(SystemExit) as raised:
        benchmark.main(argv, controller_factory=factory)
    assert raised.value.code == 2
    assert factory.calls == []


@pytest.mark.parametrize('argv', [
    device_arguments('--backend', 'raw', '--samples', '0', '--yes-device'),
    device_arguments('--backend', 'raw', '--samples', '-1', '--yes-device'),
    device_arguments('--backend', 'raw', '--samples', 'many', '--yes-device'),
    device_arguments('--backend', 'all', '--yes-device'),
    device_arguments('--backend', 'extras', '--samples', '5', '--yes-device',
                     '--mumu-path', '/opt/mumu', '--mumu-lib', '/opt/mumu/ext.dll',
                     '--mumu-index', '-1', '--mumu-clone', '0'),
])
def test_invalid_samples_backend_or_index_are_refused_by_argparse(argv):
    factory = FakeFactory()
    with pytest.raises(SystemExit) as raised:
        benchmark.main(argv, controller_factory=factory)
    assert raised.value.code == 2
    assert factory.calls == []


def test_json_run_times_every_screencap_after_one_connection(capsys):
    factory = FakeFactory()
    controller = factory.controller
    code = benchmark.main(device_arguments('--backend', 'raw', '--samples', '5', '--json', '--yes-device'),
                          controller_factory=factory, clock=scripted_clock([.01, .02, .03, .04, .05]))
    assert code == 0
    assert controller.connection_calls == 1 and controller.screencap_calls == 5
    assert len(factory.calls) == 1
    call = factory.calls[0]
    assert call['screencap_methods'] == 4 and call['input_methods'] == 0
    assert call['config']['command']['ScreencapEncode'][-1] == 'screencap -p 2>/dev/null'
    assert call['config']['command']['ScreencapRawWithGzip'][-1] == 'screencap 2>/dev/null | gzip -1'
    assert 'extras' not in call['config']
    document = json.loads(capsys.readouterr().out)
    assert document == dict(
        schema='capture-benchmark/1', purpose='developer_capture_latency_sample',
        device=dict(adb='/usr/bin/adb', serial='127.0.0.1:16384'),
        backend='raw', screencap_methods=4, input_methods=0, mumu=None,
        samples_requested=5, samples_completed=5, failed=False, error=None,
        p50_ms=30.0, p90_ms=50.0, min_ms=10.0, max_ms=50.0)


def test_text_run_reports_identity_and_millisecond_percentiles(capsys):
    code = benchmark.main(device_arguments('--backend', 'netcat', '--samples', '4', '--yes-device'),
                          controller_factory=FakeFactory(),
                          clock=scripted_clock([.001, .002, .003, .004]))
    assert code == 0
    output = capsys.readouterr().out
    assert 'backend=netcat (screencap_methods=8)' in output
    assert 'input_methods=0' in output
    assert 'samples=4/4' in output
    assert 'p50=2.000ms' in output and 'p90=4.000ms' in output
    assert 'min=1.000ms' in output and 'max=4.000ms' in output


def test_first_capture_failure_stops_without_retry_or_backend_switch(capsys):
    factory = FakeFactory(FakeController(failures={2}))
    controller = factory.controller
    code = benchmark.main(device_arguments('--backend', 'encode', '--samples', '5', '--json', '--yes-device'),
                          controller_factory=factory)
    assert code == 1
    assert controller.connection_calls == 1 and controller.screencap_calls == 3
    assert len(factory.calls) == 1 and factory.calls[0]['screencap_methods'] == 2
    document = json.loads(capsys.readouterr().out)
    assert document['failed'] is True and document['samples_completed'] == 2
    assert document['error'] == 'capture failed at sample 3 of 5'
    assert document['p50_ms'] is None and document['p90_ms'] is None


def test_native_capture_exception_also_stops_the_run(capsys):
    controller = RaisingController(raise_at=1)
    code = benchmark.main(device_arguments('--backend', 'raw', '--samples', '4', '--json', '--yes-device'),
                          controller_factory=FakeFactory(controller))
    assert code == 1
    assert controller.screencap_calls == 2
    document = json.loads(capsys.readouterr().out)
    assert document['samples_completed'] == 1
    assert document['error'] == 'capture failed at sample 2 of 4: synthetic adb pipe closed'


def test_text_failure_is_reported_on_stderr(capsys):
    code = benchmark.main(device_arguments('--backend', 'raw', '--samples', '2', '--yes-device'),
                          controller_factory=FakeFactory(FakeController(failures={0})))
    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'FAILED after 0/2 samples' in captured.err


def test_connection_failure_reports_before_any_screencap(capsys):
    controller = FakeController(connection_succeeds=False)
    code = benchmark.main(device_arguments('--backend', 'raw', '--samples', '3', '--json', '--yes-device'),
                          controller_factory=FakeFactory(controller))
    assert code == 1
    assert controller.screencap_calls == 0
    document = json.loads(capsys.readouterr().out)
    assert document['error'] == 'device connection failed'
    assert document['samples_completed'] == 0


def test_controller_creation_failure_is_reported_once(capsys):
    factory = FakeFactory(fail=True)
    code = benchmark.main(device_arguments('--backend', 'extras', '--samples', '2', '--json', '--yes-device',
                                           '--mumu-path', '/opt/mumu', '--mumu-lib', '/opt/mumu/ext.dll',
                                           '--mumu-index', '1', '--mumu-clone', '0'),
                          controller_factory=factory)
    assert code == 1 and len(factory.calls) == 1
    document = json.loads(capsys.readouterr().out)
    assert document['failed'] is True and document['samples_completed'] == 0
    assert document['error'] == 'controller creation failed: synthetic native failure'
    assert factory.calls[0]['config']['extras'] == {'mumu': {
        'enable': True, 'path': '/opt/mumu', 'lib': '/opt/mumu/ext.dll', 'index': 1,
        'app_package': 'com.bilibili.priconne', 'app_cloned_index': 0}}


def test_extras_config_binds_the_mumu_package_and_keeps_stderr_suppression():
    config = benchmark.controller_config('extras', dict(path='/opt/mumu', lib='/opt/mumu/ext.dll',
                                                        index=0, clone=2))
    assert config['extras']['mumu']['app_package'] == benchmark.MUMU_PACKAGE
    assert config['extras']['mumu']['app_cloned_index'] == 2
    assert config['command'] == benchmark.STDERR_SUPPRESSED_SCREENCAPS
    with pytest.raises(ValueError):
        benchmark.controller_config('extras')


@pytest.mark.parametrize('point,expected', [(1, 1.0), (50, 2.0), (75, 3.0), (90, 4.0), (100, 4.0)])
def test_percentile_is_nearest_rank(point, expected):
    assert benchmark.percentile([1.0, 2.0, 3.0, 4.0], point) == expected


@pytest.mark.parametrize('values,point', [([], 50), ([1.0], 0), ([1.0], 101), ([1.0], -5)])
def test_percentile_rejects_impossible_input(values, point):
    with pytest.raises(ValueError):
        benchmark.percentile(values, point)


def test_summarise_rejects_an_empty_sample():
    with pytest.raises(ValueError):
        benchmark.summarise([])


def test_capture_benchmark_is_not_referenced_by_production_code():
    offenders = [str(path.relative_to(ROOT)) for path in (ROOT / 'src').rglob('*.py')
                 if 'capture_benchmark' in path.read_text(encoding='utf-8')]
    assert offenders == []
