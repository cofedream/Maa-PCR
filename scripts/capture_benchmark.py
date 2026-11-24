"""Read-only, developer-only latency benchmark for one Maa ADB capture backend.

DO NOT IMPORT THIS MODULE FROM PRODUCTION CODE. Nothing under ``src/``, ``gui/``
or any product entry point may import it: it exists only so a human can measure
``AdbController.post_screencap()`` once a real MuMu device is available. The
benchmark is a developer sample, not native ``maafw.log`` evidence and never an
executable product path.

Guarantees:
- one explicit device address and exactly one capture backend per invocation;
- the input method is pinned to ``MaaAdbInputMethodEnum.Null`` (0), so the
  controller has no click/swipe/key method at all;
- ``--yes-device`` is mandatory; without it the CLI exits before the controller
  factory is called and no device is touched;
- samples run sequentially after one connection; the first failure stops the run
  (no retry, no backend switch) and is reported as a non-zero exit;
- only ``post_connection()``, ``post_screencap()[.wait()]`` are used: no
  display, resolution, or input option is changed by the benchmark.

The ``ScreencapEncode`` / ``ScreencapRawWithGzip`` command overrides keep
``2>/dev/null`` (Android 14 prints warnings on stderr, which corrupts the
extracted image), matching the GUI's standard-capture defaults.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time

# MaaAdbScreencapMethodEnum as pinned by maafw 5.16.1 (``maa.define``).
#   2  Encode            lossless PNG, slow
#   4  RawWithGzip       lossless raw + gzip -1
#   8  RawByNetcat       lossless, low compatibility, excluded by Maa default
#   64 EmulatorExtras    vendor SDK (MuMu 12 / LDPlayer 9 / Androws), read-only
SCREENCAP_METHODS = {'encode': 2, 'raw': 4, 'netcat': 8, 'extras': 64}
# MaaAdbInputMethodEnum.Null. Fixed; never exposed as a CLI flag.
INPUT_METHOD = 0
# MuMu extras binding must name the game package just like the GUI contract.
MUMU_PACKAGE = 'com.bilibili.priconne'
# Same command shape as the GUI's DefaultCaptureOptions(): no display binding.
STDERR_SUPPRESSED_SCREENCAPS = {
    'ScreencapEncode': ['{ADB}', '-s', '{ADB_SERIAL}', 'exec-out', 'screencap -p 2>/dev/null'],
    'ScreencapRawWithGzip': ['{ADB}', '-s', '{ADB_SERIAL}', 'exec-out', 'screencap 2>/dev/null | gzip -1'],
}


class BenchmarkFailure(RuntimeError):
    """A stop-the-run failure; ``completed`` is the number of timed samples."""

    def __init__(self, message, *, completed=0):
        super().__init__(message)
        self.completed = completed


def positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return value


def non_negative_int(text):
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError('must be a non-negative integer')
    return value


def build_parser():
    parser = argparse.ArgumentParser(
        prog='capture_benchmark.py',
        description='Developer-only read-only latency benchmark for one Maa ADB capture backend.',
        epilog='Never imported by production code. Requires --yes-device; one backend per run; '
               'first failure stops the run (no retry, no backend switch).')
    parser.add_argument('--adb', required=True,
                        help='absolute path to the local adb executable')
    parser.add_argument('--serial', required=True,
                        help='one explicit device address, e.g. 127.0.0.1:16384')
    parser.add_argument('--samples', type=positive_int, default=50,
                        help='number of timed screencaps after one connection (default: 50)')
    parser.add_argument('--backend', required=True, choices=tuple(SCREENCAP_METHODS),
                        help='capture backend to measure: encode=2 raw=4 netcat=8 extras=64')
    parser.add_argument('--json', action='store_true',
                        help='emit one machine-readable JSON object on stdout')
    parser.add_argument('--yes-device', action='store_true',
                        help='explicit consent to connect to the named device; required')
    parser.add_argument('--mumu-path', help='MuMu 12 installation root (extras only)')
    parser.add_argument('--mumu-lib', help='path to external_renderer_ipc.dll (extras only)')
    parser.add_argument('--mumu-index', type=non_negative_int,
                        help='MuMu instance index (extras only)')
    parser.add_argument('--mumu-clone', type=non_negative_int,
                        help='MuMu app-cloned index (extras only)')
    return parser


def validate_args(parser, args):
    """Return the MuMu binding dict (or None) after refusing any ambiguous run."""
    if not args.yes_device:
        parser.error('refusing to touch a device without --yes-device')
    if not args.adb.strip():
        parser.error('--adb must not be empty')
    if not args.serial.strip():
        parser.error('--serial must not be empty')
    mumu_values = (args.mumu_path, args.mumu_lib, args.mumu_index, args.mumu_clone)
    if args.backend == 'extras':
        if any(value is None for value in mumu_values):
            parser.error('--backend extras requires --mumu-path, --mumu-lib, --mumu-index and --mumu-clone')
        if not args.mumu_path.strip() or not args.mumu_lib.strip():
            parser.error('--mumu-path and --mumu-lib must not be empty')
        return dict(path=args.mumu_path, lib=args.mumu_lib,
                    index=args.mumu_index, clone=args.mumu_clone)
    if any(value is not None for value in mumu_values):
        parser.error('--mumu-* options are only valid with --backend extras')
    return None


def controller_config(backend, mumu=None):
    """AdbController config: stderr-suppressed screencaps plus the extras binding."""
    config = {'command': {name: list(command) for name, command in STDERR_SUPPRESSED_SCREENCAPS.items()}}
    if backend == 'extras':
        if mumu is None:
            raise ValueError('extras backend needs the MuMu binding')
        config['extras'] = {'mumu': {
            'enable': True,
            'path': mumu['path'],
            'lib': mumu['lib'],
            'index': mumu['index'],
            'app_package': MUMU_PACKAGE,
            'app_cloned_index': mumu['clone'],
        }}
    return config


def default_controller_factory(adb, serial, screencap_methods, input_methods, config):
    """Real factory, imported lazily so tests can inject a fake without native Maa."""
    from maa.controller import AdbController
    return AdbController(adb, serial, screencap_methods, input_methods, config=config)


def measure(controller, samples, *, clock=time.perf_counter):
    """Connect once, then time ``samples`` fresh screencaps. Stop on first failure."""
    try:
        connection = controller.post_connection()
        connection.wait()
    except Exception as exc:  # adapter errors are ordinary failures for this tool
        raise BenchmarkFailure(f'device connection failed: {exc}', completed=0) from exc
    if not connection.succeeded:
        raise BenchmarkFailure('device connection failed', completed=0)
    timings = []
    for index in range(samples):
        started = clock()
        try:
            job = controller.post_screencap()
            job.wait()
        except Exception as exc:
            raise BenchmarkFailure(f'capture failed at sample {index + 1} of {samples}: {exc}',
                                   completed=len(timings)) from exc
        elapsed = clock() - started
        if not job.succeeded:
            raise BenchmarkFailure(f'capture failed at sample {index + 1} of {samples}',
                                   completed=len(timings))
        timings.append(elapsed)
    return timings


def run(controller_factory, *, adb, serial, backend, samples, mumu=None, clock=time.perf_counter):
    config = controller_config(backend, mumu)
    try:
        controller = controller_factory(adb, serial, SCREENCAP_METHODS[backend], INPUT_METHOD, config)
    except Exception as exc:  # adapters surface native errors as ordinary exceptions
        raise BenchmarkFailure(f'controller creation failed: {exc}', completed=0) from exc
    try:
        return measure(controller, samples, clock=clock)
    finally:
        close = getattr(controller, 'close', None)
        if callable(close):
            close()


def percentile(values, point):
    """Nearest-rank percentile; deterministic for the small developer samples."""
    if not values:
        raise ValueError('no completed samples')
    if not 0 < point <= 100:
        raise ValueError('percentile must be in (0, 100]')
    ordered = sorted(values)
    return ordered[max(0, math.ceil(point / 100 * len(ordered)) - 1)]


def summarise(timings):
    if not timings:
        raise ValueError('no completed samples')
    millis = [value * 1000 for value in timings]
    return {
        'samples': len(millis),
        'p50_ms': round(percentile(millis, 50), 3),
        'p90_ms': round(percentile(millis, 90), 3),
        'min_ms': round(min(millis), 3),
        'max_ms': round(max(millis), 3),
    }


def build_document(args, *, backend, mumu, failure=None, stats=None):
    completed = failure.completed if failure is not None else stats['samples']
    return {
        'schema': 'capture-benchmark/1',
        'purpose': 'developer_capture_latency_sample',
        'device': {'adb': args.adb, 'serial': args.serial},
        'backend': backend,
        'screencap_methods': SCREENCAP_METHODS[backend],
        'input_methods': INPUT_METHOD,
        'mumu': mumu,
        'samples_requested': args.samples,
        'samples_completed': completed,
        'failed': failure is not None,
        'error': str(failure) if failure is not None else None,
        'p50_ms': stats['p50_ms'] if stats is not None else None,
        'p90_ms': stats['p90_ms'] if stats is not None else None,
        'min_ms': stats['min_ms'] if stats is not None else None,
        'max_ms': stats['max_ms'] if stats is not None else None,
    }


def render_text(document):
    identity = (f"backend={document['backend']} "
                f"(screencap_methods={document['screencap_methods']}) "
                f"input_methods={document['input_methods']} "
                f"serial={document['device']['serial']}")
    if document['failed']:
        return (f"{identity}\nFAILED after {document['samples_completed']}/"
                f"{document['samples_requested']} samples: {document['error']}")
    return (f"{identity} samples={document['samples_completed']}/{document['samples_requested']}\n"
            f"p50={document['p50_ms']:.3f}ms p90={document['p90_ms']:.3f}ms "
            f"min={document['min_ms']:.3f}ms max={document['max_ms']:.3f}ms")


def main(argv=None, *, controller_factory=None, clock=time.perf_counter):
    parser = build_parser()
    args = parser.parse_args(argv)
    mumu = validate_args(parser, args)
    factory = controller_factory or default_controller_factory
    failure = stats = None
    try:
        timings = run(factory, adb=args.adb, serial=args.serial, backend=args.backend,
                      samples=args.samples, mumu=mumu, clock=clock)
    except BenchmarkFailure as exc:
        failure = exc
    else:
        stats = summarise(timings)
    document = build_document(args, backend=args.backend, mumu=mumu, failure=failure, stats=stats)
    if args.json:
        print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    else:
        print(render_text(document), file=sys.stderr if document['failed'] else sys.stdout)
    return 1 if document['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
