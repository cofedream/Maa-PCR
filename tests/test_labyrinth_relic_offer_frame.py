"""Relic reward offer frame: OCR evidence, catalog identity and fresh guard.

The 2026-11-08 live run stopped with native_input_not_sent on this page: the
observed name was `自光辉铠·断壁` (白 misread as 自), while the trusted static
icon identified `35003` and the catalog name `白光辉铠·断壁`. The fresh guard
must use that one identity. This is an offline SavedImages replay; it is not
live-game acceptance.
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
FRAME = DATA / 'labyrinth-relic-offer-20261108.png'
MANIFEST = json.loads((DATA / 'labyrinth-relic-offer-20261108.json').read_text(encoding='utf-8'))


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


def test_relic_offer_frame_uses_the_catalog_identity(relic_offline):
    _, tasker = relic_offline
    offers, identified = _observe(tasker)
    expected = MANIFEST['expected']

    assert [offer.name for offer in offers] == expected['observed_ocr_names']
    assert [offer.mark for offer in offers] == ['守备', '守备', '守备']
    assert [offer.bonus for offer in offers] == [2, 3, 1]
    assert [offer.current for offer in offers] == [6, 6, 6]
    assert [offer.remaining for offer in offers] == [1, 1, 1]

    # The middle card still carries the live OCR misread as evidence.
    assert offers[1].name == '自光辉铠·断壁'
    assert identified[1].name == '自光辉铠·断壁'
    assert [offer.identity['id'] for offer in identified] == [25002, 35003, 15005]
    assert [offer.identity['name'] for offer in identified] == [
        '空命护珠', '白光辉铠·断壁', '再生之手镯']
    assert [offer.identity['mark'] for offer in identified] == ['守备', '守备', '守备']
    assert [offer.identity['bonus'] for offer in identified] == [2, 3, 1]

    # The fresh guard follows the catalog identity, not the observed spelling.
    guard = R.selection_override(identified, 1)['LabyRelicGuardName10Line']['expected']
    assert re.fullmatch(guard, '白光辉铠·断壁')
    assert re.fullmatch(guard, '自光辉铠·断壁') is None


def test_relic_offer_frame_fresh_guard_clicks_the_catalog_card(relic_offline):
    controller, tasker = relic_offline
    _, identified = _observe(tasker)
    with R.selection_guard(tasker, identified, 1, icon=True):
        receipt = native.run(tasker, 'LabyRelicSelectOnce')
    assert receipt['native_ok'] is True
    node = next(n for n in receipt['nodes'] if n['name'] == 'LabyRelicSelectOnce')
    assert node['completed'] is True and node['action']['success'] is True
    assert len(controller.clicks) == 1 and not controller.swipes
    x, y = controller.clicks[0]
    bx, by, bw, bh = R.BUTTON_ROIS[1]
    assert bx <= x <= bx + bw and by <= y <= by + bh
