"""Offline A/B comparison of same-frame scene recognition probe scopes.

Development-only, read-only tool. It replays public saved frames through
`SavedImages` and the pinned Maa resource, so it never connects a device, uses
the network, or writes the repository (native logs go to a private temporary
directory). For every frame it observes the same image twice:

  * ``full``   -- ``probe_kinds=None``, every scene rule runs;
  * ``scoped`` -- ``probe_kinds=<--scope>``, the narrowed ownership set.

It reports kind, payload.area, payload.node_type, the payload.controls key
set, any observation rejection reason, and the native recognition-call count
recorded by ``pcr_maa.labyrinth.timing.NativeTimings``. A recognition count is
``null`` when the native sink cannot be read; it is never invented as 0.

The production scene reader always adds its canary kinds (battle, team, map,
move_confirmation, home) to a narrowed probe scope, so ``scoped`` is expected
to read a superset of the requested kinds. ``payload.probe_kinds`` in each run
shows the scope that was actually applied.

Usage::

    python scripts/perf_scope_ab.py                       # full vs map on tests/data
    python scripts/perf_scope_ab.py --scope map,event --limit 5
    python scripts/perf_scope_ab.py --frames tests/data evidence/opening-sample --json
    python scripts/perf_scope_ab.py --frames tests/data/labyrinth-map-ex-20261022-target.png

Relative paths resolve against the repository root. Missing or unreadable
paths are reported as ``skipped`` (for example private evidence that is not
distributed) instead of being counted as a pass.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src')]

from pcr_maa import native
from pcr_maa.labyrinth import scene
from pcr_maa.labyrinth.timing import NativeTimings, Timings
from pcr_maa.replay import SavedImages

DEFAULT_FRAMES = ROOT / 'tests/data'
DEFAULT_SCOPE = 'map'
DEFAULT_PATTERN = 'labyrinth-*.png'
IMAGE_SUFFIXES = frozenset(('.png', '.jpg', '.jpeg'))
SEMANTIC_FIELDS = ('kind', 'area', 'node_type', 'reason')
EVIDENCE = ('offline SavedImages replay of public saved frames; '
            'no device, network, or repository write')


def parse_scope(value):
    """Parse ``--scope`` into the narrowed probe_kinds tuple. None is not a scope."""
    if not isinstance(value, str):
        raise ValueError('scope_required')
    text = value.strip()
    if not text:
        raise ValueError('scope_empty')
    kinds = []
    for token in text.split(','):
        kind = token.strip()
        if not kind:
            raise ValueError('scope_token_empty')
        if kind not in scene._RULES:
            raise ValueError(f'unknown_probe_kind:{kind}')
        if kind not in kinds:
            kinds.append(kind)
    return tuple(sorted(kinds))


def split_frame_args(values):
    """Expand repeated and/or comma-separated ``--frames`` values into paths."""
    if not values:
        return [DEFAULT_FRAMES]
    paths = []
    for value in values:
        parts = value if isinstance(value, (list, tuple)) else [value]
        for part in parts:
            for token in str(part).split(','):
                token = token.strip()
                if token:
                    paths.append(Path(token))
    if not paths:
        raise ValueError('frames_empty')
    return paths


def resolve_path(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def discover_frames(paths, *, pattern=DEFAULT_PATTERN):
    """Return ordered unique image paths plus skipped entries; never raises for bad input."""
    frames = []
    skipped = []
    for raw in paths:
        path = resolve_path(raw)
        if not path.exists():
            skipped.append(dict(path=str(path), reason='frame_missing'))
            continue
        if path.is_dir():
            matches = sorted(match for match in path.glob(pattern)
                             if match.is_file() and match.suffix.lower() in IMAGE_SUFFIXES)
            if not matches:
                skipped.append(dict(path=str(path), reason='no_matching_frames'))
                continue
            for match in matches:
                if match not in frames:
                    frames.append(match)
            continue
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            skipped.append(dict(path=str(path), reason='not_an_image'))
            continue
        if path not in frames:
            frames.append(path)
    return frames, skipped


def _invalid_frame_reason(path):
    """Distinguish an undecodable file from a decodable image of the wrong size."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return 'frame_unreadable'
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    return 'frame_size_invalid' if image is not None else 'frame_unreadable'


