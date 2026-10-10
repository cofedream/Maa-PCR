"""Maa same-frame scene runtime adapted from the pinned upstream classifier.

Upstream: wbero/limingjie-assistant, LabyrinthEntryClassifier / EntryRecognitionSession.
Adapted source code: CC BY-NC-SA 4.0. Template imagery keeps its separate rights.
NCC scores are evidence scores, not probabilities. A scene never proves identity,
route reachability, a fresh run or a completed run by itself.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib,json,math,re,time
from maa.custom_recognition import CustomRecognition
from .. import native
from .batch import BatchStopped
from .timing import Timings
from .session import reconnect_pipeline
from .area import area_pipeline

NODE='LabySceneObserved'
NAME='LabyrinthScene'
ASSETS=Path(__file__).resolve().parents[3]/'assets/resource/image/labyrinth'
_OCR={
 'AREA':(r'^[1-5]$',[430,42,30,32]),
 'WIN':(r'(?i)^WIN!?$',[450,0,400,190]),
 'LOSE':(r'(?i)^LOSE!?$',[450,0,400,190]),
 'DEFEAT':(r'^战斗失败$',[450,0,400,190]),
 'NO_REWARD_TITLE':(r'^确认$',[520,165,240,70]),
 'NO_REWARD_BODY':(r'^无法获得报酬[。.]?$',[510,320,270,80]),
 'NEXT':(r'^下一步$',[350,600,890,110]),
 'RETRY':(r'^重新挑战$',[936,623,288,74]),
 'FREE_ROLE_TITLE':(r'^角色选择$',[470,25,360,60]),
 'FREE_ROLE_PROMPT':(r'^邀请选择的角色成为伙伴[。.]?$',[410,85,490,50]),
 'CHEST_ANIMATION':(r'^获得宝箱$',[430,25,420,120]),
 'END':(r'^结束$',[620,600,290,115]),
 'MOVE_TITLE':(r'^移动目标确认$',[490,170,320,55]),
 'MOVE_DESCRIPTION':(r'^将移动到(?:战斗格子[（(](?:普通|极限|极难|BOSS)[）)]|(?:(?:普通|极限|极难|BOSS)战斗|BOSS|首领战|事件|商店|连结印记|连接印记|迷宫遗物|遗物)格子)[。.]?$',[430,365,450,80]),
 'CONFIRM':(r'^确认$',[340,380,700,280]),
 'CANCEL':(r'^取消$',[240,420,400,200]),
 'BOSS':(r'(?i)^.*(?:BOSS|首领战格子).*$',[0,0,750,170]),
 'NORMAL':(r'^.*普通.*$',[0,0,750,170]),
 'EXTREME':(r'^.*(?:极限|极难).*$',[0,0,750,170]),
 'SCORE_DETAIL':(r'^分数明细$',[145,290,220,55]),
 'CLEAR':(r'(?i)^(CONGRATULATIONS!?|恭喜通关[！!]?|迷宫通关[！!]?|攻略成功[！!]?|通关成功[！!]?)$',[230,100,820,490]),
 'TEAM_TITLE':(r'^队伍编组$',[450,20,380,75]),
 'MEMBERS_WORD':(r'^当前的成员$',[60,495,225,50]),
 'START_WORD':(r'^战斗开始$',[1035,565,205,90]),
 'BOSS_MEMBER':(r'^[·◆◇]?队伍[1-3]$',[60,495,225,50]),
 'BOSS_TAB1':(r'^队伍1$',[65,100,130,43]),
 'BOSS_TAB2':(r'^队伍2$',[223,100,130,43]),
 'BOSS_TAB3':(r'^队伍3$',[381,100,130,43]),
 'REST':(r'^.*休息.*$',[280,0,720,230]),
 'REST_BUTTON':(r'^休息$',[370,500,530,190]),
 'TRANSITION':(r'^(前往下一区域|进入下一区域|下一区域)$',[290,400,700,250]),
 'CHEST_TITLE':(r'^宝箱开封结果$',[350,0,590,120]),
 'DATE':(r'^日期变更$',[470,130,350,100]),
 'DISCONNECT':(reconnect_pipeline()['LabyDisconnectCode6002']['expected'],[320,150,650,240]),
 'JOINED':(r'^角色加入$',[400,40,480,180]),
 'JOINED_CLOSE':(r'^关闭$',[380,490,530,190]),
 'ITEM_TITLE':(r'^(获得道具|迷宫遗物效果|遗物效果结果)$',[380,0,540,220]),
 'ITEM_CLOSE':(r'^关闭$',[350,490,590,220]),
 # Reuse the shop anchor ROI for its native text target; chrome is not clickable.
 'SHOP_CLOSE':(r'^关闭$',[947,600,287,77]),
}
# Each term is a required same-frame anchor. Alternative tuples are disjunctions.
_RULES={
 'shop_confirmation':(('SHOP_TITLE','SHOP_PURCHASE_CONFIRMATION_TITLE'),),
 'shop_complete':(('SHOP_TITLE','SHOP_PURCHASE_COMPLETE_TITLE'),),
 'shop_exit_confirmation':(('SHOP_TITLE','SHOP_EXIT_CONFIRMATION_TITLE'),),
 'shop':(('SHOP_TITLE','SHOP_INSTRUCTION','SHOP_CLOSE'),),
 'boss_summary':(('WIN','BATTLE_RESULT_BOSS_SUMMARY_NEXT_BUTTON'),),
 'battle_victory':(('WIN','NEXT'),('WIN','BATTLE_RESULT_NEXT_BUTTON'),('BATTLE_RESULT_REWARD_BANNER','BATTLE_RESULT_NEXT_BUTTON')),
 'battle_defeat':(('LOSE','END'),('LOSE',),('DEFEAT',),('BATTLE_DEFEAT_TITLE',)),
 'final_clear':(('RUN_RESULT_LOGO','RUN_CLEAR_BADGE','RUN_RESULT_CLOSE_BUTTON'),('CLEAR',)),
 'score_result':(('RUN_RESULT_LOGO','RUN_RESULT_CLOSE_BUTTON'),('RUN_RESULT_LOGO','NEXT')),
 'chest':(('RUN_CLEAR_CHEST_RESULT_TITLE','RUN_CLEAR_CHEST_CONFIRM_BUTTON'),('CHEST_TITLE','CONFIRM')),
 'item_reward':(('ITEM_REWARD_TITLE','ITEM_REWARD_INSTRUCTION','ITEM_REWARD_CLOSE'),('RELIC_EFFECT_RESULT_TITLE','RELIC_EFFECT_RESULT_CLOSE'),('ITEM_TITLE','ITEM_CLOSE'),('NO_REWARD_TITLE','NO_REWARD_BODY','CONFIRM')),
 'joined':(('JOINED_HEADER_STANDARD','JOINED_CLOSE_STANDARD'),('JOINED','JOINED_CLOSE')),
 'link_reward':(('LINK_CHOICE_TITLE','LINK_CHOICE_INSTRUCTION','LINK_CHOICE_REMAINING'),),
 'relic_reward':(('LINK_CHOICE_TITLE','RELIC_CHOICE_INSTRUCTION','LINK_CHOICE_REMAINING'),),
 'role_reward':(('ROLE_REWARD_TITLE','ROLE_REWARD_INSTRUCTION'),),
 'free_role_reward':(('FREE_ROLE_TITLE','FREE_ROLE_PROMPT'),('SELECTION_HEADER_STANDARD','INVITE_DISABLED_STANDARD'),('SELECTION_HEADER_STANDARD','INVITE_ENABLED_STANDARD')),
 # Five upstream final templates are optional and absent in this pinned asset pack.
 # Their named routes remain available when calibrated anchors are supplied.
 'clear_congratulations':(('RUN_CLEAR_CONGRATULATIONS_TITLE',),),
 'clear_character_summary':(('RUN_CLEAR_CHARACTER_SUMMARY_TITLE','NEXT'),),
 'clear_reward_animation':(('RUN_CLEAR_REWARD_ANIMATION_TITLE',),),
 'clear_chest_animation':(('RUN_CLEAR_CHEST_ANIMATION_TITLE',),('CHEST_ANIMATION',)),
 'event':(('EVENT_CHOICE_TITLE','EVENT_CHOICE_INSTRUCTION'),('EVENT_SINGLE_CHOICE_TRIGGER_TITLE','EVENT_SINGLE_CHOICE_SELECT_BUTTON')),
 'event_animation':(('EVENT_ANIMATION_TITLE','EVENT_ANIMATION_SKIP'),),
 'team':(('BATTLE_TEAM_HEADER','BATTLE_TEAM_CURRENT_MEMBERS_SUMMARY','BATTLE_TEAM_START_BUTTON','BATTLE_TEAM_CANCEL_BUTTON','BATTLE_TEAM_FILTER_BAR'),('TEAM_TITLE','MEMBERS_WORD','START_WORD'),('TEAM_TITLE','BOSS_MEMBER','BOSS_TAB1','BOSS_TAB2','BOSS_TAB3')),
 'battle':(('BATTLE_IN_PROGRESS_MENU_BUTTON',),),
 'move_confirmation':(('MOVE_TITLE','MOVE_DESCRIPTION','CONFIRM','CANCEL'),),
 'normal_challenge':(('BATTLE_CHALLENGE_BUTTON','NORMAL'),('BATTLE_CHALLENGE_BUTTON','BATTLE_CHALLENGE_SUFFIX_NORMAL')),
 'ex_challenge':(('BATTLE_CHALLENGE_BUTTON','EXTREME'),('BATTLE_CHALLENGE_BUTTON','BATTLE_CHALLENGE_SUFFIX_EXTREME')),
 'boss_challenge':(('BATTLE_CHALLENGE_BUTTON','BOSS'),),
 'rest':(('REST','REST_BUTTON'),),
 'area_transition':(('TRANSITION',),),
 'map':(('NODE_HEADER_STANDARD','NODE_CHARACTERS_STANDARD','NODE_RETREAT_STANDARD'),
        ('NODE_HEADER_STANDARD','NODE_CHARACTERS_STANDARD','NODE_RETURN_STANDARD'),
        ('NODE_HEADER_STANDARD','NODE_RELICS_STANDARD','NODE_RETREAT_STANDARD'),
        ('NODE_HEADER_STANDARD','NODE_RELICS_STANDARD','NODE_RETURN_STANDARD')),
 'home':(('DAWN_TICKET_LABEL_STANDARD','DAWN_ACTIVE_STANDARD'),('DAWN_TICKET_LABEL_STANDARD','DAWN_START_STANDARD')),
}
_ACTIONS={
 'advance_character_animation':(frozenset(('event_animation','unknown')),'EVENT_ANIMATION_SKIP'),
 'advance_final_unknown':(frozenset(('unknown',)),'advance'),
 'retry_battle':(frozenset(('battle_defeat',)),'RETRY'),
 'advance_final_animation':(frozenset(('clear_congratulations','clear_reward_animation','clear_chest_animation')),'advance'),
 'challenge':(frozenset(('normal_challenge','ex_challenge','boss_challenge')),'BATTLE_CHALLENGE_BUTTON'),
 'start_battle':(frozenset(('team',)),'START_WORD'),
 'confirm_move':(frozenset(('move_confirmation',)),'CONFIRM'),
 'close_reward':(frozenset(('item_reward','joined','shop_complete','chest')),'close'),
 'leave_shop':(frozenset(('shop',)),'SHOP_CLOSE'),
 'confirm_shop_exit':(frozenset(('shop_exit_confirmation',)),'SHOP_EXIT_CONFIRM_BUTTON'),
 'settlement_next':(frozenset(('battle_victory','boss_summary','final_clear','score_result','chest','clear_character_summary')),'next'),
 'back_to_map':(frozenset(('normal_challenge','ex_challenge','boss_challenge')),'BATTLE_CHALLENGE_VIEW_MAP_BUTTON'),
 'next_area':(frozenset(('area_transition',)),'TRANSITION'),
 'rest':(frozenset(('rest',)),'REST_BUTTON'),
 'skip_event_animation':(frozenset(('event_animation',)),'EVENT_ANIMATION_SKIP'),
}

@dataclass(frozen=True)
class Scene:
    kind:str
    native:dict
    payload:dict

def _box(v):
    return type(v) is list and len(v)==4 and all(type(n) is int for n in v) and v[0]>=0 and v[1]>=0 and v[2]>0 and v[3]>0 and v[0]+v[2]<=1280 and v[1]+v[3]<=720

def _score(v):
    if type(v) not in (int,float):return False
    try:return math.isfinite(v) and 0<=v<=1
    except (OverflowError,ValueError):return False

def _anchors():
    value=json.loads((ASSETS/'scene-anchors.json').read_text(encoding='utf-8'))
    if type(value) is not dict or not value:raise BatchStopped('scene_resources_unavailable')
    return value

def scene_pipeline():
    from .event_observation import event_pipeline
    nodes=event_pipeline()
    for key,spec in _anchors().items():
        for i,roi in enumerate(spec['rois']):
            nodes[f'LabySceneTemplate_{key}_{i}']=dict(recognition='TemplateMatch',template=spec['template'],roi=roi,method=5,threshold=.70,count=1,action='DoNothing',next=[],pre_delay=0,post_delay=0)
    for key,(pattern,roi) in _OCR.items():
        nodes['LabySceneWord_'+key]=dict(recognition='OCR',expected=pattern,roi=roi,threshold=.8,action='DoNothing',next=[],pre_delay=0,post_delay=0)
    nodes['LabySceneWord_AREA']['only_rec']=area_pipeline()['LabyAreaCurrent']['only_rec']
    nodes[NODE]=dict(recognition='Custom',custom_recognition=NAME,roi=[0,0,1280,720],action='DoNothing',next=[],timeout=10000,rate_limit=250,pre_delay=0,post_delay=0)
    return nodes

def _primitive(detail,key):
    if detail is None or detail.hit is not True:return None
    raw=detail.raw_detail
    if type(raw) is str:
        try:raw=json.loads(raw)
        except (TypeError,ValueError):return None
    words=raw.get('filtered') if type(raw) is dict else None
    if type(words) is not list or len(words)!=1 or type(words[0]) is not dict:return None
    word=words[0];box=word.get('box');score=word.get('score')
    if not _box(box) or not _score(score):return None
    return dict(anchor=key,box=list(box),score=score,text=word.get('text'),native=raw,reco_id=detail.reco_id)

def classify_scene(anchors):
    scores={k:v['score'] for k,v in anchors.items()}
    if scores.get('DATE',0)>=.8:return 'date_changed',.8
    if scores.get('DISCONNECT',0)>=.8:return 'disconnected',.8
    ranked=[]
    for kind,alternatives in _RULES.items():
        score=max(min(scores.get(key,0) for key in terms) for terms in alternatives)
        if score>=.70:ranked.append((score,kind))
    # Dedicated modal / terminal evidence overrides its visible background.
    for kind in ('final_clear','battle_defeat','shop_confirmation','shop_complete','shop_exit_confirmation','move_confirmation','joined','item_reward','chest','free_role_reward','clear_character_summary','clear_congratulations','clear_reward_animation','clear_chest_animation','boss_summary'):
        match=next((r for r in ranked if r[1]==kind),None)
        if match:return kind,match[0]
    ranked.sort(reverse=True)
    # The pinned upstream treats the unique RESULT logo as decisive at 0.92.
    # Keep the explicit final-clear combination above: this logo alone is a
    # result page, never independent proof that the run cleared.
    logo=scores.get('RUN_RESULT_LOGO',0)
    if logo>=.92 and (not ranked or ranked[0][1] in ('map','score_result')):return 'score_result',logo
    if not ranked:return 'unknown',0
    if len(ranked)>1 and ranked[0][0]-ranked[1][0]<.03:
        # The upstream uses the pink link statue only as a discriminator.
        if {ranked[0][1],ranked[1][1]}=={'link_reward','relic_reward'} and scores.get('LINK_CHOICE_CARD_SIGNATURE',0)>=.70:return 'link_reward',next(r[0] for r in ranked if r[1]=='link_reward')
        return 'unknown',ranked[0][0]
    return ranked[0][1],ranked[0][0]

def _controls(kind,anchors):
    controls=dict(anchors)
    for logical,keys in {
      'close':(('CONFIRM',) if kind in ('shop_complete','chest') or 'NO_REWARD_BODY' in anchors else ())+('ITEM_CLOSE','ITEM_REWARD_CLOSE','RELIC_EFFECT_RESULT_CLOSE','JOINED_CLOSE','JOINED_CLOSE_STANDARD','RUN_CLEAR_CHEST_CONFIRM_BUTTON'),
      'next':(('RUN_RESULT_CLOSE_BUTTON',) if kind=='final_clear' else ())+('NEXT','BATTLE_RESULT_BOSS_SUMMARY_NEXT_BUTTON','BATTLE_RESULT_NEXT_BUTTON','RUN_CLEAR_NEXT_BUTTON','RUN_RESULT_CLOSE_BUTTON','RUN_CLEAR_CHEST_CONFIRM_BUTTON'),
    }.items():
        keys=[k for k in keys if k in anchors]
        if keys:controls[logical]=anchors[keys[0]]
    if kind in ('clear_congratulations','clear_reward_animation','clear_chest_animation'):
        keys={'clear_congratulations':('RUN_CLEAR_CONGRATULATIONS_TITLE',),
            'clear_reward_animation':('RUN_CLEAR_REWARD_ANIMATION_TITLE',),
            'clear_chest_animation':('RUN_CLEAR_CHEST_ANIMATION_TITLE','CHEST_ANIMATION')}[kind]
        found=next((anchors[k] for k in keys if k in anchors),None)
        if found is not None:controls['advance']=found
    return controls

def _probe_scope(kinds):
    if kinds is None:return None
    kinds={kinds} if isinstance(kinds,str) else set(kinds)
    return sorted((kinds & set(_RULES)) | {'battle','team','map','move_confirmation','home'})

class SceneRecognition(CustomRecognition):
    def __init__(self,stopped=None):
        super().__init__();self.stopped=stopped;self.specs=_anchors()
        from .event_observation import EventRecognition
        self.event=EventRecognition(stopped)

    def analyze(self,context,argv):
        if self.stopped is not None and self.stopped():return self.AnalyzeResult(None,dict(kind='unknown',reason='stopped'))
        if tuple(argv.image.shape[:2])!=(720,1280):return self.AnalyzeResult(None,dict(kind='unknown',reason='frame_size_unknown'))
        guard=json.loads(argv.custom_recognition_param or '{}') if type(argv.custom_recognition_param) is str else (argv.custom_recognition_param or {})
        if type(guard) is not dict:guard={}
        scope=_probe_scope(guard.get('probe_kinds'))
        anchors={};scanned=set()
        def read(key):
            if key in scanned:return anchors.get(key)
            scanned.add(key)
            if key in self.specs:
                spec=self.specs[key]
                matches=[_primitive(context.run_recognition(f'LabySceneTemplate_{key}_{i}',argv.image),key) for i in range(len(spec['rois']))]
                matches=[v for v in matches if v is not None]
                if matches:anchors[key]=max(matches,key=lambda v:v['score'])
            if key in _OCR:
                word=_primitive(context.run_recognition('LabySceneWord_'+key,argv.image),key)
                if word is not None:anchors[key]=word
            return anchors.get(key)
        if scope is None:
            for key in self.specs:read(key)
            for key in _OCR:read(key)
        else:
            read('DATE');read('DISCONNECT')
            for page in scope:
                for terms in _RULES[page]:
                    # The existing And rule is false as soon as one required anchor misses.
                    # Keep every alternative and original native threshold/ROI; no pixel gate.
                    for key in terms:
                        found=read(key)
                        if found is None or found['score']<.70:break
            if 'link_reward' in scope or 'relic_reward' in scope:read('LINK_CHOICE_CARD_SIGNATURE')
        if type(guard.get('control_anchor')) is str:read(guard['control_anchor'])
        kind,score=classify_scene(anchors)
        if scope is not None and kind=='map':read('AREA')
        event=None
        if kind in ('unknown','event') and (scope is None or 'event' in scope):
            result=self.event.analyze(context,argv)
            if result.box is not None:
                event=result.detail;kind='event';score=event['score']
        # Read optional registered controls on the identified page before availability checks.
        for kinds,key in _ACTIONS.values():
            if kind in kinds and (key in self.specs or key in _OCR):read(key)
        payload=dict(anchors=anchors,controls=_controls(kind,anchors),score=score,
            frame_pixel_sha256=hashlib.sha256(argv.image.tobytes()).hexdigest(),same_frame=True,
            source='maa',complete_map=False,clear_verified=False,
            probe_kinds=scope if scope is not None else sorted(_RULES),scanned_anchor_keys=sorted(scanned),
            global_scene_complete=scope is None or set(scope)==set(_RULES),
            recognition_scope='full_scene' if scope is None else 'owned_transition',
            score_method='Maa TemplateMatch method 5 NCC / OCR; not probability')
        if event is not None:payload['event']=event
        if kind=='team' and all(k in anchors for k in ('BOSS_MEMBER','BOSS_TAB1','BOSS_TAB2','BOSS_TAB3')):
            payload['team_mode']='boss';payload['member_label']=anchors['BOSS_MEMBER']['text'].lstrip('·◆◇');payload['member_label_raw']=anchors['BOSS_MEMBER']['text']
        if 'AREA' in anchors and re.fullmatch('[1-5]',anchors['AREA'].get('text','')):payload['area']=int(anchors['AREA']['text'])
        if kind=='move_confirmation':
            text=anchors['MOVE_DESCRIPTION'].get('text','')
            payload['node_type_text']=text
            types={'普通':2,'极限':3,'极难':3,'BOSS':8,'首领':8,'事件':5,'商店':7,'连结':4,'连接':4,'印记':4,'迷宫遗物':6,'遗物':6}
            hits={v for k,v in types.items() if k in text}
            payload['node_type']=next(iter(hits)) if len(hits)==1 else None
        detail=dict(kind=kind,payload=payload)
        if guard.get('control_logical') is not None:
            actual=payload['controls'].get(guard['control_logical'])
            if type(actual) is not dict:return self.AnalyzeResult(None,detail)
        if guard.get('final_settlement') and any(k in anchors for k in
                ('BATTLE_IN_PROGRESS_MENU_BUTTON','BATTLE_RESULT_BOSS_SUMMARY_NEXT_BUTTON','BATTLE_RESULT_NEXT_BUTTON')):
            return self.AnalyzeResult(None,detail)
        if guard.get('node_type') is not None and payload.get('node_type')!=guard['node_type']:return self.AnalyzeResult(None,detail)
        if guard.get('kind') and kind!=guard['kind']:return self.AnalyzeResult(None,detail)
        return self.AnalyzeResult([0,0,1280,720],detail)

def install_scene(resource,*,stopped=None):
    # Maa fixed callback names are registered once per Owner Resource.
    previous=getattr(resource,'_labyrinth_scene_observer',None)
    if isinstance(previous,SceneRecognition):
        if stopped is not None:previous.stopped=stopped;previous.event.stopped=stopped
        return previous
    observer=SceneRecognition(stopped)
    if not resource.override_pipeline(scene_pipeline()) or not resource.register_custom_recognition(NAME,observer):raise BatchStopped('scene_install_failed')
    resource._labyrinth_scene_observer=observer
    return observer

def _checkpoint(tasker,stopped):
    if (stopped is not None and stopped()) or getattr(tasker,'stopping',False):raise BatchStopped('stopped')

def scene_readback(receipt):
    if type(receipt) is not dict or receipt.get('native_ok') is not True or receipt.get('tasker_inited') is not True or type(receipt.get('task_id')) is not int or receipt['task_id']<=0:raise BatchStopped('scene_unknown')
    nodes=receipt.get('nodes')
    if type(nodes) is not list or len(nodes)!=1 or nodes[0].get('name')!=NODE or nodes[0].get('completed') is not True:raise BatchStopped('scene_unknown')
    raw=nodes[0].get('recognition')
    from .autoplay import _detail
    raw=_detail(raw)
    if type(raw) is not dict or type(raw.get('kind')) is not str or type(raw.get('payload')) is not dict:raise BatchStopped('scene_unknown')
    kind=raw['kind'];payload=raw['payload']
    if kind not in _RULES and kind not in ('unknown','date_changed','disconnected'):raise BatchStopped('scene_unknown')
    return Scene(kind,receipt,payload)

def observe_scene(tasker,*,stopped=None,timings=None,timeout=10,probe_kinds=None):
    _checkpoint(tasker,stopped)
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=90:raise ValueError('scene_timeout_invalid')
    timings=timings or Timings()
    token=native.RUN_DEADLINE.set(min(native.RUN_DEADLINE.get(),time.monotonic()+timeout))
    previous=None;scope=_probe_scope(probe_kinds);resource=runtime_resource(tasker) if scope is not None else None
    try:
        if scope is not None:
            previous=dict(resource.get_node_data(NODE))
            recognition=previous['recognition']
            scoped=dict(previous,recognition=dict(recognition,param=dict(recognition['param'],custom_recognition_param=dict(probe_kinds=scope))))
            if not resource.override_pipeline({NODE:scoped}):raise BatchStopped('scene_probe_install_failed')
        with timings.measure('page_wait'):receipt=native.run(tasker,NODE)
        _checkpoint(tasker,stopped);return scene_readback(receipt)
    finally:
        native.RUN_DEADLINE.reset(token)
        if previous is not None and not resource.override_pipeline({NODE:previous}):raise BatchStopped('scene_probe_restore_failed')

def perform_scene_action(tasker,scene,action,*,stopped=None,timings=None,target=None):
    _checkpoint(tasker,stopped)
    if type(scene) is not Scene or action not in _ACTIONS or scene.kind not in _ACTIONS[action][0]:raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='scene_action_unknown'))
    if action in ('advance_character_animation','advance_final_unknown'):
        # Owned animation drivers retain the actual UNKNOWN classifier result.
        # Pinned CENTER(960,780) / BOTTOM_RIGHT(1640,990), without inventing an anchor.
        final=action=='advance_final_unknown'
        if final and target not in ([640,520,1,1],[1093,660,1,1]):
            raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='scene_action_target_unknown'))
        # The caller owns this bounded animation phase. Pinned upstream advances
        # its safe point even if the next known page appears before this input.
        nodes={'LabySceneActionOnce':dict(recognition='DirectHit',action='Click',
            target=target if final else [640,520,1,1],max_hit=1,next=[],timeout=3000,pre_delay=0,post_delay=0)}
        if not tasker.resource.override_pipeline(nodes):raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='scene_action_install_failed'))
        with (timings or Timings()).measure('page_wait'):receipt=native.run(tasker,'LabySceneActionOnce',reset_hits=('LabySceneActionOnce',))
        _checkpoint(tasker,stopped)
        found=next((n for n in receipt.get('nodes',[]) if n.get('name')=='LabySceneActionOnce' and n.get('completed') is True),None)
        if receipt.get('native_ok') is not True or found is None or found.get('action',{}).get('success') is not True:raise BatchStopped('scene_input_unknown')
        return receipt
    control=scene.payload.get('controls',{}).get(_ACTIONS[action][1])
    if type(control) is not dict or not _box(control.get('box')) or not _score(control.get('score')):raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='scene_action_control_unknown'))
    box=control['box'];key=control['anchor'];nodes={};guard_kind=scene.kind
    # A Boss result can remain globally UNKNOWN while its dedicated/legacy
    # next-button template is reliable. Autoplay creates this transient typed
    # scene only after an owned Boss start; keep the fresh action guard on the
    # actual UNKNOWN classifier result and the current control anchor.
    if (scene.kind=='boss_summary'
            and scene.payload.get('same_frame') is True and scene.payload.get('source')=='maa'
            and classify_scene(scene.payload.get('anchors',{}))[0]=='unknown'):
        guard_kind='unknown'
    if action=='challenge':
        # Reuse the existing native text control; the template includes chrome
        # outside the clickable button and must not supply its random target.
        nodes['LabySceneActionControl']=dict(tasker.resource.get_node_data('LabyNormalChallengeButton'))
    elif key=='RUN_RESULT_CLOSE_BUTTON':
        # The result template includes a non-clickable border; reuse the
        # existing native close text target for CLEAR and score-result pages.
        pattern,roi=_OCR['ITEM_CLOSE']
        nodes['LabySceneActionControl']=dict(recognition='OCR',expected=pattern,roi=roi,threshold=.8,action='DoNothing',next=[])
    elif key in _OCR:
        # OCR detection boxes tightly enclose the glyphs; they are not a
        # usable detector ROI. Reuse the calibrated ROI; the native And node
        # clicks its current OCR match after the page and anchor are checked.
        pattern,roi=_OCR[key];nodes['LabySceneActionControl']=dict(recognition='OCR',expected=pattern,roi=roi,threshold=.8,action='DoNothing',next=[])
    else:
        spec=_anchors().get(key)
        if spec is None:raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='scene_action_control_unknown'))
        nodes['LabySceneActionControl']=dict(recognition='Or',
            any_of=[f'LabySceneTemplate_{key}_{i}' for i in range(len(spec['rois']))],
            action='DoNothing',next=[])
    nodes['LabySceneActionGuard']=dict(recognition='Custom',custom_recognition=NAME,custom_recognition_param=dict(kind=guard_kind,control_logical=_ACTIONS[action][1],control_anchor=key,**({'probe_kinds':scene.payload['probe_kinds']} if scene.payload.get('probe_kinds') is not None else {}),**({'node_type':scene.payload['node_type']} if action=='confirm_move' and scene.payload.get('node_type') is not None else {})),roi=[0,0,1280,720],action='DoNothing',next=[])
    nodes['LabySceneActionOnce']=dict(recognition='And',all_of=['LabySceneActionControl','LabySceneActionGuard'],action='Click',target=([640,360,1,1] if action=='advance_character_animation' else [640,360,1,1] if action=='advance_final_animation'
            and scene.kind!='clear_reward_animation' else [640,645,1,1] if action=='advance_final_animation' else True),
        max_hit=1,next=[],timeout=3000,pre_delay=0,post_delay=0)
    if not tasker.resource.override_pipeline(nodes):raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='scene_action_install_failed'))
    timings=timings or Timings()
    with timings.measure('page_wait'):receipt=native.run(tasker,'LabySceneActionOnce',reset_hits=('LabySceneActionOnce',))
    _checkpoint(tasker,stopped)
    found=next((n for n in receipt.get('nodes',[]) if n.get('name')=='LabySceneActionOnce' and n.get('completed') is True),None)
    if receipt.get('native_ok') is not True or found is None or found.get('action',{}).get('success') is not True:raise BatchStopped('scene_input_unknown')
    return receipt

def runtime_resource(tasker):
    # Direct Maa getters create new wrapper objects; bind retains the Owner.
    resource=getattr(tasker,'_resource_holder',None) or getattr(tasker,'_labyrinth_scene_resource',None)
    if resource is None:
        resource=tasker.resource;tasker._labyrinth_scene_resource=resource
    return resource

class MaaSceneRuntime:
    def __init__(self,tasker,stamp=None,*,timings=None,stopped=None):
        self.tasker=tasker;self.stamp=stamp;self.timings=timings or Timings();self.stopped=stopped
        self.observer=install_scene(runtime_resource(tasker),stopped=stopped)
    def observe(self,*,timeout=10,probe_kinds=None):return observe_scene(self.tasker,stopped=self.stopped,timings=self.timings,timeout=timeout,probe_kinds=probe_kinds if probe_kinds is not None else getattr(self,'_scene_probe_kinds',None))
    def perform(self,scene,action,*,target=None):return perform_scene_action(self.tasker,scene,action,stopped=self.stopped,timings=self.timings,target=target)
    def wait_for(self,batch,allowed,*,source=None,accept=None,timeout=30,reason='scene_readback_unknown',acquisition_fields=None,final_fields=None):
        """Business wait with independent owned acquisition and final-settlement drivers."""
        from .autoplay import _poll_pause, _native_ok, _owned_boss_summary_scene
        sources={source} if isinstance(source,str) else set(source or ())
        deadline=min(native.RUN_DEADLINE.get(),time.monotonic()+timeout)
        token=native.RUN_DEADLINE.set(deadline);acquisition_clicks=0;last_acquisition=-float('inf')
        def advance_owned(operation,fields,send):
            try:
                batch.transaction(operation,fields,send)
                return True
            except BatchStopped as error:
                cause=error.__cause__
                receipt=cause.receipt if isinstance(cause,native.NativeInputNotSent) else {}
                proof=receipt.get('native_evidence',{})
                # A fresh page can replace the owned animation before its guard runs.
                # Only affirmative no-input evidence permits another pure observation.
                if (error.args==('native_input_not_sent',)
                        and isinstance(cause,native.NativeInputNotSent)
                        and receipt.get('input_execution')=='not_sent'
                        and receipt.get('pre_input_validation_failed') is not True
                        and proof.get('entry')=='LabySceneActionOnce'
                        and proof.get('recognition_failed') is True
                        and proof.get('action_started') is False):return False
                raise
        previous_probe=getattr(self,'_scene_probe_kinds',None)
        self._scene_probe_kinds=_probe_scope(set(allowed)|sources|({'boss_summary','battle_victory'} if final_fields is not None else set()))
        try:
            while True:
                batch.checkpoint();remaining=deadline-time.monotonic()
                if remaining<=0:raise BatchStopped(reason)
                after=self.observe(timeout=min(timeout,remaining));batch.checkpoint()
                if time.monotonic()>=deadline:raise BatchStopped(reason)
                _native_ok(after.native)
                if after.kind in ('date_changed','disconnected'):raise BatchStopped(after.kind)
                # An owned summon may expose MAP before its actual JOINED popup.
                if acquisition_fields is not None and final_fields is None and after.kind=='map' and 'map' not in allowed:
                    _poll_pause(self.tasker,batch,'LabySceneTransitionPollSpacing',reason);continue
                # A recognized background with its business field still absent is
                # pending, not a changed area. Never copy the previous area into it.
                # An owned final phase keeps MAP as settlement background under this deadline.
                partial_map=after.kind=='map' and (final_fields is not None or type(after.payload.get('area')) is not int)
                if final_fields is not None and after.kind=='unknown':
                    recovered=_owned_boss_summary_scene(after,final_fields)
                    if recovered is not None:after=recovered
                    elif 'BATTLE_IN_PROGRESS_MENU_BUTTON' not in after.payload.get('anchors',{}):
                        phase=final_fields['settlement_phase'];key=(final_fields['enter_id'],phase)
                        previous=getattr(self,'_final_settlement_progress',(None,0,-float('inf')))
                        count,last=previous[1:] if previous[0]==key else (0,-float('inf'))
                        if count>=40:raise BatchStopped('final_settlement_click_limit')
                        if time.monotonic()-last>=1.5:
                            right=(count%4 in (1,3) if phase=='before_score' else count>=3)
                            target=[1093,660,1,1] if right else [640,520,1,1]
                            def advance_final(oid):
                                batch.checkpoint();sent=self.perform(after,'advance_final_unknown',target=target);_native_ok(sent,action=True)
                                return self.stamp(operation_id=oid,**final_fields,native=sent,input_native=sent,input_action_verified=True)
                            if advance_owned('advance_final_unknown',final_fields,advance_final):
                                self._final_settlement_progress=(key,count+1,time.monotonic())
                        _poll_pause(self.tasker,batch,'LabySceneTransitionPollSpacing',reason);continue
                if acquisition_fields is not None and final_fields is None and after.kind in ('unknown','event_animation'):
                    if acquisition_clicks>=40:raise BatchStopped('character_acquisition_click_limit')
                    if time.monotonic()-last_acquisition>=.65:
                        def advance(oid):
                            batch.checkpoint();sent=self.perform(after,'advance_character_animation');_native_ok(sent,action=True)
                            return self.stamp(operation_id=oid,**acquisition_fields,native=sent,input_native=sent,input_action_verified=True)
                        if advance_owned('advance_character_animation',acquisition_fields,advance):
                            acquisition_clicks+=1;last_acquisition=time.monotonic()
                    _poll_pause(self.tasker,batch,'LabySceneTransitionPollSpacing',reason);continue

                if after.kind in allowed and not partial_map and (accept is None or accept(after)):
                    if acquisition_fields is not None:
                        after=Scene(after.kind,after.native,dict(after.payload,acquisition_driver_advanced=acquisition_clicks>0))
                    return after
                if after.kind not in allowed and after.kind not in sources|{'unknown'}:raise BatchStopped(reason)
                _poll_pause(self.tasker,batch,'LabySceneTransitionPollSpacing',reason)
        except RuntimeError as error:
            if error.args not in (('Native task exceeded 90 seconds or inspection deadline',),
                    ('Inspection deadline exceeded before task input',),
                    ('Context deadline exceeded after synchronous return; outcome unknown',)):raise
            batch.checkpoint();raise BatchStopped(reason) from error
        finally:
            native.RUN_DEADLINE.reset(token);self._scene_probe_kinds=previous_probe
    def close(self):self.tasker=None;self.observer=None
