"""Shop decisions retain unknown stock/refresh evidence until a bounded observation."""
from types import SimpleNamespace as NS
import time

import pytest

from pcr_maa.labyrinth import shop_execution as shops, autoplay
from pcr_maa import native
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.scene import Scene
from pcr_maa.labyrinth.strategies import MARKS, Relic, shop_relic
from test_labyrinth_reward_shop_upstream import harness


def shelf(*, state='exhausted', known=True, allowed=True, balance=1000):
    return dict(state='shop', items=[], balance=balance, stock_state=state,
        refresh_known=known, refresh_allowed=allowed, words={})


def make_shop(monkeypatch, states, *, area=5, start=5, refresh=True, stop=False):
    batch, stamp = harness(NS(refresh_from_area=start))
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp, shop.tasker, shop.blocked = stamp, NS(), False
    shop.observer, shop.pending, shop.spent = NS(), None, set()
    log, reads, clock = [], [], [0.]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(autoplay, '_poll_pause', lambda *args: clock.__setitem__(0, clock[0] + .5))
    current = [dict(value) for value in states]
    def capture():
        value = current.pop(0) if len(current) > 1 else current[0]
        reads.append(value)
        if stop and len(reads) >= 2:
            batch.stopped = lambda: True
        return value, {}
    shop._capture = capture
    def refreshed(*args):
        log.append('refresh')
        current[:] = [shelf(allowed=False, balance=0)]
    shop._refresh = refreshed
    class Runtime:
        observer = NS()
        def observe(self, **kwargs): return Scene('shop', {}, {})
        def perform(self, _, action): log.append(action); return {}
        def wait_for(self, *args, **kwargs): return Scene('map', {}, {'area':area})
    shop.scene = Runtime()
    run = lambda: shop.run(batch, 1, area * 10000 + 601, stacks=dict.fromkeys(MARKS, 0),
        area=area, refresh_shop=refresh)
    return run, log, reads, clock, shop


@pytest.mark.parametrize('start', [1, 4])
def test_area4_explicit_refresh_allows_relic_selection_and_shop_refresh(monkeypatch, start):
    offer = dict(verified=True, kind='relic', currency='run_coins', price=100,
        purchasable=True, relic=Relic('observed', '加速', 1))
    assert shop_relic({}, [offer], balance=1000, area=4, allow_area4=True) is offer
    run, log, _, _, _ = make_shop(monkeypatch, [shelf()], area=4, start=start)
    assert run()['refresh_performed'] and log == ['refresh', 'leave_shop']


@pytest.mark.parametrize('start,refresh', [(5, True), (4, False)])
def test_area4_default_saving_and_disabled_refresh_stay_unchanged(monkeypatch, start, refresh):
    run, log, _, _, _ = make_shop(monkeypatch, [shelf()], area=4, start=start, refresh=refresh)
    assert not run()['refresh_performed'] and log == ['leave_shop']


@pytest.mark.parametrize('initial', [shelf(known=False, allowed=False), shelf(state='unknown')])
def test_unknown_refresh_or_stock_waits_then_refreshes_without_early_exit(monkeypatch, initial):
    run, log, reads, _, _ = make_shop(monkeypatch, [initial, initial, shelf()])
    assert run()['refresh_performed'] and log == ['refresh', 'leave_shop']
    assert len(reads) >= 3


def test_disabled_stock_without_titles_recovers_refresh(monkeypatch):
    run, log, _, _, _ = make_shop(monkeypatch, [shelf()])
    assert run()['refresh_performed'] and log[0] == 'refresh'


def test_permanently_unknown_stock_stops_in_original_budget_with_zero_inputs(monkeypatch):
    run, log, _, clock, shop = make_shop(monkeypatch, [shelf(state='unknown')])
    with pytest.raises(BatchStopped, match='^shop_readback_unknown$'):
        run()
    assert clock[0] == 30 and not log and shop.blocked


def test_stop_while_waiting_never_refreshes_or_leaves(monkeypatch):
    run, log, _, _, _ = make_shop(monkeypatch, [shelf(state='unknown')], stop=True)
    with pytest.raises(BatchStopped, match='^stopped$'):
        run()
    assert not log


@pytest.mark.parametrize('missing', [False, True])
def test_stock_status_uses_complete_buttons_not_missing_titles(missing):
    buttons = {slot:dict(enabled=False) for slot in range(3)}
    if missing:
        del buttons[2]
    assert shops._stock_state({}, buttons, []) == ('unknown' if missing else 'exhausted')


def test_parent_deadline_is_shorter_and_restored_after_unknown_stock(monkeypatch):
    run, log, _, clock, _ = make_shop(monkeypatch, [shelf(state='unknown')])
    token = native.RUN_DEADLINE.set(2.)
    try:
        with pytest.raises(BatchStopped, match='^shop_readback_unknown$'):
            run()
        assert clock[0] == 2. and native.RUN_DEADLINE.get() == 2. and not log
    finally:
        native.RUN_DEADLINE.reset(token)


def test_imprint_category_can_prove_stock_exhausted_without_purchase_quote():
    words = {f'Name{slot}':[dict(text='攻击型职能的随机印记')] for slot in range(3)}
    buttons = {slot:dict(enabled=True) for slot in range(3)}
    assert shops._stock_state(words, buttons, []) == 'exhausted'
    words['Name2'] = [dict(text='未确定商品')]
    assert shops._stock_state(words, buttons, []) == 'unknown'


@pytest.mark.parametrize('enabled,expected', [(True,'unknown'),(False,'exhausted')])
def test_category_only_imprint_never_becomes_a_purchase(enabled,expected):
    from test_labyrinth_reward_shop_upstream import word
    words=dict(Currency=[word('持有阿尔法金币')],Balance=[word('1200')],
               Name0=[word('职能印记')],Price0=[word('900')])
    buttons={slot:dict(enabled=(slot==0 and enabled),box=[0,0,1,1]) for slot in range(3)}
    balance,items=shops.parse_shop_inventory(words,buttons)
    assert balance==1200 and not items
    assert shops._stock_state(words,buttons,items)==expected


def test_partial_stock_does_not_buy_known_imprint_before_other_slots_settle(monkeypatch):
    initial = shelf(state='unknown')
    initial['items'] = [dict(kind='role_imprint', purchasable=True)]
    run, log, _, _, _ = make_shop(monkeypatch, [initial, initial, shelf()])
    assert run()['refresh_performed'] and log == ['refresh', 'leave_shop']
