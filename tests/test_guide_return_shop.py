"""Bounded result navigation; synthetic pages do not prove a live return."""
import pytest
from test_guide_trial_battle import Port
from pcr_maa.guide_trial_battle import battle_pipeline, return_to_stage


class ReturnPort(Port):
    def __init__(self, pages, fault=None):
        super().__init__()
        self.pages = iter(pages)
        self.fault = fault

    def choose(self, names):
        hit = next(self.pages)
        assert hit in names or hit == 'GTUnknown'
        return hit

    def wait(self, names, **kwargs):
        if self.fault:
            raise RuntimeError(self.fault)
        return self.choose(names)


@pytest.mark.parametrize('pages,clicks', [
    (['GTWinObserved','GTRewardsObserved','GTLimitedShopObserved','GTFireMap'],
     ['GTWinNext','GTRewardsNext','GTLimitedShopCancel']),
    (['GTLimitedShopObserved','GTFireMap'], ['GTLimitedShopCancel']),
    (['GTRewardsObserved','GTLimitedShopObserved','GTStageDetail','GTFireMap'],
     ['GTRewardsNext','GTLimitedShopCancel','GTDetailCancel']),
    (['GTDefeat','GTFireMap'], ['GTDefeatEnd']),
    (['GTWinObserved','GTLevelUpObserved','GTLimitedShopObserved','GTFireMap'],
     ['GTWinNext','GTLevelUpDismiss','GTLimitedShopCancel']),
    (['GTLimitedShopObserved','GTLevelUpObserved','GTFireMap'],
     ['GTLimitedShopCancel','GTLevelUpDismiss']),
    (['GTFireMap'], []),
])
def test_result_return_uses_each_navigation_action_once(pages, clicks):
    port = ReturnPort(pages)
    return_to_stage(port)
    assert port.calls == [(node, True) for node in clicks]
    assert not any('Start' in node or 'Buy' in node for node, _ in port.calls)


@pytest.mark.parametrize('failure', ['guide_trial_page_timeout', 'stopped'])
def test_shop_cancel_unknown_or_stop_never_resends(failure):
    port = ReturnPort(['GTLimitedShopObserved'], fault=failure)
    with pytest.raises(RuntimeError, match=failure):
        return_to_stage(port)
    assert port.calls == [('GTLimitedShopCancel', True)]


def test_shop_requires_all_existing_daily_texts_and_no_preset_callback():
    nodes = battle_pipeline()
    expected = ['ExecLimitedShopButton','ExecLimitedShopTitle',
                'ExecLimitedShopNotice','ExecLimitedShopInstruction']
    assert nodes['GTLimitedShopObserved']['all_of'] == expected
    assert nodes['GTLimitedShopCancel']['all_of'] == expected
    assert nodes['GTLimitedShopCancel']['max_hit'] == 1
    assert nodes['GTLimitedShopCancel']['next'] == []
    assert nodes['GTLimitedShopCancel']['target'] is True
    assert 'ExecLimitedShopSession' not in str(nodes)


def test_return_pipeline_rejection_stops_before_navigation():
    port = ReturnPort(['GTLimitedShopObserved'])
    port.override_pipeline = lambda _: False
    with pytest.raises(RuntimeError, match='pipeline_rejected'):
        return_to_stage(port)
    assert not port.calls


@pytest.mark.parametrize('returned', [False, True])
def test_completed_return_notification_preserves_victory_and_attempt_limit(returned):
    from pcr_maa.supported_daily import summary
    report = dict(selected_modules=['guide_trial'], steps=[dict(result=dict(
        stage='红焰深域 8-6', outcome='already_cleared', attempts_used=3,
        stage_return_verified=returned))])
    text = summary(report, player=True)
    assert '已有通关记录，未重打' in text and '3/3' in text
    assert ('已返回地图。' if returned else '返回地图尚未确认。') in text


@pytest.mark.parametrize('state,expected', [
    ('not_sent','本次未发出开战'), ('sent','本次已发出开战'),
    ('unknown','本次开战是否发出无法确认'), ('previously_sent','本次未新增开战'),
])
def test_disconnect_notification_reports_actual_dispatch_state(state, expected):
    from pcr_maa.supported_daily import summary
    report = dict(selected_modules=['guide_trial'], steps=[dict(result=dict(
        outcome='stopped', error='guide_trial_disconnected_6002:battle_start=' + state))])
    text = summary(report, player=True)
    assert '游戏已断线（6002），已停止' in text and expected in text
    assert '手动重连' in text
