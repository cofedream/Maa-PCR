"""Fixed upstream combat branches; offline policy/native transport checks."""
from dataclasses import replace
from types import SimpleNamespace
import numpy as np,pytest
from pcr_maa.labyrinth.encounters import (EX,BY_ID,FROST_WOLF,capability,contribution,guide_core,
    boss_encounter,match_name,ex_layout,_MULTI,_DUAL,scan_effective,encounter_pipeline,MaaEncounter)
from pcr_maa.labyrinth.roles import Optimizer,load_optimizer,configured_optimizer,Context
from pcr_maa.labyrinth.routes import Node
from pcr_maa.labyrinth.teams import boss_plan
from pcr_maa.labyrinth.batch import BatchStopped
from test_labyrinth_roles import profile
from test_labyrinth_character_port import offline

@pytest.mark.parametrize('encounter',EX,ids=lambda e:e.id)
def test_all_fourteen_fixed_ex_name_target_mechanism_catalogue(encounter):
    assert match_name(encounter.name)==encounter
    assert match_name('C'+encounter.name)==(encounter if len(encounter.name)>=3 else None)
    assert encounter.targets in (1,2,5)
    for alias in encounter.aliases:assert match_name(alias)==encounter

def test_identity_never_uses_description_substring_and_shadow_variant_is_specific():
    assert match_name('已获得新属性效果：持续攻击胡桃的暗影并降低敌人防御') is None
    assert match_name('爆炸的遗物无法触发，弱点暂无') is None
    assert match_name('凱露（新年）')==BY_ID['multi_ny_karyl_shadow']
    assert match_name('美冬') is None

def test_frost_wolf_comes_from_actual_route_quest_not_selected_filter():
    assert boss_encounter(Node(30701,3,7,1,8,(),770300300))==FROST_WOLF
    assert boss_encounter(Node(30701,3,7,1,8,(),770300200)) is None
    assert boss_encounter(Node(50701,5,7,1,8,(),770500300)) is None
    assert boss_encounter(Node(30701,3,7,1,8,(),None)) is None
    assert FROST_WOLF.targets==3

def test_capabilities_are_known_facts_not_class_or_damage_guesses():
    p=profile('p',200);p.data['functions']={};p.data['roleClass']='妨碍者'
    assert capability(p,'CONTROL') is None and capability(p,'WIDE_AOE') is None
    p.data['functions']['threeTargetDamage']=100
    assert capability(p,'WIDE_AOE') is None
    p.data['skillFacts']=[dict(effect='PULL')]
    assert capability(p,'PULL')==80

def test_partial_requirement_and_geometry_strength_and_displacement_fallback():
    dot=profile('dot',200);dot.data['functions']['dot']=80
    assert contribution((dot,),BY_ID['good_friend_x'])==.5
    assert contribution((dot,dot),BY_ID['good_friend_x'])==1
    wide=profile('wide',200);wide.data['functions'].update(wideAoeCoverage=100,backlineAoeCoverage=55)
    narrow=profile('narrow',200);narrow.data['functions'].update(wideAoeCoverage=55,enemyPull=80)
    strategy=BY_ID['multi_ny_karyl_shadow']
    assert contribution((wide,narrow),strategy)==pytest.approx(1.55+.55+.8+1)
    assert contribution((wide,),FROST_WOLF)==0

def test_player_blend_attribute_toggle_and_runtime_four_scoring_parameters():
    base=load_optimizer();updated=configured_optimizer(dict(player_weight=35,attribute_pairing_bonus_enabled=False,
        duplicate_role_penalty=3,mixed_damage_penalty=22,effective_character_system_bonus=11))
    assert updated.config['playerWeight']==.35
    assert updated.config['duplicatePureRolePenalty']==3 and updated.config['battleMixedDamagePenalty']==22
    assert updated.config['encounterEffectiveCharacterBonus']==11
    assert not any(updated.config['attributeDamageBonus'].values())
    assert base.config['playerWeight']==.7 and base.config['attributeDamageBonus']['2']==.08
    for invalid in (dict(player_weight=True),dict(effective_character_system_bonus=31),dict(attribute_pairing_bonus_enabled=1)):
        with pytest.raises(ValueError):configured_optimizer(invalid)

def optimizer(roles):return Optimizer({p.id:p for p in roles},dict(load_optimizer().config))

