"""Player-provided lag frames and native navigation, never live device input."""
from pathlib import Path
import time,pytest
from pcr_maa import native
from pcr_maa.replay import SavedImages
from pcr_maa.event_native import NativeEvent
W=Path(__file__).resolve().parents[2];R=W.parents[1]
def image(path):
 if not path.is_file():pytest.skip('private original frame required')
 return native.read_image(path)
def feedback(name):return image(W/'player-feedback/debug/on_error'/name)
def stages():
 p=R/'.runtime/pilot-readiness-20261002'
 return [image(p/'live-event-navigation-20261002/03c-close-login-receipt/after.png'),image(p/'live-event-navigation-20261002/04-event-map/after.png'),image(p/'live-event-preview-20261002/04-selection-view/after.png')]
def test_original_white_home_is_ready():
 ctrl=SavedImages([[feedback('2026.10.05-10.44.06.294_OneKeyQuickPrepare.png')]])
 r=native.run(native.bind(native.load_resource(),ctrl),'ObserveHome')
 assert r['native_ok'] and not ctrl.clicks
class Delayed(SavedImages):
 def screencap(self):
  if self.stage==0 and self.frame<12:time.sleep(.6)
  return super().screencap()
def test_event_waits_through_original_lag_then_enters_once():
 broken=feedback('2026.10.05-10.42.02.728_EventPageGate.png');home,map_,list_=stages()
 ctrl=Delayed([[broken]*12+[home],[map_],[list_]])
 t=native.bind(native.load_resource(),ctrl);r=native.run(t,'EventPageGate')
 assert r['native_ok'],r
 assert len(ctrl.clicks)==2 and not ctrl.swipes
 port=NativeEvent(t,start='event');assert port._bind_story_identity(r)
 assert port.event_identity['period'].startswith('举办时间：')
def test_permanent_bad_period_never_opens_or_consumes():
 ctrl=SavedImages([[feedback('2026.10.05-10.42.02.728_EventPageGate.png')]])
 resource=native.load_resource();resource.override_pipeline({'EventPageGate':{'timeout':500}})
 r=native.run(native.bind(resource,ctrl),'EventPageGate')
 assert not any(n['name']=='StoryEventOpenQuests' and n['completed'] for n in r['nodes'])
 assert not ctrl.clicks

@pytest.mark.parametrize('entry,source,target',[
 ('RewardsMissionsRead','missions-before','RewardsMissionsReady'),
 ('RewardsGiftsRead','gifts-before','RewardsGiftsReady'),
 ('ReturnHomeGate','gifts-start-home','ObserveHome'),
 ('OneKeyQuickPrepare','schedule-04-panel.png','OneKeyQuickUnavailable'),
])
def test_other_modules_wait_for_new_ready_frame_without_repeating_input(entry,source,target):
 import numpy as np
 good=image(W/'private-evidence'/(source+'.png')) if not source.endswith('.png') else image(W/'source/tests/fixtures'/source)
 ctrl=SavedImages([[np.zeros_like(good)]*4+[good]])
 resource=native.load_resource();resource.override_pipeline({target:{'next':[]}})
 r=native.run(native.bind(resource,ctrl),entry)
 assert any(n['name']==target and n['completed'] for n in r['nodes']),r
 assert not ctrl.clicks and not ctrl.swipes

def test_abyss_snapshot_waits_for_ready_frame():
 import numpy as np
 from pcr_maa.abyss_native import read_abyss_home
 good=image(R/'.runtime/pilot-readiness-20261002/player-followup-r15/live/05-abyss-page/after.png')
 ctrl=SavedImages([[np.zeros_like(good)]*4+[good]])
 r=read_abyss_home(native.bind(native.load_resource(),ctrl))
 assert r['abyss_page_verified'] and r['list_complete']
 assert not ctrl.clicks

