"""Refresh-button availability and dialog-absent recovery (live run 1703a4bf).

The game keeps the "用N更新" label and price after a shop's refresh cap, but dims
the button. A click then never opens the confirmation dialog. These tests pin the
luma gate and the local "refused" recovery that replaces the former batch abort.
"""
from types import SimpleNamespace as NS
from pathlib import Path

import numpy as np
import pytest

from pcr_maa.labyrinth import shop_execution as shops
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.scene import Scene
from pcr_maa.labyrinth.strategies import MARKS
from test_labyrinth_reward_shop_upstream import harness, word

ROOT = Path(__file__).resolve().parents[1]
LIVE_ENABLED = ROOT / '.runtime/ocr-failures/03eb29d9d2044de4aaa7f523176094c5/8e62a522bc8c4661a614653cd2d00636-frame.png'
LIVE_DISABLED = ROOT / '.runtime/ocr-failures/1703a4bf58994292a026902f6db8771c/b19151ad0e5d486bbfb655b5708ba727-frame.png'
LIVE_ENABLED_ARCHIVE = LIVE_ENABLED.parent / LIVE_ENABLED.name.replace('-frame.png', '.json')
LIVE_DISABLED_ARCHIVE = LIVE_DISABLED.parent / LIVE_DISABLED.name.replace('-frame.png', '.json')


def shelf(**overrides):
    value = dict(state='shop', items=[], balance=1000, stock_state='exhausted',
        refresh_allowed=True, refresh_enabled=True, refresh_known=True, refresh_price=300,
        words={'Refresh': [word('用300更新')]})
    value.update(overrides)
    return value


def test_refresh_button_evidence_separates_enabled_and_disabled_luma():
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    x, y, w, h = shops.REFRESH_BUTTON_RECT
    image[y:y+h, x:x+w] = (190, 215, 245)          # bright blue body -> actionable
    assert shops.refresh_enabled_evidence(image) >= shops.MIN_ENABLED_BUTTON_EVIDENCE
    image[y:y+h, x:x+w] = (95, 120, 150)           # dimmed body -> locked
    assert shops.refresh_enabled_evidence(image) < shops.MIN_ENABLED_BUTTON_EVIDENCE
    # Frames that do not cover the fixed geometry keep the legacy OCR-only path.
    assert shops.refresh_enabled_evidence(np.zeros((100, 100, 3), dtype=np.uint8)) is None


def test_saved_live_frames_calibrate_refresh_button_evidence():
    if not (LIVE_ENABLED.is_file() and LIVE_DISABLED.is_file()):
        pytest.skip('private live shop frames excluded from public source')
    import cv2
    enabled = shops.refresh_enabled_evidence(cv2.imread(str(LIVE_ENABLED)))
    disabled = shops.refresh_enabled_evidence(cv2.imread(str(LIVE_DISABLED)))
    assert enabled >= shops.MIN_ENABLED_BUTTON_EVIDENCE      # 10-23, 1 refresh done
    assert disabled < shops.MIN_ENABLED_BUTTON_EVIDENCE      # 10-31, refresh cap reached


def archive_payload(frame, archive):
    import cv2, json
    with open(archive, encoding='utf-8') as handle:
        words = json.load(handle)['recognition']['best']['detail']['words']
    image = cv2.imread(str(frame))
    def recognition(name, *args, **kwargs):
        key = name.removeprefix('LabyShopRead_')
        if key not in words:
            return NS(hit=False, raw_detail=None)
        return NS(hit=True, raw_detail={'filtered': words[key]})
    result = shops.ShopRecognition().analyze(NS(run_recognition=recognition),
        NS(image=image, custom_recognition_param=None))
    return result.detail


