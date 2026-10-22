"""The additional profile is a choice on the existing Owner, never a permission."""
import json
from types import SimpleNamespace as NS
import pytest
from pcr_maa import gui_contract, supported_daily
from pcr_maa.agent_entry import parse_selection


MODULE = 'clan_battle_simulation'


def test_clan_selection_uses_original_alpha_pi_slots():
    def node(name):
        return dict(enabled=False, action=dict(type='DoNothing', param={}), next=[], on_error=[],
                    attach=dict(selected=name == 'PCRAlphaSelectClanBattleSimulation'))
    raw = json.dumps(dict(selection_mode='pi_nodes_v1', selected_modules=[MODULE]))
    assert parse_selection(raw, context=NS(get_node_data=node)) == gui_contract.make_combination([MODULE])


@pytest.mark.parametrize('missing', [('PCRAlphaSelectClanBattleSimulation',),
    ('PCRAlphaSelectClanBattleSimulation','PCRAlphaSelectGuideTrial'),
    ('PCRAlphaSelectClanBattleSimulation','PCRAlphaSelectGuideTrial','PCRAlphaSelectAbyss')])
def test_old_pi_bundles_keep_their_original_choices(missing):
    def node(name):
        if name in missing:
            return None
        return dict(enabled=False, action=dict(type='DoNothing', param={}), next=[], on_error=[],
                    attach=dict(selected=name == 'PCRAlphaSelectHard'))
    assert parse_selection(json.dumps(dict(selection_mode='pi_nodes_v1')),
        context=NS(get_node_data=node)) == gui_contract.make_combination(['hard'])


@pytest.mark.parametrize('new_fields', [(), ('guide_trial',), (MODULE,), ('guide_trial', MODULE)])
def test_legacy_module_flags_remain_compatible(new_fields):
    names = ['schedule_onekey','event','abyss','deep','rewards','very_hard','hard','normal', *new_fields]
    raw = dict(version=1, module_flags={name:name == 'hard' for name in names}, rewards_final=False)
    assert parse_selection(json.dumps(raw)) == gui_contract.make_combination(['hard'])


@pytest.mark.parametrize('others,final', [(['event'],False), (['guide_trial'],False), ([],True), ([MODULE],False)])
def test_clan_cannot_mix_with_daily_or_reward_actions(others,final):
    with pytest.raises(ValueError):
        gui_contract.make_combination([MODULE,*others],rewards_final=final)


def test_clan_is_not_a_standalone_cli_execution_path():
    with pytest.raises(ValueError,match='requires_alpha_owner_entry'):
        supported_daily.execute_supported(None,{},game_day='',context='',directory=None,
            execute=True,selected_modules=[MODULE],rewards_final=False)


@pytest.mark.parametrize('outcome,started,expected', [
    ('simulated',True,'实际伤害 123,456'), ('unavailable',False,'未发出开战'),
    ('stopped',False,'已停止'), ('stopped',True,'请勿直接重开')])
def test_player_result_describes_simulation_damage_not_a_clear(outcome,started,expected):
    result = dict(outcome=outcome,phase='D',boss=2,damage=123456,
        damage_verified=outcome == 'simulated',simulation_started=started)
    report = dict(selected_modules=[MODULE],steps=[dict(result=result)])
    text = gui_contract.result_text(report)
    assert expected in text and '已通关' not in text


@pytest.mark.parametrize('error,expected', [
    ('clan_battle_calibration_missing:目标与模拟入口', '当前不可执行'),
    ('clan_battle_timing_not_calibrated', '操作耗时'),
    ('clan_battle_timing_window_unproven', '全部操作和读回'),
    ('clan_battle_previous_result_unknown', '阻止再次开战'),
    ('clan_battle_switch_missed', '不补点'),
    ('clan_battle_disconnected_6002:battle_start=unknown', '手动重连'),
])
def test_player_stops_explain_the_recovery_boundary(error, expected):
    text = gui_contract.result_text(dict(selected_modules=[MODULE], steps=[dict(result=dict(
        outcome='stopped', simulation_started=True, error=error))]))
    assert expected in text and error not in text
    assert '已发出' not in text and '保留未决记录' in text
