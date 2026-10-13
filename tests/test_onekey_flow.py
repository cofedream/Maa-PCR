"""Sequence/recovery boundaries with synthetic pages, no native controller."""
from types import SimpleNamespace

import pytest

from pcr_maa.onekey_flow import execute_pending, scan_rows
from pcr_maa.preset_execution import Journal
from pcr_maa.schedule_onekey import ITEMS
from pcr_maa.schedule_recovery import close_unresolved_dungeon


def schedule_page():
    return dict(state='schedule', bottom=True,
                sections={'delegated': {'total': 9, 'completed': 9}},
                rows={key: dict(state='complete', y=index) for index, key in enumerate(ITEMS)})


def forbidden(*args, **kwargs):
    pytest.fail('unexpected input')


def test_full_inventory_needs_no_scroll():
    page = schedule_page()
    expected = dict.fromkeys(ITEMS, 'complete')
    assert scan_rows(page, forbidden, [], expected=expected) == (expected, page)


@pytest.mark.parametrize('fault, reason', [
    ('page', 'list_replaced_by_unknown_page'),
    ('rows', 'nine_row_inventory_incomplete_or_changed'),
    ('count', 'row_marks_counter_disagree'),
    ('pending', 'nine_row_settlement_not_complete'),
    ('budget', 'scroll_budget_exhausted'),
])
def test_uncertain_inventory_never_dispatches(fault, reason):
    page, sent = schedule_page(), []
    if fault == 'page':
        page['state'] = 'unknown'
    elif fault == 'rows':
        del page['rows'][ITEMS[0]]
    elif fault == 'count':
        page['sections']['delegated']['completed'] = 8
    elif fault == 'pending':
        page['rows'][ITEMS[0]]['state'] = 'pending'
        page['sections']['delegated']['completed'] = 8
    else:
        page['bottom'] = False
        sent = ['down:' + str(i) for i in range(24)]
    with pytest.raises(ValueError, match=reason):
        scan_rows(page, forbidden, sent, expected=dict.fromkeys(ITEMS, 'complete'))


def test_normal_sequence_preserves_specific_confirmations_and_result_order():
    sequence = [('onekey', 'dungeon_confirm'), ('dungeon_confirm', 'dungeon_finish'),
                ('dungeon_finish', 'sweep_result'), ('dungeon_result', 'mana_confirm'),
                ('mana_confirm', 'sweep_result'), ('mana_result', 'exp_confirm'),
                ('exp_confirm', 'sweep_result'), ('exp_result', 'like_result'),
                ('like_result', 'item_result'), ('item_result', 'schedule')]
    sent, report = [], {}
    def send(action, before, *, targets):
        expected, state = sequence[len(sent)]
        assert action == expected and state in targets
        sent.append(action)
        return dict(state=state, quote={'synthetic': True})
    result = execute_pending(schedule_page(), ITEMS, send, report)
    assert result['state'] == 'schedule'
    assert sent == [action for action, _ in sequence]
    assert set(report['quotes']) == {'dungeon', 'mana', 'exp'}
    assert report['like_ap'] == {'synthetic': True}


@pytest.mark.parametrize('failure', ['unknown_result', 'stop'])
def test_sequence_never_retries_after_confirmation(failure):
    sent = []
    def send(action, before, *, targets):
        sent.append(action)
        if action == 'onekey':
            return dict(state='dungeon_confirm', quote={})
        if failure == 'stop':
            raise RuntimeError('stopped')
        return dict(state='unknown')
    with pytest.raises((ValueError, RuntimeError), match='settlement_missing_no_resend|stopped'):
        execute_pending(schedule_page(), ['dungeon_skip'], send, {})
    assert sent == ['onekey', 'dungeon_confirm']


@pytest.mark.parametrize('operation', ['onekey', 'dungeon_confirm'])
def test_recovery_closes_only_bound_result_and_keeps_old_journal(tmp_path, operation):
    journal = Journal(tmp_path / 'root')
    original = dict(intent_id='a' * 32)
    actions = Journal(journal.directory / 'actions' / original['intent_id'])
    with actions.locked():
        actions.append(dict(state='in_flight', operation=operation))
    before = actions.path.read_bytes()
    reader, sent = SimpleNamespace(recovery_result_actions=set()), []
    def send(action, page, **kwargs):
        assert action in reader.recovery_result_actions or action == 'close'
        sent.append(action)
        return dict(state={'dungeon_finish': 'sweep_result', 'dungeon_result': 'schedule', 'close': 'home'}[action])
    if operation == 'onekey':
        with pytest.raises(ValueError, match='recovery_result_not_bound_to_dungeon'):
            close_unresolved_dungeon(dict(state='dungeon_finish'), journal, original, reader, send)
        assert sent == []
    else:
        assert close_unresolved_dungeon(dict(state='dungeon_finish'), journal, original, reader, send)['state'] == 'home'
        assert sent == ['dungeon_finish', 'dungeon_result', 'close']
        assert not reader.recovery_result_actions
    assert actions.path.read_bytes() == before


