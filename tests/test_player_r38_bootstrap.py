"""First-upgrade bootstrap uses synthetic bytes and never executes a player."""
import importlib.util
import json
from pathlib import Path

import pytest

from test_player_online_update import encode, manifest, online_case, packet, sha

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('r38_bootstrap', ROOT / 'scripts/bootstrap-r38-update.py')
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


@pytest.fixture
def bootstrap_case(online_case):
    case = online_case
    old_bytes = encode(manifest(case['old'], 38, 'r38'))
    target_bytes = encode(manifest(case['new'], 39, 'v0.1.0'))
    (case['player'] / 'package-manifest.json').write_bytes(old_bytes)
    archive = case['tmp_path'] / 'synthetic-r38-r39.zip'
    archive.write_bytes(packet(case['new'], target_bytes))
    metadata = {'schema_version': 1, 'product': bootstrap.PRODUCT,
        'base_revision': 38, 'base_manifest_sha256': sha(old_bytes), 'target_revision': 39,
        'target_manifest_sha256': sha(target_bytes), 'archive': {'name': archive.name,
            'size': archive.stat().st_size, 'sha256': sha(archive.read_bytes())}}
    return case, archive, metadata


def test_r38_bootstrap_copies_owned_old_python_and_maintainer_externally(bootstrap_case):
    case, archive, metadata = bootstrap_case
    result = bootstrap.prepare_bootstrap(case['player'], archive, metadata, temp_root=case['tmp_path'])
    command = result['command']
    assert command[0] != str(case['player'] / 'runtime/python/python.exe')
    assert Path(command[0]).read_bytes() == case['old']['runtime/python/python.exe']
    assert command[1:3] == ['-X', 'utf8'] and command[3] == '-B'
    assert command[5:7] == ['worker', '--player']
    assert command[7] == str(case['player'])
    assert not Path(result['workspace']).is_relative_to(case['player'])
    assert not list(Path(result['workspace']).rglob('local-secret.txt'))
    assert not list(Path(result['workspace']).rglob('unknown.json'))
    receipt = json.loads((case['player'] / '.runtime/player-maintenance/external-updater.json').read_text(encoding='utf-8'))
    assert receipt['base_revision'] == 38
    assert receipt['base_manifest_sha256'] == metadata['base_manifest_sha256']
    assert (case['player'] / '.runtime/player-maintenance/recover-update.bat').is_file()
    assert (case['player'] / '.runtime/player-maintenance/recover-update.ps1').is_file()
    assert '--parent-pid' in command and '--archive-sha256' in command
    for name, data in case['private'].items():
        assert (case['player'] / name).read_bytes() == data


def test_bootstrap_refuses_to_replace_existing_pending_recovery_root(bootstrap_case):
    case, archive, metadata = bootstrap_case
    marker = case['player'] / '.runtime/player-maintenance/update-in-progress.json'
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b'keep original intent')
    with pytest.raises(ValueError, match='previous_update_incomplete'):
        bootstrap.prepare_bootstrap(case['player'], archive, metadata, temp_root=case['tmp_path'])
    assert marker.read_bytes() == b'keep original intent'


@pytest.mark.parametrize('fault', ['base_hash', 'archive_hash', 'archive_size', 'target_hash',
    'base_revision', 'schema', 'owned_python', 'owned_maintainer'])
def test_bootstrap_does_not_execute_mismatched_or_unowned_runtime(bootstrap_case, fault):
    case, archive, metadata = bootstrap_case
    if fault == 'base_hash': metadata['base_manifest_sha256'] = 'f' * 64
    if fault == 'archive_hash': metadata['archive']['sha256'] = 'f' * 64
    if fault == 'archive_size': metadata['archive']['size'] += 1
    if fault == 'target_hash': metadata['target_manifest_sha256'] = 'f' * 64
    if fault == 'base_revision': metadata['base_revision'] = 37
    if fault == 'schema': metadata['schema_version'] = True
    if fault == 'owned_python': (case['player'] / 'runtime/python/python.exe').write_bytes(b'changed')
    if fault == 'owned_maintainer': (case['player'] / 'src/pcr_maa/player_maintenance.py').write_bytes(b'changed')
    with pytest.raises(ValueError):
        bootstrap.prepare_bootstrap(case['player'], archive, metadata, temp_root=case['tmp_path'])
    assert not (case['tmp_path'] / 'Maa-PCR-update').exists()
    for name, data in case['private'].items():
        assert (case['player'] / name).read_bytes() == data
