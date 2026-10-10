"""Visible-roster team editing using same-frame Maa template recognition.

The owner loads qualified icon templates and registers TeamRecognition once.
All screenshots and matches are Maa operations; Python computes the roster
change. Hidden/paginated or ambiguous identities stop before input.
"""
import json,re
from maa.custom_recognition import CustomRecognition
from .. import native
from .batch import BatchStopped
from .roles import Context,load_optimizer
from .timing import Timings

NAME='LabyrinthTeamRecognition'

def team_pipeline(templates):
    if not 5<=len(templates)<=24 or any(not re.fullmatch(r'\d{4}',uid) or not isinstance(regions,dict) or set(regions)!= {'top','bottom'} or any(not files or not all(isinstance(f,str) and f.endswith('.png') and '/' not in f and '\\' not in f for f in files) for files in regions.values()) for uid,regions in templates.items()):raise ValueError('qualified_team_templates_required')
    nodes={}
    for uid,regions in templates.items():
        for region,roi in (('Top',[60,150,1120,335]),('Bottom',[60,535,720,140])):
            nodes[f'LabyDynamic{region}{uid}']=dict(recognition='TemplateMatch',template=list(regions[region.lower()]),roi=roi,threshold=.8,action='DoNothing',next=[],timeout=1500,pre_delay=0,post_delay=0)
    nodes['LabyDynamicTeamObserved']=dict(recognition='Custom',custom_recognition=NAME,custom_recognition_param=dict(mode='read'),action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0)
    # No target is accepted until a transaction supplies an exact fresh roster.
    nodes['LabyDynamicBattleStartOnce']=dict(recognition='Custom',custom_recognition=NAME,custom_recognition_param=dict(mode='unqualified'),action='Click',target=True,max_hit=1,next=[],timeout=3000,rate_limit=250)
    nodes['LabyDynamicTeamToggleOnce']=dict(recognition='Custom',custom_recognition=NAME,custom_recognition_param=dict(mode='unqualified'),action='Click',target=True,max_hit=1,next=[],timeout=3000,rate_limit=250)
    return nodes

class TeamRecognition(CustomRecognition):
    def __init__(self,roster,*,stopped=lambda:False):
        super().__init__();self.roster=tuple(roster);self.stopped=stopped
    def analyze(self,context,argv):
        try:
            params=json.loads(argv.custom_recognition_param)
            if params.get('mode') not in ('read','toggle','start') or self.stopped() or context.tasker.stopping:return None
            def reco(node):
                if self.stopped() or context.tasker.stopping:raise BatchStopped('stopped')
                result=context.run_recognition(node,argv.image)
                if result is None:raise BatchStopped('team_recognition_unavailable')
                return result
            if any(not reco(node).hit for node in ('LabyTeamTitle','LabyTeamMembersLabel')):return None
            top={};bottom={}
            for uid in self.roster:
                found=reco('LabyDynamicBottom'+uid)
                if found.hit:
                    if found.box is None:return None
                    bottom[uid]=list(found.box)
                else:
                    found=reco('LabyDynamicTop'+uid)
                    if not found.hit or found.box is None:return None
                    top[uid]=list(found.box)
            # Two identities pointing to the same portrait are never accepted.
            for boxes in (top,bottom):
                values=list(boxes.values())
                if any(abs(a[0]+a[2]/2-b[0]-b[2]/2)<50 and abs(a[1]+a[3]/2-b[1]-b[3]/2)<50 for i,a in enumerate(values) for b in values[i+1:]):return None
            if len(bottom)>5:return None
            selected=sorted(bottom);detail=dict(available=list(self.roster),selected=selected,top_boxes=top,bottom_boxes=bottom)
            box=[0,0,1,1]
            if params['mode']=='toggle':
                role=params.get('role_id');expected=params.get('expected_selected')
                if role not in self.roster or not isinstance(expected,list) or expected!=selected:return None
                if role not in bottom and len(bottom)>=5:return None
                box=bottom[role] if role in bottom else top[role]
            if params['mode']=='start':
                if params.get('expected_selected')!=selected or len(selected)!=5:return None
                button=reco('LabyTeamBattleStartLabel')
                if not button.hit or button.box is None or not reco('LabyTeamBattleStartBright').hit:return None
                box=list(button.box)
            if self.stopped() or context.tasker.stopping:return None
            return self.AnalyzeResult(box=box,detail=detail)
        except Exception:return None

