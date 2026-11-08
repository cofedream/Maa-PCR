"""Relic obtained modal: native OCR, exact item x1 and static icon identity.

The 2026-11-08 selection flow ended on this modal after the middle card was
chosen. The modal must be recognized from the saved ADB frame without a live
device: title/message/item/close OCR, the same icon identity (`35003`) and the
one-item result proof. This is offline replay, not live acceptance.
"""
from hashlib import sha256
import json
import socket
from pathlib import Path

import pytest

from pcr_maa import native
from pcr_maa.labyrinth import relics as R
from pcr_maa.labyrinth.relic_recognition import load_catalog
from pcr_maa.replay import SavedImages

DATA = Path(__file__).with_name('data')
FRAME = DATA / 'labyrinth-relic-obtained-20261108.png'
MANIFEST = json.loads((DATA / 'labyrinth-relic-obtained-20261108.json').read_text(encoding='utf-8'))


def _image():
    raw = FRAME.read_bytes()
    meta = MANIFEST['frames'][FRAME.name]
    assert sha256(raw).hexdigest() == meta['sha256']
    image = native.read_image(FRAME)
    assert image.shape[:2] == (meta['height'], meta['width'])
    return image


def _selected():
    data = MANIFEST['selected']
    fact = dict(id=data['relic_id'], name=data['name'], mark=data['mark'],
                bonus=data['bonus'], rarity=data['rarity'])
    return R.RelicOffer(data['slot'], data['name'], data['mark'], data['bonus'],
                        data['current'], (), (), (), 1, fact)


def _line(rows, name):
    return rows[name]['detail']['filtered'][0]['text']


@pytest.fixture
def obtained_offline(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail('offline saved frame prohibits network/device')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    resource = native.load_resource()
    assert resource.override_pipeline(R.relic_pipeline())
    assert resource.register_custom_recognition(
        'LabyRelicSelectedIconCapture', R.RelicIconCapture())
    assert resource.register_custom_recognition(
        R.STATIC_ICON, R.RelicStaticIconCapture(load_catalog()))
    tasker = native.bind(resource, SavedImages([[_image()]]))
    try:
        yield tasker
    finally:
        native.stop_tasker(tasker)


def test_relic_obtained_frame_is_recognized_and_verified(obtained_offline):
    tasker = obtained_offline
    selected = _selected()
    with R.obtained_guard(tasker, selected, icon=True, static=True):
        receipt = native.run(tasker, 'LabyRelicObtainedObserved')
    assert receipt['native_ok'] is True
    node = next(n for n in receipt['nodes'] if n['name'] == 'LabyRelicObtainedObserved')
    assert node['completed'] is True
    rows = {row['name']: row for row in node['recognition']}

    expected = MANIFEST['expected']
    assert _line(rows, 'LabyRelicObtainedTitle') == expected['title']
    assert _line(rows, 'LabyRelicObtainedMessage') == expected['message']
    assert _line(rows, 'LabyRelicObtainedItem') == expected['item_text']
    assert _line(rows, 'LabyRelicObtainedClose') == expected['close']
    static = rows['LabyRelicObtainedStaticIdentity']['detail']['filtered'][0]['detail']
    assert static['trusted'] is True
    assert static['relic']['id'] == 35003 and static['relic']['name'] == '白光辉铠·断壁'

    value = R.relic_obtained(receipt, selected, icon=True, static=True, relic_id=35003)
    assert value['verified'] is True
    assert value['relic_id'] == 35003
    assert value['relic_name'] == '白光辉铠·断壁'
    assert value['observed_result_text'] == '白光辉铠·断壁×1'
    assert value['quantity'] == 1 and value['inventory_added'] is True
    assert value['identity_method'] == expected['identity_method']
