"""Team pages only show the run's joined characters; the fine stage stays inside.

Live run 8cef9d9e (2026-11-01 21:46) stopped with native_input_not_sent while
adding the borrowed card 1215 (步美·怪盗) to an EX team. The two reads before the
click identified 1215, but the click-confirmation frame matched off-roster
若菜(冬日) 1387 at .5376 against 1215 at .4931: margin .0445, just below
RIVAL_MARGIN .045. The former roster override narrowed only when the catalog
leader was within .08 of the roster leader, and here the off-roster lead was
.139, so the rival was allowed to win and the click was never sent.

The team grid equals the in-memory run roster at every observed point of that
run (3/3, 7/7, 14/14, 15/15 cards), so a supplied roster now always narrows the
fine stage. These tests pin the row narrowing, the empty-intersection fallback
and the saved live frames.
"""
from pathlib import Path

import numpy as np
import pytest

from pcr_maa.labyrinth import character_recognition as c

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / '.runtime/ocr-failures/8cef9d9ef5804011909422d19faf42a2'
FAILED_FRAMES = (RUN / '709112a2e725400ca08514f8deef46a3-frame.png',
                 RUN / '8888703a0ddd4d748184d751ac91ea57-frame.png')
ROSTER = ['1103', '1308', '1335', '1164', '1090', '1275', '1095', '1036',
          '1045', '1044', '1022', '1120', '1031', '1215', '1323']
# The dark filter list of that frame: five ordinary cards plus the borrowed 1215.
VISIBLE = ['1044', '1045', '1031', '1022', '1120', '1215']


@pytest.fixture(scope='module')
def matcher():
    value = c.IconMatcher()
    assert len(value.entries) == 801
    return value


def _leading_role(matcher):
    outside = 0
    role = next(entry['character_id'] for entry in matcher.entries
                if entry['character_id'] != matcher.entries[outside]['character_id'])
    return outside, role


def _install_scores(matcher, monkeypatch, gap, outside):
    requested = []

    def fixed(frame, box, mask, width, *, _prepared=None, _indices=None):
        out = np.zeros(len(matcher.entries))
        if width == 12:
            out[outside] = gap
        else:
            requested.append(_indices.copy())
        return out

    monkeypatch.setattr(matcher, '_scores', fixed)
    return requested


@pytest.mark.parametrize('gap', (.0, .079, .08, .081, .5))
def test_roster_narrows_fine_rows_regardless_of_the_coarse_gap(matcher, monkeypatch, gap):
    outside, role = _leading_role(matcher)
    role_rows = [index for index, entry in enumerate(matcher.entries)
                 if entry['character_id'] == role]
    requested = _install_scores(matcher, monkeypatch, gap, outside)
    matcher.match(np.zeros((720, 1280, 3), dtype=np.uint8), (106, 206, 116, 116), roster=[role])
    assert requested, 'fine stage must run'
    # The off-roster catalog leader never enters the fine comparison.
    assert outside not in requested[0]
    assert set(requested[0].tolist()) == set(role_rows)


def test_roster_without_pack_rows_falls_back_to_the_catalog(matcher, monkeypatch):
    outside, role = _leading_role(matcher)
    requested = _install_scores(matcher, monkeypatch, .5, outside)
    matcher.match(np.zeros((720, 1280, 3), dtype=np.uint8), (106, 206, 116, 116), roster=['9999'])
    # No pack row matches the roster: the catalog head (off-roster leader first)
    # is still compared instead of returning an empty candidate set.
    assert requested[0][0] == outside
    assert outside in requested[0]


def test_no_roster_keeps_the_catalog_fine_rows(matcher, monkeypatch):
    outside, role = _leading_role(matcher)
    requested = _install_scores(matcher, monkeypatch, .5, outside)
    matcher.match(np.zeros((720, 1280, 3), dtype=np.uint8), (106, 206, 116, 116))
    assert requested[0][0] == outside
    assert outside in requested[0]


def test_saved_live_frames_identify_the_borrowed_card_inside_the_roster():
    if not all(path.is_file() for path in FAILED_FRAMES):
        pytest.skip('private live roster frames excluded from public source')
    import cv2

    expected = list(VISIBLE)
    for path in FAILED_FRAMES:
        frame = cv2.imread(str(path))
        assert frame is not None and frame.shape == (720, 1280, 3)
        observation = c.CharacterRecognizer().recognize(
            frame, 'battle', roster=ROSTER, current_filter='DARK')
        cards = {card['slot_id']: card for card in observation['cards']}
        target = cards['available_row_1_6']
        assert target['trusted'] is True and target['character_id'] == '1215'
        assert target['rival_margin'] >= c.RIVAL_MARGIN
        assert sorted(card['character_id'] for card in observation['cards']) == sorted(expected)
        # Hard restriction invariant: a battle card is either resolved inside the
        # run roster or explicitly unknown, never an off-roster identity.
        assert all(card['character_id'] is None or card['character_id'] in ROSTER
                   for card in observation['cards'])
        assert all(member['character_id'] in ROSTER for member in observation['members'])
