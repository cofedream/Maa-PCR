"""Run-bound GUI entry timing. Synthetic Owner records, no device input."""
import json
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pcr_maa import agent_entry, supported_daily
from pcr_maa.device import SERIAL
from test_agent_callbacks import FakeContext, registry
from test_r39_navigation import config, result_for


def screenshot(pid, millisecond, event, controller):
    detail = json.dumps(dict(action='screencap', ctrl_id=3, uuid=controller), separators=(',', ':'))
    return (f'[2026-11-26 00:00:00.{millisecond:03}][TRC][Px{pid}][Tx2]'
            f'[MaaNS::AgentNS::ClientNS::AgentClient::ctrl_event_sink] [handle=true] '
            f'[message=Controller.Action.{event}] [details_json={detail}] [trans_arg=true]\n')


@pytest.mark.parametrize('origin', ['owner', 'other_process', 'missing'])
def test_gui_entry_uses_owner_window_and_preserves_business_outcome(tmp_path, monkeypatch, registry, origin):
    trace = tmp_path / 'maafw.log'
    def recognized(pid, duration):
        return f'[2026-11-26 00:00:00.013][DBG][Px{pid}][Tx1][MaaNS::VisionNS::OCRer::analyze] [cost={duration}ms]\n'
    trace.write_text(recognized(17, 999), encoding='utf8')
    monkeypatch.setattr(agent_entry, 'load_config', lambda path: config())
    monkeypatch.setattr(agent_entry, 'LOG_ROOT', tmp_path / 'agent')
    monkeypatch.setattr(supported_daily, 'CONTROL_ROOT', tmp_path / 'work')
    monkeypatch.setattr('pcr_maa.player_log.capture', lambda tasker, outcome: None)
    context = FakeContext()
    context.tasker.controller = SimpleNamespace(uuid='bound')
    def dispatch(entry):
        if origin != 'missing':
            pid = 17 if origin == 'owner' else 18
            with trace.open('a', encoding='utf8') as f:
                f.write(recognized(pid, 5) + recognized(99, 1000))
                f.write(screenshot(pid, 0, 'Starting', 'other') + screenshot(pid, 90, 'Succeeded', 'other'))
                f.write(screenshot(pid, 10, 'Starting', 'bound') + screenshot(pid, 30, 'Succeeded', 'bound'))
        return SimpleNamespace(status=SimpleNamespace(succeeded=True))
    context.run_task = dispatch
    invocation = uuid4().hex
    def authorize(context, selection):
        return agent_entry.ExecutionContext(invocation_id=invocation, root_task_id=1, controller_uuid='bound',
            serial=SERIAL, combination=selection, execute=True, game_day=supported_daily.current_game_day(),
            count_context='user-reported-unreset', deadline=time.monotonic() + 30,
            input_display=2, screenshot_display='123', timing_owner_pid=17, timing_owner_log=str(trace))
    def step(tasker, name, cfg, *args, exit_page='home'):
        tasker.run_task('OfflineRead', deadline=time.monotonic() + 3)
        return result_for(name, cfg, exit_page)
    monkeypatch.setattr(supported_daily, '_run_step', step)
    action = agent_entry.AlphaEntry(registry, execution_context=authorize)
    selection = dict(version=1, selected_modules=['deep'], rewards_final=False)
    args = SimpleNamespace(custom_action_name=agent_entry.ACTION, task_detail=SimpleNamespace(task_id=1),
                           custom_action_param=json.dumps(selection))
    assert action.run(context, args), action.last_result
    report = json.loads(Path(action.last_result['report_path']).read_text(encoding='utf8'))
    perf = report['performance']
    assert report['run_id'] == Path(action.last_result['report_path']).parent.name
    assert perf['coverage']['owner_invocation_id'] == invocation
    assert perf['native_timings']['page_wait']['count'] == 1
    if origin == 'owner':
        assert perf['native_timing_collection'] == 'collected'
        assert perf['native_timings']['screenshot']['total_ms'] == 20
        assert perf['native_timings']['recognition']['total_ms'] == 5
    else:
        assert perf['native_timing_collection'] == 'incomplete'
        assert perf['native_timings']['screenshot'] is perf['native_timings']['recognition'] is None
        assert perf['coverage']['trace']['issues'] == ['owner_records_unavailable']
