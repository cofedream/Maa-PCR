"""Seal an accepted portable player and export its manifest-owned files."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from build_release import archive_player, maintenance


def owned_payload_sha256(manifest):
    canonical = json.dumps(maintenance._rows(manifest), sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def require_acceptance(receipt, revision, owned_payload=None, *, version):
    required = ('offline_tests_passed', 'isolated_update_passed', 'gui_verified',
                'clean_build_verified', 'candidate_longest_not_above_baseline')
    ratio = receipt.get('candidate_to_baseline_ratio')
    accepted_payload = receipt.get('accepted_owned_payload_sha256')
    expected_version = receipt.get('expected_version')
    if (receipt.get('schema_version') != 1 or type(receipt.get('schema_version')) is not int
            or receipt.get('revision') != revision or type(receipt.get('revision')) is not int
            or receipt.get('status') != 'passed'
            or type(expected_version) is not str
            or re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', expected_version) is None
            or type(version) is not str or version != expected_version
            or type(receipt.get('live_valid_game_days')) is not int
            or receipt['live_valid_game_days'] < 6
            or type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 < ratio <= .6
            or type(accepted_payload) is not str or re.fullmatch('[0-9a-f]{64}', accepted_payload) is None
            or owned_payload is not None and accepted_payload != owned_payload
            or any(receipt.get(key) is not True for key in required)):
        raise ValueError('release_acceptance_incomplete')


def seal(target, checks, *, tag):
    target = maintenance._root(target)
    path = target / maintenance.MANIFEST
    manifest = maintenance._read_json(path)
    maintenance._verify(target, maintenance._rows(manifest))
    version = maintenance._version(manifest)
    if manifest.get('status') != 'release_staging' or not checks:
        raise ValueError('release_not_staged')
    receipts = []
    for check in checks:
        check = Path(check)
        receipt = maintenance._read_json(check)
        require_acceptance(receipt, version['revision'], owned_payload_sha256(manifest), version=tag)
        require_acceptance(receipt, version['revision'], owned_payload_sha256(manifest),
                           version=version['version'])
        receipts.append({'name': check.name, 'sha256': maintenance._sha(check)})
    manifest.update(status='player_test_release', distribution_ready=True, blockers=[],
                    release_checks=receipts)
    temporary = path.with_name(path.name + '.sealed')
    maintenance._save(temporary, manifest, durable=True)
    maintenance._verify(target, maintenance._rows(manifest))
    temporary.replace(path)
    return {'revision': version['revision'], 'version': version['version'], 'tag': tag,
            'manifest_sha256': maintenance._sha(path),
            'owned_files': len(manifest['files']), 'distribution_ready': True,
            'owned_payload_sha256': owned_payload_sha256(manifest)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest='mode', required=True)
    final = sub.add_parser('seal')
    final.add_argument('--target', type=Path, required=True)
    final.add_argument('--checks', type=Path, nargs='+', required=True)
    final.add_argument('--tag', required=True, help='release tag bound by the reviewed acceptance record')
    export = sub.add_parser('archive')
    export.add_argument('--target', type=Path, required=True)
    export.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == 'seal':
        result = seal(args.target, args.checks, tag=args.tag)
    else:
        manifest = maintenance._read_json(args.target / maintenance.MANIFEST)
        if manifest.get('distribution_ready') is not True:
            raise ValueError('release_not_accepted')
        result = archive_player(args.target, args.output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
