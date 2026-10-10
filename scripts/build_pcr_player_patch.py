"""Build a local manifest-bound player delta; never run the player or game."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'src'))

from pcr_maa import player_maintenance as maintenance


def build_patch(base, target, output):
    base, target = maintenance._root(base), maintenance._root(target)
    output = Path(os.path.abspath(output))
    maintenance._root(output.parent)
    maintenance._require(not output.exists() and not output.is_symlink(), 'output_exists')
    maintenance._require(not output.is_relative_to(base) and not output.is_relative_to(target),
                         'output_inside_player')
    manifests, raw, rows, versions = {}, {}, {}, {}
    for name, root in (('base', base), ('target', target)):
        path = maintenance._path(root, maintenance.MANIFEST)
        maintenance._require(maintenance._plain(path).st_size <= maintenance.MAX_JSON,
                             'json_too_large')
        raw[name] = path.read_bytes()
        manifests[name] = maintenance._json(raw[name])
        rows[name] = maintenance._rows(manifests[name])
        versions[name] = maintenance._version(manifests[name])
        maintenance._require({'MFAAvalonia.exe', 'pcr-repository.txt',
            'scripts/player-maintenance.py', 'src/pcr_maa/player_maintenance.py'} <= set(rows[name]),
            'update_payload_incomplete')
    maintenance._require(versions['target']['revision'] > versions['base']['revision'],
                         'update_not_newer')
    maintenance._require(manifests['target']['layout'] == manifests['base']['layout'],
                         'update_layout_mismatch')
    maintenance._require(maintenance._path(base, 'pcr-repository.txt').read_bytes()
        == maintenance._path(target, 'pcr-repository.txt').read_bytes(), 'repository_binding_changed')
    changed = sorted(name for name in rows['target'] if rows['base'].get(name) != rows['target'][name])
    removed = sorted(set(rows['base']).difference(rows['target']))
    patch = {'schema_version': 1, 'product': maintenance.PRODUCT,
        'layout': manifests['base']['layout'], 'base_revision': versions['base']['revision'],
        'base_manifest_sha256': hashlib.sha256(raw['base']).hexdigest(),
        'target_revision': versions['target']['revision'],
        'target_manifest_sha256': hashlib.sha256(raw['target']).hexdigest()}
    maintenance._patch(patch, manifests['base'], raw['base'], manifests['target'], raw['target'])
    patch_bytes = (json.dumps(patch, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')

    def verify_inputs():
        for name, root in (('base', base), ('target', target)):
            maintenance._require(maintenance._path(root, maintenance.MANIFEST).read_bytes() == raw[name],
                                 'input_manifest_changed')
            maintenance._verify(root, rows[name])

    def verify_archive(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            expected = set(changed) | {maintenance.MANIFEST, maintenance.PATCH}
            maintenance._require(len(names) == len(expected) and set(names) == expected,
                                 'archive_inventory_mismatch')
            maintenance._require(archive.read(maintenance.MANIFEST) == raw['target']
                and archive.read(maintenance.PATCH) == patch_bytes, 'archive_manifest_mismatch')
            for name in changed:
                row = rows['target'][name]
                with archive.open(name) as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                maintenance._require(archive.getinfo(name).file_size == row['size']
                    and digest == row['sha256'], 'archive_payload_mismatch')

    verify_inputs()
    temporary = output.with_name(output.name + '.partial-' + uuid4().hex)
    with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in changed:
            archive.write(maintenance._path(target, name), name)
        archive.writestr(maintenance.MANIFEST, raw['target'])
        archive.writestr(maintenance.PATCH, patch_bytes)
    verify_inputs()
    verify_archive(temporary)
    # Claim a new final name atomically; every failed partial remains inspectable.
    try:
        os.link(temporary, output)
    except FileExistsError as error:
        raise ValueError('output_exists') from error
    try:
        verify_archive(output)
        verify_inputs()
    except BaseException:
        if output.exists() and not output.is_symlink() and os.path.samefile(temporary, output):
            output.unlink()
        raise
    temporary.unlink()
    return {'status': 'local_player_patch_archive', 'archive': str(output),
        'sha256': maintenance._sha(output), 'bytes': output.stat().st_size,
        'files': len(changed) + 2, 'changed_payload_count': len(changed),
        'removed_program_files': len(removed), **patch,
        'personal_settings_or_ledgers_included': False, 'gui_started': False}


def build_patch_from_manifest(base_manifest, target, output, expected_base_sha256):
    """A frozen hash-bound baseline needs no historical binaries to create a delta."""
    base_manifest = Path(base_manifest)
    maintenance._plain(base_manifest)
    maintenance._require(base_manifest.stat().st_size <= maintenance.MAX_JSON, 'json_too_large')
    old_bytes = base_manifest.read_bytes()
    maintenance._require(hashlib.sha256(old_bytes).hexdigest() == expected_base_sha256,
                         'baseline_manifest_hash_mismatch')
    old = maintenance._json(old_bytes)
    old_rows = maintenance._rows(old)
    target = maintenance._root(target)
    new_bytes = (target / maintenance.MANIFEST).read_bytes()
    new = maintenance._json(new_bytes)
    new_rows = maintenance._rows(new)
    old_version, new_version = maintenance._version(old), maintenance._version(new)
    required = {'MFAAvalonia.exe', 'pcr-repository.txt', 'scripts/player-maintenance.py',
                'src/pcr_maa/player_maintenance.py'}
    maintenance._require(required <= set(old_rows) and required <= set(new_rows), 'update_payload_incomplete')
    maintenance._require(old['layout'] == new['layout'], 'update_layout_mismatch')
    maintenance._require(new_version['revision'] > old_version['revision'], 'update_not_newer')
    maintenance._require(all(old_rows['pcr-repository.txt'][key] == new_rows['pcr-repository.txt'][key]
                             for key in ('size', 'sha256')), 'repository_binding_changed')
    output = Path(os.path.abspath(output))
    maintenance._root(output.parent)
    maintenance._require(not output.exists() and not output.is_symlink(), 'output_exists')
    maintenance._require(not output.is_relative_to(target), 'output_inside_player')
    changed = sorted(name for name in new_rows if old_rows.get(name) != new_rows[name])
    patch = {'schema_version': 1, 'product': maintenance.PRODUCT, 'layout': old['layout'],
        'base_revision': old_version['revision'], 'base_manifest_sha256': expected_base_sha256,
        'target_revision': new_version['revision'],
        'target_manifest_sha256': hashlib.sha256(new_bytes).hexdigest()}
    maintenance._patch(patch, old, old_bytes, new, new_bytes)
    patch_bytes = (json.dumps(patch, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    def verify():
        maintenance._require(base_manifest.read_bytes() == old_bytes, 'input_manifest_changed')
        maintenance._require((target / maintenance.MANIFEST).read_bytes() == new_bytes, 'input_manifest_changed')
        maintenance._verify(target, new_rows)
    verify()
    temporary = output.with_name(output.name + '.partial-' + uuid4().hex)
    with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in changed:
            archive.write(maintenance._path(target, name), name)
        archive.writestr(maintenance.MANIFEST, new_bytes)
        archive.writestr(maintenance.PATCH, patch_bytes)
    verify()
    with zipfile.ZipFile(temporary) as archive:
        maintenance._require(set(archive.namelist()) == set(changed) | {maintenance.MANIFEST, maintenance.PATCH}
            and archive.testzip() is None, 'archive_inventory_mismatch')
        for name in changed:
            with archive.open(name) as stream:
                sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            maintenance._require(archive.getinfo(name).file_size == new_rows[name]['size']
                                 and sha == new_rows[name]['sha256'], 'archive_payload_mismatch')
    verify()
    try:
        os.link(temporary, output)
    except FileExistsError as error:
        raise ValueError('output_exists') from error
    temporary.unlink()
    return {'status': 'local_player_patch_archive', 'archive': str(output),
        'sha256': maintenance._sha(output), 'bytes': output.stat().st_size,
        'files': len(changed) + 2, 'changed_payload_count': len(changed),
        'removed_program_files': len(set(old_rows).difference(new_rows)), **patch,
        'historical_binary_files_read': 0, 'personal_settings_or_ledgers_included': False, 'gui_started': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_patch(args.base, args.target, args.output)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
