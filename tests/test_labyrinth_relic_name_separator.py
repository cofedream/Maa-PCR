"""A relic name's display separator is not part of its identity.

Live run 2026-11-02T13:11:44Z (ledger run `59a631199d944da7870e4fb9ac06bf84`,
`enter_id=13439`): the offer frame lost the `·` of `白光辉铠·断壁` (OCR split the
line into `白光辉铠` + a 0.24 `D` token below the 0.85 threshold + `断壁`), while
the click-frame line read `白光辉铠·断壁` as one token. The exact name guard
rejected a correct card, the click never started and the batch stopped with
`native_input_not_sent`. A separator may therefore be required or dropped by
either frame; it must never be required in the guard pattern.

Live run 2026-11-07T13:24:19Z (ledger run `954368389b32431baba4d7fdb76dcc14`,
`enter_id=13461`): the offer frame read `幽玄煌装` + a 0.14 token + `绝魔`, so the
journaled name was `幽玄煌装绝魔`, while the obtained modal read the line as
`幽玄煌装·绝魔×1`. The same separator rule reached the guard, but the result
layer still compared the two spellings verbatim and stopped the batch with
`relic_result_mismatch` after the reward had actually been acquired.
"""
import os
import re
from pathlib import Path

import pytest

from pcr_maa.labyrinth import relics as R
from pcr_maa.labyrinth.batch import BatchStopped

SEPARATORS = '·・‧•'
LIVE_NAME = '白光辉铠·断壁'          # click-frame reading
JOURNALED_NAME = '白光辉铠断壁'      # offer-frame reading that stopped the run
OBTAINED_JOURNALED_NAME = '幽玄煌装绝魔'   # 2026-11-07 offer-frame reading
OBTAINED_RESULT_TEXT = '幽玄煌装·绝魔×1'   # 2026-11-07 obtained-modal line


def _words(texts, roi, widths):
    words = []
    cursor = roi[0]
    for text, width in zip(texts, widths):
        words.append(R.Word(text, (cursor, roi[1], width, roi[3]), .99))
        cursor += width
    return tuple(words)


def _offer(slot, name_words, mark, bonus, current):
    attribute = _words(('【' + mark + '+' + str(bonus) + '】', '（当前：' + str(current) + '）'),
                       R.ATTRIBUTE_ROIS[slot], (110, 123))
    button = (R.Word('选择', (R.BUTTON_ROIS[slot][0] + 65, R.BUTTON_ROIS[slot][1] + 29, 60, 31), .99),)
    return R._offer(slot, name_words, attribute, button)


def _offers(observed_name):
    """Three valid offers; only slot 0 is under test and keeps the live reading."""
    return (
        _offer(0, _words((observed_name,), R.NAME_ROIS[0], (220,)), '守备', 3, 14),
        _offer(1, _words(('苍蓝之刃',), R.NAME_ROIS[1], (120,)), '强化', 2, 5),
        _offer(2, _words(('疾风之靴',), R.NAME_ROIS[2], (120,)), '会心', 4, 9),
    )


def _name_guard_expected(observed_name):
    overrides = R.selection_override(_offers(observed_name), 0)
    assert overrides['LabyRelicGuardName00Detected']['expected'] == \
        overrides['LabyRelicGuardName00Line']['expected']
    return overrides['LabyRelicGuardName00Detected']['expected']


def test_guard_accepts_dotted_readback_when_offer_frame_dropped_the_separator():
    expected = _name_guard_expected(JOURNALED_NAME)
    for separator in SEPARATORS:
        assert re.fullmatch(expected, '白光辉铠' + separator + '断壁'), expected
    assert re.fullmatch(expected, JOURNALED_NAME)


def test_guard_accepts_undotted_readback_when_offer_frame_kept_the_separator():
    expected = _name_guard_expected(LIVE_NAME)
    assert re.fullmatch(expected, JOURNALED_NAME)
    assert re.fullmatch(expected, LIVE_NAME)


def test_separator_tolerance_is_not_character_tolerance():
    expected = _name_guard_expected(JOURNALED_NAME)
    # Cross-font / wrong-character substitutions still refuse input.
    assert re.fullmatch(expected, '白光辉铠·破壁') is None
    assert re.fullmatch(expected, '白光辉挥·断壁') is None
    assert re.fullmatch(expected, '白光辉铠·断壁X') is None
    assert re.fullmatch(expected, '白光辉铠') is None


def test_separator_class_is_literal_for_the_native_regex_engine():
    expected = _name_guard_expected(JOURNALED_NAME)
    # Maa's native engine is not Python: it does not expand `\uXXXX`, so the
    # class must spell the characters out.
    assert '\\u' not in expected
    assert '·' in expected and '・' in expected
    assert re.fullmatch(expected, '白光辉铠・断壁')