def test_saved_shop_frames_gate_refresh_through_real_recognition_path():
    enabled_archive = LIVE_ENABLED_ARCHIVE
    disabled_archive = LIVE_DISABLED_ARCHIVE
    if not (enabled_archive.is_file() and disabled_archive.is_file()):
        pytest.skip('private live shop evidence excluded from public source')
    enabled = archive_payload(LIVE_ENABLED, enabled_archive)
    assert enabled['state'] == 'shop' and enabled['refresh_allowed'] is True
    assert enabled['refresh_enabled'] is True and enabled['refresh_known'] is True
    disabled = archive_payload(LIVE_DISABLED, disabled_archive)
    assert disabled['state'] == 'shop' and disabled['refresh_allowed'] is False
    assert disabled['refresh_enabled'] is False and disabled['refresh_known'] is True
    assert disabled['refresh_price'] == 600 and disabled['stock_state'] == 'exhausted'


def test_refresh_known_when_label_missing_but_button_dim():
    if not LIVE_DISABLED_ARCHIVE.is_file():
        pytest.skip('private live shop evidence excluded from public source')
    import cv2, json
    with open(LIVE_DISABLED_ARCHIVE, encoding='utf-8') as handle:
        words = dict(json.load(handle)['recognition']['best']['detail']['words'])
    words.pop('Refresh', None)          # OCR dropped the price label entirely
    image = cv2.imread(str(LIVE_DISABLED))
    def recognition(name, *args, **kwargs):
        key = name.removeprefix('LabyShopRead_')
        if key not in words:
            return NS(hit=False, raw_detail=None)
        return NS(hit=True, raw_detail={'filtered': words[key]})
    detail = shops.ShopRecognition().analyze(NS(run_recognition=recognition),
        NS(image=image, custom_recognition_param=None)).detail
    # A dim control is explicit "not refreshable": known stays True so ready()
    # proceeds to the imprint/leave fallback instead of waiting 30s.
    assert detail['refresh_enabled'] is False
    assert detail['refresh_allowed'] is False and detail['refresh_known'] is True


@pytest.mark.parametrize('allowed', [False, True])
def test_disabled_refresh_button_is_never_clicked_by_run(monkeypatch, allowed):
    batch, stamp = harness(NS(refresh_from_area=5))
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp, shop.blocked, shop.pending, shop.spent = stamp, False, None, set()
    shop.observer, shop.timings = NS(), NS()
    current = shelf(refresh_allowed=allowed, refresh_enabled=False)
    shop._capture = lambda: (dict(current), {})
    log = []
    shop._refresh = lambda *a: log.append('refresh') or {}
    class Runtime:
        observer = NS()
        def observe(self, **kwargs): return Scene('shop', {}, {})
        def perform(self, _, action): log.append(action); return {}
        def wait_for(self, *args, **kwargs): return Scene('map', {}, {'area': 5})
    shop.scene = Runtime()
    actual = shop.run(batch, 1, 50701, stacks=dict.fromkeys(MARKS, 0), area=5, refresh_shop=True)
    assert actual['refresh_performed'] is False
    assert log == ['leave_shop']
    assert not any(e['operation'].startswith('shop_refresh') for e in batch.journal.events())


def test_unconfirmed_refresh_click_is_refused_once_without_abort():
    batch, stamp = harness()
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp = stamp
    before = shelf(balance=4790, refresh_price=600, words={'Refresh': [word('用600更新')]})
    late = shelf(balance=4790, refresh_price=600, words={'Refresh': [word('用600更新')]})
    calls = []
    shop._input = lambda state, *a, **k: calls.append(state) or {'native_ok': True}
    shop._probe_state = lambda *a: (None, None)
    shop._capture = lambda: (late, {})
    result = shop._refresh(batch, 1, 50701, before, 2)
    assert result['refresh_refused'] is True and 'refresh_verified' not in result
    assert calls == ['refresh']
    assert [e['operation'] for e in batch.journal.events() if e['state'] == 'settled'] == ['shop_refresh_proposal']


