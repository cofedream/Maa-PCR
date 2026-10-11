"""Synthetic CI/acceptance metadata; real local Git, ZIP and source-gate checks only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import warnings
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import promote_release_candidate as promotion


def write_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')


def row(name, data):
    return {'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True,
                          text=True, encoding='utf-8').stdout.strip()


def change(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path / 'checkout'
    (root / 'src/pcr_maa').mkdir(parents=True)
    (root / 'gui').mkdir()
    source, gui = b'# synthetic source\n', b'// synthetic GUI source\n'
    (root / 'src/pcr_maa/demo.py').write_bytes(source)
    (root / 'gui/demo.cs').write_bytes(gui)
    (root / '.gitignore').write_text('.runtime/\n', encoding='utf-8')
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=synthetic', '-c', 'user.email=synthetic@example.invalid',
        'commit', '-qm', 'Synthetic owned source')
    commit = git(root, 'rev-parse', 'HEAD')
    monkeypatch.setattr(promotion, 'ROOT', root)
    artifact_root = root / '.runtime/download/build'
    artifact_root.mkdir(parents=True)
    build_inputs = json.dumps({'schema_version': 1, 'files': [row('gui/demo.cs', gui)]}).encode()
    payload = {'src/pcr_maa/demo.py': source, 'MFAAvalonia.dll': b'synthetic; never execute',
               'build-source-manifest.json': build_inputs}
    manifest = {'status': 'release_staging', 'distribution_ready': False,
        'layout': 'portable-offline-validation', 'personal_settings_or_ledgers_included': False,
        'update': {'schema_version': 1, 'product': promotion.maintenance.PRODUCT,
                   'revision': 39, 'version': 'v0.1.0'},
        'files': [row(name, data) for name, data in payload.items()]}
    archive = artifact_root / 'Maa-PCR-v0.1.0-win-x64.zip'
    receipt = {'schema_version': 1, 'revision': 39, 'expected_version': 'v0.1.0', 'status': 'passed',
        'live_valid_game_days': 0, 'candidate_to_baseline_ratio': None,
        'accepted_owned_payload_sha256': promotion.owned_payload_sha256(manifest),
        'offline_tests_passed': True, 'isolated_update_passed': True, 'gui_verified': True,
        'clean_build_verified': True, 'meaningful_speedup_reviewed': False,
        'accepted_ci_artifact': {'repository': promotion.REPOSITORY, 'run_id': 123,
            'run_attempt': 2, 'artifact_id': 456, 'source_commit': commit, 'runtime_archive': {}}}
    build = {'status': 'release_staging', 'distribution_ready': False,
             'revision': 39, 'version': 'v0.1.0', 'full': {}}
    acceptance = root / '.runtime/acceptance.json'
    run = {'id': 123, 'run_attempt': 2, 'head_sha': commit, 'event': 'push', 'head_branch': 'main',
        'status': 'completed', 'conclusion': 'success', 'path': '.github/workflows/ci.yml',
        'repository': {'full_name': promotion.REPOSITORY, 'id': 789},
        'head_repository': {'full_name': promotion.REPOSITORY, 'id': 789},
        'run_started_at': '2026-10-10T10:00:00Z', 'updated_at': '2026-10-10T10:03:00Z'}
    artifact = {'id': 456, 'name': 'windows-candidate-and-tests', 'expired': False,
        'created_at': '2026-10-10T10:02:00Z', 'updated_at': '2026-10-10T10:02:30Z',
        'workflow_run': {'id': 123, 'head_sha': commit, 'head_branch': 'main',
                         'head_repository_id': 789, 'repository_id': 789}}
    run_path, artifact_path = root / '.runtime/run.json', root / '.runtime/artifact.json'
    write_json(run_path, run)
    write_json(artifact_path, artifact)

    def packet(entries=None):
        if entries is None:
            entries = list(payload.items()) + [(promotion.maintenance.MANIFEST,
                                              json.dumps(manifest).encode())]
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(archive, 'w') as output:
                for name, data in entries:
                    output.writestr(name, data)
        record = {'name': archive.name, 'size': archive.stat().st_size,
                  'sha256': promotion.digest(archive)}
        receipt['accepted_ci_artifact']['runtime_archive'] = deepcopy(record)
        build['full'] = deepcopy(record)
        write_json(acceptance, receipt)
        write_json(artifact_root / 'BUILD_RECEIPT.json', build)

    packet()
    return {'root': root, 'payload': payload, 'manifest': manifest, 'archive': archive,
        'receipt': receipt, 'build': build, 'acceptance': acceptance, 'packet': packet,
        'run': run, 'artifact': artifact, 'run_path': run_path, 'artifact_path': artifact_path,
        'output': root / '.runtime/request.json', 'github': root / '.runtime/github-output',
        'kwargs': {'acceptance': acceptance, 'tag': 'v0.1.0', 'repository': promotion.REPOSITORY,
            'run_metadata': run_path, 'artifact_metadata': artifact_path,
            'artifact_root': artifact_root, 'player': root / '.runtime/promoted/player'}}


def assert_not_promoted(candidate):
    player = candidate['kwargs']['player']
    assert not player.exists()
    assert not player.with_name(player.name + '-source-verification').exists()


def make_request(candidate):
    return promotion.request(candidate['acceptance'], 'v0.1.0', promotion.REPOSITORY,
                             candidate['output'], candidate['github'])


def test_request_outputs_only_validated_identifiers(candidate):
    candidate['github'].write_text('existing=value\n', encoding='utf-8')
    result = make_request(candidate)
    assert json.loads(candidate['output'].read_text(encoding='utf-8')) == result
    assert candidate['github'].read_text(encoding='utf-8') == (
        'existing=value\nrun_id=123\nartifact_id=456\nsource_commit='
        + candidate['receipt']['accepted_ci_artifact']['source_commit'] + '\n')
    assert_not_promoted(candidate)


@pytest.mark.parametrize('key,value', [('status', 'pending'), ('offline_tests_passed', False),
    ('isolated_update_passed', False), ('gui_verified', False), ('clean_build_verified', False),
    ('expected_version', 'v0.2.0')])
def test_acceptance_gate_precedes_all_outputs(candidate, key, value):
    candidate['receipt'][key] = value
    write_json(candidate['acceptance'], candidate['receipt'])
    candidate['output'] = candidate['root'] / '.runtime/not-created/request.json'
    for operation in (lambda: make_request(candidate), lambda: promotion.promote(**candidate['kwargs'])):
        with pytest.raises(ValueError, match='release_acceptance_incomplete'):
            operation()
    assert not candidate['output'].parent.exists()
    assert not candidate['github'].exists()
    assert_not_promoted(candidate)


@pytest.mark.parametrize('path,value', [
    (('repository',), 'other/Maa-PCR'), (('run_id',), True), (('run_id',), 0),
    (('artifact_id',), True), (('artifact_id',), -1), (('run_attempt',), True), (('run_attempt',), 0),
    (('source_commit',), 'a' * 39), (('source_commit',), 'A' * 40),
    (('runtime_archive', 'name'), '../Maa-PCR-v0.1.0-win-x64.zip'),
    (('runtime_archive', 'name'), 'Maa-PCR-v0.1.1-win-x64.zip'),
    (('runtime_archive', 'size'), True), (('runtime_archive', 'size'), 0),
    (('runtime_archive', 'sha256'), 'A' * 64)])
def test_request_rejects_invalid_binding_without_output(candidate, path, value):
    change(candidate['receipt']['accepted_ci_artifact'], path, value)
    write_json(candidate['acceptance'], candidate['receipt'])
    with pytest.raises(ValueError):
        make_request(candidate)
    assert not candidate['output'].exists() and not candidate['github'].exists()


@pytest.mark.parametrize('location,key', [('binding', 'run_id'), ('binding', 'run_attempt'),
    ('binding', 'unexpected'), ('archive', 'sha256'), ('archive', 'unexpected')])
def test_request_rejects_missing_and_unknown_keys(candidate, location, key):
    value = candidate['receipt']['accepted_ci_artifact']
    if location == 'archive':
        value = value['runtime_archive']
    if key == 'unexpected':
        value[key] = 'not allowed'
    else:
        del value[key]
    write_json(candidate['acceptance'], candidate['receipt'])
    with pytest.raises(ValueError):
        make_request(candidate)
    assert not candidate['output'].exists() and not candidate['github'].exists()


@pytest.mark.parametrize('kind,path,value', [
    ('run', ('id',), 124), ('run', ('id',), True), ('run', ('run_attempt',), 1),
    ('run', ('head_sha',), 'a' * 40), ('run', ('event',), 'pull_request'),
    ('run', ('head_branch',), 'release'), ('run', ('status',), 'in_progress'),
    ('run', ('conclusion',), 'failure'), ('run', ('path',), '.github/workflows/release.yml'),
    ('run', ('repository', 'full_name'), 'other/Maa-PCR'),
    ('run', ('head_repository', 'full_name'), 'other/Maa-PCR'),
    ('run', ('head_repository', 'id'), 790),
    ('artifact', ('id',), 457), ('artifact', ('id',), True),
    ('artifact', ('name',), 'other'), ('artifact', ('expired',), True),
    ('artifact', ('expired',), 0), ('artifact', ('workflow_run', 'id'), 124),
    ('artifact', ('workflow_run', 'head_sha'), 'a' * 40),
    ('artifact', ('workflow_run', 'head_branch'), 'other'),
    ('artifact', ('workflow_run', 'head_repository_id'), 790),
    ('artifact', ('workflow_run', 'repository_id'), 790),
    ('artifact', ('created_at',), '2026-10-10T09:59:59Z'),
    ('artifact', ('created_at',), '2026-10-10T10:03:01Z'),
    ('artifact', ('created_at',), '2026-10-10T10:02:00'),
    ('artifact', ('created_at',), 'invalid'),
    ('artifact', ('updated_at',), '2026-10-10T09:59:59Z'),
    ('artifact', ('updated_at',), '2026-10-10T10:03:01Z')])
def test_rest_provenance_rejects_wrong_run_artifact_or_attempt(candidate, kind, path, value):
    change(candidate[kind], path, value)
    write_json(candidate[kind + '_path'], candidate[kind])
    with pytest.raises(ValueError):
        promotion.promote(**candidate['kwargs'])
    assert_not_promoted(candidate)


def test_exact_ci_archive_is_verified_without_sealing_or_rebuilding(candidate):
    (candidate['root'] / 'delivery').mkdir()
    write_json(candidate['root'] / 'delivery/acceptance.json', candidate['receipt'])
    git(candidate['root'], 'add', 'delivery/acceptance.json')
    git(candidate['root'], '-c', 'user.name=synthetic', '-c', 'user.email=synthetic@example.invalid',
        'commit', '-qm', 'Synthetic acceptance-only descendant commit')
    before = candidate['acceptance'].read_bytes(), candidate['archive'].read_bytes()
    result = promotion.promote(**candidate['kwargs'])
    player = candidate['kwargs']['player']
    assert result['source_alignment']['passed'] is True
    assert result['source_alignment']['owned_program_source_alignment_count'] == 1
    assert result['source_alignment']['gui_build_input_source_alignment_count'] == 1
    assert result['distribution_ready'] is False
    assert json.loads((player / 'package-manifest.json').read_bytes()) == candidate['manifest']
    assert (candidate['acceptance'].read_bytes(), candidate['archive'].read_bytes()) == before
    assert set(path.relative_to(player).as_posix() for path in player.rglob('*') if path.is_file()) == (
        set(candidate['payload']) | {'package-manifest.json'})


def test_accepted_source_must_be_local_ancestor(candidate):
    binding = candidate['receipt']['accepted_ci_artifact']
    binding['source_commit'] = 'b' * 40
    candidate['run']['head_sha'] = 'b' * 40
    candidate['artifact']['workflow_run']['head_sha'] = 'b' * 40
    for path, value in ((candidate['acceptance'], candidate['receipt']),
                        (candidate['run_path'], candidate['run']),
                        (candidate['artifact_path'], candidate['artifact'])):
        write_json(path, value)
    with pytest.raises(ValueError, match='accepted_source_not_ancestor'):
        promotion.promote(**candidate['kwargs'])
    assert_not_promoted(candidate)


@pytest.mark.parametrize('name', ['README.md', 'scripts/build-only.py'])
def test_non_acceptance_commit_diff_is_rejected(candidate, name):
    target = candidate['root'] / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('# synthetic source changed\n', encoding='utf-8')
    git(candidate['root'], 'add', name)
    git(candidate['root'], '-c', 'user.name=synthetic', '-c', 'user.email=synthetic@example.invalid',
        'commit', '-qm', 'Synthetic source divergence')
    with pytest.raises(ValueError, match='accepted_source_changed'):
        promotion.promote(**candidate['kwargs'])
    assert_not_promoted(candidate)


@pytest.mark.parametrize('kind', ['tracked', 'untracked'])
def test_dirty_build_only_or_untracked_source_is_rejected(candidate, kind):
    target = candidate['root'] / ('gui/demo.cs' if kind == 'tracked' else 'scripts/new.py')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('# synthetic uncommitted source\n', encoding='utf-8')
    with pytest.raises(ValueError, match='accepted_source_dirty'):
        promotion.promote(**candidate['kwargs'])
    assert_not_promoted(candidate)


def test_ignored_runtime_evidence_is_not_source_dirt(candidate):
    evidence = candidate['root'] / '.runtime/local-evidence.txt'
    evidence.write_text('Synthetic isolated evidence.\n', encoding='utf-8')
    result = promotion.promote(**candidate['kwargs'])
    assert result['source_alignment']['passed'] is True
    assert evidence.read_text(encoding='utf-8') == 'Synthetic isolated evidence.\n'


@pytest.mark.parametrize('staged', [False, True])
def test_uncommitted_acceptance_is_rejected_by_promote_and_fetch(candidate, monkeypatch, staged):
    acceptance = candidate['root'] / 'delivery/acceptance.json'
    acceptance.parent.mkdir()
    write_json(acceptance, candidate['receipt'])
    git(candidate['root'], 'add', 'delivery/acceptance.json')
    git(candidate['root'], '-c', 'user.name=synthetic', '-c', 'user.email=synthetic@example.invalid',
        'commit', '-qm', 'Synthetic committed acceptance')
    write_json(acceptance, dict(candidate['receipt'], note='Synthetic dirty acceptance'))
    if staged:
        git(candidate['root'], 'add', 'delivery/acceptance.json')
    candidate['kwargs']['acceptance'] = acceptance
    before = acceptance.read_bytes()
    with pytest.raises(ValueError, match='accepted_source_dirty'):
        promotion.promote(**candidate['kwargs'])
    assert_not_promoted(candidate)
    stub_metadata(candidate, monkeypatch)
    output = candidate['root'] / '.runtime/metadata'
    with pytest.raises(ValueError, match='accepted_source_dirty'):
        promotion.fetch_metadata(acceptance, 'v0.1.0', promotion.REPOSITORY, output)
    assert not output.exists()
    assert acceptance.read_bytes() == before


@pytest.mark.parametrize('key,value', [('status', 'passed'), ('distribution_ready', True),
    ('revision', 38), ('revision', True), ('version', 'v0.1.1'), ('full', {})])
def test_build_receipt_must_match_accepted_archive(candidate, key, value):
    candidate['build'][key] = value
    write_json(candidate['kwargs']['artifact_root'] / 'BUILD_RECEIPT.json', candidate['build'])
    with pytest.raises(ValueError, match='accepted_build_receipt_mismatch'):
        promotion.promote(**candidate['kwargs'])
    assert_not_promoted(candidate)


@pytest.mark.parametrize('missing', [False, True])
def test_missing_or_changed_archive_does_not_extract(candidate, missing):
    if missing:
        candidate['archive'].unlink()
    else:
        with candidate['archive'].open('ab') as stream:
            stream.write(b'changed')
    with pytest.raises((OSError, ValueError)):
        promotion.promote(**candidate['kwargs'])
    assert_not_promoted(candidate)


def test_wrong_archive_sha_at_same_size_does_not_extract(candidate):
    data = bytearray(candidate['archive'].read_bytes())
    data[-1] ^= 1
    candidate['archive'].write_bytes(data)
    with pytest.raises(ValueError, match='accepted_archive_hash_mismatch'):
        promotion.promote(**candidate['kwargs'])
    assert_not_promoted(candidate)


def test_accepted_owned_payload_must_match_manifest(candidate):
    candidate['receipt']['accepted_owned_payload_sha256'] = 'a' * 64
    write_json(candidate['acceptance'], candidate['receipt'])
    with pytest.raises(ValueError, match='release_acceptance_incomplete'):
        promotion.promote(**candidate['kwargs'])
    assert_not_promoted(candidate)


@pytest.mark.parametrize('path,value', [(('status',), 'player_test_release'),
    (('distribution_ready',), True), (('update', 'revision'), 38), (('update', 'version'), 'v0.1.1')])
def test_candidate_manifest_cannot_be_substituted(candidate, path, value):
    change(candidate['manifest'], path, value)
    candidate['packet']()
    with pytest.raises(ValueError, match='candidate_manifest_mismatch'):
        promotion.promote(**candidate['kwargs'])
    assert_not_promoted(candidate)


@pytest.mark.parametrize('bad', ['traversal', 'directory', 'duplicate', 'extra', 'missing', 'case'])
def test_bad_zip_inventory_does_not_create_output(candidate, bad):
    entries = list(candidate['payload'].items()) + [('package-manifest.json',
                                                    json.dumps(candidate['manifest']).encode())]
    if bad == 'missing':
        entries.pop()
    else:
        entries.append({'traversal': ('../outside.txt', b'unsafe'), 'directory': ('directory/', b''),
            'duplicate': entries[0], 'extra': ('extra.txt', b'extra'),
            'case': ('SRC/PCR_MAA/DEMO.PY', b'collision')}[bad])
    candidate['packet'](entries)
    with pytest.raises(ValueError):
        promotion.promote(**candidate['kwargs'])
    assert_not_promoted(candidate)
    assert not (candidate['root'] / 'outside.txt').exists()


def test_changed_owned_payload_is_not_accepted(candidate):
    entries = [(name, b'X' * len(data) if name == 'src/pcr_maa/demo.py' else data)
               for name, data in candidate['payload'].items()]
    entries.append(('package-manifest.json', json.dumps(candidate['manifest']).encode()))
    candidate['packet'](entries)
    with pytest.raises(ValueError, match='payload_hash_mismatch'):
        promotion.promote(**candidate['kwargs'])
    assert not candidate['kwargs']['player'].with_name('player-source-verification').exists()
    assert json.loads((candidate['kwargs']['player'] / 'package-manifest.json').read_bytes())[
        'distribution_ready'] is False


@pytest.mark.parametrize('name', ['src/pcr_maa/demo.py', 'gui/demo.cs'])
def test_current_checkout_must_match_owned_program_and_gui_sources(candidate, name):
    (candidate['root'] / name).write_bytes(b'# changed source\n')
    before = candidate['acceptance'].read_bytes()
    with pytest.raises(ValueError, match='accepted_source_dirty'):
        promotion.promote(**candidate['kwargs'])
    assert candidate['acceptance'].read_bytes() == before
    assert_not_promoted(candidate)


@pytest.mark.parametrize('name', ['src/pcr_maa/demo.py', 'gui/demo.cs'])
def test_clean_checkout_still_must_align_with_candidate_sources(candidate, name):
    data = b'# different synthetic source\n'
    if name.startswith('gui/'):
        inputs = json.loads(candidate['payload']['build-source-manifest.json'])
        inputs['files'] = [row(name, data)]
        name, data = 'build-source-manifest.json', json.dumps(inputs).encode()
    candidate['payload'][name] = data
    candidate['manifest']['files'] = [row(name, data) if item['path'] == name else item
                                     for item in candidate['manifest']['files']]
    candidate['receipt']['accepted_owned_payload_sha256'] = promotion.owned_payload_sha256(candidate['manifest'])
    candidate['packet']()
    with pytest.raises(ValueError, match='(?:owned_source_mismatch|gui_build_input_source_mismatch)'):
        promotion.promote(**candidate['kwargs'])
    assert json.loads((candidate['kwargs']['player'] / 'package-manifest.json').read_bytes())[
        'distribution_ready'] is False


@pytest.mark.parametrize('existing', ['player', 'verification', 'outside'])
def test_only_fresh_runtime_output_is_allowed(candidate, existing):
    player = candidate['kwargs']['player']
    if existing == 'outside':
        candidate['kwargs']['player'] = candidate['root'] / 'not-runtime/player'
    else:
        destination = player if existing == 'player' else player.with_name('player-source-verification')
        destination.mkdir(parents=True)
        (destination / 'sentinel').write_bytes(b'preserve')
    with pytest.raises(ValueError, match='fresh_runtime_output_required'):
        promotion.promote(**candidate['kwargs'])
    if existing != 'outside':
        assert (destination / 'sentinel').read_bytes() == b'preserve'
    else:
        assert_not_promoted(candidate)


def test_pending_cli_returns_failure_and_no_github_output(candidate, capsys):
    candidate['receipt']['status'] = 'pending'
    write_json(candidate['acceptance'], candidate['receipt'])
    assert promotion.main(['request', '--acceptance', str(candidate['acceptance']), '--tag', 'v0.1.0',
        '--repository', promotion.REPOSITORY, '--output', str(candidate['output']),
        '--github-output', str(candidate['github'])]) == 1
    assert json.loads(capsys.readouterr().err)['error'] == 'release_acceptance_incomplete'
    assert not candidate['output'].exists() and not candidate['github'].exists()


@pytest.mark.parametrize('opt_in', [False, True])
def test_shared_extractor_manifest_is_explicit_opt_in(tmp_path, opt_in):
    archive, output = tmp_path / 'manifest.zip', tmp_path / 'output'
    with zipfile.ZipFile(archive, 'w') as packet:
        packet.writestr('package-manifest.json', b'{"synthetic":true}')
    if opt_in:
        promotion.extract(archive, output, allow_manifest=True)
        assert (output / 'package-manifest.json').read_bytes() == b'{"synthetic":true}'
    else:
        with pytest.raises(ValueError, match='private_payload_path'):
            promotion.extract(archive, output)
        assert not output.exists()


@pytest.mark.parametrize('bad', ['traversal', 'link', 'collision', 'manifest-case'])
def test_manifest_opt_in_keeps_existing_zip_safety(tmp_path, bad):
    archive, output = tmp_path / 'unsafe.zip', tmp_path / 'output'
    with zipfile.ZipFile(archive, 'w') as packet:
        packet.writestr('package-manifest.json', b'{}')
        if bad == 'link':
            info = zipfile.ZipInfo('link')
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            packet.writestr(info, b'outside')
        elif bad == 'collision':
            packet.writestr('a.dll', b'a')
            packet.writestr('A.dll', b'b')
        else:
            packet.writestr('../outside' if bad == 'traversal' else 'Package-Manifest.json', b'unsafe')
    with pytest.raises(ValueError):
        promotion.extract(archive, output, allow_manifest=True)
    assert not output.exists()


class MetadataResponse:
    def __init__(self, url, data):
        self.url, self.data = url, data

    def geturl(self):
        return self.url

    def read(self, count):
        return self.data[:count]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def stub_metadata(candidate, monkeypatch, transform=None):
    calls = []
    monkeypatch.setenv('GH_TOKEN', 'synthetic_token_not_credentials')

    class Opener:
        def open(self, request, timeout):
            assert timeout == 30
            assert request.get_header('Authorization') == 'Bearer synthetic_token_not_credentials'
            assert request.get_header('X-github-api-version') == '2022-11-28'
            index = len(calls)
            calls.append(request.full_url)
            data = json.dumps(candidate['run'] if index == 0 else candidate['artifact']).encode()
            url = request.full_url
            if transform:
                url, data = transform(index, url, data)
            return MetadataResponse(url, data)

    def opener(handler):
        assert isinstance(handler, promotion.NoRedirect)
        return Opener()

    monkeypatch.setattr(promotion.urllib.request, 'build_opener', opener)
    return calls


def test_fetch_metadata_uses_only_fixed_attempt_and_artifact_endpoints(candidate, monkeypatch):
    calls = stub_metadata(candidate, monkeypatch)
    output = candidate['root'] / '.runtime/metadata'
    result = promotion.fetch_metadata(candidate['acceptance'], 'v0.1.0', promotion.REPOSITORY, output)
    assert calls == ['https://api.github.com/repos/shiwandehongbai/Maa-PCR/actions/runs/123/attempts/2',
                     'https://api.github.com/repos/shiwandehongbai/Maa-PCR/actions/artifacts/456']
    assert result['run_attempt'] == 2
    assert json.loads((output / 'run.json').read_bytes()) == candidate['run']
    assert json.loads((output / 'artifact.json').read_bytes()) == candidate['artifact']
    assert all(b'synthetic_token_not_credentials' not in path.read_bytes() for path in output.iterdir())


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('bad', ['host', 'size', 'json'])
def test_fetch_metadata_rejects_changed_host_oversize_and_invalid_json(candidate, monkeypatch, index, bad):
    def transform(current, url, data):
        if current == index:
            if bad == 'host':
                url = 'https://example.invalid/redirected'
            else:
                data = b'x' * (promotion.MAX_METADATA + 1) if bad == 'size' else b'[]'
        return url, data
    stub_metadata(candidate, monkeypatch, transform)
    output = candidate['root'] / '.runtime/metadata'
    with pytest.raises(ValueError):
        promotion.fetch_metadata(candidate['acceptance'], 'v0.1.0', promotion.REPOSITORY, output)
    assert not output.exists()


@pytest.mark.parametrize('code', [301, 302, 303, 307, 308])
def test_github_metadata_redirect_handler_always_refuses(code):
    with pytest.raises(ValueError, match='github_metadata_redirect_refused'):
        promotion.NoRedirect().redirect_request(None, None, code, 'redirect', {},
                                                 'https://api.github.com/also-refused')


def test_pending_metadata_does_not_read_token_or_open_network(candidate, monkeypatch):
    candidate['receipt']['status'] = 'pending'
    write_json(candidate['acceptance'], candidate['receipt'])
    monkeypatch.delenv('GH_TOKEN', raising=False)
    monkeypatch.setattr(promotion.urllib.request, 'build_opener',
                        lambda *args: pytest.fail('pending acceptance attempted network'))
    output = candidate['root'] / '.runtime/metadata'
    with pytest.raises(ValueError, match='release_acceptance_incomplete'):
        promotion.fetch_metadata(candidate['acceptance'], 'v0.1.0', promotion.REPOSITORY, output)
    assert not output.exists()


@pytest.mark.parametrize('token', ['', 'not\na\nheader'])
def test_metadata_token_absent_or_invalid_is_never_echoed(candidate, monkeypatch, capsys, token):
    monkeypatch.setenv('GH_TOKEN', token)
    output = candidate['root'] / '.runtime/metadata'
    assert promotion.main(['fetch-metadata', '--acceptance', str(candidate['acceptance']),
        '--tag', 'v0.1.0', '--repository', promotion.REPOSITORY, '--output-dir', str(output)]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error == {'status': 'failed', 'error': 'github_token_missing_or_invalid'}
    assert not output.exists()
