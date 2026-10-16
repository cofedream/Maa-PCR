"""Use a trusted, exact-bound offline bridge; never start GUI or game tasks."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

TOOLS = {'src/pcr_maa/__init__.py', 'src/pcr_maa/player_maintenance.py',
         'src/pcr_maa/player_update.py', 'convert-r54-to-r52.py'}


def load_bundle(directory):
    directory = Path(directory).absolute()
    metadata = directory / 'bridge-manifest.json'
    if metadata.stat().st_size > 1024 * 1024:
        raise ValueError('bridge_metadata_too_large')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result

    value = json.loads(metadata.read_bytes(), object_pairs_hook=unique)
    if (type(value) is not dict or set(value) != {'schema_version', 'product',
            'base_manifest_sha256', 'target_manifest_sha256', 'archive_sha256', 'tools'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['product'] != 'maa-pcr-cn-windows-x64'
            or type(value['tools']) is not dict or set(value['tools']) != TOOLS
            or any(type(sha) is not str or re.fullmatch('[0-9a-f]{64}', sha) is None
                   for sha in [value['base_manifest_sha256'], value['target_manifest_sha256'],
                               value['archive_sha256'], *value['tools'].values()])):
        raise ValueError('invalid_bridge_metadata')
    for name, expected in value['tools'].items():
        path = directory / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('bridge_tool_hash_mismatch')
    sys.path.insert(0, str(directory / 'src'))
    from pcr_maa import player_maintenance as maintenance, player_update as updater
    maintenance._root(directory)
    maintenance._plain(metadata)
    for name in TOOLS:
        maintenance._path(directory, name)
    maintenance._require(all(Path(module.__file__).resolve() == directory / name for module, name in (
        (maintenance, 'src/pcr_maa/player_maintenance.py'),
        (updater, 'src/pcr_maa/player_update.py'))), 'bridge_module_path_mismatch')
    maintenance._require(value['base_manifest_sha256'] == maintenance.R54_TEST_MANIFEST_SHA256,
        'r54_release_manifest_mismatch')
    return directory, value, maintenance, updater


def convert(directory, player, *, recover=False):
    directory, binding, m, updater = load_bundle(directory)
    player = m._root(player)
    m._require(not directory.is_relative_to(player), 'bridge_requires_external_directory')
    pair = (binding['base_manifest_sha256'], binding['target_manifest_sha256'])
    with updater._worker_lock(player):
        if recover:
            result = m.recover_update(player, r54_release_transition=pair)
        else:
            packet = m._path(directory, 'update.zip')
            m._require(m._plain(packet).st_size <= m.MAX_ARCHIVE
                and m._sha(packet) == binding['archive_sha256'], 'bridge_archive_hash_mismatch')
            result = m.apply_update(player, packet, r54_release_transition=pair)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--player', type=Path, required=True)
    parser.add_argument('--recover', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = convert(Path(__file__).resolve().parent, args.player, recover=args.recover)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile) as error:
        code = str(error) if re.fullmatch('[a-z][a-z0-9_]{2,127}', str(error)) else type(error).__name__
        print(json.dumps({'status': 'failed', 'error': code}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
