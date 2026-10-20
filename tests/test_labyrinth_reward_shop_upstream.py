"""Pinned upstream reward/shop branches, without device or network input."""
from contextlib import nullcontext
from types import SimpleNamespace as NS
import pytest
from pcr_maa.labyrinth import rewards,shop_execution as shops
from pcr_maa.labyrinth.batch import Batch,Binding,BatchStopped
from pcr_maa.labyrinth.scene import Scene
from pcr_maa.labyrinth.strategies import MARKS,Relic,choose_relic
from test_labyrinth_autoplay import receipt as native_receipt

class Journal:
 def __init__(self):self.values=[]
 def events(self):return self.values[:]
 def append(self,value):self.values.append(value)

def harness(strategy=None):
 binding=Binding(1,'offline-shared-upstream');seq=[0]
 def stamp(**fields):seq[0]+=1;return dict(verified=True,binding=binding.payload(),frame=seq[0],**fields)
 return Batch(NS(goals=(),strategy=strategy,buy_relics=True),NS(),Journal(),binding),stamp


@pytest.mark.parametrize('source',['opening','free_role_reward','role_reward','shop_complete'])
def test_owned_acquisition_map_background_waits_for_actual_joined_without_input(monkeypatch,source):
 from pcr_maa.labyrinth import autoplay,scene
 batch,stamp=harness();clock=[0.0];reads=[];pauses=[]
 pages=iter(['map','joined'])
 def observe(**kw):
  kind=next(pages);reads.append(kind)
  return Scene(kind,native_receipt(),dict(area=2) if kind=='map' else {})
 runtime=NS(tasker=NS(),stamp=stamp,observe=observe,perform=lambda *a,**k:pytest.fail('MAP is pure waiting'))
 monkeypatch.setattr(scene,'time',NS(monotonic=lambda:clock[0]))
 def pause(*args):pauses.append(1);clock[0]+=.25
 monkeypatch.setattr(autoplay,'_poll_pause',pause)
 actual=scene.MaaSceneRuntime.wait_for(runtime,batch,{'joined','event_animation'},source=source,timeout=12,acquisition_fields=dict(source='maa',enter_id=148,reward_id='20302:role_reward:1'))
 assert actual.kind=='joined' and reads==['map','joined'] and pauses==[1]
 assert actual.payload['acquisition_driver_advanced'] is False and not batch.journal.events()


@pytest.mark.parametrize('budget',[12,30])
def test_owned_acquisition_map_keeps_original_total_deadline_and_zero_inputs(monkeypatch,budget):
 from pcr_maa.labyrinth import autoplay,scene
 batch,stamp=harness();clock=[0.0];reads=[]
 def observe(**kw):reads.append(1);return Scene('map',native_receipt(),dict(area=2))
 runtime=NS(tasker=NS(),stamp=stamp,observe=observe,perform=lambda *a,**k:pytest.fail('no MAP advancement'))
 monkeypatch.setattr(scene,'time',NS(monotonic=lambda:clock[0]))
 monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:clock.__setitem__(0,clock[0]+1))
 with pytest.raises(BatchStopped,match='^role_reward_readback_unknown$'):
  scene.MaaSceneRuntime.wait_for(runtime,batch,{'joined'},source='role_reward',timeout=budget,reason='role_reward_readback_unknown',acquisition_fields=dict(source='maa',enter_id=148))
 assert clock[0]==budget and len(reads)==budget and not batch.journal.events()


@pytest.mark.parametrize('page,owned',[('map',False),('date_changed',True),('disconnected',True),('shop',True)])
def test_acquisition_map_exception_does_not_accept_unowned_or_conflicting_pages(monkeypatch,page,owned):
 from pcr_maa.labyrinth import autoplay,scene
 batch,stamp=harness()
 runtime=NS(tasker=NS(),stamp=stamp,observe=lambda **kw:Scene(page,native_receipt(),dict(area=2)),perform=lambda *a,**k:pytest.fail('no conflict input'))
 monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:pytest.fail('conflicting page must stop'))
 reason=page if page in ('date_changed','disconnected') else 'role_reward_readback_unknown'
 with pytest.raises(BatchStopped,match='^'+reason+'$'):
  scene.MaaSceneRuntime.wait_for(runtime,batch,{'joined'},source='role_reward',timeout=12,reason='role_reward_readback_unknown',acquisition_fields=dict(source='maa',enter_id=148) if owned else None)
 assert not batch.journal.events()