def test_run_latches_refused_refresh_and_never_reclicks(monkeypatch):
    batch, stamp = harness(NS(refresh_from_area=5))
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp, shop.blocked, shop.pending, shop.spent = stamp, False, None, set()
    shop.observer, shop.timings = NS(), NS()
    current = shelf()
    shop._capture = lambda: (dict(current), {})
    calls, log = [], []
    def refused(*args):
        calls.append('refresh')
        return dict(refresh_refused=True)
    shop._refresh = refused
    class Runtime:
        observer = NS()
        def observe(self, **kwargs): return Scene('shop', {}, {})
        def perform(self, _, action): log.append(action); return {}
        def wait_for(self, *args, **kwargs): return Scene('map', {}, {'area': 5})
    shop.scene = Runtime()
    actual = shop.run(batch, 1, 50701, stacks=dict.fromkeys(MARKS, 0), area=5, refresh_shop=True)
    assert calls == ['refresh']                       # the disabled control is tried once
    assert actual['refresh_performed'] is False
    assert log == ['leave_shop']


def test_refresh_without_dialog_but_deducted_coins_is_effective():
    batch, stamp = harness()
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp = stamp
    before = shelf(balance=4790, refresh_price=600, words={'Refresh': [word('用600更新')]})
    late = shelf(balance=4190, refresh_price=600,
        items=[dict(slot=0, kind='role_imprint', name='治疗型职能的随机印记', price=780,
            purchasable=True, verified=True, choice_imprint=False)],
        words={'Refresh': [word('用600更新')]})
    calls = []
    shop._input = lambda state, *a, **k: calls.append(state) or {'native_ok': True}
    shop._probe_state = lambda *a: (None, None)
    shop._capture = lambda: (late, {})
    result = shop._refresh(batch, 1, 50701, before, 2)
    assert result['refresh_verified'] is True and result['refresh_confirmation_missing'] is True
    assert calls == ['refresh']


def test_unknown_page_after_refresh_click_still_blocks_without_replay():
    batch, stamp = harness()
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp = stamp
    before = shelf(balance=4790, refresh_price=600, words={'Refresh': [word('用600更新')]})
    calls = []
    shop._input = lambda state, *a, **k: calls.append(state) or {'native_ok': True}
    shop._probe_state = lambda *a: (None, None)
    shop._capture = lambda: (_ for _ in ()).throw(BatchStopped('shop_readback_unknown'))
    with pytest.raises(BatchStopped, match='^shop_refresh_result_unknown$'):
        shop._refresh(batch, 1, 50701, before, 2)
    assert calls == ['refresh']
    assert [e['operation'] for e in batch.journal.events() if e['state'] == 'settled'] == ['shop_refresh_proposal']


def test_late_confirmation_dialog_after_budget_still_completes():
    batch, stamp = harness()
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp = stamp
    before = shelf(balance=4790, refresh_price=600, words={'Refresh': [word('用600更新')]})
    dialog = dict(state='refresh_confirmation',
        words={'DialogBody': [word('要消耗阿尔法金币×600更新商店')], 'Confirm': [word('确认')]})
    calls = []
    shop._input = lambda state, *a, **k: calls.append(state) or {'native_ok': True}
    shop._probe_state = lambda *a: (None, None)
    shop._capture = lambda: (dialog, {})
    shop._wait_state = lambda *a: (dict(state='shop', balance=4190, items=[]), {})
    result = shop._refresh(batch, 1, 50701, before, 2)
    assert result['refresh_verified'] is True and calls == ['refresh', 'refresh_confirmation']


def test_row_mismatch_without_dialog_is_fail_closed():
    batch, stamp = harness()
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp = stamp
    before = shelf(balance=4790, refresh_price=600, words={'Refresh': [word('用600更新')]})
    late = shelf(balance=4790, refresh_price=600,
        items=[dict(slot=0, kind='role_imprint', name='治疗型职能的随机印记', price=780,
            purchasable=True, verified=True, choice_imprint=False)],
        words={'Refresh': [word('用600更新')]})
    calls = []
    shop._input = lambda state, *a, **k: calls.append(state) or {'native_ok': True}
    shop._probe_state = lambda *a: (None, None)
    shop._capture = lambda: (late, {})
    with pytest.raises(BatchStopped, match='^shop_refresh_result_unknown$'):
        shop._refresh(batch, 1, 50701, before, 2)
    assert calls == ['refresh']


