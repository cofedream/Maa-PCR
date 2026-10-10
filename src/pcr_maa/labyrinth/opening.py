"""Upstream configured invitations with fresh Maa selection and joined feedback.
Each input belongs to a locked Batch intent; actual full names and final Map
are read independently. Private SDK creation/account qualification remains
with the caller. Generic card resources come from the pinned upstream pack.
"""
import time
from .. import native
from .batch import BatchStopped
from .timing import Timings
class MaaOpening:
    def __init__(self,tasker,stamp,*,timings=None,characters=None):
        self.tasker,self.stamp=tasker,stamp
        self.timings=timings or Timings()
        self.used=set();self.granted_used=set();self.grants={};self._characters=characters
    def _run(self,node,*,input=False):
        with self.timings.measure('page_wait'):
            receipt=native.run(self.tasker,node,reset_hits=(node,) if input else ())
        if not receipt['native_ok'] or not any(n['name']==node and n['completed'] for n in receipt['nodes']):
            raise BatchStopped('opening_input_unknown' if input else 'opening_readback_unknown')
        if input and not any(n['name']==node and n.get('action') and n['action']['success'] for n in receipt['nodes']):
            raise BatchStopped('opening_input_unknown')
        return receipt
    def _wait_invitation_joined(self,batch,enter_id,invited,node):
        from .scene import MaaSceneRuntime
        # Both existing joined readers have a 3000ms node budget. Animation
        # advancement and the final identity read share it, including an outer
        # deadline; upstream 40 clicks/30s is a ceiling, not an added wait here.
        deadline=min(native.RUN_DEADLINE.get(),time.monotonic()+3)
        token=native.RUN_DEADLINE.set(deadline)
        try:
            MaaSceneRuntime(self.tasker,self.stamp,timings=self.timings,stopped=batch.stopped).wait_for(
                batch,{'joined','role_reward','free_role_reward','map'},timeout=3,
                reason='opening_readback_unknown',acquisition_fields=dict(
                    enter_id=enter_id,invitation_operation_id=invited['operation_id']))
            # The invitation source or its MAP background hands off to the original
            # joined reader; only actual named identities can complete the invitation.
            batch.checkpoint();return self._run(node)
        finally:native.RUN_DEADLINE.reset(token)
    @property
    def characters(self):
        if self._characters is None:
            from .character_recognition import MaaCharacters
            self._characters=MaaCharacters(self.tasker,timings=self.timings)
        return self._characters
    def invite(self,batch,enter_id,*,guild_id,context=None,slots=None,creation_operation_id=None):
        """Three upstream configured invitation slots, at any supported difficulty."""
        from .character_recognition import CharacterNavigation,canonical_id
        from .portrait_cache import joined_portrait_readback
        from .roles import configured_optimizer
        defaults={1:(('1075',),('1351',),('1059',)),2:(('1089',),('1088',),('1003','1801','1225')),
            3:(('1025',),('1308',),('1145',)),4:(('1242',),('1339',),('1136','1115','1236','1238')),
            5:(('1091',),('1171',),('1011',))}
        if type(enter_id) is not int or enter_id<=0 or type(guild_id) is not int or guild_id not in defaults:
            raise BatchStopped('opening_identity_required')
        claim=self._creation_key(enter_id,creation_operation_id)
        if enter_id in self.used or claim in self.used:raise BatchStopped('opening_already_attempted')
        previous=self.grants.get(claim)
        if previous is not None and (previous.get('binding')!=batch.binding.payload() or previous.get('enter_id')!=enter_id or previous.get('roles_joined')!=['1068']):
            raise BatchStopped('opening_grant_binding_unknown')
        slots=defaults[guild_id] if slots is None else slots
        if type(slots) not in (tuple,list) or len(slots)!=3:raise BatchStopped('opening_slots_invalid')
        slots=tuple(tuple(canonical_id(v) for v in row) for row in slots)
        if any(not row or len(set(row))!=len(row) for row in slots) or len(set(v for row in slots for v in row))!=sum(map(len,slots)):
            raise BatchStopped('opening_slots_invalid')
        optimizer=configured_optimizer(getattr(batch.settings,'strategy',None))
        if any(v not in optimizer.profiles for row in slots for v in row):raise BatchStopped('opening_slots_invalid')
        self.used.add(claim);batch.checkpoint()
        empty=self.stamp(source='maa',enter_id=enter_id,page='opening_empty',native=self._run('LabyOpeningEmptyObserved'))
        batch.observation(empty,enter_id=enter_id,page='opening_empty')
        characters=self.characters;nav=CharacterNavigation(characters,self.stamp);selected=[]
        observation,_=characters.read(batch,'opening')
        if observation['count']!=0 or observation['selected']:raise BatchStopped('opening_selection_unknown')
        for row in slots:
            attrs=tuple(dict.fromkeys(optimizer.profiles[key].data.get('attribute') for key in row if optimizer.profiles[key].data.get('attribute')))
            observation,matches=nav.seek(batch,enter_id,'opening',observation,row,filters=attrs)
            visible={c['character_id'] for c in matches};role=next(key for key in row if key in visible)
            before=sorted(selected);after=sorted((*before,role));fields=dict(enter_id=enter_id,role_id=role)
            def send(oid):
                _,sent=characters.run('ToggleOnce',dict(mode='toggle',page='opening',role_id=role,expected_selected=before),input=True)
                return self.stamp(source='maa',operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
            clicked=batch.transaction('opening_select',fields,send)
            batch.checkpoint();actual,receipt=characters.wait_for(batch,'opening',lambda value:value['count']==len(after) and role in value['selected'] and set(value['selected'])<=set(after) and not any(c['selected'] and not c['trusted'] for c in value['cards']))
            batch.observation(self.stamp(source='maa',operation_id=clicked['operation_id'],**fields,native=receipt,input_native=clicked['input_native'],selection_verified=True),**fields)
            selected=after;observation=actual
        nodes=opening_catalog_pipeline()
        if not self.tasker.resource.override_pipeline(nodes):raise BatchStopped('opening_pipeline_unavailable')
        fields=dict(enter_id=enter_id,expected_roles=selected)
        def invite(oid):
            _,sent=characters.run('InviteOnce',dict(mode='invite',page='opening',expected_selected=selected),input=True)
            return self.stamp(source='maa',operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
        clicked=batch.transaction('opening_invite',fields,invite)
        batch.checkpoint();receipt=self._wait_invitation_joined(batch,enter_id,clicked,'LabyOpeningAnyJoinedObserved')
        _,actual=joined_portrait_readback(receipt);ids=sorted(person.character_id for person in actual)
        if ids!=selected:raise BatchStopped('opening_joined_unknown')
        joined=batch.observation(self.stamp(source='maa',operation_id=clicked['operation_id'],**fields,roles_joined=ids,native=receipt,input_native=clicked['input_native']),**fields)
        # Joining identities are already committed. Close only the dedicated
        # joined page; harmless OCR changes cannot erase that result.
        close_fields=dict(enter_id=enter_id,phase='invitation_close')
        def close(oid):
            sent=self._run('LabyOpeningAnyJoinedCloseOnce',input=True)
            return self.stamp(source='maa',operation_id=oid,**close_fields,native=sent,input_native=sent,input_action_verified=True)
        clicked=batch.transaction('opening_close',close_fields,close)
        batch.checkpoint();hit,receipt=self._after_invitation(batch,guild_id)
        closed=batch.observation(self.stamp(source='maa',operation_id=clicked['operation_id'],**close_fields,page=hit,native=receipt,input_native=clicked['input_native']),**close_fields)
        granted=self.grants.get(claim)
        if closed['page']=='joined':
            granted=self.consume_granted(batch,enter_id,guild_id=guild_id,next_node='LabyMapObserved',page='map',creation_operation_id=creation_operation_id)
            closed=granted['granted']['closed']
        actual=sorted(set(selected)|set(granted['roles_joined'] if granted else ()))
        return dict(joined,roles_joined=actual,invitation=dict(source='maa',enter_id=enter_id,empty=empty,joined=joined,closed=closed),granted=granted)
    @staticmethod
    def _creation_key(enter_id,creation_operation_id):
        if type(enter_id) is not int or enter_id<=0:raise BatchStopped('opening_identity_required')
        if creation_operation_id is None:return enter_id
        import re
        if type(creation_operation_id) is not str or re.fullmatch(r'[0-9a-f]{32}',creation_operation_id) is None:
            raise BatchStopped('opening_creation_identity_required')
        return (enter_id,creation_operation_id)
    def _install_joined(self):
        from .portrait_cache import joined_portrait_pipeline
        nodes={}
        for count in (1,2,3):nodes.update(joined_portrait_pipeline(count))
        from .interruptions import date_changed_pipeline
        from .session import reconnect_pipeline
        from copy import deepcopy
        for pipeline,root in ((date_changed_pipeline(),'LabyDateChangedObserved'),(reconnect_pipeline(),'LabyDisconnect6002Observed')):
            nodes.update({key:deepcopy(pipeline[key]) for key in [root]+pipeline[root]['all_of']})
        if not self.tasker.resource.override_pipeline(nodes):raise BatchStopped('opening_pipeline_unavailable')
    def _choice(self,batch,entry,names):
        from .game_session import _root,_box
        if not self.tasker.resource.override_pipeline({entry:dict(recognition='Or',any_of=names,action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0)}):
            raise BatchStopped('opening_pipeline_unavailable')
        batch.checkpoint();receipt=self._run(entry);batch.checkpoint();raw=_root(receipt,entry).get('recognition')
        if (type(raw) is not list or not 1<=len(raw)<=len(names) or any(type(row) is not dict for row in raw)
            or [row.get('name') for row in raw]!=names[:len(raw)]
            or any(row.get('box') is not None for row in raw[:-1]) or not _box(raw[-1].get('box'))):
            raise BatchStopped('opening_readback_unknown')
        hit=raw[-1]['name']
        if hit=='LabyDateChangedObserved':raise BatchStopped('date_changed')
        if hit=='LabyDisconnect6002Observed':raise BatchStopped('disconnected')
        return hit,receipt
    def observe_joined(self,batch,enter_id):
        """Actual strict one/two/three-row full names; no input or owned inference."""
        from .portrait_cache import joined_portrait_readback
        self._creation_key(enter_id,None);self._install_joined()
        names=['LabyDateChangedObserved','LabyDisconnect6002Observed']+[f'LabyPortraitJoined{count}Observed' for count in (1,2,3)]
        hit,_=self._choice(batch,'LabyOpeningAnyActualJoinedWait',names)
        batch.checkpoint();receipt=self._run(hit);batch.checkpoint()
        layout,items=joined_portrait_readback(receipt)
        observed=self.stamp(source='maa',enter_id=enter_id,page='joined',roles_joined=sorted(item.character_id for item in items),native=receipt)
        batch.observation(observed,enter_id=enter_id,page='joined')
        return dict(observed,layout=layout)
    def _after_invitation(self,batch,guild_id):
        self._install_joined()
        names=['LabyDateChangedObserved','LabyDisconnect6002Observed','LabyMapObserved']
        if guild_id==5:names.append('LabyPortraitJoined1Observed')
        hit,_=self._choice(batch,'LabyOpeningAfterInvitationObserved',names)
        receipt=self._run(hit);batch.checkpoint()
        return ('map' if hit=='LabyMapObserved' else 'joined'),receipt
    def consume_granted(self,batch,enter_id,*,guild_id,next_node='LabyOpeningEmptyObserved',page='opening_empty',creation_operation_id=None):
        """Close only a freshly observed sole 1068 grant; grant never follows config alone."""
        if type(guild_id) is not int or guild_id!=5:raise BatchStopped('opening_grant_scope_unknown')
        if (next_node,page) not in (('LabyOpeningEmptyObserved','opening_empty'),('LabyMapObserved','map')):
            raise BatchStopped('opening_grant_postcondition_unknown')
        key=self._creation_key(enter_id,creation_operation_id)
        if key in self.granted_used:raise BatchStopped('opening_grant_already_attempted')
        self.granted_used.add(key);batch.checkpoint()
        joined=self.observe_joined(batch,enter_id)
        if joined['layout']!='joined1' or joined['roles_joined']!=['1068']:raise BatchStopped('opening_grant_identity_unknown')
        from .portrait_cache import joined_portrait_readback
        _,items=joined_portrait_readback(joined['native'])
        from .rewards import normalized_name
        import re
        full=normalized_name(items[0].raw_text)
        exact=r'^\s*'+r'\s*'.join('[（(]' if c=='(' else '[）)]' if c==')' else re.escape(c) for c in full)+r'\s*$'
        nodes={'LabyPortraitJoined1Name0':dict(expected=exact),
            'LabyPortraitJoined1Message':dict(expected=exact[:-1]+r'成为了同伴[。.]?$',only_rec=True),
            'LabyOpeningGrantCloseOnce':dict(recognition='And',all_of=['LabyPortraitJoined1Close','LabyPortraitJoined1Observed'],
                action='Click',target=True,max_hit=1,next=[],timeout=3000,pre_delay=0,post_delay=0)}
        originals={name:self.tasker.resource.get_node_data(name) for name in ('LabyPortraitJoined1Name0','LabyPortraitJoined1Message')}
        if not self.tasker.resource.override_pipeline(nodes):raise BatchStopped('opening_close_guard_failed')
        fields=dict(enter_id=enter_id,navigation_target=page,roles_joined=['1068'])
        def close(oid):
            sent=self._run('LabyOpeningGrantCloseOnce',input=True)
            return self.stamp(source='maa',operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
        try:
            clicked=batch.transaction('opening_granted_close',fields,close)
            batch.checkpoint();entry='LabyOpeningGrantPostObserved'
            hit,_=self._choice(batch,entry,['LabyDateChangedObserved','LabyDisconnect6002Observed',next_node])
            receipt=self._run(hit);batch.checkpoint()
            closed=batch.observation(self.stamp(source='maa',operation_id=clicked['operation_id'],**fields,page=page,native=receipt,input_native=clicked['input_native']),**fields)
        except BaseException:
            try:self.tasker.resource.override_pipeline(originals)
            except Exception:pass
            raise
        else:
            if not self.tasker.resource.override_pipeline(originals):raise BatchStopped('opening_close_guard_restore_failed')
        actual=dict(joined,granted=dict(source='maa',enter_id=enter_id,joined=joined,closed=closed))
        self.grants[key]=actual
        return actual
    def invite_gourmet(self,batch,enter_id,*,guild_id):
        if guild_id!=1 or type(guild_id) is not int or batch.settings.difficulty!=5 or type(enter_id) is not int or enter_id<=0:
            raise BatchStopped('opening_scope_not_qualified')
        if enter_id in self.used: raise BatchStopped('opening_already_attempted')
        self.used.add(enter_id)
        batch.checkpoint()
        empty=self.stamp(source='maa',enter_id=enter_id,page='opening_empty',native=self._run('LabyOpeningEmptyObserved'))
        batch.observation(empty,enter_id=enter_id,page='opening_empty')
        for role,node,readback in (
            ('1075','LabyOpeningSelect1075Once','LabyOpening1075Selected'),
            ('1351','LabyOpeningSelect1351Once','LabyOpeningFirstTwoSelected'),
            ('1059','LabyOpeningSelect1059Once','LabyOpeningThreeSelected')):
            fields=dict(enter_id=enter_id,role_id=role)
            def send(oid,node=node,readback=readback,fields=fields):
                sent=self._run(node,input=True)
                return self.stamp(source='maa',operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
            clicked=batch.transaction('opening_select',fields,send)
            batch.checkpoint();receipt=self._run(readback)
            batch.observation(self.stamp(source='maa',operation_id=clicked['operation_id'],**fields,native=receipt,input_native=clicked['input_native'],selection_verified=True),**fields)
        fields=dict(enter_id=enter_id,expected_roles=['1075','1351','1059'])
        def invite(oid):
            sent=self._run('LabyOpeningInviteOnce',input=True)
            return self.stamp(source='maa',operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
        clicked=batch.transaction('opening_invite',fields,invite)
        batch.checkpoint();receipt=self._wait_invitation_joined(batch,enter_id,clicked,'LabyOpeningJoinedObserved')
        from .portrait_cache import joined_portrait_readback
        _,actual=joined_portrait_readback(receipt);ids=[person.character_id for person in actual]
        if sorted(ids)!=sorted(fields['expected_roles']):raise BatchStopped('opening_joined_unknown')
        joined=batch.observation(self.stamp(source='maa',operation_id=clicked['operation_id'],**fields,roles_joined=ids,native=receipt,input_native=clicked['input_native']),**fields)
        close_fields=dict(enter_id=enter_id,navigation_target='map')
        def close(oid):
            sent=self._run('LabyOpeningJoinedCloseOnce',input=True)
            return self.stamp(source='maa',operation_id=oid,**close_fields,native=sent,input_native=sent,input_action_verified=True)
        clicked=batch.transaction('opening_close',close_fields,close)
        batch.checkpoint();receipt=self._run('LabyMapObserved')
        closed=batch.observation(self.stamp(source='maa',operation_id=clicked['operation_id'],**close_fields,page='map',native=receipt,input_native=clicked['input_native']),**close_fields)
        # Protocol current0 belongs before invitation; the later map position
        # is observed independently by the caller, never filled here as 0.
        return dict(joined,invitation=dict(source='maa',enter_id=enter_id,
            empty=empty,joined=joined,closed=closed))
def opening_catalog_pipeline():
    from .arrivals import NAME_ROIS,_ocr
    nodes={f'LabyOpeningJoinedName{i}':_ocr('.+',roi) for i,roi in enumerate(NAME_ROIS)}
    nodes['LabyOpeningAnyJoinedObserved']=dict(recognition='And',all_of=['LabyJoinedTitle']+[f'LabyOpeningJoinedName{i}' for i in range(3)],
        action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0)
    nodes['LabyOpeningAnyJoinedCloseOnce']=dict(recognition='And',all_of=['LabyJoinedCloseLabel','LabyJoinedTitle'],
        action='Click',target=True,max_hit=1,next=[],timeout=3000,pre_delay=0,post_delay=0)
    return nodes
