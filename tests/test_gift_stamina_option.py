"""R32 native checkbox transitions; simulated unchecked pixels are labelled as such."""
from pathlib import Path
import pytest
from pcr_maa import native,rewards,supported_daily
from pcr_maa.daily import validate_config
from pcr_maa.replay import SavedImages
W=Path(__file__).resolve().parents[2]
def frame(name):
 p=W/'private-evidence'/(name+'.png')
 if not p.is_file():pytest.skip('private gift frame required')
 return native.read_image(p)
def unchecked(image):
 image=image.copy();image[613:654,460:500]=245
 return image
@pytest.fixture
def resource(tmp_path,monkeypatch):
 monkeypatch.setattr(native,'ROOT',tmp_path);return native.load_resource()
@pytest.mark.parametrize('include,initial_checked',[(False,True),(False,False),(True,True),(True,False)])
def test_native_mode_checked_readback_single_toggle(resource,include,initial_checked):
 checked=frame('gifts-before');blank=unchecked(checked)
 initial=checked if initial_checked else blank;final=blank if include else checked
 ctrl=SavedImages([[initial],[final]]);t=native.bind(resource,ctrl)
 mode='Include' if include else 'Exclude';entry='RewardsGifts'+mode+'StaminaStart'
 r=native.run(t,entry)
 assert rewards.completed(r,'RewardsGifts'+mode+'Stamina'),r
 assert ctrl.clicks==([[480,635]] if initial_checked==include else [])
@pytest.mark.parametrize('include',[False,True])
def test_failed_toggle_never_repeated_or_claimed(resource,include):
 image=frame('gifts-before');image=image if include else unchecked(image)
 mode='Include' if include else 'Exclude';entry='RewardsGifts'+mode+'StaminaStart'
 resource.override_pipeline({'RewardsGifts'+mode+'StaminaToggle':{'timeout':500}})
 ctrl=SavedImages([[image]]);r=native.run(native.bind(resource,ctrl),entry)
 assert not rewards.completed(r,'RewardsGifts'+mode+'Stamina') and ctrl.clicks==[[480,635]]
@pytest.mark.parametrize('bad',[None,1,'true',[],{}])
def test_setting_must_be_boolean(tmp_path,bad):
 from test_gui_adb_schedule import config
 with pytest.raises(ValueError):validate_config(config(tmp_path)|{'collect_gift_stamina':bad})
 with pytest.raises(ValueError):rewards.claim_rewards(None,execute=True,collect_stamina=bad)
@pytest.mark.parametrize('step',['rewards','rewards_final'])
@pytest.mark.parametrize('setting',[None,False,True])
def test_shared_runner_forwards_old_default_and_choice(monkeypatch,step,setting):
 seen=[];monkeypatch.setattr(rewards,'claim_rewards',lambda tasker,**kw:seen.append(kw) or {})
 supported_daily._run_step(None,step,{} if setting is None else {'collect_gift_stamina':setting},'',None)
 assert seen==[{'execute':True,'collect_stamina':setting is True}]
def test_include_stamina_full_claim_once(resource):
 home=frame('gifts-start-home');checked=frame('gifts-before');blank=unchecked(checked)
 ctrl=SavedImages([[home],[checked],[blank],[frame('gifts-dispatch')],[frame('gifts-result')],[blank],[home]])
 r=rewards.claim_rewards(native.bind(resource,ctrl),execute=True,only='gifts',collect_stamina=True)
 assert r['outcome']=='complete',r.get('error')
 assert r['items']['gifts']['result_verified'] and r['home_verified']
 assert ctrl.clicks.count([480,635])==1 and ctrl.clicks.count([1080,635])==1
 assert len(ctrl.clicks)==6


def test_disabled_stamina_summary_explains_player_choice(tmp_path,monkeypatch):
 from test_reward_stamina_deferred import deferred_report
 from test_supported_daily import invoke,frozen_game_clock
 frozen_game_clock.__wrapped__(monkeypatch)
 result=deferred_report()|{'collect_stamina':False}
 report,calls=invoke(tmp_path,monkeypatch,{'rewards':result})
 assert report['supported_chain_complete'] and calls==list(supported_daily.ORDER)
 assert '已按设置保留体力礼物' in supported_daily.summary(report,player=True)