def read_frame(path):
    """Return ``(image, None)`` or ``(None, skip_reason)``; a bad frame is never a pass."""
    try:
        return native.read_image(path), None
    except ValueError:
        return None, _invalid_frame_reason(path)
    except Exception:
        return None, 'frame_unreadable'


def summarize_scene(kind, payload):
    """Field subset compared by the A/B report; tolerates an absent payload."""
    payload = payload if isinstance(payload, dict) else {}
    controls = payload.get('controls')
    score = payload.get('score')
    return dict(
        kind=kind if isinstance(kind, str) else None,
        area=payload.get('area'),
        node_type=payload.get('node_type'),
        control_keys=sorted(controls) if isinstance(controls, dict) else [],
        score=score if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
        reason=None,
    )


def diff_runs(full, scoped):
    """Compare a full-rule observation with the narrowed-scope observation.

    ``match`` covers business semantics (kind/area/node_type/reason); optional
    control keys are expected to shrink under a narrowed probe scope, so they
    are reported separately instead of failing the comparison.
    """
    differences = {field: dict(full=full.get(field), scoped=scoped.get(field))
                   for field in SEMANTIC_FIELDS if full.get(field) != scoped.get(field)}
    full_controls = set(full.get('control_keys') or ())
    scoped_controls = set(scoped.get('control_keys') or ())
    return dict(
        match=not differences,
        differences=differences,
        controls_match=full_controls == scoped_controls,
        controls_only_full=sorted(full_controls - scoped_controls),
        controls_only_scoped=sorted(scoped_controls - full_controls),
    )


def recognition_count(timings):
    """Native recognition invocations so far, or None when the collector is unavailable."""
    if timings is None:
        return None
    try:
        payload = timings.payload()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    recognition = payload.get('recognition')
    if not isinstance(recognition, dict):
        return None
    count = recognition.get('count')
    return count if type(count) is int and count >= 0 else None


def observe_once(runtime, controller, image, *, probe_kinds, timings, timeout):
    """Observe one already-loaded image under one probe scope, without input."""
    controller.stages = [[image]]
    controller.frame = 0
    clicks = len(controller.clicks)
    swipes = len(controller.swipes)
    before = recognition_count(timings)
    observed = None
    started = time.perf_counter()
    try:
        observed = runtime.observe(timeout=timeout, probe_kinds=probe_kinds)
        fields = summarize_scene(observed.kind, observed.payload)
        reason = None
    except Exception as error:
        fields = summarize_scene(None, {})
        reason = f'{type(error).__name__}:{error}'
    elapsed = time.perf_counter() - started
    after = recognition_count(timings)
    count = after - before if None not in (before, after) and after >= before else None
    payload = observed.payload if observed is not None and isinstance(observed.payload, dict) else {}
    fields.update(
        reason=reason,
        probe_kinds=payload.get('probe_kinds'),
        scanned_anchors=len(payload.get('scanned_anchor_keys') or ()),
        recognition_count=count,
        elapsed_seconds=round(elapsed, 4),
        inputs=(len(controller.clicks) - clicks) + (len(controller.swipes) - swipes),
    )
    return fields


def _sum_metric(entries, key, side):
    values = [entry[side].get(key) for entry in entries]
    if not values or any(value is None for value in values):
        return None
    return sum(values)


def _new_report(paths, scope, pattern, limit, timeout):
    return dict(
        tool='perf_scope_ab',
        evidence=EVIDENCE,
        frames_spec=[str(path) for path in paths],
        pattern=pattern,
        limit=limit,
        timeout=timeout,
        scope=dict(full=None, scoped=list(scope)),
        resource=dict(loaded=False, path=str(native.RESOURCE), load_seconds=None),
        frames=[],
        skipped=[],
        summary=dict(discovered=0, frames=0, skipped=0, semantic_mismatch=0,
                     controls_differ=0, observation_errors=0,
                     recognition_count_full=None, recognition_count_scoped=None,
                     recognition_delta=None, elapsed_seconds_full=None,
                     elapsed_seconds_scoped=None, input_count=0),
    )


