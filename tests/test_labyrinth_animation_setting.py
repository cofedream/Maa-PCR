"""Preflight for the local Labyrinth character-join animation setting."""
from pathlib import Path
from subprocess import TimeoutExpired
from types import SimpleNamespace as NS

from pcr_maa.labyrinth import animation_setting as setting

PKG = 'com.bilibili.priconne'
PREF = setting.pref_path(PKG)
OTHER = ('KXlybzR%2FK2x4aSRjMWx%2FeDljK3R%2FdS1pJHZ5eydiLHd%2BB1IEVg0DD1UPUgk'
         'HDw%3D%3D')
XML = f'''<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="{setting.KEY}">{setting.ON}</string>
    <string name="{OTHER}">{setting.ON}</string>
</map>
'''


class Adb:
    def __init__(self, *, package=PKG, root=False, xml=XML, root_works=True, stop_works=True,
                 read_requires_root=False, force_stop_fails=False, pidof_timeout=False,
                 pidof_error=False, write_interrupt=False, copy_fails=False,
                 rename_fails=False):
        self.package = package
        self.pref = setting.pref_path(package)
        self.root = root
        self.xml = xml
        self.root_works = root_works
        self.stop_works = stop_works
        self.read_requires_root = read_requires_root
        self.force_stop_fails = force_stop_fails
        self.pidof_timeout = pidof_timeout
        self.pidof_error = pidof_error
        self.write_interrupt = write_interrupt
        self.copy_fails = copy_fails
        self.rename_fails = rename_fails
        self.calls = []
        self.files = {}

    def __call__(self, args, *, timeout=10, input=None):
        self.calls.append(list(args))
        if args == ['shell', 'id']:
            return NS(returncode=0,
                      stdout='uid=0(root) gid=0(root)\n' if self.root else 'uid=2000(shell)\n',
                      stderr='')
        if args == ['root']:
            if not self.root_works:
                return NS(returncode=1, stdout='adbd cannot run as root\n', stderr='')
            self.root = True
            return NS(returncode=0, stdout='restarting adbd as root\n', stderr='')
        if args == ['wait-for-device']:
            return NS(returncode=0, stdout='', stderr='')
        if args[:2] == ['exec-out', 'cat']:
            path = args[2]
            if self.read_requires_root and not self.root:
                # Real adb exec-out returns 0 and puts the remote error on stdout.
                return NS(returncode=0,
                          stdout=f'cat: {path}: Permission denied\n', stderr='')
            if path == self.pref:
                if self.xml is None:
                    return NS(returncode=1, stdout='', stderr='No such file')
                return NS(returncode=0, stdout=self.files.get(self.pref, self.xml), stderr='')
            if path in self.files:
                return NS(returncode=0, stdout=self.files[path], stderr='')
            return NS(returncode=1, stdout='', stderr='No such file')
        if args == ['shell', 'am', 'force-stop', self.package]:
            return NS(returncode=1 if self.force_stop_fails else 0, stdout='',
                      stderr='force-stop failed\n' if self.force_stop_fails else '')
        if args == ['shell', 'pidof', self.package]:
            if self.pidof_timeout:
                raise TimeoutExpired(['pidof', self.package], 5)
            if self.pidof_error:
                return NS(returncode=1, stdout='',
                          stderr='error: device offline\n')
            return NS(returncode=0 if not self.stop_works else 1,
                      stdout='1234\n' if not self.stop_works else '', stderr='')
        if args and args[0] == 'push':
            self.files[args[2]] = Path(args[1]).read_text(encoding='utf-8')
            return NS(returncode=0, stdout='1 file pushed\n', stderr='')
        if args and args[0] == 'shell' and args[1].startswith('cp -p '):
            source, target = args[1][6:].split(' ', 1)
            source, target = source.strip(), target.strip()
            if self.copy_fails:
                return NS(returncode=1, stdout='', stderr='copy failed')
            self.files[target] = self.files.get(source, self.xml)
            return NS(returncode=0, stdout='', stderr='')
        if args and args[0] == 'shell' and args[1].startswith('cat ') and ' > ' in args[1]:
            source, target = args[1][4:].split(' > ', 1)
            source, target = source.strip(), target.strip()
            if target == self.pref:
                raise AssertionError('the atomic path must not truncate the pref')
            if self.write_interrupt:
                # Simulate a partial candidate write before the command times out.
                self.files[target] = '<map>\n'
                raise TimeoutExpired(['cat'], 20)
            self.files[target] = self.files.get(source, '')
            return NS(returncode=0, stdout='', stderr='')
        if args and args[0] == 'shell' and args[1].startswith('mv -f '):
            source, target = args[1][6:].split(' ', 1)
            source, target = source.strip(), target.strip()
            if self.rename_fails or source not in self.files:
                return NS(returncode=1, stdout='', stderr='rename failed')
            self.xml = self.files.pop(source)
            return NS(returncode=0, stdout='', stderr='')
        if args and args[0] == 'shell' and args[1].startswith('rm -f '):
            for path in args[1][6:].split():
                self.files.pop(path, None)
            return NS(returncode=0, stdout='', stderr='')
        raise AssertionError(f'unexpected adb call: {args!r}')


