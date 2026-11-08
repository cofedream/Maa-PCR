"""Static relic-icon identity on the offer page and the obtained modal.

The catalog icon is the game's own 128x128 artwork, so one frame's icon can be
identified without the OCR name; the masterdata row then supplies the canonical
name, mark and bonus. Identity is only trusted above the matcher's confidence
and rival-margin gates; a missing pack keeps the existing text path, a corrupt
pack stops, and a trusted icon whose catalog mark/bonus contradicts the
same-frame attribute line stops instead of writing a wrong stack fact.
"""
import json
from pathlib import Path

import numpy as np
import cv2
import pytest

from pcr_maa.labyrinth import relics as R
from pcr_maa.labyrinth.batch import BatchStopped

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / 'assets/labyrinth/relic-pack/icons'
CATALOG = ROOT / 'assets/labyrinth/labyrinth-relics.json'
SELECTED_NAME = '幽玄煌装绝魔'          # 2026-11-07 offer-frame reading (dot lost)
CANONICAL_NAME = '幽玄煌装·绝魔'       # masterdata spelling
RESULT_TEXT = '幽玄煌装·绝魔×1'


class _Args:
    def __init__(self, **values):
        self.__dict__.update(values)


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


def _offers(name=SELECTED_NAME):
    return (
        _offer(0, _words((name,), R.NAME_ROIS[0], (220,)), '守备', 3, 14),
        _offer(1, _words(('苍蓝之刃',), R.NAME_ROIS[1], (120,)), '强化', 2, 5),
        _offer(2, _words(('疾风之靴',), R.NAME_ROIS[2], (120,)), '会心', 4, 9),
    )


def _slot(relic_id, name, mark, bonus, *, trusted=True):
    return dict(trusted=trusted, score=.97, rival=.42, margin=.55,
                relic=None if not trusted else dict(id=relic_id, name=name, mark=mark,
                                                    bonus=bonus, rarity=3))


def _offer_detail(*slots):
    return dict(mode='offers', slots=list(slots))


def _native(detail, box):
    """Native node receipt wraps a custom result as all/best/filtered."""
    entry = dict(box=list(box), detail=detail)
    return dict(all=[entry], best=entry, filtered=[entry])


def _result_receipt(item_text, *, static, icon=True):
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
    if static is not None:
        rows.append(dict(name='LabyRelicObtainedStaticIdentity',
                         detail=_native(static, R.RESULT_ICON_ROI)))
    return dict(native_ok=True, nodes=[dict(name='LabyRelicObtainedObserved',
        completed=True, recognition=rows)])


def _static_result(relic_id, name, *, trusted=True):
    return dict(trusted=trusted, score=.78, rival=.49, margin=.29,
                relic=None if not trusted else dict(id=relic_id, name=name, mark='守备',
                                                    bonus=3, rarity=3))


def _icon(pid):
    raw = (ICONS / f'{pid}.png').read_bytes()
    return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)


def _paste(frame, image, box, background):
    x, y, w, h = box
    alpha = image[:, :, 3:4].astype(np.float32) / 255
    art = image[:, :, :3].astype(np.float32) * alpha + background * (1 - alpha)
    frame[y:y+h, x:x+w] = cv2.resize(art.astype(np.uint8), (w, h), interpolation=cv2.INTER_AREA)


def test_offer_facts_use_the_catalog_name_across_the_display_separator():
    facts = R.relic_offer_facts(_offer_detail(
        _slot(35004, CANONICAL_NAME, '守备', 3),
        _slot(11001, '加速玉饰', '强化', 2),
        _slot(13001, '杀戮之矛', '会心', 4)), _offers())
    assert facts[0]['id'] == 35004 and facts[0]['name'] == CANONICAL_NAME
    assert [fact['id'] for fact in facts] == [35004, 11001, 13001]


def test_identify_relic_offers_is_the_single_identity_entry():
    offers = _offers()
    identified = R.identify_relic_offers(offers, _offer_detail(
        _slot(35004, CANONICAL_NAME, '守备', 3),
        _slot(11001, '加速玉饰', '强化', 2),
        _slot(13001, '杀戮之矛', '会心', 4)))
    assert all(offer.identity is None for offer in offers)
    assert [offer.identity['id'] for offer in identified] == [35004, 11001, 13001]
    assert identified[0].name == SELECTED_NAME
    assert identified[0].identity['name'] == CANONICAL_NAME
    assert identified[0].mark == '守备' and identified[0].bonus == 3
    assert identified[0].current == 14


