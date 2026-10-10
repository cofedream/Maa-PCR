"""Offline callback routing contracts and independent official TCP Agent evidence."""
import time
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def registry(monkeypatch):
    from pcr_maa.agent_callbacks import AgentCallbacks
    # Do NOT import maa.agent in the host: its __init__ changes SDK library mode.
    server = SimpleNamespace(register_custom_recognition=lambda *a: True,
                             register_custom_action=lambda *a: True)
    monkeypatch.setitem(sys.modules, 'maa.agent.agent_server', SimpleNamespace(AgentServer=server))
    monkeypatch.setattr(AgentCallbacks, '_installed', None)
    registry = AgentCallbacks()
    registry.install()
    return registry


def test_explicit_registry_preserves_instance_and_cleans_partial_registration(registry):
    from pcr_maa.preset_scan import Collector
    context = SimpleNamespace(tasker=SimpleNamespace(inited=True, stopping=False),
                              get_task_job=lambda: SimpleNamespace(job_id=1))
    collector = Collector()
    with pytest.raises(RuntimeError, match='duplicate'):
        with registry.scope(context, deadline=time.monotonic() + 5) as facade:
            assert facade.resource.register_custom_action('CollectPresetPage', collector)
            assert registry._active.bindings['CollectPresetPage'] is collector
            facade.resource.register_custom_action('CollectPresetPage', Collector())
    assert registry._active is None
    assert facade._callback_scope.bindings == {}


class FakeContext:
    """Fault-injection context; never counted as native/TCP evidence."""
    def __init__(self, task_id=1):
        self.tasker = SimpleNamespace(inited=True, stopping=False)
        self.task_id = task_id
        self.nodes = {}

    def get_task_job(self):
        return SimpleNamespace(job_id=self.task_id)

    def override_pipeline(self, nodes):
        self.nodes.update(nodes)
        return True

    def get_node_data(self, name):
        return self.nodes.get(name)


def action_arg(name='CollectPresetPage', task_id=1):
    return SimpleNamespace(custom_action_name=name, task_detail=SimpleNamespace(task_id=task_id))


def test_inflight_callback_quarantines_scope_until_actual_return(registry):
    import threading
    from maa.custom_action import CustomAction
    entered, release = threading.Event(), threading.Event()

    class Slow(CustomAction):
        def run(self, context, argv):
            entered.set()
            assert release.wait(3)
            return True

    context = FakeContext()
    worker = None
    try:
        with pytest.raises(RuntimeError, match='in flight'):
            with registry.scope(context, deadline=time.monotonic() + 5) as facade:
                facade.resource.register_custom_action('CollectPresetPage', Slow())
                with facade._callback_scope.dispatch():
                    worker = threading.Thread(target=lambda: registry.actions['CollectPresetPage'].run(context, action_arg()))
                    worker.start()
                    assert entered.wait(2)
                    # Fault injection: SDK returned before its callback returned.
        assert facade._callback_scope.bindings == {}
        with pytest.raises(RuntimeError, match='Concurrent'):
            with registry.scope(FakeContext(2), deadline=time.monotonic() + 5):
                pass
    finally:
        release.set()
        if worker:
            worker.join(3)
            assert not worker.is_alive()
    assert registry._active is None


@pytest.mark.parametrize('method,name', [
    ('register_custom_action', 'Unknown'),
    ('register_custom_action', 'PresetPage'),
    ('register_custom_recognition', 'CollectPresetPage'),
    ('register_custom_recognition', 'HomeNoKnownLargePanel'),
])
def test_unknown_and_mismatched_registration_fail_closed(registry, method, name):
    from pcr_maa.preset_scan import Collector
    with pytest.raises(RuntimeError, match='Unknown or mismatched'):
        with registry.scope(FakeContext(), deadline=time.monotonic() + 5) as facade:
            getattr(facade.resource, method)(name, Collector())
    assert registry._active is None and not facade._callback_scope.bindings


