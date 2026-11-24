"""Read-only probe-scope A/B tool: argument validation, diff logic, no-frame behavior.

The tests never load the native Maa resource or construct a controller; the
``run`` paths that would need one are exercised only for empty/skipped frame
sets, where the tool must stop before any native work.
"""
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('perf_scope_ab', ROOT / 'scripts/perf_scope_ab.py')
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


def write_frame(directory, name='frame.png', shape=(720, 1280, 3), value=0):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    ok, encoded = cv2.imencode('.png', np.full(shape, value, np.uint8))
    assert ok
    path.write_bytes(encoded.tobytes())
    return path


def no_native_load(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('a run without readable frames must not load the native resource')
    monkeypatch.setattr(tool.native, 'load_resource', forbidden)


# -- scope and CLI validation -------------------------------------------------

@pytest.mark.parametrize('value,expected', [
    ('map', ('map',)),
    ('map, event', ('event', 'map')),
    ('event,map', ('event', 'map')),
    ('map,map,battle', ('battle', 'map')),
])
def test_scope_parsing_accepts_rule_kinds(value, expected):
    assert tool.parse_scope(value) == expected


@pytest.mark.parametrize('value', ['', '   ', 'map,', 'map,,event', 'bogus_kind', None, 3])
def test_scope_parsing_rejects_empty_unknown_and_malformed_values(value):
    with pytest.raises(ValueError):
        tool.parse_scope(value)


def test_cli_defaults_to_public_frames_and_map_scope():
    options = tool.parse_options([])
    assert options.frame_paths == [tool.DEFAULT_FRAMES]
    assert options.scope_kinds == ('map',)
    assert options.limit is None and options.json is False
    assert options.timeout == float(tool.scene.OBSERVATION_TIMEOUT_SECONDS)


def test_cli_accepts_repeated_and_comma_separated_frame_paths():
    options = tool.parse_options(['--frames', 'tests/data,a/b.png', '--frames', 'c',
                                  '--scope', 'map,event', '--limit', '2', '--json'])
    assert options.frame_paths == [Path('tests/data'), Path('a/b.png'), Path('c')]
    assert options.scope_kinds == ('event', 'map') and options.limit == 2 and options.json is True


@pytest.mark.parametrize('argv,message', [
    (['--scope', 'bogus'], 'unknown_probe_kind:bogus'),
    (['--scope', ''], 'scope_empty'),
    (['--limit', '-1'], 'limit must be >= 0'),
    (['--timeout', '0'], 'timeout must be in (0, 90]'),
    (['--timeout', '91'], 'timeout must be in (0, 90]'),
    (['--pattern', ''], 'pattern_empty'),
])
def test_cli_rejects_invalid_arguments(argv, message, capsys):
    with pytest.raises(SystemExit) as error:
        tool.parse_options(argv)
    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_help_exits_cleanly():
    with pytest.raises(SystemExit) as error:
        tool.parse_options(['--help'])
    assert error.value.code == 0


# -- per-run summary and diff -------------------------------------------------

def summary(kind='map', *, area=2, node_type=None, controls=None, score=.9, reason=None):
    fields = tool.summarize_scene(kind, dict(area=area, node_type=node_type, controls=controls, score=score))
    fields['reason'] = reason
    return fields


def test_scene_summary_keeps_required_fields_and_tolerates_an_absent_payload():
    fields = tool.summarize_scene('move_confirmation', dict(
        area=3, node_type=8, controls=dict(confirm={}, cancel={}), score=.88))
    assert fields['kind'] == 'move_confirmation' and fields['area'] == 3
    assert fields['node_type'] == 8 and fields['control_keys'] == ['cancel', 'confirm']
    assert fields['score'] == .88 and fields['reason'] is None
    absent = tool.summarize_scene('unknown', None)
    assert absent == dict(kind='unknown', area=None, node_type=None, control_keys=[],
                          score=None, reason=None)
    assert tool.summarize_scene(None, dict(controls=[]))['control_keys'] == []
    assert tool.summarize_scene('map', dict(score=True))['score'] is None


def test_diff_matches_semantics_and_reports_optional_control_shrinkage():
    full = summary(controls=dict(BOSS={}, close={}, next=dict(anchor='NEXT')))
    scoped = summary(controls=dict(AREA={}))
    difference = tool.diff_runs(full, scoped)
    assert difference['match'] is True
    assert difference['differences'] == {}
    assert difference['controls_match'] is False
    assert difference['controls_only_full'] == ['BOSS', 'close', 'next']
    assert difference['controls_only_scoped'] == ['AREA']


def test_diff_flags_kind_area_node_type_and_rejection_reason_changes():
    full = summary('move_confirmation', area=3, node_type=8)
    changed_kind = tool.diff_runs(full, summary('unknown', area=None, node_type=None))
    assert changed_kind['match'] is False
    assert set(changed_kind['differences']) == {'kind', 'area', 'node_type'}
    assert changed_kind['differences']['kind'] == dict(full='move_confirmation', scoped='unknown')
    changed_area = tool.diff_runs(summary(area=2), summary(area=5))
    assert list(changed_area['differences']) == ['area']
    rejected = tool.diff_runs(summary(), summary('unknown', area=None, score=None,
                                                 reason='BatchStopped:scene_unknown'))
    assert rejected['match'] is False
    assert rejected['differences']['reason']['scoped'] == 'BatchStopped:scene_unknown'
    assert tool.diff_runs(summary(), summary())['match'] is True


# -- recognition count is nullable, never a fake 0 ----------------------------

class FakeTimings:
    def __init__(self, payload):
        self.value = payload

    def payload(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_recognition_count_is_null_when_the_collector_is_unavailable():
    assert tool.recognition_count(None) is None
    assert tool.recognition_count(FakeTimings(RuntimeError('sink unavailable'))) is None
    assert tool.recognition_count(FakeTimings({})) is None
    assert tool.recognition_count(FakeTimings(dict(recognition=None))) is None
    assert tool.recognition_count(FakeTimings(dict(recognition=dict(count=None)))) is None
    assert tool.recognition_count(FakeTimings(dict(recognition=dict(count='12')))) is None


def test_recognition_count_preserves_an_observed_zero_and_a_real_count():
    assert tool.recognition_count(FakeTimings(dict(recognition=dict(count=0)))) == 0
    assert tool.recognition_count(FakeTimings(dict(recognition=dict(count=12)))) == 12


# -- frame discovery and reading ---------------------------------------------

def test_discover_frames_reports_missing_non_images_and_empty_directories(tmp_path):
    directory = tmp_path / 'frames'
    good = write_frame(directory)
    (directory / 'notes.txt').write_text('not a frame', encoding='utf-8')
    empty = tmp_path / 'empty'
    empty.mkdir()
    frames, skipped = tool.discover_frames([tmp_path / 'missing', directory, empty,
                                            good, good, directory / 'notes.txt'],
                                           pattern='*.png')
    assert frames == [good]
    assert [entry['reason'] for entry in skipped] == [
        'frame_missing', 'no_matching_frames', 'not_an_image']
    assert [Path(entry['path']).name for entry in skipped[:1]] == ['missing']


def test_discover_frames_honours_the_directory_glob(tmp_path):
    write_frame(tmp_path / 'data', 'labyrinth-map.png')
    write_frame(tmp_path / 'data', 'clan-target.png')
    frames, _ = tool.discover_frames([tmp_path / 'data'])
    assert [path.name for path in frames] == ['labyrinth-map.png']
    frames, skipped = tool.discover_frames([tmp_path / 'data'], pattern='*.jpg')
    assert frames == [] and [entry['reason'] for entry in skipped] == ['no_matching_frames']


def test_read_frame_distinguishes_wrong_size_from_unreadable(tmp_path):
    image, reason = tool.read_frame(write_frame(tmp_path, 'ok.png'))
    assert image is not None and image.shape[:2] == (720, 1280) and reason is None
    _, reason = tool.read_frame(write_frame(tmp_path, 'tiny.png', shape=(10, 10, 3)))
    assert reason == 'frame_size_invalid'
    junk = tmp_path / 'junk.png'
    junk.write_bytes(b'not a png')
    assert tool.read_frame(junk) == (None, 'frame_unreadable')


# -- no-frame runs never load the native resource ----------------------------

def test_missing_and_unreadable_frames_are_skipped_not_reported_as_passes(monkeypatch, tmp_path):
    no_native_load(monkeypatch)
    junk = tmp_path / 'junk.png'
    junk.write_bytes(b'not a png')
    report = tool.run([tmp_path / 'missing', junk], ('map',))
    assert report['resource']['loaded'] is False
    assert report['frames'] == []
    assert [entry['reason'] for entry in report['skipped']] == ['frame_missing', 'frame_unreadable']
    summary = report['summary']
    assert summary['frames'] == 0 and summary['skipped'] == 2
    assert summary['recognition_count_full'] is None and summary['recognition_count_scoped'] is None
    assert summary['recognition_delta'] is None and summary['input_count'] == 0


def test_limit_zero_selects_no_frame_and_never_loads_the_resource(monkeypatch, tmp_path):
    no_native_load(monkeypatch)
    frame = write_frame(tmp_path)
    report = tool.run([frame], ('map',), limit=0)
    assert report['frames'] == [] and report['resource']['loaded'] is False
    assert report['summary']['discovered'] == 1
    assert report['summary']['frames'] == 0


def test_limited_run_reports_discovered_but_not_skipped_frames(monkeypatch, tmp_path):
    no_native_load(monkeypatch)
    directory = tmp_path / 'data'
    write_frame(directory, 'labyrinth-a.png')
    write_frame(directory, 'labyrinth-b.png')
    report = tool.run([directory], ('map',), limit=0)
    assert report['summary']['discovered'] == 2 and report['skipped'] == []


def test_json_report_keeps_null_recognition_counts_and_skip_reasons(monkeypatch, tmp_path):
    no_native_load(monkeypatch)
    report = tool.run([tmp_path / 'missing'], ('map',))
    decoded = json.loads(json.dumps(report, ensure_ascii=False))
    assert decoded['skipped'] == [dict(path=str(tmp_path / 'missing'), reason='frame_missing')]
    assert decoded['summary']['recognition_count_full'] is None
    assert decoded['scope'] == dict(full=None, scoped=['map'])


def test_format_report_marks_no_readable_frames_and_skips(monkeypatch, tmp_path):
    no_native_load(monkeypatch)
    report = tool.run([tmp_path / 'missing'], ('map',))
    text = tool.format_report(report)
    assert 'NO READABLE FRAMES' in text
    assert 'frame_missing' in text
    assert 'full probe_kinds=None vs scoped probe_kinds=map' in text
    assert 'frames=0' in text


def test_main_json_run_without_frames_returns_zero_without_touching_a_device(monkeypatch, tmp_path, capsys):
    no_native_load(monkeypatch)
    code = tool.main(['--frames', str(tmp_path / 'missing'), '--json'])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report['resource']['loaded'] is False
    assert report['skipped'] == [dict(path=str(tmp_path / 'missing'), reason='frame_missing')]
    assert report['summary']['recognition_count_full'] is None


def test_main_text_run_without_frames_reports_scope_and_summary(monkeypatch, tmp_path, capsys):
    no_native_load(monkeypatch)
    assert tool.main(['--frames', str(tmp_path / 'missing')]) == 0
    output = capsys.readouterr().out
    assert 'perf_scope_ab: offline scene recognition A/B' in output
    assert 'NO READABLE FRAMES' in output
    assert 'SUMMARY discovered=0 frames=0 skipped=1' in output


def test_main_reports_a_native_startup_failure_without_traceback(monkeypatch, tmp_path, capsys):
    frame = write_frame(tmp_path)

    def fail():
        raise RuntimeError('resource load failed')

    monkeypatch.setattr(tool.native, 'load_resource', fail)
    assert tool.main(['--frames', str(frame)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'perf_scope_ab: native run failed: RuntimeError: resource load failed' in captured.err
