"""Candidate preparation must fail before writing when its input is incomplete or stale."""
from pathlib import Path
import importlib.util
import json

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('player_candidate',ROOT/'scripts/build_pcr_player_candidate.py')
candidate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


def test_existing_output_preserved(tmp_path):
    output=tmp_path/'existing'
    output.mkdir()
    sentinel=output/'settings.txt'
    sentinel.write_bytes(b'keep existing user work')
    with pytest.raises(ValueError,match='output_exists'):
        candidate.build(ROOT/'.runtime/absent/app',output)
    assert sentinel.read_bytes()==b'keep existing user work'
    assert list(output.iterdir())==[sentinel]


def test_candidate_outside_checkout_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(candidate,'ROOT',tmp_path/'checkout')
    output=tmp_path/'output'
    with pytest.raises(ValueError,match='outside_project_runtime'):
        candidate.build(tmp_path/'app',output)
    assert not output.exists()


def test_incomplete_gui_does_not_create_package(tmp_path,monkeypatch):
    monkeypatch.setattr(candidate,'ROOT',tmp_path)
    app=tmp_path/'.runtime/build/app'
    app.mkdir(parents=True)
    output=tmp_path/'output'
    with pytest.raises(ValueError,match='gui_payload_missing'):
        candidate.build(app,output)
    assert not output.exists()


def test_empty_fingerprints_cannot_qualify_gui(tmp_path,monkeypatch):
    monkeypatch.setattr(candidate,'ROOT',tmp_path)
    app=tmp_path/'.runtime/build/app'
    app.mkdir(parents=True)
    for name in ('MFAAvalonia.exe','MFAAvalonia.dll','MFAAvalonia.Core.dll','MFAAvalonia.deps.json','MFAAvalonia.runtimeconfig.json'):
        (app/name).write_bytes(b'not executable, metadata fixture only')
    (app.parent/'fingerprints.json').write_text('{}',encoding='utf-8')
    output=tmp_path/'output'
    with pytest.raises(ValueError,match='gui_fingerprint_incomplete'):
        candidate.build(app,output)
    assert not output.exists()


