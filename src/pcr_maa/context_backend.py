"""Narrow, callback-scoped Tasker facade over an already borrowed Maa Context.

No owner construction, binding, destruction, logging setup, or worker threads.
The caller must keep the owner and callback alive for the entire ``with`` scope.
Context overrides affect this callback context (including subsequent nested runs),
not the owner's Resource. They are not rolled back on facade exit.
"""
import time
from contextlib import nullcontext


# Direct Tasker operations get a new Maa root; borrowed operations share one.
# Renew only these non-consuming operation budgets, keeping the JSON limits
# effective inside each dispatch. Transaction consumers are explicit below.
_OPERATION_HIT_BUDGETS = {
    'NavigatePresets': ('EnterAdventure', 'EnterMain', 'OpenPresets'),
    'InspectPreset': ('EnterAdventure', 'EnterMain', 'OpenPresets',
                      'ScanSelectClick', 'ScanToggle', 'ScanResetToggle',
                      'ScanShort', 'ScanTop', 'ScanBottom', 'ScanPage', 'ScanSwipe'),
    'ExecSubmit': ('ExecOpenSweep',),
    'ExecScanStart': ('ExecScanTop', 'ExecScanBottom', 'ExecScanShort',
                      'ExecScanPage', 'ExecScanSwipe'),
    'ExecCancelStart': ('ExecCancel',),
    'ReturnHome': ('ReturnPresetCancel', 'ReturnMainHome'),
    'FitIncreaseStart': ('FitIncrease',),
    'FitDecreaseStart': ('FitDecrease',),
    'EventScanStart': ('EventToggle', 'EventResetTop', 'EventTop',
                       'EventBottom', 'EventShort', 'EventPage', 'EventSwipe'),
    'RewardsMissionsOpenStart': ('RewardsMissionsOpen',),
    'RewardsGiftsOpenStart': ('RewardsGiftsOpen',),
    'RewardsMissionsHomeStart': ('RewardsMissionsHome',),
    'RewardsGiftsHomeStart': ('RewardsGiftsHome',),
}


class _ContextResource:
    def __init__(self, facade):
        self._facade = facade

    def override_pipeline(self, pipeline_override):
        self._facade._check_ready()
        if self._facade._callback_scope is not None:
            from .agent_callbacks import _MARKER
            if _MARKER in pipeline_override:
                raise RuntimeError('Reserved callback scope node')
        return self._facade._context.override_pipeline(pipeline_override)

    def get_node_data(self, name):
        self._facade._check_open()
        return self._facade._context.get_node_data(name)

    def _unsupported(self):
        self._facade._check_open()
        raise NotImplementedError(
            'Borrowed Context cannot register/unregister custom callbacks; '
            'Agent-side registration and callback state/lifetime need an explicit integration')

    def register_custom_recognition(self, name, recognition):
        return self._registration('recognition', name, recognition)

    def unregister_custom_recognition(self, name):
        return self._registration('recognition', name)

    def register_custom_action(self, name, action):
        return self._registration('action', name, action)

    def unregister_custom_action(self, name):
        return self._registration('action', name)

    def _registration(self, kind, name, *target):
        self._facade._check_open()
        scope = self._facade._callback_scope
        if scope is None:
            return self._unsupported()
        return scope.register(kind, name, target[0]) if target else scope.unregister(kind, name)


class _ContextController:
    def __init__(self, facade):
        self._facade = facade

    @property
    def cached_image(self):
        self._facade._check_open()
        return self._facade._context.tasker.controller.cached_image

    def post_screencap(self):
        self._facade._check_ready()
        return self._facade._context.tasker.controller.post_screencap()


class _StopRequest:
    """A request acknowledgement, deliberately NOT a completed TaskJob."""

    def wait(self):
        raise RuntimeError(
            'Cannot wait for owner stop inside a Context callback; return to the owner. '
            'Stop requested does not establish controller idle or input/result completion')


