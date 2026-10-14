"""Bounded session timings and same-frame analysis cache; no image retention.

Categories overlap: page_wait measures a guarded native task's whole elapsed
wait, while sinks measure its screenshot, recognition and controller input.
They must not be added together as an end-to-end duration. Swipe is a subset
of input; business phases include protocol and native work. None means that
no collector observed a category; an observed zero-event category has count 0.
"""
import asyncio
import math
from collections import defaultdict,deque
from contextlib import contextmanager
from threading import RLock
from time import perf_counter

class Timings:
    categories=frozenset(('screenshot','recognition','strategy','input','swipe','page_wait','protocol_request','business_phase'))
    labels={
        'protocol_request':frozenset(('authentication','sdk_login','labyrinth/top','labyrinth/enter','labyrinth/resume','labyrinth/retire')),
        'business_phase':frozenset(('dependencies','resources','connecting','authenticating','ready','opening','handoff','playing','cleared','moving','battle','shop','reward','team','recovering','retiring')),
    }
    def __init__(self):
        self.samples=defaultdict(lambda:deque(maxlen=256));self.stats={};self.lock=RLock()
        self.label_stats={};self.outcomes=defaultdict(lambda:defaultdict(int));self.phase=None
    def observe(self,category):
        if category not in self.categories:raise ValueError('unknown_timing_category')
        with self.lock:
            self.stats.setdefault(category,(0,0,0))
    def record(self,category,elapsed_ms,*,label=None,outcome=None):
        if category not in self.categories:raise ValueError('unknown_timing_category')
        if type(elapsed_ms) not in (int,float) or not math.isfinite(elapsed_ms) or elapsed_ms<0:raise ValueError('invalid_elapsed_time')
        if label is not None and label not in self.labels.get(category,()):raise ValueError('unknown_timing_label')
        if outcome not in (None,'succeeded','failed','unknown'):raise ValueError('unknown_timing_outcome')
        with self.lock:
            count,total,maximum=self.stats.get(category,(0,0,0))
            self.stats[category]=(count+1,total+elapsed_ms,max(maximum,elapsed_ms))
            self.samples[category].append(elapsed_ms)
            key=(category,label)
            if label is not None:
                count,total,maximum=self.label_stats.get(key,(0,0,0))
                self.label_stats[key]=(count+1,total+elapsed_ms,max(maximum,elapsed_ms))
            if outcome is not None:self.outcomes[key][outcome]+=1
    @contextmanager
    def measure(self,category,*,label=None):
        if category not in self.categories:raise ValueError('unknown_timing_category')
        if label is not None and label not in self.labels.get(category,()):raise ValueError('unknown_timing_label')
        start=perf_counter();sample={}
        try:
            yield sample
            outcome=sample.get('outcome','succeeded')
        except BaseException as error:
            outcome=sample.get('outcome','unknown' if isinstance(error,(asyncio.CancelledError,TimeoutError,KeyboardInterrupt)) else 'failed')
            raise
        finally:self.record(category,(perf_counter()-start)*1000,label=label,outcome=outcome)
    def transition(self,phase):
        if phase is not None and phase not in self.labels['business_phase']:raise ValueError('unknown_timing_label')
        with self.lock:
            now=perf_counter()
            if self.phase is not None:
                previous,start=self.phase
                self.record('business_phase',(now-start)*1000,label=previous)
            self.phase=(phase,now) if phase is not None else None
    def payload(self):
        def summary(values):
            n,t,m=values
            return dict(count=n,total_ms=t,max_ms=m,mean_ms=t/n if n else 0)
        with self.lock:
            result={k:summary(self.stats[k]) if k in self.stats else None for k in sorted(self.categories)}
            for category in self.categories:
                if result[category] is not None and (category,None) in self.outcomes:
                    result[category]['outcomes']=dict(self.outcomes[(category,None)])
            for category in self.labels:
                if result[category] is not None:
                    result[category]['labels']={label:summary(values) for (kind,label),values in self.label_stats.items() if kind==category}
                    for label,values in result[category]['labels'].items():
                        if (category,label) in self.outcomes:values['outcomes']=dict(self.outcomes[(category,label)])
            return result

class FrameCache:
    def __init__(self):self.frame_id=None;self.results={}
    def analyze(self,frame_id,key,calculate):
        if frame_id!=self.frame_id:self.frame_id=frame_id;self.results.clear()
        if key not in self.results:self.results[key]=calculate()
        return self.results[key]

