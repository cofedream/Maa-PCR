from copy import deepcopy
from datetime import datetime
from pathlib import Path
import pytest
from pcr_maa import supported_daily as chain, schedule_quick as quick
from pcr_maa.schedule_onekey import ITEMS
from pcr_maa.preset_execution import Journal
DAY='2026-10-04'

@pytest.fixture(autouse=True)
def clock(monkeypatch):
    from pcr_maa import schedule_onekey
    monkeypatch.setattr(chain,'current_game_day',lambda *a: DAY)
    monkeypatch.setattr(schedule_onekey,'now_cn',lambda: datetime.fromisoformat(DAY+'T14:00:00+08:00'))

def config(tmp_path, serial='emulator-5556'):
    adb=tmp_path/'LDPlayer9'/'adb.exe'; adb.parent.mkdir(exist_ok=True); adb.write_text('SYNTHETIC NEVER EXECUTE')
    return dict(version=4,start='home',count_context=None,connection=dict(adb_path=str(adb),serial=serial),
        controller=dict(kind='adb',app_package='com.bilibili.priconne'),
        presets=[dict(kind=k,slot=None,label='',target_per_stage=0) for k in ('deep','very_hard','hard','normal')])

def args():
    return dict(execute=True,game_day='',context='gui-current-state',selected_modules=['schedule_onekey'],rewards_final=False,schedule_authorizations=ITEMS)

@pytest.mark.parametrize('serial',['emulator-5556','127.0.0.1:5555'])
def test_new_adb_player_gui_schedule_does_not_require_mumu_identity(tmp_path,serial):
    cfg=config(tmp_path,serial)
    assert chain.validate(cfg,**args())==cfg

def test_cli_still_requires_stable_identity(tmp_path):
    with pytest.raises(ValueError,match='schedule_identity_source_unverified'):
        chain.validate(config(tmp_path),**(args()|dict(game_day=DAY,context='user-reported-unreset')))

