import codecs
import hashlib
import os
import shutil
import subprocess
import zipfile

import pytest

from scripts import build_short_path_bridge as bridge


def test_bridge_binds_the_patch_and_builds_a_windows_double_click_entry(tmp_path, monkeypatch):
    patch = tmp_path / bridge.PATCH
    content = b"isolated test patch"
    patch.write_bytes(content)
    output = tmp_path / bridge.ARCHIVE
    with pytest.raises(ValueError, match="SHA-256"):
        bridge.build(patch, output)
    assert not output.exists()

    monkeypatch.setattr(bridge, "PATCH_SHA256", hashlib.sha256(content).hexdigest())
    bridge.build(patch, output)
    original = output.read_bytes()
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {bridge.PATCH, bridge.BATCH, "install.ps1", "使用说明.txt"}
        assert archive.read(bridge.PATCH) == content
        assert archive.read("install.ps1").startswith(codecs.BOM_UTF8)
        launcher = archive.read(bridge.BATCH)
        assert b"-TargetVersion v0.2.8" in launcher
        assert b"\n" not in launcher.replace(b"\r\n", b"")
    with pytest.raises(FileExistsError):
        bridge.build(patch, output)
    assert output.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell launcher")
@pytest.mark.parametrize(("base", "target", "marker", "exit_code", "message"), [
    (94, "v0.2.8", False, 1, "仅适用于官方 v0.2.7"),
    (95, "v0.2.8", True, 1, "上次更新尚未完成"),
    (95, "v0.2.8", False, 1, "缺少完整的官方增量包"),
    (95, "v0.2.7", False, 0, "已经是 v0.2.7"),
])
def test_installer_checks_installation_before_running_any_program(tmp_path, base, target,
                                                               marker, exit_code, message):
    bundle = tmp_path / "helper"
    player = tmp_path / "原版 玩家"
    bundle.mkdir()
    player.mkdir()
    installer = bundle / "install.ps1"
    installer.write_bytes(bridge.windows_text(bridge.ENTRIES / installer.name))
    (bundle / bridge.PATCH).write_bytes(b"incomplete")
    exe = player / "MFAAvalonia.exe"
    exe.touch()
    shutil.copyfile(bridge.ROOT / f"delivery/baselines/r{base}-package-manifest.json",
                    player / "package-manifest.json")
    if marker:
        progress = player / ".runtime/player-maintenance/update-in-progress.json"
        progress.parent.mkdir(parents=True)
        progress.write_text("{}", encoding="utf-8")
    response = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                               "-ExecutionPolicy", "Bypass", "-File", str(installer),
                               "-PlayerExe", str(exe), "-TargetVersion", target],
                              capture_output=True, encoding="utf-8", timeout=30)
    assert response.returncode == exit_code, response.stderr
    assert message in response.stdout + response.stderr
