"""Pinned EX/Boss guide data, native detail probes and effective-role scanning.

Adapted from LabyrinthExEncounterStrategy/Recognition and EntryRecognitionSession,
fixed upstream b8d8af912269cfea9ad65fd4e284d0d64da8e118 (CC BY-NC-SA 4.0).
Capabilities use existing generated facts; an unknown fact never earns credit.
"""
from dataclasses import dataclass
import json,re,time
from uuid import uuid4
from maa.custom_recognition import CustomRecognition
from .. import native
from .batch import BatchStopped
from .timing import Timings

@dataclass(frozen=True)
class Encounter:
    id:str
    name:str
    targets:int
    aliases:tuple=()
    requirements:tuple=()  # (any-of capabilities, member count, minimum score)
    preferred:tuple=()     # (capability, preferred member count)
    fallback:tuple=()      # (primary capability, primary count, alternatives, limit)

EX=(
 Encounter('ghost_lord','幽灵领主',1,preferred=(('HEAL',1),)),
 Encounter('good_friend_x','好朋友X',1,requirements=((('DOT',),2,50),)),
 Encounter('island_whale','岛鲸',1),
 Encounter('antimatter_beast','反物质兽',1),
 Encounter('giant_tuna','巨型吞拿鱼',1,preferred=(('HEAL',1),)),
 Encounter('lava_dragon','熔岩龙',1),
 Encounter('queen_bee','黄蜂女王',2,aliases=('蜂后',),requirements=((('CONTROL',),1,50),),preferred=(('BURST',1),)),
 Encounter('misora','美空',2,preferred=(('AOE',1),('BURST',1))),
 Encounter('multi_explosion_relic','爆炸・遗物',5,requirements=((('SHIELD','CONTROL'),1,50),),preferred=(('HEAL',1),)),
 Encounter('multi_kurumi_shadow','胡桃的暗影',5,requirements=((('DOT',),2,50),)),
 Encounter('multi_ny_karyl_shadow','凯露（新年）的暗影',5,requirements=((('WIDE_AOE','AOE'),1,50),),
     preferred=(('WIDE_AOE',2),('BACKLINE_AOE',1)),fallback=(('WIDE_AOE',3,('PUSH','PULL'),1),)),
 Encounter('multi_mifuyu_work_shadow','美冬（工作服）的暗影',5,requirements=((('TAUNT','SHIELD'),1,50),),preferred=(('HEAL',1),)),
 Encounter('multi_ruka_shadow','流夏的暗影',5,preferred=(('SHIELD',1),)),
 Encounter('multi_destruction_relic','破坏・遗物',5,preferred=(('BURST',1),)),
)
FROST_WOLF=Encounter('boss_frost_wolf','冰霜魔狼',3,preferred=(('AOE',1),('DOT',1)))
BY_ID={s.id:s for s in (*EX,FROST_WOLF)}
_CAPABILITIES={'DOT':('dot',),'CONTROL':('control',),'SHIELD':('shield',),'HEAL':('healing','regeneration'),
 'TAUNT':('taunt',),'AOE':('aoeDamage',),'WIDE_AOE':('wideAoeCoverage',),'BACKLINE_AOE':('backlineAoeCoverage',),
 'PUSH':('enemyPush',),'PULL':('enemyPull',),'BURST':('burstDamage',)}
_SKILLS={'CONTROL':('CONTROL',),'SHIELD':('SHIELD',),'HEAL':('HEAL','REGENERATION'),'PUSH':('PUSH',),'PULL':('PULL',)}

def capability(profile,kind):
    data=profile.data;values=[data.get('functions',{}).get(key) for key in _CAPABILITIES[kind]]
    if kind=='CONTROL':values.append(data.get('support',{}).get('control'))
    if any(f.get('effect') in _SKILLS.get(kind,()) for f in data.get('skillFacts',())):values.append(80)
    return max((v for v in values if type(v) in (int,float)),default=None)

