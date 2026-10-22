"""Formal choices use the original Owner boundary and remain disabled in production."""
from copy import deepcopy
from contextlib import contextmanager
import json
from pathlib import Path
import time
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from pcr_maa import gui_contract, supported_daily
from pcr_maa.agent_entry import parse_selection
from test_clan_battle_batch import daily_request

MODULE = 'clan_battle_daily'


def test_formal_pi_slot_is_data_only():
    def node(name):
        return dict(enabled=False, action=dict(type='DoNothing', param={}), next=[], on_error=[],
                    attach=dict(selected=name == 'PCRAlphaSelectClanBattleDaily'))
    assert parse_selection(json.dumps(dict(selection_mode='pi_nodes_v1', selected_modules=[MODULE])),
                           context=NS(get_node_data=node)) == gui_contract.make_combination([MODULE])


def test_old_bundle_without_formal_slot_remains_compatible():
    def node(name):
        if name == 'PCRAlphaSelectClanBattleDaily':
            return None
        return dict(enabled=False, action=dict(type='DoNothing', param={}), next=[], on_error=[],
                    attach=dict(selected=name == 'PCRAlphaSelectHard'))
    assert parse_selection(json.dumps(dict(selection_mode='pi_nodes_v1')),
                           context=NS(get_node_data=node)) == gui_contract.make_combination(['hard'])


@pytest.mark.parametrize('optional', [[], ['guide_trial'], ['clan_battle_simulation'],
    [MODULE], ['guide_trial','clan_battle_simulation',MODULE]])
def test_legacy_flags_optional_slots(optional):
    flags = dict.fromkeys(['schedule_onekey','event','abyss','deep','rewards','very_hard','hard','normal',*optional], False)
    flags['hard'] = True
    assert parse_selection(json.dumps(dict(version=1,module_flags=flags,rewards_final=False))) == gui_contract.make_combination(['hard'])


@pytest.mark.parametrize('other', ['event','guide_trial','clan_battle_simulation',MODULE])
def test_formal_must_run_alone(other):
    with pytest.raises(ValueError):
        gui_contract.make_combination([MODULE,other])


def test_formal_rejects_final_rewards_and_cli_dispatch():
    with pytest.raises(ValueError):
        gui_contract.make_combination([MODULE],rewards_final=True)
    with pytest.raises(ValueError,match='clan_battle_daily_requires_alpha_owner_entry'):
        supported_daily.execute_supported(None,{},game_day='',context='',directory=None,
            execute=True,selected_modules=[MODULE],rewards_final=False)


@pytest.mark.parametrize('field', ['execute','calibration_path','validated','grant','game_day'])
def test_selection_cannot_enable_production_or_inject_authority(field):
    with pytest.raises(ValueError):
        gui_contract.validate_combination(gui_contract.make_combination([MODULE]) | {field:True})


def test_formal_missing_adapter_message_does_not_claim_three_remaining():
    report=dict(selected_modules=[MODULE],steps=[dict(result=dict(outcome='stopped',
        error='clan_battle_daily_calibration_missing:production_daily_adapter_unavailable'))])
    text=gui_contract.result_text(report)
    assert '当前不可执行' in text and '实际剩余次数与尾刀库存仍未知' in text
    assert '未发出正式或补偿开战' in text and '已用完' not in text


@pytest.mark.parametrize('count,remaining',[(2,0),(3,None),(3,1)])
def test_formal_reminder_requires_three_settlements_and_actual_zero(count,remaining):
    report=dict(selected_modules=[MODULE],steps=[dict(result=dict(outcome='remind_carryover',
        regular_settled=count,remaining_count=remaining,held_carryovers=[dict(slot=1,stock_index=4)]))])
    text=gui_contract.result_text(report)
    assert '3 次主刀均已结算' not in text and '已停止' in text


def test_formal_batch_tail_reminder_reaches_player_summary(daily_request,tmp_path):
    from test_clan_battle_batch import Port
    from pcr_maa.clan_battle_batch import execute_batch
    from pcr_maa.preset_execution import Journal
    port=Port(daily_request);port.issue={1};port.refresh=False
    result=execute_batch(port,Journal(tmp_path/'ledger'),daily_request,now=port.now(),explicit_start=True)
    assert result['outcome']=='remind_carryover' and result['remaining_count']==0
    text=gui_contract.result_text(dict(selected_modules=[MODULE],steps=[dict(result=result)]))
    assert '3 次主刀均已结算' in text and '1 份尾刀未出' in text and '已停止' not in text


def test_formal_batch_all_unavailable_is_finite_and_keeps_slots(daily_request,tmp_path):
    from test_clan_battle_batch import Port
    from pcr_maa.clan_battle_batch import execute_batch
    from pcr_maa.preset_execution import Journal
    port=Port(daily_request);port.available={1:False,2:False,3:False}
    result=execute_batch(port,Journal(tmp_path/'ledger'),daily_request,now=port.now(),explicit_start=True)
    assert result['outcome']=='no_available_target' and result['attempts']==[]
    text=gui_contract.result_text(dict(selected_modules=[MODULE],steps=[dict(result=result)]))
    assert '第 1、2、3 刀目标已保留' in text and '本轮没有可执行目标' in text


@pytest.mark.parametrize('verified',[False,True])
def test_known_simulation_damage_and_return_are_separate(verified):
    report=dict(selected_modules=['clan_battle_simulation'],steps=[dict(result=dict(
        outcome='simulated',phase='D',boss=2,damage=123456,damage_verified=True,
        return_verified=verified,error=None if verified else 'clan_battle_return_unverified'))])
    text=gui_contract.result_text(report)
    assert '实际伤害 123,456' in text and '未正式出刀' in text
    assert ('已返回会战目标页' if verified else '返回会战目标页尚未核实') in text