def test_packaged_runtime_ignores_external_python_environment(tmp_path):
    """Run the shipped interpreter with hostile per-process search paths."""
    import os
    import subprocess
    package_value = os.environ.get('PCR_PLAYER_PACKAGE_ROOT')
    if not package_value:
        pytest.skip('Requires an explicitly prepared offline player candidate')
    package = Path(package_value).resolve()
    assert package.is_relative_to(ROOT/'.runtime')
    injection = tmp_path/'external-packages'
    injection.mkdir()
    (injection/'sitecustomize.py').write_text("raise RuntimeError('external sitecustomize loaded')",encoding='utf-8')
    for name in ('numpy','jsonschema'):
        (injection/(name+'.py')).write_text("raise RuntimeError('external dependency loaded')",encoding='utf-8')
    env = dict(os.environ,PYTHONHOME=str(tmp_path/'missing-python-home'),
        PYTHONPATH=str(injection),PYTHONUSERBASE=str(tmp_path/'user-site'),
        PYTHONDONTWRITEBYTECODE='1')
    env['PATH'] = str(Path(os.environ['SystemRoot'])/'System32')+';'+os.environ['SystemRoot']
    probe = """import sys,json,site
from pathlib import Path
import maa,cv2,numpy,PIL,jsonschema
root=Path(sys.argv[1])
sys.path.insert(0,str(root/'src'))
from pcr_maa import agent_entry,native,supported_daily,rewards,alpha_owner
modules=[maa,cv2,numpy,PIL,jsonschema,agent_entry,native,supported_daily,rewards,alpha_owner]
print(json.dumps(dict(isolated=sys.flags.isolated,no_site=sys.flags.no_site,user_site=site.ENABLE_USER_SITE,
    executable=sys.executable,prefix=sys.prefix,paths=sys.path,
    modules={m.__name__:m.__file__ for m in modules})))
"""
    result=subprocess.run([str(package/'runtime/python/python.exe'),'-X','utf8','-B','-c',probe,str(package)],
        cwd=tmp_path,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode == 0, result.stderr[-4000:]
    proof=json.loads(result.stdout)
    assert proof['isolated'] == 1 and proof['no_site'] == 1 and proof['user_site'] is not True
    assert Path(proof['executable']).resolve() == package/'runtime/python/python.exe'
    assert Path(proof['prefix']).resolve() == package/'runtime/python'
    assert all(Path(path).resolve().is_relative_to(package) for path in proof['paths'])
    assert all(Path(path).resolve().is_relative_to(package) for path in proof['modules'].values())


def test_native_entry_missing_tool_preserves_existing_exe(tmp_path,monkeypatch):
    output=tmp_path/'output'
    output.mkdir()
    entry=output/'MFAAvalonia.exe'
    entry.write_bytes(b'preserve prior entry')
    monkeypatch.setattr(candidate,'SDK',tmp_path/'missing-sdk')
    with pytest.raises(ValueError,match='native_entry_input_missing'):
        candidate.create_native_entry(output)
    assert entry.read_bytes()==b'preserve prior entry'


def test_native_entry_uses_bundled_runtime(tmp_path):
    """Verify actual native libraries, and no global fallback with missing runtime."""
    import os
    import shutil
    import subprocess
    value=os.environ.get('PCR_PLAYER_PACKAGE_ALIAS')
    if not value:
        pytest.skip('Requires an explicit ASCII diagnostic alias of the prepared candidate')
    package=Path(value).absolute()
    physical=package.resolve()
    assert physical.is_relative_to(ROOT/'.runtime')
    assert str(package).isascii(), 'Native host traces require ASCII paths on this machine'
    env=dict(os.environ,DOTNET_ROOT=str(tmp_path/'absent-runtime'),
        DOTNET_ROOT_X64=str(tmp_path/'absent-runtime'),DOTNET_MULTILEVEL_LOOKUP='0',
        DOTNET_DISABLE_GUI_ERRORS='1',COREHOST_TRACE='1',DOTNET_HOST_TRACE='1')
    env['PATH']=str(Path(os.environ['SystemRoot'])/'System32')+';'+os.environ['SystemRoot']
    missing=tmp_path/'missing-runtime'
    missing.mkdir()
    for name in ('MFAAvalonia.exe','MFAAvalonia.dll','MFAAvalonia.deps.json','MFAAvalonia.runtimeconfig.json'):
        shutil.copyfile(package/name,missing/name)
    results=[]
    for name,folder in [('bundled',package),('missing',missing)]:
        command=[str(folder/'MFAAvalonia.exe'),'--identify']
        result=subprocess.run(command,cwd=Path(os.environ['SystemRoot'])/'System32',env=env,
            capture_output=True,text=True,encoding='utf-8',timeout=20)
        (tmp_path/(name+'-host-trace.log')).write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        libraries=[line.removeprefix('Loaded library from ') for line in result.stderr.splitlines()
            if line.startswith('Loaded library from ')]
        results.append(dict(case=name,command=command,exit=result.returncode,libraries=libraries))
        if name=='bundled':
            assert result.returncode==0 and result.stdout.strip()=='MFAAvalonia'
            assert {Path(path).name.lower() for path in libraries}>={'hostfxr.dll','hostpolicy.dll','coreclr.dll'}
            assert all(Path(path).resolve().is_relative_to(physical/'runtime/dotnet') for path in libraries)
        else:
            assert result.returncode!=0 and not libraries
            assert 'runtime/dotnet' in result.stderr or 'runtime\\dotnet' in result.stderr
    (tmp_path/'apphost-verification.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.fixture
def crt_build_inputs(tmp_path, monkeypatch):
    """Small data-only inputs; no real runtime, compiler or device is executed."""
    import hashlib
    monkeypatch.setattr(candidate, 'ROOT', tmp_path)
    app = tmp_path/'.runtime/gui/app'
    app.mkdir(parents=True)
    for name in ('MFAAvalonia.exe', 'MFAAvalonia.dll', 'MFAAvalonia.Core.dll',
                 'MFAAvalonia.deps.json', 'MFAAvalonia.runtimeconfig.json', 'interface.json'):
        (app/name).write_bytes(b'GUI metadata fixture')
    for name in ('MaaFramework.dll', 'MaaToolkit.dll', 'MaaAgentClient.dll',
                 'MaaAdbControlUnit.dll', 'MaaUtils.dll', 'onnxruntime_maa.dll',
                 'fastdeploy_ppocr_maa.dll', 'opencv_world4_maa.dll'):
        path = app/'runtimes/win-x64/native'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic Maa native payload; never load')
    fingerprints = {'Core': hashlib.sha256((app/'MFAAvalonia.Core.dll').read_bytes()).hexdigest(),
                    'AppInterface': hashlib.sha256((app/'interface.json').read_bytes()).hexdigest()}
    for name in ('gui/MFAAvalonia/MFAAvalonia/Pcr/PcrRuntimePaths.cs',
                 'gui/MFAAvalonia/MFAAvalonia/Pcr/PcrAlphaOwner.cs',
                 'gui/MFAAvalonia/MFAAvalonia/Pcr/PcrAlphaOwner.Session.cs',
                 'gui/MFAAvalonia/MFAAvalonia/Pcr/PcrStartConsent.cs',
                 'gui/MFAAvalonia/MFAAvalonia.Desktop/Program.cs'):
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'source fixture')
        fingerprints[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (app.parent/'fingerprints.json').write_text(json.dumps(fingerprints), encoding='utf-8')
    for name in ('scripts/pcr.py', 'scripts/validate-alpha-config.py',
                 'scripts/start-alpha-agent.py', 'scripts/start-alpha-owned-agent.py', 'scripts/player-maintenance.py',
                 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'docs/REUSE.md',
                 'docs/PLAYER_GUIDE.md', 'docs/OFFLINE_UPDATES.md', 'gui/MFAAvalonia/LICENSE',
                 'docs/licenses/synthetic.txt', 'src/pcr_maa/agent_entry.py', 'src/pcr_maa/player_maintenance.py',
                 'assets/resource/pipeline/alpha_entry.json',
                 'assets/resource/model/ocr/det.onnx',
                 'assets/resource/model/ocr/rec.onnx',
                 'assets/resource/model/ocr/keys.txt'):
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'package fixture')
    (tmp_path/'interface.json').write_text(json.dumps({'resource': [{}], 'agent': {}}), encoding='utf-8')
    python = tmp_path/'python'
    python.mkdir()
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python311.dll', 'LICENSE.txt',
                 'vcruntime140.dll', 'vcruntime140_1.dll'):
        (python/name).write_bytes(b'old Python runtime source must not supply CRT')
    for name in ('DLLs/_socket.pyd', 'Lib/encodings/__init__.py', 'Lib/json/__init__.py'):
        path = python/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'# synthetic Python payload; never import')
    sdk = tmp_path/'sdk'
    sdk.mkdir()
    for name in ('dotnet.exe', 'LICENSE.txt', 'ThirdPartyNotices.txt'):
        (sdk/name).write_bytes(b'SDK fixture')
    for name in ('host/fxr/10.0.1/hostfxr.dll',
                 'shared/Microsoft.NETCore.App/10.0.1/coreclr.dll',
                 'shared/Microsoft.NETCore.App/10.0.1/hostpolicy.dll',
                 'shared/Microsoft.NETCore.App/10.0.1/System.Private.CoreLib.dll'):
        path = sdk/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic .NET payload; never load')
    monkeypatch.setattr(candidate.sys, 'base_prefix', str(python))
    monkeypatch.setattr(candidate, 'SDK', sdk)
    monkeypatch.setattr(candidate, 'runtime_distributions', lambda site: {})
    monkeypatch.setattr(candidate, 'create_native_entry',
                        lambda output: (output/'MFAAvalonia.exe').write_bytes(b'fixture entry'))
    crt = tmp_path/'Microsoft.VC143.CRT'
    crt.mkdir()
    for name in candidate.VC_CRT_FILES:
        pe = bytearray(70)
        pe[:2] = b'MZ'
        pe[60:64] = (64).to_bytes(4, 'little')
        pe[64:70] = b'PE\x00\x00' + (0x8664).to_bytes(2, 'little')
        (crt/name).write_bytes(pe + name.encode('ascii'))
    return app, tmp_path/'output', crt


