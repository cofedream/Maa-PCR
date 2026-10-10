"""Windows Job owner for the fixed synthetic TCP Agent process.

Create suspended, assign KILL_ON_JOB_CLOSE, then resume. Cleanup only targets
this Job; retained handles distinguish process exit from PID reuse.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import time
import _winapi

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
K = C.WinDLL('kernel32', use_last_error=True)


def api(name, args, result=W.BOOL):
    fn = getattr(K, name)
    fn.argtypes, fn.restype = args, result
    return fn


create_job = api('CreateJobObjectW', [C.c_void_p, W.LPCWSTR], W.HANDLE)
set_job = api('SetInformationJobObject', [W.HANDLE, C.c_int, C.c_void_p, W.DWORD])
query_job = api('QueryInformationJobObject', [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.c_void_p])
assign_job = api('AssignProcessToJobObject', [W.HANDLE, W.HANDLE])
in_job = api('IsProcessInJob', [W.HANDLE, W.HANDLE, C.POINTER(W.BOOL)])
resume = api('ResumeThread', [W.HANDLE], W.DWORD)
terminate_job = api('TerminateJobObject', [W.HANDLE, W.UINT])
terminate_process = api('TerminateProcess', [W.HANDLE, W.UINT])
wait_handle = api('WaitForSingleObject', [W.HANDLE, W.DWORD], W.DWORD)
exit_code = api('GetExitCodeProcess', [W.HANDLE, C.POINTER(W.DWORD)])
open_process = api('OpenProcess', [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
close = api('CloseHandle', [W.HANDLE])
set_handle = api('SetHandleInformation', [W.HANDLE, W.DWORD, W.DWORD])


class Limits(C.Structure):
    _fields_ = [('process_time', C.c_int64), ('job_time', C.c_int64),
                ('flags', W.DWORD), ('min_ws', C.c_size_t), ('max_ws', C.c_size_t),
                ('active_limit', W.DWORD), ('affinity', C.c_size_t),
                ('priority', W.DWORD), ('scheduling', W.DWORD)]


class Extended(C.Structure):
    _fields_ = [('basic', Limits), ('io', C.c_uint64 * 6),
                ('process_memory', C.c_size_t), ('job_memory', C.c_size_t),
                ('peak_process', C.c_size_t), ('peak_job', C.c_size_t)]


class Pids(C.Structure):
    _fields_ = [('assigned', W.DWORD), ('count', W.DWORD), ('pids', C.c_size_t * 128)]


def checked(value):
    if not value:
        raise C.WinError(C.get_last_error())
    return value


def child_env(run):
    tmp = Path(run) / 'tmp'
    tmp.mkdir(exist_ok=True)
    env = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'COMSPEC', 'SYSTEMDRIVE') if k in os.environ}
    env.update(TEMP=str(tmp), TMP=str(tmp), TMPDIR=str(tmp),
               PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8',
               PYTHONPATH=str(ROOT / 'src'))
    return env


class ServerJob:
    def __init__(self, run, label):
        self.run, self.label = Path(run), label
        self.handle = checked(create_job(None, None))
        self.handles, self.roots, self.forced = {}, [], False
        try:
            checked(set_handle(self.handle, 1, 0))
            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            checked(set_job(self.handle, 9, C.byref(limits), C.sizeof(limits)))
        except BaseException:
            close(self.handle)
            self.handle = None
            raise
        self.log('job_created', kill_on_close=True, inheritable=False, breakaway=False)

    def log(self, event, **data):
        with (self.run / f'{self.label}.job.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(event=event, owner=os.getpid(), utc_ns=time.time_ns(), **data)) + '\n')

    def start(self, identifier):
        assert str(identifier).isdigit() and self.run.is_dir()
        python = sys.executable
        argv = [python, '-B', str(BASE / 'tcp_server.py'), str(identifier), str(self.run)]
        streams = [open(os.devnull, 'rb'), (self.run / 'server.stdout.txt').open('xb'),
                   (self.run / 'server.stderr.txt').open('xb')]
        handles = [msvcrt.get_osfhandle(s.fileno()) for s in streams]
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
        startup.lpAttributeList = {'handle_list': handles}
        hp = ht = None
        try:
            for h in handles:
                os.set_handle_inheritable(h, True)
            hp, ht, pid, _ = _winapi.CreateProcess(python, subprocess.list2cmdline(argv),
                None, None, True, 0x4 | subprocess.CREATE_NO_WINDOW,
                child_env(self.run), str(self.run), startup)
            self.log('created_suspended', pid=pid, argv=argv)
            checked(assign_job(self.handle, hp))
            self.handles[pid] = hp
            self.roots.append(pid)
            self.log('assigned_before_resume', pid=pid)
            if resume(ht) == 0xFFFFFFFF:
                raise C.WinError(C.get_last_error())
            return pid
        except BaseException:
            if hp and hp not in self.handles.values():
                checked(terminate_process(hp, 71))
                assert wait_handle(hp, 3000) == 0
                close(hp)
            raise
        finally:
            if ht:
                close(ht)
            for h in handles:
                os.set_handle_inheritable(h, False)
            for stream in streams:
                stream.close()

    def snapshot(self):
        pids = Pids()
        checked(query_job(self.handle, 3, C.byref(pids), C.sizeof(pids), None))
        active = list(pids.pids[:pids.count])
        for pid in active:
            if pid not in self.handles:
                handle = open_process(0x100000 | 0x1000, False, pid)
                if not handle:
                    error = C.get_last_error()
                    if error == 87:
                        continue
                    raise C.WinError(error)
                member = W.BOOL()
                checked(in_job(handle, self.handle, C.byref(member)))
                if not member.value:
                    close(handle)
                    raise RuntimeError('PID changed ownership during observation')
                self.handles[pid] = handle
        return active

    def poll(self, pid):
        handle = self.handles[pid]
        if wait_handle(handle, 0) == 258:
            return None
        code = W.DWORD()
        checked(exit_code(handle, C.byref(code)))
        return code.value

    def wait_for(self, predicate, seconds, label):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.snapshot()
            if predicate():
                return
            time.sleep(0.02)
        raise TimeoutError(label)

    def cleanup(self, grace=3):
        try:
            self.wait_for(lambda: not self.snapshot(), grace, 'graceful exit deadline')
        except TimeoutError:
            self.log('owned_job_timeout', members=self.snapshot())
            self.forced = True
            checked(terminate_job(self.handle, 72))
            self.wait_for(lambda: not self.snapshot(), 5, 'Job termination deadline')
        self.wait_for(lambda: all(self.poll(pid) is not None for pid in self.handles),
                      5, 'all observed process handles signalled')
        records = [{'pid': pid, 'returncode': self.poll(pid)} for pid in self.handles]
        remaining = self.snapshot()
        assert not remaining and all(r['returncode'] is not None for r in records)
        report = dict(forced=self.forced, active_pids=remaining, processes=records,
                      all_reaped=True, query_job_empty=True)
        self.log('cleanup_readback', **report)
        checked(close(self.handle))
        self.handle = None
        for handle in self.handles.values():
            close(handle)
        self.handles.clear()
        return report

    def __del__(self):
        if getattr(self, 'handle', None):
            close(self.handle)
