"""Dynamic refresh quote (300 -> 600) and last-shop coin drain, offline synthetic states."""
from types import SimpleNamespace as NS
import pytest
from pcr_maa.labyrinth import shop_execution as shops
from pcr_maa.labyrinth import strategies
from pcr_maa.labyrinth import autoplay
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.scene import Scene
from pcr_maa.labyrinth.strategies import MARKS,Relic
from test_labyrinth_reward_shop_upstream import harness,word


def quote(text):return shops.refresh_quote(dict(words={'Refresh':[word(text)]}))

def test_refresh_quote_reads_scaled_prices_and_rejects_unnumbered_labels():
 assert quote('用300更新')==300
 assert quote('用600更新')==600
 assert quote('用1,200更新')==1200
 assert quote('刷新') is None and quote('') is None
 assert shops.refresh_quote(dict(words={'Refresh':[]})) is None
 assert shops.refresh_quote(dict(words={'Refresh':[word('用300更新'),word('用600更新')]})) is None


def test_refresh_uses_escalated_600_quote_for_intent_dialog_and_balance():
 batch,stamp=harness();shop=shops.MaaShop.__new__(shops.MaaShop);shop.stamp=stamp
 before=dict(balance=5540,refresh_allowed=True,words={'Refresh':[word('用600更新')]})
 dialog=dict(state='refresh_confirmation',words={'DialogBody':[word('要消耗阿尔法金币×600更新商店')],'Confirm':[word('确认')]})
 states=iter([(dialog,{}),(dict(state='shop',balance=4940,items=[]),{})])
 calls=[];shop._wait_state=lambda *a:next(states)
 shop._input=lambda state,*a,**k:calls.append((state,k.get('price'))) or {'native_ok':True}
 result=shop._refresh(batch,1,50701,before,1)
 assert result['refresh_verified'] is True and result['price']==600
 settled=[e for e in batch.journal.events() if e['state']=='settled' and e['operation'].startswith('shop_refresh')]
 assert [e['price'] for e in settled]==[600,600]
 assert calls==[('refresh',None),('refresh_confirmation',600)]


def test_refresh_refuses_dialog_price_that_differs_from_visible_quote():
 batch,stamp=harness();shop=shops.MaaShop.__new__(shops.MaaShop);shop.stamp=stamp
 before=dict(balance=5540,refresh_allowed=True,words={'Refresh':[word('用600更新')]})
 dialog=dict(state='refresh_confirmation',words={'DialogBody':[word('要消耗阿尔法金币×601更新商店')],'Confirm':[word('确认')]})
 calls=[];shop._wait_state=lambda *a:(dialog,{})
 shop._input=lambda state,*a,**k:calls.append(state) or {'native_ok':True}
 with pytest.raises(BatchStopped,match='shop_refresh_quote_unknown'):
  shop._refresh(batch,1,50701,before,1)
 assert calls==['refresh']


@pytest.mark.parametrize('before',[
 dict(balance=600,refresh_allowed=False,words={'Refresh':[word('用600更新')]}),
 dict(balance=500,refresh_allowed=True,words={'Refresh':[word('用600更新')]}),
 dict(balance=5540,refresh_allowed=True,words={'Refresh':[word('刷新')]})])
def test_refresh_never_spends_without_affordable_visible_quote(before):
 batch,stamp=harness();shop=shops.MaaShop.__new__(shops.MaaShop);shop.stamp=stamp
 calls=[];shop._wait_state=lambda *a:pytest.fail('no dialog for unknown quote')
 shop._input=lambda state,*a,**k:calls.append(state) or {'native_ok':True}
 with pytest.raises(BatchStopped,match='shop_refresh_quote_unknown'):
  shop._refresh(batch,1,50701,before,0)
 assert not calls and not batch.journal.events()


def test_final_shop_is_the_last_shop_node_of_the_route():
 node=lambda block_id,block_type:NS(block_id=block_id,block_type=block_type)
 shop=node(7,7);boss=node(8,8);start=node(1,2)
 assert autoplay.final_shop_in_route(NS(nodes=(start,shop,boss)),shop) is True
 assert autoplay.final_shop_in_route(NS(nodes=(start,shop,node(9,7),boss)),shop) is False
 assert autoplay.final_shop_in_route(NS(nodes=()),shop) is False


def test_final_shop_can_spend_in_area4_only_when_it_is_last():
 item=dict(verified=True,kind='relic',currency='run_coins',price=100,purchasable=True,
  box=[233,320,100,40],relic=Relic('observed','会心',1))
 assert strategies.shop_relic({},[dict(item)],balance=100,area=4,final_shop=True) == item
 assert strategies.shop_relic({},[dict(item)],balance=100,area=4) is None


@pytest.mark.parametrize('final_shop,expected_log,purchases,refreshed,balance',[
 (True,['refresh','shop','confirmation','complete'],1,True,300),
 (False,[],0,False,1000)])
def test_last_shop_keeps_refreshing_when_stock_has_nothing_purchasable(monkeypatch,final_shop,expected_log,purchases,refreshed,balance):
 batch,stamp=harness(NS(refresh_from_area=5));shop=shops.MaaShop.__new__(shops.MaaShop)
 shop.stamp=stamp;shop.blocked=False;shop.pending=None;shop.spent=set();shop.observer=NS();shop.timings=NS()
 relic=dict(kind='relic',slot=0,name='实际遗物',price=400,currency='run_coins',verified=True,
  purchasable=True,box=[233,320,100,40],current_stacks=1,relic=dict(relic_id='observed',mark='会心',bonus=2))
 current=dict(state='shop',items=[],balance=1000,refresh_allowed=True,words={'Refresh':[word('用300更新')]})
 phase=['shop'];log=[]
 def capture():
  if phase[0]=='shop':return dict(current),{}
  title='confirmation' if phase[0]=='confirmation' else 'complete'
  verb='购买' if title=='confirmation' else '购买了'
  return dict(state=title,words={'DialogBody':[word('要消耗阿尔法金币×400'+verb+'实际遗物【会心+2】')],'Confirm':[word('确认')]}),{}
 def refresh(*args):
  log.append('refresh');current['balance']-=300;current['items']=[dict(relic)]
  current['words']={'Refresh':[word('用600更新')]};current['refresh_allowed']=True
 def input_once(state,*args,**kw):
  log.append(state)
  if state=='shop':phase[0]='confirmation'
  elif state=='confirmation':phase[0]='complete';current['balance']-=400
  elif state=='complete':phase[0]='shop';current['items']=[]
  return {'native_ok':True}
 shop._capture=capture;shop._wait_state=lambda *a:capture();shop._input=input_once;shop._refresh=refresh
 shop.purchase_readback=lambda b,q,c:stamp(operation_id=c['operation_id'],enter_id=q['enter_id'],
  item_id=q['item_id'],price=q['price'],inventory_added=True,native=capture()[0],input_native=c['input_native'])
 monkeypatch.setattr(shops,'_readback',lambda value:value)
 class Runtime:
  observer=NS()
  def observe(self,**kwargs):return Scene('shop',{}, {})
  def perform(self,*a):return {}
  def wait_for(self,*a,**kw):return Scene('map',{}, {'area':5})
 shop.scene=Runtime();batch.port.buy_relic=lambda q,oid:shop.purchase_once(q,oid,batch=batch)
 actual=shop.run(batch,1,50701,stacks=dict.fromkeys(MARKS,0),area=5,refresh_shop=True,final_shop=final_shop)
 assert log==expected_log
 assert len(actual['purchases'])==purchases and actual['refresh_performed'] is refreshed
 assert current['balance']==balance