def contribution(members,strategy):
    if strategy is None:return 0
    strength=lambda p,kind:capability(p,kind) or 0
    value=sum(sum(sorted((strength(p,kind)/100 for p in members if strength(p,kind)>=50),reverse=True)[:count])
        for kind,count in strategy.preferred)
    for kinds,count,minimum in strategy.requirements:
        value+=min(count,sum(any(strength(p,k)>=minimum for k in kinds) for p in members))/count
    for primary,count,kinds,limit in strategy.fallback:
        if sum(strength(p,primary)>=50 for p in members)<count:
            scores=[max(strength(p,k) for k in kinds) for p in members]
            value+=sum(sorted((v/100 for v in scores if v>=50),reverse=True)[:limit])
    return value

def guide_core(optimizer,ids,context):
    strategy=context.encounter
    if strategy is None:return ()
    roles=[optimizer.profiles[uid] for uid in ids];core=[]
    core.extend(p.id for p in sorted(roles,key=lambda p:p.rating or 0,reverse=True) if p.id in context.effective_ids)
    for kinds,count,minimum in strategy.requirements:
        covers=lambda p:any((capability(p,k) or 0)>=minimum for k in kinds)
        need=max(0,count-sum(p.id in core and covers(p) for p in roles))
        core.extend(p.id for p in sorted((p for p in roles if p.id not in core and covers(p)),
            key=lambda p:max(capability(p,k) or 0 for k in kinds),reverse=True)[:need])
    gate=optimizer.cfg('relaxedMinimumReliableVanguard' if context.relaxed else 'minimumReliableVanguard')
    tanks=[p for p in roles if p.position is not None and p.vanguard(context)>=gate]
    return tuple(uid for uid in core if optimizer.profiles[uid].position is not None and
        any(t.id==uid or t.position<=optimizer.profiles[uid].position for t in tanks))[:3]

def boss_encounter(node):
    from .routes import BOSSES
    area,quest=getattr(node,'area',None),getattr(node,'quest_id',None)
    if area not in BOSSES or type(quest) is not int:return None
    ordinal=quest//100%100
    if quest//100000!=(7703 if area==3 else 7705) or not 1<=ordinal<=20:return None
    return FROST_WOLF if BOSSES[area][(ordinal-1)%5]==319604 else None

def _normalized(text):
    substitutions=str.maketrans('凱島鯨靈領質獸魚龍黃遺壞','凯岛鲸灵领质兽鱼龙黄遗坏')
    return re.sub(r'\s+','',text.translate(substitutions).replace('·','・').replace('•','・')
        .replace('的暗影','暗影').replace('（','(').replace('）',')')).lower()

def _variants(text,observed=False):
    values=set()
    for row in text.splitlines():
        row=_normalized(row)
        if not row:continue
        candidates=[row]
        noise=lambda ch:ch.isascii() and (ch.isalnum() or ch in ':;|[]{}<>_')
        if observed and len(row)>=4:
            if noise(row[0]):candidates.append(row[1:])
            if noise(row[-1]):candidates.append(row[:-1])
        for value in candidates:
            values.add(value)
            if value.endswith('暗影') and len(value)>4:values.add(value[:-2])
    return values

def _similarity(a,b):
    if a==b:return 1
    previous=[0]*(len(b)+1)
    for left in a:
        current=[0]
        for i,right in enumerate(b):current.append(previous[i]+1 if left==right else max(current[-1],previous[i+1]))
        previous=current
    aa={a[i:i+2] for i in range(len(a)-1)};bb={b[i:i+2] for i in range(len(b)-1)}
    return max(previous[-1]/max(1,len(a),len(b)),2*len(aa&bb)/(len(aa)+len(bb)) if aa and bb else 0)

def match_name(text):
    if type(text) is not str:return None
    observed=_variants(text,True)
    if not observed:return None
    scored=sorted(((max(_similarity(a,b) for name in (s.name,*s.aliases) for a in _variants(name) for b in observed),s)
        for s in EX),key=lambda row:row[0],reverse=True)
    best,strategy=scored[0]
    return strategy if best>=.72 and (best-scored[1][0]>=.08 or best>=.96) else None

def _rect(values):return tuple(round(v*2/3) for v in values)
_MULTI=tuple(_rect((x,599,70,70)) for x in (395,697,995,1295,1595))
_DUAL=tuple(tuple(_rect(box) for box in pair) for pair in (
 ((856,570,56,56),(1138,570,56,56)),((852,604,56,56),(1152,604,56,56))))