def test_bundled_pi_and_interface_default_formal_disabled():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    pipeline=json.loads((root/'assets/resource/pipeline/alpha_entry.json').read_text(encoding='utf-8'))
    node=pipeline['PCRAlphaSelectClanBattleDaily']
    assert node['enabled'] is False and node['action']=='DoNothing' and node['attach']=={'selected':False}
    interface=json.loads((root/'interface.json').read_text(encoding='utf-8'))
    task=next(t for t in interface['task'] if 'PCRAlphaSelectClanBattleDaily' in t.get('pipeline_override',{}))
    assert task['pipeline_override']['PCRAlphaSelectClanBattleDaily']=={'attach':{'selected':False}}


@pytest.fixture
def formal_flow(daily_request,tmp_path,monkeypatch):
    from pcr_maa import agent_entry as entry,native
    def forbidden(*args,**kwargs):
        pytest.fail('formal offline entry attempted external I/O')
    monkeypatch.setattr('subprocess.Popen',forbidden)
    monkeypatch.setattr('socket.create_connection',forbidden)
    monkeypatch.setattr('requests.sessions.Session.request',forbidden)
    adb=tmp_path/'synthetic'/'adb.exe';adb.parent.mkdir();adb.write_bytes(b'SYNTHETIC; NEVER EXECUTE')
    controller=dict(kind='adb',app_package='com.bilibili.priconne')
    config=dict(version=4,start='home',count_context=None,connection=dict(adb_path=str(adb),serial='emulator-5556'),
        controller=controller,presets=[dict(kind=k,slot=None,label='',target_per_stage=0) for k in ('deep','very_hard','hard','normal')],
        clan_battle_daily=deepcopy(daily_request))
    path=tmp_path/'daily-config.json';path.write_text(json.dumps(config),encoding='utf-8')
    monkeypatch.setattr(entry,'CONFIG_PATH',path);monkeypatch.setattr(entry,'LOG_ROOT',tmp_path/'entries')
    monkeypatch.setattr(supported_daily,'CONTROL_ROOT',tmp_path/'reports');monkeypatch.setattr(native,'ROOT',tmp_path)
    old=tmp_path/'old-guide.jsonl';old.write_bytes(b'SYNTHETIC OLD UNKNOWN\n');old_bytes=old.read_bytes()
    owner=NS(inited=True,stopping=False,controller=NS(uuid='synthetic-controller'))
    context=NS(tasker=owner,get_task_job=lambda:NS(job_id=1));events=[]
    @contextmanager
    def scope(actual,**kwargs):
        assert actual is context
        events.append('scope_enter')
        try:yield owner
        finally:events.append('scope_exit')
    def invoke(deny=False):
        selection=gui_contract.make_combination([MODULE])
        def authorize(actual,selected):
            assert actual is context and selected==selection
            if deny:raise ValueError('owner_denied')
            return entry.ExecutionContext(invocation_id=uuid4().hex,root_task_id=1,controller_uuid=owner.controller.uuid,
                serial='emulator-5556',combination=selection,execute=True,game_day='',count_context='gui-current-state',
                deadline=time.monotonic()+30,controller=controller)
        action=entry.AlphaEntry(NS(scope=scope),execution_context=authorize)
        ok=action.run(context,NS(custom_action_name=entry.ACTION,task_detail=NS(task_id=1),custom_action_param=json.dumps(selection)))
        assert old.read_bytes()==old_bytes
        assert not (tmp_path/'.runtime/clan-battle-daily-work').exists()
        assert not (tmp_path/'.runtime/clan-battle-simulation-work').exists()
        result=action.last_result
        report=json.loads(Path(result['report_path']).read_text(encoding='utf-8')) if result['report_path'] else None
        return ok,result,report
    return NS(config=config,path=path,owner=owner,invoke=invoke,events=events)


def test_formal_owner_real_entry_stops_before_intent_without_adapter(formal_flow):
    ok,result,report=formal_flow.invoke()
    assert ok is False and result['outcome']==report['outcome']=='stopped'
    assert formal_flow.events==['scope_enter','scope_exit']
    assert report['selected_modules']==[MODULE]
    value=report['steps'][0]['result']
    assert value['error']=='clan_battle_daily_calibration_missing:production_daily_adapter_unavailable'
    assert value['consumption_sent'] is False and value['intents_created']==0 and value['daily_execution_available'] is False
    assert '实际剩余次数与尾刀库存仍未知' in result['summary']


@pytest.mark.parametrize('kind',['denied','stopped','changed_request'])
def test_formal_owner_authorization_and_request_fail_before_scope(formal_flow,kind):
    if kind=='stopped':formal_flow.owner.stopping=True
    if kind=='changed_request':
        formal_flow.config[MODULE]['confirmed_for']='0'*64
        formal_flow.path.write_text(json.dumps(formal_flow.config),encoding='utf-8')
    ok,result,report=formal_flow.invoke(deny=kind=='denied')
    assert ok is False and report is None and formal_flow.events==[]


@pytest.mark.parametrize('change',['simulation','trial','package','authority'])
def test_formal_v4_config_rejects_mixed_modes_and_extra_authority(formal_flow,change):
    from pcr_maa.daily import validate_config
    config=deepcopy(formal_flow.config)
    if change=='simulation':config['clan_battle_simulation']=config[MODULE]['slot_plans']['1']
    elif change=='trial':config['guide_trial']={}
    elif change=='package':config['controller']['app_package']='com.example.other'
    else:config['execute']=True
    with pytest.raises(ValueError):validate_config(config)
