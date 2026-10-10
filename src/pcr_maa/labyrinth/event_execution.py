"""Single bounded, catalogue-backed event choice via Maa; CC BY-NC-SA 4.0."""
import time
from .. import native
from .batch import BatchStopped
from .events import Choice,Planner
from .roles import Context,configured_optimizer,strategy_value
from .event_observation import NODE,NAME,EventRecognition,event_pipeline,event_readback
from .scene import MaaSceneRuntime,runtime_resource
from .timing import Timings

class MaaEvent:
    def __init__(self,tasker,stamp,*,timings=None):
        self.tasker,self.stamp=tasker,stamp;self.timings=timings or Timings();self.attempted=set();self.blocked=False
        resource=runtime_resource(tasker)
        self.observer=getattr(resource,'_labyrinth_event_observer',None)
        if self.observer is None:
            self.observer=EventRecognition()
            if not resource.override_pipeline(event_pipeline()) or not resource.register_custom_recognition(NAME,self.observer):raise BatchStopped('event_install_failed')
            resource._labyrinth_event_observer=self.observer
        self.observer.last=None;self.observer.stable=0
        self.scene=MaaSceneRuntime(tasker,stamp,timings=self.timings)

    def capture(self):
        with self.timings.measure('page_wait'):receipt=native.run(self.tasker,NODE)
        return event_readback(receipt),receipt

    def run(self,batch,enter_id,block_id,*,scene=None,owned=(),stacks=None,context=Context(),area=None,locked_focus=None):
        if self.blocked or (enter_id,block_id) in self.attempted:raise BatchStopped('event_already_attempted')
        if stacks is None:raise BatchStopped('event_stacks_unknown')
        self.observer.stopped=batch.stopped;self.scene.stopped=batch.stopped;self.scene.observer.stopped=batch.stopped
        batch.checkpoint();first,receipt=self.capture();batch.checkpoint();current,receipt=self.capture()
        if current['event_id']!=first['event_id'] or current['trusted'] is not True:raise BatchStopped('event_unstable')
        strategy=getattr(batch.settings,'strategy',None)
        optimizer=configured_optimizer(strategy)
        policy={key:strategy_value(strategy,key,default) for key,default in (('baseline_last_area',3),('baseline_target',4),('debuff_pivot_minimum',12),('debuff_pivot_tolerance',2))}
        planner=Planner(optimizer,normal_win_rate=strategy_value(strategy,'normal_win_rate',95),extreme_win_rate=strategy_value(strategy,'extreme_win_rate',70),area=area,locked_focus=locked_focus,priority=strategy_value(strategy,'relic_mark_priority',()),**policy)
        self.attempted.add((enter_id,block_id))
        # Fixed upstream retires a committed choice after six seconds on the
        # same recognized event. Each replacement has its own transaction;
        # the original thirty-second result budget still bounds the whole set.
        rejected=set();sent=set();refusals=[]
        deadline=min(native.RUN_DEADLINE.get(),time.monotonic()+30)
        token=native.RUN_DEADLINE.set(deadline)
        allowed={'event','event_animation','free_role_reward','role_reward','joined','relic_reward','link_reward','item_reward','map','normal_challenge','ex_challenge','boss_challenge','chest','area_transition'}
        try:
            while True:
                batch.checkpoint()
                if time.monotonic()>=deadline:raise BatchStopped('event_result_unknown')
                choices=tuple(Choice(c['choice_id'],c['slot'],tuple(c['box']),c['enabled_confidence']) for c in current['choices'])
                with self.timings.measure('strategy'):decision=planner.decide(current['event_id'],choices,owned,context,stacks,trusted=True,rejected=frozenset(rejected))
                if decision is None:raise BatchStopped('event_choices_exhausted')
                if decision.action_safe is not True:raise BatchStopped('event_strategy_unknown')
                selected=decision.choice
                if selected.choice_id in sent:raise BatchStopped('event_already_attempted')
                fields=dict(enter_id=enter_id,block_id=block_id,event_id=current['event_id'],choice_id=selected.choice_id,phase='event_choice_observed')
                def send(oid):
                    batch.checkpoint()
                    nodes={'LabyEventSelectWord':dict(recognition='OCR',expected=r'^选择$',roi=list(selected.rect),threshold=.8,action='DoNothing',next=[]),
                        'LabyEventSelectGuard':dict(recognition='Custom',custom_recognition=NAME,custom_recognition_param=dict(event_id=current['event_id'],slot=selected.slot),roi=[0,0,1280,720],action='DoNothing',next=[]),
                        'LabyEventSelectOnce':dict(recognition='And',all_of=['LabyEventSelectWord','LabyEventSelectGuard'],action='Click',target=True,max_hit=1,next=[],timeout=3000,pre_delay=0,post_delay=0)}
                    if not self.tasker.resource.override_pipeline(nodes):raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='event_input_unknown'))
                    with self.timings.measure('page_wait'):receipt=native.run(self.tasker,'LabyEventSelectOnce',reset_hits=('LabyEventSelectOnce',))
                    done=next((n for n in receipt.get('nodes',[]) if n.get('name')=='LabyEventSelectOnce' and n.get('completed') is True),None)
                    if receipt.get('native_ok') is not True or done is None or done.get('action',{}).get('success') is not True:raise BatchStopped('event_input_unknown')
                    return self.stamp(operation_id=oid,**fields,native=receipt,input_native=receipt,input_action_verified=True,source='maa')
                sent.add(selected.choice_id)
                clicked=batch.transaction('event_choose',fields,send)
                batch.checkpoint();committed=time.monotonic()
                def advanced(value):
                    if value.kind!='event':return True
                    event=value.payload.get('event',{})
                    if type(event.get('event_id')) is not int:return False
                    if event['event_id']!=current['event_id']:return True
                    return event.get('trusted') is True and time.monotonic()-committed>=6
                after=self.scene.wait_for(batch,allowed,source='event',timeout=max(0,deadline-time.monotonic()),reason='event_result_unknown',accept=advanced,acquisition_fields=dict(source='maa',enter_id=enter_id,block_id=block_id,event_id=current['event_id'],choice_id=selected.choice_id))
                if after.kind=='event' and after.payload.get('event',{}).get('event_id')==current['event_id']:
                    # Re-read the real identity and current buttons. A last UNKNOWN
                    # frame or untrusted event never permits replacement input.
                    first,receipt=self.capture();batch.checkpoint();again,receipt=self.capture();batch.checkpoint()
                    if (first.get('event_id')!=current['event_id'] or again.get('event_id')!=current['event_id']
                            or again.get('trusted') is not True):raise BatchStopped('event_result_unknown')
                    rejected.add(selected.choice_id)
                    refusal=batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,page='event',
                        rejected_choice_ids=sorted(rejected),rejection_basis='fixed_upstream_commit_timeout',
                        inventory_added=False,native=after.native,event_native=receipt,input_native=clicked['input_native'],source='maa'),**fields)
                    refusals.append(refusal);current=again
                    continue
                # A next event/animation is known navigation, not an earned reward.
                return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,page=after.kind,
                    roles=list(owned),stacks=dict(stacks),inventory_added=False,event_rejections=refusals,
                    character_acquisition_active=(after.payload.get('acquisition_driver_advanced') is True and after.kind in ('item_reward','joined','event_animation')),
                    choices_attempted=sorted(sent),native=after.native,input_native=clicked['input_native'],source='maa'),**fields)
        except BaseException:self.blocked=True;raise
        finally:native.RUN_DEADLINE.reset(token)