def _coverage(image,box,predicate,step):
    x,y,w,h=box;pixels=image[y:y+h:step,x:x+w:step]
    if pixels.size==0:return 0
    # Native images are BGR; arithmetic must not wrap uint8 differences.
    import numpy as np
    b,g,r=(pixels[:,:,i].astype(np.int16) for i in (0,1,2))
    return float(np.mean(predicate(r,g,b)))

def info_score(image,box):
    bright=_coverage(image,box,lambda r,g,b:(r>=180)&(g>=190)&(b>=195),2)
    cyan=_coverage(image,box,lambda r,g,b:(b>=150)&(b-r>=20)&(b-g>=5),2)
    return (min(bright,.55)*.65+min(cyan,.50)*.35)/.5325

def ex_layout(image):
    scores=[info_score(image,box) for box in _MULTI]
    if sum(v>=.34 for v in scores)>=4:
        return 'multi',list(_MULTI[2]) if scores[2]>=.34 else None
    pair=max(_DUAL,key=lambda pair:sum(info_score(image,box) for box in pair))
    if all(info_score(image,box)>=.34 for box in pair):return 'dual',list(pair[1])
    return 'single',None

def _detail_present(image):
    blue=lambda r,g,b:(b>=145)&(b-r>=20)&(b-g>=5)
    light=lambda r,g,b:(r>=195)&(g>=195)&(b>=195)
    return (_coverage(image,_rect((500,45,900,90)),blue,3)>=.35 and
        _coverage(image,_rect((540,140,820,90)),light,3)>=.70 and
        _coverage(image,_rect((520,500,850,300)),light,3)>=.50 and
        _coverage(image,_rect((790,930,340,60)),light,3)>=.70)

def encounter_pipeline(namespace):
    values={}
    for key,roi,pattern in (
        ('SingleName',_rect((520,550,420,65)),'.+'),('DetailName',_rect((585,140,500,70)),'.+'),
        ('DetailClose',_rect((750,915,420,115)),'^关闭$'),('SingleDetails',_rect((1395,590,155,88)),'^详情$')):
        values[namespace+key]=dict(recognition='OCR',expected=pattern,roi=list(roi),threshold=.8,
            action='DoNothing',next=[],timeout=1500,pre_delay=0,post_delay=0)
    for suffix,action,timeout in (('Read','DoNothing',3000),('OpenOnce','Click',3000),('CloseOnce','Click',2500)):
        values[namespace+suffix]=dict(recognition='Custom',custom_recognition='LabyrinthEncounter',
            custom_recognition_param=dict(mode='unqualified'),action=action,target=True,
            next=[],timeout=timeout,rate_limit=250,pre_delay=0,post_delay=0)
        if action=='Click':values[namespace+suffix]['max_hit']=1
    return values

