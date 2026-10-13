import builtins
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import verify


def test_rejects_wrong_python_before_running_checks(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "version_info", (3, 12))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must not launch"))
    assert verify.main(["public"]) == 1
    result = json.loads(next(tmp_path.rglob("result.json")).read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["steps"] == []


def test_failed_step_keeps_log_and_stops(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(verify, "check_python", lambda: None)
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        kwargs["stdout"].write("synthetic failure\n")
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(subprocess, "run", fail)
    assert verify.main(["public"]) == 1
    result = json.loads(next(tmp_path.rglob("result.json")).read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["steps"][0]["exit_code"] == 7
    assert Path(result["steps"][0]["log"]).read_text(encoding="utf-8") == "synthetic failure\n"
    assert len(calls) == 1
    assert calls[0][calls[0].index("-p") + 1] == "scripts.public_test_policy"
    assert calls[0][calls[0].index("-m", calls[0].index("pytest")) + 1] == "not private_replay"


def test_missing_venv_fails_preflight(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 13))
    original = builtins.__import__

    def without_venv(name, *args, **kwargs):
        if name == "venv":
            raise ImportError("embedded runtime")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_venv)
    with pytest.raises(RuntimeError, match="embedded player Python"):
        verify.check_python()


def test_public_does_not_require_dotnet(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(verify, "check_python", lambda: None)
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0))
    assert verify.main(["public", "--dotnet", "absent"]) == 0
    result = json.loads(next(tmp_path.rglob("result.json")).read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert [step["name"] for step in result["steps"]] == ["public-tests"]


def test_public_ignores_environment_selection_options(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(verify, "check_python", lambda: None)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only -k test_verify")
    def run(command, **kwargs):
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, "run", run)
    assert verify.main(["public"]) == 0


@pytest.mark.parametrize('contents', ['{', '{}', '{"sdk": null}', '{"sdk": {"version": null}}'])
def test_malformed_sdk_config_is_a_failed_receipt(monkeypatch, tmp_path, contents):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(verify, "check_python", lambda: None)
    monkeypatch.setattr(verify.shutil, "which", lambda name: sys.executable)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must not launch"))
    (tmp_path / 'global.json').write_text(contents, encoding='utf-8')
    assert verify.main(["gui"]) == 1
    result = json.loads(next(tmp_path.rglob("result.json")).read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert "global.json" in result["error"]
    assert result["steps"] == []


def test_gui_rejects_stale_core_before_running(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    core = tmp_path / "gui/MFAAvalonia/bin/x64/Release/MFAAvalonia.Core.dll"
    core.parent.mkdir(parents=True)
    core.write_bytes(b"current")
    binary = tmp_path / "test-bin"
    binary.mkdir()
    (binary / core.name).write_bytes(b"stale")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must not launch"))
    with pytest.raises(RuntimeError, match="current Desktop Core"):
        verify.run_step("PcrProfileSwitch.Tests-run", ["sdk"], binary, tmp_path, {}, {"steps": []}, timeout=1)


def test_gui_plan_builds_before_checks_and_uses_matching_rids(tmp_path):
    commands = verify.gui_commands("sdk", tmp_path / "gui", tmp_path)
    assert len(commands) == 11
    assert [name for name, _, _ in commands][0:2] == ["MFAAvalonia.Desktop-restore", "MFAAvalonia.Desktop-build"]
    for name, command, cwd in commands:
        assert cwd.is_relative_to(tmp_path)
        if name.endswith("restore"):
            assert "--locked-mode" in command
            assert "--configfile" in command
        if name.endswith("build"):
            assert "--no-restore" in command
        if name.startswith(("PcrProfileSwitch", "PcrLabyrinthCredentials")):
            assert "-r" not in command
        elif not name.endswith("run"):
            assert command[command.index("-r") + 1] == "win-x64"
        if name == "PcrProfileSwitch.Tests-run":
            assert command[-3:] == [str(verify.ROOT), str(tmp_path / "fixtures/PcrProfileSwitch.Tests"), sys.executable]
