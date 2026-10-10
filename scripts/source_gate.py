"""Verify public source bytes against the owned source of a staged player."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('delivery',
    Path(__file__).with_name('source_delivery.py'))
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def gate(prepared, inventory_sha256, player):
    prepared, player = Path(prepared), Path(player)
    inventory = delivery.verify(prepared, inventory_sha256)
    source = prepared / 'source'
    player_manifest = player / 'package-manifest.json'
    manifest = delivery.json_read(player_manifest)
    player_rows = delivery.row_index(manifest['files'])
    source_rows = delivery.row_index(inventory['files'])
    selected = {name: row for name, row in player_rows.items()
        if name.startswith(('src/', 'scripts/', 'assets/', 'vendor/', 'third_party/', 'licenses/', 'docs/'))
        or name in delivery.ROOT_FILES}
    delivery.require(selected, 'owned_program_source_empty')
    for name, row in selected.items():
        delivery.checked_row(player, row)
        delivery.require(name in source_rows and all(source_rows[name][key] == row[key]
            for key in ('size', 'sha256')), 'owned_source_mismatch: ' + name)
        delivery.checked_row(source, row)
    build_input_row = player_rows.get('build-source-manifest.json')
    build_input_count = 0
    if 'MFAAvalonia.dll' in player_rows:
        delivery.require(build_input_row is not None, 'gui_build_input_binding_missing')
    if build_input_row is not None:
        build_inputs = delivery.json_read(delivery.checked_row(player, build_input_row))
        delivery.require(type(build_inputs.get('schema_version')) is int
                         and build_inputs['schema_version'] == 1, 'gui_build_input_schema')
        build_rows = delivery.row_index(build_inputs['files'])
        delivery.require(build_rows and all(name.startswith('gui/') for name in build_rows),
                         'gui_build_input_scope')
        delivery.require({name for name in source_rows if name.startswith('gui/')} == set(build_rows),
                         'gui_build_input_inventory_mismatch')
        for name, row in build_rows.items():
            delivery.require(all(source_rows[name][key] == row[key] for key in ('size', 'sha256')),
                             'gui_build_input_source_mismatch: ' + name)
            delivery.checked_row(source, row)
        build_input_count = len(build_rows)
        snapshot = source / 'delivery/build-source-manifest.json'
        if snapshot.is_file():
            delivery.require(snapshot.read_bytes() == (player / 'build-source-manifest.json').read_bytes(),
                             'gui_build_input_snapshot_changed')
    revision = manifest['update']['revision']
    if 'revision' in inventory:
        delivery.require(inventory['revision'] == revision, 'source_revision_mismatch')
    snapshot = source / 'delivery/runtime-package-manifest.json'
    snapshot_present = snapshot.is_file()
    if snapshot_present:
        delivery.require(snapshot.read_bytes() == player_manifest.read_bytes(), 'sealed_manifest_snapshot_changed')
    return {'schema_version': 1, 'passed': True,
        'verified_at': datetime.now(timezone.utc).isoformat(),
        'revision': revision, 'source_inventory_sha256': inventory_sha256,
        'source_payload_count': inventory['file_count'], 'source_payload_bytes': inventory['payload_size'],
        'staged_player_manifest_sha256': delivery.digest(player_manifest),
        'owned_program_source_alignment_count': len(selected),
        'gui_build_input_source_alignment_count': build_input_count,
        'sealed_runtime_manifest_snapshot_present_and_matching': snapshot_present,
        'source_privacy_and_extra_file_checks_passed': True,
        'no_archive_or_publication_performed': True,
        'scope': 'owned source bytes, exact public inventory and optional sealed runtime manifest',
        'deterministic_dependency_rebuild_claimed': False, 'formal_legal_opinion': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--inventory-sha256', required=True)
    parser.add_argument('--player', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = gate(args.prepared, args.inventory_sha256, args.player)
    delivery.require(not args.output.exists(), 'gate_output_exists')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
