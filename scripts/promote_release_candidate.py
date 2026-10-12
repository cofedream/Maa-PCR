"""Promote the exact accepted CI archive; never rebuild, seal or run its binaries."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

sys.dont_write_bytecode = True
from assemble_release import owned_payload_sha256, require_acceptance
from build_release import ROOT, digest, extract, maintenance, save
from source_delivery import prepare
from source_gate import gate

REPOSITORY = 'shiwandehongbai/Maa-PCR'
MAX_METADATA = 2 * 1024 * 1024


def accepted(acceptance, tag, repository):
    receipt = maintenance._read_json(Path(acceptance))
    require_acceptance(receipt, receipt.get('revision'), version=tag)
    binding = receipt.get('accepted_ci_artifact')
    maintenance._require(repository == REPOSITORY and type(binding) is dict
        and set(binding) == {'repository', 'run_id', 'run_attempt', 'artifact_id', 'source_commit', 'runtime_archive'}
        and binding['repository'] == repository, 'invalid_accepted_ci_artifact')
    maintenance._require(all(type(binding[key]) is int and binding[key] > 0
        for key in ('run_id', 'run_attempt', 'artifact_id'))
        and type(binding['source_commit']) is str
        and re.fullmatch('[0-9a-f]{40}', binding['source_commit']) is not None,
        'invalid_accepted_ci_artifact')
    archive = binding['runtime_archive']
    maintenance._require(type(archive) is dict and set(archive) == {'name', 'size', 'sha256'}
        and archive['name'] == 'Maa-PCR-' + receipt['expected_version'] + '-win-x64.zip'
        and type(archive['size']) is int and 0 < archive['size'] <= maintenance.MAX_ARCHIVE
        and type(archive['sha256']) is str and re.fullmatch('[0-9a-f]{64}', archive['sha256']) is not None,
        'invalid_accepted_runtime_archive')
    maintenance._name(archive['name'])
    maintenance._require(receipt['revision'] > 0, 'invalid_accepted_revision')
    return receipt, binding


def request(acceptance, tag, repository, output, github_output):
    receipt, binding = accepted(acceptance, tag, repository)
    output, github_output = Path(output), Path(github_output)
    maintenance._require(not output.exists() and not output.is_symlink(), 'request_output_exists')
    if github_output.exists() or github_output.is_symlink():
        maintenance._plain(github_output)
    maintenance._root(github_output.absolute().parent)
    result = {'schema_version': 1, 'revision': receipt['revision'],
              'version': receipt['expected_version'], 'accepted_ci_artifact': binding}
    save(output, result)
    with github_output.open('a', encoding='utf-8', newline='\n') as stream:
        for key in ('run_id', 'artifact_id', 'source_commit'):
            stream.write(key + '=' + str(binding[key]) + '\n')
    return result


def provenance(binding, run, artifact):
    repository, head_repository = run.get('repository'), run.get('head_repository')
    workflow_run = artifact.get('workflow_run')
    maintenance._require(type(repository) is dict
        and repository.get('full_name') == binding['repository']
        and type(repository.get('id')) is int and repository['id'] > 0
        and type(head_repository) is dict
        and head_repository.get('full_name') == binding['repository']
        and type(head_repository.get('id')) is int and head_repository['id'] == repository['id']
        and type(run.get('id')) is int and run['id'] == binding['run_id']
        and type(run.get('run_attempt')) is int and run['run_attempt'] == binding['run_attempt']
        and run.get('head_sha') == binding['source_commit'] and run.get('event') == 'push'
        and run.get('head_branch') == 'main' and run.get('status') == 'completed'
        and run.get('conclusion') == 'success' and run.get('path') == '.github/workflows/ci.yml',
        'ci_run_provenance_mismatch')
    maintenance._require(type(artifact.get('id')) is int and artifact['id'] == binding['artifact_id']
        and artifact.get('name') == 'windows-candidate-and-tests' and artifact.get('expired') is False
        and type(workflow_run) is dict and type(workflow_run.get('id')) is int
        and workflow_run['id'] == binding['run_id']
        and workflow_run.get('head_sha') == binding['source_commit']
        and workflow_run.get('head_branch') == 'main'
        and type(workflow_run.get('repository_id')) is int
        and workflow_run['repository_id'] == repository['id']
        and type(workflow_run.get('head_repository_id')) is int
        and workflow_run['head_repository_id'] == repository['id'], 'ci_artifact_provenance_mismatch')
    timestamps = (run.get('run_started_at'), artifact.get('created_at'),
                  artifact.get('updated_at'), run.get('updated_at'))
    maintenance._require(all(type(value) is str for value in timestamps), 'ci_artifact_attempt_mismatch')
    started, created, artifact_updated, updated = [datetime.fromisoformat(value) for value in timestamps]
    maintenance._require(all(value.utcoffset() is not None
                            for value in (started, created, artifact_updated, updated))
        and started <= created <= artifact_updated <= updated, 'ci_artifact_attempt_mismatch')
    ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', binding['source_commit'], 'HEAD'],
                              cwd=ROOT, capture_output=True, check=False)
    maintenance._require(ancestor.returncode == 0, 'accepted_source_not_ancestor')
    unchanged = subprocess.run(['git', 'diff', '--quiet', binding['source_commit'], 'HEAD', '--',
                                '.', ':(exclude)delivery/acceptance.json'],
                               cwd=ROOT, capture_output=True, check=False)
    maintenance._require(unchanged.returncode == 0, 'accepted_source_changed')
    dirty = subprocess.run(['git', 'diff', 'HEAD', '--name-only', '--', '.'],
                           cwd=ROOT, capture_output=True, check=False)
    untracked = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard'],
                               cwd=ROOT, capture_output=True, check=False)
    maintenance._require(dirty.returncode == 0 and not dirty.stdout
        and untracked.returncode == 0 and not untracked.stdout, 'accepted_source_dirty')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise ValueError('github_metadata_redirect_refused')


def fetch_metadata(acceptance, tag, repository, output_dir):
    _, binding = accepted(acceptance, tag, repository)
    output_dir = Path(output_dir)
    maintenance._require(not output_dir.exists() and not output_dir.is_symlink(), 'metadata_output_exists')
    token = os.environ.get('GH_TOKEN', '')
    maintenance._require(len(token) <= 4096 and re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token) is not None,
                         'github_token_missing_or_invalid')
    base = 'https://api.github.com/repos/' + binding['repository'] + '/actions/'
    urls = (base + 'runs/' + str(binding['run_id']) + '/attempts/' + str(binding['run_attempt']),
            base + 'artifacts/' + str(binding['artifact_id']))
    opener, metadata = urllib.request.build_opener(NoRedirect()), []
    for url in urls:
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'Maa-PCR-release-promotion'})
        with opener.open(request, timeout=30) as response:
            maintenance._require(response.geturl() == url
                and urllib.parse.urlsplit(response.geturl()).hostname == 'api.github.com',
                'github_metadata_host_mismatch')
            data = response.read(MAX_METADATA + 1)
        maintenance._require(len(data) <= MAX_METADATA, 'github_metadata_too_large')
        metadata.append(maintenance._json(data))
    provenance(binding, *metadata)
    output_dir.mkdir(parents=True)
    for name, value in zip(('run.json', 'artifact.json'), metadata):
        save(output_dir / name, value)
    return {'schema_version': 1, 'status': 'accepted_ci_metadata_verified',
            'run_id': binding['run_id'], 'run_attempt': binding['run_attempt'],
            'artifact_id': binding['artifact_id']}


def promote(acceptance, tag, repository, run_metadata, artifact_metadata, artifact_root, player):
    receipt, binding = accepted(acceptance, tag, repository)
    provenance(binding, maintenance._read_json(Path(run_metadata)),
               maintenance._read_json(Path(artifact_metadata)))
    artifact_root = maintenance._root(artifact_root)
    build = maintenance._read_json(artifact_root / 'BUILD_RECEIPT.json')
    record = binding['runtime_archive']
    maintenance._require(build.get('status') == 'release_staging'
        and build.get('distribution_ready') is False
        and type(build.get('revision')) is int and build['revision'] == receipt['revision']
        and build.get('version') == receipt['expected_version']
        and type(build.get('full')) is dict and set(build['full']) == set(record)
        and all(type(build['full'][key]) is type(value) and build['full'][key] == value
                for key, value in record.items()), 'accepted_build_receipt_mismatch')
    archive = artifact_root / record['name']
    maintenance._require(maintenance._plain(archive).st_size == record['size']
        and digest(archive) == record['sha256'], 'accepted_archive_hash_mismatch')
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        names = [entry.orig_filename for entry in entries]
        maintenance._require(maintenance.MANIFEST in names and len(names) == len(set(names))
            and all(not entry.is_dir() and entry.orig_filename == entry.filename
                    and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
                    for entry in entries), 'invalid_candidate_archive')
        maintenance._require(source.getinfo(maintenance.MANIFEST).file_size <= maintenance.MAX_JSON,
                             'json_too_large')
        raw = source.read(maintenance.MANIFEST)
        manifest = maintenance._json(raw)
        rows, version = maintenance._rows(manifest), maintenance._version(manifest)
        maintenance._require(manifest.get('status') == 'release_staging'
            and manifest.get('distribution_ready') is False
            and version['revision'] == receipt['revision']
            and version['version'] == receipt['expected_version'], 'candidate_manifest_mismatch')
        require_acceptance(receipt, version['revision'], owned_payload_sha256(manifest), version=tag)
        maintenance._require(set(names) == set(rows) | {maintenance.MANIFEST}
            and all(source.getinfo(name).file_size == row['size'] for name, row in rows.items()),
            'candidate_archive_inventory_mismatch')
    player = Path(player).absolute()
    prepared = player.with_name(player.name + '-source-verification')
    runtime = ROOT / '.runtime'
    maintenance._require(player.is_relative_to(runtime) and player != runtime
        and player.resolve() == player and not player.exists() and not player.is_symlink()
        and not prepared.exists() and not prepared.is_symlink(), 'fresh_runtime_output_required')
    parent = player.parent
    while not parent.exists() and not parent.is_symlink():
        parent = parent.parent
    maintenance._root(parent)
    player.mkdir(parents=True)
    extract(archive, player, allow_manifest=True)
    maintenance._require((player / maintenance.MANIFEST).read_bytes() == raw,
                         'candidate_manifest_changed')
    maintenance._verify(player, rows)
    prepare(ROOT, prepared, revision=version['revision'], player_manifest=player / maintenance.MANIFEST)
    alignment = gate(prepared, digest(prepared / 'SOURCE_INVENTORY.json'), player)
    return {'schema_version': 1, 'status': 'accepted_ci_candidate_promoted',
            'revision': version['revision'], 'version': version['version'],
            'accepted_ci_artifact': binding, 'owned_payload_sha256': owned_payload_sha256(manifest),
            'source_alignment': alignment, 'distribution_ready': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_subparsers(dest='mode', required=True)
    first, fetch, final = (modes.add_parser('request'), modes.add_parser('fetch-metadata'),
                           modes.add_parser('promote'))
    for command in (first, fetch, final):
        command.add_argument('--acceptance', type=Path, required=True)
        command.add_argument('--tag', required=True)
        command.add_argument('--repository', required=True)
    first.add_argument('--output', type=Path, required=True)
    first.add_argument('--github-output', type=Path, required=True)
    fetch.add_argument('--output-dir', type=Path, required=True)
    for name in ('run-metadata', 'artifact-metadata', 'artifact-root', 'player'):
        final.add_argument('--' + name, type=Path, required=True)
    args = vars(parser.parse_args(argv))
    mode = args.pop('mode')
    try:
        result = {'request': request, 'fetch-metadata': fetch_metadata, 'promote': promote}[mode](**args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
