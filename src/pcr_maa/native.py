import json
import time
from threading import Event, RLock
from contextvars import ContextVar
from pathlib import Path
import cv2
import numpy as np
from maa.resource import Resource
from maa.tasker import Tasker
from maa.define import LoggingLevelEnum
from .replay import SavedImages
from .observation import summarize
from .context_backend import ContextTasker

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = ROOT / "assets/resource"
RUNTIME = ROOT / ".runtime"
RUN_DEADLINE = ContextVar('inspection_deadline', default=float('inf'))


def setup():
    global RUNTIME
    if RUNTIME == ROOT / '.runtime':
        from uuid import uuid4
        RUNTIME = RUNTIME / ('cli-' + uuid4().hex)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    Tasker.set_log_dir(RUNTIME / "maa")
    Tasker.set_stdout_level(LoggingLevelEnum.Off)


def wait_job(job, *, seconds=30):
    deadline = min(time.monotonic() + seconds, RUN_DEADLINE.get())
    while not job.done:
        if time.monotonic() >= deadline:
            raise TimeoutError(f'Native job exceeded {seconds} seconds or inspection deadline')
        time.sleep(0.05)
    return job


def load_resource(path=RESOURCE):
    setup()
    resource = Resource()
    job = wait_job(resource.post_bundle(path), seconds=90)
    if not job.succeeded:
        raise RuntimeError(f"Native Resource load failed: {path}")
    for name in ("det.onnx", "rec.onnx", "keys.txt"):
        if not (Path(path) / "model/ocr" / name).is_file():
            raise RuntimeError(f"OCR resource missing: {name}")
    from .home_guard import HomeNoKnownLargePanel
    # Maa's Resource owns a strong callback reference for its entire lifetime.
    # Register once per Resource, shared by observation and navigation tasks.
    if not resource.register_custom_recognition('HomeNoKnownLargePanel', HomeNoKnownLargePanel()):
        raise RuntimeError('Cannot register native home panel guard')
    return resource


def require_frame_size(width, height):
    # Layout only. Maa handles image/input scaling; binding checks remain separate.
    if (type(width) is not int or type(height) is not int
            or width <= 0 or height <= 0 or width * 9 != height * 16):
        raise RuntimeError('PCR 需要 16:9 横屏画面，请调整 MuMu 分辨率后重新开始。')


def write_png(path, image):
    # OpenCV's filename API cannot write Unicode Windows paths. Keep its PNG
    # encoder, but let Python handle the local filesystem path.
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        return False
    Path(path).write_bytes(encoded.tobytes())
    return True


def read_image(path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] not in ((1080, 1920), (720, 1280)):
        raise ValueError("Expected a readable 1920x1080 or 1280x720 PCR image")
    return cv2.resize(image, (1280, 720), interpolation=cv2.INTER_AREA)


def bind(resource, controller):
    if not controller.set_screenshot_target_short_side(720):
        raise RuntimeError("Cannot set 720 short side")
    if not controller.post_connection().wait().succeeded:
        raise RuntimeError("Controller connection failed")
    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise RuntimeError("Tasker not initialized")
    return tasker


class NativeInputNotSent(RuntimeError):
    """A failed dispatch with affirmative proof that no input action started."""
    def __init__(self, receipt, message='native_input_not_sent'):
        super().__init__(message)
        self.receipt = receipt


# Retain only faulted owners: dropping an active SDK owner can block in Destroy.
# ponytail: linear scan of faulted owners; a process restart releases quarantine.
_BLOCKED_TASKERS = []
_STOP_SLEEP = Event().wait


def tasker_blocked(tasker):
    handle = getattr(tasker, '_handle', None)
    return any(owner is tasker or (handle is not None and
               handle == getattr(owner, '_handle', None)) for owner in _BLOCKED_TASKERS)


def stop_tasker(tasker, *, seconds=5):
    """Post stop once, poll within its own budget, retain incomplete owners.

    A callback must return to its owner before a stop can complete. Stop completion
    is controller idle evidence only; it never proves that input was not sent.
    """
    if isinstance(tasker, ContextTasker):
        evidence = dict(requested=True, stop_completed=False, idle=False)
        try:
            tasker.post_stop()
        except BaseException as exc:
            evidence['error'] = f'{type(exc).__name__}: {exc}'
        return evidence
    previous = getattr(tasker, '_pcr_stop_evidence', None)
    if tasker_blocked(tasker) and previous is not None:
        return dict(previous)
    evidence = dict(requested=True, stop_completed=False, idle=False)
    tasker._pcr_stop_evidence = evidence
    if not tasker_blocked(tasker):
        _BLOCKED_TASKERS.append(tasker)
    try:
        deadline = time.monotonic() + max(0, seconds)
        job = tasker.post_stop()
        while not job.done:
            if time.monotonic() >= deadline:
                raise TimeoutError(f'Native stop exceeded {seconds} seconds; owner quarantined')
            _STOP_SLEEP(0.05)
        evidence['stop_completed'] = True
        evidence['idle'] = (job.succeeded is True and tasker.running is False
                            and getattr(tasker, 'stopping', False) is False)
        if not evidence['idle']:
            raise RuntimeError('Native stop did not establish idle; owner quarantined')
    except BaseException as exc:
        evidence['error'] = f'{type(exc).__name__}: {exc}'
    if evidence['idle']:
        _BLOCKED_TASKERS[:] = [owner for owner in _BLOCKED_TASKERS if owner is not tasker]
    return dict(evidence)