def test_client_flush_before_exit_keeps_other_preferences():
    adb = Adb(root=True)
    flushed = XML.replace('</map>', '    <int name="volume" value="73" />\n</map>')

    def run(args, **kwargs):
        if args == ['shell', 'am', 'force-stop', PKG]:
            adb.xml = flushed
        return adb(args, **kwargs)

    assert setting.ensure_animation_off('adb.exe', 'serial', PKG, runner=run) == 'fixed'
    assert adb.xml == setting.rewrite_off(flushed)


def test_slow_animation_probe_stops_at_shared_budget_before_applying(monkeypatch):
    from pcr_maa.labyrinth.animation_ui import needs_ui_fallback
    elapsed = 0
    calls = []
    logs = []
    monkeypatch.setattr(setting, 'monotonic', lambda: elapsed)

    def slow(args, *, timeout):
        nonlocal elapsed
        calls.append((args[:2], timeout))
        elapsed += timeout
        raise TimeoutExpired(args, timeout)

    result = setting.ensure_animation_off('unused', 'serial', PKG, log=logs.append, runner=slow,
        before_apply=lambda: calls.append(('unexpected mutation', 0)))
    assert calls == [(['exec-out', 'cat'], 20), (['shell', 'id'], 10)]
    assert elapsed == 30 and result == 'probe_timeout'
    assert needs_ui_fallback(result)
    assert len(logs) == 1 and '任务继续' in logs[0]


def test_animation_already_off_after_exit_skips_write():
    adb = Adb(root=True)

    def run(args, **kwargs):
        if args == ['shell', 'am', 'force-stop', PKG]:
            adb.xml = setting.rewrite_off(XML)
        return adb(args, **kwargs)

    assert setting.ensure_animation_off('adb.exe', 'serial', PKG, runner=run) == 'already_off'
    assert not any(call[0] == 'push' for call in adb.calls)


def test_state_and_rewrite_only_the_dialog_entry():
    assert setting.state(XML) == 'on'
    assert setting.rewrite_off(XML) is not None
    rewritten = setting.rewrite_off(XML)
    assert setting.state(rewritten) == 'off'
    assert rewritten.count(setting.OFF) == 1
    # The same-prefix entry used by another client option is untouched.
    assert rewritten.count(setting.ON) == 1
    assert OTHER in rewritten


def test_state_reports_missing_and_unknown():
    assert setting.state('<map></map>') == 'missing'
    assert setting.rewrite_off('<map></map>') is None
    assert setting.state(f'<map><string name="{setting.KEY}">opaque</string></map>') == 'unknown'


def test_enabled_with_root_closes_client_and_writes_off():
    adb = Adb(root=True)
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'fixed'
    assert any('已关闭' in line for line in logs)
    assert setting.state(adb.xml) == 'off'
    assert ['shell', 'am', 'force-stop', PKG] in adb.calls
    assert any(args[0] == 'push' for args in adb.calls)
    assert any(args[0] == 'shell' and args[1].startswith('cp -p ')
               for args in adb.calls)
    assert any(args[0] == 'shell' and args[1].startswith('mv -f ')
               for args in adb.calls)
    # Only the dialog entry changed; the same-prefix entry keeps its value.
    assert adb.xml.count(setting.ON) == 1
    assert adb.xml.count(setting.OFF) == 1


def test_channel_package_uses_its_own_pref_path_and_process_name():
    package = 'com.bilibili.priconne.mi'
    adb = Adb(root=True, package=package)
    logs = []
    assert setting.pref_path(package).endswith('/' + package + '.v2.playerprefs.xml')
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', package,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'fixed'
    assert ['shell', 'am', 'force-stop', package] in adb.calls
    assert any(args == ['exec-out', 'cat', setting.pref_path(package)] for args in adb.calls)
    assert setting.state(adb.xml) == 'off'
    assert any('已关闭' in line for line in logs)


def test_no_root_reads_then_logs_hint_and_keeps_the_task_running():
    adb = Adb(root=False, root_works=False)
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'no_root'
    assert any('未获得 root' in line for line in logs)
    assert ['shell', 'am', 'force-stop', PKG] not in adb.calls
    assert not any(args[0] == 'push' for args in adb.calls)


def test_read_after_root_when_shell_cannot_see_app_data():
    adb = Adb(root=False, root_works=True, read_requires_root=True)
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'fixed'
    assert adb.root is True
    assert adb.calls[:3] == [['exec-out', 'cat', PREF], ['shell', 'id'], ['root']]
    assert setting.state(adb.xml) == 'off'
    assert any('已关闭' in line for line in logs)


def test_denied_exec_out_is_not_mistaken_for_a_prefs_document():
    # adb exec-out exits 0 for a remote permission error; only a real <map>
    # document may skip the root escalation.
    adb = Adb(root=False, root_works=False, read_requires_root=True)
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'no_root'
    assert ['root'] in adb.calls
    assert any('未获得 root' in line for line in logs)


def test_already_off_skips_client_and_write():
    adb = Adb(root=True, xml=XML.replace(setting.ON, setting.OFF, 1))
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'already_off'
    assert any('已是' in line for line in logs)
    assert ['shell', 'am', 'force-stop', PKG] not in adb.calls
    assert not any(args[0] == 'push' for args in adb.calls)


def test_stop_failure_skips_write():
    adb = Adb(root=True, stop_works=False)
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'stop_failed'
    assert any('未完全关闭' in line for line in logs)
    assert not any(args[0] == 'push' for args in adb.calls)
    assert setting.state(adb.xml) == 'on'


def test_force_stop_failure_and_pidof_timeout_skip_write():
    adb = Adb(root=True, force_stop_fails=True, pidof_timeout=True)
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'stop_unverified'
    assert ['shell', 'am', 'force-stop', PKG] in adb.calls
    assert any('进程查询超时' in line for line in logs)
    assert not any(args[0] == 'push' for args in adb.calls)
    assert setting.state(adb.xml) == 'on'


def test_process_query_transport_error_skips_write():
    adb = Adb(root=True, pidof_error=True)
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'stop_unverified'
    assert any('进程查询超时或失败' in line for line in logs)
    assert not any(args[0] == 'push' for args in adb.calls)
    assert setting.state(adb.xml) == 'on'


def test_temp_copy_failure_keeps_the_original_document():
    adb = Adb(root=True, copy_fails=True)
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'write_failed'
    assert adb.xml == XML
    assert any('原配置未改动' in line for line in logs)
    assert not any(args[0] == 'push' for args in adb.calls)


def test_write_interruption_of_the_temp_keeps_the_original_document():
    adb = Adb(root=True, write_interrupt=True)
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'write_failed'
    assert adb.xml == XML
    assert any('原配置未改动' in line for line in logs)
    assert any(args[0] == 'shell' and args[1].startswith('cp -p ')
               for args in adb.calls)
    assert not any(args[0] == 'shell' and args[1].startswith('mv -f ')
                   for args in adb.calls)


def test_rename_failure_keeps_the_original_document():
    adb = Adb(root=True, rename_fails=True)
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'write_failed'
    assert adb.xml == XML
    assert any('原配置未改动' in line for line in logs)
    assert any(args[0] == 'shell' and args[1].startswith('mv -f ')
               for args in adb.calls)


def test_missing_entry_logs_unknown_without_stopping_the_client():
    adb = Adb(root=True, xml='<map></map>')
    logs = []
    result = setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        log=logs.append, stopped=lambda: False, runner=adb)
    assert result == 'unknown'
    assert any('无法识别' in line for line in logs)
    assert ['shell', 'am', 'force-stop', PKG] not in adb.calls


def test_stopped_owner_returns_before_any_adb_call():
    adb = Adb(root=True)
    assert setting.ensure_animation_off('C:/adb.exe', 'serial', PKG,
        stopped=lambda: True, runner=adb) == 'stopped'
    assert adb.calls == []


def test_stop_during_initial_read_does_not_start_another_adb_command():
    calls = []
    def read_then_stop(args, *, timeout):
        calls.append(args[:2])
        return NS(returncode=1, stdout='', stderr='')
    assert setting.ensure_animation_off('unused', 'serial', PKG,
        stopped=lambda: bool(calls), runner=read_then_stop) == 'stopped'
    assert calls == [['exec-out', 'cat']]


def test_missing_adb_is_silent_and_touches_nothing(tmp_path):
    logs = []
    assert setting.ensure_animation_off(str(tmp_path / 'missing-adb.exe'), 'serial', PKG,
        log=logs.append) == 'adb_missing'
    assert logs == []


def test_hd_adb_path_is_supported(tmp_path):
    from pcr_maa.device import adb_path_supported
    hd = tmp_path / 'HD-Adb.exe'
    adb = tmp_path / 'adb.exe'
    other = tmp_path / 'adb2.exe'
    for path in (hd, adb, other):
        path.write_bytes(b'')
    assert adb_path_supported(str(hd))
    assert adb_path_supported(str(adb))
    assert not adb_path_supported(str(other))
