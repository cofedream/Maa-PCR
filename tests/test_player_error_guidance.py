"""Player guidance uses existing summaries without granting retry or execution."""
from contextlib import contextmanager
from copy import deepcopy
import json
import time
from types import SimpleNamespace
from pathlib import Path

import pytest


@pytest.mark.parametrize('name', ['event', 'deep', 'very_hard', 'hard', 'normal'])
def test_unconfirmed_consumption_reaches_player_summary_without_mutation(name):
    from pcr_maa import gui_contract as gui
    report = dict(outcome='stopped', supported_chain_complete=False, daily_complete=False,
        steps=[dict(name=name, status='stopped', result=dict(
            consumption_sent=True, settlement_verified=False, reason='settlement_unknown'))])
    before = deepcopy(report)
    text = gui.result_text(report)
    assert 'settlement_unknown' in text and '本步已尝试发送消费，结算结果未确认' in text
    assert '不要直接重跑、删账或换日' in text and '保留本次记录' in text
    assert report == before


@pytest.mark.parametrize('result, expected, forbidden', [
    ({}, [], ['本步已尝试发送消费', '本步未发送消费', '尚未确认恢复']),
    ({'consumption_sent': False}, ['本步未发送消费操作'], ['本步已尝试发送消费']),
    ({'count_fit': {'attempted': True, 'restored': False}}, ['尚未确认恢复', '原次数'], []),
    ({'count_fit': {'attempted': True}}, ['尚未确认恢复'], []),
    ({'count_fit': {'attempted': True, 'restored': True}}, [], ['尚未确认恢复']),
    ({'count_fit': {'attempted': False}}, [], ['尚未确认恢复']),
    ({'count_fit': None}, [], ['尚未确认恢复']),
    ({'count_fit': []}, [], ['尚未确认恢复']),
])
def test_missing_fields_do_not_invent_consumption_or_restore_facts(result, expected, forbidden):
    from pcr_maa import gui_contract as gui
    text = gui.result_text(dict(outcome='stopped', steps=[
        dict(name='hard', status='stopped', result=result)]))
    for phrase in expected:
        assert phrase in text
    for phrase in forbidden:
        assert phrase not in text
    assert '结果未确认时' in text


def test_result_error_and_step_error_are_preserved_in_summary():
    from pcr_maa import gui_contract as gui
    for step in [dict(name='deep', status='stopped', result={'error': 'producer_error'}),
                 dict(name='hard', status='failed', error='source_changed')]:
        text = gui.result_text(dict(outcome='stopped', steps=[step]))
        assert ('producer_error' if step['name'] == 'deep' else 'source_changed') in text


@pytest.mark.parametrize('error', ['game_day_changed_need_new_observation',
                                  'wall_clock_rollback_need_new_observation'])
def test_boundary_failure_names_actual_reason_without_claiming_refresh(error):
    from pcr_maa import gui_contract as gui
    text = gui.result_text(dict(outcome='stopped', error=error,
        steps=[dict(name='hard', status='not_run')]))
    assert error in text and '重新观察游戏当前状态' in text
    assert '不要直接重跑、删账或换日' in text


def test_player_copy_preserves_cli_success_and_schedule_unresolved_guidance():
    from pcr_maa import gui_contract as gui, supported_daily as chain
    complete = dict(scope='本次选集', outcome='complete', supported_chain_complete=True,
                    steps=[dict(name='hard', status='executed')])
    assert chain.summary(complete) == ('本次选集\n困难：已执行并结算\n'
        'supported_chain_complete=true\n完整日常未完成；daily_complete=false。')
    assert gui.result_text(complete) == '本次所选任务已结束。\n困难：已执行并结算'
    pending = dict(outcome='stopped', steps=[dict(name='schedule_onekey', status='stopped',
        result=dict(reason='unsettled_intent_any_day_no_resend', sent=[]))])
    text = gui.result_text(pending)
    assert '本次未发送日程输入' in text and '禁止直接重跑' in text
    assert '重开软件或换日不会解锁' in text and '不要删账' in text
    assert text.startswith('本次任务已停止。\n')
    assert 'daily_complete=' not in text and 'supported_chain_complete=' not in text


@pytest.mark.parametrize('error', ['owner_denied', 'owner_reply_timeout_no_retry',
    'owner_channel_closed_or_invalid', 'unexpected_after_input'])