def test_owned_acquisition_explicit_map_successor_remains_valid_without_input(monkeypatch):
 from pcr_maa.labyrinth import autoplay,scene
 batch,stamp=harness()
 runtime=NS(tasker=NS(),stamp=stamp,observe=lambda **kw:Scene('map',native_receipt(),dict(area=2)))
 monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:pytest.fail('explicit MAP successor should return'))
 actual=scene.MaaSceneRuntime.wait_for(runtime,batch,{'map'},source='joined',timeout=12,acquisition_fields=dict(source='maa',enter_id=99))
 assert actual.kind=='map' and actual.payload['area']==2 and actual.payload['acquisition_driver_advanced'] is False
 assert not batch.journal.events()

def word(text):return dict(text=text,box=[1,1,10,10],score=1)

@pytest.mark.parametrize('text,choice,role',[("攻击型职能的随机印记",False,'攻击者'),("治疗型职能的选择印记",True,'治疗者'),("全体选择印记",True,None),("攻击型識能的選擇印記",True,'攻击者')])
def test_exact_imprint_titles_are_real_purchase_candidates(text,choice,role):
 identity=shops.imprint_identity(text)
 assert identity['choice_imprint'] is choice and identity['role_class']==role
 words=dict(Currency=[word('持有阿尔法金币')],Balance=[word('2000')],Name0=[word(text)],Price0=[word('780')])
 balance,items=shops.parse_shop_inventory(words,{0:dict(enabled=True,box=[233,320,100,40])})
 assert balance==2000 and len(items)==1 and items[0]['kind']=='role_imprint'
 assert 'relic' not in items[0]
 assert shops.imprint_identity('未知职能的随机印记') is None

def test_printed_shop_layers_calibrate_history_and_conflict_is_not_silently_summed():
 items=[dict(kind='relic',relic=Relic('a','会心',1),current_stacks=9),dict(kind='relic',relic=Relic('b','守备',2),current_stacks=5)]
 result=shops.calibrated_stacks(dict.fromkeys(MARKS,0),items)
 assert result['会心']==9 and result['守备']==5
 items.append(dict(kind='relic',relic=Relic('c','会心',1),current_stacks=10))
 with pytest.raises(BatchStopped,match='relic_stacks_conflicting'):shops.calibrated_stacks({},items)

def test_configured_pivot_and_locked_focus_survive_no_focus_offer():
 stacks=dict(zip(MARKS,(4,4,4,10,6)))
 offers=[Relic('a','强化',1),Relic('b','弱体',1)]
 assert choose_relic(stacks,offers,area=5,locked_focus='强化').focus=='强化'
 assert choose_relic(stacks,offers,area=5,locked_focus='强化',debuff_pivot_minimum=6,debuff_pivot_tolerance=4).focus=='弱体'
 assert choose_relic(stacks,[Relic('a','加速',1)],area=5,locked_focus='强化').focus=='强化'

