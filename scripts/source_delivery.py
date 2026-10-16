"""Prepare, verify and package public source without local state or build output."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import zipfile


GAME_IMAGES = frozenset('assets/resource/image/' + name for name in (
    'adventure_title.png', 'event_exhausted_hard.png', 'home_gift.png',
    'home_schedule.png', 'main_entry.png', 'map_title.png', 'scan_check.png',
    'scan_no_rail.png', 'sweep_entry.png',
))
OLD_GUI_IMAGES = frozenset((
    'gui/MFAAvalonia/SukiUI/Content/Images/icons8-file-explorer-new-48.png',
    'gui/MFAAvalonia/SukiUI/OIG.N5o-removebg-preview.png',
    'gui/MFAAvalonia/docs/images/preview.png',
))
EXCLUDED_PARTS = frozenset((
    '.runtime', '.git', '.vs', '.idea', '.cache', 'bin', 'obj', '__pycache__',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', 'node_modules',
    'logs', 'screenshots', 'user-data', 'sessions',
))
SOURCE_ROOTS = frozenset((
    'src', 'scripts', 'assets', 'gui', 'vendor', 'third_party', 'docs',
    'licenses', 'tests', '.github',
))
ROOT_FILES = frozenset((
    'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md',
    'SECURITY.md', 'CHANGELOG.md', 'pyproject.toml', 'requirements.txt',
    'requirements-labyrinth.txt', 'requirements-dev.txt', 'requirements-runtime.lock', 'interface.json',
    'global.json', 'NuGet.Config', 'nuget.config', '.gitignore', '.gitattributes',
    '.editorconfig', '使用说明.md', '说明.txt', '导入补丁包.bat', 'inspect-daily.bat',
    '离线更新 Maa PCR.bat', '导出诊断 Maa PCR.bat', '启动 Maa PCR.bat',
))
GENERATED_LAUNCHERS = frozenset(('离线更新 Maa PCR.bat', '导入补丁包.bat',
                                 '导出诊断 Maa PCR.bat', '启动 Maa PCR.bat'))
DELIVERY_FILES = frozenset((
    'delivery/runtime-downloads.json', 'delivery/acceptance.json',
    'delivery/baselines/r38-package-manifest.json',
    'delivery/baselines/r39-package-manifest.json',
    'delivery/baselines/r41-package-manifest.json',
    'delivery/baselines/r45-package-manifest.json',
    'delivery/baselines/r46-package-manifest.json',
    'delivery/baselines/r49-package-manifest.json',
    'delivery/baselines/r50-package-manifest.json',
    'delivery/baselines/r51-package-manifest.json',
    'delivery/baselines/r52-package-manifest.json',
))
TEXT_SUFFIXES = frozenset((
    '.py', '.cs', '.md', '.txt', '.json', '.toml', '.yml', '.yaml', '.xml',
    '.axaml', '.xaml', '.csproj', '.sln', '.props', '.targets', '.proj',
    '.ps1', '.bat', '.sh', '.config', '.ini', '.rst', '.html', '.css', '.js', '.lock',
))
PRIVATE = re.compile(
    r'(?i)(?:[a-z]:[\\/]+Users[\\/]+[^\\/\s\"\']+|/(?:Users|home)/[^/\s\"\']+|'
    r'https?://[^\s<>\"\']+[?&](?:sig|signature|x-amz-signature|x-goog-signature)=[^\s<>\"\']+|'
    r'\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b|\bAKIA[A-Z0-9]{16}\b)'
)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def relative(value, *, source=True):
    require(isinstance(value, str) and value and '\\' not in value
            and ':' not in value and '\x00' not in value, 'unsafe_relative_path')
    parts = value.split('/')
    require(not value.startswith('/') and all(part not in ('', '.', '..')
            and part == part.rstrip(' .') for part in parts), 'unsafe_relative_path')
    require(re.search(r'[<>\"|?*\x00-\x1f]', value) is None, 'unsafe_relative_path')
    if source:
        require(not any(part.casefold() in EXCLUDED_PARTS for part in parts), 'private_or_build_path')
        require(value.casefold() not in {name.casefold() for name in GAME_IMAGES | OLD_GUI_IMAGES},
                'retired_resource')
    require(not any(re.fullmatch(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)
                    for part in parts), 'windows_device_path')
    return PurePosixPath(value).as_posix()


def plain(path):
    info = Path(path).lstat()
    require(stat.S_ISREG(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
            'source_not_regular_file')


def digest(path):
    plain(path)
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_read(path):
    plain(path)
    return json.loads(Path(path).read_text(encoding='utf-8'))


def row_index(rows):
    require(isinstance(rows, list), 'invalid_source_rows')
    result, folded = {}, set()
    for row in rows:
        require(isinstance(row, dict), 'invalid_source_row')
        name = relative(row.get('path'), source=False)
        require(type(row.get('size')) is int and row['size'] >= 0, 'invalid_source_size')
        require(isinstance(row.get('sha256'), str)
                and re.fullmatch('[a-f0-9]{64}', row['sha256']) is not None, 'invalid_source_sha256')
        require(name.casefold() not in folded, 'duplicate_source_path')
        folded.add(name.casefold())
        result[name] = row
    return result


def checked_row(root, row):
    name = relative(row['path'])
    path = Path(root)
    for part in PurePosixPath(name).parts:
        path = path / part
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, 'st_file_attributes', 0) & 0x400, 'source_link_or_reparse')
    plain(path)
    require(path.stat().st_size == row['size'] and digest(path) == row['sha256'],
            'source_bytes_changed: ' + name)
    return path


def privacy_scan(path, name):
    path = Path(path)
    if path.suffix.casefold() in TEXT_SUFFIXES or path.name.upper().startswith(('LICENSE', 'NOTICE')):
        require(PRIVATE.search(path.read_text(encoding='utf-8-sig')) is None,
                'private_source_content: ' + name)


def source_allowed(name):
    parts = PurePosixPath(name).parts
    return ((len(parts) == 1 and name in ROOT_FILES)
            or name in DELIVERY_FILES
            or (len(parts) > 1 and parts[0] in SOURCE_ROOTS))


def verify(prepared, inventory_sha256):
    prepared = Path(prepared)
    receipt = prepared / 'SOURCE_INVENTORY.json'
    require(digest(receipt) == inventory_sha256, 'source_inventory_changed')
    inventory = json_read(receipt)
    rows = row_index(inventory['files'])
    require(type(inventory.get('file_count')) is int and inventory['file_count'] == len(rows),
            'source_inventory_count')
    require(type(inventory.get('payload_size')) is int
            and inventory['payload_size'] == sum(row['size'] for row in rows.values()),
            'source_inventory_size')
    source = prepared / 'source'
    require(source.is_dir() and not source.is_symlink(), 'source_directory_missing')
    require(not getattr(source.lstat(), 'st_file_attributes', 0) & 0x400, 'source_link_or_reparse')
    actual = set()
    for path in source.rglob('*'):
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, 'st_file_attributes', 0) & 0x400, 'source_link_or_reparse')
        if path.is_file():
            actual.add(relative(path.relative_to(source).as_posix()))
    require(actual == set(rows), 'source_unlisted_or_missing_file')
    for name, row in rows.items():
        require(source_allowed(name) or name in ('delivery/runtime-package-manifest.json',
                                                'delivery/build-source-manifest.json'),
                'source_outside_allowlist: ' + name)
        path = checked_row(source, row)
        privacy_scan(path, name)
    return inventory


def prepare(source, output, revision=None, player_manifest=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if revision is not None:
        require(type(revision) is int and revision > 0, 'invalid_source_revision')
    require(source.is_dir(), 'source_directory_missing')
    require(output != source, 'output_inside_source')
    if source in output.parents:
        require(output.relative_to(source).parts[0] == '.runtime', 'output_inside_source')
    require(not output.exists(), 'prepared_output_exists')
    selected = []
    for path in source.rglob('*'):
        name = path.relative_to(source).as_posix()
        if not source_allowed(name):
            continue
        if any(part.casefold() in EXCLUDED_PARTS for part in PurePosixPath(name).parts):
            continue
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, 'st_file_attributes', 0) & 0x400, 'source_link_or_reparse')
        if not path.is_file():
            continue
        relative(name)
        require(path.suffix.casefold() not in ('.pyc', '.pyo', '.log', '.db', '.sqlite'),
                'non_source_payload: ' + name)
        privacy_scan(path, name)
        selected.append((name, path))
    if player_manifest is not None:
        player_manifest = Path(player_manifest)
        manifest = json_read(player_manifest)
        if revision is not None:
            require(manifest['update']['revision'] == revision, 'source_revision_mismatch')
        owned = row_index(manifest.get('files', []))
        for name in sorted(GENERATED_LAUNCHERS & owned.keys()):
            launcher = checked_row(player_manifest.parent, owned[name])
            selected = [(selected_name, path) for selected_name, path in selected if selected_name != name]
            selected.append((name, launcher))
        selected.append(('delivery/runtime-package-manifest.json', player_manifest))
        build_inputs = player_manifest.with_name('build-source-manifest.json')
        if build_inputs.exists():
            selected.append(('delivery/build-source-manifest.json', build_inputs))
    require(selected, 'empty_public_source')
    rows = [{'path': name, 'size': path.stat().st_size, 'sha256': digest(path),
             'origin': 'public-source-allowlist'} for name, path in sorted(selected)]
    row_index(rows)
    payload = output / 'source'
    payload.mkdir(parents=True)
    for (name, path), row in zip(sorted(selected), rows):
        destination = payload / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        checked_row(payload, row)
    inventory = {'schema_version': 1, 'purpose': 'matching public source',
                 'file_count': len(rows), 'payload_size': sum(row['size'] for row in rows),
                 'files': rows}
    if revision is not None:
        inventory['revision'] = revision
    receipt = output / 'SOURCE_INVENTORY.json'
    receipt.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify(output, digest(receipt))
    return inventory


def refresh_player_snapshot(prepared, inventory_sha256, player_manifest):
    prepared, player_manifest = Path(prepared), Path(player_manifest)
    inventory = verify(prepared, inventory_sha256)
    manifest = json_read(player_manifest)
    require(inventory.get('revision') == manifest['update']['revision'], 'source_revision_mismatch')
    name = 'delivery/runtime-package-manifest.json'
    rows = row_index(inventory['files'])
    require(name in rows, 'source_runtime_snapshot_missing')
    destination = prepared / 'source' / name
    shutil.copyfile(player_manifest, destination)
    rows[name].update(size=destination.stat().st_size, sha256=digest(destination))
    inventory['payload_size'] = sum(row['size'] for row in rows.values())
    receipt = prepared / 'SOURCE_INVENTORY.json'
    receipt.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return verify(prepared, digest(receipt))


def pack(prepared, archive, inventory_sha256):
    prepared, archive = Path(prepared), Path(archive)
    require((prepared / 'source').resolve() not in archive.resolve().parents,
            'archive_inside_source')
    inventory = verify(prepared, inventory_sha256)
    require(not archive.exists(), 'source_archive_exists')
    entries = [(row['path'], prepared / 'source' / row['path'], row)
               for row in inventory['files']]
    receipt = prepared / 'SOURCE_INVENTORY.json'
    entries.append(('SOURCE_INVENTORY.json', receipt,
                    {'size': receipt.stat().st_size, 'sha256': inventory_sha256}))
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as package:
        for name, path, row in sorted(entries):
            data = path.read_bytes()
            require(len(data) == row['size'] and hashlib.sha256(data).hexdigest() == row['sha256'],
                    'source_changed_during_pack')
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, data)
    with zipfile.ZipFile(archive) as package:
        require(package.namelist() == [name for name, _, _ in sorted(entries)], 'source_archive_inventory')
        for name, _, row in entries:
            with package.open(name) as stream:
                require(package.getinfo(name).file_size == row['size']
                        and hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256'],
                        'source_archive_bytes')
    return {'files': len(entries), 'size': archive.stat().st_size, 'sha256': digest(archive)}


def frozen(spec):
    path = Path(spec['manifest'])
    require('sha256' not in spec or digest(path) == spec['sha256'], 'input_manifest_changed')
    return json_read(path), path


def plan_inputs(config):
    for name in ('baseline', 'gui', 'business', 'materials', 'stable', 'business_clone'):
        frozen(config[name])


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('prepare', allow_abbrev=False)
    build.add_argument('--source', type=Path, required=True)
    build.add_argument('--output', type=Path, required=True)
    build.add_argument('--revision', type=int)
    build.add_argument('--player-manifest', type=Path)
    for command in ('verify', 'pack'):
        child = commands.add_parser(command, allow_abbrev=False)
        child.add_argument('--prepared', type=Path, required=True)
        child.add_argument('--inventory-sha256', required=True)
        if command == 'pack':
            child.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.source, args.output, args.revision, args.player_manifest)
        result = {key: value for key, value in result.items() if key != 'files'}
        result['source_inventory_sha256'] = digest(args.output / 'SOURCE_INVENTORY.json')
    elif args.command == 'verify':
        inventory = verify(args.prepared, args.inventory_sha256)
        result = {'passed': True, 'file_count': inventory['file_count'],
                  'payload_size': inventory['payload_size']}
    else:
        result = pack(args.prepared, args.archive, args.inventory_sha256)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
