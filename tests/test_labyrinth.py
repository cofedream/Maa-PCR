import unittest
from dataclasses import replace
from tempfile import TemporaryDirectory
from pcr_maa.labyrinth.routes import Node,Policy,AREA_REQUIREMENTS,select_route
from pcr_maa.labyrinth.config import Settings,Goal
from pcr_maa.labyrinth.batch import Batch,Binding
from pcr_maa.labyrinth.snapshots import OpeningSnapshot
from pcr_maa.labyrinth.handoff import match_current_marker_consensus
from pcr_maa.labyrinth.strategies import Relic,choose_relic,opening_roster,next_opening_role,shop_relic
from pcr_maa.preset_execution import Journal

def map_nodes(difficulty=1,*,template=False,boss3=4,boss5=3):
    result=[]
    for area in range(1,4 if difficulty==1 else 6):
        kinds=AREA_REQUIREMENTS[area] if template else (1,6,8 if area in (3,5) else 6)
        for col,kind in enumerate(kinds,1):
            uid=area*100+col
            quest=(7703 if area==3 else 7705)*100000+(boss3 if area==3 else boss5)*100+1 if kind==8 else None
            result.append(Node(uid,area,col,1,kind,(uid+1,) if col<len(kinds) else (),quest))
    return tuple(result)

class RouteTests(unittest.TestCase):
    def test_template_and_dynamic_value_columns(self):
        self.assertEqual(len(select_route(1,map_nodes(template=True),1,Policy(mode='template')).nodes),20)
        self.assertEqual(len(select_route(1,map_nodes(),1).nodes),9)
        self.assertEqual(len(select_route(1,map_nodes(2),2).nodes),15)
    def test_boss_filters(self):
        self.assertIsNone(select_route(1,map_nodes(boss3=1),1))
        self.assertIsNotNone(select_route(1,map_nodes(boss3=1),1,Policy(area3_bosses=())))
        self.assertIsNone(select_route(1,map_nodes(2,boss5=1),2))
    def test_complete_map_and_links(self):
        nodes=map_nodes()
        for broken in (nodes[:-1],nodes[3:],nodes+(nodes[0],), (replace(nodes[0],next_ids=(999,)),)+nodes[1:], (replace(nodes[1],next_ids=(101,)),)+nodes[2:]):
            with self.assertRaises(ValueError): select_route(1,broken,1)
        with self.assertRaises(ValueError): select_route(1,nodes,1,current_block_id=101)
    def test_real_dag_prefers_reachable_value(self):
        nodes=list(map_nodes())
        nodes[0]=replace(nodes[0],next_ids=(102,112))
        nodes[1]=replace(nodes[1],block_type=2)
        nodes.append(Node(112,1,2,2,4,(103,)))
        route=select_route(1,nodes,1,Policy(allowance=0))
        self.assertIn(112,[n.block_id for n in route.nodes])
    def test_value_shortfall(self):
        nodes=list(map_nodes());nodes[1]=replace(nodes[1],block_type=2)
        nodes.append(Node(112,1,2,2,4,(103,)))
        self.assertIsNone(select_route(1,nodes,1,Policy(allowance=0)))
        self.assertIsNotNone(select_route(1,nodes,1,Policy(allowance=1)))
    def test_flags_are_not_truthy_strings(self):
        with self.assertRaises(ValueError): Policy(perfect_start='true')
        with self.assertRaises(ValueError): Policy(area3_bosses=(312505,312505))

