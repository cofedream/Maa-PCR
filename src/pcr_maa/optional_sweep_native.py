"""Maa port for one investigation stage or the currently available tower cloister."""
from copy import deepcopy
import json
import traceback
from uuid import uuid4

from maa.custom_recognition import CustomRecognition

from . import native
from .game_day import current_game_day


class _FrameGuard(CustomRecognition):
    def __init__(self, guard):
        super().__init__()
        self.guard = guard
        self.token = uuid4().hex

    def analyze(self, context, argv):
        try:
            if argv.image.shape != (720, 1280, 3) or argv.image.dtype.name != 'uint8':
                return None
            result = self.guard(context, argv)
            if result is None:
                return None
            box, observation = result
            if len(box) != 4 or not all(type(i) is int for i in box):
                return None
            x, y, width, height = box
            if not (0 <= x < x + width <= 1280 and 0 <= y < y + height <= 720):
                return None
            return self.AnalyzeResult(box, dict(token=self.token, observation=observation))
        except Exception:
            traceback.print_exc()
            return None


class NativeOptionalSweep:
    def __init__(self, tasker, module, stage, *, game_day=None):
        if module == 'tower_cloister':
            if stage is not None:
                raise ValueError('unexpected_cloister_stage')
        elif module not in ('sanctum_survey', 'temple_survey') or type(stage) is not int or not 1 <= stage <= 999:
            raise ValueError('invalid_investigation_stage')
        self.tasker, self.module, self.stage = tasker, module, stage
        self.game_day = current_game_day() if game_day is None else game_day
        self._entered = False
        self._offered = self._dispatched = None

    @staticmethod
    def words(context, argv):
        read = context.run_recognition('OptionalSweepWords', argv.image)
        return read.raw_detail.get('filtered', []) if read and read.hit else []

    def frame(self, guard, *, click=False, timeout_ms=5000):
        """Recognize each candidate frame anew. Only the guarded hit box can receive input."""
        entry = 'OptionalSweepAct' if click else 'OptionalSweepObserve'
        node = 'OptionalSweepClick' if click else 'OptionalSweepRead'
        return self._run_frame(guard, entry, node, {entry: {'timeout': timeout_ms}}, timeout_ms)

    def scroll_list(self, guard, *, down):
        """Start the drag inside the guarded row and keep its end inside the list."""
        if type(down) is not bool:
            raise ValueError('invalid_optional_scroll_direction')
        end_y = 178 if down else 518
        return self._run_frame(guard, 'OptionalSweepScroll', 'OptionalSweepDrag',
                               {'OptionalSweepDrag': {'end': [990, end_y, 12, 8]}}, 5000)

    def _run_frame(self, guard, entry, node, overrides, timeout_ms):
        if type(timeout_ms) is not int or not 0 < timeout_ms <= 30000:
            raise ValueError('invalid_optional_sweep_timeout')
        # Tasker.resource creates a borrowed SDK view. Retain callback state on
        # the Resource used by bind(), or on the Agent's scoped callback registry.
        resource = (self.tasker.resource if isinstance(self.tasker, native.ContextTasker)
                    else getattr(self.tasker, '_resource_holder', None))
        if resource is None or getattr(resource, '_optional_sweep_unreleased', None) is not None:
            raise RuntimeError('optional_sweep_resource_owner_unavailable')
        if not resource.override_pipeline(overrides):
            raise RuntimeError('optional_sweep_pipeline_unavailable')
        input_node = node != 'OptionalSweepRead'
        callback = _FrameGuard(guard)
        if not resource.register_custom_recognition('OptionalSweepPage', callback):
            raise RuntimeError('optional_sweep_callback_unavailable')
        try:
            receipt = native.run(self.tasker, entry, reset_hits=(node,) if input_node else ())
        finally:
            if not resource.unregister_custom_recognition('OptionalSweepPage'):
                resource._optional_sweep_unreleased = callback
                raise RuntimeError('optional_sweep_callback_release_failed')
        nodes = [n for n in receipt['nodes'] if n['name'] == node and n['completed']]
        if input_node:
            native.require_native_input(receipt, (node,))
        if not receipt['native_ok'] or not receipt['tasker_inited'] or len(nodes) != 1:
            raise RuntimeError('optional_sweep_frame_unconfirmed')
        best = (nodes[0].get('recognition') or {}).get('best') or {}
        detail = best.get('detail')
        if isinstance(detail, str):
            detail = json.loads(detail)
        if not isinstance(detail, dict) or detail.get('token') != callback.token:
            raise RuntimeError('optional_sweep_receipt_mismatch')
        return dict(detail['observation'], native_ok=True, native_receipt=receipt)

    def read(self):
        from .optional_sweep_navigation import navigate
        if self._entered:
            return self.read_stage()
        reading = navigate(self)
        self._entered = reading.get('state') == 'ready'
        return reading

    def read_stage(self):
        from .optional_sweep_navigation import read_stage
        return read_stage(self)

    def quote(self, count):
        from .optional_sweep_quote import quote
        if not self._entered or self._offered is not None or type(count) is not int or count <= 0:
            raise ValueError('optional_sweep_quote_not_available')
        offered = quote(self, count)
        self._offered = deepcopy(offered)
        return offered

    def submit(self, quote):
        from .optional_sweep_quote import submit
        if self._offered is None or self._offered != quote or self._dispatched is not None:
            raise ValueError('optional_sweep_quote_stale')
        self._dispatched = self._offered
        return submit(self, quote)

    def result(self, quote):
        from .optional_sweep_quote import result
        if self._dispatched is None or self._dispatched != quote:
            raise ValueError('optional_sweep_no_matching_dispatch')
        return result(self, quote)

    def home(self):
        from .optional_sweep_navigation import return_home
        return return_home(self)
