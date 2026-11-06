"""Public icon art must retain its identity across observed card border widths."""
import cv2
import numpy as np
import pytest

from pcr_maa.labyrinth import character_recognition as cr


@pytest.fixture(scope='module')
def matcher():
    return cr.IconMatcher()


@pytest.mark.parametrize('page,size', [('battle', 130), ('boss', 130), ('roster', 120)])
@pytest.mark.parametrize('inset,trim', [(6, 14), (3, 6)])
def test_card_border_width_does_not_change_croce_to_christmas_saren(matcher, page, size, inset, trim):
    frame = np.full((720, 1280, 3), 245, np.uint8)
    x, y = 100, 260
    index = next(i for i, e in enumerate(matcher.entries) if e['unit_icon_id'] == '126431')
    width = size - trim
    frame[y+inset:y+inset+width, x+inset:x+inset+width] = cv2.resize(matcher.images[index][..., ::-1], (width, width))
    recognizer = cr.CharacterRecognizer(matcher=matcher)
    box = (x, y, size, size)
    cards = recognizer._scanned_cards(frame, (box,), {box: 'visible'}, {}, {}, page,
        cr.VIEWPORTS[page], ('1145', '1264', '1058', '1122', '1332'), dict(frame=frame))
    assert cards[0]['trusted'] and cards[0]['character_id'] == '1264'
    assert cards[0]['rival_margin'] >= cr.RIVAL_MARGIN


def test_conflicting_trusted_crops_revoke_identity(matcher, monkeypatch):
    def result(role, score):
        candidate = cr.IconCandidate(role, role, '31', score, role, role+'31')
        return cr.IconMatch(role, role, '31', score, 0, score, True, (candidate,), role+'31')

    initial = result('1264', .70)
    monkeypatch.setattr(matcher, 'match', lambda *a, **kw: result('1145', .71))
    frame = np.zeros((720, 1280, 3), np.uint8)
    actual = matcher.refine(frame, (106, 266, 116, 116), initial, fallback=(103, 263, 124, 124))
    assert not actual.trusted and actual.character_id is None
    assert actual.rival_confidence == .70
    assert actual.rival_margin < cr.RIVAL_MARGIN
