"""Record current corresponding source, dependency locks and original licenses."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from source_delivery import checked_row, digest, json_read, row_index, verify


def generate(player_root, prepared_root, output):
    player_root, prepared_root, output = map(Path, (player_root, prepared_root, output))
    inventory_path = prepared_root / 'SOURCE_INVENTORY.json'
    inventory = verify(prepared_root, digest(inventory_path))
    player = json_read(player_root / 'package-manifest.json')
    player_rows, source_rows = row_index(player['files']), row_index(inventory['files'])
    matched = []
    for name, row in player_rows.items():
        if name.startswith(('src/', 'scripts/', 'assets/', 'vendor/', 'third_party/')) or name == 'interface.json':
            if name not in source_rows or any(source_rows[name][key] != row[key] for key in ('size', 'sha256')):
                raise ValueError('player_source_not_matching: ' + name)
            checked_row(player_root, row)
            checked_row(prepared_root / 'source', row)
            matched.append(name)
    records = [row for name, row in source_rows.items() if name.endswith(('LICENSE', 'LICENSE.txt', 'COPYING'))
               or name == 'THIRD_PARTY_NOTICES.md']
    result = {'schema_version': 1, 'revision': player['update']['revision'],
        'version': player['update']['version'], 'source_inventory_sha256': digest(inventory_path),
        'player_manifest_sha256': digest(player_root / 'package-manifest.json'),
        'owned_source_alignment_count': len(matched), 'original_license_records': records,
        'runtime_dependencies': player.get('build_dependencies', {}),
        'mixed_component_licenses_apply': True, 'historical_binary_reproduction_claimed': False}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('player', 'prepared', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.player, args.prepared, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
