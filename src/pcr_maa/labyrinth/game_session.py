"""Concrete Maa client navigation for an outer-created untouched protocol run.

The Owner supplies the protocol snapshot/current-block fact, Batch's locked
journal, and stamp callback with its existing capture/binding/frame contract.
Task IDs are native receipt integrity only, never freshness evidence. This
component does not log in to the protocol, enter a run, invite, resume a map,
prove a handoff, or settle any previous unknown consumption.
"""
from copy import deepcopy
from dataclasses import dataclass
import math,re,time
from .. import native
from .batch import BatchStopped
from .interruptions import date_changed_pipeline
from .session import (title_pipeline,title_player_id,notice_pipeline,resume_home_pipeline)
from .profile import PROFILE_NODE,profile_pipeline,profile_player_id,observe_profile
from .snapshots import OpeningSnapshot
from .timing import Timings
from pathlib import Path
import json

_OPERATIONS=('client_download_retry','client_return_title','client_title_start','client_notice_close',
             'client_open_adventure','client_open_labyrinth',
             'client_open_main_menu','client_open_profile','client_profile_back',
             'client_menu_to_adventure','client_profile_return_title')
_PAGES={
    'disconnect':'LabyDisconnect6002Observed',
    'title':'LabyClientTitleObserved',
    'notice':'LabyClientNoticeObserved',
    'client_home':'LabyClientGameHomeObserved',
    'adventure':'LabyClientAdventureObserved',
    'menu':'LabyClientMainMenuObserved',
    'labyrinth_home':'LabyClientResumeHomeObserved',
}
_MENU_LABELS=(
    ('LabyClientMainMenuTitle','主菜单',[60,0,180,80]),
    ('LabyClientMainMenuProfileLabel','简介',[470,235,150,70]),
)


@dataclass(frozen=True)
class ClientSessionFacts:
    page: str
    title_player_id: int
    native: dict
    account_source: str='client_title_ocr'
    handoff_qualified: bool=False
    map_verified: bool=False
    clear_verified: bool=False
    account_native: dict|None=None


