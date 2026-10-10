"""Test-only official AgentServer child. Synthetic frames/data, no devices."""
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

from maa.agent.agent_server import AgentServer  # select server mode FIRST
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker
from maa.resource import Resource
from maa.controller import Controller
from maa.define import LoggingLevelEnum
from pcr_maa.agent_callbacks import AgentCallbacks, HOME
from pcr_maa.preset_scan import Collector
from pcr_maa import native

OUT = Path(sys.argv[2])
Tasker.set_log_dir(OUT / 'agent-maa')
Tasker.set_stdout_level(LoggingLevelEnum.Off)
registry = AgentCallbacks()
registry.install()
records = []


def forbid(*a, **kw):
    raise AssertionError('Borrowed Resource must not be registered, created or destroyed')


Resource.__init__ = Resource.__del__ = forbid
Resource.register_custom_recognition = Resource.unregister_custom_recognition = forbid
Resource.register_custom_action = Resource.unregister_custom_action = forbid
tasker_init, controller_init = Tasker.__init__, Controller.__init__


def borrowed_tasker(self, *a, **kw):
    assert kw.get('handle') and not a
    tasker_init(self, *a, **kw)
    assert not self._own


def borrowed_controller(self, *a, **kw):
    assert kw.get('handle') and not a
    controller_init(self, *a, **kw)
    assert not self._own


Tasker.__init__, Controller.__init__ = borrowed_tasker, borrowed_controller
home_analyze = registry.home.analyze


def home(context, argv):
    records[-1]['home_ids'].append(id(registry.home))
    return home_analyze(context, argv)


registry.home.analyze = home


class Entry(CustomAction):
    def run(self, context, argv):
        return self.business(context, argv)

    def business(self, context, argv):
        case = json.loads(argv.custom_action_param)['case']
        row = dict(case=case, entry_thread=threading.get_ident(), recog_threads=[],
                   action_threads=[], home_ids=[], calls=0, cleaned=False,
                   root_task=argv.task_detail.task_id, borrowed=not context.tasker._own)
        records.append(row)
        collector = Collector()
        real_collect = collector.run

        def collect(ctx, args):
            row['action_threads'].append(threading.get_ident())
            return real_collect(ctx, args)

        collector.run = collect
        unrelated = context.clone()

        class Page(CustomRecognition):
            def analyze(self, ctx, args):
                row['calls'] += 1
                row['recog_threads'].append(threading.get_ident())
                row['nested_task'] = args.task_detail.task_id
                if case == 'exception':
                    raise ValueError('SYNTHETIC callback fault')
                if case == 'late':
                    time.sleep(0.15)
                if case == 'normal':
                    rejected = unrelated.run_recognition('Read', args.image)
                    row['unrelated_missed'] = rejected is not None and not rejected.hit
                guard = ctx.run_recognition('Home', args.image)
                row['home_missed'] = guard is not None and not guard.hit
                return self.AnalyzeResult([0, 0, 1, 1], {
                    'kind': 'short', 'gaps': False, 'rows': [dict(
                        title='SYNTHETIC-1', category='主线关卡', difficulty='困难',
                        remaining=3, limit=3, checked=True)]})

        page = Page()

        class ThreadedTransport:
            # The real synchronous SDK call pumps RPC on a separate thread.
            def __getattr__(self, name):
                return getattr(context, name)

            def run_task(self, entry):
                results, errors = [], []

                def invoke():
                    try:
                        results.append(context.run_task(entry))
                    except BaseException as exc:
                        errors.append(exc)

                transport = threading.Thread(target=invoke)
                transport.start()
                transport.join(12)
                if transport.is_alive():
                    raise TimeoutError('test SDK call did not return')
                if errors:
                    raise errors[0]
                return results[0]

        try:
            deadline = time.monotonic() + (-1 if case == 'expired' else 0.06 if case == 'late' else
                                           0.1 if case == 'rpc_timeout' else 10)
            with registry.scope(ThreadedTransport(), deadline=deadline) as facade:
                row['scope_id'] = id(facade._callback_scope)
                assert facade.resource.register_custom_recognition('PresetPage', page)
                assert facade.resource.register_custom_action('CollectPresetPage', collector)
                if case == 'rpc_timeout':
                    # Do not call an expired Context after its real RPC timeout.
                    time.sleep(1.2)
                if case == 'partial':
                    facade.resource.register_custom_action('NotAllowed', collector)
                if case == 'stop':
                    facade.post_stop()
                if case == 'normal':
                    errors = []

                    def concurrent():
                        try:
                            with registry.scope(unrelated, deadline=deadline):
                                pass
                        except RuntimeError as exc:
                            errors.append(str(exc))

                    contender = threading.Thread(target=concurrent)
                    contender.start()
                    contender.join(2)
                    assert not contender.is_alive() and errors
                    row['concurrent_rejected'] = errors
                try:
                    result = native.run(facade, 'Read')
                    row['native_ok'] = result['native_ok']
                    assert result['native_ok']
                except BaseException:
                    try:
                        native.run(facade, 'Read')
                    except RuntimeError:
                        row['retry_blocked'] = True
                    else:
                        raise AssertionError('Failed run was redispatched')
                    raise
                if case == 'unbind':
                    assert facade.resource.unregister_custom_action('CollectPresetPage')
                    assert facade.resource.unregister_custom_recognition('PresetPage')
                    native.run(facade, 'Read')
                else:
                    assert collector.complete and len(collector.pages) == 1
            row['ok'] = True
            return True
        except BaseException as exc:
            row['error'] = repr(exc)
            row['traceback'] = traceback.format_exc()
            row['ok'] = False
            if case == 'rpc_timeout':
                row['new_scope_blocked'] = row['root_task'] in registry._failed_tasks
            else:
                try:
                    with registry.scope(context, deadline=time.monotonic() + 1):
                        pass
                except RuntimeError:
                    row['new_scope_blocked'] = True
            return False
        finally:
            row['pages'] = len(collector.pages)
            row['collector_complete'] = collector.complete
            row['cleaned'] = registry._active is None
            row['pid'] = os.getpid()
            row['returned_at'] = time.monotonic()
            (OUT / 'server-results.json').write_text(json.dumps(records, indent=2), encoding='utf-8')


assert AgentServer.register_custom_action('SYNTHETIC_Entry', Entry())
assert AgentServer.start_up(sys.argv[1])
(OUT / 'ready').write_text(str(os.getpid()), encoding='utf-8')
try:
    until = time.monotonic() + 50
    while not (OUT / 'shutdown').exists():
        if time.monotonic() > until:
            raise TimeoutError('test supervisor did not request shutdown')
        time.sleep(0.02)
finally:
    AgentServer.shut_down()
    AgentServer.join()