@pytest.mark.parametrize('remaining',[1,2])
def test_multi_choice_selection_is_pending_until_actual_joined(monkeypatch,remaining):
 batch,stamp=harness()
 observations=iter([dict(page='reward',remaining=remaining,cards=[dict(slot=i,character_id=k,name=k,trusted=True,identity_source='maa_name') for i,k in enumerate(('a','b','c'))]),dict(page='reward',remaining=1,cards=[dict(slot=i,character_id=k,name=k,trusted=True,identity_source='maa_name') for i,k in enumerate(('d','e','f'))])])
 chars=NS(namespace='chars',read=lambda *a,**k:(next(observations),dict(native_ok=True)))
 chars.probe=lambda b,page:chars.read(b,page,stable=False)[0]
 optimizer=NS(profiles={k:object() for k in 'abcdef'},choose_role=lambda *a,**k:[dict(character_id='b')])
 monkeypatch.setattr(rewards,'portrait_selection',lambda *a:nullcontext())
 monkeypatch.setattr(rewards.native,'run',lambda *a,**k:dict(native_ok=True,nodes=[dict(name='LabyRoleRewardSelectOnce',completed=True,action={'success':True})]))
 class Runtime:
  def __init__(self,*a,**k):pass
  def wait_for(self,b,allowed,**kw):
   scene=Scene('role_reward',dict(native_ok=True),{})
   assert bool(kw['acquisition_fields']) is (remaining==1)
   assert kw['accept'](scene);return scene
 monkeypatch.setattr('pcr_maa.labyrinth.scene.MaaSceneRuntime',Runtime)
 result=rewards.MaaRoleReward(object(),stamp,optimizer=optimizer,characters=chars).choose(batch,1,'batch1',(),defer_joined=True)
 assert result['roles_pending']==['b'] and result['selection_pending'] and not result['inventory_added']
 assert not any(e['result'].get('inventory_added') for e in batch.journal.events() if e['state']=='settled')

def test_refresh_quote_and_result_are_separate_new_intents_unknown_never_repeats():
 batch,stamp=harness();shop=shops.MaaShop.__new__(shops.MaaShop);shop.stamp=stamp
 before=dict(balance=900,refresh_allowed=True,words={'Refresh':[word('用300更新')]})
 states=iter([(dict(state='refresh_confirmation',words={'DialogBody':[word('要消耗阿尔法金币×300更新商店')],'Confirm':[word('确认')]}),{}),(dict(state='shop',balance=600,items=[]),{})])
 calls=[];shop._wait_state=lambda *a:next(states);shop._input=lambda state,*a,**k:calls.append(state) or dict(native_ok=True)
 result=shop._refresh(batch,1,30601,before,0)
 assert result['refresh_verified'] and calls==['refresh','refresh_confirmation']
 assert [e['operation'] for e in batch.journal.events() if e['state']=='settled']==['shop_refresh_proposal','shop_refresh_purchase']
 batch,stamp=harness();shop.stamp=stamp;calls.clear()
 shop._wait_state=lambda *a:(_ for _ in ()).throw(BatchStopped('shop_readback_unknown'))
 with pytest.raises(BatchStopped):shop._refresh(batch,1,30601,before,0)
 assert calls==['refresh'] and batch.journal.events()[-1]['state']=='settled'

def test_unknown_currency_or_wrong_refresh_price_never_confirms():
 batch,stamp=harness();shop=shops.MaaShop.__new__(shops.MaaShop);shop.stamp=stamp;calls=[]
 shop._input=lambda state,*a,**k:calls.append(state) or {}
 for body in ('消耗宝石×300更新商店','要消耗阿尔法金币×301更新商店'):
  shop._wait_state=lambda *a:(dict(state='refresh_confirmation',words={'DialogBody':[word(body)],'Confirm':[word('确认')]}),{})
  with pytest.raises(BatchStopped):shop._refresh(batch,1,30601,dict(balance=900,refresh_allowed=True,words={'Refresh':[word('用300更新')]}),0)
 assert calls==['refresh','refresh']