class EncounterRecognition(CustomRecognition):
    def __init__(self,namespace):super().__init__();self.namespace=namespace
    def analyze(self,context,argv):
        from .scene import _primitive
        try:
            if context.tasker.stopping:return None
            params=json.loads(argv.custom_recognition_param);mode=params.get('mode')
            if mode not in ('read','open','close'):return None
            def word(key):
                result=context.run_recognition(self.namespace+key,argv.image)
                value=_primitive(result,key)
                if value is not None or key not in ('SingleName','DetailName'):return value
                # The narrow title row may be split by Maa into separate native
                # words (e.g. a seasonal suffix). Preserve their row order.
                if result is None or result.hit is not True:return None
                raw=result.raw_detail
                if type(raw) is str:raw=json.loads(raw)
                rows=raw.get('filtered') if type(raw) is dict else None
                from .scene import _box,_score
                if (type(rows) is not list or not 1<=len(rows)<=8 or
                    any(type(w) is not dict or type(w.get('text')) is not str or not w['text'].strip()
                        or not _box(w.get('box')) or not _score(w.get('score')) or w['score']<.8 for w in rows)):return None
                text=''.join(w['text'] for w in sorted(rows,key=lambda w:w['box'][0]))
                return dict(text=text,native=raw)
            detail=_detail_present(argv.image)
            if detail:
                close=word('DetailClose');name=word('DetailName')
                if close is None or name is None or mode=='open':return None
                strategy=match_name(name['text']);identity=strategy.id if strategy else None
                if mode=='close' and (params.get('detail_owned') is not True or params.get('encounter_id')!=identity):return None
                box=close['box'] if mode=='close' else [0,0,1,1]
                if context.tasker.stopping:return None
                return self.AnalyzeResult(box,dict(page='detail',encounter_id=identity,raw_name=name['text']))
            if mode not in ('read','open'):return None
            button=context.run_recognition('LabySceneTemplate_BATTLE_CHALLENGE_BUTTON_0',argv.image)
            extreme=context.run_recognition('LabySceneWord_EXTREME',argv.image)
            suffix=context.run_recognition('LabySceneTemplate_BATTLE_CHALLENGE_SUFFIX_EXTREME_0',argv.image)
            if button is None or not button.hit or not any(v is not None and v.hit for v in (extreme,suffix)):return None
            layout,target=ex_layout(argv.image);strategy=None;name=None
            if layout=='single':
                name=word('SingleName');strategy=match_name(name['text']) if name else None
                if strategy and strategy.targets!=1:strategy=None
                details=word('SingleDetails');target=details['box'] if details else None
            if mode=='open' and (target is None or params.get('layout')!=layout):return None
            point=[target[0]+target[2]//2,target[1]+target[3]//2,1,1] if mode=='open' else [0,0,1,1]
            if context.tasker.stopping:return None
            return self.AnalyzeResult(point,dict(page='challenge',layout=layout,
                encounter_id=strategy.id if strategy else None,raw_name=name['text'] if name else None,target=target))
        except Exception as error:
            import logging
            logging.getLogger(__name__).error('Labyrinth encounter recognition failed: %s',type(error).__name__)
            return None

class MaaEncounter:
    def __init__(self,tasker,stamp,*,timings=None):
        self.tasker,self.stamp,self.timings=tasker,stamp,timings or Timings()
        from .scene import runtime_resource
        resource=runtime_resource(tasker)
        self.callback=getattr(resource,'_labyrinth_encounter_observer',None)
        if self.callback is None:
            self.namespace='LabyEncounter'+uuid4().hex;self.callback=EncounterRecognition(self.namespace)
            if not resource.register_custom_recognition('LabyrinthEncounter',self.callback) or not resource.override_pipeline(encounter_pipeline(self.namespace)):
                raise BatchStopped('encounter_runtime_unavailable')
            resource._labyrinth_encounter_observer=self.callback
        else:self.namespace=self.callback.namespace
    def _run(self,suffix,params,*,input=False):
        from .game_session import _root
        node=self.namespace+suffix;resource=self.tasker.resource;original=resource.get_node_data(node)
        if not resource.override_pipeline({node:dict(custom_recognition_param=params)}):
            if input:raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='encounter_guard_failed'))
            raise BatchStopped('encounter_readback_unknown')
        try:
            with self.timings.measure('page_wait'):receipt=native.run(self.tasker,node,reset_hits=(node,) if input else ())
            root=_root(receipt,node);action=root.get('action')
            if type(action) is not dict or action.get('name')!=node or action.get('success') is not True:
                raise BatchStopped('encounter_input_unknown' if input else 'encounter_readback_unknown')
            detail=None
            if not input:
                raw=root.get('recognition',{});detail=raw.get('best',{}).get('detail') if type(raw) is dict else None
                if type(detail) is not dict:raise BatchStopped('encounter_readback_unknown')
        except BaseException:
            try:resource.override_pipeline({node:original})
            except Exception:pass
            raise
        # Each next call writes this namespace's current params. An ACK is not
        # erased by a non-business restoration failure after a completed input.
        try:restored=resource.override_pipeline({node:original})
        except Exception:restored=False
        if not restored and not input:raise BatchStopped('encounter_guard_restore_failed')
        return detail,receipt
    def _read(self,batch,page,timeout):
        deadline=min(native.RUN_DEADLINE.get(),time.monotonic()+timeout)
        while time.monotonic()<deadline:
            batch.checkpoint();token=native.RUN_DEADLINE.set(deadline)
            try:
                try:value,receipt=self._run('Read',dict(mode='read'))
                except BatchStopped as error:
                    if str(error) not in ('encounter_readback_unknown','game_session_readback_unknown'):raise
                    continue
                batch.checkpoint()
                if time.monotonic()>=deadline:raise BatchStopped('encounter_readback_unknown')
                if value.get('page')==page:return value,receipt
            finally:native.RUN_DEADLINE.reset(token)
        raise BatchStopped('encounter_readback_unknown')
    def observe_ex(self,batch,enter_id,block_id):
        value,receipt=self._read(batch,'challenge',3);details_opened=False;layout=value['layout']
        strategy=BY_ID.get(value['encounter_id'])
        if strategy is None:
            fields=dict(enter_id=enter_id,block_id=block_id,layout=value['layout'])
            if value.get('target') is None:raise BatchStopped('encounter_identity_unknown')
            def opened(oid):
                batch.checkpoint();sent=self._run('OpenOnce',dict(mode='open',layout=value['layout']),input=True)[1]
                return self.stamp(source='maa',operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
            clicked=batch.transaction('ex_detail_open',fields,opened)
            value,receipt=self._read(batch,'detail',10);details_opened=True
            batch.observation(self.stamp(source='maa',operation_id=clicked['operation_id'],**fields,native=receipt,
                input_native=clicked['input_native'],detail_open_verified=True),**fields)
            identity=value['encounter_id'];strategy=BY_ID.get(identity)
            def closed(oid):
                batch.checkpoint();sent=self._run('CloseOnce',dict(mode='close',detail_owned=True,encounter_id=identity),input=True)[1]
                return self.stamp(source='maa',operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
            clicked=batch.transaction('ex_detail_close',fields,closed)
            _,receipt=self._read(batch,'challenge',5)
            batch.observation(self.stamp(source='maa',operation_id=clicked['operation_id'],**fields,native=receipt,
                input_native=clicked['input_native'],detail_close_verified=True),**fields)
        result=dict(strategy=strategy,target_count=strategy.targets if strategy else {'single':1,'dual':2,'multi':5}[layout],encounter_id=strategy.id if strategy else None,
            native=receipt,details_opened=details_opened,guide_fallback_verified=details_opened and strategy is None)
        batch.observation(self.stamp(source='maa',enter_id=enter_id,block_id=block_id,encounter_id=result['encounter_id'],
            target_count=result['target_count'],encounter_observed=True,guide_fallback_verified=result['guide_fallback_verified'],native=receipt),
            enter_id=enter_id,block_id=block_id)
        return result

def scan_effective(characters,batch,stamp,enter_id,block_id,*,page,roster):
    from .character_recognition import CharacterNavigation
    if page not in ('battle','boss'):raise ValueError('effective_page_required')
    nav=CharacterNavigation(characters,stamp,roster=roster);observation,_=characters.read(batch,page,roster=roster)
    if observation['filter']!='EFFECTIVE_EFFECT':
        observation=nav.move(batch,enter_id,page,observation,filter='EFFECTIVE_EFFECT',block_id=block_id)
    ids=set();skipped=False
    if observation.get('empty_effective_verified') is not True:
        observation=nav.edge(batch,enter_id,page,observation,'up',block_id=block_id)
        same=0;anchor=observation
        for _ in range(24):
            if observation['filter']!='EFFECTIVE_EFFECT':raise BatchStopped('effective_filter_changed')
            for card in observation['cards']:
                if card.get('trusted') is True and card.get('character_id') in roster:ids.add(card['character_id'])
                else:skipped=True
            before=observation
            observation=nav.move(batch,enter_id,page,before,direction='down',block_id=block_id)
            unchanged=nav.unchanged(anchor,observation)
            same=same+1 if unchanged else 0
            if not unchanged:anchor=observation
            if same>=2:break
        else:raise BatchStopped('character_list_edge_unknown')
    elif observation.get('cards'):raise BatchStopped('effective_empty_conflict')
    observation=nav.move(batch,enter_id,page,observation,filter='ALL',block_id=block_id)
    # A completed empty scan stays empty; there is no catalog/old-encounter fallback.
    batch.observation(stamp(source='maa',enter_id=enter_id,block_id=block_id,effective_ids=sorted(ids),
        effective_scan_verified=True,effective_scan_skipped_unsafe=skipped,filter_restored=observation['filter']=='ALL'),
        enter_id=enter_id,block_id=block_id)
    return tuple(sorted(ids))
