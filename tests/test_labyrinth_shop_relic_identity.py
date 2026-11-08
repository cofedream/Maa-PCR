"""Shop relics: trusted static icon identity first, OCR text only as fallback.

The 2026-11-08 live captures cover one run-coin shop visit end to end: the shelf
with three purchasable relics, the paired purchase confirmation for
深渊蚀杖诅咒, the purchase-complete modal, and the same shelf after the purchase.
The shelf identity must be the catalog icon id/name/mark/bonus, cross-checked
with the same-frame attribute line; the confirmation dialog must pair with the
clicked card; OCR text remains only for an untrusted icon or an absent pack.
This is offline SavedImages replay, not live-game acceptance.
"""
from hashlib import sha256
import json
import socket
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from pcr_maa import native
from pcr_maa.labyrinth import relics as R
from pcr_maa.labyrinth import shop_execution as S
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.relic_recognition import load_catalog
from pcr_maa.replay import SavedImages

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).with_name('data')
MANIFEST = json.loads((DATA / 'labyrinth-shop-relics-20261108.json').read_text(encoding='utf-8'))
PRE = 'labyrinth-shop-relics-20261108.png'
CONFIRM = 'labyrinth-shop-relics-confirm-20261108.png'
COMPLETE = 'labyrinth-shop-relics-complete-20261108.png'
AFTER = 'labyrinth-shop-relics-after-20261108.png'
ICONS = ROOT / 'assets/labyrinth/relic-pack/icons'


def _image(name):
    path = DATA / name
    meta = MANIFEST['frames'][name]
    assert sha256(path.read_bytes()).hexdigest() == meta['sha256']
    image = native.read_image(path)
    assert image.shape[:2] == (meta['height'], meta['width'])
    return image


def _icon(pid):
    raw = (ICONS / f'{pid}.png').read_bytes()
    return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)


def _paste(frame, image, box):
    x, y, w, h = box
    image = cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)
    region = frame[y:y + h, x:x + w].astype(np.float32)
    alpha = image[:, :, 3:4].astype(np.float32) / 255
    art = image[:, :, :3].astype(np.float32) * alpha + region * (1 - alpha)
    frame[y:y + h, x:x + w] = art.astype(np.uint8)


