"""Synthetic release trust-boundary checks; never run GUI or game tasks."""
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_release as builder
import build_pcr_player_patch as patcher
from build_pcr_player_candidate import startup_launcher
from assemble_release import require_acceptance, seal, owned_payload_sha256
import release_ci
from test_player_patch_builder import case, save_manifest


def test_startup_launcher_preserves_r38_entry_with_local_runtime():
    content = startup_launcher()
    assert '"%~dp0MFAAvalonia.exe" %*\r\n' in content
    assert 'set "DOTNET_ROOT=%~dp0runtime\\dotnet"\r\n' in content
    assert 'set "DOTNET_ROOT_X64=%~dp0runtime\\dotnet"\r\n' in content
    assert 'cd /d "%~dp0"\r\n' in content
    assert 'exit /b %errorlevel%\r\n' in content


@pytest.fixture
def complete_player_stub(tmp_path, monkeypatch):
    player = tmp_path / 'player'
    player.mkdir()
    metadata = {'python': {'version': '3.13.16'}, 'dotnet': {'version': '10.0.1'}}
    monkeypatch.setattr(builder, 'ROOT', tmp_path / 'source')
    with pytest.raises(ValueError, match='release_payload_missing: ') as missing:
        builder.require_payload(player, metadata)
    for name in str(missing.value).removeprefix('release_payload_missing: ').split(', '):
        path = player / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic payload; never executed')
    return player, metadata


@pytest.mark.parametrize('name', ['read-guide-snapshot.py', 'prepare-clan-guide.py'])
def test_guide_launcher_is_copied_and_required(complete_player_stub, name):
    player, metadata = complete_player_stub
    assert name in builder.PAYLOAD_SCRIPTS
    assert (player / 'scripts' / name).is_file()
    builder.require_payload(player, metadata)


@pytest.mark.parametrize('name', ['read-guide-snapshot.py', 'prepare-clan-guide.py'])
def test_player_payload_rejects_missing_guide_launcher(complete_player_stub, name):
    player, metadata = complete_player_stub
    (player / 'scripts' / name).unlink()
    with pytest.raises(ValueError) as missing:
        builder.require_payload(player, metadata)
    assert str(missing.value) == 'release_payload_missing: scripts/' + name