class StrategyTests(unittest.TestCase):
    def test_opening_no_random_fallback(self):
        self.assertEqual(opening_roster(2,{'1089','1088','1801'}),('1089','1088','1801'))
        self.assertIsNone(opening_roster(2,{'1089','1088','9999'}))
        self.assertEqual(next_opening_role(2,{'1801'},{'1089','1088'}),'1801')
    def test_relic_capstone_debuff_and_unknowns(self):
        relics=[Relic('a','加速',3),Relic('b','弱体',3),Relic('c','守备',5,False)]
        self.assertEqual(choose_relic({'加速':12,'弱体':12},relics,area=1).choice.relic_id,'b')
        self.assertIsNone(choose_relic({},[Relic('u','未知',2)]))
        self.assertEqual(choose_relic({},[Relic('c','暴击',5)],area=1).phase,'baseline')
    def test_purchase_limits(self):
        item=dict(verified=True,kind='relic',currency='run_coins',price=100,purchasable=True,relic=Relic('r','守备',5))
        self.assertEqual(shop_relic({},[item],balance=100),item)
        for change in ({'currency':'jewels'},{'verified':False},{'price':True},{'kind':'role_imprint'}):
            self.assertIsNone(shop_relic({},[dict(item,**change)],balance=100))
        self.assertIsNone(shop_relic({},[item],balance=99))
        self.assertIsNone(shop_relic({},[item],balance=100,buy=False))
        self.assertIsNone(shop_relic({},[item],balance=100,area=4))
    def test_config_credentials_cannot_persist(self):
        value=dict(version=1,goals=[dict(guild_id=1,clears=1)])
        settings=Settings.parse(value)
        self.assertFalse(settings.reroll);self.assertEqual(settings.max_attempts,100);self.assertEqual(settings.max_seconds,1800)
        for change in ({'password':'secret'},{'reroll':'true'},{'refresh_shop':'true'},{'route':{'area3_bosses':'312505'}},{'version':True}):
            with self.assertRaises(ValueError): Settings.parse(dict(value,**change))

class ConfigBudgetTests(unittest.TestCase):
    def test_reroll_budget_accepts_confirmed_caps_and_smaller_values(self):
        base=dict(version=1,goals=[dict(guild_id=1,clears=1)])
        for attempts,seconds in ((100,1800),(1,1),(99,1799)):
            with self.subTest(attempts=attempts,seconds=seconds):
                settings=Settings.parse(dict(base,max_attempts=attempts,max_seconds=seconds))
                self.assertEqual((settings.max_attempts,settings.max_seconds),(attempts,seconds))
                self.assertEqual((settings.payload()['max_attempts'],settings.payload()['max_seconds']),(attempts,seconds))
                self.assertFalse(settings.reroll)
    def test_reroll_budget_rejects_above_confirmed_caps(self):
        base=dict(version=1,goals=[dict(guild_id=1,clears=1)])
        for field,values in (('max_attempts',(101,800)),('max_seconds',(1801,21600))):
            for value in values:
                with self.subTest(field=field,value=value),self.assertRaisesRegex(ValueError,'invalid_'+field):
                    Settings.parse(dict(base,**{field:value}))

def handoff_map_nodes(*args,**kwargs):
    """Synthetic map with real AACCNN ID encoding required by local matcher."""
    nodes=map_nodes(*args,**kwargs)
    ids={n.block_id:n.area*10000+n.column*100+n.row for n in nodes}
    return tuple(replace(n,block_id=ids[n.block_id],next_ids=tuple(ids[i] for i in n.next_ids)) for n in nodes)


def synthetic_native(node,raw=None):
    """Synthetic contract fixture; never a saved-frame or real-game qualification."""
    return dict(native_ok=True,tasker_inited=True,task_id=1,
                nodes=[dict(name=node,completed=True,recognition=raw or [])])


def synthetic_joined():
    rows=[]
    for name,text,box in (
        ('LabyJoinedTitle','角色加入',[530,100,120,30]),
        ('LabyJoined1075','佩可莉姆(夏日)',[420,245,200,30]),
        ('LabyJoined1351','雪菲(夏日)',[420,339,200,30]),
        ('LabyJoined1059','可可萝',[420,433,200,30])):
        word=dict(text=text,box=box,score=.99)
        rows.append(dict(name=name,algorithm='OCR',box=box,
                         detail=dict(all=[word],filtered=[word],best=word)))
    return synthetic_native('LabyOpeningJoinedObserved',rows)


def synthetic_local(nodes,frame,*,start=0):
    """Visible boxes / marker are explicit synthetic Maa facts, no route bias."""
    group=sorted((n for n in nodes if n.area==1),key=lambda n:(n.column,n.row))[start:start+2]
    columns=[dict(nodes=[dict(box=[200+250*i,200,100,100],block_type=n.block_type)])
             for i,n in enumerate(group)]
    return dict(source='maa',scope='observed_subgraph',area=1,frame=frame,columns=columns,
                positive_edges=[dict(**{'from':[0,0],'to':[1,0]})],
                current_marker=dict(column=0,node=0,box=[240,240,10,10]))