def test_mechanism_and_empty_effective_reach_scoring_and_guide_core():
    tank=profile('tank',100,tank=True);dot=profile('dot',200,rating=1);dot.data['functions']['dot']=90
    dps=[profile('d'+str(i),300+i,rating=99) for i in range(5)]
    a=optimizer([tank,dot,*dps]);ids=tuple(a.profiles);context=Context(defense=4,encounter=BY_ID['good_friend_x'])
    assert guide_core(a,ids,context)==('dot',)
    assert 'dot' in a.best(ids,context,battle=True).members
    team=('tank','dot','d0','d1','d2')
    plain=a.score(team,Context(defense=4));guide=a.score(team,context)
    assert guide.score>plain.score
    assert a.score(team,replace(context,effective_ids=())).score==guide.score
    assert a.score(team,replace(context,effective_ids=('dot',))).score>guide.score
    assert a.best(('missing',),context,battle=True) is None

def test_retry_excludes_failed_signature_including_reordered_ids_and_boss_fill():
    a=optimizer([profile('tank',100,tank=True),*[profile('d'+str(i),300+i) for i in range(6)]])
    ids=tuple(a.profiles);context=Context(defense=4)
    first=a.best(ids,context,battle=True)
    retry=a.retry_best(ids,context,failed_teams=(tuple(reversed(first.members)),))
    assert retry is not None and set(retry.members)!=set(first.members)
    plan=boss_plan(a,ids,replace(context,boss=True),requested=1,excluded=(first.members,))
    assert plan.teams and set(plan.teams[0].members)!=set(first.members)
    assert a.retry_best(first.members,context,failed_teams=(first.members,)) is None

def test_second_ex_failure_swaps_healer_preserving_front_and_effective_first():
    tank=profile('tank',100,tank=True);effective=profile('effective',200,rating=1)
    roles=[tank,effective,*[profile('d'+str(i),300+i,rating=20+i) for i in range(3)]]
    healer=profile('heal',600,rating=5);healer.data['functions'].update(healing=80);healer.data['roleClass']='治疗者'
    a=optimizer([*roles,healer]);failed=tuple(p.id for p in roles)
    c=Context(defense=4,encounter=BY_ID['good_friend_x'],effective_ids=('effective',))
    retry=a.retry_best(tuple(a.profiles),c,failed_teams=(failed,),last_failed_team=failed,retry_number=2)
    assert {'tank','effective','heal'}<=set(retry.members) and 'd0' not in retry.members
    # Effective membership is a preference; a full effective roster may still swap one.
    retry=a.retry_best(tuple(a.profiles),replace(c,effective_ids=failed),failed_teams=(failed,),last_failed_team=failed,retry_number=2)
    assert {'tank','heal'}<=set(retry.members) and 'effective' not in retry.members

def circle(frame,box):
    x,y,w,h=box;frame[y:y+h,x:x+w]=[215,215,205]
    frame[y:y+h,x:x+int(w*.45)]=[220,170,130]

@pytest.mark.parametrize('kind',['multi','dual0','dual1','single'])
def test_original_info_circle_structures_distinguish_slot_three_and_dual_right(kind):
    frame=np.zeros((720,1280,3),np.uint8)
    boxes=_MULTI if kind=='multi' else _DUAL[int(kind[-1])] if kind.startswith('dual') else ()
    for box in boxes:circle(frame,box)
    observed,target=ex_layout(frame)
    assert observed==('dual' if kind.startswith('dual') else kind)
    assert target==list(boxes[2] if kind=='multi' else boxes[1]) if boxes else target is None

def test_read_has_no_single_hit_budget_and_detail_click_budgets_remain_scoped():
    pipeline=encounter_pipeline('Example')
    assert 'max_hit' not in pipeline['ExampleRead']
    assert pipeline['ExampleOpenOnce']['max_hit']==pipeline['ExampleCloseOnce']['max_hit']==1

class FakeBatch:
    def __init__(self):self.observed=[]
    def checkpoint(self):pass
    def observation(self,value,**expected):self.observed.append(value);return value
    def transaction(self,kind,fields,send):return send(kind)

