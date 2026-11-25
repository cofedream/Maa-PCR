"""Export accepted release assets; tag existence is never acceptance evidence."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from assemble_release import seal, require_acceptance, owned_payload_sha256
from build_hotfix_repair import build as build_hotfix_repair
from build_release import ROOT, package_outputs, save, digest
from release_baselines import check_release_baseline, require_patch_base
from source_delivery import prepare, pack, refresh_player_snapshot
from source_gate import gate


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--player', type=Path, required=True)
    parser.add_argument('--acceptance', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tag', required=True, help='release tag bound by the reviewed acceptance record')
    baseline = parser.add_mutually_exclusive_group()
    baseline.add_argument('--base', type=Path)
    baseline.add_argument('--base-manifest', type=Path)
    baseline.add_argument('--release-baseline', action='store_true')
    args = parser.parse_args()
    manifest_path = args.player / 'package-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    revision = manifest['update']['revision']
    acceptance = json.loads(args.acceptance.read_text(encoding='utf-8'))
    require_acceptance(acceptance, revision, owned_payload_sha256(manifest), version=args.tag)
    require_acceptance(acceptance, revision, owned_payload_sha256(manifest),
                       version=manifest['update']['version'])
    if args.release_baseline:
        args.base_manifest = check_release_baseline(revision=revision, version=args.tag)
    baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
    require_patch_base(revision, baseline_path)
    args.output.mkdir(parents=True, exist_ok=False)
    prepared = args.output / 'prepared-source'
    prepare(ROOT, prepared, revision=revision, player_manifest=manifest_path)
    source_alignment = gate(prepared, digest(prepared / 'SOURCE_INVENTORY.json'), args.player)
    result = seal(args.player, [args.acceptance], tag=args.tag)
    refresh_player_snapshot(prepared, digest(prepared / 'SOURCE_INVENTORY.json'), manifest_path)
    inventory_sha = digest(prepared / 'SOURCE_INVENTORY.json')
    source_alignment = gate(prepared, inventory_sha, args.player)
    save(args.output / 'SOURCE_ALIGNMENT.json', source_alignment)
    package_outputs(args.player, args.output, args.base, args.base_manifest)
    version = manifest['update']['version']
    build_hotfix_repair(args.player, args.output / ('Maa-PCR-' + version + '-update-repair.zip'))
    pack(prepared, args.output / ('Maa-PCR-' + version + '-source.zip'), inventory_sha)
    save(args.output / 'RELEASE_RECEIPT.json', dict(result, source_inventory_sha256=inventory_sha))
    checksums = '\n'.join(digest(path) + '  ' + path.name for path in sorted(args.output.iterdir()) if path.is_file()) + '\n'
    (args.output / 'SHA256SUMS.txt').write_text(checksums, encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