def synthetic_handoff(stamp,snapshot,creation):
    empty=stamp(source='maa',enter_id=snapshot.enter_id,native=synthetic_native('LabyOpeningEmptyObserved'))
    joined=stamp(source='maa',enter_id=snapshot.enter_id,native=synthetic_joined())
    closed=stamp(source='maa',enter_id=snapshot.enter_id,native=synthetic_native('LabyMapObserved'))
    local=synthetic_local(snapshot.nodes,stamp()['frame'])
    binding=match_current_marker_consensus([n.payload() for n in snapshot.nodes],local)
    return stamp(scope='fresh_opening_current',current_marker_binding_verified=True,
        current_binding=binding,ui_map_fingerprint_verified=False,next_target_qualified=False,
        enter_id=snapshot.enter_id,game_account_id=snapshot.account_id,
        protocol_map_sha256=snapshot.fingerprint,protocol_current_block_id=0,game_area=1,
        game_current_block_id=min((n for n in snapshot.nodes if n.area==1),key=lambda n:(n.column,n.row)).block_id,local_observation=local,
        invitation=dict(source='maa',enter_id=snapshot.enter_id,empty=empty,joined=joined,closed=closed),
        creation_operation_id=creation['operation_id'])


class Port:
    def __init__(self,*,bad=False,defeated=False,unknown_enter=False,wrong_handoff=False):
        self.binding=Binding(123,'custom-controller');self.seq=0;self.enter_id=0
        self.enters=0;self.retires=0;self.bad=bad;self.defeated=defeated;self.unknown_enter=unknown_enter;self.wrong_handoff=wrong_handoff
    def observe(self,**kw):
        self.seq+=1
        return dict(verified=True,binding=self.binding.payload(),frame=self.seq,**kw)
    def top(self): return self.observe(enter_id=0,fresh_start=True)
    def enter(self,guild,difficulty,oid):
        self.enters+=1
        if self.unknown_enter: raise TimeoutError('secret transport detail')
        self.enter_id=self.enters;self.guild=guild;self.difficulty=difficulty;self.creation_oid=oid
        return self.observe(operation_id=oid,guild_id=guild,difficulty=difficulty,enter_id=self.enter_id)
    def read_opening(self,enter_id):
        snapshot=OpeningSnapshot.read(self.binding.account_id,enter_id,
            dict(enter_id=enter_id,guild_id=self.guild,difficulty=self.difficulty),
            dict(guild_id=self.guild,block_id=0,map_list=[n.payload() for n in handoff_map_nodes(boss3=1 if self.bad else 4)]))
        return self.observe(**snapshot.payload())
    def handoff(self,route,batch):
        snapshot=OpeningSnapshot.read(self.binding.account_id,route.enter_id,
            dict(enter_id=route.enter_id,guild_id=self.guild,difficulty=self.difficulty),
            dict(guild_id=self.guild,block_id=0,map_list=[n.payload() for n in route.full_map]))
        value=synthetic_handoff(self.observe,snapshot,dict(operation_id=self.creation_oid))
        value.update(guild_id=self.guild,difficulty=self.difficulty,source='protocol_maa_handoff',
            identity_source='protocol')
        if self.wrong_handoff:value['protocol_map_sha256']='0'*64
        return value
    def retire(self,enter_id,oid):
        self.retires+=1
        return self.observe(enter_id=enter_id,operation_id=oid)
    def play(self,route,batch):
        return self.observe(enter_id=route.enter_id,guild_id=self.guild,difficulty=self.difficulty,
            outcome='defeated' if self.defeated else 'cleared',clear_verified=not self.defeated)

