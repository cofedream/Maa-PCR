"""Fixed AgentServer routes for one explicitly scoped business callback.

Install once before AgentServer.start_up; inside the business entry callback use
``with registry.scope(context, deadline=time.monotonic() + budget) as tasker:``.
Pass that facade to existing business functions. Exceptions propagate to the
entry, which must return an explicit failure to the SDK. No retry is performed.

The registry is process-local, single-executor, and owns only Python callbacks.
A fresh capability in a disabled Context-only node correlates each synchronous
run with nested RPC callbacks across SDK threads. No ContextVar, GUI Resource
mutation, tasker handle identity, or 'currently active means related' fallback.
Callbacks may nest (e.g. OneKeyPage -> home guard); only the entry thread may
change bindings or dispatch runs. This is not a persistent transaction ledger.
"""
from contextlib import contextmanager
import math
import threading
import time
from uuid import uuid4

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from .context_backend import ContextTasker
from .home_guard import HomeNoKnownLargePanel


RECOGNITIONS = frozenset({
    'OneKeySnapshot', 'OneKeyPage', 'HousePage', 'SchedulePage', 'ScheduleDecision',
    'PresetPage', 'CountFitNumber', 'ConfirmationPage', 'ConfirmationCommitGuard',
    'CurrentResultEvidence', 'CurrentClanPrompt', 'EventPage', 'ClosedAdventurePage',
    'ActivityEntryAbsent', 'ActivityHomePage',
    'AbyssHomePage', 'AbyssOpenPage', 'AbyssQuotePage', 'AbyssConfirmedQuote',
    'AbyssResultPage',
    'AbyssTicketPage',
    # Historical routes stay compatible; GUI labyrinth uses a separate Owner.
    'LabyrinthCharacters', 'LabyrinthScene', 'LabyrinthShop', 'LabyrinthEvent',
    'LabyrinthAutoplayTarget', 'LabyRelicSelectedIconCapture',
})
ACTIONS = frozenset({'CollectSchedule', 'CollectPresetPage', 'CollectConfirmation', 'CollectEvent'})
HOME = 'HomeNoKnownLargePanel'
_MARKER = '__PCR_AgentCallbackScope'


class _RecognitionRoute(CustomRecognition):
    def __init__(self, registry, name):
        super().__init__()
        self.registry, self.name = registry, name

    def analyze(self, context, argv):
        return self.registry._dispatch('recognition', self.name, context, argv)


class _ActionRoute(CustomAction):
    def __init__(self, registry, name):
        super().__init__()
        self.registry, self.name = registry, name

    def run(self, context, argv):
        return self.registry._dispatch('action', self.name, context, argv)


class AgentCallbacks:
    """One registry per AgentServer process; partial installation fails closed."""
    _installed = None
    _installation_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.RLock()
        self._active = None
        self._ready = False
        self._failed_tasks = set()
        self.home = HomeNoKnownLargePanel()
        self.recognitions = {n: _RecognitionRoute(self, n) for n in RECOGNITIONS | {HOME}}
        self.actions = {n: _ActionRoute(self, n) for n in ACTIONS}

    def install(self):
        from maa.agent.agent_server import AgentServer
        with self._installation_lock:
            if AgentCallbacks._installed is not None:
                raise RuntimeError('Agent callback registry already installed or installation failed')
            # Do not retry/replace partially installed native routes.
            AgentCallbacks._installed = self
            for name, route in sorted(self.recognitions.items()):
                if not AgentServer.register_custom_recognition(name, route):
                    raise RuntimeError('Cannot pre-register recognition: ' + name)
            for name, route in sorted(self.actions.items()):
                if not AgentServer.register_custom_action(name, route):
                    raise RuntimeError('Cannot pre-register action: ' + name)
            self._ready = True

    @contextmanager
    def scope(self, context, *, deadline, owner_source=None):
        """Borrow for one business run, release bindings even on BaseException.

        Deadline is explicit and applies on RPC threads too. Synchronous SDK
        work cannot be hard-interrupted; exit waits for normal stack unwinding.
        A failed root callback cannot open another scope in this registry.
        Stop is only a request, never input cancellation or controller idle.
        """
        if not math.isfinite(deadline):
            raise ValueError('A finite monotonic deadline is required')
        with self._lock:
            if not self._ready:
                raise RuntimeError('Agent routes not installed')
            if self._active is not None:
                raise RuntimeError('Concurrent or nested business scope rejected')
            task_id = context.get_task_job().job_id
            if task_id in self._failed_tasks:
                raise RuntimeError('Failed callback task is blocked; no retry')
            scope = _Scope(self, context, task_id, deadline)
            scope.owner_source = owner_source
            self._active = scope
        facade = ContextTasker(context, callback_scope=scope)
        scope.facade = facade
        try:
            with facade:
                scope.check()
                yield facade
                scope.check()
        except BaseException as exc:
            scope.fail(exc)
            raise
        finally:
            with self._lock:
                scope.token = None
                scope.bindings.clear()
                scope.closed = True
                if scope.error is not None or facade._blocked:
                    self._failed_tasks.add(task_id)
                # A transport timeout may return while a target still runs.
                # Revoke bindings now, but quarantine admission until its actual
                # return. No hard cancellation or controller-idle claim.
                if not scope.inflight:
                    self._active = None

    def _dispatch(self, kind, name, context, argv):
        miss = False if kind == 'action' else None
        scope = None
        associated = False
        leased = False
        try:
            # Native RPC here must not hold the registry lock: a nested callback
            # may be serviced by another SDK thread.
            marker = context.get_node_data(_MARKER)
            token = (marker or {}).get('attach', {}).get('token')
            with self._lock:
                scope = self._active
                if scope is None or not token or token != scope.token or scope.closed:
                    return miss
                if getattr(argv, 'custom_' + kind + '_name') != name:
                    return miss
                if argv.task_detail.task_id != scope.task_id or context.get_task_job().job_id != scope.task_id:
                    return miss
                associated = True
                scope.check(owner=False)
                target = self.home if name == HOME else scope.bindings.get(name)
                if target is None:
                    raise RuntimeError('Unbound callback: ' + name)
                if name in scope.inflight:
                    raise RuntimeError('Concurrent callback target rejected: ' + name)
                scope.inflight.add(name)
                leased = True
            result = target.run(context, argv) if kind == 'action' else target.analyze(context, argv)
            with self._lock:
                scope.check(owner=False)
                if kind == 'action':
                    if not (result is True or isinstance(result, CustomAction.RunResult) and result.success is True):
                        raise RuntimeError('Callback action failed: ' + name)
                return result
        except BaseException as exc:
            # Never let Python exceptions escape SDK ctypes callbacks, where
            # an undefined native return could otherwise look successful.
            if associated:
                scope.fail(exc)
            return miss
        finally:
            if leased:
                with self._lock:
                    scope.inflight.remove(name)
                    if scope.closed and not scope.inflight and self._active is scope:
                        self._active = None