class _InputEvidence:
    """Reuse the labyrinth proof rules with one dispatch's paired native events."""
    def __init__(self, tasker, entry):
        self.tasker, self.entry = tasker, entry
        self.tasks, self.lock, self.error = {}, RLock(), None
        self.sinks = []

    def event(self, msg, detail):
        try:
            with self.lock:
                tid = detail['task_id']
                if msg == 'Tasker.Task.Starting' and detail['entry'] == self.entry:
                    if tid in self.tasks:
                        raise ValueError('duplicate native task start')
                    self.tasks[tid] = dict(entry=self.entry, task_id=tid, started=True,
                        finished=False, recognition_failed=False, action_started=False,
                        non_input_actions=[], actions={}, recognitions={})
                proof = self.tasks.get(tid)
                if proof is None:
                    return
                if proof['finished'] and msg.startswith('Node.'):
                    raise ValueError('native node event after task end')
                if msg in ('Tasker.Task.Succeeded', 'Tasker.Task.Failed'):
                    if detail['entry'] != self.entry or proof['finished']:
                        raise ValueError('mismatched native task end')
                    proof.update(finished=True, failed=msg.endswith('.Failed'))
                elif msg.startswith('Node.Recognition.'):
                    rid = detail['reco_id']
                    if msg.endswith('.Starting'):
                        if rid in proof['recognitions']:
                            raise ValueError('duplicate native recognition')
                        proof['recognitions'][rid] = detail['name']
                    else:
                        if proof['recognitions'].pop(rid, None) != detail['name']:
                            raise ValueError('unpaired native recognition')
                        if msg.endswith('.Failed'):
                            proof['recognition_failed'] = True
                elif msg.startswith('Node.Action.'):
                    aid = detail['action_id']
                    if msg.endswith('.Starting'):
                        if aid in proof['actions']:
                            raise ValueError('duplicate native action')
                        proof['actions'][aid] = dict(name=detail['name'], non_input=False, finished=False)
                    else:
                        action = proof['actions'].get(aid)
                        if not action or action['name'] != detail['name'] or action['finished']:
                            raise ValueError('unpaired native action')
                        raw = detail.get('action_details') or {}
                        action['finished'] = True
                        action['non_input'] = (msg.endswith('.Succeeded') and raw.get('success') is True
                            and raw.get('action_id') == aid and raw.get('name') == detail['name']
                            and raw.get('action') in ('DoNothing', 'StopTask'))
        except Exception as exc:
            # SDK notification callbacks swallow exceptions; invalidate the proof.
            self.error = f'{type(exc).__name__}: {exc}'

    def open(self):
        from maa.event_sink import EventSink
        owner = self
        class Sink(EventSink):
            # Do not use typed sink dispatch: it constructs borrowed SDK proxies
            # for every event, including within active custom callbacks.
            def _on_raw_notification(self, handle, msg, detail):
                if msg.startswith(('Tasker.Task.', 'Node.Recognition.', 'Node.Action.')):
                    owner.event(msg, detail)
        try:
            for add, remove, sink in ((self.tasker.add_sink, self.tasker.remove_sink, Sink()),
                                     (self.tasker.add_context_sink, self.tasker.remove_context_sink, Sink())):
                sid = add(sink)
                if sid is None:
                    raise RuntimeError('Native input proof sink unavailable')
                self.sinks.append((remove, sid))
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
        return self

    def close(self):
        for remove, sid in reversed(self.sinks):
            try:
                remove(sid)
            except Exception as exc:
                self.error = f'{type(exc).__name__}: {exc}'

    def receipt_proof(self, task_id):
        with self.lock:
            proof = self.tasks.get(task_id)
            if self.error or proof is None or len(self.sinks) != 2:
                return dict(source='native_task_events', complete=False, error=self.error or 'missing_task_events')
            result = {k: v for k, v in proof.items() if k not in ('actions', 'recognitions')}
            result['action_started'] = any(not a['non_input'] for a in proof['actions'].values())
            result['non_input_actions'] = [a['name'] for a in proof['actions'].values() if a['non_input']]
            result.update(source='native_task_events', complete=proof['finished']
                          and not proof['recognitions'] and all(a['finished'] for a in proof['actions'].values()))
            return result


def require_native_input(receipt, action_nodes):
    """Require all intended actions, or distinguish proven rejection from unknown."""
    from .result_checkpoint import action_completed
    if action_nodes and all(action_completed(receipt, name) for name in action_nodes):
        return receipt
    proof = receipt.get('native_evidence') or {}
    if (receipt.get('tasker_inited') is True and proof.get('source') == 'native_task_events' and proof.get('complete') is True
            and proof.get('task_id') == receipt.get('task_id') and proof.get('started') is True
            and proof.get('finished') is True and proof.get('recognition_failed') is True
            and proof.get('action_started') is False
            and all(n.get('action') is None or n['action'].get('name') in proof.get('non_input_actions', ())
                    for n in receipt.get('nodes', []))):
        receipt['input_execution'] = 'not_sent'
        raise NativeInputNotSent(receipt)
    error = RuntimeError('Native input outcome unknown; no retry')
    error.receipt = receipt
    raise error