class NativeTimings:
    """Attach to the Owner's actual Tasker/controller, including child Contexts.

    The sinks retain only IDs and elapsed times. An optional private native
    trace provides pipeline capture and leaf recognition durations, including
    Maa's bulk OCR preanalysis before Node.Recognition.Starting. No params, images, OCR words
    or account information. Detach after native completion, before disposal.
    """
    def __init__(self,tasker,controller,timings,*,trace_path=None,measure_page_wait=False,collect_input_evidence=True):
        from maa.controller import ControllerEventSink
        from maa.tasker import TaskerEventSink
        from maa.context import ContextEventSink
        from maa.event_sink import NotificationType
        self.tasker,self.controller,self.timings=tasker,controller,timings
        self.pending={};self.lock=RLock();self.closed=False;self.action_kinds={}
        self.input_evidence={}
        self.trace_path=trace_path
        self.trace_offset=trace_path.stat().st_size if trace_path and trace_path.exists() else 0
        owner=self
        class ControllerSink(ControllerEventSink):
            def on_controller_action(self,controller,noti_type,detail):
                action=detail.action.lower()
                # The private trace covers both external and pipeline captures.
                # Use one source so an external capture is counted only once.
                category='screenshot' if action=='screencap' and owner.trace_path is None else None
                if category:owner.event(('controller',detail.ctrl_id),category,noti_type,NotificationType)
        class TaskSink(TaskerEventSink):
            def on_tasker_task(self,tasker,noti_type,detail):
                if measure_page_wait:owner.event(('task',detail.task_id),'page_wait',noti_type,NotificationType)
                if not collect_input_evidence:return
                with owner.lock:
                    if noti_type==NotificationType.Starting:
                        # ponytail: retain the last 256 native tasks, not a second ledger.
                        if len(owner.input_evidence)>=256:owner.input_evidence.pop(next(iter(owner.input_evidence)))
                        owner.input_evidence[detail.task_id]=dict(entry=detail.entry,started=True,
                            finished=False,recognition_failed=False,action_started=False,non_input_actions=[])
                    elif detail.task_id in owner.input_evidence:
                        owner.input_evidence[detail.task_id].update(finished=True,failed=noti_type==NotificationType.Failed)
        class ContextSink(ContextEventSink):
            def on_node_recognition(self,context,noti_type,detail):
                with owner.lock:
                    evidence=owner.input_evidence.get(detail.task_id) if collect_input_evidence else None
                    if evidence is not None and noti_type==NotificationType.Failed:evidence['recognition_failed']=True
                if owner.trace_path is None:
                    owner.event(('recognition',detail.task_id,detail.reco_id),'recognition',noti_type,NotificationType)
            def on_node_action(self,context,noti_type,detail):
                if detail.name not in owner.action_kinds:
                    node=context.get_node_data(detail.name) or {}
                    kind=node.get('action')
                    if type(kind) is dict:kind=kind.get('type')
                    owner.action_kinds[detail.name]=kind
                with owner.lock:
                    evidence=owner.input_evidence.get(detail.task_id) if collect_input_evidence else None
                    # Proven DoNothing guards cannot send input. Custom or
                    # unknown actions may, so their uncertainty is retained.
                    if evidence is not None:
                        if owner.action_kinds[detail.name]=='DoNothing':
                            if detail.name not in evidence['non_input_actions']:evidence['non_input_actions'].append(detail.name)
                        else:evidence['action_started']=True
                if owner.action_kinds[detail.name] in ('Click','Swipe','MultiSwipe','TouchDown','TouchMove','TouchUp','ClickKey','InputText','KeyDown','KeyUp','StartApp','StopApp'):
                    owner.event(('input',detail.task_id,detail.action_id),'input',noti_type,NotificationType)
                if owner.action_kinds[detail.name] in ('Swipe','MultiSwipe'):
                    owner.event(('swipe',detail.task_id,detail.action_id),'swipe',noti_type,NotificationType)
        self.controller_sink=ControllerSink();self.context_sink=ContextSink();self.task_sink=TaskSink()
        self.task_id=self.controller_id=self.context_id=None
        try:
            self.task_id=tasker.add_sink(self.task_sink)
            self.controller_id=controller.add_sink(self.controller_sink)
            self.context_id=tasker.add_context_sink(self.context_sink)
            if self.controller_id is None or self.context_id is None or self.task_id is None:
                raise RuntimeError('native_timing_sink_unavailable')
            # Daily profiling must never replace the paired native input proof.
            if collect_input_evidence:tasker._labyrinth_input_evidence=self.input_evidence
        except Exception:
            self.close();raise
        for category in ('input','swipe')+ (('page_wait',) if measure_page_wait else ()):
            self.timings.observe(category)
        if trace_path is None:
            self.timings.observe('screenshot');self.timings.observe('recognition')
    def event(self,key,category,notification,types):
        with self.lock:
            if self.closed:return
            if notification==types.Starting:self.pending[key]=(category,perf_counter())
            elif notification in (types.Succeeded,types.Failed):
                started=self.pending.pop(key,None)
                if started:self.timings.record(started[0],(perf_counter()-started[1])*1000,
                    outcome='succeeded' if notification==types.Succeeded else 'failed')
    def finish_native_trace(self):
        # Maa 5.13's pipeline-internal captures bypass Controller.Action events.
        # Read only the Owner's new private trace tail, extracting scalar native
        # capture durations. Never add another screenshot merely for profiling.
        if not self.trace_path or not self.trace_path.exists():return
        self.timings.observe('screenshot');self.timings.observe('recognition')
        import re
        pattern=re.compile(r'ControlUnitMgr::screencap\] \| leave, (\d+)ms')
        recognition=re.compile(r'VisionNS::(?:OCRer|TemplateMatcher|ColorMatcher|FeatureMatcher|NeuralNetworkClassifier|NeuralNetworkDetector)::analyze\].*\[cost=(\d+)ms\]')
        with self.trace_path.open('rb') as handle:
            handle.seek(self.trace_offset)
            for line in handle:
                match=pattern.search(line.decode('utf-8',errors='replace'))
                if match:self.timings.record('screenshot',int(match.group(1)))
                match=recognition.search(line.decode('utf-8',errors='replace'))
                if match:self.timings.record('recognition',int(match.group(1)))
            self.trace_offset=handle.tell()
    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            now=perf_counter()
            for category,start in self.pending.values():
                self.timings.record(category,(now-start)*1000,outcome='unknown')
            self.pending.clear()
        if self.controller_id is not None:self.controller.remove_sink(self.controller_id)
        if self.context_id is not None:self.tasker.remove_context_sink(self.context_id)
        if self.task_id is not None:self.tasker.remove_sink(self.task_id)