class _Scope:
    def __init__(self, registry, context, task_id, deadline):
        self.registry, self.context = registry, context
        self.task_id, self.deadline = task_id, deadline
        self.owner = threading.get_ident()
        self.bindings = {}
        self.inflight = set()
        self.token = None
        self.error = None
        self.closed = False
        self.facade = None
        self.owner_source = None

    def fail(self, exc):
        with self.registry._lock:
            if self.error is None:
                self.error = exc
            self.token = None
            if self.facade is not None:
                self.facade._blocked = True

    def check_owner(self):
        if self.closed or self.registry._active is not self:
            raise RuntimeError('Callback scope closed or mismatched')
        if threading.get_ident() != self.owner:
            raise RuntimeError('Cross-thread scope use rejected')

    def check(self, *, owner=True):
        if owner:
            self.check_owner()
        if self.closed or self.registry._active is not self:
            raise RuntimeError('Callback scope closed or mismatched')
        if self.error is not None:
            raise RuntimeError('Callback scope blocked; no retry') from self.error
        if time.monotonic() >= self.deadline:
            raise RuntimeError('Callback scope deadline exceeded; outcome unknown')
        if self.context.tasker.stopping:
            self.facade._stop_requested = True
        if self.facade._blocked or self.facade._stop_requested:
            raise RuntimeError('Callback scope stopped or blocked; outcome unknown')

    def register(self, kind, name, target):
        with self.registry._lock:
            self.check_owner()
            try:
                self.check()
                allowed = RECOGNITIONS if kind == 'recognition' else ACTIONS
                cls = CustomRecognition if kind == 'recognition' else CustomAction
                if name not in allowed or not isinstance(target, cls):
                    raise RuntimeError('Unknown or mismatched callback registration: ' + name)
                if name in self.bindings:
                    raise RuntimeError('duplicate callback registration: ' + name)
                self.bindings[name] = target
                return True
            except BaseException as exc:
                self.fail(exc)
                raise

    def unregister(self, kind, name):
        with self.registry._lock:
            self.check_owner()
            allowed = RECOGNITIONS if kind == 'recognition' else ACTIONS
            if name not in allowed or name not in self.bindings:
                exc = RuntimeError('Unknown, unbound or mismatched callback unregistration: ' + name)
                self.fail(exc)
                raise exc
            del self.bindings[name]
            return True

    @contextmanager
    def dispatch(self):
        self.check()
        if self.token is not None:
            raise RuntimeError('Concurrent scope dispatch rejected')
        token = uuid4().hex
        try:
            if not self.context.override_pipeline({_MARKER: {
                    'enabled': False, 'attach': {'token': token}}}):
                raise RuntimeError('Cannot correlate callback Context')
            self.token = token
            yield
            if self.inflight:
                raise RuntimeError('Callback still in flight after SDK return; outcome unknown')
            self.check()
        except BaseException as exc:
            self.fail(exc)
            raise
        finally:
            # The Context-only disabled marker may persist, but no active token
            # accepts it after this run, including late RPCs and later scopes.
            self.token = None
