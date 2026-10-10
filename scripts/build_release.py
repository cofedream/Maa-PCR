"""Build a Windows x64 Release candidate using verified dependencies and locked restore.

Committed NuGet locks target Release. Debug has additional diagnostics dependencies;
use a separate Debug lock file, never overwrite the release locks in place.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa import player_maintenance as maintenance

SDK_VERSION = '10.0.101'
R38_MANIFEST_SHA256 = '59fa329ba5ac76de8e51ee10765573a7be737b81228137aa2aaf0cac55726668'
GUI_PROJECT = 'gui/MFAAvalonia/MFAAvalonia.Desktop/MFAAvalonia.Desktop.csproj'
PAYLOAD_DIRS = ('src', 'vendor', 'assets', 'third_party', 'licenses', 'docs')
PAYLOAD_SCRIPTS = ('pcr.py', 'validate-alpha-config.py', 'start-alpha-agent.py',
                   'start-alpha-owned-agent.py', 'start-labyrinth-owned-agent.py',
                   'player-maintenance.py', 'player-update.py', 'recover-player-update.ps1')
IGNORED = {'__pycache__', '.git', '.runtime', '.venv', 'bin', 'obj', 'tests'}
CRT_FILES = ('concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
             'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'vccorlib140.dll',
             'vcruntime140.dll', 'vcruntime140_1.dll', 'vcruntime140_threads.dll')


def digest(path, algorithm='sha256'):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def download(record, cache):
    algorithm = 'sha256' if 'sha256' in record else 'sha512'
    expected = record[algorithm]
    if re.fullmatch('[0-9a-f]{' + str(hashlib.new(algorithm).digest_size * 2) + '}', expected) is None:
        raise ValueError('invalid_download_digest')
    if not record['url'].startswith('https://'):
        raise ValueError('download_requires_https')
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (expected + '.zip')
    if target.exists():
        if digest(target, algorithm) != expected:
            raise ValueError('download_cache_hash_mismatch')
        return target
    partial = target.with_suffix('.partial')
    with urllib.request.urlopen(record['url'], timeout=60) as source, partial.open('xb') as output:
        if not source.url.startswith('https://'):
            raise ValueError('download_redirect_requires_https')
        copied = 0
        while chunk := source.read(1024 * 1024):
            copied += len(chunk)
            if copied > 1024 * 1024 * 1024:
                raise ValueError('download_too_large')
            output.write(chunk)
    if digest(partial, algorithm) != expected:
        raise ValueError('download_hash_mismatch')
    partial.rename(target)
    return target


def extract(archive, target, *, wheel=False):
    """Never trust ZIP paths, symlinks or case collisions, including upstream wheels."""
    entries, folded = [], set()
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            if info.is_dir():
                continue
            name = info.orig_filename
            if name != info.filename:
                raise ValueError('unsafe_dependency_archive')
            if wheel and '.data/' in name:
                parts = name.split('/')
                if len(parts) < 3 or parts[1] not in ('purelib', 'platlib'):
                    continue
                name = '/'.join(parts[2:])
            maintenance._name(name)
            if any(ord(character) < 32 for character in name):
                raise ValueError('unsafe_dependency_archive')
            if name.casefold() in folded or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('unsafe_dependency_archive')
            folded.add(name.casefold())
            entries.append((name, info))
        if sum(info.file_size for _, info in entries) > maintenance.MAX_PAYLOAD:
            raise ValueError('dependency_archive_too_large')
        for name, info in entries:
            output = target / name
            if output.exists():
                if output.read_bytes() != source.read(info):
                    raise ValueError('dependency_file_collision: ' + name)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as reader, output.open('xb') as writer:
                shutil.copyfileobj(reader, writer)


def copy_tree(source, target, *, ignore=IGNORED):
    source = Path(source)
    snapshot = {}
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if any(part in ignore for part in relative.parts) or path.suffix in ('.pyc', '.pdb'):
            continue
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('nonregular_build_input')
        if path.is_file():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError('build_file_collision: ' + relative.as_posix())
            expected = digest(path)
            shutil.copyfile(path, destination)
            if digest(destination) != expected or digest(path) != expected:
                raise ValueError('source_changed_during_copy')
            snapshot[relative.as_posix()] = expected
    return snapshot


def run(command, *, cwd, log, extra_env=None):
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1',
               DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', MSBUILDDISABLENODEREUSE='1',
               PYTHONDONTWRITEBYTECODE='1')
    env.update(extra_env or {})
    with log.open('x', encoding='utf-8') as stream:
        completed = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, timeout=1800)
    if completed.returncode:
        raise ValueError('build_command_failed: ' + str(log))


def find_crt(explicit=None):
    if explicit:
        return Path(explicit).resolve(strict=True)
    vswhere = Path(os.environ.get('ProgramFiles(x86)', '')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    if vswhere.is_file():
        home = subprocess.check_output([str(vswhere), '-latest', '-products', '*',
            '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property',
            'installationPath'], text=True, encoding='utf-8').strip()
        candidates = sorted((Path(home) / 'VC/Redist/MSVC').glob('*/x64/Microsoft.VC*.CRT'))
        for candidate in reversed(candidates):
            if all((candidate / name).is_file() for name in CRT_FILES):
                return candidate
    raise ValueError('vc_redist_required: pass --vc-redist with the official x64 CRT directory')


def copy_locked_wheels(wheelhouse, lock, output):
    hashes = set()
    for line in lock.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line.strip() in ('--index-url https://pypi.org/simple', '--only-binary=:all:', '--require-hashes'):
            continue
        match = re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+ --hash=sha256:([0-9a-f]{64})', line.strip())
        if match is None or match[1] in hashes:
            raise ValueError('invalid_runtime_wheel_lock')
        hashes.add(match[1])
    selected = {}
    for wheel in Path(wheelhouse).glob('*.whl'):
        maintenance._plain(wheel)
        actual = digest(wheel)
        if actual in hashes:
            if actual in selected:
                raise ValueError('duplicate_locked_wheel')
            selected[actual] = wheel
    if not hashes or set(selected) != hashes:
        raise ValueError('locked_wheel_missing_or_mismatched')
    output.mkdir()
    for wheel in selected.values():
        shutil.copyfile(wheel, output / wheel.name)


def rows(player):
    result = []
    for path in sorted(player.rglob('*')):
        if path.is_file():
            name = path.relative_to(player).as_posix()
            maintenance._name(name)
            maintenance._plain(path)
            result.append({'path': name, 'size': path.stat().st_size, 'sha256': digest(path)})
    return result


def require_payload(player, metadata):
    digits = ''.join(metadata['python']['version'].split('.')[:2])
    runtime = metadata['dotnet']['version']
    required = {'MFAAvalonia.exe', 'MFAAvalonia.dll', 'libs/MFAAvalonia.Core.dll',
        'MFAAvalonia.deps.json', 'MFAAvalonia.runtimeconfig.json', 'interface.json',
        'pcr-repository.txt', '启动 Maa PCR.bat', 'runtime/python/python.exe', 'runtime/python/python' + digits + '.dll',
        'runtime/python/python' + digits + '.zip', 'runtime/python/python' + digits + '._pth',
        'runtime/dotnet/dotnet.exe', 'runtime/dotnet/host/fxr/' + runtime + '/hostfxr.dll',
        'src/pcr_maa/agent_entry.py', 'src/pcr_maa/labyrinth/owned_runtime.py',
        'vendor/autopcr_sync/autopcr_sync/__init__.py',
        'assets/labyrinth/protocol-version.json', 'assets/labyrinth/event-recognition.json',
        'assets/labyrinth/labyrinth-event-ocr.json', 'assets/labyrinth/labyrinth-role-decision.json',
        'assets/labyrinth/scoring-defaults.json', 'assets/labyrinth/character-pack/icons.json',
        'assets/labyrinth/character-pack/characters.landosolroster.json',
        'docs/RELEASE_STATUS.md', 'docs/SOURCE_AND_LICENSES.md', 'docs/LABYRINTH_INTEGRATION.md',
        'docs/LABYRINTH_SOURCES.json',
        'assets/resource/pipeline/alpha_entry.json', 'assets/resource/model/ocr/det.onnx',
        'assets/resource/model/ocr/rec.onnx', 'assets/resource/model/ocr/keys.txt',
        *(('scripts/' + name) for name in PAYLOAD_SCRIPTS),
        *(('runtimes/win-x64/native/' + name) for name in ('MaaFramework.dll', 'MaaToolkit.dll',
            'MaaAgentClient.dll', 'MaaAdbControlUnit.dll', 'MaaUtils.dll', 'onnxruntime_maa.dll',
            'fastdeploy_ppocr_maa.dll', 'opencv_world4_maa.dll'))}
    missing = sorted(name for name in required if not (player / name).is_file())
    if missing:
        raise ValueError('release_payload_missing: ' + ', '.join(missing))
    source_icons = ROOT / 'assets/labyrinth/character-pack/icons'
    for path in source_icons.rglob('*'):
        if path.is_file():
            copied = player / path.relative_to(ROOT)
            if not copied.is_file() or digest(copied) != digest(path):
                raise ValueError('labyrinth_character_icon_missing_or_changed')


def archive_player(player, output):
    player = maintenance._root(player)
    raw = (player / maintenance.MANIFEST).read_bytes()
    manifest = maintenance._json(raw)
    owned = maintenance._rows(manifest)
    maintenance._verify(player, owned)
    if output.exists():
        raise ValueError('output_exists')
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name in sorted(owned):
            package.write(maintenance._path(player, name), name)
        package.writestr(maintenance.MANIFEST, raw)
    with zipfile.ZipFile(output) as package:
        if set(package.namelist()) != set(owned) | {maintenance.MANIFEST} or package.testzip():
            raise ValueError('archive_inventory_mismatch')
        for name, row in owned.items():
            with package.open(name) as stream:
                sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            if package.getinfo(name).file_size != row['size'] or sha != row['sha256']:
                raise ValueError('archive_payload_mismatch')
    maintenance._verify(player, owned)
    if (player / maintenance.MANIFEST).read_bytes() != raw:
        raise ValueError('archive_manifest_changed')
    return asset_record(output)


def asset_record(path):
    return {'name': Path(path).name, 'size': Path(path).stat().st_size, 'sha256': digest(path)}


def update_index(player, full, patches=()):
    raw = (player / maintenance.MANIFEST).read_bytes()
    version = maintenance._version(maintenance._json(raw))
    if re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version['version']) is None:
        raise ValueError('release_version_requires_semver_tag')
    return {'schema_version': 1, 'product': maintenance.PRODUCT, 'platform': 'win-x64',
            'tag': version['version'], 'version': version['version'],
            'revision': version['revision'], 'target_manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'full': asset_record(full), 'patches': list(patches)}


def package_outputs(player, output, base=None, base_manifest=None):
    manifest = maintenance._read_json(player / maintenance.MANIFEST)
    version = maintenance._version(manifest)
    full = output / ('Maa-PCR-' + version['version'] + '-win-x64.zip')
    archive_player(player, full)
    patches = []
    bootstrap = None
    if base or base_manifest:
        spec = importlib.util.spec_from_file_location('patch_builder', ROOT / 'scripts/build_pcr_player_patch.py')
        patcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patcher)
        base_path = Path(base) / maintenance.MANIFEST if base else Path(base_manifest)
        base_version = maintenance._version(maintenance._read_json(base_path))
        patch = output / ('Maa-PCR-R' + str(base_version['revision']) + '-to-R' + str(version['revision']) + '.patch.zip')
        if base:
            result = patcher.build_patch(base, player, patch)
        else:
            result = patcher.build_patch_from_manifest(base_path, player, patch, R38_MANIFEST_SHA256)
        patches.append(dict(asset_record(patch), base_revision=result['base_revision'],
                            base_manifest_sha256=result['base_manifest_sha256']))
        if base_version['revision'] == 38 and version['revision'] == 39:
            bootstrap = build_bootstrap_patch(base_path, player, patch,
                                              output / 'Maa-PCR-R38-to-R39-bootstrap.zip')
    save(output / 'update-index.json', update_index(player, full, patches))
    result = {'full': asset_record(full), 'patches': patches}
    if bootstrap is not None:
        result['bootstrap'] = bootstrap
    return result


def build_bootstrap_patch(base_manifest, player, patch, output):
    """R38's embedded interpreter must leave the install before changing itself."""
    old = maintenance._read_json(base_manifest)
    target = maintenance._read_json(player / maintenance.MANIFEST)
    if maintenance._version(old)['revision'] != 38 or maintenance._version(target)['revision'] != 39:
        raise ValueError('bootstrap_requires_R38_to_R39')
    if digest(base_manifest) != R38_MANIFEST_SHA256:
        raise ValueError('bootstrap_requires_frozen_R38_manifest')
    helper = ROOT / 'scripts/bootstrap-r38-update.py'
    if not helper.is_file():
        raise ValueError('bootstrap_helper_missing')
    metadata = {'schema_version': 1, 'product': maintenance.PRODUCT,
        'base_revision': 38, 'base_manifest_sha256': digest(base_manifest),
        'target_revision': 39, 'target_manifest_sha256': digest(player / maintenance.MANIFEST),
        'archive': asset_record(patch)}
    launcher = ('@echo off\r\nsetlocal DisableDelayedExpansion\r\n'
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0first-update.ps1"\r\n'
        'set "RESULT=%errorlevel%"\r\npause\r\nexit /b %RESULT%\r\n')
    powershell = ("$ErrorActionPreference='Stop'\n"
        "$player=Read-Host 'Maa-PCR folder'\n"
        "$python=Join-Path $player 'runtime/python/python.exe'\n"
        "if(-not(Test-Path -LiteralPath $python -PathType Leaf)){throw 'Invalid player folder'}\n"
        "& $python -X utf8 -B (Join-Path $PSScriptRoot 'bootstrap-r38-update.py') --player $player "
        "--archive (Join-Path $PSScriptRoot '" + patch.name + "') "
        "--metadata (Join-Path $PSScriptRoot 'bootstrap-manifest.json')\nexit $LASTEXITCODE\n")
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(patch, patch.name)
        archive.write(helper, helper.name)
        archive.writestr('bootstrap-manifest.json', json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
        archive.writestr('first-update.bat', launcher.encode('ascii'))
        archive.writestr('first-update.ps1', powershell.encode('ascii'))
    return asset_record(output)


def build(args):
    if os.name != 'nt':
        raise ValueError('build_requires_windows_x64')
    if type(args.revision) is not int or args.revision < 1:
        raise ValueError('invalid_revision')
    if re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', args.version) is None:
        raise ValueError('release_version_requires_semver_tag')
    output = Path(args.output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / '.runtime'):
        raise ValueError('new_output_must_be_inside_checkout_runtime')
    dotnet = args.dotnet or shutil.which('dotnet')
    if not dotnet or subprocess.check_output([str(dotnet), '--version'], cwd=ROOT, text=True).strip() != SDK_VERSION:
        raise ValueError('dotnet_sdk_requires_' + SDK_VERSION)
    crt = find_crt(args.vc_redist)
    spec = importlib.util.spec_from_file_location('candidate_builder', ROOT / 'scripts/build_pcr_player_candidate.py')
    candidate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidate)
    crt_paths = candidate.vc_redist_files(crt)
    metadata = json.loads((ROOT / 'delivery/runtime-downloads.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True)
    app, player = output / 'app', output / 'player'
    workspace = output / 'build-source'
    copy_tree(ROOT / 'gui', workspace / 'gui')
    gui_snapshot = {path.relative_to(workspace).as_posix(): digest(path)
                    for path in workspace.rglob('*') if path.is_file()}
    command = [str(dotnet), 'publish', str(workspace / GUI_PROJECT), '-c', 'Release', '-r', 'win-x64',
         '-p:Platform=x64',
         '--self-contained', 'false', '-o', str(app), '-p:AppHostDotNetSearch=AppRelative',
         '-p:AppHostRelativeDotNet=runtime/dotnet', '-p:RestorePackagesWithLockFile=true',
         '-p:RestoreLockedMode=true',
         '-p:UsedAvaloniaProducts=']
    if args.nuget_source:
        command += ['-p:RestoreSources=' + str(args.nuget_source.resolve()), '-p:NuGetAudit=false']
    extra_env = {'NUGET_PACKAGES': str(args.nuget_cache.resolve())} if args.nuget_cache else {}
    run(command, cwd=ROOT, log=output / 'gui-build.log', extra_env=extra_env)
    copy_tree(app, player, ignore={'config', 'logs', 'resource', 'debug', 'temp', '__pycache__'})
    cache = ROOT / '.runtime/downloads'
    extract(download(metadata['python'], cache), player / 'runtime/python')
    extract(download(metadata['dotnet'], cache), player / 'runtime/dotnet')
    wheels = output / 'wheels'
    command = [sys.executable, '-m', 'pip', 'download', '--require-hashes', '--no-deps', '--only-binary=:all:',
         '--platform', 'win_amd64', '--python-version', '313', '--implementation', 'cp', '--abi', 'cp313',
         '-r', str(ROOT / 'requirements-runtime.lock'), '-d', str(wheels)]
    if args.wheelhouse:
        copy_locked_wheels(args.wheelhouse.resolve(), ROOT / 'requirements-runtime.lock', wheels)
        save(output / 'python-dependencies.json', {'status': 'hash_locked_offline_copy',
                                                  'wheels': len(list(wheels.glob('*.whl')))})
    else:
        run(command, cwd=ROOT, log=output / 'python-dependencies.log')
    site = player / 'runtime/python/Lib/site-packages'
    for wheel in sorted(wheels.glob('*.whl')):
        extract(wheel, site, wheel=True)
    version_digits = ''.join(metadata['python']['version'].split('.')[:2])
    (player / ('runtime/python/python' + version_digits + '._pth')).write_text(
        'python' + version_digits + '.zip\n.\nLib/site-packages\n', encoding='utf-8')
    # Both owners load the same locked Maa native distribution, not an older GUI snapshot.
    native = player / 'runtimes/win-x64/native'
    native.mkdir(parents=True, exist_ok=True)
    for path in (site / 'maa/bin').rglob('*'):
        if path.is_file():
            destination = native / path.relative_to(site / 'maa/bin')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
    for path in crt_paths:
        shutil.copyfile(path, native / path.name)
        shutil.copyfile(path, player / 'runtime/python' / path.name)
    source_snapshot = {}
    for name in PAYLOAD_DIRS:
        source_snapshot.update({name + '/' + relative: sha for relative, sha
                                in copy_tree(ROOT / name, player / name).items()})
    for name in PAYLOAD_SCRIPTS:
        source = ROOT / 'scripts' / name
        if not source.is_file():
            raise ValueError('release_script_missing: ' + name)
        destination = player / 'scripts' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        source_snapshot['scripts/' + name] = digest(source)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'CONTRIBUTING.md', '使用说明.md', 'interface.json'):
        shutil.copyfile(ROOT / name, player / name)
        source_snapshot[name] = digest(ROOT / name)
    (player / 'pcr-repository.txt').write_text('.', encoding='utf-8')
    (player / '启动 Maa PCR.bat').write_bytes(candidate.startup_launcher().encode('utf-8'))
    for name, content in candidate.maintenance_launchers().items():
        (player / name).write_bytes(content.encode('utf-8'))
    if any(not (ROOT / name).is_file() or digest(ROOT / name) != expected
           for name, expected in gui_snapshot.items()):
        raise ValueError('gui_source_changed_during_build')
    if any(not (ROOT / name).is_file() or digest(ROOT / name) != expected
           or digest(player / name) != expected for name, expected in source_snapshot.items()):
        raise ValueError('payload_source_changed_during_build')
    require_payload(player, metadata)
    save(player / 'build-source-manifest.json', {
        'schema_version': 1, 'purpose': 'GUI build input source binding',
        'bit_for_bit_rebuild_claimed': False,
        'files': [{'path': name, 'size': (ROOT / name).stat().st_size, 'sha256': sha}
                  for name, sha in sorted(gui_snapshot.items())],
    })
    manifest = {'status': 'release_staging', 'distribution_ready': False,
        'layout': 'portable-offline-validation', 'personal_settings_or_ledgers_included': False,
        'update': {'schema_version': 1, 'product': maintenance.PRODUCT,
                   'revision': args.revision, 'version': args.version},
        'python_version': metadata['python']['version'], 'dotnet_runtime': metadata['dotnet']['version'],
        'native_entry': {'dotnet_search': 'app-relative-only', 'relative_runtime': 'runtime/dotnet'},
        'build_dependencies': metadata, 'vc_redist_files': [asset_record(path) for path in crt_paths],
        'blockers': ['offline_and_live_acceptance_pending'], 'files': rows(player)}
    save(player / maintenance.MANIFEST, manifest)
    maintenance._verify(player, maintenance._rows(manifest))
    from source_delivery import prepare, pack
    from source_gate import gate
    prepared = output / 'matching-source'
    prepare(ROOT, prepared, revision=args.revision, player_manifest=player / maintenance.MANIFEST)
    inventory_sha = digest(prepared / 'SOURCE_INVENTORY.json')
    source_alignment = gate(prepared, inventory_sha, player)
    save(output / 'SOURCE_ALIGNMENT.json', source_alignment)
    source_archive = output / ('Maa-PCR-' + args.version + '-source.zip')
    pack(prepared, source_archive, inventory_sha)
    outputs = package_outputs(player, output, args.base, args.base_manifest)
    result = {'status': 'release_staging', 'distribution_ready': False,
              'revision': args.revision, 'version': args.version, 'owned_files': len(manifest['files']),
              'personal_files_included': 0, 'matching_source': asset_record(source_archive),
              'source_inventory_sha256': inventory_sha, **outputs}
    save(output / 'BUILD_RECEIPT.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--revision', type=int, default=39)
    parser.add_argument('--version', default='v0.1.0')
    parser.add_argument('--dotnet', type=Path)
    parser.add_argument('--vc-redist', type=Path)
    parser.add_argument('--nuget-source', type=Path, help='optional official package cache/feed for offline builds')
    parser.add_argument('--nuget-cache', type=Path, help='optional isolated NuGet global package directory')
    parser.add_argument('--wheelhouse', type=Path, help='optional directory of hash-locked official wheels')
    baseline = parser.add_mutually_exclusive_group()
    baseline.add_argument('--base', type=Path, help='immutable exact manifest-bound patch baseline')
    baseline.add_argument('--base-manifest', type=Path, help='exact public R38 manifest; installation verifies old files')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build(args), ensure_ascii=False))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
