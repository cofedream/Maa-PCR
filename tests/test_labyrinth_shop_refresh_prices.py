"""Shop refresh uses the visible quote throughout the existing purchase flow.

The merged refresh path also requires the control's inner-luma evidence before it
treats the quote as actionable, so synthetic frames have to carry an enabled
refresh button instead of a blank fill.
"""
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from pcr_maa.labyrinth import shop_execution as shops
from pcr_maa.labyrinth.batch import BatchStopped
from test_labyrinth_reward_shop_upstream import harness, word


def enabled_shop_frame(luma=200):
    """1280x720 synthetic shop frame with an actionable refresh control."""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    x, y, w, h = shops.REFRESH_BUTTON_RECT
    image[y:y + h, x:x + w, :] = luma
    return image


def observe_refresh(text, balance, guard=None):
    values = {'Currency': '持有阿尔法金币', 'Balance': str(balance), 'Refresh': text}

    def recognize(node, image, *args):
        key = node.removeprefix('LabyShopRead_')
        text = values.get(key)
        return NS(hit=text is not None, raw_detail={'filtered': [dict(
            text=text, box=shops._READS[key][1], score=.99)] if text is not None else []})

    return shops.ShopRecognition().analyze(NS(run_recognition=recognize), NS(
        image=enabled_shop_frame(), custom_recognition_param=json.dumps(guard or {}))).detail


@pytest.mark.parametrize('text,balance,allowed,known', [
    ('用300更新', 300, True, True), ('用600更新', 599, False, True),
    ('用600更新', 600, True, True), ('用900更新', 1590, True, True),
    ('用1,200更新', 1200, True, True), ('用750刷新商店', 750, True, True),
    ('用200更新', 200, True, True), ('用600更新', 0, False, True),
    ('更新', 1590, False, False), (None, 1590, False, False),
    ('用600或900更新', 1590, False, False), ('用-600更新', 1590, False, False),
    ('用0更新', 1590, False, False), (None, 0, False, True),
])
def test_refresh_affordability_uses_the_observed_price(text, balance, allowed, known):
    result = observe_refresh(text, balance)
    assert result['refresh_allowed'] is allowed
    assert result['refresh_known'] is known


@pytest.mark.parametrize('text,balance,accepted', [
    ('用600更新', 1100, True), ('用600更新', 800, False), ('用900更新', 1100, False),
    ('更新', 1100, False)])
def test_refresh_click_rechecks_observed_balance_and_quote(text, balance, accepted):
    result = observe_refresh(text, balance, dict(state='shop', refresh_balance=1100, price=600))
    assert (result.get('status') == 'observed') is accepted


def test_refresh_below_the_reserve_never_starts_a_transaction():
    batch, _ = harness(NS(refresh_min_balance=500))
    shop = shops.MaaShop.__new__(shops.MaaShop)
    current = dict(balance=800, refresh_allowed=True, words={'Refresh':[word('用600更新')]})
    with pytest.raises(BatchStopped, match='shop_refresh_quote_unknown'):
        shop._refresh(batch, 1, 50601, current, 0)
    assert batch.journal.events() == []


@pytest.mark.parametrize('price', [200, 300, 600, 750, 900, 1200])
def test_refresh_quote_confirmation_journal_and_balance_use_the_same_price(price):
    batch, stamp = harness()
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp = stamp
    current = dict(balance=3000, refresh_allowed=True,
                   words={'Refresh': [word(f'用{price:,}更新')]})
    states = iter([(dict(state='refresh_confirmation', words={
        'DialogBody': [word(f'要消耗阿尔法金币×{price}更新商店')], 'Confirm': [word('确认')]}), {}),
        (dict(state='shop', balance=3000-price), {})])
    shop._probe_state = lambda *args: next(states)
    shop._capture = lambda: next(states)
    calls = []
    shop._input = lambda state, *args, **kwargs: calls.append((state, kwargs)) or dict(native_ok=True)
    result = shop._refresh(batch, 1, 50601, current, 7)
    assert result['refresh_verified'] and result['price'] == price
    assert [state for state, _ in calls] == ['refresh', 'refresh_confirmation']
    assert calls[0][1] == {'price': price, 'refresh_balance': current['balance']}
    assert calls[1][1]['price'] == price
    settled = [event for event in batch.journal.events() if event['state'] == 'settled']
    assert len(settled) == 2 and all(event['result']['price'] == price for event in settled)


@pytest.mark.parametrize('change', ['dialog_price', 'currency', 'balance_after', 'insufficient'])
def test_dynamic_refresh_keeps_quote_and_result_checks(change, monkeypatch):
    from pcr_maa.labyrinth import autoplay

    batch, stamp = harness()
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp, shop.tasker = stamp, NS()
    current = dict(balance=599 if change == 'insufficient' else 1590, refresh_allowed=True,
                   words={'Refresh': [word('用600更新')]})
    currency = '宝石' if change == 'currency' else '阿尔法金币'
    states = iter([(dict(state='refresh_confirmation', words={
        'DialogBody': [word(f'要消耗{currency}×{900 if change == "dialog_price" else 600}更新商店')],
        'Confirm': [word('确认')]}), {})])
    shop._probe_state = lambda *args: next(states)
    shop._capture = lambda: (dict(state='shop', balance=991), {})
    clock = [100.]
    monkeypatch.setattr(shops.native, 'active_now', lambda: clock[0])
    monkeypatch.setattr(autoplay, '_poll_pause', lambda *args: clock.__setitem__(0, 130.))
    calls = []
    shop._input = lambda state, *args, **kwargs: calls.append(state) or dict(native_ok=True)
    with pytest.raises(BatchStopped):
        shop._refresh(batch, 1, 50601, current, 1)
    assert calls == ([] if change == 'insufficient' else
                     ['refresh', 'refresh_confirmation'] if change == 'balance_after' else ['refresh'])