@pytest.mark.parametrize('empty',[True,False])
def test_effective_complete_scan_empty_branch_restore_all_and_skip_unknown(monkeypatch,empty):
    from pcr_maa.labyrinth import character_recognition as cr
    moved=[]
    card=lambda role,trusted:dict(character_id=role,trusted=trusted)
    def value(filter='ALL',position=0):
        return dict(filter=filter,signature=str(position),stable=True,empty_effective_verified=empty and filter=='EFFECTIVE_EFFECT',
            cards=[] if empty else [card('1001',True),card(None,False)] if position==0 else [card('1002',True)],boss_tab=1)
    class Nav(cr.CharacterNavigation):
        def __init__(self,*args,roster):assert tuple(roster)==('1001','1002')
        def move(self,batch,enter,page,observation,*,filter=None,direction=None,**kw):
            moved.append(filter or direction)
            return value(filter=filter or 'EFFECTIVE_EFFECT',position=0 if filter else 1)
        def edge(self,batch,enter,page,observation,direction,**kw):moved.append(direction);return observation
    monkeypatch.setattr(cr,'CharacterNavigation',Nav)
    chars=SimpleNamespace(read=lambda *args,**kw:(value(),{}));batch=FakeBatch()
    ids=scan_effective(chars,batch,lambda **fields:fields,11,20301,page='battle',roster=('1001','1002'))
    assert ids==(() if empty else ('1001','1002'))
    assert moved[0]=='EFFECTIVE_EFFECT' and moved[-1]=='ALL'
    assert ('up' not in moved and 'down' not in moved) if empty else 'up' in moved
    assert batch.observed[-1]['effective_ids']==list(ids)
    assert batch.observed[-1]['effective_scan_skipped_unsafe']==(not empty)

@pytest.mark.parametrize('name,targets',[('美空',2),('未来未知魔物',5)])
def test_owned_detail_open_close_successor_and_unknown_guide_fallback(name,targets):
    adapter=object.__new__(MaaEncounter);batch=FakeBatch();adapter.stamp=lambda **fields:fields
    strategy=match_name(name);identity=strategy.id if strategy else None
    values=iter([(dict(page='challenge',layout='dual' if strategy else 'multi',encounter_id=None,target=[100,100,30,30]),{'native_ok':True}),
        (dict(page='detail',encounter_id=identity,raw_name=name),{'native_ok':True}),
        (dict(page='challenge',encounter_id=None),{'native_ok':True})])
    adapter._read=lambda *args:next(values);inputs=[]
    def run(suffix,params,**kw):inputs.append((suffix,params));return None,{'native_ok':True}
    adapter._run=run
    result=adapter.observe_ex(batch,11,20301)
    assert [suffix for suffix,_ in inputs]==['OpenOnce','CloseOnce']
    assert result['target_count']==targets and result['strategy']==strategy
    assert result['guide_fallback_verified']==(strategy is None)
    assert inputs[1][1]['detail_owned'] and inputs[1][1]['encounter_id']==identity


def test_real_native_detail_transactions_repeated_shared_callback_reads_and_budget(tmp_path,monkeypatch):
    """Synthetic independent page bytes test Maa transport; not client OCR accuracy."""
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    from maa.custom_recognition import CustomRecognition
    from pcr_maa.labyrinth import encounters
    class Pages(SavedImages):
        def __init__(self):super().__init__([[np.zeros((720,1280,3),np.uint8)]]);self.detail=False
        def screencap(self):
            frame=np.zeros((720,1280,3),np.uint8);frame[0,0,0]=self.detail;return frame
        def click(self,x,y):
            self.clicks.append([x,y])
            if not self.detail:assert 600<=x<620 and 400<=y<420
            else:assert 500<=x<600 and 620<=y<660
            self.detail=not self.detail;return True
    class Recognition(CustomRecognition):
        def __init__(self,namespace):super().__init__();self.namespace=namespace
        def analyze(self,context,argv):
            import json
            params=json.loads(argv.custom_recognition_param);mode=params['mode'];detail=bool(argv.image[0,0,0])
            if mode=='open' and not detail:return self.AnalyzeResult([600,400,20,20],dict(page='challenge'))
            if mode=='close' and detail and params.get('detail_owned') and params.get('encounter_id')=='multi_kurumi_shadow':
                return self.AnalyzeResult([500,620,100,40],dict(page='detail'))
            if mode!='read':return None
            return self.AnalyzeResult([0,0,1,1],dict(page='detail',encounter_id='multi_kurumi_shadow',raw_name='胡桃的暗影')
                if detail else dict(page='challenge',encounter_id=None,layout='multi',target=[600,400,20,20]))
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    monkeypatch.setattr(encounters,'EncounterRecognition',Recognition)
    controller=Pages();resource=native.load_resource();owner=native.bind(resource,controller)
    batch=FakeBatch();adapters=[]
    try:
        for block in (20501,40501,40501):
            adapter=MaaEncounter(owner,lambda **fields:fields);adapters.append(adapter)
            observed=adapter.observe_ex(batch,11,block)
            assert observed['encounter_id']=='multi_kurumi_shadow' and observed['target_count']==5
            assert observed['native']['native_ok'] and not controller.detail
        assert len(controller.clicks)==6
        assert len({id(a.callback) for a in adapters})==1
        assert len({a.namespace for a in adapters})==1
        assert all(e['native']['native_ok'] for e in batch.observed)
    finally:assert native.wait_job(owner.post_stop(),seconds=10).succeeded


