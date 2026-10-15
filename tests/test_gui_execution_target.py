"""Current GUI target across modules; no device discovery or game input."""
from copy import deepcopy
import pytest
from pcr_maa import supported_daily as chain, abyss, native
from pcr_maa.preset_execution import Journal
from pcr_maa.schedule_quick import gui_device
from pcr_maa.game_day import current_game_day
from test_gui_adb_schedule import config
from test_abyss import Port, reading

@pytest.mark.parametrize('name',['event','abyss','deep','very_hard','hard','normal','rewards','rewards_final'])
def test_all_gui_modules_accept_same_ordinary_adb_target(tmp_path,monkeypatch,name):
    cfg=config(tmp_path)
    result=chain.validate(cfg,execute=True,game_day='',context='gui-current-state',
        selected_modules=[name] if name!='rewards_final' else [],rewards_final=name=='rewards_final')
    assert gui_device(result)==dict(serial='emulator-5556',source='gui_adb_session_v1',app_package='com.bilibili.priconne')

@pytest.mark.parametrize('serial',['emulator-5556','127.0.0.1:5555'])
def test_real_abyss_routing_uses_gui_target_not_mumu(tmp_path,monkeypatch,serial):
    from pcr_maa import abyss_native
    cfg=config(tmp_path,serial);port=Port()
    page=reading()
    for row in page['rows']:row['remaining']=0
    port.read=lambda:page
    monkeypatch.setattr(abyss_native,'NativeAbyss',lambda *a,**k:port)
    monkeypatch.setattr(native,'ROOT',tmp_path)
    result=chain._run_step(None,'abyss',cfg,current_game_day(),'gui-current-state')
    assert result['outcome']=='skipped_goal_met' and result['home_verified']
    assert result['device']==gui_device(cfg)

@pytest.mark.parametrize('change',['new_start','new_day','new_serial','new_package'])
def test_ordinary_adb_unknown_keeps_original_ledger_and_blocks_new_start(tmp_path,change):
    cfg=config(tmp_path);device=gui_device(cfg);journal=Journal(tmp_path/'ledger')
    first=abyss.execute_abyss(Port(),journal,game_day=current_game_day(),context='gui-current-state',device=device,execute=True)
    assert first['outcome']=='unknown_settlement' and first['consumption_sent']
    before=journal.path.read_bytes();port=Port();day=current_game_day()
    if change=='new_day':day='2026-10-03'
    if change=='new_serial':device=gui_device(config(tmp_path,'emulator-5558'))
    if change=='new_package':device=device|dict(app_package='example.channel.client')
    result=abyss.execute_abyss(port,journal,game_day=day,context='gui-current-state',device=device,execute=True)
    assert result['outcome']=='blocked_in_flight' and port.calls==[]
    assert journal.path.read_bytes()==before

@pytest.mark.parametrize('context',['user-reported-unreset','same-day-unreset'])
def test_generic_target_does_not_grant_cli_or_maintenance_identity(tmp_path,context):
    port=Port();journal=Journal(tmp_path/'ledger')
    result=abyss.execute_abyss(port,journal,game_day=current_game_day(),context=context,device=gui_device(config(tmp_path)),execute=True)
    assert result['outcome']=='blocked_or_failed' and port.calls==[] and journal.events()==[]

@pytest.mark.parametrize('target_change',['serial','package','extra'])
def test_gui_target_drift_or_invalid_data_stops_before_navigation(tmp_path,target_change):
    from pcr_maa.schedule_quick import validate_gui_device
    target=gui_device(config(tmp_path))
    if target_change=='serial':target['serial']='remote.example:5555'
    elif target_change=='package':target['app_package']='invalid/package;command'
    else:target['manager']={}
    with pytest.raises(ValueError):validate_gui_device(target)


def test_settled_generic_target_new_start_reads_current_page_without_replay(tmp_path):
    device=gui_device(config(tmp_path));journal=Journal(tmp_path/'ledger')
    page=reading()
    for row in page['rows']:row['remaining']=0
    class SettledPort(Port):
        def read(self):
            self.calls.append('read')
            return page if 'submit' in self.calls else reading()
        def submit(self,q):
            self.calls.append('submit')
            return dict(consumption_dispatched=True,result_verified=True,abyss_return_verified=True,
                        result_images=[dict(kind='synthetic business result')])
    first=abyss.execute_abyss(SettledPort(),journal,game_day=current_game_day(),context='gui-current-state',device=device,execute=True)
    assert first['outcome']=='executed',first
    original=journal.path.read_bytes();port=Port();port.read=lambda:page
    result=abyss.execute_abyss(port,journal,game_day=current_game_day(),context='gui-current-state',device=device,execute=True)
    assert result['outcome']=='skipped_goal_met' and result['home_verified'],result
    assert 'submit' not in port.calls and journal.path.read_bytes()==original

@pytest.mark.parametrize('changed',[False,True])
def test_generic_target_checkpoint_recovers_only_same_target(tmp_path,changed):
    import time
    from pcr_maa import result_checkpoint as cp
    from test_result_checkpoint import consumer,frame,anchors
    from test_abyss import quotation
    device=gui_device(config(tmp_path));journal=Journal(tmp_path/'ledger');before=reading();quote=quotation()
    with journal.locked():
        journal.append(dict(state='in_flight',scope=abyss.SCOPE,device=device,game_day=current_game_day(),
            at=time.time(),quote=quote,abyss_identity=before['abyss_identity']))
        checkpoint=cp.ResultCheckpoint(journal,kind='abyss',before=before,quote=quote)
        checkpoint.capture(consumer('AbyssTicketCommit'),frame(tmp_path),anchors())
    original=journal.path.read_bytes();port=Port()
    page=reading()
    for row in page['rows']:row['remaining']=0
    port.read=lambda:page
    if changed:device=device|dict(serial='emulator-5558')
    result=abyss.execute_abyss(port,journal,game_day=current_game_day(),context='gui-current-state',device=device,execute=True)
    if changed:
        assert result['outcome']=='blocked_or_failed' and port.calls==[] and journal.path.read_bytes()==original
    else:
        assert result['outcome']=='skipped_goal_met' and result['home_verified'] and 'submit' not in port.calls
        assert journal.path.read_bytes().startswith(original) and len(journal.events())==2
