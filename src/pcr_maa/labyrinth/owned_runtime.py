"""One GUI-owned Labyrinth session, with memory-only login and explicit Stop.

No SDK state is shared with the read-only synchronization client or diagnostic
processes. Protocol and Maa phases are composed by ProtocolAcquisitionPort.
"""
import contextlib
import io
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4
from .config import Settings


class ConfigurationError(ValueError):
    pass


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ConfigurationError('duplicate_configuration_key')
        value[key] = item
    return value


@dataclass(repr=False)
class OwnedRequest:
    settings: Settings
    account_id: int
    connection: dict
    username: str = field(repr=False)
    password: str = field(repr=False)
    keep_old_run: bool = False
    server: str = 'cn-bilibili'

    def clear(self):
        self.username = self.password = ''


def parse_request(value):
    credentials = value.pop('credentials', None) if type(value) is dict else None
    try:
        if (type(value) is not dict or set(value) - {'version', 'settings', 'account_id', 'connection', 'keep_old_run', 'server'}
                or not {'version', 'settings', 'account_id', 'connection'} <= set(value)
                or type(value['version']) is not int or value['version'] != 1
                or type(value['account_id']) is not int or not 0 < value['account_id'] <= 2**63-1):
            raise ConfigurationError('invalid_configuration')
        keep_old_run = value.get('keep_old_run', False)
        if type(keep_old_run) is not bool:
            raise ConfigurationError('invalid_configuration')
        settings = Settings.parse(value['settings'])
        from .servers import get_server
        server = get_server(value.get('server','cn-bilibili')).server_id
        from ..device import validate_connection
        connection = validate_connection(value['connection'])
        if (type(credentials) is not dict or set(credentials) != {'username', 'password'}
                or type(credentials['username']) is not str or not 0 < len(credentials['username']) <= 512
                or type(credentials['password']) is not str or not 0 < len(credentials['password']) <= 4096
                or any(c in credentials['username'] for c in '\r\n\x00')
                or '\x00' in credentials['password']):
            raise ConfigurationError('invalid_configuration')
        return OwnedRequest(settings, value['account_id'], connection,
                            credentials['username'], credentials['password'], keep_old_run, server)
    except (KeyError, TypeError, ValueError):
        raise ConfigurationError('invalid_configuration') from None
    finally:
        if type(credentials) is dict:
            credentials.clear()


class Discard(io.TextIOBase):
    def write(self, value):
        return len(value)
    def flush(self):
        pass


