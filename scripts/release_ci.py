"""Export accepted release assets; tag existence is never acceptance evidence."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from assemble_release import seal, require_acceptance, owned_payload_sha256
from build_release import ROOT, R38_MANIFEST_SHA256, R39_MANIFEST_SHA256, R41_MANIFEST_SHA256, R45_MANIFEST_SHA256, R46_MANIFEST_SHA256, R49_MANIFEST_SHA256, R50_MANIFEST_SHA256, R51_MANIFEST_SHA256, R52_MANIFEST_SHA256, R53_MANIFEST_SHA256, R55_MANIFEST_SHA256, R56_MANIFEST_SHA256, R57_MANIFEST_SHA256, R58_MANIFEST_SHA256, R59_MANIFEST_SHA256, package_outputs, save, digest
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
    args = parser.parse_args()
    manifest_path = args.player / 'package-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    revision = manifest['update']['revision']
    acceptance = json.loads(args.acceptance.read_text(encoding='utf-8'))
    require_acceptance(acceptance, revision, owned_payload_sha256(manifest), version=args.tag)
    require_acceptance(acceptance, revision, owned_payload_sha256(manifest),
                       version=manifest['update']['version'])
    if revision == 39:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R38_MANIFEST_SHA256:
            raise ValueError('R39_requires_exact_R38_patch_base')
    if revision == 41:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R39_MANIFEST_SHA256:
            raise ValueError('R41_requires_exact_R39_patch_base')
    if revision == 45:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R41_MANIFEST_SHA256:
            raise ValueError('R45_requires_exact_R41_patch_base')
    if revision == 46:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R45_MANIFEST_SHA256:
            raise ValueError('R46_requires_exact_R45_patch_base')
    if revision == 49:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R46_MANIFEST_SHA256:
            raise ValueError('R49_requires_exact_R46_patch_base')
    if revision == 50:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R49_MANIFEST_SHA256:
            raise ValueError('R50_requires_exact_R49_patch_base')
    if revision == 51:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R50_MANIFEST_SHA256:
            raise ValueError('R51_requires_exact_R50_patch_base')
    if revision == 52:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R51_MANIFEST_SHA256:
            raise ValueError('R52_requires_exact_R51_patch_base')
    if revision == 53:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R52_MANIFEST_SHA256:
            raise ValueError('R53_requires_exact_R52_patch_base')
    if revision == 55:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R53_MANIFEST_SHA256:
            raise ValueError('R55_requires_exact_R53_patch_base')
    if revision == 56:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R55_MANIFEST_SHA256:
            raise ValueError('R56_requires_exact_R55_patch_base')
    if revision == 57:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R56_MANIFEST_SHA256:
            raise ValueError('R57_requires_exact_R56_patch_base')
    if revision == 58:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R57_MANIFEST_SHA256:
            raise ValueError('R58_requires_exact_R57_patch_base')
    if revision == 59:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R58_MANIFEST_SHA256:
            raise ValueError('R59_requires_exact_R58_patch_base')
    if revision == 60:
        baseline_path = args.base / 'package-manifest.json' if args.base else args.base_manifest
        if baseline_path is None or digest(baseline_path) != R59_MANIFEST_SHA256:
            raise ValueError('R60_requires_exact_R59_patch_base')
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
    pack(prepared, args.output / ('Maa-PCR-' + version + '-source.zip'), inventory_sha)
    save(args.output / 'RELEASE_RECEIPT.json', dict(result, source_inventory_sha256=inventory_sha))
    checksums = '\n'.join(digest(path) + '  ' + path.name for path in sorted(args.output.iterdir()) if path.is_file()) + '\n'
    (args.output / 'SHA256SUMS.txt').write_text(checksums, encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