def game_session_pipeline(expected_player_id):
    """Only specific sampled pages; input nodes have no implicit next actions."""
    nodes=title_pipeline(expected_player_id)
    # Observe actual title ID independently, then gate Start with the exact
    # expected ID again. Neither observation nor reported ID echoes settings.
    nodes['LabyClientTitlePlayerIdBound']=deepcopy(nodes['LabyClientTitlePlayerId'])
    nodes['LabyClientTitlePlayerId']['expected']=r'^ID[:：]\s*[0-9][0-9\s]*$'
    nodes['LabyClientTitleStartOnce']['all_of']=[
        'LabyClientTitlePlayerIdBound' if n=='LabyClientTitlePlayerId' else n
        for n in nodes['LabyClientTitleStartOnce']['all_of']]
    nodes.update(notice_pipeline())
    nodes['LabyClientNoticeObserved']=dict(recognition='And',
        all_of=['LabyClientNoticeTitle','LabyClientNoticeUpdates','LabyClientNoticeCalendar','LabyClientNoticeCloseLabel'],
        action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0)
    def label(pattern,roi):
        return dict(recognition='OCR',expected=pattern,roi=roi,threshold=.8,
            action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)
    # Full-text crops avoid detector grouping changing OCR between HOME endpoints.
    shared=json.loads((Path(__file__).resolve().parents[3]/'assets/resource/pipeline/home_text.json').read_text(encoding='utf-8'))
    nodes.update(deepcopy(shared))
    for local, common in (('LabyClientAdventureLabel','HomeAdventureText'),
                          ('LabyClientScheduleLabel','HomeScheduleText'),('LabyClientGiftLabel','HomeGiftText')):
        nodes[local]=deepcopy(shared[common])
    home=['HomeTextLayout','HomeNoKnownLargePanel']
    nodes['LabyClientGameHomeObserved']=dict(recognition='And',all_of=home,
        action='DoNothing',next=[],timeout=5000,pre_delay=0,post_delay=0)
    nodes['LabyClientHomeToAdventure']=dict(recognition='And',all_of=home,
        action='Click',target=True,max_hit=1,next=[],timeout=3000,rate_limit=250,pre_delay=0,post_delay=0)
    nodes['LabyClientAdventureObserved']=dict(recognition='And',all_of=['LabyAdventureIdentity','LabyDawnEntryLabel'],
        action='DoNothing',next=[],timeout=5000,pre_delay=0,post_delay=0)
    # These menu labels and targets were independently sampled. Menu's bottom
    # adventure word does not pass complete-word OCR, so the sampled icon target
    # is guarded by the two complete Main Menu anchors, never a residual '险'.
    nodes['LabyClientMainMenuButton']=label('^主菜单$',[1130,650,150,70])
    for name,text,roi in _MENU_LABELS:nodes[name]=label('^'+text+'$',roi)
    menu=[name for name,_,_ in _MENU_LABELS]
    nodes[_PAGES['menu']]=dict(recognition='And',all_of=menu,
        action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0)
    nodes['LabyClientOpenMainMenuOnce']=dict(recognition='And',
        all_of=['LabyClientMainMenuButton','LabyAdventureIdentity','LabyDawnEntryLabel'],
        action='Click',target=True,max_hit=1,next=[],timeout=3000,rate_limit=250,pre_delay=0,post_delay=0)
    nodes['LabyClientOpenProfileOnce']=dict(recognition='And',all_of=list(reversed(menu)),
        action='Click',target=True,max_hit=1,next=[],timeout=3000,rate_limit=250,pre_delay=0,post_delay=0)
    nodes['LabyClientMenuToAdventureOnce']=dict(recognition='And',all_of=menu,
        action='Click',target=[690,630,85,85],max_hit=1,next=[],timeout=3000,rate_limit=250,
        pre_delay=0,post_delay=0)
    nodes.update(profile_pipeline())
    nodes['LabyClientProfileBackOnce']=dict(deepcopy(nodes[PROFILE_NODE]),
        action='Click',target=[10,10,60,60],max_hit=1,timeout=3000)
    nodes['LabyClientProfileReturnTitleOnce']=deepcopy(nodes['LabyDisconnectReturnTitleOnce'])
    nodes['LabyClientOpenLabyrinthOnce']=dict(recognition='And',all_of=['LabyDawnEntryLabel','LabyAdventureIdentity'],
        action='Click',target=True,max_hit=1,next=[],timeout=3000,rate_limit=250,pre_delay=0,post_delay=0)
    nodes.update(resume_home_pipeline())
    # This component stops at the actual active-run home; map resume is outside.
    nodes.pop('LabyClientResumeHomeDepartOnce')
    readonly=date_changed_pipeline();readonly.pop('LabyDateChangedConfirmOnce');nodes.update(readonly)
    for page,node in _PAGES.items():
        nodes['LabyGameSessionWait_'+page]=dict(recognition='Or',
            any_of=['LabyDateChangedObserved',node],action='DoNothing',next=[],
            timeout=5000,rate_limit=250,pre_delay=0,post_delay=0)
    nodes['LabyClientAfterTitleGiftLabel']=deepcopy(nodes['LabyClientGiftLabel'])
    nodes['LabyClientAfterTitleHomeObserved']=deepcopy(nodes['LabyClientGameHomeObserved'])
    nodes['LabyClientAfterTitleHomeObserved']['all_of']=[
        'LabyClientAfterTitleGiftLabel' if n=='LabyClientGiftLabel' else n for n in home]
    nodes['LabyClientDownloadErrorTitle']=label('^下载错误$',[480,170,330,60])
    nodes['LabyClientDownloadErrorBody']=label('^通信超时$',[480,310,320,50])
    nodes['LabyClientDownloadRetryLabel']=label('^重试$',[650,450,275,85])
    download=['LabyClientDownloadRetryLabel','LabyClientDownloadErrorTitle','LabyClientDownloadErrorBody']
    nodes['LabyClientDownloadErrorObserved']=dict(recognition='And',all_of=download,
        action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0)
    nodes['LabyClientDownloadRetryOnce']=dict(recognition='And',all_of=download,
        action='Click',target=True,max_hit=1,next=[],timeout=3000,pre_delay=0,post_delay=0)
    # Upstream permits HOME without announcement and allows cold-start loading.
    # This endpoint wait is read-only; only the full announcement guard can close it.
    nodes['LabyGameSessionAfterTitleObserved']=dict(recognition='Or',
        any_of=['LabyDateChangedObserved','LabyDisconnect6002Observed',
                'LabyClientNoticeObserved','LabyClientAfterTitleHomeObserved','LabyClientDownloadErrorObserved'],
        action='DoNothing',next=[],timeout=75000,rate_limit=250,pre_delay=0,post_delay=0)
    nodes['LabyGameSessionTitleStabilityWait']=dict(recognition='DirectHit',
        action='DoNothing',next=[],timeout=1000,pre_delay=500,post_delay=0)
    nodes['LabyGameSessionEntryObserved']=dict(recognition='Or',
        any_of=['LabyDateChangedObserved','LabyDisconnect6002Observed','LabyClientTitleObserved',
                'LabyClientGameHomeObserved','LabyClientAdventureObserved'],
        action='DoNothing',next=[],timeout=5000,rate_limit=250,pre_delay=0,post_delay=0)
    # Recovery may spend time on an unrecognized splash, but performs no input
    # while waiting. The old 6002 may remain briefly after Click, so it cannot
    # terminate this wait as a supposed second disconnect. Only Title/HOME
    # completes; an unknown/6002 timeout leaves the recovery intent in flight.
    nodes['LabyGameSessionRecoveryObserved']=dict(recognition='Or',
        any_of=['LabyDateChangedObserved','LabyClientTitleObserved',
                'LabyClientGameHomeObserved'],action='DoNothing',next=[],timeout=30000,
        rate_limit=250,pre_delay=0,post_delay=0)
    return nodes


