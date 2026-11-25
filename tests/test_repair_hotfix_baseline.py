import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import repair_hotfix_baseline as bridge


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def installation(tmp_path):
    player, bundle = tmp_path / '旧版 测试', tmp_path / '修复包'
    player.mkdir()
    (bundle / 'payload').mkdir(parents=True)
    files = {'src/one.py': b'one', 'src/two.py': b'two', 'MFAAvalonia.exe': b'GUI'}
    entries = {}
    for name, original in files.items():
        dest = player / name
        dest.parent.mkdir(exist_ok=True)
        if name.endswith('.py'):
            entries[name] = {'sha256': sha(original), 'hotfix_sha256': [sha(original + b' hotfix')]}
            (bundle / 'payload' / sha(original)).write_bytes(original)
            dest.write_bytes(original + b' hotfix')
        else:
            dest.write_bytes(original)
    manifest = {'layout': 'portable-offline-validation', 'personal_settings_or_ledgers_included': False,
                'files': [{'path': name, 'size': len(data), 'sha256': sha(data)} for name, data in files.items()],
                'update': {'schema_version': 1, 'product': bridge.maintenance.PRODUCT,
                           'revision': 93, 'version': 'v0.2.5'}}
    raw = json.dumps(manifest).encode()
    (player / bridge.maintenance.MANIFEST).write_bytes(raw)
    (bundle / 'repair-data.json').write_text(json.dumps({
        'schema_version': 1, 'baselines': {sha(raw): {'version': 'v0.2.5', 'files': entries}}}),
        encoding='utf-8')
    (player / 'config').mkdir()
    (player / 'config/settings.json').write_bytes(b'private')
    return player, bundle


def repair(case, *, check=False):
    return bridge.repair(*case, process_probe=lambda _: [], check=check)


def test_check_restore_backup_retry_and_private_data(installation):
    player, bundle = installation
    names = ['src/one.py', 'src/two.py']
    manifest = (player / bridge.maintenance.MANIFEST).read_bytes()
    assert repair(installation, check=True)['files'] == names
    assert not (player / '.runtime').exists()
    result = repair(installation)
    backup = Path(result['backup'])
    assert json.loads((backup / 'receipt.json').read_bytes())['manifest_sha256'] == sha(manifest)
    for name in names:
        original = Path(name).stem.encode()
        assert (backup / 'before' / name).read_bytes() == original + b' hotfix'
        assert (player / name).read_bytes() == original
    assert repair(installation)['backup'] is None
    assert (player / bridge.maintenance.MANIFEST).read_bytes() == manifest
    assert (player / 'config/settings.json').read_bytes() == b'private'


@pytest.mark.parametrize('change', ['new_hash', 'missing', 'different_manifest', 'bad_payload', 'pending', 'running'])
def test_refuse_unrecognized_or_active_installation_without_partial_repair(installation, change):
    player, bundle = installation
    expected = 'src/two.py'
    if change == 'new_hash':
        (player / 'src/two.py').write_bytes(b'unknown')
    elif change == 'missing':
        (player / 'src/two.py').unlink()
    elif change == 'different_manifest':
        with (player / bridge.maintenance.MANIFEST).open('ab') as writer:
            writer.write(b' ')
    elif change == 'bad_payload':
        (bundle / 'payload' / sha(b'two')).write_bytes(b'bad')
    elif change == 'pending':
        marker = player / '.runtime/player-maintenance/update-in-progress.json'
        marker.parent.mkdir(parents=True)
        marker.write_bytes(b'{}')
        expected = '未完成的更新'
    else:
        expected = '退出安装目录'
    with pytest.raises(ValueError, match=expected):
        bridge.repair(player, bundle, process_probe=lambda _: change == 'running')
    assert (player / 'src/one.py').read_bytes() == b'one hotfix'


def test_second_replace_failure_rolls_back_first_and_can_retry(installation, monkeypatch):
    player, _ = installation
    replace = bridge.maintenance._replace

    def fail_second(source, destination):
        if destination == player / 'src/two.py':
            raise OSError('simulated file lock')
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(bridge.maintenance, '_replace', fail_second)
        with pytest.raises(RuntimeError, match='已回滚'):
            repair(installation)
    assert (player / 'src/one.py').read_bytes() == b'one hotfix'
    assert len(repair(installation)['files']) == 2


def test_retry_after_interruption_repairs_only_still_modified_files(installation):
    player, _ = installation
    (player / 'src/one.py').write_bytes(b'one')
    assert repair(installation)['files'] == ['src/two.py']
    assert (player / 'src/two.py').read_bytes() == b'two'


def test_does_not_compete_with_old_updater(installation):
    player, _ = installation
    with bridge.updater._worker_lock(player):
        with pytest.raises(ValueError, match='updater_worker_running'):
            repair(installation)
    assert (player / 'src/one.py').read_bytes() == b'one hotfix'


def test_cli_explains_process_probe_timeout(installation, monkeypatch, capsys):
    def timeout(*args, **kwargs):
        raise bridge.subprocess.TimeoutExpired('powershell', 30)

    monkeypatch.setattr(bridge, 'repair', timeout)
    assert bridge.main(['--player', str(installation[0])]) == 1
    assert '检查 Maa-PCR 进程超时' in capsys.readouterr().err