class StopSignal:
    """Stop revokes the owner; pause waits only between settled operations."""
    def __init__(self, stream, emit=lambda value:None, *, response_interval_ms=300):
        self.stream = stream
        self.emit = emit
        self.response_interval_ms = response_interval_ms
        self.event = threading.Event()
        self.callbacks = []
        self.lock = threading.RLock()
        self.finished = False
        self.transaction_thread = None
        self.transaction_depth = 0
        self.condition = threading.Condition(self.lock)
        self.owner_thread = threading.get_ident()
        self.pause_requested = False
        self.pause_started = None
        self.paused_seconds = 0.0
        self.last_observation = -float('inf')
        self.diagnostics = None
        self.thread = threading.Thread(target=self._read, daemon=True,
                                       name='labyrinth-owner-stop')

    def start(self):
        self.thread.start()

    def __call__(self):
        with self.lock:
            return self.event.is_set() and self.transaction_thread != threading.get_ident()

    def now(self):
        with self.lock:
            return (self.pause_started if self.pause_started is not None else time.monotonic()) - self.paused_seconds

    def set_paused(self, value):
        with self.condition:
            if self.finished or self.event.is_set():return
            self.pause_requested = value
            self.condition.notify_all()

    def checkpoint(self):
        # Native callbacks may run on a different thread while their owner waits.
        if threading.get_ident() != self.owner_thread:return
        with self.condition:
            if self.transaction_depth or self.event.is_set() or not self.pause_requested:return
            # Finish the protocol request before pausing its wall-clock timeout.
            import asyncio
            try:asyncio.get_running_loop()
            except RuntimeError:pass
            else:return
            self.pause_started = time.monotonic()
            if self.diagnostics is not None:
                try:self.diagnostics.capture_state('paused')
                except Exception:pass
            self.emit(dict(kind='control',state='paused'))
            try:
                while self.pause_requested and not self.event.is_set():
                    self.condition.wait(.1)
            finally:
                self.paused_seconds += time.monotonic()-self.pause_started
                self.pause_started = None
            if not self.event.is_set():
                if self.diagnostics is not None:
                    try:self.diagnostics.capture_state('ready')
                    except Exception:pass
                self.emit(dict(kind='control',state='running'))

    def before_dispatch(self, tasker, entry):
        self.checkpoint()
        if self():
            from .batch import BatchStopped
            raise BatchStopped('stopped')
        node = tasker.resource.get_node_data(entry)
        if type(node) is not dict:return
        action=node.get('action')
        kind=action.get('type') if type(action) is dict else action
        if kind!='DoNothing' or node.get('next'):return
        # Pace observation starts; input nodes retain their calibrated timing.
        interval = self.response_interval_ms/1000
        target = self.last_observation+interval
        while self.now() < target:
            self.checkpoint()
            if self():
                from .batch import BatchStopped
                raise BatchStopped('stopped')
            from .. import native
            if self.now() >= native.RUN_DEADLINE.get():break
            self.event.wait(min(.05, max(0,target-self.now())))
        if node.get('rate_limit')!=self.response_interval_ms:
            if not tasker.resource.override_pipeline({entry:dict(rate_limit=self.response_interval_ms)}):
                raise RuntimeError('response_interval_configuration_failed')
        self.last_observation = self.now()

    @contextlib.contextmanager
    def defer(self):
        # Finish the already-started input and persist its ACK before cancelling it.
        from .batch import BatchStopped
        self.checkpoint()
        with self.lock:
            if self.event.is_set() and not self.transaction_depth:
                raise BatchStopped('stopped')
            self.transaction_thread = threading.get_ident()
            self.transaction_depth += 1
        try:
            yield
        finally:
            with self.lock:
                self.transaction_depth -= 1
                if not self.transaction_depth:
                    self.transaction_thread = None
                callbacks = tuple(self.callbacks) if not self.transaction_depth and self.event.is_set() else ()
            for callback in callbacks:
                try: callback()
                except Exception: pass

    def on_stop(self, callback):
        with self.lock:
            self.callbacks.append(callback)
            stopped = self.event.is_set() and not self.transaction_depth
        if stopped:
            callback()

    def request(self):
        with self.lock:
            if self.finished or self.event.is_set():
                return
            self.event.set()
            self.condition.notify_all()
            callbacks = () if self.transaction_depth else tuple(self.callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def _read(self):
        try:
            while True:
                line=self.stream.readline(257)
                if not line or len(line)>256:break
                value=json.loads(line,object_pairs_hook=unique_object)
                if type(value) is not dict or set(value)!={'kind'}:break
                if value['kind']=='pause':self.set_paused(True)
                elif value['kind']=='resume':self.set_paused(False)
                else:break
        except Exception:
            pass
        self.request()

    def finish(self):
        with self.condition:
            self.finished = True
            self.pause_requested = False
            self.condition.notify_all()
            self.callbacks.clear()


def public_result(value):
    outcome = value.get('outcome', 'adapter_failed') if type(value) is dict else 'adapter_failed'
    if type(outcome) is not str or re.fullmatch(r'[a-z][a-z_]{0,95}', outcome) is None:
        outcome = 'adapter_failed'
    result = dict(kind='result', outcome=outcome,
                  batch_complete=outcome == 'complete' and value.get('batch_complete') is True)
    completed = value.get('completed') if type(value) is dict else None
    if (type(completed) is dict and all(str(k) in {'1','2','3','4','5'}
            and type(v) is int and 0 <= v <= 999 for k,v in completed.items())):
        result['completed'] = completed
    return result


class OwnedRuntime:
    def __init__(self, request, stop, emit, *, root=None):
        from .timing import Timings
        self.request, self.stop, self.emit = request, stop, emit
        self.root = Path(root or Path(__file__).resolve().parents[3])
        self.client = self.port = self.tasker = self.controller = self.native_timings = None
        self.ocr_diagnostics = None
        self.ocr_success = False
        self.run_id = uuid4().hex
        self.timings = Timings()
        self.diagnostics = self.database = None
        self.database_token = None

    def _database_progress(self, value):
        status=value.get('status')
        if status=='updated':self._log('游戏数据库已更新，下次任务将使用新数据。')
        elif status=='failed':self._log('游戏数据库更新未完成，本次继续使用已验证的数据。')

    def _checkpoint(self):
        from .batch import BatchStopped
        checkpoint=getattr(self.stop,'checkpoint',None)
        if callable(checkpoint):checkpoint()
        if self.stop():
            raise BatchStopped('stopped')

    def _cancel(self):
        # Client.close cancels its sole active asyncio task through its loop.
        # The Runner itself is closed only by its owning main thread in finally.
        if self.client is not None:
            self.client.close()
        if self.tasker is not None:
            self.tasker.post_stop()

    def _log(self, text):
        """Live owner trace for the GUI log panel; never a result channel.

        Display only: a failed delivery is dropped here so a trace line can
        never replace a business result or a settled ledger entry.
        """
        if type(text) is not str:
            return
        text = ' '.join(text.split())[:512]
        if not text:
            return
        try:
            self.emit(dict(kind='log', text=text))
        except Exception:
            pass

    def _progress(self, value):
        stages = {'dependencies','resources','connecting','authenticating','ready',
            'opening','handoff','playing','cleared','moving','battle','shop','reward','team','recovering','retiring'}
        stage = value.get('stage')
        stage = {'map':'moving','move_confirmation':'moving',
            'normal_challenge':'team','ex_challenge':'team','boss_challenge':'team',
            'normal_team':'team','boss_team':'team','battle_win':'reward',
            'boss_summary':'reward','score_result':'reward','final_clear':'reward',
            'free_role_reward':'reward','clear_congratulations':'reward','clear_character_summary':'reward',
            'clear_reward_animation':'reward','clear_chest_animation':'reward','role_reward':'reward','relic_reward':'reward','link_reward':'reward',
            'joined':'reward','event':'playing'}.get(stage,stage)
        if stage in stages:
            if self.diagnostics is not None:
                try:
                    if stage in ('connecting','ready'):self.diagnostics.capture_state(stage)
                except Exception:pass
            self.timings.transition(stage)
            result = dict(kind='progress',stage=stage)
            completed=value.get('completed')
            if (type(completed) is dict and all(str(k) in {'1','2','3','4','5'}
                    and type(v) is int and 0<=v<=999 for k,v in completed.items())):
                result['completed']=completed
            self.emit(result)

    def _authenticate(self,binding):
        self.port._run(self.client.login,binding.account_id)

    def _run_batch(self, binding, journal):
        from .batch import Batch
        return Batch(self.request.settings,self.port,journal,binding,
                     stopped=self.stop,progress=self._progress,log=self._log,resume=True,
                      clock=self.stop.now if hasattr(self.stop,'now') else time.monotonic,
                     keep_old_run=self.request.keep_old_run,
                     require_fresh_start=not self.request.keep_old_run).run()

    def _prepare_restart(self, journal):
        from .restart import prepare_restart
        prepare_restart(self.request, self.port, journal, self.stop, self._progress)

    def _prepare_ocr(self):
        """Run the first inference before login or any business wait/input."""
        import cv2
        import numpy as np
        from maa.pipeline import JOCR, JRecognitionType
        from .. import native
        from .batch import BatchStopped
        self._checkpoint()
        deadline = min(native.active_now() + 30, native.RUN_DEADLINE.get())
        sample = np.full((64, 192, 3), 255, np.uint8)
        cv2.putText(sample, 'PCR', (15, 48), cv2.FONT_HERSHEY_SIMPLEX,
                    1.5, (0, 0, 0), 3, cv2.LINE_AA)
        if native.active_now() >= deadline:
            raise BatchStopped('runtime_resources_timeout')
        # Recognition-only API uses this synthetic image, never the controller.
        job = self.tasker.post_recognition(JRecognitionType.OCR,
            JOCR(expected=['^PCR$'], roi=(0, 0, 192, 64), threshold=.8), sample)
        while not job.done:
            self._checkpoint()
            if native.active_now() >= deadline:
                raise BatchStopped('runtime_resources_timeout')
            time.sleep(.05)
        self._checkpoint()
        if native.active_now() >= deadline:
            raise BatchStopped('runtime_resources_timeout')
        if not job.succeeded:
            raise BatchStopped('runtime_ocr_preparation_failed')

    def _finish_timings(self):
        try:
            if self.native_timings is not None:self.native_timings.finish_native_trace()
        except Exception:
            # Performance evidence must not replace a completed business result.
            self.timing_collection = 'incomplete'
        finally:
            try:self.timings.transition(None)
            except Exception:self.timing_collection = 'incomplete'

    def run(self):
        if self.stop():return dict(outcome='stopped',batch_complete=False)
        self._progress(dict(stage='dependencies'))
        from .batch import Batch, Binding, BatchStopped
        from ..preset_execution import Journal
        from .protocol import LabyrinthClient, ProtocolFailure
        from .acquisition import ProtocolAcquisitionPort
        from .captcha import HumanCaptcha
        from .owner_messages import captcha_message
        from .game_port import MaaGamePort
        from .timing import NativeTimings
        from .. import native
        from maa.controller import AdbController
        from maa.tasker import Tasker
        from maa.define import MaaAdbScreencapMethodEnum, MaaAdbInputMethodEnum
        area = self.root/'.runtime/labyrinth-work'
        from .servers import get_server
        server=get_server(self.request.server).server_id
        journal = Journal(area/'ledger' if server=='cn-bilibili' else area/'servers'/server/'ledger')
        # A second explicit owner cannot authenticate or use the controller
        # while this invocation is alive, even before the transaction lock.
        lease = Journal(area/'owner-lock')
        self.stop.on_stop(self._cancel)
        try:
            with lease.locked():
                self._checkpoint()
                with journal.locked():
                    from .restart import pending_intent
                    target_binding = Binding(self.request.account_id, self.request.connection['serial'])
                    pending_intent(journal, target_binding)
                with Journal(journal.directory/'restart').locked() as recovery:
                    retiring = pending_intent(recovery, target_binding)
                    if retiring is not None and retiring['operation'] != 'restart_retire':
                        raise BatchStopped('restart_records_invalid')
                # Import the complete adapter before allocating a device/client.
                # A partially assembled checkout reports a missing component.
                from .scene import MaaSceneRuntime
                from .autoplay import AutoplayRunner
                if not callable(getattr(MaaSceneRuntime,'observe',None)) or not callable(getattr(AutoplayRunner,'play',None)):
                    raise BatchStopped('runtime_components_missing')
                native.RUNTIME=area/'runs'/self.run_id/'native'
                from .diagnostics import RuntimeDiagnostics
                from .database import DatabaseCache, DATABASE_SNAPSHOT
                try:
                    self.diagnostics=RuntimeDiagnostics(area/'runs'/self.run_id/'diagnostics')
                    self.timings.attach_diagnostics(self.diagnostics)
                    self.diagnostics.start()
                    self.stop.diagnostics=self.diagnostics
                except Exception:
                    self.diagnostics=None
                self.database=DatabaseCache(self.root)
                self.database_token=DATABASE_SNAPSHOT.set(self.database.snapshot())
                if self.request.settings.strategy.auto_database_update:
                    self.database.start_update(self._database_progress)
                self._progress(dict(stage='resources'))
                try:resource=native.load_resource(self.root/'assets/resource')
                except TimeoutError:raise BatchStopped('runtime_resources_timeout') from None
                self._checkpoint()
                connection=self.request.connection
                self._progress(dict(stage='connecting'))
                from ..capture_performance import begin_capture_report, finish_capture_report
                capture_config={'command': {
                    'ScreencapEncode': ['{ADB}', '-s', '{ADB_SERIAL}', 'exec-out', 'screencap -p'],
                    'ScreencapRawWithGzip': ['{ADB}', '-s', '{ADB_SERIAL}', 'exec-out', 'screencap | gzip -1']}}
                capture_report=begin_capture_report(self.root,self.run_id,native.RUNTIME/'maa/maafw.log',
                    options=capture_config,serial=connection['serial'],
                    display_binding=dict(basis='default_screen_only',logical=0,physical=None))
                self.controller=AdbController(connection['adb_path'],connection['serial'],
                    MaaAdbScreencapMethodEnum.Encode | MaaAdbScreencapMethodEnum.RawWithGzip,
                    MaaAdbInputMethodEnum.AdbShell,config=capture_config)
                if not self.controller.set_screenshot_target_short_side(720):
                    raise BatchStopped('controller_configuration_failed')
                try:connected=native.wait_job(self.controller.post_connection(),seconds=30).succeeded
                except TimeoutError:raise BatchStopped('controller_connection_timeout') from None
                finally:finish_capture_report(capture_report)
                if not connected:
                    raise BatchStopped('controller_connection_failed')
                self._checkpoint()
                self.tasker=Tasker()
                if not self.tasker.bind(resource,self.controller) or not self.tasker.inited:
                    raise BatchStopped('controller_connection_failed')
                self.tasker._labyrinth_diagnostics=self.diagnostics
                self._progress(dict(stage='resources'))
                self._prepare_ocr()
                timings=self.timings
                self.native_timings=NativeTimings(self.tasker,self.controller,timings,
                    trace_path=native.RUNTIME/'maa/maafw.log')
                from ..ocr_diagnostics import FailureDiagnostics, fingerprints
                try:
                    self.ocr_diagnostics=FailureDiagnostics(native.ROOT/'.runtime/ocr-failures',
                        self.run_id, 'labyrinth', fingerprints(native.RESOURCE)).attach(self.tasker)
                except Exception:
                    pass
                self._progress(dict(stage='authenticating'))
                from .app_version import read_installed_version
                app_version=read_installed_version(self.controller,stopped=self.stop,server=server)
                self._checkpoint()
                self.client=LabyrinthClient(self.request.username,self.request.password,
                    cache_root=self.root,app_version=app_version,server=server,timings=timings,manual_captcha=HumanCaptcha(
                        lambda url,stage:self.emit(captcha_message(url,stage))))
                self.request.clear()
                self._checkpoint()
                binding=Binding(self.request.account_id,connection['serial'])
                self.port=ProtocolAcquisitionPort(self.client,None,binding,stopped=self.stop)
                self.port.timings=timings
                self.port.finish_timings=self._finish_timings
                self.port.game=MaaGamePort(self.tasker,self.port.stamp,server=server,stopped=self.stop)
                self.client.before_game_login=self.port.game.prepare_protocol_login
                self._authenticate(binding)
                self._checkpoint()
                self._progress(dict(stage='recovering'))
                self._prepare_restart(journal)
                self._checkpoint()
                self._progress(dict(stage='ready'))
                result=self._run_batch(binding,journal)
                self.ocr_success=result.get('batch_complete') is True
                self._finish_timings()
                return result
        except BatchStopped as error:
            return dict(outcome=str(error),batch_complete=False)
        except ProtocolFailure as error:
            return dict(outcome=str(error),batch_complete=False)
        except (ImportError, ModuleNotFoundError):
            return dict(outcome='runtime_components_missing',batch_complete=False)
        except Exception:
            return dict(outcome='adapter_failed',batch_complete=False)
        finally:
            try:self.timings.transition(None)
            except Exception:self.timing_collection = 'incomplete'
            if self.tasker is not None:
                from .. import player_log
                player_log.capture(self.tasker, 'complete' if self.ocr_success else 'stopped')
            self.request.clear()
            if self.ocr_diagnostics is not None:
                try:self.ocr_diagnostics.finish(not self.ocr_success)
                except Exception:pass
            if self.tasker is not None:
                try:
                    native.wait_job(self.tasker.post_stop(),seconds=20)
                except Exception:
                    pass
            if self.native_timings is not None:
                try:self.native_timings.close()
                except Exception:self.timing_collection = 'incomplete'
            try:
                if self.port is not None:self.port.close()
                elif self.client is not None:self.client.close()
            finally:
                if self.diagnostics is not None:
                    try:
                        self.diagnostics.capture_state('stopped')
                        self.diagnostics.close()
                    except Exception:pass
                try:
                    if self.database is not None:self.database.close()
                finally:
                    if self.database_token is not None:
                        from .database import DATABASE_SNAPSHOT
                        DATABASE_SNAPSHOT.reset(self.database_token)
                        self.database_token=None


def run_owned(request, stream, emit, *, runtime_factory=OwnedRuntime):
    strategy=getattr(getattr(request,'settings',None),'strategy',None)
    stop=StopSignal(stream,emit,response_interval_ms=getattr(strategy,'response_interval_ms',300))
    runtime=runtime_factory(request,stop,emit)
    try:
        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):
            if sys.platform == 'win32':
                # NumPy's first import can deadlock while another thread reads redirected Windows stdin.
                try:import numpy
                except ImportError:return dict(outcome='runtime_components_missing',batch_complete=False)
            from .. import native
            token=native.RUN_CONTROL.set(stop)
            try:
                stop.start()
                return runtime.run()
            finally:native.RUN_CONTROL.reset(token)
    finally:
        stop.finish()
        request.clear()