@pytest.fixture
def replay(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail('offline saved frame prohibits network/device')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    started = []

    def build(images):
        controller = SavedImages([[image] for image in images])
        resource = native.load_resource()
        tasker = native.bind(resource, controller)
        token = native.RUN_DEADLINE.set(time.monotonic() + 120)
        counter = [0]

        def stamp(**values):
            counter[0] += 1
            return dict(verified=True, binding=dict(account_id=1,
                        device_id='offline-saved-image-replay'), frame=counter[0], **values)

        started.append((tasker, token))
        return controller, tasker, S.MaaShop(tasker, stamp)

    yield build
    for tasker, token in started:
        try:
            native.stop_tasker(tasker)
        finally:
            native.RUN_DEADLINE.reset(token)


def test_shelf_relics_use_the_trusted_catalog_identity(replay):
    _, _, shop = replay([_image(PRE)])
    value, receipt = shop._capture()
    assert receipt['native_ok'] is True
    assert value['state'] == 'shop' and value['stock_state'] == 'ready'
    assert value['balance'] == MANIFEST['expected']['shop']['balance']
    assert value['refresh_price'] == MANIFEST['expected']['shop']['refresh_price']
    expected = MANIFEST['expected']['shop']['items']
    assert [(item['slot'], item['kind'], item['price']) for item in value['items']] == \
        [(row['slot'], row['kind'], row['price']) for row in expected]
    for item, row in zip(value['items'], expected):
        assert item['name'] == row['name']
        assert item['catalog_id'] == row['relic_id']
        assert item['catalog_name'] == row['name']
        assert item['identity_source'] == 'static_icon'
        assert item['relic']['mark'] == row['mark'] and item['relic']['bonus'] == row['bonus']
        assert item['current_stacks'] == row['current']
        assert item['catalog_match']['score'] >= row['icon_score'] - .01
        assert item['catalog_match']['margin'] >= row['icon_margin'] - .01


def test_confirmation_pairs_with_the_clicked_shelf_card_and_purchase_completes(replay):
    controller, _, shop = replay([_image(PRE), _image(CONFIRM), _image(COMPLETE), _image(AFTER)])
    shop_page, _ = shop._capture()
    target = next(item for item in shop_page['items'] if item['slot'] == 2)
    assert target['catalog_id'] == MANIFEST['expected']['confirmation']['relic_id']
    shop._input('shop', target['box'], name=target['name'], price=target['price'])
    dialog, _ = shop._capture()
    assert dialog['state'] == 'confirmation'
    assert dialog['selected_icon_verified'] is True
    assert dialog['dialog_catalog']['id'] == MANIFEST['expected']['confirmation']['dialog_icon_id']
    assert S.confirmation_terms(dialog, mark='弱体', bonus=3)[1] == \
        MANIFEST['expected']['confirmation']['price']
    # The paired catalog id is the strongest identity; the captured-icon
    # template remains the fallback when no expected catalog id is supplied.
    identity = S._item_identity(dialog, target['name'], 'relic',
                                expected_catalog_id=target['catalog_id'])
    assert identity == 'native_observed_catalog'
    assert S._item_identity(dialog, target['name'], 'relic') == 'native_observed_icon'
    shop._input('confirmation', dialog['words']['Confirm'][0]['box'], name=target['name'],
                price=target['price'], mark='弱体', bonus=3,
                identity_method=identity, catalog_id=target['catalog_id'])
    complete, _ = shop._capture()
    assert complete['state'] == 'complete'
    assert S.confirmation_terms(complete)[1] == MANIFEST['expected']['complete']['price']
    shop._input('complete', complete['words']['Confirm'][0]['box'], name=None, price=target['price'])
    after, _ = shop._capture()
    expected_after = MANIFEST['expected']['after']
    assert after['state'] == 'shop' and after['balance'] == expected_after['balance']
    rows = [(item['slot'], item['kind'], item['price']) for item in after['items']]
    assert rows == [(row['slot'], row['kind'], row['price']) for row in expected_after['items']]
    assert after['items'][0]['current_stacks'] == 3 and after['items'][0]['catalog_id'] == 12003
    assert after['items'][1]['current_stacks'] == 1 and after['items'][1]['catalog_id'] == 14003
    assert 'catalog_id' not in after['items'][2]  # a role imprint is never a relic id
    sold_out = load_catalog().match(_image(AFTER), (836, 460, 103, 103))
    assert sold_out.trusted is True and sold_out.facts.id == 32002


def test_shelf_conflict_with_the_attribute_line_stops_instead_of_guessing(replay):
    frame = _image(PRE)
    _paste(frame, _icon(14003), S.SHOP_ICON_ROIS[2])  # 强化+1 art over a 削弱+3 card
    _, _, shop = replay([frame])
    with pytest.raises(BatchStopped, match='shop_readback_unknown'):
        shop._capture()


def test_untrusted_shelf_icon_keeps_the_ocr_text_path(replay):
    frame = _image(PRE)
    for roi in S.SHOP_ICON_ROIS:
        x, y, w, h = roi
        frame[y:y + h, x:x + w] = (128, 128, 128)
    catalog = load_catalog()
    assert all(not catalog.match(frame, roi).trusted for roi in S.SHOP_ICON_ROIS)
    _, _, shop = replay([frame])
    value, _ = shop._capture()
    assert value['state'] == 'shop'
    assert [item['name'] for item in value['items']] == ['暗剑毒药', '抗魔战裙', '深渊蚀杖诅咒']
    assert all('catalog_id' not in item for item in value['items'] if item['kind'] == 'relic')


def test_absent_pack_keeps_the_ocr_text_path(replay, monkeypatch):
    monkeypatch.setattr(S, 'relic_catalog', lambda tasker: None)
    _, _, shop = replay([_image(PRE)])
    value, _ = shop._capture()
    assert value['state'] == 'shop'
    assert all('catalog_id' not in item for item in value['items'] if item['kind'] == 'relic')


def test_dialog_catalog_identity_is_available_without_the_template():
    value = dict(state='confirmation', words={'DialogBody': []},
                 dialog_catalog=dict(id=32002, name='深渊蚀杖诅咒'))
    assert S._item_identity(value, '深渊蚀杖退咒', 'relic',
                            expected_catalog_id=32002) == 'native_observed_catalog'
    with pytest.raises(BatchStopped):
        S._item_identity(value, '深渊蚀杖退咒', 'relic', expected_catalog_id=12003)