def run(tasker, entry, *, reset_hits=()):
    if isinstance(tasker, ContextTasker):
        try:
            detail = tasker.run_task(entry, deadline=RUN_DEADLINE.get(), reset_hits=reset_hits)
            receipt = _receipt(detail, native_ok=bool(detail and detail.status.succeeded),
                               tasker_inited=tasker.inited,
                               task_id=detail.task_id if detail else None)
        except BaseException:
            # Includes lazy SDK node-detail failures during receipt conversion.
            tasker._blocked = True
            raise
        # Nested synchronous tasks share owner IDs. No correlated task start/end
        # proof is available here; do not attach sinks to borrowed/RPC proxies.
        return receipt
    if tasker_blocked(tasker):
        raise RuntimeError('Native tasker stopped or blocked; outcome unknown, no retry')
    if time.monotonic() >= RUN_DEADLINE.get():
        if reset_hits and hasattr(tasker, '_labyrinth_input_evidence'):
            raise NativeInputNotSent(dict(input_execution='not_sent',
                pre_input_validation_failed=True, reason='native_deadline_before_dispatch'))
        error = RuntimeError('Inspection deadline exceeded before task input')
        error.receipt = dict(native_ok=False, tasker_inited=False, task_id=None,
            nodes=[], input_execution='not_sent', native_evidence=dict(source='pre_dispatch',
            entry=entry, complete=True))
        # This might be a post-consumption result/cleanup dispatch. Only the
        # business adapter can classify its reserved consumer as not sent.
        raise error
    proof = _InputEvidence(tasker, entry).open()
    try:
        job = tasker.post_task(entry)
        deadline = min(time.monotonic() + 90, RUN_DEADLINE.get())
        while not job.done:
            if time.monotonic() >= deadline:
                raise RuntimeError('Native task exceeded 90 seconds or inspection deadline')
            time.sleep(0.05)
        detail = job.get()
        receipt = _receipt(detail, native_ok=job.succeeded, tasker_inited=tasker.inited,
                           task_id=job.job_id)
    except BaseException as exc:
        exc.native_stop = stop_tasker(tasker)
        raise
    finally:
        proof.close()
    receipt['native_evidence'] = proof.receipt_proof(receipt['task_id'])
    proof=getattr(tasker,'_labyrinth_input_evidence',{}).get(job.job_id)
    if (reset_hits and type(proof) is dict
            and proof.get('entry')==entry and proof.get('started') is True
            and proof.get('finished') is True and type(proof.get('failed')) is bool
            and proof.get('recognition_failed') is True and proof.get('action_started') is False
            and all(n['action'] is None or n['action']['name'] in proof.get('non_input_actions',()) for n in receipt['nodes'])):
        receipt['input_execution']='not_sent';receipt['native_evidence']=dict(proof)
        raise NativeInputNotSent(receipt)
    return receipt


def _receipt(detail, *, native_ok, tasker_inited, task_id):
    nodes = []
    for node in detail.nodes if detail else []:
        nodes.append({"name": node.name, "completed": node.completed,
                      "action": {"name": node.action.name, "success": node.action.success,
                                 "raw": node.action.raw_detail} if node.action else None,
                      "recognition": node.recognition.raw_detail if node.recognition else None})
    return {"native_ok": native_ok, "tasker_inited": tasker_inited,
            "task_id": task_id, "nodes": nodes,
            "navigation_complete": native_ok and any(n["name"] == "PresetsReached" and n["completed"] for n in nodes),
            "daily_complete": False}


def inspect(image, resource=None):
    resource = resource or load_resource()
    controller = SavedImages([[image]])
    tasker = bind(resource, controller)
    receipt = observe_tasker(tasker)
    receipt['source'] = 'offline replay'
    receipt['clicks'] = controller.clicks
    return receipt


def observe_tasker(tasker):
    """Fresh read on the already bound Tasker, without a second Resource binding."""
    receipt = run(tasker, "ReadScreen")
    raw = next((n["recognition"] for n in receipt["nodes"] if n["name"] == "ScreenOCR"), {}) or {}
    words = raw.get("all", [])
    receipt["observation"] = summarize(words)
    names = [n["name"] for n in receipt["nodes"] if n["completed"]]
    pages = {"ObservePresets": "presets", "ObserveMain": "main_map", "ObserveAdventure": "adventure", "ObserveHome": "home"}
    receipt["observation"]["page"] = next((page for name, page in pages.items() if name in names), "unknown")
    receipt["observation"]["selected_slot"] = next((i for i in range(1, 8) if f"SelectedSlot{i}" in names), None)
    return receipt


def save_receipt(receipt, label):
    path = RUNTIME / f"{label}-{time.time_ns()}.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