@pytest.mark.parametrize('source_root, missing_name', [
    ('app', 'runtimes/win-x64/native'),
    ('app', 'runtimes/win-x64/native/fastdeploy_ppocr_maa.dll'),
    ('app', 'runtimes/win-x64/native/opencv_world4_maa.dll'),
    ('root', 'assets/resource/model/ocr/det.onnx'),
    ('python', 'Lib'),
    ('sdk', 'host/fxr/10.0.1'),
    ('sdk', 'shared/Microsoft.NETCore.App/10.0.1'),
])
def test_required_runtime_inputs_missing_does_not_create_output(crt_build_inputs, source_root, missing_name):
    import shutil

    app, output, crt = crt_build_inputs
    sources = {'app': app, 'root': candidate.ROOT,
               'python': Path(candidate.sys.base_prefix), 'sdk': candidate.SDK}
    missing = sources[source_root]/missing_name
    assert missing.resolve().is_relative_to(candidate.ROOT.resolve())
    if missing.is_dir():
        shutil.rmtree(missing)
    else:
        missing.unlink()
    if source_root == 'app' and missing_name.endswith('.dll'):
        assert missing.parent.is_dir()
        assert (missing.parent/'MaaFramework.dll').is_file()
    if source_root == 'root':
        # A missing weight cannot be hidden by the remaining valid resource tree.
        assert (candidate.ROOT/'assets/resource/model/ocr/rec.onnx').is_file()
        assert (candidate.ROOT/'assets/resource/model/ocr/keys.txt').is_file()
        assert (candidate.ROOT/'assets/resource/pipeline/alpha_entry.json').is_file()

    with pytest.raises(ValueError, match='player_(input_directory|payload)_missing') as error:
        candidate.build(app, output, vc_redist=crt)

    assert missing_name in str(error.value).replace('\\', '/')
    assert not output.exists()


