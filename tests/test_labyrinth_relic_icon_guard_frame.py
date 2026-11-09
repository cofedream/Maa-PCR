"""Relic fresh guard: a trusted catalog icon, not the OCR card name, grants input.

The 2026-11-08 15:14 live run stopped with `native_input_not_sent` on this page:
the card art showed `杀戮之矛` but both frames' OCR read `杀之矛` (the complex
glyph `戮` was dropped), while the trusted static icon identified `13001`. The
previous guard required the catalog name as an exact OCR pattern, so a correct
card was rejected and no click was sent. The guard must prove the same-frame
catalog id instead. This is an offline SavedImages replay; it is not live-game
acceptance.
"""
from hashlib import sha256
import json
import re
import socket
from pathlib import Path

import pytest

from pcr_maa import native
from pcr_maa.labyrinth import relics as R
from pcr_maa.labyrinth.relic_recognition import load_catalog
from pcr_maa.replay import SavedImages

DATA = Path(__file__).with_name('data')
FRAME = DATA / 'labyrinth-relic-ocr-drop-20261108.png'
MANIFEST = json.loads((DATA / 'labyrinth-relic-ocr-drop-20261108.json').read_text(encoding='utf-8'))


def _image():
    raw = FRAME.read_bytes()
    meta = MANIFEST['frames'][FRAME.name]
    assert sha256(raw).hexdigest() == meta['sha256']
    image = native.read_image(FRAME)
    assert image.shape[:2] == (meta['height'], meta['width'])
    return image


@pytest.fixture
def relic_offline(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail('offline saved frame prohibits network/device')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    controller = SavedImages([[_image()]])
    resource = native.load_resource()
    assert resource.override_pipeline(R.relic_pipeline())
    assert resource.register_custom_recognition(
        'LabyRelicSelectedIconCapture', R.RelicIconCapture())
    assert resource.register_custom_recognition(
        R.STATIC_ICON, R.RelicStaticIconCapture(load_catalog()))
    tasker = native.bind(resource, controller)
    try:
        yield controller, tasker
    finally:
        native.stop_tasker(tasker)


def _observe(tasker):
    offers = R.relic_offers(native.run(tasker, 'LabyRelicObserved'))
    receipt = native.run(tasker, 'LabyRelicOfferedStaticIdentity')
    node = next(n for n in receipt['nodes'] if n['name'] == 'LabyRelicOfferedStaticIdentity')
    assert node['completed'] is True
    detail = R._custom_detail(node['recognition'])
    assert detail is not None
    return offers, R.identify_relic_offers(offers, detail)


def test_ocr_dropped_one_glyph_but_the_icon_identity_is_complete(relic_offline):
    _, tasker = relic_offline
    offers, identified = _observe(tasker)
    expected = MANIFEST['expected']

    assert [offer.name for offer in offers] == expected['observed_ocr_names']
    assert offers[0].name == '杀之矛'  # the live frame's 杀戮之矛 lost 戮
    assert [offer.identity['id'] for offer in identified] == \
        [entry['relic_id'] for entry in expected['offers']]
    assert [offer.identity['name'] for offer in identified] == \
        [entry['name'] for entry in expected['offers']]
    assert [(offer.mark, offer.bonus, offer.current) for offer in offers] == \
        [(entry['mark'], entry['bonus'], entry['current']) for entry in expected['offers']]
    # The canonical name can never match this frame's OCR text; the icon can.
    assert re.fullmatch(R._pattern(identified[0].identity['name']), offers[0].name) is None
    assert identified[0].identity['id'] == 13001


def test_icon_guard_clicks_the_slot_with_the_journaled_identity(relic_offline):
    controller, tasker = relic_offline
    _, identified = _observe(tasker)
    guard = R.selection_override(identified, 0)
    assert 'LabyRelicGuardName00Detected' not in guard
    assert guard['LabyRelicGuardIcon00']['custom_recognition_param'] == dict(
        mode='guard', slot=0, relic_id=13001)
    with R.selection_guard(tasker, identified, 0, icon=True):
        receipt = native.run(tasker, 'LabyRelicSelectOnce')
    assert receipt['native_ok'] is True
    assert receipt['native_evidence']['action_started'] is True
    assert len(controller.clicks) == 1 and not controller.swipes
    x, y = controller.clicks[0]
    bx, by, bw, bh = R.BUTTON_ROIS[0]
    assert bx <= x <= bx + bw and by <= y <= by + bh
    node = next(n for n in receipt['nodes'] if n['name'] == 'LabyRelicSelectOnce')
    names = [row.get('name') for row in node['recognition']]
    assert 'LabyRelicGuardIcon00' in names and 'LabyRelicGuardName00' not in names


def test_icon_guard_refuses_a_different_catalog_id_on_the_same_frame(relic_offline):
    controller, tasker = relic_offline
    _, identified = _observe(tasker)
    # Same page, same OCR, but the journaled identity is a different relic id.
    wrong = R.attach_offer_identity(identified, (
        dict(id=13002, name='别的东西', mark='会心', bonus=1, rarity=1), None, None))
    with R.selection_guard(tasker, wrong, 0, icon=True):
        receipt = native.run(tasker, 'LabyRelicSelectOnce')
    assert not controller.clicks and not controller.swipes
    assert receipt['native_ok'] is False
    assert receipt['native_evidence']['recognition_failed'] is True
    assert receipt['native_evidence']['action_started'] is False
    with pytest.raises(native.NativeInputNotSent):
        native.require_native_input(receipt, ('LabyRelicSelectOnce',))
