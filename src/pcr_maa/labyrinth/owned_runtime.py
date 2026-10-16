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

    def clear(self):
        self.username = self.password = ''


def parse_request(value):
    credentials = value.pop('credentials', None) if type(value) is dict else None
    try:
        if (type(value) is not dict or set(value) - {'version', 'settings', 'account_id', 'connection', 'keep_old_run'}
                or not {'version', 'settings', 'account_id', 'connection'} <= set(value)
                or type(value['version']) is not int or value['version'] != 1
                or type(value['account_id']) is not int or not 0 < value['account_id'] <= 2**63-1):
            raise ConfigurationError('invalid_configuration')
        keep_old_run = value.get('keep_old_run', False)
        if type(keep_old_run) is not bool:
            raise ConfigurationError('invalid_configuration')
        settings = Settings.parse(value['settings'])
        from ..device import validate_connection
        connection = validate_connection(value['connection'])
        if (type(credentials) is not dict or set(credentials) != {'username', 'password'}
                or type(credentials['username']) is not str or not 0 < len(credentials['username']) <= 512
                or type(credentials['password']) is not str or not 0 < len(credentials['password']) <= 4096
                or any(c in credentials['username'] for c in '\r\n\x00')
                or '\x00' in credentials['password']):
            raise ConfigurationError('invalid_configuration')
        return OwnedRequest(settings, value['account_id'], connection,
                            credentials['username'], credentials['password'], keep_old_run)
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
    """The owner's closed stdin and explicit Stop both revoke this invocation."""
    def __init__(self, stream):
        self.stream = stream
        self.event = threading.Event()
        self.callbacks = []
        self.lock = threading.RLock()
        self.finished = False
        self.transaction_thread = None
        self.transaction_depth = 0
        self.thread = threading.Thread(target=self._read, daemon=True,
                                       name='labyrinth-owner-stop')

    def start(self):
        self.thread.start()

    def __call__(self):
        with self.lock:
            return self.event.is_set() and self.transaction_thread != threading.get_ident()

    @contextlib.contextmanager
    def defer(self):
        # Finish the already-started input and persist its ACK before cancelling it.
        from .batch import BatchStopped
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
            callbacks = () if self.transaction_depth else tuple(self.callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def _read(self):
        try:
            # No secondary job/configuration channel: anything after the one
            # initial request is a Stop. EOF includes an exited GUI owner.
            self.stream.readline(257)
        except Exception:
            pass
        self.request()

    def finish(self):
        with self.lock:
            self.finished = True
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

    def _checkpoint(self):
        from .batch import BatchStopped
        if self.stop():
            raise BatchStopped('stopped')

    def _cancel(self):
        # Client.close cancels its sole active asyncio task through its loop.
        # The Runner itself is closed only by its owning main thread in finally.
        if self.client is not None:
            self.client.close()
        if self.tasker is not None:
            self.tasker.post_stop()

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
                     stopped=self.stop,progress=self._progress,resume=True,
                     keep_old_run=self.request.keep_old_run,
                     require_fresh_start=not self.request.keep_old_run).run()

    def _prepare_restart(self, journal):
        from .restart import prepare_restart
        prepare_restart(self.request, self.port, journal, self.stop, self._progress)

    def _finish_timings(self):
        try:
            if self.native_timings is not None:self.native_timings.finish_native_trace()
        except Exception:
            # Performance evidence must not replace a completed business result.
            self.timing_collection = 'incomplete'
        finally:
            self.timings.transition(None)

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
        journal = Journal(area/'ledger')
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
                app_version=read_installed_version(self.controller,stopped=self.stop)
                self._checkpoint()
                self.client=LabyrinthClient(self.request.username,self.request.password,
                    app_version=app_version,timings=timings,manual_captcha=HumanCaptcha(
                        lambda url,stage:self.emit(captcha_message(url,stage))))
                self.request.clear()
                self._checkpoint()
                binding=Binding(self.request.account_id,connection['serial'])
                self.port=ProtocolAcquisitionPort(self.client,None,binding,stopped=self.stop)
                self.port.timings=timings
                self.port.finish_timings=self._finish_timings
                self.port.game=MaaGamePort(self.tasker,self.port.stamp)
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
            self.timings.transition(None)
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
            if self.port is not None:
                self.port.close()
            elif self.client is not None:
                self.client.close()


def run_owned(request, stream, emit, *, runtime_factory=OwnedRuntime):
    stop=StopSignal(stream)
    runtime=runtime_factory(request,stop,emit)
    try:
        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):
            if sys.platform == 'win32':
                # NumPy's first import can deadlock while another thread reads redirected Windows stdin.
                try:import numpy
                except ImportError:return dict(outcome='runtime_components_missing',batch_complete=False)
            stop.start()
            return runtime.run()
    finally:
        stop.finish()
        request.clear()