def _finish_report(report, discovered):
    entries = report['frames']
    skipped = len(report['skipped'])
    full_recognition = _sum_metric(entries, 'recognition_count', 'full')
    scoped_recognition = _sum_metric(entries, 'recognition_count', 'scoped')
    report['summary'].update(
        discovered=discovered,
        frames=len(entries),
        skipped=skipped,
        semantic_mismatch=sum(not entry['diff']['match'] for entry in entries),
        controls_differ=sum(not entry['diff']['controls_match'] for entry in entries),
        observation_errors=sum(entry['full']['reason'] is not None or entry['scoped']['reason'] is not None
                               for entry in entries),
        recognition_count_full=full_recognition,
        recognition_count_scoped=scoped_recognition,
        recognition_delta=(full_recognition - scoped_recognition
                           if full_recognition is not None and scoped_recognition is not None else None),
        elapsed_seconds_full=(round(sum(entry['full']['elapsed_seconds'] for entry in entries), 4)
                              if entries else None),
        elapsed_seconds_scoped=(round(sum(entry['scoped']['elapsed_seconds'] for entry in entries), 4)
                                if entries else None),
        input_count=sum(entry['full']['inputs'] + entry['scoped']['inputs'] for entry in entries),
    )


def prepare_frames(paths, *, pattern=DEFAULT_PATTERN, limit=None):
    """Discover, bound and load frames without touching Maa resource/tasker/device."""
    discovered, skipped = discover_frames(paths, pattern=pattern)
    images = []
    for path in discovered:
        if limit is not None and len(images) >= limit:
            break
        image, reason = read_frame(path)
        if image is None:
            skipped.append(dict(path=str(path), reason=reason))
            continue
        images.append((path, image))
    return images, skipped, len(discovered)


def run(paths, scope, *, pattern=DEFAULT_PATTERN, limit=None,
        timeout=float(scene.OBSERVATION_TIMEOUT_SECONDS)):
    """Run the full-vs-scoped comparison over the selected frames and return a report."""
    paths = [Path(path) for path in paths]
    scope = tuple(scope)
    report = _new_report(paths, scope, pattern, limit, timeout)
    images, skipped, discovered = prepare_frames(paths, pattern=pattern, limit=limit)
    report['skipped'] = skipped
    if not images:
        _finish_report(report, discovered)
        return report
    started = time.perf_counter()
    resource = native.load_resource()
    report['resource'].update(loaded=True, load_seconds=round(time.perf_counter() - started, 4))
    controller = SavedImages([[images[0][1]]])
    tasker = native.bind(resource, controller)
    timings = Timings()
    native_timings = None
    try:
        try:
            native_timings = NativeTimings(tasker, controller, timings)
        except Exception:
            # The A/B outcome is still usable; the sub-recognition count stays null.
            native_timings = None
        try:
            runtime = scene.MaaSceneRuntime(tasker, timings=timings)
            for path, image in images:
                full = observe_once(runtime, controller, image, probe_kinds=None,
                                    timings=timings, timeout=timeout)
                scoped = observe_once(runtime, controller, image, probe_kinds=scope,
                                      timings=timings, timeout=timeout)
                report['frames'].append(dict(frame=path.name, path=str(path),
                                             full=full, scoped=scoped,
                                             diff=diff_runs(full, scoped)))
        finally:
            if native_timings is not None:
                try:
                    native_timings.close()
                except Exception:
                    pass
    finally:
        native.stop_tasker(tasker)
    _finish_report(report, discovered)
    return report


def _pair(full, scoped, suffix=''):
    left = '-' if full is None else f'{full}{suffix}'
    right = '-' if scoped is None else f'{scoped}{suffix}'
    return f'{left}/{right}'