def test_only_authenticated_started_teams_become_failed_signatures():
    from pcr_maa.labyrinth.team_execution import MaaTeam
    team=object.__new__(MaaTeam);key=(11,30701)
    team.started={key};team.prepared={key:['tank','d1']};team.boss_teams={key:[['tank','d1'],['t2','d2']]}
    assert team.failed_signatures(*key)==(('d1','tank'),('d2','t2'))
    assert team.failed_signature(*key)==('d1','tank')
    with pytest.raises(BatchStopped,match='battle_without_owned_start'):team.failed_signatures(11,30702)

@pytest.mark.parametrize('force_multi',[False,True])
def test_boss_mode_config_and_explicit_fallback_prepare_real_disjoint_plan(monkeypatch,force_multi):
    from pcr_maa.labyrinth.team_execution import MaaTeam
    from pcr_maa.labyrinth import character_recognition as cr
    roles=[profile('t'+str(i),100+i,tank=True) for i in range(3)]+[profile('d'+str(i),300+i) for i in range(12)]
    a=optimizer(roles);state=dict(boss_tab=1,selected=[],members=[])
    def read(*args,**kw):return dict(state),{'native_ok':True}
    class Nav:
        def __init__(self,*args,roster):assert tuple(roster)==tuple(a.profiles)
        def move(self,*args,tab,**kw):state['boss_tab']=tab;return dict(state)
    monkeypatch.setattr(cr,'CharacterNavigation',Nav)
    team=MaaTeam(SimpleNamespace(),lambda **fields:fields,tuple(a.profiles),optimizer=a,characters=SimpleNamespace(read=read))
    def edit(batch,enter,block,page,desired,observation):
        state['selected']=desired;return dict(state),{'native_ok':True}
    team._edit=edit;batch=FakeBatch();batch.settings=SimpleNamespace(strategy=SimpleNamespace(boss_mode='single'))
    result=team.prepare_boss(batch,11,30701,Context(defense=4,boss=True),force_multi=force_multi)
    assert len(result['teams'])==(3 if force_multi else 1)
    assert result['selected_teams_verified'] and len(set(uid for group in result['teams'] for uid in group))==sum(map(len,result['teams']))
    assert team.boss_all_teams[11,30701][1:]==[[],[]] if not force_multi else all(team.boss_all_teams[11,30701])

def test_known_retry_has_no_distinct_plan_explicitly_reports_roster_exhaustion():
    from pcr_maa.labyrinth.team_execution import MaaTeam
    a=optimizer([profile('tank',100,tank=True),*[profile('d'+str(i),300+i) for i in range(4)]])
    ids=tuple(a.profiles);batch=FakeBatch();batch.settings=SimpleNamespace(strategy=None)
    team=MaaTeam(SimpleNamespace(resource=SimpleNamespace(get_node_data=lambda node:None)),lambda **fields:fields,ids,
        optimizer=a,characters=SimpleNamespace(),failed_teams=(ids,),retry_number=1)
    with pytest.raises(BatchStopped,match='battle_retry_team_exhausted'):team.prepare(batch,11,10201,Context(defense=4))

@pytest.mark.parametrize('input',[False,True])
def test_native_ack_is_not_hidden_by_parameter_restore_failure(monkeypatch,input):
    from pcr_maa import native
    adapter=object.__new__(MaaEncounter);adapter.namespace='Fixture'
    from pcr_maa.labyrinth.timing import Timings
    adapter.timings=Timings();changes=[]
    class Resource:
        def get_node_data(self,node):return {'original':True}
        def override_pipeline(self,rows):changes.append(rows);return len(changes)==1
    adapter.tasker=SimpleNamespace(resource=Resource())
    node='Fixture'+('OpenOnce' if input else 'Read')
    receipt=dict(tasker_inited=True,task_id=1,native_ok=True,nodes=[dict(name=node,completed=True,
        action=dict(name=node,success=True),recognition=dict(best=dict(detail=dict(page='challenge'))))])
    monkeypatch.setattr(native,'run',lambda *args,**kw:receipt)
    if input:assert adapter._run('OpenOnce',dict(mode='open'),input=True)[1] is receipt
    else:
        with pytest.raises(BatchStopped,match='encounter_guard_restore_failed'):adapter._run('Read',dict(mode='read'))