def _box(value):
    return (type(value) is list and len(value)==4 and all(type(v) is int for v in value)
        and value[0]>=0 and value[1]>=0 and value[2]>0 and value[3]>0
        and value[0]+value[2]<=1280 and value[1]+value[3]<=720)


def _root(receipt,name):
    if (type(receipt) is not dict or receipt.get('tasker_inited') is not True
        or type(receipt.get('task_id')) is not int or receipt['task_id']<=0):
        raise BatchStopped('game_session_readback_unknown')
    nodes=receipt.get('nodes')
    if (type(nodes) is not list or len(nodes)!=1 or type(nodes[0]) is not dict
        or nodes[0].get('name')!=name or nodes[0].get('completed') is not True
        or receipt.get('native_ok') is not True):raise BatchStopped('game_session_readback_unknown')
    return nodes[0]


class MaaGameSession:
    def __init__(self,tasker,stamp,*,timings=None):
        if not callable(stamp):raise ValueError('owner_stamp_required')
        self.tasker,self.stamp=tasker,stamp
        self.timings=timings or Timings();self.used=set();self.blocked=False
        self.creation_operation_id=None;self.opening_snapshot=None

    def _checkpoint(self,batch):
        batch.checkpoint()
        if self.blocked:raise BatchStopped('game_session_blocked')
        if getattr(self.tasker,'stopping',False):raise BatchStopped('stopped')

    def _wait(self,batch,page):
        self._checkpoint(batch);entry='LabyGameSessionWait_'+page
        with self.timings.measure('page_wait'):receipt=native.run(self.tasker,entry)
        self._checkpoint(batch);root=_root(receipt,entry);raw=root.get('recognition')
        names=['LabyDateChangedObserved',_PAGES[page]]
        # Native Or includes failed earlier alternatives with box=None.
        if (type(raw) is not list or not 1<=len(raw)<=2
            or any(type(row) is not dict or row.get('algorithm')!='And' for row in raw)
            or [row.get('name') for row in raw]!=names[:len(raw)]
            or any(row.get('box') is not None for row in raw[:-1]) or not _box(raw[-1].get('box'))):
            raise BatchStopped('game_session_readback_unknown')
        if raw[-1]['name']=='LabyDateChangedObserved':raise BatchStopped('date_changed')
        if page=='menu':self._menu(raw[-1].get('detail'))
        return receipt

    def _after_title(self,batch):
        """Two fresh identical Notice/HOME frames; startup budget is shared."""
        self._checkpoint(batch);entry='LabyGameSessionAfterTitleObserved'
        wait='LabyGameSessionTitleStabilityWait'
        names=['LabyDateChangedObserved','LabyDisconnect6002Observed',
               'LabyClientNoticeObserved','LabyClientAfterTitleHomeObserved','LabyClientDownloadErrorObserved']
        started=time.monotonic();owner_deadline=native.RUN_DEADLINE.get()
        expires=min(owner_deadline,started+75)
        # Repeated endpoint reads share the original 75s pipeline budget and
        # preserve the Owner's earlier deadline; native conversion gets 85s max.
        token=native.RUN_DEADLINE.set(min(owner_deadline,started+85))
        previous_page=previous_native=wait_native=None;download_retries=0
        try:
            while True:
                self._checkpoint(batch);remaining=expires-time.monotonic()
                if remaining<=0:raise BatchStopped('game_session_readback_unknown')
                if not self.tasker.resource.override_pipeline({entry:dict(timeout=max(1,int(remaining*1000)))}):
                    raise BatchStopped('game_session_pipeline_unavailable')
                with self.timings.measure('page_wait'):receipt=native.run(self.tasker,entry)
                self._checkpoint(batch);raw=_root(receipt,entry).get('recognition')
                if (type(raw) is not list or not 1<=len(raw)<=len(names)
                    or any(type(row) is not dict or row.get('algorithm')!='And' for row in raw)
                    or [row.get('name') for row in raw]!=names[:len(raw)]
                    or any(row.get('box') is not None for row in raw[:-1]) or not _box(raw[-1].get('box'))):
                    raise BatchStopped('game_session_readback_unknown')
                hit=raw[-1]['name']
                if hit=='LabyDateChangedObserved':raise BatchStopped('date_changed')
                if hit=='LabyDisconnect6002Observed':raise BatchStopped('disconnected')
                if hit=='LabyClientDownloadErrorObserved':
                    if time.monotonic()>=expires:raise BatchStopped('game_session_readback_unknown')
                    # Retry ACK may leave the old dialog during its closing animation.
                    if download_retries:continue
                    download_retries+=1
                    fields=self._fields(self.opening_snapshot,input_node='LabyClientDownloadRetryOnce')
                    def retry_send(oid):
                        sent=self._input(batch,'LabyClientDownloadRetryOnce')
                        return self.stamp(operation_id=oid,**fields,input_action_verified=True,
                            client_page_verified=False,native=sent,input_native=sent)
                    batch.transaction('client_download_retry',fields,retry_send)
                    previous_page=previous_native=wait_native=None
                    continue
                if time.monotonic()>=expires:raise BatchStopped('game_session_readback_unknown')
                page='notice' if hit=='LabyClientNoticeObserved' else 'client_home'
                if page==previous_page:
                    receipt['title_stability']=dict(consecutive_frames=2,minimum_interval_ms=500,
                        page=page,previous_native=previous_native,wait_native=wait_native)
                    return page,receipt
                # A different actual page replaces the prior candidate; it can
                # never inherit HOME's stability or permanently skip Notice.
                previous_page,previous_native=page,receipt
                if expires-time.monotonic()<.5:raise BatchStopped('game_session_readback_unknown')
                with self.timings.measure('page_wait'):wait_native=native.run(self.tasker,wait)
                self._checkpoint(batch);_root(wait_native,wait)
        finally:native.RUN_DEADLINE.reset(token)

    def _menu(self,rows):
        if (type(rows) is not list or len(rows)!=len(_MENU_LABELS)
            or any(type(row) is not dict for row in rows)
            or [row.get('name') for row in rows]!=[name for name,_,_ in _MENU_LABELS]):
            raise BatchStopped('game_session_readback_unknown')
        for row,(name,text,roi) in zip(rows,_MENU_LABELS):
            detail=row.get('detail');words=detail.get('filtered') if type(detail) is dict else None
            if (row.get('algorithm')!='OCR' or type(words) is not list or len(words)!=1
                or type(words[0]) is not dict or detail.get('best')!=words[0]
                or row.get('box')!=words[0].get('box') or words[0].get('text')!=text):
                raise BatchStopped('game_session_readback_unknown')
            box=words[0].get('box');score=words[0].get('score')
            if not _box(box) or type(score) not in (int,float):raise BatchStopped('game_session_readback_unknown')
            try:valid=math.isfinite(score) and .8<=score<=1
            except (ValueError,OverflowError):valid=False
            x,y,w,h=box;rx,ry,rw,rh=roi
            if not valid or not (rx<=x and ry<=y and x+w<=rx+rw and y+h<=ry+rh):
                raise BatchStopped('game_session_readback_unknown')

    def _entry(self,batch,*,recovery=False):
        self._checkpoint(batch)
        entry='LabyGameSessionRecoveryObserved' if recovery else 'LabyGameSessionEntryObserved'
        names=['LabyDateChangedObserved','LabyClientTitleObserved','LabyClientGameHomeObserved'] if recovery else [
            'LabyDateChangedObserved','LabyDisconnect6002Observed','LabyClientTitleObserved',
            'LabyClientGameHomeObserved','LabyClientAdventureObserved']
        token=native.RUN_DEADLINE.set(min(native.RUN_DEADLINE.get(),time.monotonic()+30)) if recovery else None
        try:
            with self.timings.measure('page_wait'):receipt=native.run(self.tasker,entry)
        finally:
            if token is not None:native.RUN_DEADLINE.reset(token)
        self._checkpoint(batch);raw=_root(receipt,entry).get('recognition')
        if (type(raw) is not list or not 1<=len(raw)<=len(names)
            or any(type(row) is not dict or row.get('algorithm')!='And' for row in raw)
            or [row.get('name') for row in raw]!=names[:len(raw)]
            or any(row.get('box') is not None for row in raw[:-1]) or not _box(raw[-1].get('box'))):
            raise BatchStopped('game_session_readback_unknown')
        hit=raw[-1]['name']
        if hit=='LabyDateChangedObserved':raise BatchStopped('date_changed')
        return next(page for page,node in _PAGES.items() if node==hit),receipt

    def _title(self,batch,expected):
        # A genuine separately completed native title receipt, not an OR leaf
        # renamed into a receipt. Prevalidate its one actual ID word first.
        self._checkpoint(batch)
        with self.timings.measure('page_wait'):receipt=native.run(self.tasker,'LabyClientTitleObserved')
        self._checkpoint(batch);root=_root(receipt,'LabyClientTitleObserved');raw=root.get('recognition')
        names=['LabyClientTitleLogo','LabyClientTitleStartLabel','LabyClientTitleVersion','LabyClientTitlePlayerId']
        if (type(raw) is not list or len(raw)!=4 or any(type(row) is not dict for row in raw)
            or {row.get('name') for row in raw}!=set(names)):raise BatchStopped('game_session_readback_unknown')
        row=next(row for row in raw if row['name']=='LabyClientTitlePlayerId');detail=row.get('detail')
        words=detail.get('filtered') if type(detail) is dict else None
        if (row.get('algorithm')!='OCR' or type(words) is not list or len(words)!=1
            or type(words[0]) is not dict or detail.get('all')!=words
            or detail.get('best')!=words[0] or row.get('box')!=words[0].get('box')):
            raise BatchStopped('game_account_unknown')
        score=words[0].get('score')
        if type(score) not in (int,float) or not .8<=score<=1 or not math.isfinite(score):
            raise BatchStopped('game_account_unknown')
        try:actual=title_player_id(receipt)
        except (ValueError,TypeError,OverflowError):raise BatchStopped('game_account_unknown') from None
        if actual!=expected:raise BatchStopped('game_account_mismatch')
        return actual,receipt

    def _input(self,batch,node):
        self._checkpoint(batch)
        # Called only inside a newly journaled transaction after duplicate/unknown
        # gates. Maa's max_hit is global to Context, so scope its reset to this
        # one navigation node for this new intent, never to a business budget.
        with self.timings.measure('page_wait'):receipt=native.run(self.tasker,node,reset_hits=(node,))
        self._checkpoint(batch);root=_root(receipt,node);action=root.get('action')
        if (type(action) is not dict or action.get('name')!=node
            or action.get('success') is not True):
            raise BatchStopped('game_session_input_unknown')
        return receipt

    def _fields(self,snapshot,**extra):
        fields=dict(enter_id=snapshot.enter_id,protocol_enter_id=snapshot.enter_id,**extra)
        if self.creation_operation_id is not None:fields['creation_operation_id']=self.creation_operation_id
        return fields

    def restore_new_opening(self,batch,snapshot,*,current_block_id,creation_operation_id=None):
        """Caller supplies the actual protocol position and the Owner's retained ENTER or untouched-opening read.

        Internal transaction stamps use the Owner callback unchanged. The
        returned facts deliberately lack Batch/Acquisition handoff fields,
        verified/frame/binding, guild/difficulty, and full-map/clear claims.
        """
        batch.checkpoint()
        if (type(snapshot) is not OpeningSnapshot or (current_block_id is not None and type(current_block_id) is not int)
            or current_block_id!=snapshot.protocol_current_block_id
            or type(snapshot.account_id) is not int or snapshot.account_id!=batch.binding.account_id
            or type(snapshot.enter_id) is not int or snapshot.enter_id<=0):
            raise BatchStopped('game_session_new_opening_required')
        if current_block_id not in (None,0):
            require=getattr(batch.port,'require_created_opening',None)
            if not callable(require):raise BatchStopped('game_session_new_opening_required')
            require(snapshot,batch=batch)
            if current_block_id!=snapshot.initial_start_block_id:raise BatchStopped('game_session_new_opening_required')
        if creation_operation_id is not None:
            require=getattr(batch.port,'require_created_opening',None)
            if not callable(require) or type(creation_operation_id) is not str or not creation_operation_id:
                raise BatchStopped('game_session_new_opening_required')
            require(snapshot,batch=batch)
            creation=getattr(batch.port,'creations',{}).get(snapshot.enter_id)
            if type(creation) is not dict or creation.get('operation_id')!=creation_operation_id:
                raise BatchStopped('game_session_new_opening_required')
        try:events=batch.journal.events()
        except Exception:raise BatchStopped('game_session_journal_unknown') from None
        if events and events[-1]['state']=='in_flight':raise BatchStopped('blocked_in_flight')
        key=(snapshot.enter_id,creation_operation_id)
        if key in self.used or any(event.get('operation') in _OPERATIONS
                and event.get('enter_id',event.get('protocol_enter_id'))==snapshot.enter_id
                and (creation_operation_id is None or event.get('creation_operation_id')==creation_operation_id)
                for event in events):
            raise BatchStopped('game_session_already_attempted')
        self._checkpoint(batch);self.used.add(key);self.creation_operation_id=creation_operation_id;self.opening_snapshot=snapshot
        try:
            pipeline=game_session_pipeline(batch.binding.account_id)
            if not self.tasker.resource.override_pipeline(pipeline):raise BatchStopped('game_session_pipeline_unavailable')
            initial,_=self._entry(batch)
            if initial=='disconnect':
                return self._recover(batch,snapshot,'client_return_title','LabyDisconnectReturnTitleOnce')
            if initial in ('client_home','adventure'):
                return self._logged(batch,snapshot,initial,recovered=False)
            return self._from_title(batch,snapshot,initial)
        except BaseException:
            self.blocked=True
            raise

    def _from_title(self,batch,snapshot,initial,*,actual=None,account_native=None):
        # Click ACK and its fresh page follow-up have different postconditions.
        # A startup wait cannot turn a confirmed Click into an unknown input.
        plan=(
            ('client_return_title','LabyDisconnectReturnTitleOnce','title'),
            ('client_title_start','LabyClientTitleStartOnce','notice_or_client_home'),
            ('client_notice_close','LabyClientNoticeCloseOnce','client_home'),
            ('client_open_adventure','LabyClientHomeToAdventure','adventure'),
            ('client_open_labyrinth','LabyClientOpenLabyrinthOnce','labyrinth_home'),
        )
        if initial=='title':
            if account_native is None:
                actual,account_native=self._title(batch,batch.binding.account_id)
                batch.observation(self.stamp(source='maa',**self._fields(snapshot),
                    session_page='title',title_player_id=actual,account_source='client_title_ocr',
                    native=account_native,account_native=account_native))
            plan=plan[1:]
        notice_required=True
        for operation,node,page in plan:
            if operation=='client_notice_close' and not notice_required:continue
            self._checkpoint(batch)
            fields=self._fields(snapshot,navigation_target=page,input_node=node)
            def send(oid,node=node,fields=fields):
                sent=self._input(batch,node)
                account={} if actual is None else dict(title_player_id=actual,
                    account_source='client_title_ocr',account_native=account_native)
                return self.stamp(operation_id=oid,**fields,**account,
                    input_action_verified=True,client_page_verified=False,native=sent,input_native=sent)
            clicked=batch.transaction(operation,fields,send)
            self._checkpoint(batch)
            if page=='notice_or_client_home':
                observed_page,observed=self._after_title(batch)
                notice_required=observed_page=='notice'
            else:
                observed_page=page;observed=self._wait(batch,page)
            if page=='title':
                actual,observed=self._title(batch,batch.binding.account_id);account_native=observed
            if actual is None:raise BatchStopped('game_account_unknown')
            self._checkpoint(batch)
            result=batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,
                session_page=observed_page,title_player_id=actual,account_source='client_title_ocr',
                account_native=account_native,input_action_verified=True,client_page_verified=True,
                native=observed,input_native=clicked['input_native']),**fields,client_page_verified=True)
        return ClientSessionFacts('labyrinth_home',actual,result['native'],account_native=account_native)

    def _navigate(self,batch,snapshot,operation,node,page,*,actual=None,account_native=None):
        fields=self._fields(snapshot,session_page=page)
        def send(oid):
            sent=self._input(batch,node)
            return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True,client_page_verified=False)
        clicked=batch.transaction(operation,fields,send)
        observed=self._wait(batch,page);self._checkpoint(batch)
        account={} if actual is None else dict(title_player_id=actual,
            account_source='client_profile_ocr',account_native=account_native)
        return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,**account,native=observed,input_native=clicked['input_native'],client_page_verified=True),**fields)

    def _recover(self,batch,snapshot,operation,node):
        fields=self._fields(snapshot)
        def recovery_send(oid):
            sent=self._input(batch,node)
            return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
        clicked=batch.transaction(operation,fields,recovery_send)
        page,observed=self._entry(batch,recovery=True);account={}
        if page=='title':
            uid,observed=self._title(batch,batch.binding.account_id)
            account=dict(title_player_id=uid,account_source='client_title_ocr',account_native=observed)
        self._checkpoint(batch)
        restored=batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,**account,session_page=page,
            native=observed,input_native=clicked['input_native']),**fields)
        if restored['session_page']=='title':
            return self._from_title(batch,snapshot,'title',actual=restored['title_player_id'],
                account_native=restored['account_native'])
        return self._logged(batch,snapshot,'client_home',recovered=True)

    def _logged(self,batch,snapshot,initial,*,recovered):
        if initial=='client_home':
            self._navigate(batch,snapshot,'client_open_adventure','LabyClientHomeToAdventure','adventure')
        self._navigate(batch,snapshot,'client_open_main_menu','LabyClientOpenMainMenuOnce','menu')
        fields=self._fields(snapshot)
        actual=None;account_native=None
        def profile_send(oid):
            sent=self._input(batch,'LabyClientOpenProfileOnce')
            return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
        clicked=batch.transaction('client_open_profile',fields,profile_send)
        def profile_readback():
            nonlocal actual,account_native
            try:
                self._checkpoint(batch)
                observed=observe_profile(self.tasker,stopped=batch.stopped,timeout=10)
                self._checkpoint(batch)
            except (ValueError,TypeError,OverflowError):
                raise BatchStopped('game_account_unknown') from None
            except BatchStopped as error:
                if str(error)!='disconnected':raise
                # The observer's exception carries no receipt. Read 6002 again
                # independently before settling only this navigation outcome.
                observed=self._wait(batch,'disconnect')
                return self.stamp(operation_id=clicked['operation_id'],**fields,session_page='disconnect',
                    native=observed,input_native=clicked['input_native'])
            try:actual=profile_player_id(observed)
            except (ValueError,TypeError,OverflowError):raise BatchStopped('game_account_unknown') from None
            if actual!=batch.binding.account_id:raise BatchStopped('game_account_mismatch')
            account_native=observed
            return self.stamp(operation_id=clicked['operation_id'],**fields,session_page='profile',title_player_id=actual,
                account_source='client_profile_ocr',account_native=observed,native=observed,input_native=clicked['input_native'])
        profile=batch.observation(profile_readback(),**fields)
        if profile['session_page']=='disconnect':
            if recovered:raise BatchStopped('disconnected')
            return self._recover(batch,snapshot,'client_profile_return_title','LabyClientProfileReturnTitleOnce')
        self._navigate(batch,snapshot,'client_profile_back','LabyClientProfileBackOnce','menu',
            actual=actual,account_native=account_native)
        self._navigate(batch,snapshot,'client_menu_to_adventure','LabyClientMenuToAdventureOnce','adventure',
            actual=actual,account_native=account_native)
        final=self._navigate(batch,snapshot,'client_open_labyrinth','LabyClientOpenLabyrinthOnce','labyrinth_home',
            actual=actual,account_native=account_native)
        return ClientSessionFacts('labyrinth_home',actual,final['native'],
            account_source='client_profile_ocr',account_native=account_native)
