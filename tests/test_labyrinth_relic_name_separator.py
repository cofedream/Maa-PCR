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

Live run 2026-11-08T03:17:37Z (player-log run `65bdcc4e34b74c0b9d89707782ea4f74`,
`enter_id=13473`, `relic_id=35003`): the offer frame read `自光辉铠` + `·断壁`
(`白` misread as `自`), while the click frame read `白光辉铠·断壁`. The static
icon catalog had already identified the card as `35003` and journaled its
canonical name, but the fresh click guard was still built from the observed OCR
spelling, so it expected `自` and rejected the real card; no input was sent.
The trusted catalog name must therefore replace the OCR spelling in the guard,
without adding any character tolerance.
"""
import os
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcr_maa.labyrinth import relics as R
from pcr_maa.labyrinth.batch import BatchStopped

SEPARATORS = '·・‧•'
LIVE_NAME = '白光辉铠·断壁'          # click-frame reading / catalog name
JOURNALED_NAME = '白光辉铠断壁'      # offer-frame reading that stopped the run
OCR_MISREAD_NAME = '自光辉铠·断壁'   # 2026-11-08 offer-frame reading
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


def test_guard_uses_the_trusted_catalog_name_not_the_offer_ocr_spelling():
    observed = _offers(OCR_MISREAD_NAME)
    fact = dict(id=35003, name=LIVE_NAME, mark='守备', bonus=3, rarity=3)
    identified = R.attach_offer_identity(observed, (fact, None, None))
    expected = R.selection_override(identified, 0)[
        'LabyRelicGuardName00Line']['expected']
    assert re.fullmatch(expected, LIVE_NAME)
    assert re.fullmatch(expected, OCR_MISREAD_NAME) is None
    # The override is name-only: a different card name still refuses input.
    assert re.fullmatch(expected, '幽玄煌装·绝魔') is None
    # Without a trusted catalog fact the existing OCR path is unchanged.
    text_only = _name_guard_expected(OCR_MISREAD_NAME)
    assert re.fullmatch(text_only, OCR_MISREAD_NAME)
    assert re.fullmatch(text_only, LIVE_NAME) is None


@pytest.mark.parametrize('name', ['', '   ', '白×1', '白\n光辉铠', 3, True, 'x' * 49])
def test_catalog_identity_name_must_be_a_plain_display_name(name):
    fact = dict(id=35003, name=name, mark='守备', bonus=3, rarity=3)
    with pytest.raises(BatchStopped, match='relic_identity_unknown'):
        R.attach_offer_identity(_offers(OCR_MISREAD_NAME), (fact, None, None))


class _GuardResource:
    def __init__(self):
        self.nodes = deepcopy(R.relic_pipeline())
        self.calls = []

    def get_node_data(self, name):
        return deepcopy(self.nodes.get(name))

    def override_pipeline(self, override):
        self.calls.append(deepcopy(override))
        return True


def test_selection_guard_installs_the_catalog_name_override():
    fact = dict(id=35003, name=LIVE_NAME, mark='守备', bonus=3, rarity=3)
    identified = R.attach_offer_identity(_offers(OCR_MISREAD_NAME), (fact, None, None))
    resource = _GuardResource()
    with R.selection_guard(SimpleNamespace(resource=resource), identified, 0):
        expected = resource.calls[-1]['LabyRelicGuardName00Line']['expected']
        assert expected == resource.calls[-1]['LabyRelicGuardName00Detected']['expected']
        assert re.fullmatch(expected, LIVE_NAME)
        assert re.fullmatch(expected, OCR_MISREAD_NAME) is None


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