def test_wait_changes_never_raise_input_budgets_or_weaken_ocr():
 import hashlib,json
 # Canonical guard fields from the frozen public R38 source, not a private R31 install.
 baseline={
  'abyss.json':'c42bda29f1ff9a9f36b2e2b3aeb62be040f6c13331ef3ef948662ba3adaba7e1',
  'activity_navigation.json':'e788006f5682592dba1e60c1f6fa5f384e45f71808ff5769f547e04a3f744018',
  'alpha_entry.json':'daa650104cb85cccc363500fd7db5b1a850262d40bd9479013bf9857fce47991',
  'event.json':'5c2fd49da67729dd1452a34595ee9c54c6a51a14e6bd50d0f15fb3acab18fc0f',
  'labyrinth.json':'ccb3ab5a528b5d84c05d6f496f47b9ed8090c43cfbe822e36d3ba4677e99d6f8',
  'presets.json':'6082ff2ad6c5d61651022e9511ae190907e30ac5d0a83baeaf3da5ec36dd9efc',
  'preset_execution.json':'10ad78382a01f774bd3162183e8fae364a83823494d5123a465e6d215467f936',
  'preset_scan.json':'3460d09b9369b26ba3262f2e30e241991a439fa2954f8a3a62afd7746514afaf',
  'return_home.json':'f879eec48947a4e6064c1ab2128405f5ee042ce0e11fd646fcdb6ddd84fac9cb',
  'rewards.json':'77e5c16bd7fffe45423c0b687aeba9d5333a64ede410f88f3b1facf198038d74',
  'schedule.json':'77fdee996b48276566f4e08504b4f7e92e38998bf1aaa56dcd48a4fc0f54f147',
  'schedule_house.json':'009e60985b4b5a0f36947ca3a54742bd8c0f38ff03c7c600eae89a37bda7a79d',
  'schedule_onekey.json':'fb967f075c3f724c37d86132d8591aa16aebae65a82d7354965c5c596e9f6d97',
  'schedule_quick.json':'995834f93c4c3278fbf3899576923de9ac69c2377a0029b8214f082f71bdf6c4',
 }
 # R50/R51 add guarded result-only branches; all existing guard fields stay frozen.
 additions={
  'abyss.json':{'AbyssResultStart','AbyssSweepSkipFinish','AbyssLevelUpDismiss'},
  'preset_execution.json':{'ExecSweepSkipButton','ExecSweepAnimationRound','ExecSweepAnimationExp',
                           'ExecSweepAnimationPanel','ExecSweepSkipGuard','ExecSweepSkipFinish',
                           'ExecLevelUpTitle','ExecLevelUpBody','ExecLevelUpButton','ExecLevelUpDismiss'},
  'rewards.json':{'RewardsMissionsAfterClose','RewardsMissionsLevelUpDismiss',
                  'RewardsGiftsAfterClose','RewardsGiftsLevelUpDismiss'},
  'schedule_onekey.json':{'OneKeyDo_mana_skip_finish','OneKeyDo_exp_skip_finish',
                         'OneKeyInput_mana_skip_finish','OneKeyInput_exp_skip_finish','OneKeySkipResultReady'},
  'schedule_quick.json':{'OneKeyQuickManaSkipFinish','OneKeyQuickExpSkipFinish','OneKeyQuickLevelUpDismiss'},
 }
 branches={'abyss.json':{name:'AbyssLevelUpDismiss' for name in (
              'AbyssResultClose','AbyssLimitedShopCancel','AbyssRecoverResultStart')},
           'preset_execution.json':{'ExecResultStart':'ExecSweepSkipFinish',
              **{name:'ExecLevelUpDismiss' for name in (
                  'ExecPostResult','ExecAfterShop','ExecAfterClan','ExecRecoverResultStart')}},
           'rewards.json':{'RewardsMissionsClose':'RewardsMissionsAfterClose',
                           'RewardsGiftsClose':'RewardsGiftsAfterClose'},
           'schedule_quick.json':{'OneKeyQuickManaConfirm':'OneKeyQuickManaSkipFinish',
                                 'OneKeyQuickExpConfirm':'OneKeyQuickExpSkipFinish',
              **{name:'OneKeyQuickLevelUpDismiss' for name in (
                  'OneKeyQuickDungeonFinish','OneKeyQuickSweepResult',
                  'OneKeyQuickLimitedShopCancel','OneKeyQuickItemResult')}}}
 fields=('max_hit','repeat','threshold','action','target','next','on_error')
 for filename,expected in baseline.items():
  pipeline=json.loads((native.RESOURCE/'pipeline'/filename).read_text(encoding='utf-8'))
  if filename=='presets.json':
   # R55 removes the EXP anchor; all remaining guard and input fields stay frozen.
   assert 'HomeReadyAnchor' not in pipeline
  # R53 moves the unchanged gift threshold into two layout-bound text branches.
  if filename=='rewards.json':
   assert pipeline['RewardsHomeGifts']==dict(recognition='Or',any_of=[
       'RewardsHomeDefaultGifts','RewardsHomeEdgeGifts'])
   for name in ('RewardsHomeGiftText','RewardsHomeEdgeGiftText'):
    assert pipeline[name]['threshold']==.85 and pipeline[name]['expected']=='^礼物$'
   pipeline['RewardsHomeGifts']['threshold']=.85
   additions[filename]|={'RewardsHomeDefaultGifts','RewardsHomeEdgeGifts',
                         'RewardsHomeGiftText','RewardsHomeEdgeGiftText'}
  added=additions.get(filename,set())
  assert added<=pipeline.keys()
  for name in added:
   if pipeline[name].get('action')=='Click':
    assert pipeline[name].get('max_hit')==(3 if name=='OneKeyQuickLevelUpDismiss' else 1),name
   if name.endswith('LevelUpDismiss'):
    assert pipeline[name]['recognition']=='And'
    assert pipeline[name]['all_of']==['ExecLevelUpButton','ExecLevelUpTitle','ExecLevelUpBody']
    assert pipeline[name]['action']=='Click' and pipeline[name]['target'] is True
    assert pipeline[name]['post_delay']>=600
  if filename=='preset_execution.json':
   for name,expected_text in (('ExecLevelUpTitle','^等级提升$'),
       ('ExecLevelUpBody','^体力回复了[！!]$'),('ExecLevelUpButton','^确认$')):
    assert pipeline[name]['recognition']=='OCR' and pipeline[name]['threshold']==.9
    assert pipeline[name]['expected']==expected_text and pipeline[name]['next']==[]
  guards={name:{field:node.get(field) for field in fields}
          | ({'timeout':60000} if node.get('timeout')==60000 else {})
          for name,node in pipeline.items()
          if isinstance(node,dict) and name!='PresetReadbackStart' and name not in added}
  for name,branch in branches.get(filename,{}).items():
   assert guards[name]['next'].count(branch)==1
   guards[name]['next']=[target for target in guards[name]['next'] if target!=branch]
   if filename=='rewards.json':
    assert guards[name]['next']==[]
    guards[name]['next']=None
  encoded=json.dumps(guards,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
  assert hashlib.sha256(encoded).hexdigest()==expected,filename
