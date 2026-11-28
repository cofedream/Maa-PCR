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


def test_public_jobs_rejects_a_non_public_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(verify, "check_python", lambda: None)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must not launch"))
    assert verify.main(["gui", "--jobs", "2"]) == 1
    result = json.loads(next(tmp_path.rglob("result.json")).read_text(encoding="utf-8"))
    assert result["status"] == "failed" and "public scope" in result["error"]


def test_public_shards_pack_every_test_file_exactly_once(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    for name, size in (("test_a.py", 10), ("test_b.py", 20), ("test_c.py", 30)):
        (tests / name).write_bytes(b"x" * size)
    output = tmp_path / "verification"
    output.mkdir()
    commands = verify.public_shards(tmp_path, output, 2)
    assert [name for name, _, _ in commands] == ["public-tests-0", "public-tests-1"]
    packed = sorted(Path(item).name for _, command, _ in commands
                    for item in command if item.endswith(".py"))
    assert packed == ["test_a.py", "test_b.py", "test_c.py"]
    for name, command, cwd in commands:
        assert cwd == tmp_path
        assert Path(command[command.index("--basetemp") + 1]).is_relative_to(output / name)
        assert Path(command[command.index("--junitxml") + 1]).is_relative_to(output / name)


def test_public_shards_prefer_recorded_module_durations(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("test_a.py", "test_b.py", "test_c.py"):
        (tests / name).write_bytes(b"x")
    record = tmp_path / ".runtime/verification/20260101T000000.000000Z"
    record.mkdir(parents=True)
    (record / "public-tests.xml").write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_a" name="one" time="100.0"/>'
        '<testcase classname="tests.test_b" name="two" time="1.0"/>'
        '<testcase classname="tests.test_c" name="three" time="1.0"/>'
        '</testsuite></testsuites>', encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    assert verify.recorded_test_seconds(tmp_path) == {
        "test_a": 100.0, "test_b": 1.0, "test_c": 1.0}
    packed = {name: sorted(Path(item).name for item in command if item.endswith(".py"))
              for name, command, _ in verify.public_shards(tmp_path, output, 2)}
    assert packed == {"public-tests-0": ["test_a.py"],
                      "public-tests-1": ["test_b.py", "test_c.py"]}


def test_public_shards_estimate_files_missing_from_the_record(tmp_path):
    """New or fully filtered modules use the record's seconds-per-byte rate."""
    tests = tmp_path / "tests"
    tests.mkdir()
    for name, size in (("test_a.py", 100), ("test_b.py", 300), ("test_c.py", 50)):
        (tests / name).write_bytes(b"x" * size)
    record = tmp_path / ".runtime/verification/20260101T000000.000000Z"
    record.mkdir(parents=True)
    (record / "public-tests.xml").write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_a" name="one" time="100.0"/>'
        '</testsuite></testsuites>', encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    packed = {name: sorted(Path(item).name for item in command if item.endswith(".py"))
              for name, command, _ in verify.public_shards(tmp_path, output, 2)}
    # Rate is 100 s / 100 bytes = 1.0, so test_b (300 B) outweighs test_a (100 s).
    assert packed == {"public-tests-0": ["test_b.py"],
                      "public-tests-1": ["test_a.py", "test_c.py"]}


def test_public_jobs_run_shards_concurrently_and_merge_the_record(monkeypatch, tmp_path):
    import xml.etree.ElementTree as ET
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(verify, "check_python", lambda: None)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("def test_one(): pass\n", encoding="utf-8")
    (tests / "test_b.py").write_text("def test_two(): pass\n", encoding="utf-8")

    def run(command, **kwargs):
        kwargs["stdout"].write("synthetic shard\n")
        xml = Path(command[command.index("--junitxml") + 1])
        xml.parent.mkdir(parents=True, exist_ok=True)
        xml.write_text('<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="1" '
            'failures="0" errors="0" skipped="0" time="0.010"><testcase classname="tests.test_a" '
            'name="test_one" time="0.010"/></testsuite></testsuites>', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert verify.main(["public", "--jobs", "2"]) == 0
    result = json.loads(next(tmp_path.rglob("result.json")).read_text(encoding="utf-8"))
    assert result["status"] == "passed" and result["public_jobs"] == 2
    assert [step["name"] for step in result["steps"]] == ["public-tests-0", "public-tests-1"]
    merged = next(tmp_path.rglob("public-tests.xml"))
    assert ET.parse(merged).getroot().get("tests") == "2"
    assert (next(tmp_path.rglob("public-tests.log"))).read_text(encoding="utf-8").count(
        "synthetic shard") == 2


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


@pytest.mark.parametrize('mode', ['default', *verify.PROFILE_CHECK_MODES])
def test_gui_rejects_stale_core_before_running(monkeypatch, tmp_path, mode):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    core = tmp_path / "gui/MFAAvalonia/bin/x64/Release/MFAAvalonia.Core.dll"
    core.parent.mkdir(parents=True)
    core.write_bytes(b"current")
    binary = tmp_path / "test-bin"
    binary.mkdir()
    (binary / core.name).write_bytes(b"stale")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must not launch"))
    with pytest.raises(RuntimeError, match="current Desktop Core"):
        verify.run_step("PcrProfileSwitch.Tests-" + mode + "-run", ["sdk"], binary, tmp_path, {}, {"steps": []}, timeout=1)


def test_gui_plan_builds_before_checks_and_uses_matching_rids(tmp_path):
    commands = verify.gui_commands("sdk", tmp_path / "gui", tmp_path)
    assert len(commands) == 24
    assert [name for name, _, _ in commands][0:2] == ["MFAAvalonia.Desktop-restore", "MFAAvalonia.Desktop-build"]
    for name, command, cwd in commands:
        assert cwd.is_relative_to(tmp_path)
        if name.endswith("restore"):
            assert "--locked-mode" in command
            assert "--configfile" in command
        if name.endswith("build"):
            assert "--no-restore" in command
        if name.startswith(("PcrProfileSwitch", "PcrLabyrinth")):
            assert "-r" not in command
        elif not name.endswith("run"):
            assert command[command.index("-r") + 1] == "win-x64"
        if name == "PcrProfileSwitch.Tests-run":
            assert command[-3:] == [str(verify.ROOT), str(tmp_path / "fixtures/PcrProfileSwitch.Tests"), sys.executable]
        if name == "PcrUpdate.Tests-startup-actions-run":
            assert command[-3:] == [str(tmp_path / "fixtures/startup-actions"), "--startup-actions-only", str(verify.ROOT)]
            assert Path(command[1]).parent == cwd
        if name in ("PcrLabyrinthAccounts.Tests-run","PcrLabyrinthRatings.Tests-run"):
            project=name.removesuffix('-run')
            assert command[-2:]==[str(tmp_path/'fixtures'/project),str(verify.ROOT)]
            lock=verify.ROOT/'gui/MFAAvalonia'/project/'packages.lock.json'
            assert json.loads(lock.read_text(encoding='utf-8'))['dependencies']['net10.0']


def test_gui_plan_runs_each_clan_contract_in_a_separate_process_without_rebuilding(tmp_path):
    commands = verify.gui_commands("sdk", tmp_path / "gui", tmp_path)
    modes = {'clan-daily-only', 'clan-workflow-only', 'clan-formal-only',
             'clan-postsave-only', 'profile-registration-only', 'runbar-only'}
    profile = [(name, command, cwd) for name, command, cwd in commands if name.startswith('PcrProfileSwitch.Tests')]
    assert [name for name, _, _ in profile[:3]] == [
        'PcrProfileSwitch.Tests-restore', 'PcrProfileSwitch.Tests-build', 'PcrProfileSwitch.Tests-run']
    runs = profile[2:]
    assert len(runs) == 7
    assert len({command[1] for _, command, _ in runs}) == 1
    assert len({command[3] for _, command, _ in runs}) == 7
    assert {command[-1].removeprefix('--') for _, command, _ in runs[1:]} == modes
    for name, command, cwd in runs:
        assert name.endswith('-run')  # run_step checks the Core hash for each subprocess.
        assert Path(command[1]).parent == cwd
        assert command[2] == str(verify.ROOT) and command[4] == sys.executable
        assert Path(command[3]).is_relative_to(tmp_path / 'fixtures')
