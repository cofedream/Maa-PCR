from copy import deepcopy
from datetime import datetime
from pathlib import Path
import pytest
from pcr_maa import supported_daily as chain, schedule_quick as quick
from pcr_maa.schedule_onekey import ITEMS
from pcr_maa.preset_execution import Journal
DAY='2026-10-04'
GUI_SERIALS = [
    'emulator-5556', 'emulator-1', 'emulator-65535',
    '127.0.0.1:5555', '127.0.0.1:1', '127.0.0.1:65535',
    '192.168.1.4:5555', '192.168.0.1:1', '192.168.255.254:65535',
    '10.0.0.1:1', '10.255.255.254:65535',
    '172.16.0.1:5555', '172.31.255.254:5555',
]

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

@pytest.mark.parametrize('serial', GUI_SERIALS)
def test_new_adb_player_gui_schedule_does_not_require_mumu_identity(tmp_path,serial):
    cfg=config(tmp_path,serial)
    assert chain.validate(cfg,**args())==cfg

@pytest.mark.parametrize('serial', ['emulator-5556', '192.168.1.4:5555'])
def test_cli_still_requires_stable_identity(tmp_path, serial):
    with pytest.raises(ValueError,match='schedule_identity_source_unverified'):
        chain.validate(config(tmp_path,serial),**(args()|dict(game_day=DAY,context='user-reported-unreset')))


@pytest.mark.parametrize('serial', [
    None, '', 'remote.example:5555', '8.8.8.8:5555', '11.0.0.1:5555',
    '172.15.255.254:5555', '172.32.0.1:5555', '192.169.1.4:5555',
    '127.0.0.2:5555', '169.254.1.1:5555', '100.64.1.4:5555',
    '0.0.0.0:5555', '224.0.0.1:5555', '255.255.255.255:5555',
    '[::1]:5555', '[::ffff:192.168.1.4]:5555',
    '192.168.1.256:5555', '192.168.001.4:5555', '192.168.1:5555',
    '0xc0a80104:5555', '3232235780:5555',
    '192.168.1.4:0', '192.168.1.4:65536', '192.168.1.4:05555',
    '192.168.1.4:+5555', '192.168.1.4:5 555',
    '192.168.1.4:5555:5556', '192.168.1.4:5555;calc', '192.168.1.4\n:5555',
    'emulator-0', 'emulator-65536', 'emulator-05556',
])
def test_gui_target_rejects_non_lan_or_noncanonical_serial(serial):
    with pytest.raises(ValueError):
        quick.validate_gui_device(dict(serial=serial, source='gui_adb_session_v1',
                                       app_package='com.bilibili.priconne'))

