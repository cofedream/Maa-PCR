"""Diagnostic exports are whitelist summaries, never private-state archives."""
import hashlib
import json
from pathlib import Path

import pytest
from pcr_maa import player_maintenance as maintenance


@pytest.fixture
def diagnostic_case(tmp_path):
    player = tmp_path / '玩家 中文 space'
    player.mkdir()
    data = b'.'
    manifest = {'layout': 'portable-offline-validation', 'personal_settings_or_ledgers_included': False,
        'files': [{'path': 'pcr-repository.txt', 'size': 1, 'sha256': hashlib.sha256(data).hexdigest()}],
        'update': {'schema_version': 1, 'product': maintenance.PRODUCT, 'revision': 8,
            'version': 'r8-pilot-candidate'}}
    (player / 'pcr-repository.txt').write_bytes(data)
    (player / maintenance.MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
    run_id = 'run-' + 'a' * 32
    report_path = player / '.runtime/supported-daily-work' / run_id / 'report.json'
    report_path.parent.mkdir(parents=True)
    report = {'run_id': run_id, 'outcome': 'failed', 'selected_modules': ['event', 'hard'],
        'rewards_final': True, 'supported_chain_complete': False, 'daily_complete': False,
        'serial': 'DEVICE-SECRET', 'controller_uuid': 'CONTROLLER-SECRET',
        'ledger': {'state': 'in_flight', 'intent_id': 'INTENT-SECRET'},
        'steps': [{'name': 'event', 'status': 'failed', 'error': 'settlement_unknown: C:/secret/private.png',
            'result': {'consumption_sent': True, 'settlement_verified': False,
                'home_verified': False, 'evidence': {'image': 'SCREENSHOT-SECRET'}}},
            {'name': 'hard', 'status': 'not_run'}]}
    report_path.write_text(json.dumps(report), encoding='utf-8')
    entry_path = player / '.runtime/alpha-agent' / ('entry-' + 'b' * 32 + '.json')
    entry_path.parent.mkdir()
    entry_path.write_text(json.dumps({'report_path': str(report_path), 'outcome': 'failed',
        'summary': 'PRIVATE-SUMMARY', 'error': 'outcome_unknown: DEVICE-SECRET'}), encoding='utf-8')
    private_path = player / '.runtime/schedule-onekey-work/ledger/unknown.json'
    private_path.parent.mkdir(parents=True)
    private_path.write_bytes(b'unknown intent preserved')
    return player, run_id, report_path, entry_path, private_path


def exported(case, **kwargs):
    result = maintenance.export_diagnostics(case[0], **kwargs)
    path = Path(result['path'])
    return result, json.loads(path.read_text(encoding='utf-8')), path.read_text(encoding='utf-8')


def test_latest_summary_includes_version_selection_run_and_safe_errors(diagnostic_case):
    result, document, text = exported(diagnostic_case)
    assert result['status'] == 'diagnostics_exported'
    assert document['player_version'] == 'r8-pilot-candidate'
    assert document['run_id'] == diagnostic_case[1]
    assert document['selected_modules'] == ['event', 'hard'] and document['rewards_final'] is True
    assert document['steps'][0]['error'] == 'settlement_unknown'
    assert document['error'] == 'outcome_unknown'
    assert '不要直接重跑、删账或换日' in document['summary']
    assert document['private_state_included'] is False and document['sent_to_anyone'] is False
    for secret in ('SECRET', 'private.png', 'PRIVATE-SUMMARY', 'ledger', 'serial', 'controller_uuid'):
        assert secret not in text
    assert diagnostic_case[4].read_bytes() == b'unknown intent preserved'


def test_explicit_run_ignores_other_latest_entry(diagnostic_case):
    diagnostic_case[3].write_bytes(b'invalid latest JSON; explicit run must not read it')
    result, document, _ = exported(diagnostic_case, run_id=diagnostic_case[1])
    assert result['run_id'] == diagnostic_case[1] and document['error'] is None


def test_no_runs_exports_version_without_scanning_private_runtime(diagnostic_case):
    diagnostic_case[2].unlink()
    diagnostic_case[3].unlink()
    result, document, text = exported(diagnostic_case)
    assert result['run_id'] is None and document['outcome'] == 'no_run'
    assert document['steps'] == [] and 'unknown intent preserved' not in text


def test_entry_cannot_read_report_outside_its_repository(diagnostic_case, tmp_path):
    outside = tmp_path / 'private-outside.json'
    outside.write_bytes(b'invalid outside data must not be read')
    diagnostic_case[3].write_text(json.dumps({'outcome': 'unknown', 'report_path': str(outside)}), encoding='utf-8')
    _, document, _ = exported(diagnostic_case)
    assert document['run_id'] is None and document['outcome'] == 'unknown'


@pytest.mark.parametrize('run_id', ['../outside', 'run-' + 'a' * 31, 'run-' + 'A' * 32])
def test_bad_run_id_cannot_access_files(diagnostic_case, run_id):
    with pytest.raises(ValueError, match='invalid_run_id'):
        maintenance.export_diagnostics(diagnostic_case[0], run_id=run_id)


def test_existing_output_is_never_overwritten(diagnostic_case, tmp_path):
    output = tmp_path / 'diagnostic.json'
    output.write_bytes(b'preserve existing file')
    with pytest.raises(ValueError, match='diagnostic_output_exists'):
        maintenance.export_diagnostics(diagnostic_case[0], output=output)
    assert output.read_bytes() == b'preserve existing file'


def test_stopped_exception_error_is_reduced_to_a_safe_code(diagnostic_case):
    diagnostic_case[3].write_text(json.dumps({'outcome': 'unknown', 'error':
        'owner_channel_closed_or_invalid: C:/private-target/settings.json 127.0.0.1:5555',
        'combination': {'selected_modules': ['rewards'], 'rewards_final': False}}), encoding='utf-8')
    _, document, text = exported(diagnostic_case)
    assert document['error'] == 'owner_channel_closed_or_invalid'
    assert document['selected_modules'] == ['rewards'] and document['run_id'] is None
    assert 'C:/' not in text and '5555' not in text
