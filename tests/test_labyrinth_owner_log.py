"""The owner's live trace is display only: it can never move a business result."""
import json
from tempfile import TemporaryDirectory

from pcr_maa.labyrinth.batch import Batch
from pcr_maa.labyrinth.config import Goal, Settings
from pcr_maa.preset_execution import Journal
from test_labyrinth import Port


def run_batch(port, log, settings=None):
    with TemporaryDirectory() as tmp:
        journal = Journal(tmp)
        return Batch(settings or Settings((Goal(1, 1),)), port, journal, port.binding,
                     log=log).run()


def test_one_operation_reports_one_sent_and_one_confirmed_line():
    lines = []
    port = Port()
    assert run_batch(port, lines.append)['batch_complete'] is True
    sent = [line for line in lines if line.startswith('操作 ') and line.endswith('已发出，等待确认')]
    confirmed = [line for line in lines if line.startswith('操作 ') and '已确认' in line]
    # A third confirmation line per operation would double the panel without
    # adding state; the observation inside transaction is merged into 已确认.
    assert len(sent) == len(confirmed) == port.enters == 1
    assert not [line for line in lines if 'operation_id=' in line]
    opening = lines.index('开始第 1 次开局筛选（工会 1）')
    first_sent = next(i for i, line in enumerate(lines) if line.startswith('操作 '))
    assert opening < first_sent
    assert lines[-1] == '批次结果 complete 进度 1:1'


def test_failed_log_delivery_never_changes_the_batch_result():
    def broken(text):
        raise RuntimeError('synthetic log sink failure')

    port = Port()
    assert run_batch(port, broken)['batch_complete'] is True


def test_reroll_decision_and_stop_reason_are_traced():
    lines = []
    port = Port(bad=True)
    result = run_batch(port, lines.append)
    assert result['outcome'] == 'opening_not_matching'
    assert '第 1 次开局候选不符合路线计划，已关闭“重新开局”，本次结束' in lines
    assert '本次停止：opening_not_matching（位置 ' in '\n'.join(lines)


def test_trace_never_carries_exception_text():
    lines = []
    port = Port(unknown_enter=True)
    assert run_batch(port, lines.append)['outcome'] == 'adapter_failed'
    joined = '\n'.join(lines)
    assert 'secret transport detail' not in joined
    assert any(line.startswith('本次停止：adapter_failed（位置 ') for line in lines)


def test_owner_log_is_normalized_bounded_and_display_only(tmp_path, monkeypatch):
    from test_labyrinth_owned_startup import runtime
    owned, events, calls = runtime(tmp_path, monkeypatch)
    owned._log('  确认\n页面   shop ')
    owned._log('x' * 900)
    owned._log(123)
    owned._log('   ')
    assert [value['kind'] for value in events] == ['log', 'log']
    assert events[0] == dict(kind='log', text='确认 页面 shop')
    assert len(events[1]['text']) == 512


def test_owner_log_pipe_failure_preserves_startup_and_credentials(tmp_path, monkeypatch):
    from test_labyrinth_owned_startup import runtime
    owned, events, calls = runtime(tmp_path, monkeypatch)

    def emit(value):
        if value.get('kind') == 'log':
            raise RuntimeError('synthetic owner pipe failure')
        events.append(value)

    owned.emit = emit
    owned._log('trace before startup')
    assert owned.run() == dict(outcome='stopped', batch_complete=False)
    assert 'batch' in calls
    logged = json.dumps([value for value in events if value.get('kind') == 'log'], ensure_ascii=False)
    assert 'synthetic-user' not in logged and 'synthetic-password' not in logged
