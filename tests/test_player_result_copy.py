"""Player wording is presentation only; CLI output and report facts stay intact."""
from copy import deepcopy

import pytest

from pcr_maa import gui_contract as gui, supported_daily as chain


def test_player_copy_selected_success_omits_diagnostic_scope_without_mutating_report():
    report = dict(scope='diagnostic-only-scope\n深渊：unknown/unsupported。daily_complete=false。',
        outcome='complete', supported_chain_complete=True, daily_complete=False,
        selected_modules=['normal'], steps=[dict(name='normal', status='executed')])
    before = deepcopy(report)
    expected = '本次所选任务已结束。\n普通（每关本次次数）：已执行并结算'
    assert gui.result_text(report) == expected
    assert chain.summary(report, player=True) == expected
    assert report == before


def test_player_copy_stamina_skip_is_a_safe_end_not_completed_daily_goals():
    report = dict(outcome='complete', supported_chain_complete=True, daily_complete=False,
        steps=[dict(name='hard', status='skipped_insufficient_stamina',
            result=dict(consumption_sent=False, settlement_verified=False))])
    before = deepcopy(report)
    assert gui.result_text(report) == ('本次所选任务已结束。\n'
        '困难：体力不足，安全跳过（日目标未满）')
    assert report == before


def test_player_copy_partial_preserves_module_facts_and_cli_default_bytes():
    report = dict(outcome='partial', supported_chain_complete=False, daily_complete=False,
        steps=[dict(name='event', status='partial'), dict(name='rewards', status='partial')])
    before = deepcopy(report)
    expected_cli = ('【仅已支持项目串联：活动→深域→每日任务/礼物（早段）→高难→困难→每日任务/礼物（收尾）】\n'
        '日程执行：unsupported；深渊仅支持前哨；首领、购买重置和次数调整：unsupported。完整日常未交付，daily_complete=false。\n'
        '活动：已执行并结算，日目标未满\n每日任务/礼物（早段）：部分领取，仍有 remaining\n'
        'supported_chain_complete=false\n完整日常未完成；daily_complete=false。').encode('utf-8')
    assert chain.summary(report).encode('utf-8') == expected_cli
    assert chain.summary(report, player=False).encode('utf-8') == expected_cli
    assert gui.result_text(report) == ('本次所选任务仍有未完成项。\n'
        '活动：已执行并结算，日目标未满\n每日任务/礼物（早段）：仍有奖励未领取')
    assert report == before


@pytest.mark.parametrize('outcome,complete,title', [
    ('stopped', False, '本次任务已停止。'),
    ('failed', False, '本次任务异常停止。'),
    ('interrupted', False, '本次任务已中断。'),
    ('stopped_by_request', False, '本次任务已按请求停止。'),
    ('running', False, '记录显示任务仍在运行，请确认程序当前状态。'),
    ('unknown', False, '本次任务结果尚未确认。'),
    ('complete', False, '本次任务结果尚未确认。'),
    ('complete', 1, '本次任务结果尚未确认。'),
])
def test_player_copy_headline_does_not_promote_incomplete_or_uncertain_results(outcome, complete, title):
    report = dict(scope='diagnostic-only-scope\n深渊：unknown/unsupported。', outcome=outcome,
        supported_chain_complete=complete, daily_complete=False, steps=[])
    before = deepcopy(report)
    text = gui.result_text(report)
    assert text.splitlines()[0] == title
    assert all(token not in text for token in (
        'diagnostic-only-scope', 'unsupported', 'daily_complete=', 'supported_chain_complete='))
    if outcome == 'stopped_by_request':
        assert '后续未运行' in text
    assert report == before


def test_player_copy_unknown_reward_keeps_attempted_claim_and_no_retry_guidance():
    reason = 'Unverified reward stage: RewardsMissionsResultRead'
    report = dict(outcome='stopped', supported_chain_complete=False, daily_complete=False,
        steps=[dict(name='rewards', status='stopped', result=dict(outcome='unknown',
            error=reason, home_verified=False, items=dict(missions=dict(claim_attempted=True,
                result_verified=False, home_verified=False, status='unknown'))))])
    before = deepcopy(report)
    text = gui.result_text(report)
    assert text.startswith('本次任务已停止。\n')
    assert '每日任务：已尝试领取，结果尚未确认。' in text and reason in text
    assert '结果未确认时不要直接重跑、删账或换日' in text
    assert '礼物：未尝试领取。' not in text and '本步未发送消费操作。' not in text
    assert 'daily_complete=' not in text and 'supported_chain_complete=' not in text
    assert report == before


def test_player_copy_agent_exception_explains_recovery_before_original_reasons():
    scope_error = 'Callback scope stopped or blocked; outcome unknown'
    report = dict(outcome='unknown', action_success=False, supported_chain_complete=False,
        daily_complete=False, error='alpha_result_publication_failed', source_error=scope_error,
        exception_type='RuntimeError')
    before = deepcopy(report)
    text = gui.result_text(report)
    lines = text.splitlines()
    assert lines[0] == '本次执行已停止；未确认的结果不能视为成功。'
    assert lines[1] == '本次结果记录未能正常发布。'
    assert lines[-1] == '原因：alpha_result_publication_failed；原错误：' + scope_error
    assert text.index('保留本次日志') < text.index('原因：')
    assert '不要直接重跑、删账或换日' in text
    assert 'daily_complete=' not in text and 'supported_chain_complete=' not in text
    assert report == before
