import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


SPEC = importlib.util.spec_from_file_location('clan_hud_benchmark', Path(__file__).resolve().parents[1]/'scripts/benchmark-clan-hud.py')
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


@pytest.mark.parametrize('text,expected', [('1:28', 88), ('0：07', 7), ('1 : 29', 89),
                                         ('1:30', None), ('0:60', None), ('1:28 extra', None)])
def test_clock_uses_existing_timer_range(text, expected):
    assert benchmark.clock_seconds(NS(hit=True, raw_detail=dict(filtered=[dict(text=text)]))) == expected


def test_clock_unknown_and_multiple_matches_stay_unknown():
    assert benchmark.clock_seconds(NS(hit=False, raw_detail={})) is None
    assert benchmark.clock_seconds(NS(hit=True, raw_detail=dict(filtered=[dict(text='1:28')]*2))) is None


@pytest.mark.parametrize('ids,complete', [([1, 2, 3, 4, 5], True), ([1, 2, 3, 4, None], False)])
def test_observation_reads_one_shared_image_and_keeps_obscured_identity(ids, complete):
    image, calls = object(), []

    def recognize(name, frame):
        calls.append((name, frame))
        return NS(hit=name != 'GTPauseTitle', raw_detail=dict(filtered=[dict(text='1:28')]))

    def controls(frame):
        calls.append(('controls', frame))
        return dict(auto=True, sets=[True]*5, unit_ids=ids)

    result = benchmark.observe(NS(run_recognition=recognize), image,
                               NS(team=[1, 2, 3, 4, 5], read_controls=controls))
    assert calls == [(name, image) for name in ('GTBattleTimer', 'controls')]
    assert result['remaining_seconds'] == 88 and result['controls_complete'] is complete
    assert result['state']['unit_ids'] == ids
    assert result['callback_seconds'] >= result['hud_seconds'] >= 0


def test_manifest_binds_frame_hash_and_expectations(tmp_path):
    frame = tmp_path/'frame.png'
    frame.write_bytes(b'original-saved-image')
    sample = dict(id='frame', path=str(frame), sha256=benchmark.digest(frame), stage='红焰深域 8-6',
                  expected_remaining_seconds=88, expected_controls_complete=False)
    assert benchmark.validate_samples(dict(samples=[sample])) == [sample]
    with pytest.raises(ValueError, match='invalid_or_duplicate_sample_id'):
        benchmark.validate_samples(dict(samples=[sample, sample]))
    frame.write_bytes(b'changed')
    with pytest.raises(ValueError, match='saved_frame_hash_mismatch'):
        benchmark.validate_samples(dict(samples=[sample]))


def test_report_keeps_cold_and_warm_max_separate():
    rows = [dict(repeat=i, **{key: value for key in ('end_to_end_seconds', 'ocr_seconds', 'hud_seconds', 'callback_seconds')})
            for i, value in enumerate([.8, .2, .3])]
    summary = benchmark.summarize(rows)
    assert summary['end_to_end_seconds'] == dict(max_seconds=.8, max_warm_seconds=.3)