@pytest.mark.parametrize('name', ['PcrAlphaOwner.Session.cs', 'PcrStartConsent.cs'])
@pytest.mark.parametrize('fault', ['missing_fingerprint', 'changed_source'])
def test_split_owner_sources_must_match_candidate(crt_build_inputs, name, fault):
    app, output, crt = crt_build_inputs
    relative = 'gui/MFAAvalonia/MFAAvalonia/Pcr/' + name
    if fault == 'missing_fingerprint':
        path = app.parent / 'fingerprints.json'
        fingerprints = json.loads(path.read_text(encoding='utf-8'))
        del fingerprints[relative]
        path.write_text(json.dumps(fingerprints), encoding='utf-8')
        expected = 'gui_fingerprint_incomplete'
    else:
        (candidate.ROOT / relative).write_bytes(b'changed after compilation')
        expected = 'gui_source_or_payload_changed'
    with pytest.raises(ValueError, match=expected):
        candidate.build(app, output, vc_redist=crt)
    assert not output.exists()


def test_crt_source_required_before_output(crt_build_inputs):
    app, output, _ = crt_build_inputs
    with pytest.raises(ValueError, match='vc_redist_source_required'):
        candidate.build(app, output)
    assert not output.exists()


def test_missing_offline_update_guide_does_not_create_output(crt_build_inputs):
    app, output, crt = crt_build_inputs
    guide = candidate.ROOT/'docs/OFFLINE_UPDATES.md'
    guide.unlink()
    with pytest.raises(FileNotFoundError):
        candidate.build(app, output, vc_redist=crt)
    assert not output.exists()