def test_agent_exception_summary_preserves_error_without_claiming_no_consumption(error):
    from pcr_maa import gui_contract as gui
    result = dict(outcome='unknown', action_success=False, supported_chain_complete=False,
                  daily_complete=False, error=error, exception_type='RuntimeError')
    before = deepcopy(result)
    text = gui.result_text(result)
    assert error in text and '本次执行已停止' in text
    assert '不要直接重跑、删账或换日' in text and '保留本次日志' in text
    assert '任务尚未开始' not in text and '没有消费' not in text and '未发送消费' not in text
    assert result == before


@pytest.mark.parametrize('phase', ['authorize', 'business', 'publication'])
def test_actual_agent_exception_exit_publishes_guidance_and_keeps_unknown(
        phase, tmp_path, monkeypatch, capsys):
    from pcr_maa import agent_entry as entry, supported_daily as chain
    events = []

    @contextmanager
    def scope(*args, **kwargs):
        events.append('scope')
        yield object()

    action = entry.AlphaEntry(SimpleNamespace(scope=scope))
    grant = SimpleNamespace(deadline=time.monotonic() + 30, execute=True, game_day='',
        count_context='gui-current-state', schedule_authorizations=(),
        schedule_attest_current_plan=False, schedule_confirm_new_cycle=False, observation_source=None)

    def authorize(*args):
        if phase == 'authorize':
            raise ValueError('owner_denied')
        return grant, {}

    def business(*args, **kwargs):
        events.append('simulated_business')
        if phase == 'business':
            raise RuntimeError('unexpected_after_input')
        return dict(outcome='complete', supported_chain_complete=True,
                    daily_complete=False, steps=[])

    monkeypatch.setattr(action, '_authorize', authorize)
    monkeypatch.setattr(chain, 'execute_supported', business)
    monkeypatch.setattr(chain, 'CONTROL_ROOT', tmp_path / 'reports')
    monkeypatch.setattr(entry, 'LOG_ROOT', tmp_path / 'entry')
    if phase == 'publication':
        monkeypatch.setattr(action, '_publish',
            lambda result: (_ for _ in ()).throw(OSError('publication fault')))
    context = SimpleNamespace(get_task_job=lambda: SimpleNamespace(job_id=1))
    args = SimpleNamespace(custom_action_name=entry.ACTION, task_detail=SimpleNamespace(task_id=1),
        custom_action_param=json.dumps(dict(version=1, selected_modules=['hard'], rewards_final=False)))
    assert action.run(context, args) is False
    result = action.last_result
    assert result['outcome'] == 'unknown' and result['action_success'] is False
    assert result['supported_chain_complete'] is False and result['daily_complete'] is False
    assert '本次执行已停止' in result['summary']
    assert '不要直接重跑、删账或换日' in result['summary']
    assert '任务尚未开始' not in result['summary'] and '没有消费' not in result['summary']
    if phase == 'authorize':
        assert events == [] and result['error'] == 'owner_denied'
    else:
        assert events == ['scope', 'simulated_business']
        assert result['error'] == ('unexpected_after_input' if phase == 'business'
                                  else 'alpha_result_publication_failed')
    if phase != 'publication':
        published = json.loads(capsys.readouterr().out.split('info: PCR Alpha ', 1)[1])
        assert published['error'] == result['error'] and published['summary'] == result['summary']
        assert Path(result['entry_receipt']).is_relative_to(tmp_path)


def test_publication_failure_preserves_original_exception(monkeypatch, tmp_path):
    from pcr_maa import agent_entry as entry
    action = entry.AlphaEntry(SimpleNamespace(scope=lambda *a, **kw: pytest.fail('business')))
    monkeypatch.setattr(action, '_authorize',
        lambda *a: (_ for _ in ()).throw(ValueError('owner_denied')))
    monkeypatch.setattr(action, '_publish',
        lambda result: (_ for _ in ()).throw(OSError('publication fault')))
    context = SimpleNamespace(get_task_job=lambda: SimpleNamespace(job_id=1))
    args = SimpleNamespace(custom_action_name=entry.ACTION, task_detail=SimpleNamespace(task_id=1),
        custom_action_param=json.dumps(dict(version=1, selected_modules=['hard'], rewards_final=False)))
    assert action.run(context, args) is False
    assert action.last_result['error'] == 'alpha_result_publication_failed'
    assert action.last_result['source_error'] == 'owner_denied'
    assert 'owner_denied' in action.last_result['summary']
