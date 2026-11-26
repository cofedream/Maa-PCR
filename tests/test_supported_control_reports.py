"""Control readers never need to load the archival report for a new run."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from pcr_maa import supported_daily as chain
from pcr_maa.cli import main
from test_supported_daily import config, success


RUN_ID = 'run-' + 'a' * 32


def test_cli_stop_with_large_receipt_and_concurrent_active_worker(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(chain, 'CONTROL_ROOT', tmp_path)
    directory = tmp_path / RUN_ID
    entered, resume = threading.Event(), threading.Event()
    calls = []
    def run(tasker, name, cfg, day, context, **kwargs):
        calls.append(name)
        if name == 'event':
            return success(name) | {'detail': 'x' * (chain.REPORT_LIMIT + 1)}
        entered.set()
        assert resume.wait(20)
        return success(name)

    monkeypatch.setattr(chain, '_run_step', run)
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(chain.execute_supported, None, config(), game_day=chain.current_game_day(),
                                 context='user-reported-unreset', execute=True, directory=directory)
        try:
            assert entered.wait(20)
            assert (directory / '1-event.json').stat().st_size > chain.REPORT_LIMIT
            assert (directory / 'report.json').stat().st_size < chain.CONTROL_SNAPSHOT_LIMIT
            assert main(['supported-control', 'summary', '--run-id', RUN_ID]) == 0
            active = json.loads(capsys.readouterr().out)
            assert active['steps'][0]['settlement_verified'] is True
            assert active['steps'][1]['unsettled_or_unknown'] is True
            assert active['reported_outcome'] == 'running'
            assert main(['supported-control', 'stop', '--run-id', RUN_ID]) == 0
            assert json.loads(capsys.readouterr().out)['outcome'] == 'stop_requested'
            first_request = (directory / 'stop-request.json').read_bytes()
            assert chain.request_stop(RUN_ID)['outcome'] == 'stop_requested'
            assert (directory / 'stop-request.json').read_bytes() == first_request
        finally:
            resume.set()
        result = worker.result(timeout=20)
    assert calls == ['event', 'deep']
    assert result['outcome'] == 'stopped_by_request'
    assert (directory / 'control-final.json').stat().st_size < chain.CONTROL_SNAPSHOT_LIMIT
    assert (directory / 'report.json').stat().st_size > chain.REPORT_LIMIT
    original = chain._local_json
    def read_control_only(path, *args):
        assert path.name in {'control-final.json', 'stop-request.json'}
        return original(path, *args)
    monkeypatch.setattr(chain, '_local_json', read_control_only)
    assert chain.inspect_run(RUN_ID)['stop_state'] == 'acknowledged_at_boundary'
    assert chain.request_stop(RUN_ID)['outcome'] == 'already_finalized'
    with pytest.raises(ValueError, match='existing_report_cannot_restart'):
        chain.execute_supported(None, config(), execute=True, game_day=chain.current_game_day(),
                                context='user-reported-unreset', directory=directory)


def test_final_snapshot_survives_archival_write_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(chain, 'CONTROL_ROOT', tmp_path)
    folder = tmp_path / RUN_ID
    original = chain._atomic_json
    def write(path, value):
        if path.name == 'report.json' and 'performance' in value:
            raise OSError('archive write failure')
        original(path, value)
    monkeypatch.setattr(chain, '_atomic_json', write)
    monkeypatch.setattr(chain, '_run_step', lambda *a, **kw: success('rewards'))
    with pytest.raises(OSError, match='archive write failure'):
        chain.execute_supported(None, config(), game_day=chain.current_game_day(),
                                context='user-reported-unreset', execute=True,
                                directory=folder, selected_modules=['rewards'])
    result = chain.inspect_run(RUN_ID)
    assert result['report_finalized'] and result['reported_outcome'] == 'complete'
    assert result['steps'][0]['settlement_verified'] is True
    assert json.loads((folder / '1-rewards.json').read_text(encoding='utf-8'))['result'] == success('rewards')
    assert chain.request_stop(RUN_ID)['outcome'] == 'already_finalized'


def test_old_report_strict_validation_and_compact_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(chain, 'CONTROL_ROOT', tmp_path)
    folder = tmp_path / RUN_ID
    monkeypatch.setattr(chain, '_run_step', lambda t, name, *a, **kw: success(name))
    report = chain.execute_supported(None, config(), execute=True, game_day=chain.current_game_day(),
                                     context='user-reported-unreset', directory=folder,
                                     selected_modules=['event', 'rewards'])
    new_summary = chain.inspect_run(RUN_ID)
    (folder / 'control-final.json').unlink()
    assert chain.inspect_run(RUN_ID) == new_summary
    report['steps'][0]['result']['settlement_verified'] = False
    chain._atomic_json(folder / 'report.json', report)
    with pytest.raises(ValueError, match='invalid_report'):
        chain.inspect_run(RUN_ID)
    chain._atomic_json(folder / 'control-final.json', chain._report_projection(report))
    with pytest.raises(ValueError, match='invalid_report'):
        chain.inspect_run(RUN_ID)


def test_receipt_commit_failure_does_not_publish_success(tmp_path, monkeypatch):
    monkeypatch.setattr(chain, 'CONTROL_ROOT', tmp_path)
    folder = tmp_path / RUN_ID
    original = chain._atomic_json
    def write(path, value):
        if path.name == '1-rewards.json':
            raise OSError('receipt unavailable')
        original(path, value)
    monkeypatch.setattr(chain, '_atomic_json', write)
    monkeypatch.setattr(chain, '_run_step', lambda *a, **kw: success('rewards'))
    report = chain.execute_supported(None, config(), execute=True, game_day=chain.current_game_day(),
                                     context='user-reported-unreset', directory=folder, selected_modules=['rewards'])
    assert report['steps'][0]['error'] == 'step_receipt_write_failed'
    summary = chain.inspect_run(RUN_ID)
    assert summary['reported_outcome'] == 'failed'
    assert summary['steps'][0]['unsettled_or_unknown']
    assert not summary['supported_chain_complete']


def test_stop_rechecks_when_finalization_wins_admission_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(chain, 'CONTROL_ROOT', tmp_path)
    folder = tmp_path / RUN_ID
    folder.mkdir()
    report = dict(run_id=RUN_ID, control_version=1, selection_version=1, selected_modules=['rewards'],
                  rewards_final=False, game_day='2026-11-26', started_at=1, outcome='running',
                  daily_complete=False, supported_chain_complete=False, steps=[dict(name='rewards', status='not_run')])
    chain._atomic_json(folder / 'report.json', chain._report_projection(report))

    @contextmanager
    def finish(directory, *, existing_only=False):
        if not existing_only:
            report.update(outcome='failed', finished_at=2)
            chain._atomic_json(directory / 'control-final.json', chain._report_projection(report))
        yield
    monkeypatch.setattr(chain, '_control_lock', finish)
    assert chain.request_stop(RUN_ID)['outcome'] == 'already_finalized'
    assert not (folder / 'stop-request.json').exists()


def test_blocked_progress_reader_does_not_break_windows_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(chain, 'CONTROL_ROOT', tmp_path)
    folder = tmp_path / RUN_ID
    folder.mkdir()
    report = dict(run_id=RUN_ID, control_version=1, selection_version=1, selected_modules=['rewards'],
                  rewards_final=False, game_day='2026-11-26', started_at=1, outcome='running',
                  daily_complete=False, supported_chain_complete=False, steps=[dict(name='rewards', status='not_run')])
    path = folder / 'report.json'
    with chain._control_lock(folder):
        chain._atomic_json(path, chain._report_projection(report))
    opened, trying, published, release = (threading.Event() for _ in range(4))
    original = chain._local_bytes
    def slow_read(p, limit):
        if p == path:
            with p.open('rb'):
                opened.set()
                assert release.wait(10)
        return original(p, limit)
    def writer():
        trying.set()
        with chain._control_lock(folder):
            report['steps'][0].update(status='running', started_at=2)
            chain._atomic_json(path, chain._report_projection(report))
        published.set()
    monkeypatch.setattr(chain, '_local_bytes', slow_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshot = pool.submit(chain.inspect_run, RUN_ID)
        try:
            assert opened.wait(10)
            update = pool.submit(writer)
            assert trying.wait(10)
            assert not published.wait(.05)
        finally:
            release.set()
        assert snapshot.result(timeout=10)['steps'][0]['status'] == 'not_run'
        update.result(timeout=10)
    assert published.is_set() and chain.inspect_run(RUN_ID)['steps'][0]['status'] == 'running'
