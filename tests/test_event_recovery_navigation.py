"""Synthetic post-result pages; no controller, SDK job or game input."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from pcr_maa import activity_navigation, native
from pcr_maa.event_native import NativeEvent
from pcr_maa.preset_execution_native import NativePreset


OLD_IDENTITY = dict(layout='story_event_h', period='举办时间：10/0112:00~10/0704:59')
NEW_IDENTITY = dict(layout='story_event_h', period='举办时间：11/0112:00~11/0704:59')


def receipt(*names, ok=True):
    return dict(native_ok=ok, nodes=[dict(name=name, completed=True) for name in names])


@pytest.fixture
def recovery(monkeypatch, tmp_path):
    state = dict(page='list', calls=[], hits={}, home_ok=True)
    tasker = SimpleNamespace(resource=SimpleNamespace(override_pipeline=lambda _: True))
    port = NativeEvent(tasker, start='event')
    monkeypatch.setattr(port, '_confirmation_scope', lambda: nullcontext())

    def finish(_self, before, quote, submission, entry):
        assert entry == 'ExecRecoverResultStart'
        assert port.event_identity == OLD_IDENTITY
        state['calls'].append(entry)
        terminal = {'list': 'ExecReturnedPreset', 'home': 'ObserveHome'}.get(state['page'])
        return dict(result_evidence=receipt(*([terminal] if terminal else []), ok=bool(terminal)))

    def run(_tasker, entry, *, reset_hits=()):
        state['calls'].append(entry)
        for node in reset_hits:
            state['hits'][node] = 0
        if entry == 'EventRestoreView':
            assert state['page'] == 'list'
            if state['hits'].get('EventRestoreToggle', 0) == 1:
                return receipt(ok=False)
            state['hits']['EventRestoreToggle'] = 1
            return receipt('EventAllReady')
        if entry == 'EventReturnHome':
            assert state['page'] == 'list'
            # Borrowed Context retains these production max_hit=1 budgets.
            for node in ('EventListCancel', 'EventMapHome'):
                if state['hits'].get(node, 0) == 1:
                    return receipt(ok=False)
                state['hits'][node] = 1
            if not state['home_ok']:
                return receipt(ok=False)
            state['page'] = 'home'
            return receipt('ObserveHome')
        assert entry == 'EventFromHome' and state['page'] == 'home'
        assert port.event_identity is None
        state['page'] = 'list'
        result = receipt('StoryEventOpenQuests', 'EventListReady')
        result['nodes'][0]['recognition'] = dict(all_of=[dict(name='StoryEventPeriod',
            detail=dict(filtered=[dict(text=NEW_IDENTITY['period'], score=.99)]))])
        return result

    def prepare(_tasker):
        state['calls'].append('ActivityPrepareHome')
        return receipt('ObserveHome', ok=state['page'] == 'home')

    def read_list():
        port.original_view = 'all'  # scanning the all view selects the checked view
        return dict(native_ok=True, list_complete=True, event_verified=True,
                    rows=[dict(title='活动关卡H-3')], event_identity=dict(port.event_identity))

    monkeypatch.setattr(NativePreset, '_finish_result', finish)
    monkeypatch.setattr(native, 'run', run)
    monkeypatch.setattr(native, 'save_receipt', lambda *a: tmp_path / 'reading.json')
    monkeypatch.setattr(activity_navigation, 'prepare_home', prepare)
    monkeypatch.setattr(activity_navigation, 'absent_entry', lambda *a: nullcontext())
    monkeypatch.setattr(port, 'read_list', read_list)
    record = dict(before=dict(event_identity=OLD_IDENTITY), quote={}, consumer={})
    return port, record, state


@pytest.mark.parametrize('page', ['list', 'home'])
@pytest.mark.parametrize('start', ['home', 'event'])
@pytest.mark.parametrize('reused', [False, True])
def test_result_recovery_reaches_home_then_rebinds_event(recovery, page, start, reused):
    port, record, state = recovery
    state['page'] = page
    port.start = start
    if reused:
        port.entered = port._navigation_attempted = True
        port.original_view = 'all'
        port.map_name = 'old-map'

    result = port.recover_result(record)
    assert result['recovery_verified'] is True
    assert state['page'] == 'home'
    assert port.event_identity is None and port.entered is False
    reading = port.read()
    assert reading['event_verified'] and reading['list_complete']
    assert reading['event_identity'] == NEW_IDENTITY
    assert state['calls'].count('EventReturnHome') == (1 if page == 'list' else 0)
    assert state['calls'].count('EventFromHome') == 1
    # The normal goal-complete path can return home again in the same Context.
    assert port.home() is True
    assert state['page'] == 'home'


def test_recovered_list_without_verified_home_stays_incomplete(recovery):
    port, record, state = recovery
    state['home_ok'] = False
    result = port.recover_result(record)
    assert result['recovery_verified'] is False
    assert state['calls'] == ['ExecRecoverResultStart', 'EventReturnHome']
    assert port.event_identity is None and not port.entered


def test_unknown_recovery_page_never_attempts_home(recovery):
    port, record, state = recovery
    state['page'] = 'unknown'
    result = port.recover_result(record)
    assert result['recovery_verified'] is False
    assert state['calls'] == ['ExecRecoverResultStart']
    assert port.event_identity is None


def test_failed_home_dispatch_clears_temporary_identity(recovery, monkeypatch):
    port, record, state = recovery

    def stopped():
        raise RuntimeError('synthetic stopped navigation')

    monkeypatch.setattr(port, 'home', stopped)
    with pytest.raises(RuntimeError, match='synthetic stopped navigation'):
        port.recover_result(record)
    assert port.event_identity is None