@pytest.mark.parametrize('changed_inventory', [False, True])
def test_unresolved_readonly_completion_rechecks_history(tmp_path, monkeypatch, changed_inventory):
    """Synthetic native receipts exercise orchestration, not Maa OCR or input."""
    from datetime import datetime
    from pcr_maa import native
    import pcr_maa.schedule_onekey as module

    now = datetime.fromisoformat('2027-09-24T14:00:00+08:00')
    monkeypatch.setattr(module, 'now_cn', lambda: now)
    device = dict(serial='127.0.0.1:16384', manager=dict(source='mumu_manager_local_v1',
        installation=str(tmp_path / 'synthetic-installation'), index=0,
        created_timestamp=1, app_package='com.bilibili.priconne'))
    journal = Journal(tmp_path / '.runtime/schedule-onekey-work/ledger')
    binding = dict(game_day='2027-09-24', device=device, intent_id='a' * 32,
                   session_id='b' * 32, image_sha256='c' * 64)
    with journal.locked():
        journal.append(binding | dict(state='in_flight', operation='schedule_onekey',
            invocation_id='run-' + 'd' * 32, authorizations=list(ITEMS)))
    actions = Journal(journal.directory / 'actions' / binding['intent_id'])
    with actions.locked():
        actions.append(binding | dict(state='in_flight', operation='enter'))
        actions.append(binding | dict(state='settled', operation='enter', native_task_id=1))
        actions.append(binding | dict(state='in_flight', operation='onekey'))
    original = {p: p.read_bytes() for p in (journal.path, actions.path)}

    readers, released, sent = [], [], []
    def register(name, reader):
        assert name == 'OneKeyPage'
        readers.append(reader)
        return True
    def unregister(name):
        released.append(name)
        return True
    tasker = SimpleNamespace(stopping=False, controller=SimpleNamespace(_handle=1),
        resource=SimpleNamespace(_handle=2, register_custom_recognition=register,
                                 unregister_custom_recognition=unregister))
    pages = [dict(state='home', resources=dict(ap=100, jewels=500)), schedule_page(),
             dict(state='home', resources=dict(ap=100, jewels=500))]
    def run(current, entry):
        assert current is tasker
        assert entry in ('OneKeyExecRead', 'OneKeyDo_enter', 'OneKeyDo_close')
        reader = readers[0]
        reader.capture_index += 1
        reader.last = pages[len(sent)] | dict(session_id=reader.session_id,
            capture_index=reader.capture_index, captured_at=now.isoformat(),
            acquisition='current_tasker_capture', image_sha256='e' * 64)
        node = 'OneKeyExecSnapshot'
        if entry != 'OneKeyExecRead':
            action = entry.removeprefix('OneKeyDo_')
            assert action == ('enter', 'close')[len(sent)]
            sent.append(action)
            node = 'OneKeyInput_' + action
            if changed_inventory and action == 'close':
                orphan = Journal(journal.directory / 'actions' / ('f' * 32))
                with orphan.locked():
                    orphan.append(binding | dict(state='in_flight', operation='onekey'))
        return dict(native_ok=True, tasker_inited=True, task_id=reader.capture_index,
                    nodes=[dict(name=node, completed=True, action=dict(name=node, success=True))])
    monkeypatch.setattr(native, 'run', run)

    result = module.schedule_onekey(tasker, execute=True, game_day='2027-09-24',
        device=device, authorizations=ITEMS, journal=journal, gui_start_intent=True)
    assert sent == result['sent'] == ['enter', 'close']
    assert released == ['OneKeyPage']
    assert all(p.read_bytes() == before for p, before in original.items())
    assert result['consumer_state'] == 'not_started' and not result['one_key_attempted']
    assert result['original_execution_unresolved'] and not result['settlement_verified']
    assert result['retry_permitted'] is False
    if changed_inventory:
        assert result['outcome'] == 'unknown'
        assert result['reason'] == 'orphan_action_history_unverified'
        assert not result.get('current_observation_verified')
    else:
        assert result['outcome'] == 'current_complete_readonly', result.get('reason')
        assert result['current_observation_verified'] and result['home_verified']
        assert result['input_evidence'] == 'current_readonly_observation'
        assert set(p.name for p in (journal.directory / 'actions').iterdir()) == {binding['intent_id']}