def test_wrong_association_stale_scope_and_cross_thread_are_rejected(registry):
    import threading
    from maa.custom_action import CustomAction
    calls = []

    class Action(CustomAction):
        def run(self, context, argv):
            calls.append(1)
            return True

    context = FakeContext()
    route = registry.actions['CollectPresetPage']
    with registry.scope(context, deadline=time.monotonic() + 5) as old:
        old.resource.register_custom_action('CollectPresetPage', Action())
        with old._callback_scope.dispatch():
            assert route.run(FakeContext(), action_arg()) is False  # missing marker
            assert route.run(context, action_arg(task_id=2)) is False
            assert route.run(context, action_arg(name='CollectEvent')) is False
            foreign = FakeContext(2)
            foreign.nodes = context.nodes.copy()
            assert route.run(foreign, action_arg(task_id=2)) is False  # even with copied marker
            assert route.run(context, action_arg()) is True
            errors = []

            def change_from_other_thread():
                try:
                    old.resource.unregister_custom_action('CollectPresetPage')
                except RuntimeError as exc:
                    errors.append(str(exc))

            thread = threading.Thread(target=change_from_other_thread)
            thread.start()
            thread.join(2)
            assert errors and 'Cross-thread' in errors[0]
        assert route.run(context, action_arg()) is False  # dispatch over, scope still open
        assert old.resource.unregister_custom_action('CollectPresetPage')
    with registry.scope(FakeContext(2), deadline=time.monotonic() + 5):
        with pytest.raises(RuntimeError, match='closed'):
            old.resource.register_custom_action('CollectPresetPage', Action())
        assert route.run(context, action_arg()) is False
    assert calls == [1]


def test_scope_cleans_keyboard_interrupt_and_blocks_same_root(registry):
    from pcr_maa.preset_scan import Collector
    context = FakeContext()
    with pytest.raises(KeyboardInterrupt):
        with registry.scope(context, deadline=time.monotonic() + 5) as facade:
            facade.resource.register_custom_action('CollectPresetPage', Collector())
            raise KeyboardInterrupt()
    assert not facade._callback_scope.bindings and registry._active is None
    with pytest.raises(RuntimeError, match='no retry'):
        with registry.scope(context, deadline=time.monotonic() + 5):
            pass


@pytest.mark.parametrize('failure', ['false', 'none', 'result', 'exception'])
def test_action_failure_is_caught_and_latched(registry, failure):
    from maa.custom_action import CustomAction
    calls = []

    class Broken(CustomAction):
        def run(self, context, argv):
            calls.append(1)
            if failure == 'exception':
                raise ValueError('injected action exception')
            return {'false': False, 'none': None, 'result': self.RunResult(False)}[failure]

    context = FakeContext()
    with pytest.raises(RuntimeError, match='blocked'):
        with registry.scope(context, deadline=time.monotonic() + 5) as facade:
            facade.resource.register_custom_action('CollectPresetPage', Broken())
            with facade._callback_scope.dispatch():
                route = registry.actions['CollectPresetPage']
                assert route.run(context, action_arg()) is False
                assert route.run(context, action_arg()) is False
    assert calls == [1] and registry._active is None


def test_unregister_mismatch_releases_remaining_binding_on_exit(registry):
    from pcr_maa.preset_scan import Collector
    with pytest.raises(RuntimeError, match='mismatched'):
        with registry.scope(FakeContext(), deadline=time.monotonic() + 5) as facade:
            facade.resource.register_custom_action('CollectPresetPage', Collector())
            facade.resource.unregister_custom_recognition('CollectPresetPage')
    assert not facade._callback_scope.bindings


def test_partial_static_install_is_permanently_closed(monkeypatch):
    from pcr_maa.agent_callbacks import AgentCallbacks
    registered = []

    def register(name, callback):
        registered.append(name)
        return len(registered) < 3

    server = SimpleNamespace(register_custom_recognition=register, register_custom_action=register)
    monkeypatch.setitem(sys.modules, 'maa.agent.agent_server', SimpleNamespace(AgentServer=server))
    monkeypatch.setattr(AgentCallbacks, '_installed', None)
    registry = AgentCallbacks()
    with pytest.raises(RuntimeError, match='pre-register'):
        registry.install()
    assert len(registered) == 3
    with pytest.raises(RuntimeError, match='already installed'):
        AgentCallbacks().install()
    with pytest.raises(RuntimeError, match='not installed'):
        with registry.scope(FakeContext(), deadline=time.monotonic() + 5):
            pass


