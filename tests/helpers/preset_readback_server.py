"""Official AgentServer entry for bounded synthetic native readback tests."""
import json
import os
from pathlib import Path
import sys
import time
import traceback

from maa.agent.agent_server import AgentServer  # Select server SDK mode first.
from maa.controller import Controller
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.resource import Resource
from maa.tasker import Tasker

from pcr_maa.agent_callbacks import AgentCallbacks
from pcr_maa.preset_execution_native import NativePreset


OUT = Path(sys.argv[2])
Tasker.set_log_dir(OUT / 'agent-maa')
Tasker.set_stdout_level(LoggingLevelEnum.Off)
registry = AgentCallbacks()
registry.install()
rows = []


def forbidden(*args, **kwargs):
    raise AssertionError('Agent readback may not create, register or destroy an owned Resource')


Resource.__init__ = Resource.__del__ = forbidden
Resource.register_custom_action = Resource.unregister_custom_action = forbidden
Resource.register_custom_recognition = Resource.unregister_custom_recognition = forbidden
tasker_init, controller_init = Tasker.__init__, Controller.__init__


def borrowed_tasker(self, *args, **kwargs):
    assert not args and kwargs.get('handle')
    tasker_init(self, *args, **kwargs)
    assert not self._own


def borrowed_controller(self, *args, **kwargs):
    assert not args and kwargs.get('handle')
    controller_init(self, *args, **kwargs)
    assert not self._own


Tasker.__init__, Controller.__init__ = borrowed_tasker, borrowed_controller


class Readback(CustomAction):
    def run(self, context, argv):
        row = dict(root_task=argv.task_detail.task_id,
                   borrowed=not context.tasker._own, pid=os.getpid())
        try:
            with registry.scope(context, deadline=time.monotonic() + 8) as facade:
                row['result'] = NativePreset(facade).verify_preset(2)
            row['ok'] = True
            return True
        except BaseException as exc:
            row.update(ok=False, error=repr(exc), traceback=traceback.format_exc())
            return False
        finally:
            row['cleaned'] = registry._active is None
            rows.append(row)
            pending = OUT / 'server-results.pending'
            pending.write_text(json.dumps(rows, indent=2), encoding='utf-8')
            pending.replace(OUT / 'server-results.json')


assert AgentServer.register_custom_action('SYNTHETIC_Readback', Readback())
assert AgentServer.start_up(sys.argv[1])
(OUT / 'ready').write_text(str(os.getpid()), encoding='utf-8')
try:
    until = time.monotonic() + 50
    while not (OUT / 'shutdown').exists():
        if time.monotonic() >= until:
            raise TimeoutError('Synthetic test owner did not request shutdown')
        time.sleep(0.02)
finally:
    AgentServer.shut_down()
    AgentServer.join()
