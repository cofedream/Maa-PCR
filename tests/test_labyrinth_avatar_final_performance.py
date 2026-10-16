"""Fine calculation may omit only the already rejected template rows."""
from dataclasses import replace
import numpy as np
import pytest

from pcr_maa.labyrinth import character_recognition as c
from pcr_maa.labyrinth.batch import BatchStopped
from test_labyrinth_vision_performance import original_scores, art_frame


@pytest.fixture(scope='module')
def matcher():
    value = c.IconMatcher()
    assert len(value.entries) == 801
    return value


MASKS = (c.IconMask(), c.IconMask(right=True), c.IconMask(top_left=True), c.IconMask(top_right=True),
    c.IconMask(bottom_left=True), c.IconMask(bottom_band=True),
    c.IconMask(right=True, top_left=True, top_right=True, bottom_left=True, bottom_band=True),
    c.IconMask(min_y=.25, max_y=.80), c.IconMask(min_y=.95, max_y=1), c.IconMask(min_y=.9, max_y=.1))
BOXES = ((106, 206, 116, 116), (0, 0, 128, 128), (1152, 592, 128, 128), (0, 0, 1, 128), (1270, 704, 10, 16))


@pytest.mark.parametrize('mask', MASKS)
@pytest.mark.parametrize('box', BOXES)
def test_default_full_scores_and_requested_fine_rows_are_byte_equal(matcher, mask, box):
    frame = art_frame(matcher)
    prepared = dict(frame=frame)
    for width in (12, 20):
        expected = original_scores(matcher, frame, box, mask, width)
        actual = matcher._scores(frame, box, mask, width, _prepared=prepared)
        assert actual.shape == expected.shape == (801,)
        assert actual.dtype == expected.dtype == np.float64
        assert actual.tobytes() == expected.tobytes()
    # Full shape preserves the original-index consumer and existing formula replacement tests.
    # Unrequested private rows are neither consumed by match nor published as diagnostics.
    for indices in (np.array([800, 0, 42]), np.arange(512), np.array([], dtype=int)):
        selected = matcher._scores(frame, box, mask, 20, _prepared=prepared, _indices=indices)
        assert selected.shape == (801,)
        assert selected[indices].tobytes() == expected[indices].tobytes()
        omitted = np.ones(801, dtype=bool)
        omitted[indices] = False
        assert not selected[omitted].any()


@pytest.mark.parametrize('mask,attribute,roster', [
    (MASKS[0], None, None), (MASKS[1], 'WATER', ['1001', '1059', '1075']),
    (MASKS[2], 'FIRE', ['1183', '1184', '1807']), (MASKS[3], 'LIGHT', ['1059']),
    (MASKS[4], 'DARK', ['1351', '1075']), (MASKS[5], 'WIND', []),
    (MASKS[6], None, ['1001']), (MASKS[7], 'NO_SUCH_ATTRIBUTE', None),
    (MASKS[8], None, None), (MASKS[9], None, None),
])
def test_complete_result_and_low_score_refine_match_original(matcher, monkeypatch, mask, attribute, roster):
    frame = art_frame(matcher)
    box = BOXES[0]
    options = dict(mask=mask, attribute=attribute, roster=roster)
    actual = matcher.match(frame, box, **options)
    initial = replace(actual, trusted=False)
    refined = matcher.refine(frame, box, initial, fallback=(104, 204, 120, 120), **options)
    with monkeypatch.context() as patch:
        patch.setattr(matcher, '_scores', lambda *a, **kw: original_scores(matcher, *a, **kw))
        assert matcher.match(frame, box, **options) == actual
        assert matcher.refine(frame, box, initial, fallback=(104, 204, 120, 120), **options) == refined


def test_stable_ties_keep_order_variant_merge_and_rival(matcher, monkeypatch):
    frame = art_frame(matcher)
    requested = []

    def fixed(frame, box, mask, width, *, _prepared=None, _indices=None):
        if width == 12:
            return np.zeros(801)
        assert _indices is not None
        requested.append(_indices.copy())
        out = np.zeros(801)
        out[_indices] = .5
        return out

    monkeypatch.setattr(matcher, '_scores', fixed)
    actual = matcher.match(frame, BOXES[0])
    assert requested[0].tolist() == list(range(512))
    expected_ids = list(dict.fromkeys(entry['character_id'] for entry in matcher.entries[:512]))[:5]
    assert [item.character_id for item in actual.candidates] == expected_ids
    assert actual.confidence == actual.rival_confidence == .5
    assert not actual.trusted and actual.character_id is None
    assert actual.candidates[0].variant == matcher.entries[0]['variant']


@pytest.mark.parametrize('gap,inside_selected',[(.079, True), (.08, False), (.081, False)])
def test_roster_override_keeps_strict_existing_gap(matcher, monkeypatch, gap, inside_selected):
    frame = art_frame(matcher)
    outside = 0
    inside = next(i for i, entry in enumerate(matcher.entries) if entry['character_id'] != matcher.entries[outside]['character_id'])
    requested = []

    def fixed(frame, box, mask, width, *, _prepared=None, _indices=None):
        out = np.zeros(801)
        if width == 12:
            out[outside] = gap
        else:
            requested.append(_indices.tolist())
        return out

    monkeypatch.setattr(matcher, '_scores', fixed)
    role = matcher.entries[inside]['character_id']
    matcher.match(frame, BOXES[0], roster=[role])
    assert (outside not in requested[0]) is inside_selected
    if inside_selected:
        assert all(matcher.entries[i]['character_id'] == role for i in requested[0])


def test_private_fine_subset_still_rejects_different_array(matcher):
    frame = art_frame(matcher)
    prepared = dict(frame=frame)
    with pytest.raises(BatchStopped, match='^character_frame_unknown$'):
        matcher._scores(frame.copy(), BOXES[0], MASKS[0], 20, _prepared=prepared, _indices=np.arange(64))