@pytest.mark.parametrize('choice',[False,True])
def test_imprint_actual_joined_is_read_and_shop_return_required(monkeypatch,choice):
 batch,stamp=harness();shop=shops.MaaShop.__new__(shops.MaaShop);shop.stamp=stamp;shop.tasker=object();shop.timings=NS()
 selected=dict(choice_imprint=choice,role_class=None)
 first=Scene('free_role_reward' if choice else 'joined',{},{});joined=Scene('joined',{},{});returned=Scene('shop',{},{});steps=iter([first,returned])
 class Runtime:
  def __init__(self):self.inputs=[]
  def wait_for(self,*a,**kw):return next(steps)
  def perform(self,scene,action):self.inputs.append(action);return dict(native_ok=True)
 shop.scene=Runtime();characters=NS(read=lambda *a,**kw:(dict(roles_joined=['confirmed']),dict(native_ok=True)))
 if choice:
  class Reward:
   def __init__(self,*a,**kw):pass
   def choose_free(self,*a,**kw):return dict(roles_pending=['confirmed'],following_scene=joined)
  monkeypatch.setattr(rewards,'MaaRoleReward',Reward)
 actual=shop._imprint_following(batch,1,'imprint',[],characters,selected)
 assert actual==['confirmed'] and shop.scene.inputs==['close_reward']
 assert any(e['state']=='settled' and e['operation']=='shop_imprint_close_reward' for e in batch.journal.events())
 # Merely closing a complete purchase without any independent join is not acquisition.
 shop.scene.wait_for=lambda *a,**kw:returned
 with pytest.raises(BatchStopped,match='shop_imprint_result_unknown'):shop._imprint_following(batch,1,'other',[],characters,selected)

@pytest.mark.parametrize('current_stacks,expected',[(8,10),(30,30)])
def test_shared_shop_loop_refreshes_exhausted_cycle_then_buys_fresh_relic(monkeypatch,current_stacks,expected):
 batch,stamp=harness(NS(refresh_from_area=5));shop=shops.MaaShop.__new__(shops.MaaShop)
 shop.stamp=stamp;shop.blocked=False;shop.pending=None;shop.spent=set();shop.observer=NS();shop.timings=NS()
 stock=[dict(kind='role_imprint',slot=i,item_id=str(i),name='攻击型职能的随机印记',price=780,purchasable=True,choice_imprint=False,box=[233+i*384,320,100,40]) for i in range(3)]
 current=dict(state='shop',items=stock,balance=2000,refresh_allowed=True,words={'Refresh':[word('用300更新')]});phase=['shop'];selected=[None];log=[]
 relic=dict(kind='relic',slot=0,name='实际遗物',price=400,currency='run_coins',verified=True,purchasable=True,box=[233,320,100,40],current_stacks=current_stacks,relic=dict(relic_id='observed',mark='会心',bonus=2))
 def capture():
  if phase[0]=='shop':return dict(current),{}
  title='confirmation' if phase[0]=='confirmation' else 'complete'
  verb='购买' if title=='confirmation' else '购买了'
  return dict(state=title,words={'DialogBody':[word('要消耗阿尔法金币×400'+verb+'实际遗物【会心+2】')],'Confirm':[word('确认')]}),{}
 def refresh(*args):
  log.append('refresh');current.update(items=[relic],balance=1700,refresh_allowed=False)
 def input_once(state,*args,**kw):
  log.append(state)
  if state=='shop':phase[0]='confirmation'
  elif state=='confirmation':phase[0]='complete';current['balance']-=400
  elif state=='complete':phase[0]='shop';current['items']=[]
  return {}
 shop._capture=capture;shop._wait_state=lambda *a:capture();shop._input=input_once;shop._refresh=refresh
 # Preserve the business state receipt rather than manufacturing Maa structure in this test.
 shop.purchase_readback=lambda b,q,c:stamp(operation_id=c['operation_id'],enter_id=q['enter_id'],item_id=q['item_id'],price=q['price'],inventory_added=True,native=capture()[0],input_native=c['input_native'])
 monkeypatch.setattr(shops,'_readback',lambda value:value)
 class Runtime:
  observer=NS()
  def observe(self,**kwargs):return Scene('shop',{}, {})
  def perform(self,*a):return {}
  def wait_for(self,*a,**kw):return Scene('map',{}, {'area':5})
 shop.scene=Runtime();batch.port.buy_relic=lambda q,oid:shop.purchase_once(q,oid,batch=batch)
 actual=shop.run(batch,1,50601,stacks=dict.fromkeys(MARKS,0),area=5,refresh_shop=True)
 assert log==['refresh','shop','confirmation','complete']
 assert actual['refresh_performed'] and actual['stacks']['会心']==expected and len(actual['purchases'])==1
 assert current['balance']==1300 and actual['focus']==('会心' if current_stacks<15 else '加速')
 assert actual['calibrated_marks']==['会心']

