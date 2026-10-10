"""Synthetic controller contract tests; these never qualify a real game clear."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from pcr_maa.labyrinth.autoplay import AutoplayRunner,bind_route_target
from pcr_maa.labyrinth.batch import Batch,BatchStopped,Binding,GameRunSession
from pcr_maa.labyrinth.config import Settings,Goal
from pcr_maa.labyrinth.routes import Node,Route
from pcr_maa.labyrinth.scene import Scene
from pcr_maa.preset_execution import Journal

def receipt(n=1,action=False):
    return dict(native_ok=True,tasker_inited=True,task_id=n,nodes=[dict(name='offline',
        completed=True,action=dict(name='Click' if action else 'DoNothing',success=True))])

def route_for(types,areas=None):
    areas=areas or [1]*len(types)
    nodes=[]
    columns={}
    for i,(kind,area) in enumerate(zip(types,areas)):
        columns[area]=columns.get(area,0)+1
        col=columns[area];uid=area*10000+col*100+1
        nextarea=areas[i+1] if i+1<len(types) else None
        nxt=(nextarea*10000+(columns.get(nextarea,0)+(1 if nextarea==area else 1))*100+1
             if nextarea is not None else None)
        # Recompute links after constructing the actual sequence.
        nodes.append(Node(uid,area,col,1,kind,()))
    nodes=[replace(n,next_ids=(nodes[i+1].block_id,) if i+1<len(nodes) else ()) for i,n in enumerate(nodes)]
    return Route(99,tuple(nodes),tuple(nodes),'protocol')

class Runtime:
    def __init__(self,pages):
        self.pages=pages;self.i=0;self.inputs=[];self.reads=0
    def observe(self,timeout=10):
        self.reads+=1
        kind,payload=self.pages[self.i]
        return Scene(kind,receipt(self.reads),dict(frame_pixel_sha256=str(self.i),**payload))
    def wait_for(self,*args,**kwargs):
        from pcr_maa.labyrinth.scene import MaaSceneRuntime
        return MaaSceneRuntime.wait_for(self,*args,**kwargs)
    def advance(self):
        self.i=min(self.i+1,len(self.pages)-1)
    def perform(self,scene,action):
        self.inputs.append(action);self.advance()
        return receipt(self.reads+100,True)

class Navigator:
    def __init__(self,runtime):self.runtime=runtime;self.previews=[]
    def locate(self,route,current):return dict(status='target_observed'),receipt(55)
    def preview(self,route,current):
        self.previews.append(current.block_id);self.runtime.advance()
        return receipt(56,True)
    def scroll(self,scene):raise AssertionError('unexpected scroll')

class Actions:
    def __init__(self,runtime,batch,stamp):
        self.runtime,self.batch,self.stamp=runtime,batch,stamp;self.calls=[];self.owned=[]
    def prepare_team(self,route,node,boss=False):self.calls.append(('team',node.block_type,boss))
    def start_battle(self,route,node):
        def send(oid):
            self.runtime.perform(self.runtime.observe(),'start_battle')
            scene=self.runtime.observe()
            if scene.kind not in ('battle_victory','battle_defeat'):raise BatchStopped('battle_result_unknown')
            return self.stamp(operation_id=oid,enter_id=route.enter_id,block_id=node.block_id,
                outcome='defeated' if scene.kind=='battle_defeat' else 'won',native=scene.native)
        return self.batch.transaction('battle_start',dict(enter_id=route.enter_id,block_id=node.block_id),send)
    def choose_reward(self,kind,route,node,reward_id):
        self.calls.append((kind,node.block_type));self.runtime.advance()
    def shop(self,route,node):self.calls.append(('shop',node.block_type));self.runtime.advance()
    def event(self,route,node,scene):self.calls.append(('event',node.block_type));self.runtime.advance()

def make(tmp_path,route,pages):
    tasker=SimpleNamespace()
    binding=Binding(123,'offline')
    seq=[0]
    def stamp(**fields):
        seq[0]+=1
        return dict(verified=True,binding=binding.payload(),frame=seq[0],**fields)
    game=SimpleNamespace(tasker=tasker,stamp=stamp)
    port=SimpleNamespace(game=game)
    batch=Batch(Settings((Goal(1,1),)),port,Journal(tmp_path),binding)
    session=GameRunSession(batch.run_id,binding,route,1,1,'creation', 'maphash',route.nodes[0].block_id,1,0)
    batch._game_session=session;batch._game_session_owner=batch._game_owner();batch.last_frame=0
    runtime=Runtime(pages);runtime.tasker=tasker;navigator=Navigator(runtime);actions=Actions(runtime,batch,stamp)
    runner=AutoplayRunner(tasker,stamp,runtime=runtime,navigator=navigator,actions=actions)
    return runner,batch,runtime,navigator,actions

def sequence(types,confirm=False,final=True):
    pages=[('map',dict(area=1))]
    for kind in types[1:]:
        if confirm:pages.append(('move_confirmation',dict(node_type=kind)))
        if kind in (2,3,8):
            pages.extend([({2:'normal_challenge',3:'ex_challenge',8:'boss_challenge'}[kind],{}),
                          ('team',{}),('battle_victory',{})])
            pages.append(('final_clear',{}) if kind==types[-1]==8 and final else ('map',dict(area=1)))
        elif kind in (4,6):pages.extend([({4:'link_reward',6:'relic_reward'}[kind],{}),('map',dict(area=1))])
        elif kind==5:pages.extend([('event',{}),('map',dict(area=1))])
        elif kind==7:pages.extend([('shop',{}),('map',dict(area=1))])
    if final:pages.append(('home',{}))
    return pages

def test_full_single_run_dispatches_all_nodes_and_counts_only_terminal(tmp_path):
    types=[1,2,3,4,5,6,7,8];route=route_for(types)
    runner,batch,runtime,navigator,actions=make(tmp_path,route,sequence(types,confirm=True))
    result=runner.play(route,batch,roles=('a','b','c'))
    assert result['outcome']=='cleared' and result['clear_verified'] is True
    assert result['final_boss_won'] and result['terminal_clear_observed']
    assert result['confirmed_nodes']==[n.block_id for n in route.nodes]
    assert len(navigator.previews)==7 and runtime.inputs.count('confirm_move')==7
    assert ('team',8,True) in actions.calls
    assert {v[0] for v in actions.calls}>={'link_reward','relic_reward','event','shop'}
    assert all(e['state']=='settled' for e in batch.journal.events()[1::2])

def test_area_transition_commits_only_actual_next_area_map(tmp_path):
    route=route_for([1,6,1,8],[1,1,2,2])
    pages=[('map',dict(area=1)),('relic_reward',{}),('area_transition',{}),
        ('map',dict(area=2)),('boss_challenge',{}),('team',{}),('battle_victory',{}),
        ('final_clear',{}),('home',{})]
    runner,batch,_,_,_=make(tmp_path,route,pages)
    result=runner.play(route,batch)
    assert result['confirmed_nodes']==[n.block_id for n in route.nodes] and result['clear_verified']

@pytest.mark.parametrize('kind,payload,reason',[
    ('move_confirmation',dict(node_type=7),'node_type_conflict'),
    ('unknown',{},'node_preview_unknown'),('date_changed',{},'date_changed'),('disconnected',{},'disconnected')])
def test_preview_wrong_or_unknown_never_retries(tmp_path,kind,payload,reason,monkeypatch):
    route=route_for([1,2])
    runner,batch,runtime,navigator,_=make(tmp_path,route,[('map',dict(area=1)),(kind,payload)])
    if kind=='unknown':
        import pcr_maa.labyrinth.autoplay as module
        clock=[0.0];monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:clock[0]))
        monkeypatch.setattr(module,'_poll_pause',lambda *args:clock.__setitem__(0,clock[0]+.5))
        from pcr_maa.labyrinth import scene as scene_module
        monkeypatch.setattr(scene_module,'time',module.time)
    with pytest.raises(BatchStopped,match=reason):runner.play(route,batch)
    assert len(navigator.previews)==1 and not runtime.inputs
    assert batch.journal.events()[-1]['state']=='settled'
    assert batch.journal.events()[-1]['result']['input_action_verified'] is True
    with pytest.raises(BatchStopped,match='autoplay_already_attempted'):runner.play(route,batch)
    assert len(navigator.previews)==1

def test_unknown_after_confirmation_keeps_known_ack_without_replay(tmp_path,monkeypatch):
    route=route_for([1,4])
    runner,batch,runtime,navigator,_=make(tmp_path,route,
        [('map',dict(area=1)),('move_confirmation',dict(node_type=4)),('unknown',{})])
    import pcr_maa.labyrinth.autoplay as module
    clock=[0.0];monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    monkeypatch.setattr(module,'_poll_pause',lambda *args:clock.__setitem__(0,clock[0]+.5))
    from pcr_maa.labyrinth import scene as scene_module
    monkeypatch.setattr(scene_module,'time',module.time)
    with pytest.raises(BatchStopped,match='autoplay_readback_unknown'):runner.play(route,batch)
    assert runtime.inputs==['confirm_move'] and len(navigator.previews)==1
    assert batch.journal.events()[-1]['operation']=='node_move_confirm'
    assert batch.journal.events()[-1]['state']=='settled'
    assert batch.journal.events()[-1]['result']['input_action_verified'] is True

@pytest.mark.parametrize('wrapped',[False,True])
def test_battle_defeat_never_counts_a_clear(wrapped,tmp_path):
    route=route_for([1,8])
    runner,batch,runtime,_,_=make(tmp_path,route,
        [('map',dict(area=1)),('boss_challenge',{}),('team',{}),('battle_defeat',{})])
    if wrapped:
        original=runner.actions.start_battle
        def start(*args):
            result=original(*args)
            return dict(result,native=dict(verified=True,outcome='defeated',native=result['native']))
        runner.actions.start_battle=start
    result=runner.play(route,batch)
    assert result['native']['native_ok'] is True
    assert result['outcome']=='defeated' and result['clear_verified'] is False
    assert not result['terminal_clear_observed'] and runtime.inputs==['challenge','start_battle']

def test_result_without_independent_clear_stops(tmp_path):
    route=route_for([1,8])
    runner,batch,_,_,_=make(tmp_path,route,
        [('map',dict(area=1)),('boss_challenge',{}),('team',{}),('battle_victory',{}),('score_result',{}),('home',{})])
    with pytest.raises(BatchStopped,match='terminal_clear_not_observed'):runner.play(route,batch)
    assert batch.completed=={1:0}

def test_stop_or_cloned_session_causes_zero_inputs(tmp_path):
    for case in ('stop','clone'):
        path=tmp_path/case;path.mkdir()
        route=route_for([1,2]);runner,batch,runtime,navigator,_=make(path,route,[('map',dict(area=1))])
        if case=='stop':batch.stopped=lambda:True
        else:batch._game_session=replace(batch._game_session,run_id='other')
        with pytest.raises(BatchStopped):runner.play(route,batch)
        assert not runtime.inputs and not navigator.previews and not batch.journal.events()

def test_route_mapping_retains_real_type_activation_and_bottom_to_top():
    start=Node(10101,1,1,1,1,(10201,10202))
    low=Node(10201,1,2,1,2,());high=Node(10202,1,2,2,4,())
    route=Route(99,(start,low),(start,low,high),'protocol')
    columns=[[dict(block_type=4,active=True,box=[400,100,100,100]),
              dict(block_type=2,active=True,box=[400,400,100,100])]]
    assert bind_route_target(route,start,columns)['box']==[400,400,100,100]
    columns[0][1]['active']=False
    assert bind_route_target(route,start,columns) is None
    columns[0][1].update(active=True,block_type=5)
    assert bind_route_target(route,start,columns) is None

def test_same_type_multiple_visual_columns_do_not_pick_first():
    route=route_for([1,2])
    columns=[[dict(block_type=2,active=True,box=[350,250,100,100])],
             [dict(block_type=2,active=True,box=[850,250,100,100])]]
    assert bind_route_target(route,route.nodes[0],columns) is None


def test_default_adapters_consume_confirmed_defense_and_combat_context(monkeypatch):
    from pcr_maa.labyrinth.autoplay import MaaRunActions
    from pcr_maa.labyrinth.config import Strategy
    from pcr_maa.labyrinth.strategies import MARKS
    import pcr_maa.labyrinth.team_execution as team_module
    import pcr_maa.labyrinth.rewards as reward_module
    import pcr_maa.labyrinth.shop_execution as shop_module
    trace=[]
    strategy=Strategy(boss_mode='single',deployment_weights=(1,.2,.1))
    batch=SimpleNamespace(settings=SimpleNamespace(strategy=strategy),timings=object())
    adapter=MaaRunActions.__new__(MaaRunActions)
    adapter.tasker=object();adapter.stamp=lambda **v:v;adapter.batch=batch
    adapter.characters=object();adapter.stacks={mark:0 for mark in MARKS};adapter.context_basis={}
    adapter.stacks['守备']=8;adapter.owned=['1075','1351','1059']
    adapter.role_pending=[];adapter.encounter=None;adapter.effective_ids=();adapter.failed_teams={}
    adapter.retry_numbers={};adapter.last_failed_team={};adapter.force_multi_blocks=set();adapter.relic_focus=None
    class Team:
        def __init__(self,*args,**kw):assert kw['characters'] is adapter.characters
        def prepare(self,batch,enter,block,context,*,hold_current=False):
            assert hold_current==(adapter.combat_kind=='normal_challenge')
            trace.append(('normal',context,batch.settings.strategy))
        def prepare_boss(self,batch,enter,block,context,**kw):trace.append(('boss',context,batch.settings.strategy))
    class Reward:
        def __init__(self,*args,**kw):pass
        def choose(self,batch,enter,reward,owned,context,**kw):
            assert kw['defer_joined'] is True
            trace.append(('reward',context,batch.settings.strategy));return dict(roles_pending=['1001'],inventory_added=False)
    class Event:
        def __init__(self,*args,**kw):pass
        def run(self,batch,enter,block,**kw):
            trace.append(('event',kw['context'],batch.settings.strategy));return {}
    import pcr_maa.labyrinth.encounters as encounters
    monkeypatch.setattr(encounters,'scan_effective',lambda *args,**kw:())
    monkeypatch.setattr(team_module,'MaaTeam',Team)
    monkeypatch.setattr(reward_module,'MaaRoleReward',Reward)
    monkeypatch.setattr(shop_module,'MaaEvent',Event)
    route=route_for([1,2,3,8])
    adapter.combat_kind='normal_challenge';adapter.prepare_team(route,route.nodes[1])
    adapter.combat_kind='ex_challenge';adapter.prepare_team(route,route.nodes[2])
    adapter.prepare_team(route,route.nodes[3],boss=True)
    adapter.choose_reward('role_reward',route,route.nodes[1],'reward')
    adapter.event(route,route.nodes[1],object())
    assert all(c.defense==8 and c.targets==1 and c.enemy=='UNKNOWN' and s is strategy for _,c,s in trace)
    assert not trace[0][1].prefer_single and trace[1][1].prefer_single and trace[2][1].boss
    assert not trace[3][1].boss and adapter.role_pending==['1001'] and '1001' not in adapter.owned
    assert trace[2][1].prefer_single and trace[2][1].effective_ids==()
    adapter.stacks['守备']=None
    with pytest.raises(BatchStopped,match='relic_stack_unknown'):adapter._context()


@pytest.mark.parametrize('challenge',['normal_challenge','ex_challenge'])
def test_confirmed_event_arena_challenge_uses_typed_combat_then_continues(challenge,tmp_path):
    route=route_for([1,5,8])
    pages=[('map',dict(area=1)),('event',{}),(challenge,{}),('team',{}),
        ('battle_victory',{}),('map',dict(area=1)),('boss_challenge',{}),('team',{}),
        ('battle_victory',{}),('final_clear',{}),('home',{})]
    runner,batch,runtime,_,actions=make(tmp_path,route,pages)
    result=runner.play(route,batch)
    assert result['clear_verified'] and runtime.inputs.count('start_battle')==2
    assert ('event',5) in actions.calls and ('team',5,False) in actions.calls


def test_event_animation_result_completes_current_event_before_next_target(tmp_path):
    route=route_for([1,5,8])
    pages=[('map',dict(area=1)),('event_animation',{}),('item_reward',{}),
        ('map',dict(area=1)),('boss_challenge',{}),('team',{}),('battle_victory',{}),
        ('final_clear',{}),('home',{})]
    runner,batch,runtime,_,_=make(tmp_path,route,pages)
    result=runner.play(route,batch)
    assert result['clear_verified'] and runtime.inputs[:2]==['skip_event_animation','close_reward']


def test_default_actions_install_actual_link_obtained_readback_and_dormant_input_guards():
    from pcr_maa.labyrinth.autoplay import MaaRunActions
    from pcr_maa.labyrinth.timing import Timings
    installed={}
    def override(nodes):installed.update(nodes);return True
    tasker=SimpleNamespace(resource=SimpleNamespace(override_pipeline=override,
        register_custom_recognition=lambda name,observer:True))
    adapter=MaaRunActions(tasker,lambda **kw:kw,object(),SimpleNamespace(timings=Timings()),
                          SimpleNamespace(characters=object()))
    assert {'LabyLinkObserved','LabyLinkSelectOnce','LabyLinkObtainedObserved',
            'LabyLinkObtainedCloseOnce','LabyLinkFollowingObserved','LabyRelicObserved'}<=set(installed)
    assert installed['LabyLinkSelectOnce']['all_of']==['LabyLinkGuardRequired']
    assert adapter.stacks['守备']==0 and adapter.stack_basis=='actual_opening_initial_selection_reset'


@pytest.mark.parametrize('next_area',[2,3,'never'])
def test_automatic_area_transition_waits_old_map_and_black_without_input(tmp_path,monkeypatch,next_area):
    from pcr_maa.labyrinth.scene import MaaSceneRuntime
    import pcr_maa.labyrinth.scene as scenes
    import pcr_maa.labyrinth.autoplay as autoplay
    route=route_for([1,6,1,8],[1,1,2,2])
    after=('map',dict(area=1 if next_area=='never' else next_area))
    pages=[('map',dict(area=1)),('relic_reward',{}),('map',dict(area=1)),
        ('unknown',{}),after,('boss_challenge',{}),('team',{}),
        ('battle_victory',{}),('final_clear',{}),('home',{})]
    runner,batch,runtime,nav,_=make(tmp_path,route,pages)
    clock=[0.0];monkeypatch.setattr(scenes,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    runtime.tasker=runner.tasker
    runtime.wait_for=lambda *args,**kwargs:MaaSceneRuntime.wait_for(runtime,*args,**kwargs)
    def pause(*args):
        clock[0]+=.5
        if runtime.i<4:runtime.advance()
    monkeypatch.setattr(autoplay,'_poll_pause',pause)
    if next_area==2:
        result=runner.play(route,batch)
        assert result['confirmed_nodes']==[n.block_id for n in route.nodes] and result['clear_verified']
        assert len(nav.previews)==2 and 'next_area' not in runtime.inputs
    else:
        with pytest.raises(BatchStopped,match='area_transition_unknown'):runner.play(route,batch)
        assert len(nav.previews)==1 and not runtime.inputs


@pytest.mark.parametrize('state',['old_map_then_black','black_first'])
def test_reward_close_observation_reuses_pure_wait_before_automatic_area(tmp_path,monkeypatch,state):
    import pcr_maa.labyrinth.scene as scenes
    import pcr_maa.labyrinth.autoplay as autoplay
    route=route_for([1,6,1,8],[1,1,2,2])
    intermediate=[('map',dict(area=1)),('unknown',{})] if state=='old_map_then_black' else [('unknown',{})]
    pages=[('map',dict(area=1)),('relic_reward',{}),*intermediate,
        ('map',dict(area=2)),('boss_challenge',{}),('team',{}),
        ('battle_victory',{}),('final_clear',{}),('home',{})]
    runner,batch,runtime,nav,_=make(tmp_path,route,pages)
    clock=[0.0];monkeypatch.setattr(scenes,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    def pause(*args):clock[0]+=.5;runtime.advance()
    monkeypatch.setattr(autoplay,'_poll_pause',pause)
    result=runner.play(route,batch)
    assert result['clear_verified'] and len(nav.previews)==2 and 'next_area' not in runtime.inputs


def test_post_clear_chest_and_final_items_route_home_through_map_background(tmp_path,monkeypatch):
    import pcr_maa.labyrinth.autoplay as module
    route=route_for([1,8]);pages=sequence([1,8])[:-1]+[
        ('map',dict(area=1)),('chest',{}),('map',dict(area=1)),
        ('item_reward',{}),('map',dict(area=1)),('home',{})]
    runner,batch,runtime,navigator,_=make(tmp_path,route,pages)
    monkeypatch.setattr(module,'_poll_pause',lambda *a:runtime.advance())
    result=runner.play(route,batch)
    assert result['outcome']=='cleared' and result['final_boss_won'] and result['terminal_clear_observed']
    assert len(navigator.previews)==1 and runtime.inputs==[
        'challenge','start_battle','settlement_next','settlement_next','settlement_next','close_reward']