class MaaTeam:
    def __init__(self,tasker,stamp,roster=None,*,optimizer=None,timings=None,characters=None,failed_teams=(),retry_number=0,last_failed_team=None):
        self.failed_teams=tuple(tuple(sorted(team)) for team in failed_teams);self.retry_number=retry_number;self.last_failed_team=last_failed_team
        self.tasker,self.stamp,self.roster=tasker,stamp,tuple(roster or ())
        self._characters=characters;self._optimizer_supplied=optimizer is not None;self.pages={};self.boss_teams={};self.boss_all_teams={}
        self.optimizer=optimizer or load_optimizer();self.timings=timings or Timings();self.attempted=set();self.prepared={};self.started=set()
    def _read(self):
        with self.timings.measure('page_wait'):receipt=native.run(self.tasker,'LabyDynamicTeamObserved')
        if not receipt['native_ok']:raise BatchStopped('team_readback_unknown')
        raw=next((n['recognition'] for n in receipt['nodes'] if n['name']=='LabyDynamicTeamObserved' and n['completed']),None)
        value=raw.get('best',{}).get('detail') if isinstance(raw,dict) else None
        if not isinstance(value,dict) or value.get('available')!=list(self.roster) or not isinstance(value.get('selected'),list):raise BatchStopped('team_readback_unknown')
        selected=value['selected']
        if selected!=sorted(set(selected)) or not set(selected)<=set(self.roster) or len(selected)>5:raise BatchStopped('team_readback_unknown')
        return selected,receipt
    def _prepare_legacy(self,batch,enter_id,block_id,context=Context()):
        if type(enter_id) is not int or enter_id<=0 or type(block_id) is not int or block_id<=0:raise BatchStopped('team_identity_required')
        key=(enter_id,block_id)
        if key in self.attempted:raise BatchStopped('team_already_attempted')
        batch.checkpoint()
        with self.timings.measure('strategy'):
            plan=(self.optimizer.retry_best(self.roster,context,failed_teams=self.failed_teams,
                retry_number=self.retry_number,last_failed_team=self.last_failed_team) if self.retry_number
                else self.optimizer.best(self.roster,context,battle=True))
        if plan is None or len(plan.members)!=5:raise BatchStopped('team_strategy_unknown')
        desired=sorted(plan.members);selected,receipt=self._read();self.attempted.add(key)
        for role in sorted(set(selected)-set(desired))+sorted(set(desired)-set(selected)):
            before=list(selected);after=sorted(set(before)^{role});fields=dict(enter_id=enter_id,block_id=block_id,role_id=role,selected=role in after)
            def send(oid,before=before,after=after,role=role,fields=fields):
                batch.checkpoint();resource=self.tasker.resource;node='LabyDynamicTeamToggleOnce';original=resource.get_node_data(node)
                if not resource.override_pipeline({node:dict(custom_recognition_param=dict(mode='toggle',role_id=role,expected_selected=before))}):raise BatchStopped('team_guard_failed')
                try:
                    with self.timings.measure('page_wait'):result=native.run(self.tasker,node,reset_hits=(node,))
                    if not result['native_ok'] or not any(n['name']==node and n.get('completed') and n.get('action',{}).get('success') for n in result.get('nodes',())):raise BatchStopped('team_input_unknown')
                finally:
                    if not resource.override_pipeline({node:original}):raise BatchStopped('team_guard_restore_failed')
                return self.stamp(operation_id=oid,**fields,native=result,input_native=result,input_action_verified=True)
            clicked=batch.transaction('team_toggle',fields,send)
            batch.checkpoint();actual,readback=self._read()
            if actual!=after:raise BatchStopped('team_readback_unknown')
            batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,native=readback,input_native=clicked['input_native'],selection_verified=True),**fields)
            selected=after
        if selected!=desired:raise BatchStopped('team_readback_unknown')
        batch.checkpoint();selected,receipt=self._read()
        if selected!=desired:raise BatchStopped('team_readback_unknown')
        self.prepared[key]=desired
        return self.stamp(enter_id=enter_id,block_id=block_id,team=desired,native=receipt)
    def _start_legacy(self,batch,enter_id,block_id,read_result):
        key=(enter_id,block_id);desired=self.prepared.get(key)
        if desired is None:raise BatchStopped('team_not_prepared')
        if key in self.started:raise BatchStopped('battle_already_attempted')
        self.started.add(key);fields=dict(enter_id=enter_id,block_id=block_id,team=desired)
        def send(oid):
            batch.checkpoint();resource=self.tasker.resource;node='LabyDynamicBattleStartOnce';original=resource.get_node_data(node)
            if not resource.override_pipeline({node:dict(custom_recognition_param=dict(mode='start',expected_selected=desired))}):raise BatchStopped('team_guard_failed')
            try:
                with self.timings.measure('page_wait'):receipt=native.run(self.tasker,node,reset_hits=(node,))
                if not receipt['native_ok'] or not any(n['name']==node and n.get('completed') and n.get('action',{}).get('success') for n in receipt.get('nodes',())):raise BatchStopped('battle_input_unknown')
            finally:
                if not resource.override_pipeline({node:original}):raise BatchStopped('team_guard_restore_failed')
            return self.stamp(operation_id=oid,**fields,native=receipt,input_native=receipt,input_action_verified=True)
        clicked=batch.transaction('battle_start',fields,send)
        batch.checkpoint();result=read_result(enter_id,block_id)
        if not isinstance(result,dict) or result.get('verified') is not True or result.get('outcome') not in ('won','defeated') or result.get('enter_id')!=enter_id or result.get('block_id')!=block_id:raise BatchStopped('battle_result_unknown')
        return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,battle_finished=True,outcome=result['outcome'],native=result,input_native=clicked['input_native']),**fields)


    @property
    def characters(self):
        if self._characters is None:
            from .character_recognition import MaaCharacters
            self._characters=MaaCharacters(self.tasker,timings=self.timings)
        return self._characters

    def _configured(self,batch):
        if not self._optimizer_supplied:
            from .roles import configured_optimizer
            self.optimizer=configured_optimizer(getattr(batch.settings,'strategy',None))

    def _identity(self,batch,enter_id,block_id):
        if type(enter_id) is not int or enter_id<=0 or type(block_id) is not int or block_id<=0:raise BatchStopped('team_identity_required')
        key=(enter_id,block_id)
        if key in self.attempted:raise BatchStopped('team_already_attempted')
        batch.checkpoint();self._configured(batch);self.attempted.add(key)
        if not self.roster or len(set(self.roster))!=len(self.roster) or any(role not in self.optimizer.profiles for role in self.roster):raise BatchStopped('team_roster_unknown')
        return key

    def _edit(self,batch,enter_id,block_id,page,desired,observation):
        from .character_recognition import CharacterNavigation
        characters=self.characters;nav=CharacterNavigation(characters,self.stamp,roster=self.roster)
        if any(not c['trusted'] for c in observation['members']):raise BatchStopped('team_identity_unknown')
        selected=observation['selected']
        for role in sorted(set(selected)-set(desired))+sorted(set(desired)-set(selected)):
            before=sorted(selected);after=sorted(set(before)^{role})
            if role not in before:
                # Member feedback omits candidates; acquire the current full list before seeking.
                if observation.get('members_only'):observation,_=characters.read(batch,page,roster=self.roster)
                attribute=self.optimizer.profiles[role].data.get('attribute')
                observation,_=nav.seek(batch,enter_id,page,observation,(role,),block_id=block_id,filters=(attribute,) if attribute else ())
                if observation['selected']!=before:raise BatchStopped('team_selection_changed')
            fields=dict(enter_id=enter_id,block_id=block_id,role_id=role,selected=role in after)
            def send(oid):
                batch.checkpoint();_,sent=characters.run('ToggleOnce',dict(mode='toggle',page=page,roster=list(self.roster),role_id=role,expected_selected=before),input=True)
                return self.stamp(source='maa',operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
            clicked=batch.transaction('team_toggle',fields,send)
            batch.checkpoint();actual,receipt=characters.wait_for(batch,page,lambda value:value['selected']==after and all(c['trusted'] for c in value['members']),roster=self.roster,members_only=True)
            batch.observation(self.stamp(source='maa',operation_id=clicked['operation_id'],**fields,native=receipt,input_native=clicked['input_native'],selection_verified=True),**fields)
            selected=after;observation=actual
        actual,receipt=characters.read(batch,page,roster=self.roster)
        if actual['selected']!=desired or any(not c['trusted'] for c in actual['members']):raise BatchStopped('team_readback_unknown')
        return actual,receipt

    def prepare(self,batch,enter_id,block_id,context=Context(),*,hold_current=False):
        # Explicit legacy native templates remain a compatibility adapter.
        if self._characters is None and self.tasker.resource.get_node_data('LabyDynamicTeamObserved'):
            return self._prepare_legacy(batch,enter_id,block_id,context)
        from .character_recognition import CharacterNotFound
        key=self._identity(batch,enter_id,block_id);pool=self.roster
        while True:
            with self.timings.measure('strategy'):
                plan=(self.optimizer.retry_best(pool,context,failed_teams=self.failed_teams,
                    retry_number=self.retry_number,last_failed_team=self.last_failed_team) if self.retry_number
                    else self.optimizer.best(pool,context,battle=True))
            if plan is None or len(plan.members)!=min(5,sum(self.optimizer.profiles[role].position is not None for role in pool)):
                raise BatchStopped('battle_retry_team_exhausted' if self.retry_number and plan is None else 'team_strategy_unknown')
            desired=sorted(plan.members);observation,_=self.characters.read(batch,'battle',roster=self.roster)
            # Fixed upstream ordinary first attempt keeps a fully identified existing team.
            if (hold_current and not self.retry_number and len(observation['selected'])==len(observation['members'])==5
                    and all(c['trusted'] for c in observation['members']) and set(observation['selected'])<=set(self.roster)):
                desired=observation['selected']
            try:actual,receipt=self._edit(batch,enter_id,block_id,'battle',desired,observation)
            except CharacterNotFound as missing:
                remaining=tuple(role for role in pool if role not in missing.targets)
                if remaining==pool:raise
                pool=remaining
                continue
            self.prepared[key]=desired;self.pages[key]='battle'
            return self.stamp(source='maa',enter_id=enter_id,block_id=block_id,team=desired,native=receipt)

    def prepare_boss(self,batch,enter_id,block_id,context=Context(boss=True),*,requested=3,force_multi=False):
        from .teams import boss_plan
        from .roles import strategy_value
        from .character_recognition import CharacterNavigation,CharacterNotFound
        key=self._identity(batch,enter_id,block_id);pool=self.roster
        if not force_multi and strategy_value(getattr(batch.settings,'strategy',None),'boss_mode','multi')=='single':requested=1
        characters=self.characters;nav=CharacterNavigation(characters,self.stamp,roster=self.roster)
        while True:
            # A completed UI search only shrinks this planning pool, never owned roles or defeat history.
            with self.timings.measure('strategy'):plan=boss_plan(self.optimizer,pool,context,requested=requested,supplements=True,excluded=self.failed_teams)
            if not plan.teams or plan.safe_count<1:
                raise BatchStopped('battle_retry_team_exhausted' if self.retry_number and not plan.teams else 'team_strategy_unknown')
            observation,_=characters.read(batch,'boss',roster=self.roster);receipts=[]
            if observation['boss_tab'] is None:raise BatchStopped('boss_team_tab_unknown')
            all_teams=[sorted(plan.teams[index].members) if index<len(plan.teams) else [] for index in range(3)]
            try:
                # Free old cross-tab members before any additions; keep members already in their desired tab.
                for index,desired in enumerate(all_teams,1):
                    if observation['boss_tab']!=index:observation=nav.move(batch,enter_id,'boss',observation,tab=index,block_id=block_id)
                    retained=sorted(set(observation['selected'])&set(desired))
                    if observation['selected']!=retained:observation,_=self._edit(batch,enter_id,block_id,'boss',retained,observation)
                for index,desired in enumerate(all_teams,1):
                    if observation['boss_tab']!=index:observation=nav.move(batch,enter_id,'boss',observation,tab=index,block_id=block_id)
                    observation,receipt=self._edit(batch,enter_id,block_id,'boss',desired,observation)
                    receipts.append(receipt)
            except CharacterNotFound as missing:
                remaining=tuple(role for role in pool if role not in missing.targets)
                if remaining==pool:raise
                pool=remaining
                continue
            if observation['boss_tab']!=3:raise BatchStopped('boss_team_tab_unknown')
            observation,receipt=characters.read(batch,'boss',roster=self.roster)
            if observation['selected']!=all_teams[2]:raise BatchStopped('team_readback_unknown')
            teams=[team for team in all_teams if team]
            self.prepared[key]=teams[0];self.pages[key]='boss';self.boss_teams[key]=teams;self.boss_all_teams[key]=all_teams
            return self.stamp(source='maa',enter_id=enter_id,block_id=block_id,team=teams[0],teams=teams,
                selected_teams_verified=True,safe_count=plan.safe_count,plan_reason=plan.reason,native=receipt,team_native=receipts)

    def failed_signatures(self,enter_id,block_id):
        key=(enter_id,block_id)
        if key not in self.started or key not in self.prepared:raise BatchStopped('battle_without_owned_start')
        return tuple(tuple(sorted(team)) for team in self.boss_teams.get(key,(self.prepared[key],)))

    def failed_signature(self,enter_id,block_id):
        key=(enter_id,block_id)
        if key not in self.started or key not in self.prepared:raise BatchStopped('battle_without_owned_start')
        return tuple(sorted(self.prepared[key]))

    def start_once(self,batch,enter_id,block_id,read_result):
        key=(enter_id,block_id)
        if key not in self.pages:return self._start_legacy(batch,enter_id,block_id,read_result)
        desired=self.prepared.get(key)
        if desired is None:raise BatchStopped('team_not_prepared')
        if key in self.started:raise BatchStopped('battle_already_attempted')
        self.started.add(key);page=self.pages[key];fields=dict(enter_id=enter_id,block_id=block_id,team=desired)
        batch.checkpoint();observation,_=self.characters.read(batch,page,roster=self.roster)
        expected=desired;params=dict(mode='start',page=page,roster=list(self.roster))
        if page=='boss':
            from .character_recognition import CharacterNavigation
            nav=CharacterNavigation(self.characters,self.stamp,roster=self.roster);all_teams=self.boss_all_teams[key]
            for index in range(1,4):
                if observation['boss_tab']!=index:observation=nav.move(batch,enter_id,page,observation,tab=index,block_id=block_id)
                observation,_=self.characters.read(batch,page,roster=self.roster)
                if observation['selected']!=all_teams[index-1] or any(not c['trusted'] for c in observation['members']):raise BatchStopped('team_readback_unknown')
            expected=all_teams[2];params['expected_teams']=all_teams
        params.update(expected_selected=expected,expected_size=len(expected))
        def send(oid):
            batch.checkpoint()
            _,receipt=self.characters.run('StartOnce',params,input=True)
            return self.stamp(operation_id=oid,**fields,native=receipt,input_native=receipt,input_action_verified=True)
        clicked=batch.transaction('battle_start',fields,send)
        batch.checkpoint();result=read_result(enter_id,block_id)
        if not isinstance(result,dict) or result.get('verified') is not True or result.get('outcome') not in ('won','defeated') or result.get('enter_id')!=enter_id or result.get('block_id')!=block_id:raise BatchStopped('battle_result_unknown')
        return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,battle_finished=True,outcome=result['outcome'],native=result,input_native=clicked['input_native']),**fields)
