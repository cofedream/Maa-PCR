"""Same-frame guard tests. The OCR words below are synthetic, not device samples."""
from types import SimpleNamespace
import os
from pathlib import Path

import numpy as np
import pytest

from pcr_maa import optional_sweep_quote as adapter


def word(text, x, y, w=110):
    return dict(text=text, score=.99, box=[x, y, w, 24])


def stage(remaining=3, limit=5, level=2):
    return [word(f'圣迹调查关卡{level}级', 78, 56, 210), word('剩余挑战次数', 870, 555),
            word(f'{remaining}/{limit}', 1040, 554)]


def ticket_port(words):
    return SimpleNamespace(module='sanctum_survey', stage=2, words=lambda ctx, argv: words)


class Context:
    def __init__(self, **changes):
        self.values = dict(StaminaCost='60', StaminaOwned='100', Cost='3', Owned='512')
        self.values.update(changes)

    def run_recognition(self, name, image, override):
        value = self.values.get(name.removeprefix('AbyssTicket'), name)
        words = [] if value is None else [word(value, 730, 483, 55)]
        return SimpleNamespace(hit=bool(words), reco_id=1,
                               box=[730, 483, 55, 24], raw_detail={'filtered': words})


def observe(words, *, expected=None, ctx=None):
    reading = dict(remaining=3, limit=5, game_day='2026-11-26')
    guard = adapter._ticket(ticket_port(words), 3, reading, expected=expected)
    return guard(ctx or Context(), SimpleNamespace(image=np.zeros((720,1280,3), np.uint8)))


def test_confirm_current_stage_and_exact_cost(monkeypatch):
    monkeypatch.setattr(adapter, 'current_game_day', lambda: '2026-11-26')
    box, offer = observe(stage())
    assert box == [730,483,55,24] and offer['stamina_cost'] == 60 and offer['tickets_before'] == 512
    assert observe(stage(), expected=offer)[1] == offer
    assert not observe(stage(), expected=offer, ctx=Context(StaminaCost='61'))
    assert not observe(stage(), expected=offer, ctx=Context(Cost='2'))
    assert not observe(stage(), expected=offer, ctx=Context(Owned=None))
    assert not observe(stage(), expected=offer, ctx=Context(StaminaOwned='3'))


def test_same_price_stale_or_wrong_stage_cannot_click(monkeypatch):
    monkeypatch.setattr(adapter, 'current_game_day', lambda: '2026-11-26')
    _, offer = observe(stage())
    for changed in (stage(2), stage(limit=6), stage(level=1), stage()[:-1],
                    stage() + [word('回复次数', 430, 300)]):
        assert not observe(changed, expected=offer)
    monkeypatch.setattr(adapter, 'current_game_day', lambda: '2026-11-27')
    assert not observe(stage(), expected=offer)


def test_ticket_only_tower_quote_requires_explicit_currency():
    words = [word('扫荡券确认', 500, 174), word('要消耗3张扫荡券通关回廊吗？', 451, 282),
             word('消耗券3',430,390), word('持有券5678',770,390), word('取消',450,484),
             word('确认',760,484)]
    amounts = adapter._cloister_ticket(words, 3)[2]
    assert amounts == dict(stamina_cost=0, stamina_before=None, stamina_applicable=False,
                           tickets_cost=3, tickets_before=5678)
    assert not adapter._cloister_ticket(words, 2)
    assert not adapter._cloister_ticket(words[0:1]+words[2:], 3)
    assert not adapter._cloister_ticket(words+[word('消耗体力',420,337)], 3)
    assert not adapter._cloister_ticket([w for w in words if w['text'] != '持有券5678'], 3)


def test_tower_rejects_paid_stamina_modal(monkeypatch):
    monkeypatch.setattr(adapter, 'current_game_day', lambda: '2026-11-26')
    monkeypatch.setattr(adapter, 'stage_identity', lambda *args: True)
    port = ticket_port(stage())
    port.module, port.stage = 'tower_cloister', None
    expect = dict(remaining=3, limit=5, game_day='2026-11-26')
    guard = adapter._ticket(port, 3, expect)
    assert not guard(Context(StaminaCost='1'), SimpleNamespace(image=np.zeros((720,1280,3), np.uint8)))


def test_new_game_day_during_recognition_prevents_dispatch(monkeypatch):
    days = iter(('2026-11-26', '2026-11-27'))
    monkeypatch.setattr(adapter, 'current_game_day', lambda: next(days))
    assert not observe(stage())


def test_settlement_requires_exact_remaining_and_limit(monkeypatch):
    monkeypatch.setattr(adapter, 'current_game_day', lambda: '2026-11-26')
    port = SimpleNamespace(module='sanctum_survey', stage=2,
                           frame=lambda *args, **kwargs: dict(page='complete', native_ok=True))
    offer = dict(count=3, remaining=3, limit=5, game_day='2026-11-26')
    reading = dict(module='sanctum_survey', stage=2, remaining=0, limit=5, state='ready')
    monkeypatch.setattr(adapter, 'navigate', lambda p: reading)
    assert adapter.result(port, offer)['verified']
    for key, value in [('limit', 6), ('remaining', 1), ('stage', 1), ('state','locked')]:
        monkeypatch.setattr(adapter, 'navigate', lambda p: {**reading, key:value})
        with pytest.raises(ValueError, match='counter_mismatch'):
            adapter.result(port, offer)


@pytest.mark.private_replay
@pytest.mark.parametrize('start_at_max', [False, True])
def test_cn_quest_details_and_ticket_modal(start_at_max):
    """Play both native button actions against saved player frames only."""
    from pcr_maa import native
    from pcr_maa.optional_sweep_native import NativeOptionalSweep
    from pcr_maa.replay import SavedImages
    directory = Path(os.environ.get('PCR_SURVEY_REPLAY', '.runtime/live-acceptance'))
    module = os.environ.get('PCR_SURVEY_MODULE', 'sanctum_survey')
    stage = int(os.environ.get('PCR_SURVEY_STAGE', '9'))
    prefix = ('sanctum' if module == 'sanctum_survey' else 'temple') + '-' + str(stage)
    paths = [directory/(prefix+'-one-detail.png'), directory/(prefix+'-one-quote.png')]
    if start_at_max:
        paths.insert(0, directory/(prefix+'-detail.png'))
    if not all(p.is_file() for p in paths):
        pytest.skip('requires CN quest and matching one-ticket confirmation frames')
    ctrl = SavedImages([[native.read_image(p)] for p in paths])
    resource = native.load_resource()
    tasker = native.bind(resource, ctrl)
    port = NativeOptionalSweep(tasker, module, stage)
    reading = port.read()
    assert reading['state'] == 'ready' and reading['remaining'] >= 1 and not ctrl.clicks
    offer = port.quote(1)
    expected = 2 if start_at_max else 1
    assert offer['tickets_cost'] == 1 and offer['stamina_cost'] > 0 and len(ctrl.clicks) == expected
    sent = port.submit(offer)
    assert sent['native_ok'] and len(ctrl.clicks) == expected + 1
    assert offer['limit'] == reading['limit'] and offer['remaining'] == reading['remaining']