@pytest.mark.parametrize('page',['battle','boss'])
@pytest.mark.parametrize('notice',[True,False])
def test_effective_empty_list_needs_real_game_notice(page,notice,monkeypatch):
 import json,numpy as np
 from pcr_maa.labyrinth import character_recognition as chars
 base=dict(page=page,filter='EFFECTIVE_EFFECT',cards=[],members=[],selected=[],boss_tab=1,stable=False)
 recognizer=NS(stopped=lambda:False,recognize=lambda *a,**k:dict(base))
 callback=chars.CharacterRecognition('test',recognizer)
 def recognition(name,image):
  if name in ('LabyDateChangedObserved','LabyDisconnect6002Observed'):return NS(hit=False)
  if 'Filter' in name and not name.endswith('EFFECTIVE_EFFECT'):return NS(hit=False)
  if name.endswith('EffectiveEmptyNotice') and not notice:return NS(hit=False)
  text='未搜索到该角色' if name.endswith('EffectiveEmptyNotice') else '有效效果'
  value=dict(text=text,box=[534,351,198,25],score=.95)
  return NS(hit=True,raw_detail=dict(filtered=[value],best=value))
 expected_box=chars._filter_rects(page)[-1]
 monkeypatch.setattr(chars,'_filter_blue',lambda frame,box:1 if tuple(box)==expected_box else 0)
 result=callback.analyze(NS(tasker=NS(stopping=False),run_recognition=recognition),NS(image=np.zeros((720,1280,3),np.uint8),custom_recognition_param=json.dumps(dict(mode='read',page=page))))
 if notice:
  assert result.detail['empty_effective_verified'] and result.detail['stable'] and not result.detail['cards']
  assert chars.CharacterNavigation.trusted_content(result.detail)
 else:assert result is None

def test_event_run_reads_configured_win_rates_without_touching_unknown_retry(monkeypatch):
 from pcr_maa.labyrinth import event_execution as events
 from pcr_maa.labyrinth.events import Choice
 batch,stamp=harness(NS(normal_win_rate=42,extreme_win_rate=13));adapter=events.MaaEvent.__new__(events.MaaEvent)
 adapter.stamp=stamp;adapter.blocked=False;adapter.attempted=set();adapter.observer=NS();adapter.timings=NS(measure=lambda _:nullcontext())
 current=dict(event_id=1105,trusted=True,choices=[dict(choice_id=11051,slot=0,box=[1,1,20,20],enabled_confidence=1)])
 adapter.capture=lambda:(current,{})
 adapter.tasker=NS(resource=NS(override_pipeline=lambda _:True))
 adapter.scene=NS(observer=NS(),wait_for=lambda *a,**k:Scene('map',{},dict(area=1)))
 rates=[]
 class Planner:
  def __init__(self,opt,**kw):rates.append(kw)
  def decide(self,event,choices,*a,**k):return NS(choice=choices[0],action_safe=True)
 monkeypatch.setattr(events,'configured_optimizer',lambda _:object());monkeypatch.setattr(events,'Planner',Planner)
 monkeypatch.setattr(events.native,'run',lambda *a,**k:dict(native_ok=True,nodes=[dict(name='LabyEventSelectOnce',completed=True,action=dict(success=True))]))
 result=adapter.run(batch,1,20303,stacks={})
 assert rates[0]['normal_win_rate']==42 and rates[0]['extreme_win_rate']==13 and rates[0]['baseline_target']==4 and result['page']=='map'
 assert len(batch.journal.events())==2

@pytest.mark.parametrize('policy',[dict(baseline_target=7),dict(area=4,baseline_last_area=3),dict(debuff_pivot_minimum=6,debuff_pivot_tolerance=4),dict(priority=('弱体',)),dict(locked_focus='弱体')])
def test_all_event_relic_effects_use_same_configured_focus_and_phase(policy):
 from pcr_maa.labyrinth.events import Planner
 stacks=dict(zip(MARKS,(2,4,4,10,6))) if 'area' in policy else dict(zip(MARKS,(4,4,4,10,6)));base=Planner(NS())
 changed=Planner(NS(),**policy)
 assert changed.relic_utility('弱体',1,stacks)!=base.relic_utility('弱体',1,stacks) or changed.relic_utility('加速',1,stacks)!=base.relic_utility('加速',1,stacks)
 assert changed.coins(1000,stacks)==1000/1800*max(2.5,max(changed.relic_utility(m,1,stacks) for m in MARKS))
 assert changed.random_relic(1,stacks)==sum(changed.relic_utility(m,1,stacks) for m in MARKS)/len(MARKS)


