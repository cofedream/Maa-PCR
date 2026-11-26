import os

import pytest

from pcr_maa import daily_legacy_control as legacy
from pcr_maa import supported_daily as daily


def historical_run(directory, *, running=False, padding=0, cooperative=True):
    directory.mkdir()
    rows = []
    for index, name in enumerate(('event', 'deep'), 1):
        if running and name == 'deep':
            rows.append(dict(name=name, status='running', started_at=3))
            continue
        path = directory / f'{index}-{name}.json'
        result = dict(outcome='executed', settlement_verified=True, consumption_sent=True, home_verified=True)
        if padding and index == 1:
            result['native_evidence'] = 'x' * padding
        row = dict(name=name, status='executed', started_at=index, finished_at=index + 1, result=result,
                   receipt=str(path))
        daily._atomic_json(path, row)
        rows.append(row)
    document = dict(run_id=directory.name, game_day='2026-11-26', daily_complete=False,
                    supported_chain_complete=not running, started_at=1,
                    outcome='running' if running else 'complete', steps=rows,
                    selection_version=1, selected_modules=['event', 'deep'], rewards_final=False)
    if not running:
        document['finished_at'] = 3
    if cooperative:
        document['control_version'] = 1
        with daily._control_lock(directory):
            pass
    daily._atomic_json(directory / 'report.json', document)
    return document


def test_large_v027_single_receipt_query_and_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, 'CONTROL_ROOT', tmp_path)
    run_id = 'run-' + 'd' * 32
    directory = tmp_path / run_id
    historical_run(directory, running=True, padding=daily.REPORT_LIMIT + 1)
    assert (directory / '1-event.json').stat().st_size > daily.REPORT_LIMIT
    result = daily.inspect_run(run_id)
    assert result['reported_outcome'] == 'running'
    assert result['steps'][0]['settlement_verified'] is True
    assert result['steps'][1]['unsettled_or_unknown'] is True
    assert daily.request_stop(run_id)['outcome'] == 'stop_requested'
    assert daily.inspect_run(run_id)['stop_state'] == 'requested_not_acknowledged'
    assert not (directory / 'control-final.json').exists()


@pytest.mark.parametrize('cooperative', [False, True])
def test_final_and_pre_control_reports_stay_read_only(tmp_path, cooperative):
    directory = tmp_path / ('run-' + 'c' * 32)
    report = historical_run(directory, cooperative=cooperative)
    before = sorted(p.name for p in directory.iterdir())
    expected = daily._report_projection(daily._validate_report(directory, report))
    assert legacy.read_legacy_control(directory) == expected
    assert ('control_version' in expected) == cooperative
    assert sorted(p.name for p in directory.iterdir()) == before


@pytest.mark.parametrize('damage', ['receipt', 'missing', 'false_settlement', 'duplicate_key', 'wrong_target'])
def test_large_reader_rejects_broken_original_evidence(tmp_path, damage):
    directory = tmp_path / ('run-' + 'a' * 32)
    report = historical_run(directory)
    path = directory / '1-event.json'
    if damage == 'receipt':
        path.write_bytes(b'{"name":"event"}')
    elif damage == 'missing':
        path.rename(directory / 'unreferenced.json')
    elif damage == 'false_settlement':
        report['steps'][0]['result']['settlement_verified'] = False
        daily._atomic_json(path, report['steps'][0])
        daily._atomic_json(directory / 'report.json', report)
    elif damage == 'wrong_target':
        report['steps'][0]['name'] = '../same-name'
        daily._atomic_json(directory / 'report.json', report)
    else:
        target = directory / 'report.json'
        original = target.read_bytes()
        target.write_bytes(original.replace(b'"outcome": "complete"', b'"outcome": "complete", "outcome": "complete"'))
    with pytest.raises(ValueError, match='invalid_report|control_file_missing'):
        legacy.read_legacy_control(directory)


def test_reader_does_not_block_old_worker_replacing_running_snapshot(tmp_path, monkeypatch):
    directory = tmp_path / ('run-' + 'b' * 32)
    historical_run(directory, running=True)
    final_directory = tmp_path / 'completed'
    final = historical_run(final_directory)
    final['run_id'] = directory.name
    for index, row in enumerate(final['steps'], 1):
        row['receipt'] = str(directory / f"{index}-{row['name']}.json")
        daily._atomic_json(directory / f"{index}-{row['name']}.json", row)
    replacement = directory / 'next.tmp'
    daily._atomic_json(replacement, final)
    old_check = legacy._check_receipt

    def publish_next_snapshot(*args):
        os.replace(replacement, directory / 'report.json')
        return old_check(*args)

    with monkeypatch.context() as patch:
        patch.setattr(legacy, '_check_receipt', publish_next_snapshot)
        with pytest.raises(ValueError, match='control_snapshot_changed'):
            legacy.read_legacy_control(directory)
    assert legacy.read_legacy_control(directory)['outcome'] == 'complete'


def test_legacy_metadata_and_single_proof_limits(tmp_path, monkeypatch):
    directory = tmp_path / ('run-' + 'e' * 32)
    historical_run(directory, padding=1024)
    with monkeypatch.context() as patch:
        patch.setattr(legacy, 'STEP_LIMIT', 512)
        with pytest.raises(ValueError, match='control_file_too_large'):
            legacy.read_legacy_control(directory)
    with monkeypatch.context() as patch:
        patch.setattr(legacy, 'METADATA_LIMIT', 32)
        with pytest.raises(ValueError, match='control_file_too_large'):
            legacy.read_legacy_control(directory)