def _business_registrations(paths):
    import ast
    found = {'recognition': set(), 'action': set()}
    def string_constants(tree):
        return {target.id: node.value.value for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str) for target in node.targets if isinstance(target, ast.Name)}
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        constants = string_constants(tree)
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                imported = path.parent.joinpath(*node.module.split('.')).with_suffix('.py')
                if imported.is_file():
                    values = string_constants(ast.parse(imported.read_text(encoding='utf-8-sig')))
                    constants.update({alias.asname or alias.name: values[alias.name]
                        for alias in node.names if alias.name in values})
        def name(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            return constants.get(node.id) if isinstance(node, ast.Name) else None
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == '_reader' and len(node.args) >= 2 and name(node.args[1])):
                found['recognition'].add(name(node.args[1]))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                # Abyss readers register their fixed names through _reader.
                # Include the literal call sites rather than dropping them.
                if node.func.attr == '_reader' and node.args and name(node.args[0]):
                    found['recognition'].add(name(node.args[0]))
                for kind in found:
                    if node.func.attr in ('register_custom_' + kind, 'unregister_custom_' + kind):
                        if node.args and name(node.args[0]):
                            found[kind].add(name(node.args[0]))
    return found


LEGACY_LABYRINTH_ROUTES = frozenset({'LabyrinthCharacters', 'LabyrinthScene', 'LabyrinthShop',
    'LabyrinthEvent', 'LabyrinthAutoplayTarget', 'LabyRelicSelectedIconCapture'})


def test_static_whitelist_matches_all_business_registrations():
    from pathlib import Path
    from pcr_maa.agent_callbacks import RECOGNITIONS, ACTIONS, HOME
    from pcr_maa.agent_entry import ACTION as ENTRY_ACTION
    # The GUI launches labyrinth's own process, Resource and controller rather
    # than AlphaEntry's daily callback scope. Do not grant its routes to daily.
    root = Path(__file__).resolve().parents[1] / 'src/pcr_maa'
    found = _business_registrations(root.glob('*.py'))
    assert LEGACY_LABYRINTH_ROUTES <= RECOGNITIONS
    assert found == {'recognition': (RECOGNITIONS - LEGACY_LABYRINTH_ROUTES) | {HOME},
                     'action': ACTIONS | {ENTRY_ACTION}}


def test_labyrinth_owner_registrations_do_not_expand_daily_authority():
    from pathlib import Path
    from pcr_maa.agent_callbacks import RECOGNITIONS, ACTIONS
    root = Path(__file__).resolve().parents[1] / 'src/pcr_maa/labyrinth'
    found = _business_registrations(root.rglob('*.py'))
    assert found['recognition'] & RECOGNITIONS == LEGACY_LABYRINTH_ROUTES
    assert not found['action'] & ACTIONS
    owned_only = {'LabyBrightEdgesObserved', 'LabyPartialMapObserved', 'LabyRouteTargetObserved',
        'LabyShopSelectedIconCapture', 'LabyStartGateObserved', 'LabyWorldMarkerObserved', 'LabyrinthEncounter'}
    assert owned_only <= found['recognition']
    assert not owned_only & RECOGNITIONS


def test_static_registration_audit_resolves_module_constants(tmp_path):
    source = tmp_path / 'constants.py'
    (tmp_path / 'shared.py').write_text("IMPORTED = 'ImportedRecognition'\n", encoding='utf-8')
    source.write_text("CALLBACK = 'NamedRecognition'\nACTION = 'NamedAction'\n"
        "from .shared import IMPORTED as ALIAS\n"
        "resource.register_custom_recognition(CALLBACK, reader)\n"
        "resource.register_custom_recognition(ALIAS, reader)\n"
        "resource.unregister_custom_recognition(CALLBACK)\n"
        "resource.register_custom_action(ACTION, collector)\n"
        "_reader(resource, 'LiteralRecognition', callback)\n", encoding='utf-8')
    assert _business_registrations([source]) == dict(
        recognition={'NamedRecognition', 'LiteralRecognition', 'ImportedRecognition'}, action={'NamedAction'})