def event_retry_harness(monkeypatch,following='map'):
 from pcr_maa.labyrinth import event_execution as events
 batch,stamp=harness();adapter=events.MaaEvent.__new__(events.MaaEvent)
 adapter.stamp=stamp;adapter.blocked=False;adapter.attempted=set();adapter.observer=NS();adapter.timings=NS(measure=lambda _:nullcontext())
 current=dict(event_id=1314,trusted=True,choices=[dict(choice_id=11111,slot=0,box=[1,1,20,20],enabled_confidence=1),dict(choice_id=11082,slot=1,box=[1,40,20,20],enabled_confidence=1)])
 captured=[];adapter.capture=lambda:captured.append(1) or (dict(current),{})
 selected=[];adapter.tasker=NS(resource=NS(override_pipeline=lambda nodes:selected.append(nodes['LabyEventSelectGuard']['custom_recognition_param']['slot']) or True))
 clock=[0];monkeypatch.setattr(events,'time',NS(monotonic=lambda:clock[0]))
 monkeypatch.setattr(events,'configured_optimizer',lambda _:NS(profiles={}))
 monkeypatch.setattr(events.native,'run',lambda *a,**k:dict(native_ok=True,nodes=[dict(name='LabyEventSelectOnce',completed=True,action=dict(success=True))]))
 same=Scene('event',{},dict(event=dict(event_id=1314,trusted=True)))
 waits=[]
 def wait_for(b,allowed,**kw):
  waits.append(kw)
  assert kw['acquisition_fields']['event_id']==1314
  if len(waits)==1:
   clock[0]=5.9;assert not kw['accept'](same)
   clock[0]=6;assert kw['accept'](same)
   if following=='unknown':raise BatchStopped('event_result_unknown')
   if following=='stop':batch.stopped=lambda:True;batch.checkpoint()
   if following=='wrong_identity':current['event_id']=9999
   return same
  assert kw['timeout']<=24
  return Scene('map',{},dict(area=1))
 adapter.scene=NS(observer=NS(),wait_for=wait_for)
 return adapter,batch,current,selected,captured,waits


def test_fixed_upstream_event_timeout_retires_once_then_new_transaction(monkeypatch):
 adapter,batch,current,selected,captured,waits=event_retry_harness(monkeypatch)
 result=adapter.run(batch,1,20303,stacks={})
 assert result['page']=='map' and result['choices_attempted']==[11082,11111]
 assert selected==[0,1] and len(captured)==4
 assert result['event_rejections'][0]['rejection_basis']=='fixed_upstream_commit_timeout'
 settled=[e for e in batch.journal.events() if e['state']=='settled']
 assert [e['choice_id'] for e in settled]==[11111,11082]
 assert len({e['operation_id'] for e in settled})==2 and not adapter.blocked


@pytest.mark.parametrize('following',['unknown','stop','wrong_identity'])
def test_fixed_upstream_event_replacement_requires_same_identity_and_no_stop(monkeypatch,following):
 adapter,batch,current,selected,captured,waits=event_retry_harness(monkeypatch,following)
 with pytest.raises(BatchStopped):adapter.run(batch,1,20303,stacks={})
 assert selected==[0] and len(waits)==1 and adapter.blocked
 assert len(captured)==(4 if following=='wrong_identity' else 2)