@pytest.mark.parametrize('mode',['unqualified','read','open','close'])
def test_real_encounter_callback_scopes_owned_modal_close_and_inert_default(monkeypatch,mode):
    import json
    from pcr_maa.labyrinth import encounters,scene
    monkeypatch.setattr(encounters,'_detail_present',lambda image:True)
    words={'DetailClose':dict(text='关闭',box=[515,625,100,30]),
        'DetailName':dict(text='美空',box=[395,97,50,30])}
    monkeypatch.setattr(scene,'_primitive',lambda result,key:words[key])
    context=SimpleNamespace(tasker=SimpleNamespace(stopping=False),run_recognition=lambda *args:None)
    recognizer=encounters.EncounterRecognition('Test')
    params=dict(mode=mode,detail_owned=True,encounter_id='misora')
    result=recognizer.analyze(context,SimpleNamespace(custom_recognition_param=json.dumps(params),image=np.zeros((720,1280,3),np.uint8)))
    assert (result is not None)==(mode in ('read','close'))
    if mode=='close':
        params['encounter_id']='queen_bee'
        assert recognizer.analyze(context,SimpleNamespace(custom_recognition_param=json.dumps(params),image=np.zeros((720,1280,3),np.uint8))) is None


def test_native_detail_title_split_words_keep_known_seasonal_encounter(monkeypatch):
    import json
    from pcr_maa.labyrinth import encounters
    monkeypatch.setattr(encounters,'_detail_present',lambda image:True)
    def result(rows):return SimpleNamespace(hit=True,reco_id=1,raw_detail=dict(filtered=rows))
    def word(text,x,y,w):return dict(text=text,box=[x,y,w,26],score=.92)
    name=result([word('的暗影',585,97,60),word('凯露（新年）',395,97,165)])
    close=result([word('关闭',515,625,100)])
    context=SimpleNamespace(tasker=SimpleNamespace(stopping=False),run_recognition=lambda node,image:close if node.endswith('DetailClose') else name)
    value=encounters.EncounterRecognition('Test').analyze(context,SimpleNamespace(
        custom_recognition_param=json.dumps(dict(mode='read')),image=np.zeros((720,1280,3),np.uint8)))
    assert value is not None and value.detail['encounter_id']=='multi_ny_karyl_shadow'


def test_run122_real_empty_effective_notice_native_read_no_input(tmp_path,monkeypatch,offline):
    from pathlib import Path
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    from pcr_maa.labyrinth.character_recognition import MaaCharacters
    screen=Path(__file__).resolve().parents[1]/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/b400772e3b6248f89f2346c2384f3b34/acceptance-ending/screen.png'
    if not screen.is_file():pytest.skip('private run122 frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(screen)]])
    owner=native.bind(native.load_resource(),controller)
    try:
        chars=MaaCharacters(owner)
        observed,receipt=chars.run('Read',dict(mode='read',page='battle',roster=None))
        assert receipt['native_ok'] and observed['filter']=='EFFECTIVE_EFFECT'
        assert observed['empty_effective_verified'] and observed['stable'] and not observed['cards']
        assert observed['empty_effective_native']['text']=='！未搜索到该角色。'
        assert len(observed['members'])==5 and all(m['trusted'] for m in observed['members'])
        assert not controller.clicks and not controller.swipes
    finally:assert native.wait_job(owner.post_stop(),seconds=10).succeeded


def test_effective_empty_notice_covers_battle_boss_and_rejects_other_messages():
    import re
    from pcr_maa.labyrinth.character_recognition import character_pipeline
    node=character_pipeline('scope')['scopeEffectiveEmptyNotice']
    x,y,w,h=node['roi']
    for box in ([543,274,183,24],[534,351,198,25]):
        a,b,c,d=box
        assert x<=a and y<=b and a+c<=x+w and b+d<=y+h
    for text in ('未搜索到该角色','未搜索到该角色。','！未搜索到该角色。','!未搜索到该角色。'):
        assert re.fullmatch(node['expected'],text)
    for text in ('未搜索到该角色，加载中','未搜索到该角色技能。','请选择角色','网络连接错误','！！未搜索到该角色。'):
        assert not re.fullmatch(node['expected'],text)
    assert node['threshold']==.8 and node['timeout']==1000 and node['action']=='DoNothing'