def test_offer_facts_stop_when_the_attribute_line_contradicts_the_icon():
    # A wrong catalog fact would corrupt the stack ledger, so never guess.
    with pytest.raises(BatchStopped, match='relic_identity_conflict'):
        R.relic_offer_facts(_offer_detail(
            _slot(35004, CANONICAL_NAME, '守备', 3),
            _slot(11001, '加速玉饰', '守备', 2),
            _slot(13001, '杀戮之矛', '会心', 4)), _offers())
    with pytest.raises(BatchStopped, match='relic_identity_conflict'):
        R.relic_offer_facts(_offer_detail(
            _slot(35004, CANONICAL_NAME, '守备', 5),
            _slot(11001, '加速玉饰', '强化', 2),
            _slot(13001, '杀戮之矛', '会心', 4)), _offers())


def test_offer_facts_keep_the_text_path_when_the_icon_is_untrusted():
    facts = R.relic_offer_facts(_offer_detail(
        _slot(35004, CANONICAL_NAME, '守备', 3, trusted=False),
        _slot(11001, '加速玉饰', '强化', 2),
        _slot(0, '', '', 0, trusted=False)), _offers())
    assert facts[0] is None and facts[1]['id'] == 11001 and facts[2] is None


def test_offer_facts_refuse_malformed_or_duplicate_catalog_rows():
    for detail in (_offer_detail(_slot(35004, CANONICAL_NAME, '守备', 3),
                                 _slot(35004, CANONICAL_NAME, '强化', 2),
                                 _slot(13001, '杀戮之矛', '会心', 4)),
                   dict(mode='offers', slots=[_slot(35004, CANONICAL_NAME, '守备', 3)]),
                   None):
        with pytest.raises(BatchStopped, match='relic_identity_'):
            R.relic_offer_facts(detail, _offers())


def test_static_result_identity_accepts_the_selected_relic():
    value = R.relic_obtained(_result_receipt(RESULT_TEXT,
        static=_static_result(35004, CANONICAL_NAME)), SELECTED_NAME,
        icon=True, static=True, relic_id=35004)
    assert value['verified'] and value['identity_method'] == 'static_icon'
    assert value['relic_id'] == 35004 and value['relic_name'] == CANONICAL_NAME
    assert value['observed_result_text'] == RESULT_TEXT


def test_static_result_identity_refuses_another_relic():
    with pytest.raises(BatchStopped, match='relic_result_mismatch'):
        R.relic_obtained(_result_receipt(RESULT_TEXT,
            static=_static_result(35003, '白光辉铠·断壁')), SELECTED_NAME,
            icon=True, static=True, relic_id=35004)


def test_static_result_requires_the_item_quantity():
    with pytest.raises(BatchStopped, match='relic_result_mismatch'):
        R.relic_obtained(_result_receipt('幽玄煌装·绝魔×2',
            static=_static_result(35004, CANONICAL_NAME)), SELECTED_NAME,
            icon=True, static=True, relic_id=35004)


def test_untrusted_static_result_falls_back_to_the_observed_text():
    # Same 2026-11-07 frame class: the OCR name lacks the display separator.
    value = R.relic_obtained(_result_receipt(RESULT_TEXT, static=_static_result(0, '', trusted=False)),
                             SELECTED_NAME, icon=True, static=True, relic_id=None)
    assert value['verified'] and value['identity_method'] == 'exact_ocr'
    with pytest.raises(BatchStopped, match='relic_result_mismatch'):
        R.relic_obtained(_result_receipt('幽玄煌装·破魔×1',
            static=_static_result(0, '', trusted=False)), SELECTED_NAME,
            icon=True, static=True, relic_id=None)


def test_static_result_requires_the_native_identity_node_to_be_present():
    # The guard adds the node only when the pack is available; a receipt that
    # claims `static` without it is a different observation, never a pass.
    receipt = _result_receipt(RESULT_TEXT, static=None)
    with pytest.raises(BatchStopped, match='relic_result_unknown'):
        R.relic_obtained(receipt, SELECTED_NAME, icon=True, static=True, relic_id=35004)


def test_custom_detail_unwraps_the_native_node_receipt_only():
    raw = dict(trusted=True, relic=dict(id=35004))
    assert R._custom_detail(_native(raw, R.RESULT_ICON_ROI)) == raw
    assert R._custom_detail(raw) is None
    assert R._custom_detail(None) is None
    assert R._custom_detail(dict(filtered=[])) is None