def test_fixed_upstream_event_all_choices_retired_never_resends(monkeypatch):
 adapter,batch,current,selected,captured,waits=event_retry_harness(monkeypatch)
 from pcr_maa.labyrinth import event_execution as events
 clock=[0];monkeypatch.setattr(events,'time',NS(monotonic=lambda:clock[0]))
 same=Scene('event',{},dict(event=dict(event_id=1314,trusted=True)))
 def wait_for(b,allowed,**kw):
  clock[0]+=6;assert kw['accept'](same);return same
 adapter.scene.wait_for=wait_for
 with pytest.raises(BatchStopped,match='event_choices_exhausted'):adapter.run(batch,1,20303,stacks={})
 assert selected==[0,1] and len(captured)==6 and len([e for e in batch.journal.events() if e['state']=='settled'])==2


@pytest.mark.parametrize('count',[1,2])
def test_free_role_invite_arms_shared_acquisition_only_after_ack(monkeypatch,count):
 from test_labyrinth_event_free_roles import Characters
 batch,stamp=harness();characters=Characters(count)
 profiles={str(i):NS(rating=float(i)) for i in range(4)}
 joined=Scene('joined',{},{});calls=[]
 class Runtime:
  def __init__(self,*a,**kw):pass
  def wait_for(self,b,allowed,**kw):
   assert b.journal.events()[-1]['state']=='settled' and b.journal.events()[-1]['operation']=='event_free_role_invite'
   assert kw['acquisition_fields']==dict(source='maa',enter_id=1,reward_id='free-batch') and kw['timeout']==12
   calls.append(kw);return joined
 monkeypatch.setattr('pcr_maa.labyrinth.scene.MaaSceneRuntime',Runtime)
 result=rewards.MaaRoleReward(NS(),stamp,characters=characters,optimizer=NS(profiles=profiles)).choose_free(batch,1,'free-batch',('0',),defer_joined=True)
 assert result['roles_pending']==sorted(str(i) for i in range(4-count,4)) and not result['inventory_added'] and result['following_scene'] is joined
 assert len(calls)==1 and len(characters.calls)==count+1


def test_effective_empty_notice_accepts_the_pinned_game_full_stop_only():
 import re
 from pcr_maa.labyrinth.character_recognition import character_pipeline
 notice=character_pipeline('test')['testEffectiveEmptyNotice']
 assert notice['threshold']==.8 and notice['roi']==[525,250,260,230]
 assert all(re.fullmatch(notice['expected'],text) for text in ('未搜索到该角色','未搜索到该角色。'))
 assert all(not re.fullmatch(notice['expected'],text) for text in ('搜索到角色','未搜索到该角色？','有效效果不存在'))


def relic_name_receipt(path):
 from pcr_maa.labyrinth import relics
 rows=[]
 for name in relics.relic_pipeline()['LabyRelicObserved']['all_of']:
  if name.endswith(('Title','Subtitle','Remaining')):
   label=name.removeprefix('LabyRelic')
   text,roi={'Title':('获得道具的机会!',relics.TITLE_ROI),'Subtitle':('请选择要获得的迷宫遗物。',relics.SUBTITLE_ROI),'Remaining':('还剩1次',relics.REMAINING_ROI)}[label]
   detail=dict(filtered=[dict(text=text,box=list(roi),score=.99)])
  else:
   slot=int(name[-1]);kind=name[len('LabyRelic'):-1]
   if kind=='Bright':detail=dict(filtered=[dict(box=list(relics.BRIGHT_ROIS[slot]),count=800)])
   else:
    text,roi={'Name':(('强化宝石','绝杀战戟','霹雳斩赤刃')[slot],relics.NAME_ROIS[slot]),'Attribute':('强化+1 当前0',relics.ATTRIBUTE_ROIS[slot]),'Button':('选择',relics.BUTTON_ROIS[slot])}[kind]
    detail=dict(filtered=[dict(text=text,box=list(roi),score=.99)])
    if kind=='Name':
     hit=lambda suffix:dict(algorithm='OCR',name=name+suffix,box=list(roi),detail=detail)
     miss=lambda suffix:dict(algorithm='OCR',name=name+suffix,box=None,detail={})
     variants={'detected':[hit('Detected')],'line_fallback':[miss('Detected'),hit('Line')],'reversed':[miss('Line'),hit('Detected')],'prior_hit':[hit('Detected'),hit('Line')]}[path]
     detail=dict(filtered=[dict(detail=variants)])
  rows.append(dict(name=name,detail=detail))
 return dict(native_ok=True,nodes=[dict(name='LabyRelicObserved',completed=True,recognition=rows)])