def format_report(report):
    """Render the human-readable report; the JSON form carries the same values."""
    scope = report['scope']['scoped']
    resource = report['resource']
    lines = [
        'perf_scope_ab: offline scene recognition A/B; '
        f'full probe_kinds=None vs scoped probe_kinds={",".join(scope)}',
        f'evidence: {report["evidence"]}',
        f'resource: {resource["path"]} loaded={resource["loaded"]} '
        f'load_seconds={resource["load_seconds"]}',
        '',
        f'{"FRAME":<44}{"FULL KIND":<26}{"SCOPED KIND":<26}{"AREA f/s":<10}'
        f'{"NODE f/s":<10}{"CTRL f/s":<10}{"REC f/s":<12}{"ELAPSED f/s":<20}MATCH',
    ]
    for entry in report['frames']:
        full = entry['full']
        scoped = entry['scoped']
        difference = entry['diff']
        marker = 'NO' if not difference['match'] else ('controls' if not difference['controls_match'] else 'yes')
        elapsed = f'{full["elapsed_seconds"]:.3f}s/{scoped["elapsed_seconds"]:.3f}s'
        lines.append(
            f'{entry["frame"]:<44}{str(full["kind"]):<26}{str(scoped["kind"]):<26}'
            f'{_pair(full["area"], scoped["area"]):<10}'
            f'{_pair(full["node_type"], scoped["node_type"]):<10}'
            f'{_pair(len(full["control_keys"]), len(scoped["control_keys"])):<10}'
            f'{_pair(full["recognition_count"], scoped["recognition_count"]):<12}'
            f'{elapsed:<20}{marker}')
    mismatches = [entry for entry in report['frames'] if not entry['diff']['match']]
    control_changes = [entry for entry in report['frames'] if not entry['diff']['controls_match']]
    if mismatches:
        lines.extend(['', 'DIFFERENCES'])
        for entry in mismatches:
            details = '; '.join(f'{field}: full={value["full"]!r} scoped={value["scoped"]!r}'
                                for field, value in entry['diff']['differences'].items())
            lines.append(f'  {entry["frame"]}: {details}')
    if control_changes:
        lines.extend(['', 'CONTROL KEY DIFFERENCES'])
        for entry in control_changes:
            lines.append(f'  {entry["frame"]}: only_full={entry["diff"]["controls_only_full"]} '
                         f'only_scoped={entry["diff"]["controls_only_scoped"]}')
    if report['skipped']:
        lines.extend(['', 'SKIPPED'])
        for skipped in report['skipped']:
            lines.append(f'  {skipped["path"]}: {skipped["reason"]}')
    if not report['frames']:
        lines.extend(['', 'NO READABLE FRAMES to compare; see SKIPPED above.'])
    summary = report['summary']
    lines.extend(['', 'SUMMARY ' + ' '.join(f'{key}={value}' for key, value in summary.items())])
    return '\n'.join(lines) + '\n'


def build_parser():
    parser = argparse.ArgumentParser(
        prog='perf_scope_ab.py',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--frames', nargs='+', action='append', default=None, metavar='PATH',
                        help='frame files and/or directories (default: tests/data); '
                             'repeated/comma-separated values are accepted')
    parser.add_argument('--scope', default=DEFAULT_SCOPE, metavar='KINDS',
                        help=f'comma-separated narrowed probe kinds (default: {DEFAULT_SCOPE}); '
                             'production still adds its canary kinds')
    parser.add_argument('--pattern', default=DEFAULT_PATTERN, metavar='GLOB',
                        help=f'directory frame glob (default: {DEFAULT_PATTERN})')
    parser.add_argument('--limit', type=int, default=None, metavar='N',
                        help='run at most N readable frames (default: all)')
    parser.add_argument('--timeout', type=float, default=float(scene.OBSERVATION_TIMEOUT_SECONDS),
                        metavar='SECONDS', help='per-observation timeout')
    parser.add_argument('--json', action='store_true', help='emit the full report as JSON')
    return parser


def parse_options(argv=None):
    parser = build_parser()
    options = parser.parse_args(argv)
    try:
        options.scope_kinds = parse_scope(options.scope)
        options.frame_paths = split_frame_args(options.frames)
    except ValueError as error:
        parser.error(str(error))
    if options.limit is not None and options.limit < 0:
        parser.error('limit must be >= 0')
    if not (0 < options.timeout <= 90):
        parser.error('timeout must be in (0, 90]')
    if not options.pattern:
        parser.error('pattern_empty')
    return options


def main(argv=None):
    options = parse_options(argv)
    with tempfile.TemporaryDirectory(prefix='pcr-perf-scope-ab-') as runtime_dir:
        previous_runtime = native.RUNTIME
        native.RUNTIME = Path(runtime_dir)
        try:
            try:
                report = run(options.frame_paths, options.scope_kinds, pattern=options.pattern,
                             limit=options.limit, timeout=options.timeout)
            except Exception as error:
                print(f'perf_scope_ab: native run failed: {type(error).__name__}: {error}',
                      file=sys.stderr)
                return 1
        finally:
            native.RUNTIME = previous_runtime
    if options.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
