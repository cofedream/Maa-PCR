"""Callback-local configuration copies; OCR and action-frame reads stay fresh."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

import pytest

from pcr_maa import abyss_native, event_native
from test_abyss import reading, quotation
from test_r39_recognition import Context, args, found


PIPELINES = Path(__file__).resolve().parents[1] / 'assets/resource/pipeline'


def event_context(*, exhausted=False):
    pipeline = json.loads((PIPELINES / 'event.json').read_text(encoding='utf-8'))
    results = {name: found() for name in ('EventListGuard', 'EventCheckedLabel',
        'EventBlueTop', 'EventGrayBottom', 'EventRailBlue', 'EventRailGray')}
    results.update(EventRowsOCR=found(words=[dict(text=f'活动关卡H-{3-i}', score=.99,
        box=[100, 199+i*80, 150, 20]) for i in range(3)]),
        EventRowCategory=found('活动'), EventRowDifficulty=found(hit=not exhausted, text='困难'),
        EventRowRemaining=found('0/3' if exhausted else '3/3'),
        EventExhaustedHard=found(), EventRowExhausted=found('已无剩余挑战次数。'))
    context = Context(results)
    context.pipeline, context.queries = pipeline, []
    def get_node_data(name):
        context.queries.append(name)
        return pipeline[name]
    context.get_node_data = get_node_data
    return context


def abyss_context():
    source = json.loads((PIPELINES / 'abyss.json').read_text(encoding='utf-8'))
    pipeline = {name: dict(recognition=dict(param=dict(roi=deepcopy(node['roi']))))
                for name, node in source.items() if 'roi' in node}
    results = {name: found() for name in ('AbyssConfirmGuard', 'AbyssTicketGuard',
                                         'AbyssTicketConfirmButton')}
    results.update(AbyssQuoteTitles=found(words=[dict(text='前哨关卡', score=.99,
        box=[100, 147+i*80, 150, 20]) for i in range(3)]),
        AbyssQuoteRow1Mode=found('深渊讨伐战'),
        AbyssQuoteRow1Difficulty=lambda name, override: found(
            override[name]['expected'].removeprefix('^').removesuffix('$')),
        AbyssQuoteRow1Times=found('3'), AbyssQuoteRow1Remaining=found('3/4'),
        AbyssQuoteRow1RemainingLabel=found('剩余挑战次数'), AbyssQuoteRow1TimesLabel=found('跳过次数'))
    for name, value in dict(AbyssQuoteStages=3, AbyssQuoteTotalTimes=9,
            AbyssQuoteStaminaCost=225, AbyssQuoteStaminaOwned=245, AbyssQuoteTicketCost=9,
            AbyssQuoteTicketsOwned=206495, AbyssTicketStaminaCost=225,
            AbyssTicketStaminaOwned=245, AbyssTicketCost=9, AbyssTicketOwned=206495).items():
        results[name] = found(str(value))
    context = Context(results)
    context.pipeline, context.queries = pipeline, []
    def get_node_data(name):
        context.queries.append(name)
        return pipeline[name]
    context.get_node_data = get_node_data
    return context


def checked(context, image, roi, *, evidence):
    evidence.update(hit=True, raw=dict(roi=list(roi)))
    return True


@pytest.mark.parametrize('exhausted', [False, True])
def test_event_roi_copies_expire_at_each_callback(exhausted, monkeypatch):
    monkeypatch.setattr(event_native, 'checked_glyph', checked)
    context = event_context(exhausted=exhausted)
    page = event_native.EventPage(dict(layout='story_event_h', period='举办时间：10/0112:00~10/0704:59'))
    original = deepcopy(context.pipeline)
    first = page.analyze(context, args('top'))
    assert first and not first.detail['gaps'] and context.pipeline == original
    names = ['EventRowCategory', 'EventRowDifficulty', 'EventRowRemaining']
    if exhausted:
        names.append('EventRowExhausted')
    assert Counter(context.queries) == dict.fromkeys(names, 1)
    prior = [override['EventRowRemaining']['roi'] for name, _, override in context.calls
             if name == 'EventRowRemaining']
    context.pipeline['EventRowRemaining']['attach']['row_roi'][1] += 7
    context.results['EventRowRemaining'] = found('1/3' if not exhausted else '0/3')
    updated = deepcopy(context.pipeline)
    second_args = args('top')
    second = page.analyze(context, second_args)
    assert second and not second.detail['gaps'] and context.pipeline == updated
    assert Counter(context.queries) == dict.fromkeys(names, 2)
    after = [override['EventRowRemaining']['roi'] for name, _, override in context.calls
             if name == 'EventRowRemaining'][3:]
    assert [roi[1] for roi in after] == [roi[1]+7 for roi in prior]
    assert len({tuple(roi) for roi in prior}) == 3
    assert all(row['remaining'] == (0 if exhausted else 1) for row in second.detail['rows'])
    assert sum(image is second_args.image for _, image, _ in context.calls) > 0


@pytest.mark.parametrize('ticket', [False, True])
def test_abyss_roi_copies_expire_at_each_callback(ticket):
    context = abyss_context()
    page = (abyss_native.AbyssTicketPage(reading(), quotation(), require_ready=True) if ticket
            else abyss_native.AbyssQuotePage(reading()))
    original = deepcopy(context.pipeline)
    first = page.analyze(context, args())
    assert first and context.pipeline == original
    names = ['AbyssQuoteRow1'+field for field in ('Mode', 'Difficulty', 'Times')]
    if not ticket:
        names += ['AbyssQuoteRow1'+field for field in ('RemainingLabel', 'TimesLabel', 'Remaining')]
    assert Counter(context.queries) == dict.fromkeys(names, 1)
    prior = [override['AbyssQuoteRow1Times']['roi'] for name, _, override in context.calls
             if name == 'AbyssQuoteRow1Times']
    context.pipeline['AbyssQuoteRow1Times']['recognition']['param']['roi'][1] += 7
    context.results['AbyssTicketOwned' if ticket else 'AbyssQuoteTicketsOwned'] = found('206494')
    updated = deepcopy(context.pipeline)
    second = page.analyze(context, args())
    assert second and second.detail['tickets_owned'] == 206494 and context.pipeline == updated
    assert Counter(context.queries) == dict.fromkeys(names, 2)
    after = [override['AbyssQuoteRow1Times']['roi'] for name, _, override in context.calls
             if name == 'AbyssQuoteRow1Times'][3:]
    assert [roi[1] for roi in after] == [roi[1]+7 for roi in prior]
    assert len({tuple(roi) for roi in prior}) == 3


@pytest.mark.parametrize('ticket,field', [(False, field) for field in
    ('Mode', 'Difficulty', 'Times', 'RemainingLabel', 'TimesLabel', 'Remaining')]
    + [(True, field) for field in ('Mode', 'Difficulty', 'Times')])
@pytest.mark.parametrize('fault', ['low_score', 'multiple_values', 'missing', 'bad_layout'])
def test_abyss_cache_keeps_unsafe_field_rejection(ticket, field, fault):
    context = abyss_context()
    name = 'AbyssQuoteRow1' + field
    found_word = (context.results[name](name, {name: {'expected': '^高难$'}})
                  if callable(context.results[name]) else context.results[name])
    word = deepcopy(found_word.raw_detail['filtered'][0])
    if fault == 'low_score':
        word['score'] = .89
        context.results[name] = found(words=[word])
    elif fault == 'multiple_values':
        context.results[name] = found(words=[word, deepcopy(word)])
    elif fault == 'missing':
        context.results[name] = found(hit=False)
    else:
        context.pipeline[name]['recognition']['param']['roi'][1] = 500
    page = (abyss_native.AbyssTicketPage(reading(), quotation(), require_ready=True) if ticket
            else abyss_native.AbyssQuotePage(reading()))
    assert page.analyze(context, args()) is None


@pytest.mark.parametrize('field', ['EventRowCategory', 'EventRowDifficulty', 'EventRowRemaining'])
@pytest.mark.parametrize('fault', ['low_score', 'multiple_values', 'missing'])
def test_event_cache_keeps_unsafe_field_rejection(field, fault, monkeypatch):
    monkeypatch.setattr(event_native, 'checked_glyph', checked)
    context = event_context()
    word = deepcopy(context.results[field].raw_detail['filtered'][0])
    if fault == 'low_score':
        word['score'] = .89
        context.results[field] = found(words=[word])
    elif fault == 'multiple_values':
        context.results[field] = found(words=[word, deepcopy(word)])
    else:
        context.results[field] = found(hit=False)
    page = event_native.EventPage(dict(layout='story_event_h', period='举办时间：10/0112:00~10/0704:59'))
    result = page.analyze(context, args('top'))
    assert result is None or result.detail['gaps']
