"""Synthetic online updates: no network, real player, game or executable."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.parse import quote
import zipfile

import pytest

from pcr_maa import player_maintenance as maintenance
from pcr_maa import player_update as updater


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def manifest(payload, revision, version):
    return {
        'layout': 'portable-offline-validation',
        'distribution_ready': False,
        'personal_settings_or_ledgers_included': False,
        'update': {'schema_version': 1, 'product': maintenance.PRODUCT,
                   'revision': revision, 'version': version},
        'files': [{'path': name, 'size': len(data), 'sha256': sha(data)}
                  for name, data in sorted(payload.items())],
    }


def packet(payload, target_bytes, *, patch=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
        archive.writestr(maintenance.MANIFEST, target_bytes)
        if patch is not None:
            archive.writestr('patch-manifest.json', encode(patch))
    return output.getvalue()


def asset(name, data):
    return {'name': name, 'size': len(data), 'sha256': sha(data)}


def download_url(name, tag='v0.1.1'):
    return ('https://github.com/shiwandehongbai/Maa-PCR/releases/download/'
            + tag + '/' + quote(name, safe=''))


@pytest.fixture
def online_case(tmp_path):
    player = tmp_path / 'synthetic player Chinese space'
    old = {
        'MFAAvalonia.exe': b'synthetic GUI never execute',
        'MFAAvalonia.Core.dll': b'old synthetic core',
        'pcr-repository.txt': b'.',
        'runtime/python/python.exe': b'synthetic interpreter never execute',
        'runtime/python/python311._pth': b'python311.zip\n.\n',
        'runtime/python/python311.zip': b'synthetic standard library',
        'scripts/player-update.py': b'# synthetic online wrapper',
        'scripts/player-maintenance.py': b'# synthetic offline wrapper',
        'scripts/recover-player-update.ps1': b'# synthetic independent recovery',
        'src/pcr_maa/__init__.py': b'',
        'src/pcr_maa/player_update.py': b'# synthetic online module',
        'src/pcr_maa/player_maintenance.py': b'# synthetic maintenance module',
    }
    private = {
        'config/instances/player.json': b'{"selected":["schedule","rewards"]}',
        '.runtime/ledger/unknown.json': b'{"state":"in_flight"}',
        'runtime/python/local-secret.txt': b'unowned must not be copied',
    }
    for name, data in {**old, **private}.items():
        destination = player / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    old_bytes = encode(manifest(old, 39, 'v0.1.0'))
    (player / maintenance.MANIFEST).write_bytes(old_bytes)
    new = {**old, 'MFAAvalonia.Core.dll': b'new synthetic core',
           'runtime/python/python.exe': b'updated synthetic interpreter never execute'}
    target_bytes = encode(manifest(new, 40, 'v0.1.1'))
    full = packet(new, target_bytes)
    patch_metadata = {
        'schema_version': 1, 'product': maintenance.PRODUCT,
        'layout': 'portable-offline-validation', 'base_revision': 39,
        'base_manifest_sha256': sha(old_bytes), 'target_revision': 40,
        'target_manifest_sha256': sha(target_bytes),
    }
    delta = packet({name: data for name, data in new.items()
                    if old.get(name) != data}, target_bytes, patch=patch_metadata)
    index = {
        'schema_version': 1, 'product': maintenance.PRODUCT, 'platform': 'win-x64',
        'tag': 'v0.1.1', 'version': 'v0.1.1', 'revision': 40,
        'target_manifest_sha256': sha(target_bytes),
        'full': asset('Maa-PCR-v0.1.1-win-x64.zip', full),
        'patches': [{**asset('Maa-PCR-R39-R40-patch.zip', delta),
                     'base_revision': 39, 'base_manifest_sha256': sha(old_bytes)}],
    }
    index_bytes = encode(index)
    blobs = {index['full']['name']: full, index['patches'][0]['name']: delta,
             'update-index.json': index_bytes}
    release = {
        'tag_name': 'v0.1.1', 'prerelease': False, 'draft': False,
        'assets': [{'name': name, 'size': len(data),
                    'browser_download_url': download_url(name)}
                   for name, data in blobs.items()],
    }
    return {'player': player, 'old': old, 'new': new, 'private': private,
            'old_bytes': old_bytes, 'target_bytes': target_bytes, 'index': index,
            'release': release, 'blobs': blobs, 'tmp_path': tmp_path}


def assert_original(case):
    assert (case['player'] / maintenance.MANIFEST).read_bytes() == case['old_bytes']
    for name, data in {**case['old'], **case['private']}.items():
        assert (case['player'] / name).read_bytes() == data, name


class Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data))}
        self.url = url
        self.status = 200

    def geturl(self):
        return self.url


def fake_opener(case, *, replacements=None, failure_url=None):
    calls = []
    routes = {download_url(name): data for name, data in case['blobs'].items()}
    routes['https://api.github.com/repos/shiwandehongbai/Maa-PCR/releases/latest'] = encode(case['release'])
    routes.update(replacements or {})

    def open_response(request, timeout=30):
        url = request.full_url
        calls.append(url)
        if url == failure_url:
            raise URLError('synthetic offline failure')
        assert url in routes, 'unexpected network request: ' + url
        return Response(routes[url], url)

    return open_response, calls


def test_exact_manifest_selects_patch_and_uses_business_revision(online_case):
    case = online_case
    result = updater.validate_index(case['index'], case['release'], case['old_bytes'])
    assert result['status'] == 'update_available'
    assert result['current_revision'] == 39 and result['revision'] == 40
    assert result['current_version'] == 'v0.1.0'
    assert result['asset']['kind'] == 'delta'
    assert result['asset']['name'] == case['index']['patches'][0]['name']
    assert result['asset']['url'] == download_url(result['asset']['name'])


@pytest.mark.parametrize('mismatch', ['revision', 'manifest_bytes'])
def test_patch_requires_exact_revision_and_manifest_otherwise_full(online_case, mismatch):
    case = online_case
    old = json.loads(case['old_bytes'])
    if mismatch == 'revision':
        old['update']['revision'] = 38
    current = json.dumps(old, separators=(',', ':')).encode('utf-8')
    assert current != case['old_bytes']
    result = updater.validate_index(case['index'], case['release'], current)
    assert result['status'] == 'update_available'
    assert result['asset']['kind'] == 'full'
    assert result['asset']['name'] == case['index']['full']['name']


@pytest.mark.parametrize('revision', [40, 41])
def test_equal_or_newer_installed_revision_is_up_to_date(online_case, revision):
    case = online_case
    current = json.loads(case['old_bytes'])
    current['update']['revision'] = revision
    result = updater.validate_index(case['index'], case['release'], encode(current))
    assert result['status'] == 'up_to_date'


@pytest.mark.parametrize('fault', [
    'schema_bool', 'product', 'platform', 'revision_bool', 'tag_mismatch',
    'full_size_bool', 'full_hash', 'unsafe_name', 'asset_size',
    'wrong_repo_url', 'http_url', 'prerelease', 'draft',
])
def test_untrusted_index_or_release_is_rejected(online_case, fault):
    case = online_case
    index, release = deepcopy(case['index']), deepcopy(case['release'])
    full_asset = next(row for row in release['assets'] if row['name'] == index['full']['name'])
    if fault == 'schema_bool': index['schema_version'] = True
    if fault == 'product': index['product'] = 'other-player'
    if fault == 'platform': index['platform'] = 'linux-x64'
    if fault == 'revision_bool': index['revision'] = True
    if fault == 'tag_mismatch': release['tag_name'] = 'v9.9.9'
    if fault == 'full_size_bool': index['full']['size'] = True
    if fault == 'full_hash': index['full']['sha256'] = 'not-a-sha256'
    if fault == 'unsafe_name': index['full']['name'] = '../outside.zip'
    if fault == 'asset_size': full_asset['size'] += 1
    if fault == 'wrong_repo_url': full_asset['browser_download_url'] = full_asset['browser_download_url'].replace('shiwandehongbai', 'someone-else')
    if fault == 'http_url': full_asset['browser_download_url'] = full_asset['browser_download_url'].replace('https:', 'http:')
    if fault == 'prerelease': release['prerelease'] = True
    if fault == 'draft': release['draft'] = True
    with pytest.raises(ValueError):
        updater.validate_index(index, release, case['old_bytes'])
    assert_original(case)


@pytest.mark.parametrize('fault', ['duplicate_base', 'base_revision_bool', 'base_hash'])
def test_patch_metadata_cannot_ambiguously_select_a_base(online_case, fault):
    case = online_case
    index, release = deepcopy(case['index']), deepcopy(case['release'])
    if fault == 'duplicate_base':
        duplicate = {**index['patches'][0], 'name': 'duplicate-base.zip'}
        index['patches'].append(duplicate)
        release['assets'].append({'name': duplicate['name'], 'size': duplicate['size'],
                                  'browser_download_url': download_url(duplicate['name'])})
    if fault == 'base_revision_bool': index['patches'][0]['base_revision'] = True
    if fault == 'base_hash': index['patches'][0]['base_manifest_sha256'] = 'invalid-hash'
    with pytest.raises(ValueError):
        updater.validate_index(index, release, case['old_bytes'])
    assert_original(case)


def test_check_uses_only_own_release_metadata_and_never_downloads_package(online_case):
    opener, calls = fake_opener(online_case)
    result = updater.check_update(online_case['player'], opener=opener)
    assert result['status'] == 'update_available' and result['revision'] == 40
    assert calls == [
        'https://api.github.com/repos/shiwandehongbai/Maa-PCR/releases/latest',
        download_url('update-index.json'),
    ]
    assert_original(online_case)


def test_previous_interruption_is_reported_without_network(online_case):
    marker = online_case['player'] / '.runtime/player-maintenance/update-in-progress.json'
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b'{"workspace":"existing-original"}')

    def unexpected_network(*args, **kwargs):
        raise AssertionError('recovery must precede network')

    result = updater.check_update(online_case['player'], opener=unexpected_network)
    assert result['status'] == 'recovery_required'
    assert marker.read_bytes() == b'{"workspace":"existing-original"}'
    assert_original(online_case)


def test_offline_check_preserves_current_installation(online_case):
    opener, _ = fake_opener(online_case,
        failure_url='https://api.github.com/repos/shiwandehongbai/Maa-PCR/releases/latest')
    with pytest.raises((URLError, ValueError, RuntimeError)):
        updater.check_update(online_case['player'], opener=opener)
    assert_original(online_case)


@pytest.mark.parametrize('stage', ['release', 'index', 'package'])
@pytest.mark.parametrize('redirect', [
    'http://github.com/untrusted', 'https://foreign.example/untrusted',
    'https://user:password@github.com/untrusted',
])
def test_http_foreign_or_credential_redirect_is_rejected(online_case, stage, redirect):
    case = online_case
    regular, _ = fake_opener(case)
    target = {
        'release': 'https://api.github.com/repos/shiwandehongbai/Maa-PCR/releases/latest',
        'index': download_url('update-index.json'),
        'package': download_url(case['index']['patches'][0]['name']),
    }[stage]
    responses = []

    def opener(request, timeout=30):
        response = regular(request, timeout)
        if request.full_url == target:
            response.url = redirect
            responses.append(response)
        return response

    with pytest.raises(ValueError, match='unsafe_update_redirect'):
        updater.download_update(case['player'], opener=opener)
    assert len(responses) == 1 and responses[0].closed
    assert_original(case)


def test_github_https_release_asset_redirect_remains_supported(online_case):
    case = online_case
    regular, _ = fake_opener(case)

    def opener(request, timeout=30):
        response = regular(request, timeout)
        if request.full_url != 'https://api.github.com/repos/shiwandehongbai/Maa-PCR/releases/latest':
            response.url = 'https://release-assets.githubusercontent.com/synthetic?' + 'signature' + '=test'
        return response

    assert updater.download_update(case['player'], opener=opener)['status'] == 'downloaded'
    assert_original(case)


def test_download_validates_delta_without_installing_or_resuming(online_case):
    opener, calls = fake_opener(online_case)
    result = updater.download_update(online_case['player'], opener=opener)
    assert result['status'] == 'downloaded'
    downloaded = Path(result['archive'])
    assert downloaded.is_file()
    assert downloaded.read_bytes() == online_case['blobs'][online_case['index']['patches'][0]['name']]
    assert calls[-1] == download_url(online_case['index']['patches'][0]['name'])
    assert_original(online_case)


@pytest.mark.parametrize('fault', ['hash', 'size', 'network'])
def test_failed_download_cannot_change_or_launch_player(online_case, fault):
    case = online_case
    url = download_url(case['index']['patches'][0]['name'])
    original = case['blobs'][case['index']['patches'][0]['name']]
    changed = original[:-1] + bytes([original[-1] ^ 1]) if fault == 'hash' else original[:-1]
    opener, _ = fake_opener(case, replacements={url: changed},
                            failure_url=url if fault == 'network' else None)
    with pytest.raises((URLError, ValueError, RuntimeError)):
        updater.download_update(case['player'], opener=opener)
    assert_original(case)
    assert not (case['player'] / '.runtime/player-maintenance/update-in-progress.json').exists()


@pytest.mark.parametrize('kind', ['full', 'patch'])
def test_offline_packet_uses_same_manifest_validation(online_case, kind):
    case = online_case
    entry = case['index']['full'] if kind == 'full' else case['index']['patches'][0]
    archive = case['tmp_path'] / entry['name']
    archive.write_bytes(case['blobs'][entry['name']])
    result = updater.validate_archive(case['player'], archive)
    assert result['revision'] == 40
    assert_original(case)


@pytest.mark.parametrize('fault', ['unsafe', 'extra', 'target_manifest', 'bad_zip'])
def test_archive_rejects_bad_inventory_or_expected_target(online_case, fault):
    case = online_case
    payload = dict(case['new'])
    expected = updater.validate_index(case['index'], case['release'], case['old_bytes'])
    if fault == 'unsafe': payload['../outside.txt'] = b'never install'
    if fault == 'extra': payload['not-owned.txt'] = b'never install'
    target = case['target_bytes']
    if fault == 'target_manifest':
        value = json.loads(target)
        value['update']['version'] = 'unexpected-display-version'
        target = encode(value)
    archive = case['tmp_path'] / 'synthetic-offline.zip'
    archive.write_bytes(b'not a zip' if fault == 'bad_zip' else packet(payload, target))
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        updater.validate_archive(case['player'], archive, expected=expected)
    assert_original(case)


def test_prepare_copies_verified_runtime_outside_installation_without_private_files(online_case):
    case = online_case
    archive = case['tmp_path'] / 'synthetic-offline.zip'
    archive.write_bytes(case['blobs'][case['index']['full']['name']])
    worker = updater.prepare_worker(case['player'], archive,
        temp_root=case['tmp_path'] / 'external workers', parent_pid=123)
    workspace = Path(worker['workspace'])
    assert not workspace.is_relative_to(case['player'])
    assert Path(worker['python']).is_relative_to(workspace)
    assert Path(worker['script']).is_relative_to(workspace)
    assert Path(worker['python']).read_bytes() == case['old']['runtime/python/python.exe']
    assert Path(worker['archive']).read_bytes() == archive.read_bytes()
    assert worker['archive_sha256'] == sha(archive.read_bytes())
    copied = [path.read_bytes() for path in workspace.rglob('*') if path.is_file()]
    for data in case['private'].values():
        assert data not in copied
    assert_original(case)


def test_prepare_rejects_modified_owned_interpreter_before_launch(online_case):
    case = online_case
    path = case['player'] / 'runtime/python/python.exe'
    path.write_bytes(b'external modification must not be executed')
    archive = case['tmp_path'] / 'synthetic-offline.zip'
    archive.write_bytes(case['blobs'][case['index']['full']['name']])
    with pytest.raises(ValueError):
        updater.prepare_worker(case['player'], archive,
                               temp_root=case['tmp_path'] / 'external workers')
    assert path.read_bytes() == b'external modification must not be executed'
    for name, data in case['private'].items():
        assert (case['player'] / name).read_bytes() == data


def test_launch_uses_external_worker_and_does_not_start_gui(online_case, monkeypatch):
    case = online_case
    archive = case['tmp_path'] / 'synthetic-offline.zip'
    archive.write_bytes(case['blobs'][case['index']['full']['name']])
    original = updater.prepare_worker

    def prepare(player, archive=None, **kwargs):
        return original(player, archive, temp_root=case['tmp_path'] / 'external workers', **kwargs)

    calls = []

    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(pid=456)

    monkeypatch.setattr(updater, 'prepare_worker', prepare)
    result = updater.launch_update(case['player'], archive, parent_pid=123, popen=popen)
    assert result['status'] == 'installer_started'
    assert len(calls) == 1
    assert Path(calls[0][0][0]).name == 'python.exe'
    assert not Path(calls[0][0][0]).is_relative_to(case['player'])
    assert all('MFAAvalonia.exe' not in str(argument) for argument in calls[0][0])
    assert_original(case)


def test_worker_updates_runtime_then_restarts_only_gui(online_case):
    case = online_case
    archive = case['tmp_path'] / 'synthetic-offline.zip'
    archive.write_bytes(case['blobs'][case['index']['full']['name']])
    calls = []

    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(pid=789)

    result = updater.run_worker(case['player'], archive,
        archive_sha256=sha(archive.read_bytes()), process_probe=lambda _: [],
        sleep=lambda _: None, popen=popen)
    assert result['status'] == 'updated'
    assert (case['player'] / maintenance.MANIFEST).read_bytes() == case['target_bytes']
    for name, data in case['new'].items():
        assert (case['player'] / name).read_bytes() == data
    for name, data in case['private'].items():
        assert (case['player'] / name).read_bytes() == data
    assert len(calls) == 1
    assert list(map(str, calls[0][0])) == [str(case['player'] / 'MFAAvalonia.exe'), '--pcr-after-update']
    assert result['game_tasks_started'] is False


def test_worker_rejects_archive_changed_after_handoff_without_launch(online_case):
    case = online_case
    archive = case['tmp_path'] / 'synthetic-offline.zip'
    original = case['blobs'][case['index']['full']['name']]
    archive.write_bytes(original + b'changed-after-validation')
    calls = []
    with pytest.raises(ValueError, match='update_archive_changed'):
        updater.run_worker(case['player'], archive, archive_sha256=sha(original),
            process_probe=lambda _: [], sleep=lambda _: None,
            popen=lambda *args, **kwargs: calls.append(args))
    assert calls == []
    assert_original(case)


def test_worker_refuses_still_running_owner_without_force_kill(online_case):
    case = online_case
    archive = case['tmp_path'] / 'synthetic-offline.zip'
    archive.write_bytes(case['blobs'][case['index']['full']['name']])
    calls = []
    with pytest.raises((ValueError, RuntimeError, TimeoutError)):
        updater.run_worker(case['player'], archive,
            archive_sha256=sha(archive.read_bytes()), process_probe=lambda _: [123],
            sleep=lambda _: None, popen=lambda *args, **kwargs: calls.append(args),
            wait_seconds=0)
    assert calls == []
    assert_original(case)