@pytest.mark.parametrize('fault, expected', [
    ('missing', 'vc_redist_file_missing'),
    ('directory', 'nonregular_vc_redist_input'),
    ('x86', 'vc_redist_requires_x64'),
    ('arm64', 'vc_redist_requires_x64'),
    ('not_pe', 'invalid_vc_redist_pe'),
    ('pe_offset', 'invalid_vc_redist_pe'),
])
def test_invalid_crt_source_does_not_create_output(crt_build_inputs, fault, expected):
    app, output, crt = crt_build_inputs
    source = crt/'msvcp140.dll'
    if fault in ('missing', 'directory'):
        source.unlink()
        if fault == 'directory':
            source.mkdir()
    else:
        pe = bytearray(source.read_bytes())
        if fault in ('x86', 'arm64'):
            pe[68:70] = (0x14c if fault == 'x86' else 0xaa64).to_bytes(2, 'little')
        elif fault == 'not_pe':
            pe[64:68] = b'NOPE'
        else:
            pe[60:64] = (len(pe) + 100).to_bytes(4, 'little')
        source.write_bytes(pe)
    with pytest.raises(ValueError, match=expected):
        candidate.build(app, output, vc_redist=crt)
    assert not output.exists()


@pytest.mark.parametrize('name', ['msvcp140.dll', 'MSVCP140.dll'])
def test_existing_gui_crt_cannot_mix_with_explicit_source(crt_build_inputs, name):
    app, output, crt = crt_build_inputs
    native = app/'runtimes/win-x64/native'
    native.mkdir(parents=True, exist_ok=True)
    (native/name).write_bytes(b'other CRT build')
    with pytest.raises(ValueError, match='duplicate_package_path'):
        candidate.build(app, output, vc_redist=crt)
    assert not output.exists()


def test_complete_crt_is_identical_in_both_process_directories(crt_build_inputs):
    import hashlib
    app, output, crt = crt_build_inputs
    report = candidate.build(app, output, vc_redist=crt)
    disk = json.loads((output/'package-manifest.json').read_text(encoding='utf-8'))
    assert disk == report
    recorded = {row['name']: row['sha256'] for row in report['vc_redist']['files']}
    inventory = {row['path']: row for row in report['files']}
    assert report['vc_redist']['architecture'] == 'x64'
    assert report['vc_redist']['destinations'] == ['runtimes/win-x64/native', 'runtime/python']
    assert len(recorded) == 10
    for name in candidate.VC_CRT_FILES:
        expected = (crt/name).read_bytes()
        digest = hashlib.sha256(expected).hexdigest()
        assert recorded[name] == digest
        for relative in ('runtimes/win-x64/native/' + name, 'runtime/python/' + name):
            assert (output/relative).read_bytes() == expected
            assert inventory[relative]['sha256'] == digest
            assert inventory[relative]['size'] == len(expected)
    assert report['distribution_ready'] is False
    assert report['update'] == {'schema_version': 1, 'product': 'maa-pcr-cn-windows-x64',
        'revision': 8, 'version': 'r8-pilot-candidate'}
    for name in ('scripts/player-maintenance.py', 'src/pcr_maa/player_maintenance.py',
                 '离线更新 Maa PCR.bat', '导入补丁包.bat', '导出诊断 Maa PCR.bat',
                 'docs/OFFLINE_UPDATES.md'):
        assert name in inventory and (output/name).is_file()
    assert (output/'导入补丁包.bat').read_bytes() == (output/'离线更新 Maa PCR.bat').read_bytes()
    guide = (candidate.ROOT/'docs/OFFLINE_UPDATES.md').read_bytes()
    assert (output/'docs/OFFLINE_UPDATES.md').read_bytes() == guide
    assert inventory['docs/OFFLINE_UPDATES.md']['sha256'] == hashlib.sha256(guide).hexdigest()
    assert inventory['docs/OFFLINE_UPDATES.md']['size'] == len(guide)
    assert {'导入补丁包.bat', 'docs/OFFLINE_UPDATES.md'} <= candidate.REQUIRED_PLAYER_FILES
    for name, command in (('离线更新 Maa PCR.bat', 'update'), ('导入补丁包.bat', 'update'),
                          ('导出诊断 Maa PCR.bat', 'diagnostics')):
        launch = (output/name).read_text(encoding='utf-8')
        assert 'runtime\\python\\python.exe' in launch
        assert 'player-maintenance.py' in launch and command + ' --player' in launch
        assert 'DisableDelayedExpansion' in launch


