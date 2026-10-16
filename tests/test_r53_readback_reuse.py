"""Offline receipt and same-callback cache checks; never creates a device."""
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from pcr_maa import native, preset_execution_native as adapter
from pcr_maa.preset_confirmation import ConfirmationPage
from test_r39_recognition import PIPELINE, args, confirmation_context, found


def adjustment_receipt(value=4, action='FitIncrease'):
    return dict(native_ok=True, tasker_inited=True, task_id=123, nodes=[
        dict(name=action, completed=True, action=dict(name=action, success=True, raw=dict(point=[1, 1]))),
        dict(name='FitUpdatedRead', completed=True, action=None, recognition=[
            dict(name='FitPreset', algorithm='And', detail=[]),
            dict(name='FitUpdatedGuard', algorithm='Custom', detail=dict(filtered=[
                dict(detail=dict(filtered=[dict(text=str(value), score=.99, box=[1, 1, 10, 20])]))]))])])


class Resource:
    def __init__(self):
        self.registered = []
        self.unregistered = []
        self.overrides = []

    def override_pipeline(self, overrides):
        self.overrides.append(overrides)
        return True

    def register_custom_recognition(self, name, callback):
        self.registered.append(name)
        return True

    def unregister_custom_recognition(self, name):
        self.unregistered.append(name)


def fit(monkeypatch, receipts, target=5, current=3):
    calls = []
    original = dict(native_ok=True, task_id=122, nodes=[dict(name='FitCount', completed=True,
        recognition=dict(filtered=[dict(text=str(current), score=.99)]))])
    pending = iter(receipts)
    def run(tasker, entry):
        calls.append(entry)
        return original if entry == 'FitRead' else next(pending)
    monkeypatch.setattr(native, 'run', run)
    monkeypatch.setattr(native, 'save_receipt', lambda *a: 'synthetic-count-receipt')
    resource = Resource()
    port = adapter.NativePreset(SimpleNamespace(resource=resource))
    return port.fit_count(2, target), calls, resource, original


@pytest.mark.parametrize('current,target,values,action', [(3, 5, [4, 5], 'FitIncrease'),
    (5, 3, [4, 3], 'FitDecrease')])
def test_count_adjustment_reuses_each_real_completed_readback(monkeypatch, current, target, values, action):
    receipts = [adjustment_receipt(value, action) for value in values]
    unchanged = deepcopy(receipts)
    result, calls, resource, original = fit(monkeypatch, receipts, target, current)
    assert result['verified'] and result['observed'] == target
    assert calls == ['FitRead'] + [action + 'Start'] * len(values)
    assert result['initial_evidence'] is original
    for step, receipt, value in zip(result['steps'], receipts, values):
        assert step['observed'] == value
        assert step['evidence'] is step['readback'] is receipt
        assert not any(n['name'] == 'FitCount' for n in step['readback']['nodes'])
    assert receipts == unchanged
    assert resource.registered == resource.unregistered == ['CountFitNumber']


@pytest.mark.parametrize('fault', ['failed_task', 'uninitialized_tasker', 'missing_action', 'failed_action', 'incomplete_action',
    'missing_read', 'incomplete_read', 'read_before_action', 'duplicate_read', 'duplicate_action',
    'wrong_algorithm', 'duplicate_guard', 'no_custom_match', 'duplicate_custom_match', 'missing_detail',
    'missing_words', 'ambiguous_words', 'low_score', 'old_value', 'jump', 'zero', 'negative', 'merged',
    'out_of_range', 'wrong_guard'])
def test_count_untrustworthy_readback_stops_before_another_click(monkeypatch, fault):
    receipt = adjustment_receipt()
    clicked, read = receipt['nodes']
    guard = read['recognition'][1]
    matches = guard['detail']['filtered']
    words = matches[0]['detail']['filtered']
    if fault == 'failed_task': receipt['native_ok'] = False
    elif fault == 'uninitialized_tasker': receipt['tasker_inited'] = False
    elif fault == 'missing_action': clicked['action'] = None
    elif fault == 'failed_action': clicked['action']['success'] = False
    elif fault == 'incomplete_action': clicked['completed'] = False
    elif fault == 'missing_read': receipt['nodes'].pop()
    elif fault == 'incomplete_read': read['completed'] = False
    elif fault == 'read_before_action': receipt['nodes'].reverse()
    elif fault == 'duplicate_read': receipt['nodes'].append(deepcopy(read))
    elif fault == 'duplicate_action': receipt['nodes'].append(deepcopy(clicked))
    elif fault == 'wrong_algorithm': guard['algorithm'] = 'OCR'
    elif fault == 'duplicate_guard': read['recognition'].append(deepcopy(guard))
    elif fault == 'no_custom_match': matches.clear()
    elif fault == 'duplicate_custom_match': matches.append(deepcopy(matches[0]))
    elif fault == 'missing_detail': matches[0].pop('detail')
    elif fault == 'missing_words': words.clear()
    elif fault == 'ambiguous_words': words.append(dict(text='4', score=.1))
    elif fault == 'low_score': words[0]['score'] = .89
    elif fault == 'wrong_guard': guard['name'] = 'FitExpectedGuard'
    else: words[0]['text'] = dict(old_value='3', jump='5', zero='0', negative='-4',
        merged='4 4', out_of_range='999')[fault]
    result, calls, resource, _ = fit(monkeypatch, [receipt])
    assert not result['verified'] and result['observed'] is None
    assert calls == ['FitRead', 'FitIncreaseStart']
    assert result['steps'][0]['readback'] is receipt
    assert resource.unregistered == ['CountFitNumber']


