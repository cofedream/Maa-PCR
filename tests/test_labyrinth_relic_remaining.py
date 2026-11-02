"""Relic choices use the current remaining count, including later choice pages."""
from dataclasses import replace
import re

import pytest

from pcr_maa.labyrinth import relics
from pcr_maa.labyrinth.batch import BatchStopped
from test_labyrinth_reward_shop_upstream import relic_name_receipt


def receipt_with_remaining(text):
    receipt = relic_name_receipt('detected')
    row = next(row for row in receipt['nodes'][0]['recognition'] if row['name'] == 'LabyRelicRemaining')
    row['detail']['filtered'][0]['text'] = text
    return receipt


@pytest.mark.parametrize('count', [1, 2, 3, 10])
def test_relic_remaining_is_read_and_guarded(count):
    text = f'还剩{count}次'
    assert re.fullmatch(relics.relic_pipeline()['LabyRelicRemaining']['expected'], text)
    offers = relics.relic_offers(receipt_with_remaining(text))
    assert all(offer.remaining == count for offer in offers)
    nodes = relics.selection_override(offers, 0)
    guard = nodes['LabyRelicRemaining']['expected']
    assert re.fullmatch(guard, text)
    assert not re.fullmatch(guard, f'还剩{count+1}次')


@pytest.mark.parametrize('text', ['还剩0次', '还剩-1次', '还剩1或2次', '还剩9223372036854775808次'])
def test_invalid_remaining_stops_before_selection(text):
    with pytest.raises(BatchStopped):
        relics.relic_offers(receipt_with_remaining(text))


def test_mixed_remaining_counts_cannot_build_one_selection_guard():
    offers = list(relics.relic_offers(receipt_with_remaining('还剩2次')))
    offers[1] = replace(offers[1], remaining=1)
    with pytest.raises(BatchStopped):
        relics.selection_override(offers, 0)


def test_sequential_relic_pages_have_independent_reward_ids(tmp_path):
    from test_labyrinth_autoplay import make, route_for, sequence
    route = route_for([1, 6, 8])
    pages = sequence([1, 6, 8])
    index = next(i for i, (kind, _) in enumerate(pages) if kind == 'relic_reward')
    pages[index:index] = [('relic_reward', {}), ('relic_reward', {})]
    runner, batch, runtime, navigator, actions = make(tmp_path, route, pages)
    original = actions.choose_reward
    rewards = []

    def choose(kind, route, node, reward_id):
        if kind == 'relic_reward': rewards.append(reward_id)
        return original(kind, route, node, reward_id)

    actions.choose_reward = choose
    runner.play(route, batch)
    assert rewards[:3] == [f'10201:relic_reward:{i}' for i in (1, 2, 3)]
    assert len(rewards) == len(set(rewards))