def test_static_icon_capture_identifies_the_result_and_offer_tiles():
    from pcr_maa.labyrinth.relic_recognition import load_catalog
    capture = R.RelicStaticIconCapture(load_catalog())
    background = np.float32([240, 240, 240])
    frame = np.full((720, 1280, 3), 240, np.uint8)
    x, y, w, h = R.RESULT_ICON_ROI
    span, dx, dy = .87, -.02, -.02
    sw, sh = round(w * span), round(h * span)
    rect = (x + round(w * (.5 + dx) - sw / 2), y + round(h * (.5 + dy) - sh / 2), sw, sh)
    _paste(frame, _icon(35004), rect, background)
    result = capture.analyze(_Args(), _Args(image=frame,
        custom_recognition_param=json.dumps(dict(mode='obtained'))))
    assert result.detail['trusted'] is True and result.detail['relic']['id'] == 35004
    assert result.detail['relic']['name'] == CANONICAL_NAME
    board = np.full((720, 1280, 3), 20, np.uint8)
    for pid, box in zip((35004, 35001, 31006), R.ICON_ROIS):
        _paste(board, _icon(pid), box, np.float32([20, 20, 20]))
    slots = capture.analyze(_Args(), _Args(image=board,
        custom_recognition_param=json.dumps(dict(mode='offers')))).detail['slots']
    assert [slot['relic']['id'] for slot in slots] == [35004, 35001, 31006]
    assert all(slot['trusted'] for slot in slots)


def test_static_icon_capture_without_a_pack_keeps_the_text_path():
    capture = R.RelicStaticIconCapture(None)
    result = capture.analyze(_Args(), _Args(image=np.zeros((720, 1280, 3), np.uint8),
        custom_recognition_param=json.dumps(dict(mode='obtained'))))
    assert result.detail['trusted'] is False and result.detail['reason'] == 'resource_missing'
    assert result.box is not None


def test_pipeline_carries_the_static_identity_nodes():
    pipeline = R.relic_pipeline()
    assert pipeline['LabyRelicOfferedStaticIdentity']['custom_recognition'] == R.STATIC_ICON
    assert pipeline['LabyRelicOfferedStaticIdentity']['custom_recognition_param'] == dict(mode='offers')
    assert pipeline['LabyRelicObtainedStaticIdentity']['custom_recognition'] == R.STATIC_ICON
    assert pipeline['LabyRelicObtainedStaticIdentity']['custom_recognition_param'] == dict(mode='obtained')
    for slot in range(3):
        guard = pipeline[f'LabyRelicGuardIcon{slot}0']
        assert guard['custom_recognition'] == R.STATIC_ICON
        assert guard['custom_recognition_param'] == dict(mode='guard')


def test_static_icon_guard_matches_only_the_expected_catalog_id():
    from pcr_maa.labyrinth.relic_recognition import load_catalog
    capture = R.RelicStaticIconCapture(load_catalog())
    board = np.full((720, 1280, 3), 20, np.uint8)
    for pid, box in zip((35004, 35001, 31006), R.ICON_ROIS):
        _paste(board, _icon(pid), box, np.float32([20, 20, 20]))

    def guard(slot, relic_id):
        return capture.analyze(_Args(), _Args(image=board, custom_recognition_param=json.dumps(
            dict(mode='guard', slot=slot, relic_id=relic_id))))

    matched = guard(1, 35001)
    assert matched.box is not None and matched.detail['matched'] is True
    assert matched.detail['relic']['id'] == 35001
    assert matched.detail['expected_id'] == 35001 and matched.detail['slot'] == 1
    missed = guard(1, 35004)
    assert missed.box is None and missed.detail['matched'] is False
    assert missed.detail['relic']['id'] == 35001
    malformed = guard(3, 35001)
    assert malformed.box is None and malformed.detail['reason'] == 'guard_param_unknown'


def test_static_icon_guard_without_a_pack_or_frame_never_hits():
    from pcr_maa.labyrinth.relic_recognition import load_catalog
    no_pack = R.RelicStaticIconCapture(None)
    result = no_pack.analyze(_Args(), _Args(image=np.zeros((720, 1280, 3), np.uint8),
        custom_recognition_param=json.dumps(dict(mode='guard', slot=0, relic_id=35004))))
    assert result.box is None and result.detail['trusted'] is False
    wrong_frame = R.RelicStaticIconCapture(load_catalog()).analyze(_Args(),
        _Args(image=np.zeros((10, 10, 3), np.uint8),
              custom_recognition_param=json.dumps(dict(mode='guard', slot=0, relic_id=35004))))
    assert wrong_frame.box is None