def test_count_no_change_keeps_only_initial_independent_read(monkeypatch):
    result, calls, resource, _ = fit(monkeypatch, [], target=3)
    assert result['verified'] and result['observed'] == 3 and result['steps'] == []
    assert calls == ['FitRead']
    assert resource.unregistered == ['CountFitNumber']


def test_count_previous_step_receipt_cannot_authorize_a_third_click(monkeypatch):
    previous = adjustment_receipt(4)
    result, calls, _, _ = fit(monkeypatch, [previous, previous], target=6)
    assert not result['verified'] and result['observed'] is None
    assert [step['observed'] for step in result['steps']] == [4, None]
    assert calls == ['FitRead', 'FitIncreaseStart', 'FitIncreaseStart']


def test_count_expired_adjustment_window_never_clicks(monkeypatch):
    import time
    clock = iter((0, 120))
    monkeypatch.setattr(time, 'monotonic', lambda: next(clock, 120))
    result, calls, resource, _ = fit(monkeypatch, [])
    assert not result['verified'] and result['steps'] == []
    assert calls == ['FitRead'] and resource.unregistered == ['CountFitNumber']


def test_count_reads_real_native_and_custom_receipt_envelope(tmp_path, monkeypatch):
    from pcr_maa.replay import SavedImages

    monkeypatch.setattr(native, 'ROOT', tmp_path)
    resource = native.load_resource()
    assert resource.override_pipeline({
        'FitIncreaseStart': dict(next=['FitIncrease'], pre_delay=0, post_delay=0),
        'FitIncrease': dict(recognition='DirectHit', action='Click', target=[10, 10],
                            next=['FitUpdatedRead'], post_delay=0),
        'FitPreset': dict(recognition='DirectHit'),
        'FitUpdatedRead': dict(pre_delay=0, post_delay=0),
    })
    # Exercise Maa's actual Custom -> And JSON wrapping independently of font OCR.
    def recognize(self, context, argv):
        return self.AnalyzeResult([1, 1, 10, 20],
            dict(filtered=[dict(text='4', score=.99, box=[1, 1, 10, 20])]))
    monkeypatch.setattr(adapter.PresetNumber, 'analyze', recognize)
    controller = SavedImages([[np.zeros((720, 1280, 3), dtype=np.uint8)]])
    port = adapter.NativePreset(native.bind(resource, controller))
    reads = []
    def initial(slot):
        reads.append(slot)
        port._count_receipt = dict(native_ok=True, nodes=[])
        return 3
    monkeypatch.setattr(port, 'read_count', initial)
    result = port.fit_count(2, 4)
    assert result['verified'] and result['observed'] == 4
    assert controller.clicks == [[10, 10]] and reads == [2]
    step = result['steps'][0]
    assert step['evidence'] is step['readback']
    assert adapter._updated_count(step['readback'], 'FitIncrease', 4, 10) == 4


def test_confirmation_row_configuration_cached_only_within_each_callback():
    context = confirmation_context(3)
    pipeline = deepcopy(PIPELINE)
    queries = []
    def get_node_data(name):
        queries.append(name)
        return pipeline[name]
    context.get_node_data = get_node_data
    page = ConfirmationPage()
    original = deepcopy(pipeline)
    assert page.analyze(context, args())
    expected = dict.fromkeys(('ExecRowCategory', 'ExecRowDifficulty', 'ExecRowCount',
                             'ExecRowRemaining', 'ExecRowSkipLabel'), 1)
    assert Counter(queries) == expected
    assert pipeline == original
    prior = [override['ExecRowCount']['roi'] for name, _, override in context.calls if name == 'ExecRowCount']
    pipeline['ExecRowCount']['attach']['row_roi'][1] += 7
    assert page.analyze(context, args())
    assert Counter(queries) == {name: 2 for name in expected}
    updated = [override['ExecRowCount']['roi'] for name, _, override in context.calls if name == 'ExecRowCount'][3:]
    assert [roi[1] for roi in updated] == [roi[1] + 7 for roi in prior]
    assert len([name for name, _, _ in context.calls if name == 'ExecRowCount']) == 6


@pytest.mark.parametrize('middle', ['animation', 'different'])
def test_confirmation_interrupted_short_sequence_needs_two_new_matching_frames(middle):
    context = confirmation_context()
    page = ConfirmationPage(stable_short=True)
    assert page.analyze(context, args()) is None
    if middle == 'animation':
        context.results['ExecRowsOCR'] = found(hit=False)
    else:
        context.results['ExecRowCount'] = found('2')
        context.results['ExecTimesTotal'] = found('2')
    assert page.analyze(context, args()) is None
    context = confirmation_context()
    assert page.analyze(context, args()) is None
    assert page.analyze(context, args())