class BatchTests(unittest.TestCase):
    def run_batch(self,port,settings=None,**kw):
        with TemporaryDirectory() as tmp:
            journal=Journal(tmp)
            result=Batch(settings or Settings((Goal(1,2),)),port,journal,port.binding,**kw).run()
            return result,journal.events()
    def test_only_confirmed_clears_count_and_goals_loop(self):
        port=Port();result,events=self.run_batch(port,Settings((Goal(1,2),Goal(2,1))))
        self.assertTrue(result['batch_complete']);self.assertEqual(result['completed'],{1:2,2:1});self.assertEqual(port.enters,3)
        self.assertEqual(len(events),6)
    def test_defeat_stops_batch(self):
        port=Port(defeated=True);result,_=self.run_batch(port)
        self.assertEqual(result['outcome'],'defeated');self.assertEqual(result['completed'],{1:0});self.assertEqual(port.enters,1)
    def test_reroll_disabled_never_retires(self):
        port=Port(bad=True);result,_=self.run_batch(port)
        self.assertEqual(result['outcome'],'opening_not_matching');self.assertEqual(port.retires,0)
    def test_attempt_limit_keeps_last_opening(self):
        port=Port(bad=True);result,_=self.run_batch(port,Settings((Goal(1,1),),reroll=True,max_attempts=3))
        self.assertEqual(result['outcome'],'reroll_attempt_limit');self.assertEqual(port.enters,3);self.assertEqual(port.retires,2)
    def test_time_limit_no_new_input(self):
        ticks=iter((0,1800));port=Port()
        result,_=self.run_batch(port,clock=lambda:next(ticks))
        self.assertEqual(result['outcome'],'reroll_time_limit');self.assertEqual(port.enters,0)
    def test_unknown_intent_survives_and_blocks_reentry(self):
        with TemporaryDirectory() as tmp:
            port=Port(unknown_enter=True);journal=Journal(tmp);settings=Settings((Goal(1,1),))
            result=Batch(settings,port,journal,port.binding).run()
            self.assertEqual(result['outcome'],'adapter_failed');self.assertEqual(journal.events()[-1]['state'],'in_flight')
            original=journal.path.read_bytes()
            port.unknown_enter=False
            result=Batch(settings,port,journal,port.binding).run()
            self.assertEqual(result['outcome'],'blocked_in_flight');self.assertEqual(port.enters,1);self.assertEqual(journal.path.read_bytes(),original)
    def test_stop_and_handoff_mismatch(self):
        port=Port();result,_=self.run_batch(port,stopped=lambda:True)
        self.assertEqual(result['outcome'],'stopped');self.assertEqual(port.enters,0)
        result,_=self.run_batch(Port(wrong_handoff=True))
        self.assertEqual(result['outcome'],'observation_unknown');self.assertEqual(result['completed'],{1:0})

if __name__=='__main__': unittest.main()


class CurrentReceiptBatchTests(unittest.TestCase):
 run_batch=BatchTests.run_batch
 def test_batch_checks_explicit_current_only_receipt_and_bound_id(self):
  for change,expected in (({'scope':'full_handoff'},'observation_unknown'),
                          ({'next_target_qualified':True},'observation_unknown'),
                          ({'ui_map_fingerprint_verified':True},'observation_unknown'),
                          ({'current_marker_binding_verified':1},'observation_unknown'),
                          ({'current_binding':{'bound_protocol_block_id':10101}},'handoff_current_binding_unknown')):
   with self.subTest(change=change):
    class Wrong(Port):
     def handoff(self,*args):return dict(super().handoff(*args),**change)
    result,_=self.run_batch(Wrong(),Settings((Goal(1,1),)))
    self.assertEqual(result['outcome'],expected);self.assertEqual(result['completed'],{1:0})

 def test_complete_current_receipt_with_wrong_id_or_unfinished_search_stops(self):
  for field,value in (('bound_protocol_block_id',999),('search_complete',False),('next_target_qualified',True)):
   with self.subTest(field=field):
    class Wrong(Port):
     def handoff(self,*args):
      result=super().handoff(*args);result['current_binding'][field]=value;return result
    result,_=self.run_batch(Wrong(),Settings((Goal(1,1),)))
    self.assertEqual(result['outcome'],'handoff_current_binding_unknown');self.assertEqual(result['completed'],{1:0})
