"""Restore recorded test hotfixes to the exact official bytes before using an old updater."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

BUNDLE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUNDLE / 'src'))
from pcr_maa import player_maintenance as maintenance
from pcr_maa import player_update as updater


def _idle(player):
    marker = maintenance._path(player, '.runtime/player-maintenance/update-in-progress.json', optional=True)
    if marker.exists():
        raise ValueError('安装目录里有未完成的更新。请先用原更新器恢复。')


def _hotfixes(bundle, checksum, version, rows):
    data = maintenance._read_json(maintenance._path(bundle, 'repair-data.json'))
    if (type(data.get('schema_version')) is not int or data['schema_version'] != 1
            or not isinstance(data.get('baselines'), dict)):
        raise ValueError('修复包的版本表无效。')
    baseline = data['baselines'].get(checksum)
    if baseline is None:
        return {}
    if (not isinstance(baseline, dict) or baseline.get('version') != version
            or not isinstance(baseline.get('files'), dict) or not baseline['files']):
        raise ValueError('修复包记录的基线与安装清单不符。')
    for name, entry in baseline['files'].items():
        if (name not in rows or not isinstance(entry, dict) or entry.get('sha256') != rows[name]['sha256']
                or not isinstance(entry.get('hotfix_sha256'), list) or not entry['hotfix_sha256']
                or not all(updater._digest(digest) for digest in entry['hotfix_sha256'])):
            raise ValueError(f'修复包的文件记录无效：{name}')
    return baseline['files']


def _scan(player, rows, hotfixes):
    found, unknown = {}, []
    for name, row in rows.items():
        try:
            path = maintenance._path(player, name)
            if name not in hotfixes and path.stat().st_size != row['size']:
                unknown.append(name)
                continue
            actual = maintenance._sha(path)
        except (OSError, ValueError) as error:
            unknown.append(f'{name} ({error})')
            continue
        if actual != row['sha256']:
            if name in hotfixes and actual in hotfixes[name]['hotfix_sha256']:
                found[name] = actual
            else:
                unknown.append(name)
    if unknown:
        raise ValueError('以下程序文件有未登记的差异，已停止修复：\n' + '\n'.join(unknown))
    return found


def _replace_verified(source, destination, before, after, work):
    temporary = work / ('stage-' + uuid4().hex)
    try:
        shutil.copyfile(source, temporary)
        maintenance._sync_file(temporary)
        if maintenance._sha(temporary) != after or maintenance._sha(destination) != before:
            raise ValueError(f'文件在修复期间发生变化：{destination}')
        maintenance._replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def repair(player, bundle=BUNDLE, *, check=False, process_probe=None):
    player, bundle = maintenance._root(player), maintenance._root(bundle)
    if bundle.is_relative_to(player):
        raise ValueError('请将修复工具解压到安装目录以外。')
    _idle(player)
    manifest = maintenance._path(player, maintenance.MANIFEST)
    if maintenance._plain(manifest).st_size > maintenance.MAX_JSON:
        raise ValueError('安装清单过大。')
    raw = manifest.read_bytes()
    current = maintenance._json(raw)
    rows = maintenance._rows(current)
    version = maintenance._version(current)['version']
    checksum = hashlib.sha256(raw).hexdigest()
    hotfixes = _hotfixes(bundle, checksum, version, rows)
    found = _scan(player, rows, hotfixes)
    result = {'version': version, 'verified_files': len(rows), 'files': list(found), 'backup': None}
    if check or not found:
        return result

    probe = process_probe or maintenance._processes
    with updater._worker_lock(player):
        _idle(player)
        if manifest.read_bytes() != raw:
            raise ValueError('检查后安装版本发生变化，请重新运行。')
        if probe(player):
            raise ValueError('请退出安装目录中的 Maa-PCR 和 Agent 后再运行修复。')
        # Verify all replacement bytes before creating backups or changing program files.
        originals = {name: maintenance._path(bundle, 'payload/' + rows[name]['sha256']) for name in found}
        for name, path in originals.items():
            if path.stat().st_size != rows[name]['size'] or maintenance._sha(path) != rows[name]['sha256']:
                raise ValueError(f'修复包内的原版文件校验失败：{name}')
        directory = '.runtime/player-maintenance/hotfix-backup-' + uuid4().hex
        work = maintenance._path(player, directory + '/receipt.json', optional=True).parent
        work.mkdir()
        # Every backup is complete before the first replacement. After abrupt termination,
        # the next scan accepts the remaining known hotfixes and skips the restored files.
        backups = {}
        for name, digest in found.items():
            destination = maintenance._path(work, 'before/' + name, optional=True)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(maintenance._path(player, name), destination)
            maintenance._sync_file(destination)
            if maintenance._sha(destination) != digest:
                raise ValueError(f'备份时文件发生变化：{name}')
            backups[name] = destination
        maintenance._save(work / 'receipt.json', {
            'version': version, 'manifest_sha256': checksum, 'before_sha256': found,
        }, durable=True)
        _idle(player)
        if probe(player) or manifest.read_bytes() != raw:
            raise ValueError('安装状态在备份期间发生变化，请退出程序后重试。')
        restored = []
        try:
            for name, digest in found.items():
                destination = maintenance._path(player, name)
                _replace_verified(originals[name], destination, digest, rows[name]['sha256'], work)
                restored.append(name)
            maintenance._verify(player, rows)
        except Exception as error:
            for name in reversed(restored):
                try:
                    _replace_verified(backups[name], maintenance._path(player, name),
                                      rows[name]['sha256'], found[name], work)
                except (OSError, ValueError) as rollback_error:
                    raise RuntimeError(f'修复中断，回滚时无法恢复 {name}；备份位于 {work}') from rollback_error
            raise RuntimeError(f'修复未完成。本次已替换的文件已回滚。备份位于 {work}。{error}') from error
        result['backup'] = str(work)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='还原已登记的 Maa-PCR 测试补丁，以便使用原更新器升级。')
    parser.add_argument('--player', type=Path, required=True, help='旧版 Maa-PCR 的安装目录')
    parser.add_argument('--check', action='store_true', help='只检查当前安装')
    args = parser.parse_args(argv)
    try:
        result = repair(args.player, check=args.check)
    except subprocess.TimeoutExpired:
        print('修复停止。检查 Maa-PCR 进程超时，请确认软件已退出后重试。', file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        message = str(error)
        if message == 'updater_worker_running':
            message = '更新器正在使用安装目录，请等它退出后重试。'
        elif message in ('process_inspection_failed', 'process_inspection_incomplete'):
            message = '无法确认 Maa-PCR 进程是否已退出，请检查后重试。'
        print('修复停止。' + message, file=sys.stderr)
        return 1
    print(f"当前版本 {result['version']}，共检查 {result['verified_files']} 个程序文件。")
    if result['files']:
        print('发现的已知测试补丁：\n' + '\n'.join(result['files']))
    if args.check and result['files']:
        print('本次仅检查。如需还原，请运行修复入口。')
    elif result['backup']:
        print(f"已还原测试补丁并复核整份清单。备份位于 {result['backup']}")
        print('请重新打开 Maa-PCR，点击检查更新。')
    else:
        print('程序文件与当前安装清单一致，可直接使用软件内更新。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