def test_runtime_copy_excludes_only_the_local_guide_handoff(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    docs = source / 'docs'
    (docs / 'archive').mkdir(parents=True)
    private = docs / 'GUIDE_CHECK.md'
    private.write_bytes(b'local work handoff; preserve the original')
    original = private.read_bytes()
    (docs / 'public.md').write_bytes(b'public guide')
    (docs / 'archive/GUIDE_CHECK.md').write_bytes(b'ordinary nested document')
    monkeypatch.setattr(builder, 'ROOT', source)
    target = tmp_path / 'player/docs'
    copied = builder.copy_tree(docs, target)
    assert set(copied) == {'public.md', 'archive/GUIDE_CHECK.md'}
    assert not (target / 'GUIDE_CHECK.md').exists()
    assert (target / 'public.md').read_bytes() == b'public guide'
    assert private.read_bytes() == original


@pytest.mark.parametrize('name', ['../outside', '/outside', 'A\\B', 'CON.txt', '.runtime/secret'])
def test_dependency_zip_rejects_unsafe_paths(tmp_path, name):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as source:
        info = zipfile.ZipInfo('payload')
        info.filename = name
        source.writestr(info, b'not executed')
    with pytest.raises(ValueError):
        builder.extract(archive, tmp_path / 'unpacked')
    assert not (tmp_path / 'unpacked').exists()


def test_dependency_zip_case_collision_rejected_before_extraction(tmp_path):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as source:
        source.writestr('a.dll', b'one')
        source.writestr('A.dll', b'two')
    with pytest.raises(ValueError, match='unsafe_dependency_archive'):
        builder.extract(archive, tmp_path / 'unpacked')
    assert not (tmp_path / 'unpacked').exists()


def accepted():
    return {'schema_version': 1, 'revision': 39, 'expected_version': 'v0.1.0', 'status': 'passed',
            'live_valid_game_days': 0, 'candidate_to_baseline_ratio': None,
            'accepted_owned_payload_sha256': 'a' * 64,
            'offline_tests_passed': True, 'isolated_update_passed': True, 'gui_verified': True,
            'clean_build_verified': True, 'meaningful_speedup_reviewed': False}


@pytest.mark.parametrize('key,value', [('status', 'pending'), ('revision', 38),
    ('offline_tests_passed', 1), ('isolated_update_passed', False), ('gui_verified', False),
    ('clean_build_verified', False),
    ('expected_version', None), ('expected_version', True), ('expected_version', 'v0.1.1')])
def test_acceptance_cannot_be_inferred_from_build(key, value):
    receipt = accepted()
    receipt[key] = value
    with pytest.raises(ValueError, match='release_acceptance_incomplete'):
        require_acceptance(receipt, 39, version='v0.1.0')


def test_release_acceptance_keeps_unmeasured_performance_unclaimed():
    receipt = accepted()
    before = receipt.copy()
    require_acceptance(receipt, 39, 'a' * 64, version='v0.1.0')
    assert receipt == before
    assert receipt['live_valid_game_days'] == 0
    assert receipt['candidate_to_baseline_ratio'] is None
    assert receipt['meaningful_speedup_reviewed'] is False


def test_sealing_rejects_program_bytes_changed_since_live_acceptance():
    with pytest.raises(ValueError, match='release_acceptance_incomplete'):
        require_acceptance(accepted(), 39, 'b' * 64, version='v0.1.0')
    require_acceptance(accepted(), 39, 'a' * 64, version='v0.1.0')


def test_acceptance_rejects_wrong_release_tag():
    with pytest.raises(ValueError, match='release_acceptance_incomplete'):
        require_acceptance(accepted(), 39, version='v0.1.9')


@pytest.mark.parametrize('manifest_version,tag', [('v0.1.9', 'v0.1.0'), ('v0.1.0', 'v0.1.9')])
def test_seal_rejects_wrong_manifest_version_or_tag_without_modifying_player(case, manifest_version, tag):
    _, target, _, _, _ = case
    metadata = builder.maintenance._read_json(target / builder.maintenance.MANIFEST)
    metadata['status'] = 'release_staging'
    metadata['update']['version'] = manifest_version
    save_manifest(target, metadata)
    receipt = dict(accepted(), revision=metadata['update']['revision'],
                   accepted_owned_payload_sha256=owned_payload_sha256(metadata))
    check = target.parent / 'reviewed-acceptance.json'
    builder.save(check, receipt)
    before = (target / builder.maintenance.MANIFEST).read_bytes()
    with pytest.raises(ValueError, match='release_acceptance_incomplete'):
        seal(target, [check], tag=tag)
    assert (target / builder.maintenance.MANIFEST).read_bytes() == before
    assert not (target / (builder.maintenance.MANIFEST + '.sealed')).exists()


@pytest.mark.parametrize('manifest_version,tag', [('v0.1.9', 'v0.1.0'), ('v0.1.0', 'v0.1.9')])
def test_release_preflight_rejects_version_mismatch_before_output(case, monkeypatch, manifest_version, tag):
    _, target, _, _, _ = case
    metadata = builder.maintenance._read_json(target / builder.maintenance.MANIFEST)
    metadata['update']['version'] = manifest_version
    save_manifest(target, metadata)
    check = target.parent / 'reviewed-acceptance.json'
    builder.save(check, dict(accepted(), revision=metadata['update']['revision'],
                            accepted_owned_payload_sha256=owned_payload_sha256(metadata)))
    output = target.parent / 'release-output'
    monkeypatch.setattr(sys, 'argv', ['release_ci.py', '--player', str(target),
                                    '--acceptance', str(check), '--tag', tag, '--output', str(output)])
    with pytest.raises(ValueError, match='release_acceptance_incomplete'):
        release_ci.main()
    assert not output.exists()


def test_update_index_uses_business_revision_and_exact_runtime_asset(tmp_path):
    manifest = {'update': {'schema_version': 1, 'product': builder.maintenance.PRODUCT,
                          'revision': 39, 'version': 'v0.1.0'}}
    builder.save(tmp_path / 'package-manifest.json', manifest)
    full = tmp_path / 'runtime.zip'
    full.write_bytes(b'synthetic full payload')
    patch = {'base_revision': 38, 'base_manifest_sha256': '1' * 64,
             'name': 'patch.zip', 'size': 5, 'sha256': '2' * 64}
    index = builder.update_index(tmp_path, full, [patch])
    assert index['revision'] == 39 and index['tag'] == 'v0.1.0'
    assert index['target_manifest_sha256'] == builder.digest(tmp_path / 'package-manifest.json')
    assert index['full'] == builder.asset_record(full) and index['patches'] == [patch]


def test_package_receipt_contains_exact_bootstrap_asset_hash(case, monkeypatch):
    base, target, _, _, _ = case
    for player, revision in ((base, 38), (target, 39)):
        metadata = builder.maintenance._read_json(player / builder.maintenance.MANIFEST)
        metadata['update'].update(revision=revision, version='v0.1.0' if revision == 39 else 'r38-synthetic')
        save_manifest(player, metadata)
    baseline = base / builder.maintenance.MANIFEST
    monkeypatch.setattr(builder, 'R38_MANIFEST_SHA256', builder.digest(baseline))
    output = target.parent / 'artifacts'
    output.mkdir()
    result = builder.package_outputs(target, output, base_manifest=baseline)
    assert result['bootstrap'] == builder.asset_record(output / 'Maa-PCR-R38-to-R39-bootstrap.zip')


@pytest.mark.parametrize('trusted', [True, False])
@pytest.mark.parametrize('base_revision,target_revision,base_version,target_version', [
    (39, 41, 'v0.1.0', 'v0.1.2'), (41, 45, 'v0.1.2', 'v0.1.3'),
    (45, 46, 'v0.1.3', 'v0.1.4'), (46, 49, 'v0.1.4', 'v0.1.5'), (49, 50, 'v0.1.5', 'v0.1.6'), (50, 51, 'v0.1.6', 'v0.1.7'), (51, 52, 'v0.1.7', 'v0.1.8'), (52, 53, 'v0.1.8', 'v0.1.9'),
    (53, 55, 'v0.1.9', 'v0.1.10'), (55, 56, 'v0.1.10', 'v0.1.11'),
    (56, 57, 'v0.1.11', 'v0.1.12'),
    (57, 58, 'v0.1.12', 'v0.1.13'),
    (58, 59, 'v0.1.13', 'v0.1.14'),
    (59, 60, 'v0.1.14', 'v0.1.15'),
    (60, 61, 'v0.1.15', 'v0.1.16'),
    (61, 64, 'v0.1.16', 'v0.1.17'),
    (64, 65, 'v0.1.17', 'v0.1.18'),
    (65, 66, 'v0.1.18', 'v0.1.19'),
])
def test_patch_requires_pinned_baseline(case, monkeypatch, trusted, base_revision, target_revision, base_version, target_version):
    base, target, _, _, _ = case
    for player, revision, version in ((base, base_revision, base_version), (target, target_revision, target_version)):
        metadata = builder.maintenance._read_json(player / builder.maintenance.MANIFEST)
        metadata['update'].update(revision=revision, version=version)
        save_manifest(player, metadata)
    baseline = base / builder.maintenance.MANIFEST
    monkeypatch.setattr(builder, f'R{base_revision}_MANIFEST_SHA256', builder.digest(baseline) if trusted else '0' * 64)
    patch_name = f'Maa-PCR-R{base_revision}-to-R{target_revision}.patch.zip'
    output = target.parent / 'artifacts'
    output.mkdir()
    if not trusted:
        before = {player: (player / builder.maintenance.MANIFEST).read_bytes() for player in (base, target)}
        with pytest.raises(ValueError, match='baseline_manifest_hash_mismatch'):
            builder.package_outputs(target, output, base_manifest=baseline)
        assert not (output / patch_name).exists()
        assert all((player / builder.maintenance.MANIFEST).read_bytes() == raw for player, raw in before.items())
        return
    result = builder.package_outputs(target, output, base_manifest=baseline)
    assert 'bootstrap' not in result
    assert result['patches'][0]['base_manifest_sha256'] == builder.digest(baseline)
    assert result['patches'][0]['name'] == patch_name
    builder.maintenance.apply_update(base, output / result['patches'][0]['name'], process_probe=lambda _: [])
    assert (base / builder.maintenance.MANIFEST).read_bytes() == (target / builder.maintenance.MANIFEST).read_bytes()


@pytest.mark.parametrize('provide_wrong_baseline', [False, True])
@pytest.mark.parametrize('revision,version,base_revision', [(41, 'v0.1.2', 39), (45, 'v0.1.3', 41), (46, 'v0.1.4', 45), (49, 'v0.1.5', 46), (50, 'v0.1.6', 49), (51, 'v0.1.7', 50), (52, 'v0.1.8', 51), (53, 'v0.1.9', 52), (55, 'v0.1.10', 53), (56, 'v0.1.11', 55), (57, 'v0.1.12', 56), (58, 'v0.1.13', 57), (59, 'v0.1.14', 58), (60, 'v0.1.15', 59), (61, 'v0.1.16', 60), (64, 'v0.1.17', 61), (65, 'v0.1.18', 64), (66, 'v0.1.19', 65)])
def test_release_requires_baseline_before_sealing(case, monkeypatch, revision, version, base_revision, provide_wrong_baseline):
    base, target, _, _, _ = case
    metadata = builder.maintenance._read_json(target / builder.maintenance.MANIFEST)
    metadata['update'].update(revision=revision, version=version)
    save_manifest(target, metadata)
    check = target.parent / 'reviewed-acceptance.json'
    builder.save(check, dict(accepted(), revision=revision, expected_version=version,
                            accepted_owned_payload_sha256=owned_payload_sha256(metadata)))
    output = target.parent / 'release-output'
    argv = ['release_ci.py', '--player', str(target), '--acceptance', str(check),
            '--tag', version, '--output', str(output)]
    if provide_wrong_baseline:
        argv += ['--base-manifest', str(base / builder.maintenance.MANIFEST)]
    monkeypatch.setattr(sys, 'argv', argv)
    before = (target / builder.maintenance.MANIFEST).read_bytes()
    with pytest.raises(ValueError, match=f'R{revision}_requires_exact_R{base_revision}_patch_base'):
        release_ci.main()
    assert not output.exists()
    assert (target / builder.maintenance.MANIFEST).read_bytes() == before


def test_download_metadata_hash_shape_is_checked_before_network(tmp_path):
    with pytest.raises(ValueError, match='invalid_download_digest'):
        builder.download({'url': 'https://example.invalid/runtime.zip', 'sha256': 'invented'}, tmp_path)


def test_offline_wheelhouse_needs_every_exact_hash(tmp_path):
    wheel = tmp_path / 'package-1.0-py3-none-any.whl'
    wheel.write_bytes(b'not executed fixture')
    lock = tmp_path / 'requirements.lock'
    lock.write_text('package==1.0 --hash=sha256:' + builder.digest(wheel), encoding='utf-8')
    builder.copy_locked_wheels(tmp_path, lock, tmp_path / 'wheels')
    assert (tmp_path / 'wheels' / wheel.name).read_bytes() == wheel.read_bytes()
    wheel.write_bytes(b'changed')
    with pytest.raises(ValueError, match='locked_wheel_missing_or_mismatched'):
        builder.copy_locked_wheels(tmp_path, lock, tmp_path / 'bad-wheels')


def test_frozen_manifest_delta_needs_no_historical_binaries(case):
    base, target, output, old, _ = case
    metadata = base / builder.maintenance.MANIFEST
    expected = builder.digest(metadata)
    for name in old:
        (base / name).unlink()
    result = patcher.build_patch_from_manifest(metadata, target, output, expected)
    assert result['historical_binary_files_read'] == 0
    assert result['base_manifest_sha256'] == expected
    assert result['changed_payload_count'] == 2 and result['removed_program_files'] == 1


def test_frozen_manifest_delta_still_installs_via_original_transaction(case):
    base, target, output, _, new = case
    metadata = base / builder.maintenance.MANIFEST
    patcher.build_patch_from_manifest(metadata, target, output, builder.digest(metadata))
    result = builder.maintenance.apply_update(base, output, process_probe=lambda _: [])
    assert result['status'] == 'updated' and result['revision'] == 23
    assert all((base / name).read_bytes() == data for name, data in new.items())


def test_frozen_manifest_delta_rejects_changed_trusted_baseline(case):
    base, target, output, _, _ = case
    with pytest.raises(ValueError, match='baseline_manifest_hash_mismatch'):
        patcher.build_patch_from_manifest(base / builder.maintenance.MANIFEST, target, output, '0' * 64)
    assert not output.exists()


def test_build_defaults_to_current_r66_identity_without_running_build(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(builder, 'build', lambda args: captured.append(args) or {'status': 'not_executed'})
    assert builder.main(['--output', str(tmp_path / 'unused-output')]) == 0
    assert captured[0].revision == 66 and captured[0].version == 'v0.1.19'
    assert not (tmp_path / 'unused-output').exists()


@pytest.mark.parametrize('revision,version', [(51, 'v0.1.7'), (52, 'v0.1.8'), (53, 'v0.1.9'), (55, 'v0.1.10'), (56, 'v0.1.11'), (57, 'v0.1.12'), (58, 'v0.1.13'), (59, 'v0.1.14'), (60, 'v0.1.15'), (61, 'v0.1.16'), (64, 'v0.1.17'), (65, 'v0.1.18')])
def test_public_manifest_is_exact_pinned_release_baseline(revision, version):
    path = ROOT / f'delivery/baselines/r{revision}-package-manifest.json'
    assert builder.digest(path) == getattr(builder, f'R{revision}_MANIFEST_SHA256')
    manifest = builder.maintenance._read_json(path)
    assert manifest['update']['revision'] == revision
    assert manifest['update']['version'] == version
    assert manifest['distribution_ready'] is True