def _saved_frame_node(tmp_path, monkeypatch, frame, node):
    """One native task against a saved synthetic frame; no device and no network."""
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    from pcr_maa.labyrinth.relic_recognition import load_catalog
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    controller = SavedImages([[frame]])
    resource = native.load_resource()
    assert resource.override_pipeline(R.relic_pipeline())
    assert resource.register_custom_recognition(R.STATIC_ICON, R.RelicStaticIconCapture(load_catalog()))
    tasker = native.bind(resource, controller)
    try:
        receipt = native.run(tasker, node)
    finally:
        native.stop_tasker(tasker)
    assert receipt['native_ok'] is True
    return next(n for n in receipt['nodes'] if n['name'] == node)


def test_offered_static_identity_node_identifies_a_saved_frame(tmp_path, monkeypatch):
    board = np.full((720, 1280, 3), 20, np.uint8)
    for pid, box in zip((35004, 35001, 31006), R.ICON_ROIS):
        _paste(board, _icon(pid), box, np.float32([20, 20, 20]))
    node = _saved_frame_node(tmp_path, monkeypatch, board, 'LabyRelicOfferedStaticIdentity')
    assert node['completed'] is True
    detail = node['recognition']['filtered'][0]['detail']
    assert [slot['relic']['id'] for slot in detail['slots']] == [35004, 35001, 31006]


def test_obtained_static_identity_node_identifies_a_saved_frame(tmp_path, monkeypatch):
    frame = np.full((720, 1280, 3), 240, np.uint8)
    x, y, w, h = R.RESULT_ICON_ROI
    sw, sh = round(w * .87), round(h * .87)
    _paste(frame, _icon(35004),
           (x + round(w * .48 - sw / 2), y + round(h * .48 - sh / 2), sw, sh), np.float32([240, 240, 240]))
    node = _saved_frame_node(tmp_path, monkeypatch, frame, 'LabyRelicObtainedStaticIdentity')
    assert node['completed'] is True
    assert node['recognition']['filtered'][0]['detail']['relic']['id'] == 35004


def test_choose_guards_with_the_trusted_catalog_identity(monkeypatch, tmp_path):
    """A trusted icon's catalog fact must reach the fresh click guard.

    2026-11-08 live run: the offer OCR read 自光辉铠·断壁 while the catalog and
    the click frame read 白光辉铠·断壁. The journal already used the catalog
    name, but the guard still expected the OCR spelling and sent no input.
    """
    from contextlib import nullcontext
    from pcr_maa.labyrinth.batch import Batch, Binding
    from pcr_maa.labyrinth.config import Goal, Settings
    from pcr_maa.preset_execution import Journal

    observed = (
        _offer(0, _words(('自光辉铠·断壁',), R.NAME_ROIS[0], (220,)), '守备', 3, 6),
        _offer(1, _words(('空命护珠',), R.NAME_ROIS[1], (120,)), '守备', 2, 6),
        _offer(2, _words(('再生之手镯',), R.NAME_ROIS[2], (120,)), '守备', 1, 6),
    )
    fact = dict(id=35003, name='白光辉铠·断壁', mark='守备', bonus=3, rarity=3)
    batch = Batch(Settings((Goal(1, 1),)), None, Journal(tmp_path),
                  Binding(123, 'synthetic-no-device'))
    reward = object.__new__(R.MaaRelicReward)
    reward.attempted = set()
    reward.tasker = object()
    reward.catalog = object()
    reward.timings = _Args(measure=lambda name: nullcontext())
    seen = []

    def guard(tasker, offers, slot, *, icon=False):
        seen.append((slot, icon, offers[0].identity))
        raise BatchStopped('guard_probe')

    monkeypatch.setattr(R, 'selection_guard', guard)
    monkeypatch.setattr(R, 'choose_offer', lambda *args, **kwargs: observed[0])
    reward._identify_offers = lambda offers: R.attach_offer_identity(
        offers, (fact, None, None))
    with pytest.raises(BatchStopped, match='guard_probe'):
        reward.choose(batch, 13473, '50501:relic_reward:1', 1, offers=observed)
    assert seen == [(0, True, fact)]
    assert observed[0].identity is None  # the caller's tuple is not mutated

    # An untrusted icon keeps the previous text-only guard path.
    reward.attempted = set()
    reward._identify_offers = lambda offers: offers
    with pytest.raises(BatchStopped, match='guard_probe'):
        reward.choose(batch, 13473, '50501:relic_reward:2', 1, offers=observed)
    assert seen[-1] == (0, True, None)