def test_probe_state_budget_deadline_and_stop_semantics(monkeypatch):
    batch, stamp = harness()
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp, shop.tasker = stamp, NS()
    shop._capture = lambda: (dict(state='shop'), {})
    clock = [100.]
    monkeypatch.setattr(shops.native, 'active_now', lambda: clock[0])
    from pcr_maa.labyrinth import autoplay
    monkeypatch.setattr(autoplay, '_poll_pause', lambda *a: clock.__setitem__(0, clock[0] + 1))
    parent = shops.native.RUN_DEADLINE.set(200.)
    try:
        assert shop._probe_state(batch, 'refresh_confirmation', budget=2.) == (None, None)
        assert clock[0] < 200 and shops.native.RUN_DEADLINE.get() == 200.
    finally:
        shops.native.RUN_DEADLINE.reset(parent)
    batch.stopped = lambda: True
    with pytest.raises(BatchStopped, match='^stopped$'):
        shop._probe_state(batch, 'refresh_confirmation', budget=2.)
    batch.stopped = lambda: False
    shop._capture = lambda: (_ for _ in ()).throw(
        RuntimeError('Native task exceeded 90 seconds or inspection deadline'))
    assert shop._probe_state(batch, 'refresh_confirmation', budget=2.) == (None, None)


def test_refused_refresh_still_buys_imprint_instead_of_aborting(monkeypatch):
    batch, stamp = harness(NS(refresh_from_area=5))
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp, shop.blocked, shop.pending, shop.spent = stamp, False, None, set()
    shop.observer, shop.timings = NS(), NS()
    imprint = dict(kind='role_imprint', slot=0, item_id='imp1', name='治疗型职能的随机印记',
        price=780, purchasable=True, choice_imprint=False, box=[233, 320, 100, 40])
    current = dict(state='shop', items=[dict(imprint)], balance=4790, stock_state='exhausted',
        refresh_allowed=True, refresh_enabled=True, refresh_known=True, refresh_price=600,
        words={'Refresh': [word('用600更新')]})
    phase, log = ['shop'], []
    def capture():
        if phase[0] == 'shop': return dict(current), {}
        title = 'confirmation' if phase[0] == 'confirmation' else 'complete'
        verb = '购买' if title == 'confirmation' else '购买了'
        return dict(state=title, words={'DialogBody': [word(f'要消耗阿尔法金币×780{verb}治疗型职能的随机印记')],
            'Confirm': [word('确认')]}), {}
    def refused(*args):
        log.append('refresh'); return dict(refresh_refused=True)
    def input_once(state, *args, **kwargs):
        log.append(state)
        if state == 'shop': phase[0] = 'confirmation'
        elif state == 'confirmation': phase[0] = 'complete'; current['balance'] -= 780
        elif state == 'complete': phase[0] = 'shop'; current['items'] = []
        return {'native_ok': True}
    shop._capture = capture; shop._wait_state = lambda *a: capture()
    shop._input = input_once; shop._refresh = refused
    shop._imprint_following = lambda *a: []
    shop.purchase_readback = lambda b, q, c: stamp(operation_id=c['operation_id'], enter_id=q['enter_id'],
        item_id=q['item_id'], price=q['price'], inventory_added=True, native=capture()[0],
        input_native=c['input_native'])
    monkeypatch.setattr(shops, '_readback', lambda value: value)
    class Runtime:
        observer = NS()
        def observe(self, **kwargs): return Scene('shop', {}, {})
        def perform(self, *a): return {}
        def wait_for(self, *a, **kw): return Scene('map', {}, {'area': 5})
    shop.scene = Runtime()
    actual = shop.run(batch, 1, 50701, stacks=dict.fromkeys(MARKS, 0), area=5,
        refresh_shop=True, final_shop=True)
    assert actual['refresh_performed'] is False and len(actual['purchases']) == 1
    assert log == ['refresh', 'shop', 'confirmation', 'complete']
    assert current['balance'] == 4010
