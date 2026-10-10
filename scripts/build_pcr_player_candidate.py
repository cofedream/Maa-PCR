"""Offline local candidate using installed runtimes; no installation or publication."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import subprocess
import os
import re
from uuid import uuid4
import zipfile
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
SDK = Path(os.environ.get('DOTNET_ROOT') or Path(shutil.which('dotnet') or 'dotnet').absolute().parent)
RUNTIME_VERSION = '10.0.1'
REQUIRED_PACKAGES = ('maafw', 'numpy', 'opencv-python-headless', 'pillow', 'jsonschema')
PRIVATE_PAYLOAD_PARTS = {'.runtime', '.venv', '.git', 'pcr-alpha-v1', '.pcr-alpha-v1',
    'ledger', 'ledgers', 'evidence', 'screenshot', 'screenshots'}
VC_CRT_FILES = ('concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
    'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'vccorlib140.dll',
    'vcruntime140.dll', 'vcruntime140_1.dll', 'vcruntime140_threads.dll')

# This builder fixes Windows x64, Python 3.11 and .NET 10.0.1. Check the files
# needed to start that layout before creating output, including for ZIP exports.
GENERATED_PLAYER_FILES = frozenset({
    'MFAAvalonia.exe', 'interface.json', 'pcr-repository.txt',
    'runtime/python/python311._pth', '启动 Maa PCR.bat', '说明.txt',
    '离线更新 Maa PCR.bat', '导入补丁包.bat', '导出诊断 Maa PCR.bat',
})
REQUIRED_PLAYER_FILES = frozenset({
    *GENERATED_PLAYER_FILES, '使用说明.md', 'docs/OFFLINE_UPDATES.md',
    'MFAAvalonia.dll', 'MFAAvalonia.Core.dll',
    'MFAAvalonia.deps.json', 'MFAAvalonia.runtimeconfig.json',
    'scripts/validate-alpha-config.py', 'scripts/start-alpha-owned-agent.py',
    'scripts/player-maintenance.py', 'src/pcr_maa/player_maintenance.py',
    'src/pcr_maa/agent_entry.py', 'assets/resource/pipeline/alpha_entry.json',
    'assets/resource/model/ocr/det.onnx', 'assets/resource/model/ocr/rec.onnx',
    'assets/resource/model/ocr/keys.txt',
    'runtime/python/python.exe', 'runtime/python/python311.dll',
    'runtime/python/Lib/encodings/__init__.py', 'runtime/python/Lib/json/__init__.py',
    'runtime/dotnet/dotnet.exe',
    f'runtime/dotnet/host/fxr/{RUNTIME_VERSION}/hostfxr.dll',
    *(f'runtime/dotnet/shared/Microsoft.NETCore.App/{RUNTIME_VERSION}/'+name
      for name in ('coreclr.dll', 'hostpolicy.dll', 'System.Private.CoreLib.dll')),
    *('runtimes/win-x64/native/'+name for name in (
        'MaaFramework.dll', 'MaaToolkit.dll', 'MaaAgentClient.dll',
        'MaaAdbControlUnit.dll', 'MaaUtils.dll', 'onnxruntime_maa.dll',
        'fastdeploy_ppocr_maa.dll', 'opencv_world4_maa.dll')),
    *(directory+'/'+name for directory in ('runtimes/win-x64/native', 'runtime/python')
      for name in VC_CRT_FILES),
})


def require_player_payload(paths):
    missing = sorted(REQUIRED_PLAYER_FILES.difference(paths))
    if missing:
        raise ValueError('player_payload_missing: ' + ', '.join(missing))


def vc_redist_files(directory):
    """Validate an explicit CRT source without loading or executing its DLLs."""
    if directory is None:
        raise ValueError('vc_redist_source_required')
    directory = Path(directory)
    sources = []
    for name in VC_CRT_FILES:
        source = directory/name
        try:
            info = source.lstat()
        except FileNotFoundError as error:
            raise ValueError('vc_redist_file_missing: ' + name) from error
        if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('nonregular_vc_redist_input: ' + name)
        with source.open('rb') as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b'MZ':
                raise ValueError('invalid_vc_redist_pe: ' + name)
            pe_offset = int.from_bytes(header[60:64], 'little')
            if pe_offset < 64 or pe_offset > info.st_size - 6:
                raise ValueError('invalid_vc_redist_pe: ' + name)
            stream.seek(pe_offset)
            pe = stream.read(6)
        if len(pe) != 6 or pe[:4] != b'PE\x00\x00':
            raise ValueError('invalid_vc_redist_pe: ' + name)
        if int.from_bytes(pe[4:6], 'little') != 0x8664:
            raise ValueError('vc_redist_requires_x64: ' + name)
        sources.append(source)
    return sources


def runtime_distributions(site):
    available = {canonicalize_name(d.metadata['Name']): d for d in metadata.distributions(path=[str(site)])}
    selected = {}
    todo = list(REQUIRED_PACKAGES)
    while todo:
        name = canonicalize_name(todo.pop())
        if name in selected:
            continue
        if name not in available:
            raise ValueError('installed_runtime_dependency_missing: ' + name)
        distribution = available[name]
        selected[name] = distribution
        for text in distribution.requires or ():
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate({'extra': ''}):
                continue
            dependency = canonicalize_name(requirement.name)
            if dependency not in available or available[dependency].version not in requirement.specifier:
                raise ValueError('installed_runtime_dependency_mismatch: ' + text)
            todo.append(dependency)
    return selected



def create_native_entry(output):
    """Use the SDK apphost task; the player executable searches its bundled runtime."""
    output=Path(output).resolve()
    project=ROOT/'scripts/create_pcr_apphost.proj'
    tasks=SDK/'sdk/10.0.101/Sdks/Microsoft.NET.Sdk/tools/net10.0/Microsoft.NET.Build.Tasks.dll'
    template=SDK/'packs/Microsoft.NETCore.App.Host.win-x64'/RUNTIME_VERSION/'runtimes/win-x64/native/apphost.exe'
    for path in (project,tasks,template,output/'MFAAvalonia.dll'):
        if not path.is_file():
            raise ValueError('native_entry_input_missing: '+str(path))
    logs=ROOT/'.runtime/player-candidate-build'/uuid4().hex
    logs.mkdir(parents=True)
    env=dict(os.environ,DOTNET_CLI_TELEMETRY_OPTOUT='1',DOTNET_NOLOGO='1',
        DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',DOTNET_CLI_HOME=str(logs/'dotnet-home'),
        MSBUILDDISABLENODEREUSE='1')
    command=[str(SDK/'dotnet.exe'),'msbuild',str(project),'/t:CreateEntry','/nologo','/v:minimal',
        '/p:SdkTaskAssembly='+str(tasks),'/p:HostTemplate='+str(template),
        '/p:EntryOutput='+str(output/'MFAAvalonia.exe'),'/p:GuiAssembly='+str(output/'MFAAvalonia.dll')]
    with (logs/'apphost.log').open('w',encoding='utf-8') as stream:
        result=subprocess.run(command,cwd=logs,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=30)
    (logs/'apphost-command.json').write_text(json.dumps(dict(command=command,exit=result.returncode),
        ensure_ascii=False,indent=2),encoding='utf-8')
    if result.returncode:
        raise ValueError('native_entry_creation_failed: '+str(logs/'apphost.log'))
    return logs


def player_notes(bind_checkout=False):
    return ('Maa PCR 本机测试候选\n\n'
        '双击“MFAAvalonia.exe”，使用步骤见同目录“使用说明.md”。\n'
        + ('本机入口绑定原工程，继续使用原账本和运行环境。\n' if bind_checkout else
           '本目录仅用于隔离迁移验收，可编辑保存设置。\n'
           '真实设备业务请使用经核验的原工程绑定入口；不要在此目录启动真实账户任务。\n')
        + '本包尚未取得小范围分发资格，不要上传或转发。\n')


def export_archive(package, archive):
    """Archive the verified payload only; runtime settings stay in the source folder."""
    package, archive = Path(package).absolute(), Path(archive).absolute()
    if archive.exists() or archive.is_symlink():
        raise ValueError('output_exists')
    if not package.is_relative_to(ROOT/'.runtime'):
        raise ValueError('package_outside_project_runtime')

    def require_plain_path(path):
        leaf_info = None
        for node in (path, *path.parents):
            info = node.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('nonregular_archive_path: '+str(node))
            if leaf_info is None: leaf_info = info
            if node == ROOT: break
        return leaf_info

    require_plain_path(package)
    package = package.resolve()
    if not package.is_relative_to(ROOT/'.runtime'):
        raise ValueError('package_outside_project_runtime')
    if archive.is_relative_to(package):
        raise ValueError('archive_inside_package')
    if not archive.parent.is_dir() or archive.parent.resolve() != archive.parent:
        raise ValueError('output_parent_missing_or_redirected')
    manifest_path = package/'package-manifest.json'
    info = require_plain_path(manifest_path)
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('nonregular_archive_manifest')
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if (manifest.get('layout') != 'portable-offline-validation'
            or manifest.get('distribution_ready') is not False
            or manifest.get('personal_settings_or_ledgers_included') is not False):
        raise ValueError('archive_requires_private_portable_candidate')
    private_roots = {'.runtime', '.venv', '.git', 'config', 'logs', 'backup',
        'debug', 'temp', 'pcr-alpha-v1', '.pcr-alpha-v1', 'resource'}
    entries = {}; case_names = set()
    for row in manifest['files']:
        name = row['path']; relative = PurePosixPath(name)
        if (not name or not relative.parts or relative.is_absolute() or '..' in relative.parts
                or name != relative.as_posix() or '\\' in name or ':' in name):
            raise ValueError('invalid_archive_path: '+name)
        if (relative.parts[0].lower() in private_roots
                or any(part.casefold() in PRIVATE_PAYLOAD_PARTS for part in relative.parts)):
            raise ValueError('personal_runtime_path: '+name)
        if name.casefold() in case_names or name.casefold() == 'package-manifest.json':
            raise ValueError('duplicate_archive_path: '+name)
        case_names.add(name.casefold()); entries[name] = row
    require_player_payload(entries)
    def verify_inputs():
        if manifest_path.read_bytes() != manifest_bytes:
            raise ValueError('archive_manifest_changed')
        for name, row in entries.items():
            path = package/name
            info = require_plain_path(path)
            if (not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                    or not path.resolve().is_relative_to(package)):
                raise ValueError('nonregular_archive_input: '+name)
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if info.st_size != row['size'] or digest != row['sha256']:
                raise ValueError('archive_input_changed: '+name)
    verify_inputs()
    if (package/'pcr-repository.txt').read_text(encoding='utf-8').strip() != '.':
        raise ValueError('archive_checkout_binding_not_portable')
    # Keep a failed partial separate, and never overwrite a user's prior archive.
    temporary = archive.with_name(archive.name+'.partial-'+uuid4().hex)
    with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for name in sorted(entries):
            target.write(package/name, name)
        target.writestr('package-manifest.json', manifest_bytes)
    verify_inputs()
    with zipfile.ZipFile(temporary) as target:
        if set(target.namelist()) != set(entries) | {'package-manifest.json'}:
            raise ValueError('archive_inventory_mismatch')
        for name, row in entries.items():
            with target.open(name) as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if target.getinfo(name).file_size != row['size'] or digest != row['sha256']:
                raise ValueError('archive_payload_mismatch: '+name)
        if target.read('package-manifest.json') != manifest_bytes:
            raise ValueError('archive_manifest_mismatch')
    verify_inputs()
    # The temporary and final names share a parent/volume. Linking claims the
    # final path atomically without replacing a file that appeared meanwhile.
    try:
        os.link(temporary, archive)
    except FileExistsError as error:
        raise ValueError('output_exists') from error
    temporary.unlink()
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(status='local_candidate_archive', distribution_ready=False,
        archive=str(archive), files=len(entries)+1, bytes=archive.stat().st_size,
        sha256=digest, personal_settings_or_ledgers_included=False)


def maintenance_launchers():
    result = {}
    for name, command in (('离线更新 Maa PCR.bat', 'update'), ('导入补丁包.bat', 'update'),
                          ('导出诊断 Maa PCR.bat', 'diagnostics')):
        result[name] = ('@echo off\r\nsetlocal DisableDelayedExpansion\r\n'
            'set "PYTHONDONTWRITEBYTECODE=1"\r\ncd /d "%~dp0"\r\n'
            '"%~dp0runtime\\python\\python.exe" -X utf8 -B '
            '"%~dp0scripts\\player-maintenance.py" ' + command + ' --player "%~dp0." %*\r\n'
            'set "PCR_MAINTENANCE_EXIT=%errorlevel%"\r\npause\r\nexit /b %PCR_MAINTENANCE_EXIT%\r\n')
    return result


def startup_launcher():
    return ('@echo off\r\nsetlocal DisableDelayedExpansion\r\n'
            'set "DOTNET_ROOT=%~dp0runtime\\dotnet"\r\n'
            'set "DOTNET_ROOT_X64=%~dp0runtime\\dotnet"\r\n'
            'set "PYTHONDONTWRITEBYTECODE=1"\r\ncd /d "%~dp0"\r\n'
            '"%~dp0MFAAvalonia.exe" %*\r\nexit /b %errorlevel%\r\n')


def build(app, output, *, bind_checkout=False, vc_redist=None, revision=8, player_version='r8-pilot-candidate'):
    if (type(revision) is not int or revision < 1 or type(player_version) is not str
            or re.fullmatch('[A-Za-z0-9][A-Za-z0-9._+-]{0,95}', player_version) is None):
        raise ValueError('invalid_player_update_version')
    app, output = Path(app).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('output_exists')
    if not app.is_relative_to(ROOT/'.runtime') or app.name != 'app':
        raise ValueError('gui_candidate_outside_project_runtime')
    if output.parent.resolve() != output.parent or not output.parent.is_dir():
        raise ValueError('output_parent_missing_or_redirected')
    for name in ('MFAAvalonia.exe', 'MFAAvalonia.dll', 'MFAAvalonia.Core.dll', 'MFAAvalonia.deps.json', 'MFAAvalonia.runtimeconfig.json'):
        if not (app/name).is_file():
            raise ValueError('gui_payload_missing: ' + name)
    # Require the complete current GUI, rather than relabel an old app snapshot.
    fingerprints = json.loads((app.parent/'fingerprints.json').read_text(encoding='utf-8'))
    normalized = {}
    for name, expected in fingerprints.items():
        if name not in ('Core', 'AppInterface'):
            path = (ROOT/name.replace(chr(92), '/')).resolve()
            if not path.is_relative_to(ROOT):
                raise ValueError('gui_fingerprint_outside_checkout')
            name = path.relative_to(ROOT).as_posix()
        if name in normalized and normalized[name] != expected:
            raise ValueError('gui_fingerprint_conflict')
        normalized[name] = expected
    fingerprints = normalized
    required = {'Core', 'AppInterface',
        'gui/MFAAvalonia/MFAAvalonia/Pcr/PcrRuntimePaths.cs',
        'gui/MFAAvalonia/MFAAvalonia/Pcr/PcrAlphaOwner.cs',
        'gui/MFAAvalonia/MFAAvalonia.Desktop/Program.cs'}
    if not required.issubset(fingerprints):
        raise ValueError('gui_fingerprint_incomplete')
    for relative, expected in fingerprints.items():
        if relative in ('Core', 'AppInterface'):
            source = app/('MFAAvalonia.Core.dll' if relative == 'Core' else 'interface.json')
        else:
            source = ROOT/relative
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('gui_source_or_payload_changed: ' + relative)
    crt_sources = vc_redist_files(vc_redist)
    inputs = {}; input_names = set()
    def add(source, relative):
        source, relative = Path(source), Path(relative)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('invalid_package_path')
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_file_attributes & 0x400:
            raise ValueError('nonregular_input: ' + str(source))
        name = relative.as_posix()
        if any(part.casefold() in PRIVATE_PAYLOAD_PARTS for part in relative.parts):
            raise ValueError('private_package_input: ' + name)
        if name.casefold() in input_names:
            raise ValueError('duplicate_package_path: ' + name)
        input_names.add(name.casefold())
        inputs[name] = (source, hashlib.sha256(source.read_bytes()).hexdigest())
    def tree(source, relative, *, exclude=()):
        if not Path(source).is_dir():
            raise ValueError('player_input_directory_missing: ' + str(relative))
        for path in sorted(Path(source).rglob('*')):
            sub = path.relative_to(source)
            if any(part in exclude for part in sub.parts) or path.suffix == '.pyc':
                continue
            if path.is_file():
                add(path, Path(relative)/sub)
    for path in sorted(app.iterdir()):
        if path.name != 'MFAAvalonia.exe' and path.is_file() and (path.suffix in ('.dll', '.exe') or path.name.endswith(('.deps.json', '.runtimeconfig.json'))):
            add(path, path.name)
    for name in ('libs', 'zh-Hans', 'zh-Hant', 'en-US', 'cs', 'de', 'es', 'fr', 'it', 'ja', 'ja-JP', 'ko', 'pl', 'pt-BR', 'ru', 'tr'):
        if (app/name).is_dir():
            tree(app/name, name)
    tree(app/'runtimes/win-x64', 'runtimes/win-x64')
    # Keep the GUI CRT beside Maa native DLLs; Python has its own executable directory.
    for source in crt_sources:
        add(source, Path('runtimes/win-x64/native')/source.name)
        add(source, Path('runtime/python')/source.name)
    tree(ROOT/'src/pcr_maa', 'src/pcr_maa', exclude=('__pycache__',))
    for name in ('pcr.py', 'validate-alpha-config.py', 'start-alpha-agent.py', 'start-alpha-owned-agent.py', 'player-maintenance.py'):
        add(ROOT/'scripts'/name, 'scripts/'+name)
    # Private resources stay in a local-only candidate. Publication is a separate gate.
    tree(ROOT/'assets/resource', 'assets/resource', exclude=('__pycache__',))
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        add(ROOT/name, name)
    tree(ROOT/'docs/licenses', 'licenses/project')
    add(ROOT/'docs/REUSE.md', 'docs/REUSE.md')
    add(ROOT/'docs/PLAYER_GUIDE.md', '使用说明.md')
    add(ROOT/'docs/OFFLINE_UPDATES.md', 'docs/OFFLINE_UPDATES.md')
    add(ROOT/'gui/MFAAvalonia/LICENSE', 'licenses/MFAAvalonia-LICENSE.txt')

    python = Path(sys.base_prefix)
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python311.dll', 'LICENSE.txt'):
        add(python/name, 'runtime/python/'+name)
    tree(python/'DLLs', 'runtime/python/DLLs', exclude=('__pycache__',))
    tree(python/'Lib', 'runtime/python/Lib', exclude=('__pycache__', 'site-packages', 'test'))
    site = Path(sys.prefix)/'Lib/site-packages'
    distributions = runtime_distributions(site)
    for distribution in distributions.values():
        for relative in distribution.files or ():
            name = Path(str(relative))
            if '..' in name.parts or '__pycache__' in name.parts or name.suffix == '.pyc':
                continue
            add(site/name, Path('runtime/python/Lib/site-packages')/name)
    add(SDK/'dotnet.exe', 'runtime/dotnet/dotnet.exe')
    for name in ('LICENSE.txt', 'ThirdPartyNotices.txt'):
        add(SDK/name, 'runtime/dotnet/'+name)
    tree(SDK/'host/fxr'/RUNTIME_VERSION, 'runtime/dotnet/host/fxr/'+RUNTIME_VERSION)
    tree(SDK/'shared/Microsoft.NETCore.App'/RUNTIME_VERSION, 'runtime/dotnet/shared/Microsoft.NETCore.App/'+RUNTIME_VERSION)

    require_player_payload(set(inputs) | GENERATED_PLAYER_FILES)
    output.mkdir()
    inventory = []
    for name, (source, expected) in inputs.items():
        destination = output/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        actual = hashlib.sha256(destination.read_bytes()).hexdigest()
        if actual != expected or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('input_changed_during_copy: ' + name)
        inventory.append({'path': name, 'size': destination.stat().st_size, 'sha256': actual})
    create_native_entry(output)
    # CPython's package-local path policy excludes user sites, cwd and Python env.
    # Scripts add src explicitly; bundled distributions require no site .pth hooks.
    (output/'runtime/python/python311._pth').write_text(
        '.\nDLLs\nLib\nLib/site-packages\n', encoding='utf-8')
    interface = json.loads((ROOT/'interface.json').read_text(encoding='utf-8'))
    interface['resource'][0]['path'] = ['assets/resource']
    interface['agent']['child_exec'] = './runtime/python/python.exe'
    (output/'interface.json').write_text(json.dumps(interface,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'pcr-repository.txt').write_text(str(ROOT) if bind_checkout else '.',encoding='utf-8')
    (output/'启动 Maa PCR.bat').write_bytes(startup_launcher().encode('utf-8'))
    (output/'说明.txt').write_text(player_notes(bind_checkout),encoding='utf-8')
    for name, content in maintenance_launchers().items():
        (output/name).write_bytes(content.encode('utf-8'))
    for name in ('MFAAvalonia.exe', 'runtime/python/python311._pth', 'interface.json', 'pcr-repository.txt', '启动 Maa PCR.bat', '说明.txt', *maintenance_launchers()):
        path=output/name
        inventory.append({'path':name,'size':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    report={'status':'local_candidate','distribution_ready':False,
        'layout':'bound-checkout' if bind_checkout else 'portable-offline-validation',
        'update':{'schema_version':1,'product':'maa-pcr-cn-windows-x64',
            'revision':revision,'version':player_version},
        'source_gui':app.relative_to(ROOT).as_posix(),'python_version':sys.version.split()[0],'dotnet_runtime':RUNTIME_VERSION,
        'python_distributions':{n:d.version for n,d in distributions.items()},
        'vc_redist':{'architecture':'x64', 'destinations':['runtimes/win-x64/native', 'runtime/python'],
            'files':[{'name':name, 'sha256':inputs['runtimes/win-x64/native/'+name][1]} for name in VC_CRT_FILES]},
        'native_entry':{'dotnet_search':'app-relative-only','relative_runtime':'runtime/dotnet'},
        'private_assets_included_for_local_use':True,'personal_settings_or_ledgers_included':False,
        'blockers':['asset_redistribution_evidence_pending','real_device_and_business_acceptance_pending','player_machine_acceptance_pending'],
        'files':sorted(inventory,key=lambda r:r['path'])}
    (output/'package-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--app',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--package',type=Path,help='archive an existing private portable candidate by its manifest')
    parser.add_argument('--archive',type=Path,help='new ZIP path outside the package; no publication')
    parser.add_argument('--vc-redist',type=Path,help='explicit official Microsoft.VC143.CRT x64 directory; no installation')
    parser.add_argument('--bind-checkout',action='store_true',help='local user entry retains original repository and journals')
    parser.add_argument('--revision',type=int,default=8,help='monotonically increasing offline update revision')
    parser.add_argument('--player-version',default='r8-pilot-candidate',help='human-readable package version')
    args=parser.parse_args()
    if args.package:
        if args.app or args.output or args.bind_checkout or args.vc_redist is not None or not args.archive:
            parser.error('--package requires --archive and cannot be combined with build arguments')
        report=export_archive(args.package,args.archive)
    else:
        if not args.app or not args.output:
            parser.error('building requires --app and --output')
        if args.bind_checkout and args.archive:
            parser.error('checkout-bound candidates cannot be archived')
        report=build(args.app,args.output,bind_checkout=args.bind_checkout,vc_redist=args.vc_redist,revision=args.revision,player_version=args.player_version)
        if args.archive:
            report=export_archive(args.output,args.archive)
        else:
            report={'status':report['status'],'layout':report['layout'],'files':len(report['files']),
                'logical_MiB':sum(r['size'] for r in report['files'])/2**20,'distribution_ready':False}
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