def test_cli_forwards_explicit_crt_source(crt_build_inputs, monkeypatch, capsys):
    app, output, crt = crt_build_inputs
    monkeypatch.setattr(candidate.sys, 'argv', ['build_pcr_player_candidate.py',
        '--app', str(app), '--output', str(output), '--vc-redist', str(crt)])
    candidate.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'local_candidate'
    assert (output/'runtimes/win-x64/native/msvcp140.dll').read_bytes() == (crt/'msvcp140.dll').read_bytes()



def test_packaged_native_crt_loads_from_python_directory(tmp_path):
    """Inspect loaded CRT paths in a fresh process without constructing a controller."""
    import os
    import subprocess
    value = os.environ.get('PCR_PLAYER_PACKAGE_ROOT')
    if not value:
        pytest.skip('Requires an explicitly prepared offline player candidate')
    package = Path(value).resolve()
    assert package.is_relative_to(ROOT/'.runtime')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env['PATH'] = str(Path(os.environ['SystemRoot'])/'System32') + ';' + os.environ['SystemRoot']
    probe = """import ctypes,json,sys
from maa.library import Library
framework=Library.framework()
framework.MaaVersion.restype=ctypes.c_char_p
version=framework.MaaVersion().decode('utf-8')
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.GetModuleHandleW.argtypes=[ctypes.c_wchar_p]
kernel.GetModuleHandleW.restype=ctypes.c_void_p
kernel.GetModuleFileNameW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_ulong]
kernel.GetModuleFileNameW.restype=ctypes.c_ulong
loaded={}
for name in ('msvcp140.dll','msvcp140_1.dll','concrt140.dll','vcruntime140.dll','vcruntime140_1.dll'):
    handle=kernel.GetModuleHandleW(name)
    if not handle:
        raise RuntimeError('expected_import_not_loaded:'+name)
    buffer=ctypes.create_unicode_buffer(32768)
    length=kernel.GetModuleFileNameW(handle,buffer,len(buffer))
    if not length or length>=len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    loaded[name]=buffer.value
print(json.dumps(dict(maa_version=version,executable=sys.executable,loaded=loaded)))
"""
    result = subprocess.run([str(package/'runtime/python/python.exe'), '-X', 'utf8', '-B', '-c', probe],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    proof = json.loads(result.stdout)
    (tmp_path/'loaded-crt.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    assert proof['maa_version'].startswith('v')
    assert Path(proof['executable']).resolve() == package/'runtime/python/python.exe'
    assert set(proof['loaded']) == {'msvcp140.dll', 'msvcp140_1.dll', 'concrt140.dll',
                                    'vcruntime140.dll', 'vcruntime140_1.dll'}
    assert all(Path(path).resolve() == package/'runtime/python'/name
               for name, path in proof['loaded'].items()), proof['loaded']