class ContextTasker:
    """Use only within a live CustomAction/recognition callback.

    ``running`` describes the owner tasker, not nested-task or controller status.
    ``post_stop`` posts once without waiting; its ``wait`` fails closed because
    waiting on the owner inside its callback can deadlock. Owner code must wait
    after callback return and separately establish input completion/result state.

    A synchronous SDK call cannot be hard-interrupted here. Deadlines are checked
    before entry and after return. An exception, stop, timeout or unsuccessful
    task blocks further runs on this facade; no recovery or retry is attempted.
    This is not a persistent deduplication ledger or a complete business port.
    """

    def __init__(self, context, *, callback_scope=None):
        self._context = context
        self._callback_scope = callback_scope
        self._closed = False
        self._blocked = False
        self._stop_requested = False
        self._stop_request = _StopRequest()
        self.resource = _ContextResource(self)
        self.controller = _ContextController(self)

    def __enter__(self):
        self._check_open()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._closed = True

    def _check_open(self):
        if self._closed:
            raise RuntimeError('Borrowed Context facade is closed')
        if self._callback_scope is not None:
            self._callback_scope.check_owner()

    @property
    def inited(self):
        self._check_open()
        return self._context.tasker.inited

    @property
    def running(self):
        self._check_open()
        return self._context.tasker.running

    @property
    def stop_requested(self):
        self._check_open()
        if self._context.tasker.stopping:
            self._stop_requested = True
        return self._stop_requested

    def _check_ready(self):
        self._check_open()
        if self._callback_scope is not None:
            self._callback_scope.check()
        from .native import tasker_blocked
        if self.stop_requested or self._blocked or tasker_blocked(self._context.tasker):
            raise RuntimeError('Borrowed Context stopped or blocked; outcome unknown, no retry')
        if not self.inited:
            raise RuntimeError('Borrowed Context tasker is not initialized')

    def observation_source(self):
        """Identity of this borrowed callback, certified by its live Owner.

        Remote resource/controller getter proxies are deliberately excluded.
        The Owner keeps actual objects alive and checks their current binding
        over its existing private channel before and after every observation.
        A registry scope alone does not certify the client's actual target.
        """
        self._check_ready()
        scope = self._callback_scope
        if scope is None or not callable(scope.owner_source):
            raise RuntimeError('Live Owner source verification required')
        if (scope.context is not self._context
                or self._context.get_task_job().job_id != scope.task_id
                or scope.owner_source(self._context) is not True):
            self._blocked = True
            raise RuntimeError('Owner observation binding changed or revoked')
        self._check_ready()
        return scope, self._context, scope.task_id, scope.owner_source

    def post_stop(self):
        self._check_open()
        self._blocked = True
        if not self.stop_requested:
            # Latch BEFORE calling SDK, including when the SDK raises.
            self._stop_requested = True
            self._context.tasker.post_stop()
        return self._stop_request

    def run_task(self, entry, *, deadline, reset_hits=()):
        self._check_ready()
        deadline = min(deadline, time.monotonic() + 90)
        if self._callback_scope is not None:
            deadline = min(deadline, self._callback_scope.deadline)
        try:
            if time.monotonic() >= deadline:
                raise RuntimeError('Inspection deadline exceeded before task input')
            if self._callback_scope is not None and self._callback_scope.owner_source is not None:
                self.observation_source()
            # Owner/stop/deadline checks precede all budget changes. Explicit
            # transaction nodes are supplied only by the one-shot business port;
            # a plain consumer run never renews its spent allowance.
            for node in _OPERATION_HIT_BUDGETS.get(entry, ()) + tuple(reset_hits):
                if not self._context.clear_hit_count(node):
                    raise RuntimeError('Cannot rearm native operation budget: ' + node)
            dispatch = self._callback_scope.dispatch() if self._callback_scope else nullcontext()
            with dispatch:
                detail = self._context.run_task(entry)
            # No post_task, wait, retry, or post_stop-and-wait on this path.
            if time.monotonic() >= deadline:
                raise RuntimeError('Context deadline exceeded after synchronous return; outcome unknown')
            if self.stop_requested:
                raise RuntimeError('Context stop requested; outcome unknown')
            if detail is None or not detail.status.succeeded:
                self._blocked = True
            return detail
        except BaseException:
            self._blocked = True
            raise
