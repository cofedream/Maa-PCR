"""Public synthetic recognition contracts; no device or private frames required."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pcr_maa import native, preset_execution_native
from pcr_maa.abyss_native import AbyssHomePage, _NoMatch, _Words
from pcr_maa.daily_goal import evaluate_daily_goal
from pcr_maa.preset_confirmation import ConfirmationCollector, ConfirmationPage
from pcr_maa.preset_execution import assess


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = json.loads((ROOT / 'assets/resource/pipeline/preset_execution.json').read_text(encoding='utf-8'))


def frame():
    return np.zeros((720, 1280, 3), dtype=np.uint8)


def found(text=None, *, hit=True, score=.99, box=None, words=None):
    if words is None:
        words = [] if text is None else [dict(text=text, score=score, box=box or [1, 1, 20, 20])]
    return SimpleNamespace(hit=hit, raw_detail=dict(filtered=words), reco_id=123, box=[0, 0, 1, 1])


class Context:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def run_recognition(self, name, image, override=None):
        self.calls.append((name, image, deepcopy(override)))
        result = self.results.get(name, found(hit=False))
        if isinstance(result, Exception):
            raise result
        return result(name, override) if callable(result) else result

    def get_node_data(self, name):
        return PIPELINE[name]


def confirmation_context(rows=1, *, kind='short'):
    results = dict(ExecConfirmHeader=found(), ExecConfirmStamina=found(str(rows * 10)),
                   ExecConfirmTickets=found(str(rows * 3)), ExecOwnedStamina=found('100'),
                   ExecStageTotal=found(str(rows) + '处'), ExecTimesTotal=found(str(rows * 3)),
                   ExecRowCategory=found('主线关卡'), ExecRowDifficulty=found('高难'),
                   ExecRowCount=found('3'), ExecRowRemaining=found('3/3'),
                   ExecRowSkipLabel=found('跳过次数'))
    results['ExecRowsOCR'] = found(words=[dict(text=f'测试1-{i + 1}', score=.99,
        box=[100, 140 + i * 80, 150, 20]) for i in range(rows)])
    if kind == 'page':
        results.update(ExecRailBlue=found(), ExecRailGray=found())
    elif kind == 'top':
        results.update(ExecBlueTop=found(), ExecGrayBottom=found())
    elif kind == 'bottom':
        results.update(ExecGrayTop=found(), ExecBlueBottom=found())
    return Context(results)


def args(kind='short', image=None):
    return SimpleNamespace(custom_recognition_param=json.dumps(kind), image=frame() if image is None else image)


def test_confirmation_page_deduplicates_rail_on_same_frame_only():
    context = confirmation_context(kind='page')
    page = ConfirmationPage()
    result = page.analyze(context, args('page'))
    assert result and not result.detail['gaps']
    counts = Counter(name for name, _, _ in context.calls)
    assert counts['ExecRailBlue'] == counts['ExecRailGray'] == 1
    first_count = len(context.calls)
    assert page.analyze(context, args('page'))
    assert len(context.calls) == first_count * 2


@pytest.mark.parametrize('rows', [1, 2, 3])
def test_confirmation_row_overrides_are_not_shared(rows):
    context = confirmation_context(rows)
    result = ConfirmationPage().analyze(context, args())
    assert result and not result.detail['gaps']
    calls = [override['ExecRowCount']['roi'] for name, _, override in context.calls if name == 'ExecRowCount']
    assert len(calls) == rows and len({tuple(roi) for roi in calls}) == rows
    assert [row['count'] for row in result.detail['rows']] == [3] * rows


@pytest.mark.parametrize('kind', ['top', 'bottom', 'page', 'short'])
def test_confirmation_layout_contract(kind):
    result = ConfirmationPage().analyze(confirmation_context(kind=kind), args(kind))
    assert result and result.detail['kind'] == kind


@pytest.mark.parametrize('change', ['danger', 'missing_header', 'missing_count', 'low_score',
    'merged_number', 'ambiguous_number', 'wrong_layout', 'partial_title', 'wrong_total'])
def test_confirmation_negative_candidates_not_accepted(change):
    context = confirmation_context()
    if change == 'danger': context.results['ExecUnsafeStop'] = found()
    elif change == 'missing_header': context.results['ExecConfirmHeader'] = found(hit=False)
    elif change == 'missing_count': context.results['ExecRowCount'] = None
    elif change == 'low_score': context.results['ExecRowCount'] = found('3', score=.89)
    elif change == 'merged_number': context.results['ExecRowCount'] = found('3 3')
    elif change == 'ambiguous_number':
        context.results['ExecRowCount'] = found(words=[dict(text='3', score=.99, box=[1, 1, 20, 20])] * 2)
    elif change == 'wrong_layout': context.results['ExecRailBlue'] = found()
    elif change == 'partial_title': context.results['ExecRowsOCR'].raw_detail['filtered'][0]['box'][1] = 130
    elif change == 'wrong_total': context.results['ExecTimesTotal'] = found('4')
    assert ConfirmationPage().analyze(context, args()) is None


def test_short_confirmation_requires_two_fresh_callbacks():
    context = confirmation_context()
    page = ConfirmationPage(stable_short=True)
    first, second = args(), args()
    assert page.analyze(context, first) is None
    assert page.analyze(context, second)
    assert any(image is first.image for _, image, _ in context.calls)
    assert any(image is second.image for _, image, _ in context.calls)
    assert Counter(name for name, _, _ in context.calls)['ExecRowsOCR'] == 2
    context.results['ExecRowCount'] = found('2')
    assert page.analyze(context, args()) is None


def test_short_confirmation_deadline_still_checked_after_recognition(monkeypatch):
    from pcr_maa import preset_confirmation
    context = confirmation_context(kind='page')
    now = [0]
    monkeypatch.setattr(preset_confirmation.time, 'monotonic', lambda: now[0])
    page = ConfirmationPage()
    page.short_deadline = 10
    def expire(name, override):
        now[0] = 10
        return found(hit=False)
    context.results['ExecRailBlue'] = expire
    assert page.analyze(context, args()) is None
    assert Counter(name for name, _, _ in context.calls)['ExecRailBlue'] == 1
    assert 'ExecRowsOCR' not in [name for name, _, _ in context.calls]


def test_confirmation_sdk_exception_stays_inside_callback():
    context = confirmation_context()
    context.results['ExecRowCount'] = RuntimeError('synthetic SDK exception')
    assert ConfirmationPage().analyze(context, args()) is None


def test_confirmation_duplicate_titles_still_rejected_by_collector():
    context = confirmation_context(2)
    context.results['ExecRowsOCR'].raw_detail['filtered'][1]['text'] = '测试1-1'
    result = ConfirmationPage().analyze(context, args())
    assert result is not None
    collector = ConfirmationCollector()
    collected = collector.run(None, SimpleNamespace(reco_detail=SimpleNamespace(
        raw_detail=dict(best=dict(detail=result.detail)))))
    assert collected is False and collector.complete is False
    assert collector.reason == 'conflicting_rows'


def test_confirmation_long_list_requires_bottom_with_matching_overlap():
    context = confirmation_context(3, kind='top')
    page = ConfirmationPage()
    collector = ConfirmationCollector()
    first = page.analyze(context, args('top'))
    assert first is not None
    def collect(detail):
        return collector.run(None, SimpleNamespace(reco_detail=SimpleNamespace(
            raw_detail=dict(best=dict(detail=detail)))))
    assert collect(first.detail) and not collector.complete
    context = confirmation_context(3, kind='bottom')
    last = page.analyze(context, args('bottom'))
    assert last is not None
    assert collect(last.detail) and collector.complete
    assert len(collector.rows) == 3


def test_abyss_cache_key_contains_name_and_full_canonical_override():
    context = Context({'A': found('3'), 'B': found('3')})
    words = _Words(context, frame())
    original = {'A': {'roi': [1, 2, 3, 4], 'expected': '^3$', 'only_rec': False}}
    reordered = {'A': {'only_rec': False, 'expected': '^3$', 'roi': [1, 2, 3, 4]}}
    assert words.integer('A', original) == words.integer('A', reordered) == 3
    assert len(context.calls) == 1
    for changed in ({'A': {'roi': [1, 3, 3, 4], 'expected': '^3$', 'only_rec': False}},
                    {'A': {'roi': [1, 2, 3, 4], 'expected': '^4$', 'only_rec': False}},
                    {'A': {'roi': [1, 2, 3, 4], 'expected': '^3$', 'only_rec': True}}, None, {}):
        words.recognize('A', changed)
    words.recognize('B', original)
    assert len(context.calls) == 7


def test_abyss_cache_does_not_reuse_mutated_override_or_new_frame():
    context = Context({'A': found('3')})
    image = frame()
    words = _Words(context, image)
    override = {'A': {'roi': [1, 2, 3, 4]}}
    words.recognize('A', override)
    override['A']['roi'][1] = 5
    words.recognize('A', override)
    _Words(context, frame()).recognize('A', override)
    assert len(context.calls) == 3
    assert context.calls[0][2]['A']['roi'][1] == 2


@pytest.mark.parametrize('result', [None, found(hit=False)])
def test_abyss_negative_result_cache_keeps_no_match_semantics(result):
    context = Context({'A': result})
    words = _Words(context, frame())
    for _ in range(2):
        with pytest.raises(_NoMatch): words.integer('A')
    assert len(context.calls) == 1


@pytest.mark.parametrize('result', [found('3', score=.89), found('3 3'), found('-1'),
    found(words=[dict(text='3', score=.99, box=[1, 1, 2, 2])] * 2)])
def test_abyss_numeric_validation_unchanged(result):
    with pytest.raises(_NoMatch): _Words(Context({'A': result}), frame()).integer('A')


def test_abyss_sdk_exception_is_not_cached_or_allowed_through_callback():
    context = Context({'A': RuntimeError('synthetic SDK exception'),
                       'AbyssHomeGuard': RuntimeError('synthetic SDK exception')})
    words = _Words(context, frame())
    for _ in range(2):
        with pytest.raises(RuntimeError): words.recognize('A')
    assert len(context.calls) == 2
    assert AbyssHomePage().analyze(context, args()) is None


class Resource:
    def get_node_data(self, name):
        return dict(attach=dict(default_roi=[1, 2, 3, 4], normal_roi=[5, 6, 7, 8], default_only_rec=False))

    def override_pipeline(self, overrides):
        return True


def scanned(slot=2, *, normal=False):
    return dict(native_ok=True, list_complete=True, incomplete_reason=None, requested_slot=slot,
        selected_slot=slot, view='checked', rows=[dict(title='测试1-1', category='主线关卡',
            difficulty='普通' if normal else '高难', checked=True, unlimited=normal,
            remaining=None if normal else 3, limit=None if normal else 3)])


def receipt(names, *, native_ok=True):
    return dict(native_ok=native_ok, nodes=[dict(name=name, completed=True) for name in names])


def read_with(monkeypatch, returned, *, normal=False):
    monkeypatch.setattr(preset_execution_native, 'scan', lambda *a, **kw: scanned(normal=normal))
    def run(tasker, entry):
        assert entry == 'PresetReadbackStart'
        return deepcopy(returned)
    monkeypatch.setattr(native, 'run', run)
    monkeypatch.setattr(native, 'save_receipt', lambda *a: 'synthetic-receipt')
    monkeypatch.setattr(native, 'observe_tasker', lambda *a: pytest.fail('full-screen OCR must not run'))
    return preset_execution_native.NativePreset(SimpleNamespace(resource=Resource())).read(2)


@pytest.mark.parametrize('actual_slot', range(1, 8))
def test_preset_readback_reports_actual_slot_not_requested(monkeypatch, actual_slot):
    result = read_with(monkeypatch, receipt(['ObservePresets', f'SelectedSlot{actual_slot}']))
    assert result['ocr_receipt']['native_ok'] is True
    assert result['observation']['page'] == 'presets'
    assert result['observation']['selected_slot'] == actual_slot
    goal = evaluate_daily_goal(result, 3, count_context='gui-current-state')
    assert ('final_slot_unverified' in goal['unknown_reasons']) == (actual_slot != 2)


@pytest.mark.parametrize('returned', [receipt(['ObserveUnknown']), receipt(['ObservePresets', 'SelectionUnknown']),
    receipt(['ObservePresets', 'SelectedSlot2'], native_ok=False), receipt(['SelectedSlot2'])])
def test_preset_readback_unknown_or_failed_state_never_qualifies(monkeypatch, returned):
    result = read_with(monkeypatch, returned)
    assert result['observation']['selected_slot'] is None
    assert 'final_slot_unverified' in evaluate_daily_goal(result, 3, count_context='gui-current-state')['unknown_reasons']


@pytest.mark.parametrize('actual_slot,expected', [(2, 'needs_execution'), (3, 'reading_unknown')])
def test_normal_readback_uses_same_fresh_page_slot_contract(monkeypatch, actual_slot, expected):
    result = read_with(monkeypatch, receipt(['ObservePresets', f'SelectedSlot{actual_slot}']), normal=True)
    outcome, _ = assess(result, dict(slot=2, kind='normal', target_per_stage=3), 'gui-current-state')
    assert outcome == expected


def test_preset_readback_propagates_stop_and_unknown_exceptions(monkeypatch):
    monkeypatch.setattr(preset_execution_native, 'scan', lambda *a, **kw: scanned())
    def stopped(*args):
        raise RuntimeError('Native tasker stopped or blocked; outcome unknown, no retry')
    monkeypatch.setattr(native, 'run', stopped)
    with pytest.raises(RuntimeError, match='no retry'):
        preset_execution_native.NativePreset(SimpleNamespace(resource=Resource())).read(2)


@pytest.mark.parametrize('slot', [0, 8, -1, '2', True])
def test_preset_boundary_invalid_slot_rejected_before_native_input(slot):
    with pytest.raises(ValueError, match='slot'):
        preset_execution_native.NativePreset(None).verify_preset(slot)


def test_preset_boundary_verification_does_not_scan_or_invent_target_slot(monkeypatch):
    monkeypatch.setattr(native, 'run', lambda *a: receipt(['ObservePresets', 'SelectedSlot3']))
    monkeypatch.setattr(preset_execution_native, 'scan', lambda *a, **kw: pytest.fail('boundary must not scan'))
    result = preset_execution_native.NativePreset(SimpleNamespace(resource=Resource())).verify_preset(2)
    assert result['native_ok'] and result['requested_slot'] == 2
    assert result['selected_slot'] == result['observation']['selected_slot'] == 3


def test_preset_readback_pipeline_has_only_existing_no_input_guards():
    pipeline = json.loads((ROOT / 'assets/resource/pipeline/presets.json').read_text(encoding='utf-8'))
    entry = pipeline['PresetReadbackStart']
    assert entry['timeout'] == pipeline['ReadScreen']['timeout']
    assert entry['rate_limit'] == pipeline['ReadScreen']['rate_limit']
    pending, seen = ['PresetReadbackStart'], set()
    while pending:
        name = pending.pop()
        if name in seen: continue
        seen.add(name)
        node = pipeline[name]
        assert node.get('action', 'DoNothing') == 'DoNothing'
        pending.extend(node.get('next', []))
        pending.extend(node.get('all_of', []) if all(isinstance(n, str) for n in node.get('all_of', [])) else [])
    assert 'ObservePresets' in seen and 'ObserveUnknown' in seen
    assert {f'SelectedSlot{i}' for i in range(1, 8)} <= seen
    assert not {'ScreenOCR', 'ReadScreen', 'EnterAdventure', 'OpenPresets'} & seen