@pytest.mark.parametrize('path',['detected','line_fallback','reversed','prior_hit'])
def test_relic_name_or_receipt_follows_detection_first_short_circuit(path):
 from pcr_maa.labyrinth import relics
 value=relic_name_receipt(path)
 if path in ('reversed','prior_hit'):
  with pytest.raises(BatchStopped,match='^relic_offers_unknown$'):relics.relic_offers(value)
 else:
  offers=relics.relic_offers(value)
  assert [offer.name for offer in offers]==['强化宝石','绝杀战戟','霹雳斩赤刃']
  for nodes,prefix in ((relics.relic_pipeline(),'LabyRelicName1'),(relics.selection_override(offers,1),'LabyRelicGuardName10')):
   assert nodes[prefix+'Native']['any_of']==[prefix+'Detected',prefix+'Line']
   assert nodes[prefix+'Detected']['only_rec'] is False and nodes[prefix+'Line']['only_rec'] is True
   assert all(nodes[prefix+suffix]['threshold']==.85 and nodes[prefix+suffix]['roi']==list(relics.NAME_ROIS[1]) for suffix in ('Detected','Line'))


@pytest.mark.parametrize('hit',[True,False])
def test_relic_name_read_uses_current_isolated_native_or_without_icon_state(hit):
 from pcr_maa.labyrinth import relics
 import json,numpy as np
 calls=[];raw=[dict(algorithm='OCR',name='LabyRelicName1Detected',box=None,detail={}),dict(algorithm='OCR',name='LabyRelicName1Line',box=[573,385,134,27],detail=dict(filtered=[dict(text='影刃剑幽影',box=[573,385,134,27],score=.96)]))]
 def read(node,image):
  calls.append(node);return NS(hit=hit,box=[573,385,134,27],raw_detail=raw)
 capture=relics.RelicIconCapture()
 result=capture.analyze(NS(run_recognition=read),NS(image=np.zeros((720,1280,3),np.uint8),custom_recognition_param=json.dumps(dict(mode='name',node='LabyRelicName1Native'))))
 assert calls==['LabyRelicName1Native']
 assert (result.box is not None) is hit
 assert result.detail==raw and capture.icon is None


def test_actual128_relic_offer_and_fresh_name_guard_agree_zero_input(tmp_path,monkeypatch):
 from pathlib import Path
 import socket
 from pcr_maa import native
 from pcr_maa.labyrinth import relics
 from pcr_maa.replay import SavedImages
 from maa import controller as controller_module
 from maa.toolkit import Toolkit
 screen=Path(__file__).resolve().parents[1]/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/324309800cfe4465b79061c9968ec3c3/acceptance-ending/screen.png'
 if not screen.is_file():pytest.skip('private run128 frame excluded from public source')
 def deny(*a,**kw):pytest.fail('offline saved frame prohibits network/device')
 monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket,'create_connection',deny)
 monkeypatch.setattr(controller_module,'AdbController',deny);monkeypatch.setattr(Toolkit,'find_adb_devices',deny)
 monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
 controller=SavedImages([[native.read_image(screen)]]);resource=native.load_resource();assert resource.override_pipeline(relics.relic_pipeline());tasker=native.bind(resource,controller)
 relics.MaaRelicReward(tasker,lambda **kw:kw)
 try:
  offers=relics.relic_offers(native.run(tasker,'LabyRelicObserved'))
  assert [o.name for o in offers]==['强化宝石','影刃剑幽影','霹雳斩赤刃']
  with relics.selection_guard(tasker,offers,1,icon=True):
   assert resource.override_pipeline({'LabyRelicSelectOnce':dict(action='DoNothing')})
   actual=native.run(tasker,'LabyRelicSelectOnce')
  assert actual['native_ok'] and not controller.clicks and not controller.swipes
 finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded
