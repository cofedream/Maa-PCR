from pathlib import Path
import json
import pytest


def config():
    return {'version': 1, 'start': 'home', 'device': {'input_display': 2, 'screenshot_display': '123'},
            'presets': [dict(kind=k, slot=s, label='重名', target_per_stage=t)
                        for k, s, t in [('hard', 1, 3), ('deep', 4, 10), ('very_hard', 2, 3)]]}


def test_config_missing_invalid_and_labels(tmp_path):
    from pcr_maa.daily import load_config, validate_config
    with pytest.raises(ValueError, match='配置'):
        load_config(tmp_path/'missing.json')
    p = tmp_path/'bad.json'; p.write_text('{', encoding='utf-8')
    with pytest.raises(ValueError, match='配置'):
        load_config(p)
    c = validate_config(config())
    assert [v['kind'] for v in c['presets']] == ['deep', 'very_hard', 'hard']
    assert c['count_context'] is None
    assert len({v['label'] for v in c['presets']}) == 1


@pytest.mark.parametrize('change', [
    lambda c: c['presets'][0].update(slot=8),
    lambda c: c['presets'][0].update(slot=True),
    lambda c: c['presets'][0].update(slot=4),
    lambda c: c['presets'][0].update(kind='deep'),
    lambda c: c['presets'][0].update(target_per_stage=-1),
    lambda c: c['device'].update(command='shell'),
    lambda c: c.update(count_context=True),
    lambda c: c.update(presets=[]),
])
def test_bad_config(change):
    from pcr_maa.daily import validate_config
    c = config(); change(c)
    with pytest.raises(ValueError):
        validate_config(c)


@pytest.mark.parametrize('key', ['execute', 'game_day', 'authorize', 'schedule_authorizations',
    'schedule_attest_current_plan', 'attest_current_plan', 'ticket_permission', 'unknown'])
def test_portable_config_rejects_persisted_authority(tmp_path, key):
    from pcr_maa.daily import validate_config
    adb = tmp_path / 'adb.exe'; adb.write_bytes(b'not executed')
    c = config() | dict(version=2, connection=dict(adb_path=str(adb), serial='127.0.0.1:5555'))
    assert validate_config(c)['connection'] == c['connection']
    with pytest.raises(ValueError):
        validate_config(c | {key: True})
    with pytest.raises(ValueError):
        validate_config(c | dict(count_context='user-reported-unreset'))


def test_portable_config_default_inspection_zero_device(tmp_path, monkeypatch):
    from pcr_maa import cli, device, native
    adb = tmp_path / 'adb.exe'; adb.write_bytes(b'not executed')
    c = config() | dict(version=2, connection=dict(adb_path=str(adb), serial='127.0.0.1:5555'))
    path = tmp_path / 'local.json'; path.write_text(json.dumps(c), encoding='utf-8')
    monkeypatch.setattr(device, 'navigate', lambda **kw: pytest.fail('default device operation'))
    monkeypatch.setattr(native, 'setup', lambda: pytest.fail('default native setup'))
    assert cli.main(['inspect-daily', '--config', str(path)]) == 0
    assert cli.main(['execute-preset', '--config', str(path), '--game-day', '2026-09-26',
                     '--kind', 'deep']) == 2


def test_portable_observation_forwards_binding_only_when_explicit(tmp_path, monkeypatch):
    from pcr_maa import cli, native, device
    adb = tmp_path / 'adb.exe'; adb.write_bytes(b'not executed')
    c = config() | dict(version=2, connection=dict(adb_path=str(adb), serial='127.0.0.1:5555'))
    path = tmp_path / 'local.json'; path.write_text(json.dumps(c), encoding='utf-8')
    calls = []
    def observe(**kw):
        calls.append(kw)
        return kw['daily_report'] | dict(inspection_complete=True, outcome='ok')
    monkeypatch.setattr(device, 'navigate', observe)
    monkeypatch.setattr(native, 'setup', lambda: None)
    monkeypatch.setattr(native, 'save_receipt', lambda *a: tmp_path / 'unused.json')
    assert cli.main(['inspect-daily', '--config', str(path), '--observe']) == 0
    assert calls[0]['connection'] == c['connection']


def test_missing_config_cli_never_connects(tmp_path, monkeypatch, capsys):
    from pcr_maa.cli import main
    import pcr_maa.device as device
    monkeypatch.setattr(device, 'navigate', lambda *a, **k: pytest.fail('connection attempted'))
    assert main(['inspect-daily', '--config', str(tmp_path/'missing.json')]) == 1
    assert '配置' in capsys.readouterr().err


def test_close_schedule_native():
    from pcr_maa.native import load_resource, bind, run
    from pcr_maa.replay import SavedImages
    from test_schedule import frame
    c = SavedImages([[frame('schedule-04-panel.png')], [frame('schedule-03-home-settled.png')]])
    r = run(bind(load_resource(), c), 'CloseSchedule')
    assert r['native_ok'] and any(n['name'] == 'ObserveHome' and n['completed'] for n in r['nodes'])
    assert len(c.clicks) == 1 and not c.swipes


@pytest.mark.parametrize('name', ['schedule-05-settings-top.png', '05-sweep-presets.png', 'schedule-03-home-settled.png', None])
def test_close_wrong_page_zero_input(name):
    import numpy as np
    from pcr_maa.native import load_resource, bind, run
    from pcr_maa.replay import SavedImages
    from test_schedule import frame
    im = frame(name) if name else np.zeros((720, 1280, 3), dtype=np.uint8)
    c = SavedImages([[im]])
    r = run(bind(load_resource(), c), 'CloseSchedule')
    assert not any(n['name'] == 'ObserveHome' and n['completed'] for n in r['nodes'])
    assert not c.clicks and not c.swipes


@pytest.mark.parametrize('start', ['home', 'schedule'])
def test_full_daily_native_replay(start):
    from pcr_maa.daily import inspect_daily
    from pcr_maa.native import load_resource, bind, save_receipt
    from pcr_maa.replay import SavedImages
    from test_preset_scan import frame, checked_short, SHORT_SCAN
    names = ['schedule-03-home-settled.png', 'schedule-04-panel.png',
             'schedule-07-notifications-middle.png', 'schedule-08-notifications-lower.png',
             'parent-home-before-product.png', '02-adventure-settled.png',
             '04-main-map-settled.png', '05-sweep-presets.png', '10-slot4-top.png',
             '12-slot4-list-view-toggle.png', '13-slot4-checked-bottom.png',
             '09-slot2-top.png']
    # One synthetic checked label for slot2, explicitly not new live evidence.
    if start == 'schedule':
        names = names[1:]
    images = [frame(n) for n in names] + [checked_short()] + [frame(n) for n in SHORT_SCAN]
    images += [frame('04-main-map-settled.png'), frame('schedule-03-home-settled.png')]
    c = SavedImages([[im] for im in images])
    cfg = config(); cfg['start'] = start; cfg['count_context'] = 'same-day-unreset'
    tasker = bind(load_resource(), c)
    r = inspect_daily(tasker, cfg)
    save_receipt(r | {'clicks': c.clicks, 'swipes': c.swipes,
                      'synthetic': 'slot2 checked label'}, 'daily-full-replay')
    assert r['inspection_complete'], r
    assert [s['status'] for s in r['steps']] == ['ok'] * 7
    schedule = r['steps'][0]['result']
    assert schedule['notifications']['completed'] == 4 and schedule['notifications']['total'] == 7
    assert schedule['delegated']['completed'] == schedule['delegated']['total'] == 9
    assert [len(s['result']['rows']) for s in r['steps'][3:6]] == [5, 3, 6]
    assert r['business_goal_met'] is True and not r['daily_complete'] and not r['schedule_executed']
    assert [row['needed'] for row in r['steps'][5]['result']['daily_goal']['rows'][-2:]] == [0, 0]
    assert r['steps'][-1]['name'] == 'return_home'
    assert any(n['name'] == 'ObserveHome' and n['completed'] for n in r['steps'][-1]['result']['nodes'])
    assert c.stage == len(images) - 1
    assert len(c.swipes) == 6
    # Repeat with the SAME Resource/Tasker to catch dangling registrations and old collectors.
    c.stage = 0; c.frame = 0
    cfg['count_context'] = None
    second = inspect_daily(tasker, cfg)
    save_receipt(second, 'daily-repeat-unconfirmed')
    assert second['inspection_complete'] and second['business_goal_met'] is None
    assert len(c.swipes) == 12
    assert second['steps'][3]['result']['pages'] is not r['steps'][3]['result']['pages']


def injected(monkeypatch, *, fail=None, interrupt=False, wrong_slot=False):
    from types import SimpleNamespace
    import pcr_maa.native as native
    import pcr_maa.schedule as schedule
    import pcr_maa.preset_scan as scan
    calls = []
    def step(name, value):
        calls.append(name)
        if name == fail:
            if interrupt:
                raise KeyboardInterrupt()
            raise RuntimeError('injected failure')
        return value
    monkeypatch.setattr(schedule, 'inspect_schedule', lambda *a, **k: step('schedule', dict(native_ok=True, inspection_complete=True)))
    monkeypatch.setattr(native, 'run', lambda t, entry: step(entry, dict(native_ok=True, navigation_complete=True, nodes=[dict(name='ObserveHome', completed=True)])))
    def scanning(t, slot, **kw):
        return step(slot, dict(native_ok=True, list_complete=True, view='checked', requested_slot=slot, selected_slot=slot,
                              rows=[dict(title='外传1-3', category='外传', difficulty='困难', remaining=2, limit=5, checked=True)]))
    monkeypatch.setattr(scan, 'scan', scanning)
    monkeypatch.setattr(native, 'observe_tasker', lambda t: step('observe', dict(native_ok=True, observation={'page': 'presets', 'selected_slot': 7 if wrong_slot else next(c for c in reversed(calls) if type(c) is int)})))
    tasker = SimpleNamespace(running=False, stopping=False,
        post_stop=lambda: calls.append('stop') or SimpleNamespace(done=True, succeeded=True))
    return tasker, calls


@pytest.mark.parametrize('context,expected', [(None, None), ('same-day-unreset', False)])
def test_context_and_unmet_are_not_read_failure(monkeypatch, context, expected):
    from pcr_maa.daily import inspect_daily
    t, calls = injected(monkeypatch)
    cfg = config(); cfg['count_context'] = context
    r = inspect_daily(t, cfg)
    assert r['inspection_complete'] and r['business_goal_met'] is expected
    assert r['count_context_source'] == ('caller_assertion' if context else 'unconfirmed')
    assert not r['daily_complete'] and not r['schedule_executed']


@pytest.mark.parametrize('failure,index', [('schedule', 0), ('CloseSchedule', 1), ('NavigatePresets', 2), (4, 3), (2, 4), (1, 5), ('ReturnHome', 6)])
@pytest.mark.parametrize('interrupt', [False, True])
def test_failure_stops_and_keeps_partial(monkeypatch, failure, index, interrupt):
    from pcr_maa.daily import inspect_daily
    t, calls = injected(monkeypatch, fail=failure, interrupt=interrupt)
    r = inspect_daily(t, config())
    assert not r['inspection_complete']
    assert all(s['status'] == 'ok' and 'result' in s for s in r['steps'][:index])
    assert r['steps'][index]['status'] == ('interrupted' if interrupt else 'failed')
    assert all(s['status'] == 'not_run' for s in r['steps'][index+1:])
    assert calls[-1] == 'stop' and calls[-2] == failure


def test_wrong_final_slot_stops(monkeypatch):
    from pcr_maa.daily import inspect_daily
    t, calls = injected(monkeypatch, wrong_slot=True)
    r = inspect_daily(t, config())
    assert not r['inspection_complete'] and r['steps'][3]['status'] == 'failed'
    assert all(s['status'] == 'not_run' for s in r['steps'][4:])
    assert 2 not in calls and 1 not in calls


def test_repeated_calls_collect_again(monkeypatch):
    from pcr_maa.daily import inspect_daily
    t, calls = injected(monkeypatch)
    a = inspect_daily(t, config()); b = inspect_daily(t, config())
    assert a is not b and a['steps'][0]['result'] is not b['steps'][0]['result']
    assert calls.count('schedule') == calls.count(4) == calls.count(2) == calls.count(1) == 2


def test_duplicate_json_keys(tmp_path):
    from pcr_maa.daily import load_config
    p = tmp_path/'duplicate.json'; p.write_text('{"version":1,"version":1}', encoding='utf-8')
    with pytest.raises(ValueError, match='重复 JSON'):
        load_config(p)


def test_total_timeout_before_input(monkeypatch):
    import pcr_maa.daily as daily
    t, calls = injected(monkeypatch)
    ticks = iter([0, 601, 602])
    monkeypatch.setattr(daily.time, 'monotonic', lambda: next(ticks, 602))
    r = daily.inspect_daily(t, config())
    assert not r['inspection_complete'] and calls == ['stop']
    assert all(s['status'] == 'not_run' for s in r['steps'][1:])


def test_expired_native_deadline_posts_no_task():
    from types import SimpleNamespace
    import pcr_maa.native as native
    token = native.RUN_DEADLINE.set(0)
    try:
        with pytest.raises(RuntimeError, match='deadline'):
            native.run(SimpleNamespace(post_task=lambda _: pytest.fail('expired task posted')), 'Doctor')
    finally:
        native.RUN_DEADLINE.reset(token)


@pytest.mark.parametrize('pause', [True, False])
@pytest.mark.parametrize('observe', [True, False])
@pytest.mark.parametrize('exit_code', [0, 2, 130, 37])
def test_launcher_space_unicode_and_exit(tmp_path, exit_code, pause, observe):
    import os
    import shutil
    import subprocess
    import sys
    from pcr_maa.native import ROOT
    # Execute a relocated launcher ONLY with an injected offline CLI, never the live entry.
    root = tmp_path/'中文 space project'
    (root/'.venv/Scripts').mkdir(parents=True)
    (root/'src/pcr_maa').mkdir(parents=True)
    interpreter = Path(sys.executable).resolve()
    venv_config = interpreter.parents[1]/'pyvenv.cfg'
    embedded_paths = list(interpreter.parent.glob('python*._pth'))
    if venv_config.is_file():
        shutil.copyfile(interpreter, root/'.venv/Scripts/python.exe')
        shutil.copyfile(venv_config, root/'.venv/pyvenv.cfg')
    elif embedded_paths:
        shutil.copyfile(interpreter, root/'.venv/Scripts/python.exe')
        for library in interpreter.parent.glob('*.dll'):
            shutil.copyfile(library, root/'.venv/Scripts'/library.name)
        # Load only the injected CLI plus the real interpreter's standard library.
        paths = [root/'src', *interpreter.parent.glob('python*.zip'), interpreter.parent]
        (root/'.venv/Scripts'/embedded_paths[0].name).write_text(
            '\n'.join(str(path) for path in paths) + '\n', encoding='utf-8')
    else:
        import venv
        venv.EnvBuilder(with_pip=False).create(root/'.venv')
    shutil.copyfile(ROOT/'inspect-daily.bat', root/'inspect-daily.bat')
    (root/'src/pcr_maa/__init__.py').write_text('', encoding='utf-8')
    (root/'src/pcr_maa/cli.py').write_text(
        'import sys, json\nfrom pathlib import Path\n'
        'assert sys.argv[1:] == ["inspect-daily", "--config", str(Path.cwd()/".runtime/daily-config.json")]'
        f' + {(["--observe"] if observe else [])!r}\n'
        f'print("OFFLINE_ENTRY_INJECTION")\nraise SystemExit({exit_code})\n', encoding='utf-8')
    done = subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(root/'inspect-daily.bat'),
                          *([] if pause else ['--no-pause']), *(['--observe'] if observe else [])],
                          cwd=tmp_path, input=b"\n", capture_output=True, timeout=20)
    assert done.returncode == exit_code, done.stderr
    assert b'OFFLINE_ENTRY_INJECTION' in done.stdout
    assert (b'Press any key' in done.stdout) is pause
    invalid = subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(root/'inspect-daily.bat'), '--no-pause', '--bad'], cwd=tmp_path, input=b'\n', capture_output=True, timeout=20)
    assert invalid.returncode == 2 and b'OFFLINE_ENTRY_INJECTION' not in invalid.stdout


def test_cli_daily_receipt_and_exit(tmp_path, monkeypatch, capsys):
    from pcr_maa.cli import main
    import pcr_maa.device as device
    p = tmp_path/'local.json'; p.write_text(json.dumps(config()), encoding='utf-8')
    t, calls = injected(monkeypatch)
    def offline(**kwargs):
        from pcr_maa.daily import inspect_daily
        return inspect_daily(t, kwargs['daily_config'], source='function injection', report=kwargs['daily_report'])
    monkeypatch.setattr(device, 'navigate', offline)
    assert main(['inspect-daily', '--config', str(p)]) == 0
    out = capsys.readouterr().out
    assert '只检查，不执行日常' in out and '结构化报告' in out and 'unknown' in out


def test_native_deadline_stops_real_task():
    import time
    import numpy as np
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    c = SavedImages([[np.zeros((720, 1280, 3), dtype=np.uint8)]])
    t = native.bind(native.load_resource(), c)
    token = native.RUN_DEADLINE.set(time.monotonic() + .15)
    try:
        with pytest.raises(RuntimeError, match='exceeded'):
            native.run(t, 'NavigatePresets')
    finally:
        native.RUN_DEADLINE.reset(token)
    assert not t.running and not c.clicks and not c.swipes


@pytest.mark.parametrize('mask', [(325, 620, 100, 43), (550, 620, 130, 43)])
def test_close_requires_both_controls(mask):
    from pcr_maa.native import load_resource, bind, run
    from pcr_maa.replay import SavedImages
    from test_schedule import frame
    im = frame('schedule-04-panel.png')
    x, y, w, h = mask; im[y:y+h, x:x+w] = 0
    c = SavedImages([[im]])
    r = run(bind(load_resource(), c), 'CloseSchedule')
    assert not any(n['name'] == 'ObserveHome' for n in r['nodes'])
    assert not c.clicks and not c.swipes


def test_close_without_home_stops_after_one_click():
    import numpy as np
    from pcr_maa.native import load_resource, bind, run
    from pcr_maa.replay import SavedImages
    from test_schedule import frame
    c = SavedImages([[frame('schedule-04-panel.png')], [np.zeros((720, 1280, 3), dtype=np.uint8)]])
    r = run(bind(load_resource(), c), 'CloseSchedule')
    assert not any(n['name'] == 'ObserveHome' and n['completed'] for n in r['nodes'])
    assert len(c.clicks) == 1 and not c.swipes


def test_returned_failure_stops_without_next_input(monkeypatch):
    from pcr_maa.daily import inspect_daily
    import pcr_maa.preset_scan as scan
    t, calls = injected(monkeypatch)
    monkeypatch.setattr(scan, 'scan', lambda *a, **k: dict(native_ok=True, list_complete=False, rows=[], incomplete_reason='no_progress'))
    r = inspect_daily(t, config())
    assert r['steps'][3]['status'] == 'failed' and r['steps'][3]['error'] == 'no_progress'
    assert all(s['status'] == 'not_run' for s in r['steps'][4:])
    assert calls == ['schedule', 'CloseSchedule', 'NavigatePresets']


def test_invalid_config_never_mutates_file(tmp_path, monkeypatch):
    from pcr_maa.cli import main
    import pcr_maa.device as device
    p = tmp_path/'account.json'; p.write_bytes(b'{bad json')
    monkeypatch.setattr(device, 'navigate', lambda *a, **k: pytest.fail('connected'))
    assert main(['inspect-daily', '--config', str(p)]) == 1
    assert p.read_bytes() == b'{bad json'


def test_connection_job_wait_is_bounded(monkeypatch):
    from types import SimpleNamespace
    from pcr_maa import native
    ticks = iter([0, 31])
    monkeypatch.setattr(native.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(TimeoutError, match='30'):
        native.wait_job(SimpleNamespace(done=False), seconds=30)


def test_connection_job_wait_interrupts(monkeypatch):
    from types import SimpleNamespace
    from pcr_maa import native
    def interrupt(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(native.time, 'sleep', interrupt)
    with pytest.raises(KeyboardInterrupt):
        native.wait_job(SimpleNamespace(done=False), seconds=30)


def test_finish_summary_config_and_unknown(monkeypatch):
    from pcr_maa.daily import inspect_daily, summary
    t, calls = injected(monkeypatch)
    r = inspect_daily(t, config())
    text = summary(r)
    assert '配置目标未知' not in text
    assert '每日目标：每关10次' in text and '每日目标：每关3次' in text
    assert '剩余2/5' in text and '目标达成：未知' in text
    assert '计数语境未确认' in text
    assert calls[-1] == 'ReturnHome'
    t, calls = injected(monkeypatch, fail='schedule')
    text = summary(inspect_daily(t, config()))
    assert '槽?' not in text and '槽4，重名' in text and '槽2，重名' in text
    assert 'not_run' in text and '每日目标：每关10次' in text


def test_return_missing_home_keeps_results(monkeypatch):
    from pcr_maa.daily import inspect_daily, summary
    import pcr_maa.native as native
    t, calls = injected(monkeypatch)
    original = native.run
    monkeypatch.setattr(native, 'run', lambda t, e: dict(native_ok=True, nodes=[]) if e == 'ReturnHome' else original(t, e))
    r = inspect_daily(t, config())
    assert not r['inspection_complete']
    assert all(s['status'] == 'ok' and s['result']['rows'] for s in r['steps'][3:6])
    assert r['steps'][-1]['status'] == 'failed'
    assert '检查未完整收尾' in summary(r)


@pytest.mark.parametrize('mask', [None, 'cancel', 'title', 'home', 'map', 'wrong', 'final'])
def test_return_home_controls_native(mask):
    from pcr_maa.native import load_resource, bind, run
    from pcr_maa.replay import SavedImages
    from test_preset_scan import frame
    import numpy as np
    preset = frame('05-sweep-presets.png')
    main = frame('04-main-map-settled.png')
    home = frame('schedule-03-home-settled.png')
    if mask == 'cancel': preset[610:665, 690:865] = 0
    if mask == 'title': preset[30:77, 480:800] = 0
    if mask == 'home': main[680:720, 45:165] = 0
    if mask == 'map': main[11:68, 76:226] = 0
    if mask == 'wrong': preset = home
    if mask == 'final': home = np.zeros_like(home)
    c = SavedImages([[preset], [main], [home]])
    r = run(bind(load_resource(), c), 'ReturnHome')
    observed = any(n['name'] == 'ObserveHome' and n['completed'] for n in r['nodes'])
    assert observed is (mask is None)
    assert len(c.clicks) == (0 if mask in ('cancel', 'title', 'wrong') else 1 if mask in ('home', 'map') else 2)
    assert not c.swipes
    if c.clicks:
        assert 690 <= c.clicks[0][0] <= 865 and 610 <= c.clicks[0][1] <= 665
    if len(c.clicks) == 2:
        assert 45 <= c.clicks[1][0] <= 165 and 675 <= c.clicks[1][1] <= 720


@pytest.mark.parametrize('delay', [1.7, 3.0, 6.0])
def test_home_gate_waits_for_animation_without_input(delay):
    """Saved animation or blank frames wait before the strict home read."""
    from pathlib import Path
    import os
    import time
    import numpy as np
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    from pcr_maa.preset_execution_native import completed
    from test_preset_scan import frame

    home = frame('schedule-03-home-settled.png')
    animation = np.zeros_like(home)
    frame_path = os.environ.get('PCR_HOME_ANIMATION_FRAME')
    if frame_path is not None:
        source = Path(frame_path)
        assert source.is_file(), 'PCR_HOME_ANIMATION_FRAME must be an existing saved image'
        animation = native.read_image(source)
    resource = native.load_resource()
    # The live return path has already used OCR. Separate model startup from animation.
    warm = SavedImages([[home]])
    assert completed(native.run(native.bind(resource, warm), 'ReturnHomeGate'), 'ObserveHome')
    assert not warm.clicks and not warm.swipes

    class Animation(SavedImages):
        first = None
        home_frames = 0

        def screencap(self):
            if len(self.stages) > 1 and self.stage < 2:
                return super().screencap()
            now = time.monotonic()
            if self.first is None:
                self.first = now
            if now - self.first >= delay:
                self.home_frames += 1
                return home.copy()
            return animation.copy()

    stages = ([[frame('05-sweep-presets.png')], [frame('04-main-map-settled.png')], [home]]
              if delay > 4 else [[home]])
    controller = Animation(stages)
    tasker = native.bind(resource, controller)
    started = time.monotonic()
    result = native.run(tasker, 'ReturnHome' if delay > 4 else 'ReturnHomeGate')
    elapsed = time.monotonic() - started
    native.save_receipt(result | dict(synthetic_delay=delay, elapsed=elapsed,
                                    animation_source=frame_path, home_frames=controller.home_frames,
                                    clicks=controller.clicks, swipes=controller.swipes), 'home-gate-animation')
    assert completed(result, 'ObserveHome'), result
    assert controller.home_frames > 0 and not completed(result, 'UnknownStop')
    assert delay <= elapsed < 20
    if delay > 4:
        assert len(controller.clicks) == 2
        assert 690 <= controller.clicks[0][0] <= 865 and 610 <= controller.clicks[0][1] <= 665
        assert 45 <= controller.clicks[1][0] <= 165 and 675 <= controller.clicks[1][1] <= 720
    else:
        assert not controller.clicks
    assert not controller.swipes and not tasker.running
    assert result['daily_complete'] is False


@pytest.mark.parametrize('kind', ['blank', 'map', 'missing-schedule', 'missing-gift'])
def test_home_gate_persistent_negative_stops_without_input(kind):
    import time
    import numpy as np
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    from pcr_maa.preset_execution_native import completed
    from test_preset_scan import frame

    image = (np.zeros((720, 1280, 3), dtype=np.uint8) if kind == 'blank' else
             frame('04-main-map-settled.png' if kind == 'map' else 'schedule-03-home-settled.png'))
    if kind == 'missing-schedule': image[580:640, 970:1080] = 0
    if kind == 'missing-gift': image[580:640, 1170:1270] = 0
    controller = SavedImages([[image]])
    tasker = native.bind(native.load_resource(), controller)
    started = time.monotonic()
    result = native.run(tasker, 'ReturnHomeGate')
    elapsed = time.monotonic() - started
    native.save_receipt(result | dict(case=kind, elapsed=elapsed, clicks=controller.clicks,
                                    swipes=controller.swipes), 'home-gate-negative')
    assert not completed(result, 'ObserveHome')
    import json
    budget = json.loads((native.ROOT / 'assets/resource/pipeline/return_home.json').read_text(encoding='utf-8'))['ReturnHomeGate']['timeout'] / 1000
    assert completed(result, 'UnknownStop') and budget <= elapsed < budget + 8
    assert not controller.clicks and not controller.swipes and not tasker.running
    assert result['daily_complete'] is False