def test_official_tcp_callbacks(tmp_path, monkeypatch):
    """Real host + separate AgentServer, official TCP, synthetic data only."""
    import json
    import importlib.util
    from pathlib import Path
    import numpy as np
    from maa.agent_client import AgentClient
    from maa.controller import CustomController, AdbController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from maa.define import LoggingLevelEnum
    from pcr_maa.agent_callbacks import ACTIONS, RECOGNITIONS, HOME, _MARKER

    def forbidden(*args, **kwargs):
        raise AssertionError('No device or host callback registration allowed')

    monkeypatch.setattr(AdbController, '__init__', forbidden)
    monkeypatch.setattr(Toolkit, 'find_adb_devices', forbidden)
    for method in ('register_custom_action', 'register_custom_recognition',
                   'unregister_custom_action', 'unregister_custom_recognition'):
        monkeypatch.setattr(Resource, method, forbidden)

    class Synthetic(CustomController):
        def connect(self):
            return True

        def request_uuid(self):
            return 'SYNTHETIC-CALLBACK-TEST'

        def get_features(self):
            return 0

        def screencap(self):
            return np.zeros((96, 128, 3), dtype=np.uint8)

        def denied(self, *a):
            raise AssertionError('No synthetic inputs expected')

        click = swipe = touch_down = touch_move = touch_up = denied
        start_app = stop_app = input_text = click_key = key_down = key_up = denied

    def until(predicate, seconds=8):
        end = time.monotonic() + seconds
        while not predicate():
            assert time.monotonic() < end, 'bounded test deadline'
            time.sleep(0.02)

    Tasker.set_log_dir(tmp_path / 'host-maa')
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    bundle = tmp_path / 'bundle'
    (bundle / 'pipeline').mkdir(parents=True)
    pipeline = {
        'Entry': dict(action='Custom', custom_action='SYNTHETIC_Entry', post_delay=0),
        'Read': dict(recognition='Custom', custom_recognition='PresetPage',
                     action='Custom', custom_action='CollectPresetPage',
                     post_delay=0, timeout=100),
        'Home': dict(recognition='Custom', custom_recognition=HOME, post_delay=0),
    }
    (bundle / 'pipeline/test.json').write_text(json.dumps(pipeline), encoding='utf-8')
    resource, controller, tasker = Resource(), Synthetic(), Tasker()
    assert resource.post_bundle(bundle).wait().succeeded
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    before = resource.get_node_data('Read')
    client = AgentClient.create_tcp(0)
    assert client.set_timeout(5000) and client.bind(resource)
    assert client.identifier.isdigit()
    helper = Path(__file__).resolve().parent / 'helpers/tcp_server.py'
    spec = importlib.util.spec_from_file_location('callback_server_process', helper.with_name('server_process.py'))
    process_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(process_module)
    server_job = process_module.ServerJob(tmp_path, 'server')
    server = None
    host_results = []
    with (tmp_path / 'server.log').open('w', encoding='utf-8') as log:
        try:
            server = server_job.start(client.identifier)
            until(lambda: (tmp_path / 'ready').exists() or server_job.poll(server) is not None)
            assert server_job.poll(server) is None, (tmp_path / 'server.stderr.txt').read_text(encoding='utf-8')
            assert int((tmp_path / 'ready').read_text(encoding='utf-8')) in server_job.snapshot()
            assert client.connect()
            assert set(client.custom_recognition_list) == RECOGNITIONS | {HOME}
            assert set(client.custom_action_list) == ACTIONS | {'SYNTHETIC_Entry'}
            for case in ('normal', 'normal', 'unbind', 'exception', 'partial', 'expired', 'late', 'stop'):
                job = tasker.post_task('Entry', {'Entry': {'custom_action_param': {'case': case}}})
                until(lambda: job.done)
                until(lambda: not tasker.running)
                until(lambda: (tmp_path / 'server-results.json').exists() and len(
                    json.loads((tmp_path / 'server-results.json').read_text(encoding='utf-8'))) == len(host_results) + 1)
                host_results.append(dict(case=case, succeeded=job.succeeded, task_id=job.job_id))
            rows = json.loads((tmp_path / 'server-results.json').read_text(encoding='utf-8'))
            assert len(rows) == 8
            for i, (row, result) in enumerate(zip(rows, host_results)):
                assert row['cleaned'] and row['borrowed'] and row['root_task'] == result['task_id'], row
                assert row['ok'] is (i < 2), row
                # SDK StopTask/stop can report native success. The scope still
                # rejects the business result; do not equate these statuses.
                if row['case'] != 'stop':
                    assert result['succeeded'] is (i < 2), row
                if i < 2:
                    assert row['calls'] == row['pages'] == 1 and row['collector_complete'], row
                    assert row['unrelated_missed'] and row['concurrent_rejected'], row
                    assert all(t != row['entry_thread'] for t in row['recog_threads'] + row['action_threads']), row
                else:
                    assert row['new_scope_blocked'], row
            assert rows[0]['home_ids'] == rows[1]['home_ids'] and len(rows[0]['home_ids']) == 1
            assert rows[2]['calls'] == 1 and rows[2]['pages'] == 1  # unbound second read rejected
            assert rows[3]['calls'] == 1 and rows[3]['pages'] == 0 and rows[3]['retry_blocked']
            assert all(rows[i]['calls'] == 0 for i in (4, 5, 7))
            assert rows[6]['calls'] == 1 and rows[6]['pages'] == 0 and rows[6]['retry_blocked']
            assert resource.get_node_data('Read') == before
            assert resource.get_node_data(_MARKER) is None
            # A real RPC timeout can return before the synchronous callback.
            # Observe its late return and cleanup; NEVER repost that task.
            assert client.set_timeout(300)
            job = tasker.post_task('Entry', {'Entry': {'custom_action_param': {'case': 'rpc_timeout'}}})
            until(lambda: job.done)
            timeout_done_at = time.monotonic()
            assert not job.succeeded
            until(lambda: len(json.loads((tmp_path / 'server-results.json').read_text(encoding='utf-8'))) == 9)
            late = json.loads((tmp_path / 'server-results.json').read_text(encoding='utf-8'))[-1]
            assert late['cleaned'] and late['calls'] == 0 and late['pages'] == 0, late
            assert late['retry_blocked'] and late['new_scope_blocked'] and not late['ok'], late
            assert timeout_done_at < late['returned_at']
            host_results.append(dict(case='rpc_timeout', succeeded=job.succeeded,
                                     task_id=job.job_id, done_at=timeout_done_at))
            (tmp_path / 'host-results.json').write_text(json.dumps(host_results, indent=2), encoding='utf-8')
        finally:
            client.disconnect()
            (tmp_path / 'shutdown').write_text('shutdown', encoding='utf-8')
            cleanup = server_job.cleanup(grace=5)
            (tmp_path / 'process.json').write_text(json.dumps(cleanup, indent=2), encoding='utf-8')
    assert cleanup['all_reaped'] and cleanup['query_job_empty'] and not cleanup['forced']
    assert all(p['returncode'] == 0 for p in cleanup['processes'])


@pytest.mark.parametrize('raise_inside',[False,True])
def test_abyss_fixed_reader_routes_register_and_unregister_on_every_exit(raise_inside):
    from pcr_maa.abyss_native import _reader
    from pcr_maa.agent_callbacks import RECOGNITIONS
    class Resource:
        def __init__(self):self.calls=[]
        def register_custom_recognition(self,name,callback):self.calls.append(('register',name,callback));return True
        def unregister_custom_recognition(self,name):self.calls.append(('unregister',name));return True
    names=('AbyssHomePage','AbyssOpenPage','AbyssQuotePage','AbyssConfirmedQuote','AbyssResultPage','AbyssTicketPage')
    for name in names:
        assert name in RECOGNITIONS
        resource=Resource();callback=object()
        try:
            with _reader(resource,name,callback):
                if raise_inside:raise RuntimeError('synthetic reader body failure')
        except RuntimeError:
            assert raise_inside
        assert resource.calls==[('register',name,callback),('unregister',name)]
