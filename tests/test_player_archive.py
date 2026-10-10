"""Offline manifest-only player archive checks; every payload is synthetic."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import zipfile

import pytest


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    module_path = Path(__file__).resolve().parents[1] / "scripts/build_pcr_player_candidate.py"
    spec = importlib.util.spec_from_file_location("player_candidate_archive_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setattr(module, "ROOT", root)
    return module


def file_record(name, data):
    return {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


@pytest.fixture
def package_case(candidate):
    package = candidate.ROOT / ".runtime" / "玩家候选 中文 space"
    package.mkdir(parents=True)
    payload = {
        "MFAAvalonia.exe": b"MZ synthetic payload; never execute\n",
        "MFAAvalonia.dll": b"synthetic GUI payload; never load\n",
        "MFAAvalonia.Core.dll": b"synthetic GUI core; never load\n",
        "MFAAvalonia.deps.json": b"{}\n",
        "MFAAvalonia.runtimeconfig.json": b"{}\n",
        "interface.json": b"{}\n",
        "启动 Maa PCR.bat": b"@rem synthetic launcher; never execute\n",
        "离线更新 Maa PCR.bat": b"@rem synthetic maintenance; never execute\n",
        "导入补丁包.bat": b"@rem synthetic patch import; never execute\n",
        "导出诊断 Maa PCR.bat": b"@rem synthetic diagnostics; never execute\n",
        "说明.txt": "合成候选说明；不执行。\n".encode("utf-8"),
        "runtime/python/python.exe": b"synthetic Python entry; never execute\n",
        "runtime/python/python311.dll": b"synthetic Python library; never load\n",
        "runtime/python/python311._pth": b".\nDLLs\nLib\nLib/site-packages\n",
        "runtime/python/Lib/encodings/__init__.py": b"# synthetic encodings payload\n",
        "runtime/python/Lib/json/__init__.py": b"# synthetic JSON payload\n",
        "runtime/dotnet/dotnet.exe": b"synthetic .NET entry; never execute\n",
        "runtime/dotnet/host/fxr/10.0.1/hostfxr.dll": b"synthetic hostfxr; never load\n",
        "runtime/dotnet/shared/Microsoft.NETCore.App/10.0.1/coreclr.dll": b"synthetic coreclr; never load\n",
        "runtime/dotnet/shared/Microsoft.NETCore.App/10.0.1/hostpolicy.dll": b"synthetic hostpolicy; never load\n",
        "runtime/dotnet/shared/Microsoft.NETCore.App/10.0.1/System.Private.CoreLib.dll": b"synthetic .NET core library\n",
        "runtimes/win-x64/native/MaaFramework.dll": b"synthetic MaaFramework; never load\n",
        "runtimes/win-x64/native/MaaToolkit.dll": b"synthetic MaaToolkit; never load\n",
        "runtimes/win-x64/native/MaaAgentClient.dll": b"synthetic MaaAgentClient; never load\n",
        "runtimes/win-x64/native/MaaAdbControlUnit.dll": b"synthetic MaaAdbControlUnit; never load\n",
        "runtimes/win-x64/native/MaaUtils.dll": b"synthetic MaaUtils; never load\n",
        "runtimes/win-x64/native/onnxruntime_maa.dll": b"synthetic inference runtime; never load\n",
        "runtimes/win-x64/native/fastdeploy_ppocr_maa.dll": b"synthetic OCR native runtime; never load\n",
        "runtimes/win-x64/native/opencv_world4_maa.dll": b"synthetic OpenCV native runtime; never load\n",
        "assets/resource/model/ocr/det.onnx": b"synthetic OCR detection weight\n",
        "assets/resource/model/ocr/rec.onnx": b"synthetic OCR recognition weight\n",
        "assets/resource/model/ocr/keys.txt": b"synthetic OCR keys\n",
        "assets/resource/pipeline/alpha_entry.json": b"{}\n",
        "scripts/validate-alpha-config.py": b"# synthetic config validator; never execute\n",
        "scripts/start-alpha-owned-agent.py": b"# synthetic agent launcher; never execute\n",
        "src/pcr_maa/agent_entry.py": b"# synthetic agent entry; never import\n",
        "src/pcr_maa/player_maintenance.py": b"# synthetic maintenance; never import\n",
        "scripts/player-maintenance.py": b"# synthetic maintenance entry; never execute\n",
        "pcr-repository.txt": b".\n",
        "使用说明.md": "解压后打开 GUI，选择设备和任务；设置自动保存。\n".encode("utf-8"),
        "docs/OFFLINE_UPDATES.md": b"Synthetic offline update guide; never execute\n",
        "资源/中文 空格.txt": "合成中文资源\n".encode("utf-8"),
        "assets/resource/pipeline/example.json": b"{}\n",
        "runtime/python/Lib/logging/config.py": b"# synthetic standard-library payload\n",
    }
    for name in candidate.VC_CRT_FILES:
        for directory in ("runtimes/win-x64/native", "runtime/python"):
            payload[directory + "/" + name] = b"synthetic CRT payload; never load\n"
    for name, data in payload.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {
        "status": "local_candidate",
        "layout": "portable-offline-validation",
        "distribution_ready": False,
        "personal_settings_or_ledgers_included": False,
        "files": [file_record(name, data) for name, data in sorted(payload.items())],
    }
    write_manifest(package, manifest)
    archive = candidate.ROOT / ".runtime" / "玩家归档 中文 space.zip"
    return candidate, package, archive, payload


def write_manifest(package, manifest):
    (package / "package-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def read_manifest(package):
    return json.loads((package / "package-manifest.json").read_text(encoding="utf-8"))


def rejects_archive(case):
    candidate, package, archive, _ = case
    with pytest.raises((ValueError, OSError)):
        candidate.export_archive(package, archive)
    assert not archive.exists()


def test_manifest_archive_preserves_unicode_and_excludes_runtime_state(package_case):
    candidate, package, archive, payload = package_case
    private_files = {
        "config/instances/player.json": b"synthetic configuration\n",
        "pcr-alpha-v1/journal/intent.json": b"synthetic pending record\n",
        "logs/player.log": b"synthetic local log\n",
    }
    for name, data in private_files.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest_bytes = (package / "package-manifest.json").read_bytes()

    candidate.export_archive(package, archive)

    with zipfile.ZipFile(archive) as exported:
        names = exported.namelist()
        assert set(names) == set(payload) | {"package-manifest.json"}
        assert len(names) == len(set(names))
        assert exported.read("package-manifest.json") == manifest_bytes
        for name, data in payload.items():
            actual = exported.read(name)
            assert len(actual) == len(data)
            assert hashlib.sha256(actual).digest() == hashlib.sha256(data).digest()
        assert not set(private_files).intersection(names)
    assert (package / "package-manifest.json").read_bytes() == manifest_bytes
    for name, data in {**payload, **private_files}.items():
        assert (package / name).read_bytes() == data


def test_same_size_corruption_is_rejected(package_case):
    _, package, _, payload = package_case
    name = "MFAAvalonia.exe"
    (package / name).write_bytes(b"X" + payload[name][1:])
    rejects_archive(package_case)


def test_missing_payload_is_rejected(package_case):
    (package_case[1] / "资源/中文 空格.txt").unlink()
    rejects_archive(package_case)


@pytest.mark.parametrize("field,value", [("size", 999999), ("sha256", "0" * 64)])
def test_manifest_size_and_hash_are_enforced(package_case, field, value):
    package = package_case[1]
    manifest = read_manifest(package)
    manifest["files"][0][field] = value
    write_manifest(package, manifest)
    rejects_archive(package_case)


@pytest.mark.parametrize("name", ["../outside.txt", "/absolute.txt", "C:/absolute.txt", r"..\outside.txt", r"C:\absolute.txt", "assets/../outside.txt"])
def test_unsafe_manifest_paths_are_rejected(package_case, name):
    package = package_case[1]
    manifest = read_manifest(package)
    manifest["files"].append(file_record(name, b"synthetic"))
    write_manifest(package, manifest)
    rejects_archive(package_case)


def test_duplicate_manifest_path_is_rejected(package_case):
    package = package_case[1]
    manifest = read_manifest(package)
    manifest["files"].append(dict(manifest["files"][0]))
    write_manifest(package, manifest)
    rejects_archive(package_case)


def test_existing_parent_traversal_payload_is_rejected(package_case):
    package = package_case[1]
    data = b"synthetic outside-package payload\n"
    (package.parent / "outside.txt").write_bytes(data)
    manifest = read_manifest(package)
    manifest["files"].append(file_record("../outside.txt", data))
    write_manifest(package, manifest)
    rejects_archive(package_case)


@pytest.mark.parametrize("name", [
    ".runtime/report.json", ".venv/settings.txt", ".git/config",
    "config/instances/player.json", "logs/player.log", "backup/settings.json",
    "debug/diagnostic.txt", "temp/cache.txt", "pcr-alpha-v1/journal/intent.json",
    ".pcr-alpha-v1/settings.json", "resource/state.json", "CONFIG/private.json",
    "assets/local/ledger/unknown.json", "assets/local/evidence/frame.png",
])
def test_personal_top_level_directory_cannot_enter_manifest(package_case, name):
    package = package_case[1]
    data = b"synthetic private state\n"
    path = package / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    manifest = read_manifest(package)
    manifest["files"].append(file_record(name, data))
    write_manifest(package, manifest)
    rejects_archive(package_case)


def test_existing_archive_is_preserved(package_case):
    candidate, package, archive, _ = package_case
    original = b"existing archive must remain byte-identical"
    archive.write_bytes(original)
    with pytest.raises((ValueError, OSError)):
        candidate.export_archive(package, archive)
    assert archive.read_bytes() == original


@pytest.mark.parametrize("field,value", [("layout", "bound-checkout"), ("distribution_ready", True)])
def test_nonportable_or_distribution_ready_manifest_is_rejected(package_case, field, value):
    package = package_case[1]
    manifest = read_manifest(package)
    manifest[field] = value
    write_manifest(package, manifest)
    rejects_archive(package_case)


def test_bound_repository_cannot_masquerade_as_portable(package_case):
    candidate, package, _, _ = package_case
    data = str(candidate.ROOT).encode("utf-8")
    (package / "pcr-repository.txt").write_bytes(data)
    manifest = read_manifest(package)
    manifest["files"] = [file_record(row["path"], data) if row["path"] == "pcr-repository.txt" else row for row in manifest["files"]]
    write_manifest(package, manifest)
    rejects_archive(package_case)


@pytest.mark.parametrize("name", ["pcr-repository.txt", "使用说明.md"])
def test_required_player_files_must_be_listed(package_case, name):
    package = package_case[1]
    manifest = read_manifest(package)
    manifest["files"] = [row for row in manifest["files"] if row["path"] != name]
    write_manifest(package, manifest)
    rejects_archive(package_case)


@pytest.mark.parametrize("name", [
    "MFAAvalonia.exe",
    "runtime/python/python.exe",
    "runtime/python/Lib/encodings/__init__.py",
    "assets/resource/model/ocr/det.onnx",
    "runtimes/win-x64/native/MaaFramework.dll",
    "runtimes/win-x64/native/fastdeploy_ppocr_maa.dll",
    "runtimes/win-x64/native/opencv_world4_maa.dll",
    "runtime/dotnet/host/fxr/10.0.1/hostfxr.dll",
    "runtime/dotnet/shared/Microsoft.NETCore.App/10.0.1/coreclr.dll",
    "runtimes/win-x64/native/msvcp140.dll",
    "runtime/python/msvcp140.dll",
    "interface.json",
    "runtime/python/python311._pth",
    "启动 Maa PCR.bat",
])
def test_required_runtime_manifest_must_be_listed_before_opening_zip(package_case, monkeypatch, name):
    candidate, package, archive, payload = package_case
    manifest = read_manifest(package)
    manifest["files"] = [row for row in manifest["files"] if row["path"] != name]
    write_manifest(package, manifest)
    assert (package / name).read_bytes() == payload[name]

    def unexpected_zip_creation(*args, **kwargs):
        pytest.fail("missing required payload must be rejected before opening a ZIP")

    monkeypatch.setattr(candidate.zipfile, "ZipFile", unexpected_zip_creation)
    with pytest.raises(ValueError, match="player_payload_missing") as error:
        candidate.export_archive(package, archive)

    assert name in str(error.value)
    assert not archive.exists()
    assert not list(archive.parent.glob(archive.name + ".partial-*"))
    assert (package / name).read_bytes() == payload[name]


def test_archive_must_be_outside_package(package_case):
    candidate, package, _, _ = package_case
    archive = package / "player.zip"
    with pytest.raises((ValueError, OSError)):
        candidate.export_archive(package, archive)
    assert not archive.exists()


def test_archive_parent_must_already_exist(package_case):
    candidate, package, _, _ = package_case
    archive = candidate.ROOT / ".runtime" / "not-created" / "player.zip"
    with pytest.raises((ValueError, OSError)):
        candidate.export_archive(package, archive)
    assert not archive.parent.exists()


def test_package_must_be_inside_checkout_runtime(package_case):
    candidate, package, archive, _ = package_case
    outside = candidate.ROOT / "outside-package"
    package.rename(outside)
    with pytest.raises((ValueError, OSError)):
        candidate.export_archive(outside, archive)
    assert not archive.exists()


@pytest.mark.parametrize("changed_name", ["package-manifest.json", "MFAAvalonia.exe"])
def test_input_change_during_export_never_publishes_final_archive(package_case, monkeypatch, changed_name):
    _, package, _, _ = package_case
    original_close = zipfile.ZipFile.close
    changed = False

    def close_then_change(exported):
        nonlocal changed
        result = original_close(exported)
        if not changed and exported.mode in ("w", "x"):
            changed = True
            path = package / changed_name
            path.write_bytes(path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(zipfile.ZipFile, "close", close_then_change)
    rejects_archive(package_case)
    assert changed, "the synthetic change must occur while the export is in progress"


def test_ordinary_hardlinked_payload_is_allowed(package_case, tmp_path):
    candidate, package, archive, payload = package_case
    name = "MFAAvalonia.exe"
    donor = tmp_path / "same-bytes.exe"
    donor.write_bytes(payload[name])
    (package / name).unlink()
    try:
        os.link(donor, package / name)
    except OSError:
        pytest.skip("hardlink creation unavailable")
    candidate.export_archive(package, archive)
    with zipfile.ZipFile(archive) as exported:
        assert exported.read(name) == payload[name]


def test_payload_redirect_outside_package_is_rejected(package_case, tmp_path):
    package = package_case[1]
    target = tmp_path / "outside.exe"
    target.write_bytes(package_case[3]["MFAAvalonia.exe"])
    link = package / "MFAAvalonia.exe"
    link.unlink()
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    rejects_archive(package_case)



def directory_alias_in_tmp(alias, target, tmp_path):
    assert alias.absolute().is_relative_to(tmp_path.resolve())
    assert target.resolve().is_relative_to(tmp_path.resolve())
    assert alias.parent.is_dir() and not alias.exists()
    if os.name == "nt":
        import subprocess

        script = tmp_path / "create-directory-alias.ps1"
        script.write_text(
            "param([string]$TaskAlias, [string]$TaskTarget)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "New-Item -ItemType Junction -Path $TaskAlias -Value $TaskTarget -ErrorAction Stop | Out-Null\n",
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script),
                 "-TaskAlias", str(alias), "-TaskTarget", str(target)],
                cwd=tmp_path, capture_output=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            pytest.skip("Windows junction creation unavailable: " + type(error).__name__)
        if result.returncode:
            pytest.skip("Windows junction creation rejected, exit " + str(result.returncode))
        assert getattr(alias.lstat(), "st_file_attributes", 0) & 0x400
    else:
        try:
            os.symlink(target, alias, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            pytest.skip("directory symlink creation unavailable: " + type(error).__name__)
        assert alias.is_symlink()
    assert alias.is_dir() and alias.resolve() == target.resolve()


def test_parent_directory_alias_to_private_state_is_rejected(package_case, tmp_path):
    package = package_case[1]
    private = package / "pcr-alpha-v1" / "config"
    private.mkdir(parents=True)
    data = b'{"synthetic_private_state": true}\n'
    (private / "entry.json").write_bytes(data)
    alias = package / "runtime" / "alias"
    directory_alias_in_tmp(alias, private, tmp_path)
    manifest = read_manifest(package)
    manifest["files"].append(file_record("runtime/alias/entry.json", data))
    write_manifest(package, manifest)
    assert (alias / "entry.json").read_bytes() == data

    rejects_archive(package_case)

    assert (private / "entry.json").read_bytes() == data


def test_package_directory_alias_to_valid_package_is_rejected(package_case, tmp_path):
    candidate, package, archive, _ = package_case
    alias = package.parent / "linked-package"
    directory_alias_in_tmp(alias, package, tmp_path)
    manifest_bytes = (package / "package-manifest.json").read_bytes()

    with pytest.raises((ValueError, OSError)):
        candidate.export_archive(alias, archive)

    assert not archive.exists()
    assert (package / "package-manifest.json").read_bytes() == manifest_bytes