def test_other_punctuation_fields_stay_unchanged():
    # The separator class is additive only; the existing full/half-width rules
    # and the rest of the identity string are untouched.
    assert R._normalized('【守备＋3】（当前：14）') == R._normalized('【守备+3】(当前:14)')
    assert re.fullmatch(R._pattern('白光辉铠断壁×1'), '白光辉铠·断壁×1')


def _result_receipt(item_text, *, icon):
    """One obtained-modal receipt; boxes mirror the 2026-11-07 live frame."""
    def line(text, roi):
        return dict(filtered=[dict(text=text, box=list(roi), score=.99)])
    item = [dict(text=item_text, box=[454, 186, 186, 20], score=.978742)]
    rows = [
        dict(name='LabyRelicObtainedTitle', detail=line('获得道具', R.RESULT_TITLE_ROI)),
        dict(name='LabyRelicObtainedMessage', detail=line('获得了以下道具。', R.RESULT_MESSAGE_ROI)),
        dict(name='LabyRelicObtainedItem', detail=dict(filtered=item)),
        dict(name='LabyRelicObtainedClose', detail=line('关闭', R.RESULT_CLOSE_ROI)),
    ]
    if icon:
        rows.append(dict(name='LabyRelicObtainedIdentity', detail=[dict(
            algorithm='OCR', name='LabyRelicObtainedExactItem', box=[454, 186, 186, 20],
            detail=dict(filtered=item))]))
    return dict(native_ok=True, nodes=[dict(name='LabyRelicObtainedObserved',
        completed=True, recognition=rows)])


@pytest.mark.parametrize('icon', [False, True])
def test_obtained_result_accepts_the_result_frame_separator(icon):
    # 2026-11-07: the journaled name lost the dot, the obtained modal kept it.
    # The guard and the native OCR node already tolerated that; the Python
    # re-check must use the same identity instead of a verbatim comparison.
    value = R.relic_obtained(_result_receipt(OBTAINED_RESULT_TEXT, icon=icon),
                             OBTAINED_JOURNALED_NAME, icon=icon)
    assert value['verified'] and value['relic_name'] == OBTAINED_JOURNALED_NAME
    assert value['observed_result_text'] == OBTAINED_RESULT_TEXT
    assert value['identity_method'] == 'exact_ocr'


@pytest.mark.parametrize('item_text', ['幽玄煌装·破魔×1', '幽玄煌装·绝魔×2',
                                       '幽玄煌装·绝×1', '幽玄煌装绝魔×1X'])
def test_obtained_result_still_refuses_a_different_item(item_text):
    # Separator tolerance is not character, quantity or tail tolerance.
    with pytest.raises(BatchStopped, match='relic_result_mismatch'):
        R.relic_obtained(_result_receipt(item_text, icon=False), OBTAINED_JOURNALED_NAME)


def test_item_identity_uses_the_guard_pattern_only():
    assert R._item_identity_matches(OBTAINED_RESULT_TEXT, OBTAINED_JOURNALED_NAME)
    assert R._item_identity_matches('幽玄煌装・绝魔×1', OBTAINED_JOURNALED_NAME)
    assert R._item_identity_matches('幽玄煌装绝魔×1', OBTAINED_JOURNALED_NAME)
    assert not R._item_identity_matches('幽玄煌装·破魔×1', OBTAINED_JOURNALED_NAME)
    assert not R._item_identity_matches('幽玄煌装·绝魔×2', OBTAINED_JOURNALED_NAME)


@pytest.mark.private_replay
def test_saved_selection_frame_sends_the_clicked_relic(monkeypatch):
    source = os.environ.get('PCR_LABYRINTH_RELIC_FRAME')
    if not source:
        pytest.skip('private saved relic-selection frame is not distributed')
    import socket
    def deny(*args, **kwargs):
        raise AssertionError('saved-frame replay only')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    image = native.read_image(Path(source))
    resource = native.load_resource()
    controller = SavedImages([[image]])
    tasker = native.bind(resource, controller)
    assert resource.override_pipeline(R.relic_pipeline())
    assert resource.register_custom_recognition('LabyRelicSelectedIconCapture', R.RelicIconCapture())
    try:
        with R.selection_guard(tasker, _offers(JOURNALED_NAME), 0, icon=True):
            receipt = native.run(tasker, 'LabyRelicSelectOnce')
    finally:
        native.stop_tasker(tasker)
    assert receipt['native_ok'] is True
    assert receipt['native_evidence']['action_started'] is True
    assert len(controller.clicks) == 1 and not controller.swipes
    x, y = controller.clicks[0]
    button = R.BUTTON_ROIS[0]
    assert button[0] <= x <= button[0] + button[2], controller.clicks
    assert button[1] <= y <= button[1] + button[3], controller.clicks
