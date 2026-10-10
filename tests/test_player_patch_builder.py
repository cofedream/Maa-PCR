"""Synthetic manifest-bound patches; no player executable or private data."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest
from pcr_maa import player_maintenance as maintenance


@pytest.fixture
def builder():
    path = Path(__file__).resolve().parents[1] / 'scripts/build_pcr_player_patch.py'
    spec = importlib.util.spec_from_file_location('pcr_player_patch_builder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest(payload, revision):
    return {'layout': 'portable-offline-validation',
        'personal_settings_or_ledgers_included': False,
        'update': {'schema_version': 1, 'product': maintenance.PRODUCT,
                   'revision': revision, 'version': 'r' + str(revision) + '-synthetic'},
        'files': [dict(path=name, size=len(data), sha256=hashlib.sha256(data).hexdigest())
                  for name, data in sorted(payload.items())]}


def write_player(root, payload, metadata):
    root.mkdir()
    for name, data in payload.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    save_manifest(root, metadata)


def save_manifest(root, metadata):
    (root / maintenance.MANIFEST).write_bytes(
        (json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


@pytest.fixture
def case(tmp_path):
    base, target = tmp_path / 'base 中文 space', tmp_path / 'target 中文 space'
    old = {'MFAAvalonia.exe': b'synthetic never execute',
        'MFAAvalonia.Core.dll': b'synthetic old core', 'pcr-repository.txt': b'.',
        'scripts/player-maintenance.py': b'# synthetic launcher',
        'src/pcr_maa/player_maintenance.py': b'# synthetic tool',
        'assets/obsolete.txt': b'synthetic obsolete'}
    new = {**old, 'MFAAvalonia.Core.dll': b'synthetic new core',
           'assets/中文 new.txt': b'synthetic addition'}
    del new['assets/obsolete.txt']
    write_player(base, old, manifest(old, 22))
    write_player(target, new, manifest(new, 23))
    return base, target, tmp_path / '补丁 中文 space.zip', old, new


def test_delta_contains_only_changed_added_and_exact_metadata(builder, case):
    base, target, output, old, new = case
    before = {root: {name: (root / name).read_bytes() for name in payload}
              for root, payload in ((base, old), (target, new))}
    result = builder.build_patch(base, target, output)
    assert result['changed_payload_count'] == 2 and result['removed_program_files'] == 1
    assert result['files'] == 4 and result['gui_started'] is False
    assert result['sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()
    with zipfile.ZipFile(output) as packet:
        assert set(packet.namelist()) == {'MFAAvalonia.Core.dll', 'assets/中文 new.txt',
                                         maintenance.MANIFEST, maintenance.PATCH}
        assert packet.read(maintenance.MANIFEST) == (target / maintenance.MANIFEST).read_bytes()
        patch = json.loads(packet.read(maintenance.PATCH))
        assert set(patch) == {'schema_version', 'product', 'layout', 'base_revision',
                             'base_manifest_sha256', 'target_revision', 'target_manifest_sha256'}
        assert patch['base_manifest_sha256'] == hashlib.sha256(
            (base / maintenance.MANIFEST).read_bytes()).hexdigest()
        assert patch['target_manifest_sha256'] == hashlib.sha256(
            (target / maintenance.MANIFEST).read_bytes()).hexdigest()
    for root, files in before.items():
        assert all((root / name).read_bytes() == data for name, data in files.items())
    assert not list(output.parent.glob(output.name + '.partial-*'))


def test_generated_patch_is_accepted_by_existing_update_transaction(builder, case):
    base, target, output, _, new = case
    builder.build_patch(base, target, output)
    result = maintenance.apply_update(base, output, process_probe=lambda _: [])
    assert result['status'] == 'updated' and result['revision'] == 23
    assert all((base / name).read_bytes() == data for name, data in new.items())
    assert not (base / 'assets/obsolete.txt').exists()
    assert (base / maintenance.MANIFEST).read_bytes() == (target / maintenance.MANIFEST).read_bytes()


@pytest.mark.parametrize('fault', ['base_hash', 'target_hash', 'revision', 'product',
    'layout', 'binding', 'private', 'unsafe', 'case_collision', 'missing'])
def test_invalid_inputs_never_publish_patch(builder, case, fault):
    base, target, output, _, _ = case
    metadata = json.loads((target / maintenance.MANIFEST).read_bytes())
    if fault in ('base_hash', 'target_hash'):
        root = base if fault == 'base_hash' else target
        (root / 'MFAAvalonia.Core.dll').write_bytes(b'externally changed synthetic input')
    elif fault == 'revision': metadata['update']['revision'] = 22
    elif fault == 'product': metadata['update']['product'] = 'other-product'
    elif fault == 'layout': metadata['layout'] = 'bound-checkout'
    elif fault == 'binding':
        (target / 'pcr-repository.txt').write_bytes(b'other')
        row = next(row for row in metadata['files'] if row['path'] == 'pcr-repository.txt')
        row.update(size=5, sha256=hashlib.sha256(b'other').hexdigest())
    elif fault in ('private', 'unsafe', 'case_collision'):
        name = {'private': '.runtime/ledger/fake.json', 'unsafe': '../outside.txt',
                'case_collision': 'mfaavalonia.CORE.dll'}[fault]
        metadata['files'].append(dict(path=name, size=0, sha256=hashlib.sha256(b'').hexdigest()))
    elif fault == 'missing': (target / 'assets/中文 new.txt').unlink()
    save_manifest(target, metadata)
    with pytest.raises((ValueError, OSError)):
        builder.build_patch(base, target, output)
    assert not output.exists()


def test_existing_output_is_never_overwritten(builder, case):
    base, target, output, _, _ = case
    output.write_bytes(b'prior synthetic output')
    with pytest.raises(ValueError, match='output_exists'):
        builder.build_patch(base, target, output)
    assert output.read_bytes() == b'prior synthetic output'


def test_racing_output_is_never_overwritten_and_partial_is_preserved(builder, case, monkeypatch):
    base, target, output, _, _ = case
    def occupied(source, destination):
        output.write_bytes(b'racing synthetic output')
        raise FileExistsError('synthetic race')
    monkeypatch.setattr(builder.os, 'link', occupied)
    with pytest.raises(ValueError, match='output_exists'):
        builder.build_patch(base, target, output)
    assert output.read_bytes() == b'racing synthetic output'
    assert len(list(output.parent.glob(output.name + '.partial-*'))) == 1


@pytest.mark.parametrize('changed_input', ['base_payload', 'target_manifest'])
def test_input_changes_during_build_keep_failed_partial(builder, case, monkeypatch, changed_input):
    base, target, output, _, _ = case
    write = builder.zipfile.ZipFile.write
    changed = False
    def change_once(packet, filename, arcname=None, *args, **kwargs):
        nonlocal changed
        result = write(packet, filename, arcname, *args, **kwargs)
        if not changed:
            changed = True
            path = base / 'MFAAvalonia.exe' if changed_input == 'base_payload' else target / maintenance.MANIFEST
            path.write_bytes(b'external synthetic change during construction')
        return result
    monkeypatch.setattr(builder.zipfile.ZipFile, 'write', change_once)
    with pytest.raises(ValueError):
        builder.build_patch(base, target, output)
    assert not output.exists()
    assert len(list(output.parent.glob(output.name + '.partial-*'))) == 1


def test_output_cannot_be_inside_input_player(builder, case):
    base, target, _, _, _ = case
    with pytest.raises(ValueError, match='output_inside_player'):
        builder.build_patch(base, target, target / 'synthetic.zip')


def test_cli_uses_same_builder_and_reports_json(builder, case, capsys):
    base, target, output, _, _ = case
    assert builder.main(['--base', str(base), '--target', str(target), '--output', str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'local_player_patch_archive' and result['target_revision'] == 23
