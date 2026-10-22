"""Run local public/GUI checks without installing dependencies or touching a player."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from xml.sax.saxutils import quoteattr

ROOT = Path(__file__).resolve().parents[1]
PROFILE_CHECK_MODES = ("clan-daily-only", "clan-workflow-only", "clan-formal-only",
                       "clan-postsave-only", "profile-registration-only")


def check_python() -> None:
    if platform.python_implementation() != "CPython" or sys.version_info[:2] != (3, 13):
        raise RuntimeError("Use complete CPython 3.13 (CI uses 3.13.16).")
    try:
        import venv  # noqa: F401
        import pytest  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Use complete CPython with requirements-dev.txt installed; embedded player Python is unsupported.") from exc
    if sys.flags.isolated:
        raise RuntimeError("Use non-isolated development Python; subprocess tests require PYTHONPATH.")


def public_command(output: Path) -> list[str]:
    return [sys.executable, "-X", "utf8", "-B", "-u", "-m", "pytest",
            "-p", "scripts.public_test_policy", "-m", "not private_replay",
            "--basetemp", str(output / "test-temp"),
            "--junitxml", str(output / "public-tests.xml")]


def gui_commands(dotnet: str, gui: Path, output: Path) -> list[tuple[str, list[str], Path]]:
    commands = []
    common = ["-p:Platform=x64", "-p:Configuration=Release", "-p:UsedAvaloniaProducts="]
    for name in ("MFAAvalonia.Desktop", "PcrUpdate.Tests", "PcrProfileSwitch.Tests", "PcrLabyrinthCredentials.Tests"):
        project = gui / name / (name + ".csproj")
        runtime = ["-r", "win-x64"] if name in ("MFAAvalonia.Desktop", "PcrUpdate.Tests") else []
        properties = common + ([f"-p:PcrCoreDirectory={gui / 'bin/x64/Release'}",
                               f"-p:PcrNativeDirectory={gui / 'bin/x64/Release/runtimes/win-x64/native'}"]
                              if not runtime else [])
        commands.append((name + "-restore", [dotnet, "restore", str(project), "--locked-mode",
                         "--configfile", str(output / "NuGet.Offline.Config"), "-p:NuGetAudit=false",
                         *runtime, *properties], gui))
        commands.append((name + "-build", [dotnet, "build", str(project), "-c", "Release",
                         "--no-restore", *runtime, *properties], gui))
        if name == "MFAAvalonia.Desktop":
            continue
        binary = gui / name / "bin/x64/Release/net10.0"
        if runtime:
            binary /= "win-x64"
        command = [dotnet, str(binary / (name + ".dll"))]
        if name in ("PcrUpdate.Tests", "PcrLabyrinthCredentials.Tests"):
            command.append(str(output / "fixtures" / name))
        if name == "PcrLabyrinthCredentials.Tests":
            command.append(str(ROOT))
        if name == "PcrProfileSwitch.Tests":
            command.extend([str(ROOT), str(output / "fixtures" / name), sys.executable])
        commands.append((name + "-run", command, binary))
        if name == "PcrProfileSwitch.Tests":
            for mode in PROFILE_CHECK_MODES:
                commands.append((name + "-" + mode + "-run",
                    [dotnet, str(binary / (name + ".dll")), str(ROOT),
                     str(output / "fixtures" / (name + "-" + mode)), sys.executable, "--" + mode], binary))
    return commands


def run_step(name: str, command: list[str], cwd: Path, output: Path,
             env: dict[str, str], receipt: dict, *, timeout: int) -> None:
    log = output / (name + ".log")
    record = {"name": name, "command": command, "cwd": str(cwd), "log": str(log)}
    receipt["steps"].append(record)
    if name.endswith("-run"):
        core = ROOT / "gui/MFAAvalonia/bin/x64/Release/MFAAvalonia.Core.dll"
        record["core_sha256"] = hashlib.sha256(core.read_bytes()).hexdigest()
        if hashlib.sha256((cwd / core.name).read_bytes()).hexdigest() != record["core_sha256"]:
            raise RuntimeError(f"{name} does not reference the current Desktop Core build.")
    print(f"{name}: {log}", flush=True)
    with log.open("w", encoding="utf-8") as stream:
        try:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=timeout,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            record["exit_code"] = result.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            record["error"] = str(exc)
            raise RuntimeError(f"{name} could not finish; see {log}") from exc
    if result.returncode:
        raise RuntimeError(f"{name} exited {result.returncode}; see {log}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scope", choices=("public", "gui", "all"), nargs="?", default="public")
    parser.add_argument("--dotnet", default="dotnet", help="SDK executable; defaults to PATH")
    parser.add_argument("--nuget-cache", type=Path, default=Path(os.environ.get("NUGET_PACKAGES", Path.home() / ".nuget/packages")),
                        help="Existing offline NuGet package cache; never downloads packages")
    args = parser.parse_args(argv)
    output = ROOT / ".runtime/verification" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output.mkdir(parents=True)
    receipt = {"scope": args.scope, "root": str(ROOT), "python": sys.version,
               "executable": sys.executable, "status": "running", "steps": []}
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    env.pop("PYTEST_ADDOPTS", None)  # A local filter/collect-only must not narrow public verification.
    for variable, folder in (("TEMP", "temp"), ("TMP", "temp"), ("LOCALAPPDATA", "local-appdata"),
                             ("DOTNET_CLI_HOME", "dotnet-home"), ("NUGET_HTTP_CACHE_PATH", "nuget-http")):
        (output / folder).mkdir(exist_ok=True)
        env[variable] = str(output / folder)
    try:
        check_python()
        commands = []
        if args.scope in ("public", "all"):
            commands.append(("public-tests", public_command(output), ROOT))
        if args.scope in ("gui", "all"):
            if os.name != "nt":
                raise RuntimeError("GUI checks require Windows (including current-user DPAPI).")
            dotnet = shutil.which(args.dotnet)
            if not dotnet:
                raise RuntimeError(".NET SDK not found; pass --dotnet with its executable path.")
            try:
                expected = json.loads((ROOT / "global.json").read_text(encoding="utf-8"))["sdk"]["version"]
                if not isinstance(expected, str) or not expected:
                    raise ValueError("missing SDK version")
            except (ValueError, KeyError, TypeError) as exc:
                raise RuntimeError("global.json must contain sdk.version.") from exc
            env.update(DOTNET_ROOT=str(Path(dotnet).parent), DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
                       DOTNET_CLI_TELEMETRY_OPTOUT="1", MSBUILDDISABLENODEREUSE="1")
            run_step("dotnet-version", [dotnet, "--version"], ROOT, output, env, receipt, timeout=60)
            actual = (output / "dotnet-version.log").read_text(encoding="utf-8-sig").strip()
            if actual != expected:
                raise RuntimeError(f"global.json requires SDK {expected}; got {actual!r}.")
            cache = args.nuget_cache.resolve()
            if not cache.is_dir():
                raise RuntimeError(f"NuGet cache missing: {cache}. Prepare locked dependencies separately.")
            env["NUGET_PACKAGES"] = str(cache)
            (output / "NuGet.Offline.Config").write_text(
                '<?xml version="1.0" encoding="utf-8"?><configuration><packageSources><clear />'
                f'<add key="local-cache" value={quoteattr(str(cache))} />'
                '</packageSources></configuration>', encoding="utf-8")
            gui = ROOT / "gui/MFAAvalonia"
            commands.extend(gui_commands(dotnet, gui, output))
        for name, command, cwd in commands:
            run_step(name, command, cwd, output, env, receipt,
                     timeout=3600 if name == "public-tests" else 600)
        receipt["status"] = "passed"
        return 0
    except (OSError, RuntimeError, KeyboardInterrupt) as exc:
        receipt["status"] = "failed"
        receipt["error"] = str(exc) or "interrupted"
        print(receipt["error"], file=sys.stderr)
        return 1
    finally:
        (output / "result.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{receipt['status']}: {output / 'result.json'}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
