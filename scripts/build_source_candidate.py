"""Build a local, source-only candidate from an exact reviewed allowlist.

The archive is deliberately marked BLOCKED while required public game/OCR
resources are unavailable. No network, installer, device or account is used.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile


SOURCE_MODULES = (
    "__init__", "abyss", "abyss_native", "cli", "daily", "daily_goal", "device", "event",
    "event_native", "home_guard", "local_config", "native", "observation", "onekey_history", "onekey_reconcile",
    "operator_entry", "preset_confirmation", "preset_execution",
    "preset_execution_native", "preset_reconcile", "preset_scan", "replay",
    "rewards", "schedule", "schedule_house", "schedule_onekey", "schedule_quick", "schedule_recovery", "supported_daily", "player_maintenance",
)
PIPELINES = (
    "abyss", "event", "preset_execution", "preset_scan", "presets", "return_home",
    "rewards", "schedule", "schedule_house", "schedule_onekey", "schedule_quick",
)
PUBLIC_FILES = (
    "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "pyproject.toml", "requirements.txt",
    "supported-daily.bat", "execute-supported.bat", "inspect-daily.bat", "supported-control.bat",
    "scripts/pcr.py", "scripts/build_source_candidate.py", "scripts/player-maintenance.py",
    "tests/test_source_candidate.py",
    "docs/REUSE.md", "docs/RELEASE_CANDIDATE.md", "docs/PUBLIC_TESTING.md",
    "docs/PLAYER_GUIDE.md",
    "docs/daily-config.template.json", "docs/DEVICE.md", "docs/INSPECT_DAILY.md",
    "docs/licenses/MaaFramework-LICENSE.md",
    "docs/licenses/MaaCommonAssets-LICENSE.txt",
    "assets/interface.json",
    "assets/resource/model/ocr/README.md",
    *(f"src/pcr_maa/{name}.py" for name in SOURCE_MODULES),
    *(f"assets/resource/pipeline/{name}.json" for name in PIPELINES),
)
# These are used by the current runtime, but no public distribution rights have
# been established for the game images or the exact OCR model weights.
REQUIRED_UNPUBLISHABLE = (
    "assets/resource/image/*.png (game templates)",
    "assets/resource/model/ocr/{det.onnx,rec.onnx,keys.txt} (OCR bundle)",
)
FORBIDDEN_PARTS = {".runtime", ".git", ".venv", "__pycache__", "fixtures"}
SENSITIVE_PATTERNS = (
    ("credential_assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|access[_-]?token|private[_-]?key)\b['\"]?\s*[=:]\s*['\"][^'\"]+['\"]")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("personal_path", re.compile(r"(?i)(?:[a-z]:[/\\]users[/\\]|/ho" + r"me/[^/\s]+/)")),
    ("cloud_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
)
MACHINE_PATH = re.compile(r"(?i)\b[a-z]:[/\\](?:program files|apps|tools)[/\\]")

GUI_SOURCE_INVENTORY = "docs/gui-source-files.json"
GUI_BUILD_ENTRYPOINT = "gui/MFAAvalonia/MFAAvalonia.Desktop/MFAAvalonia.Desktop.csproj"
GUI_PROJECTS = (
    "MFAAvalonia", "MFAAvalonia.Desktop", "LazyStaticGenerator", "SukiUI",
    "Markdown.Avalonia", "Markdown.Avalonia.Html", "Markdown.Avalonia.Svg",
    "Markdown.Avalonia.Tight", "Markdown.Avalonia.SyntaxHigh", "Markdown.Avalonia.Math",
    "ColorDocument.Avalonia", "ColorTextBlock.Avalonia",
)
GUI_TEXT_SUFFIXES = {
    ".cs", ".axaml", ".xaml", ".resx", ".tt", ".csproj", ".props", ".targets",
    ".manifest", ".sksl", ".tsv", ".txt", ".sh", ".bat",
}
GUI_BINARY_SUFFIXES = {".png", ".ico", ".bmp", ".ttf", ".otf", ".wav"}
GUI_EXCLUDED_PARTS = {"bin", "obj", "logs", "ledger", "ledgers", "publishprofiles"}
# This small suite runs from an extracted companion without private replay assets.
# Keep its shared device-isolation fixtures together with the selected tests.
GUI_OFFLINE_TEST_FILES = {
    'tests/conftest.py', 'tests/test_alpha_owner.py', 'tests/test_gui_contract.py',
    'tests/test_owner_source_channel.py', 'tests/test_player_error_guidance.py',
    'tests/test_player_result_copy.py', 'tests/test_player_archive.py',
    'tests/test_player_update.py', 'tests/test_player_diagnostics.py',
}
GUI_EXTRA_TEXT_FILES = {
    *GUI_OFFLINE_TEST_FILES,
    'docs/licenses/COMPONENTS.json',
    'docs/licenses/gui/NotoSansSC-OFL.txt',
    'docs/licenses/gui/NotoSansSC-PROVENANCE.json',
    'docs/licenses/gui/Roboto-LICENSE.txt',
    'docs/licenses/gui/SukiUI-LICENSE.txt',
    'docs/licenses/nuget/Avalonia.Angle.Windows.Natives/2.1.25547.20250602/LICENSE',
    'docs/licenses/nuget/BouncyCastle.Cryptography/2.6.1/LICENSE.md',
    'docs/licenses/nuget/CalcBindingAva/2.5.3/LICENSE',
    'docs/licenses/nuget/CommunityToolkit.Mvvm/8.4.0/License.md',
    'docs/licenses/nuget/CrossPlatformMemoryPinning/1.0.2/LICENSE.txt',
    'docs/licenses/nuget/CrossPlatformProtectedData/1.0.3/LICENSE.txt',
    'docs/licenses/nuget/ElipticCurveX509Certificates/1.0.6/LICENSE.txt',
    'docs/licenses/nuget/HarfBuzzSharp.NativeAssets.Win32/8.3.1.1/LICENSE.txt',
    'docs/licenses/nuget/HarfBuzzSharp/8.3.1.1/LICENSE.txt',
    'docs/licenses/nuget/Maa.AgentBinary/1.2.0/LICENSE',
    'docs/licenses/nuget/Maa.AgentBinary/1.2.0/README.md',
    'docs/licenses/nuget/Maa.AgentBinary/1.2.0/maa.agentbinary.nuspec',
    'docs/licenses/nuget/Newtonsoft.Json/13.0.5-beta1/LICENSE.md',
    'docs/licenses/nuget/SecureArrays/1.0.41/LICENSE.txt',
    'docs/licenses/nuget/SkiaSharp.NativeAssets.Win32/3.119.1/LICENSE.txt',
    'docs/licenses/nuget/SkiaSharp/3.119.1/LICENSE.txt',
    'docs/licenses/nuget/SoundFlow/1.4.0/LICENSE.md',
    'docs/licenses/nuget/System.Security.Cryptography.Pkcs/8.0.1/LICENSE.TXT',
    'docs/licenses/nuget/WindowsAPICodePack/8.0.14/License.md',
    "gui/MFAAvalonia/Directory.Build.props", "gui/MFAAvalonia/LICENSE",
    "gui/MFAAvalonia/README.md", "gui/MFAAvalonia/README_en.md", "gui/MFAAvalonia/cliff.toml",
    "gui/MFAAvalonia/docs/en/external-notification.md", "gui/MFAAvalonia/docs/zh/外部通知.md",
    "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "docs/REUSE.md",
    "docs/RELEASE_CANDIDATE.md",
    "docs/licenses/MaaFramework-LICENSE.md", "docs/licenses/MaaCommonAssets-LICENSE.txt",
    "pyproject.toml", "requirements.txt", "interface.json", "assets/interface.json",
    "scripts/pcr.py", "scripts/start-alpha-agent.py", "scripts/start-alpha-owned-agent.py",
    "scripts/validate-alpha-config.py", "scripts/create_pcr_apphost.proj", "scripts/player-maintenance.py",
    "scripts/build_pcr_player_candidate.py", "scripts/build_source_candidate.py",
    "docs/PLAYER_GUIDE.md",
}
GUI_EXTRA_TEXT_FILES.update({
    'docs/OFFLINE_UPDATES.md',
    'scripts/build_pcr_player_patch.py',
    'tests/test_player_candidate.py',
    'tests/test_player_delta_update.py',
    'tests/test_player_patch_builder.py',
    '导入补丁包.bat',
    'scripts/generate_gui_release_assets.py',
    'docs/GUI_ASSET_CHANGES.md',
    'docs/licenses/gui/OriginalAssets-PROVENANCE.json',
    'docs/licenses/gui/UserProvidedLogo-PROVENANCE.json',
})
GUI_EXTRA_BINARY_FILES = set()
GUI_REQUIRED_FILES = {
    *GUI_OFFLINE_TEST_FILES,
    *(f"gui/MFAAvalonia/{name}/{name}.csproj" for name in GUI_PROJECTS),
    "gui/MFAAvalonia/Directory.Build.props", "gui/MFAAvalonia/LICENSE",
    "gui/MFAAvalonia/MFAAvalonia/NuGet.Config", "interface.json",
    "src/pcr_maa/agent_entry.py", "src/pcr_maa/alpha_owner.py", "src/pcr_maa/player_maintenance.py",
    "scripts/start-alpha-owned-agent.py", "assets/resource/pipeline/alpha_entry.json",
}


class CandidateError(RuntimeError):
    """Unsafe or unstable candidate input; no archive should be published."""


def _is_link(path):
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError:
        return False


def _safe_relative(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise CandidateError(f"unsafe archive path: {name}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise CandidateError(f"unsafe archive path: {name}")
    if any(part in FORBIDDEN_PARTS or part.startswith(".") for part in path.parts):
        raise CandidateError(f"excluded archive path: {name}")
    return path


def _read_once(path):
    before = path.stat()
    if before.st_nlink > 1:
        raise CandidateError(f"hardlink in archive path: {path.name}")
    data = path.read_bytes()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_nlink) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_nlink):
        raise CandidateError(f"unstable source: {path.name}")
    return data


def _stable_read(path):
    for _ in range(3):
        try:
            first = _read_once(path)
            second = _read_once(path)
        except (OSError, CandidateError):
            continue
        if hashlib.sha256(first).digest() == hashlib.sha256(second).digest():
            return first
    raise CandidateError(f"unstable source: {path.name}")


def _checked_file(root, name, output):
    rel = _safe_relative(name)
    path = root.joinpath(*rel.parts)
    if path == output or output in path.parents:
        raise CandidateError(f"output selected as input: {name}")
    current = root
    for part in rel.parts:
        current = current / part
        if _is_link(current):
            raise CandidateError(f"symlink in archive path: {name}")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise CandidateError(f"missing or escaped source: {name}")
    if path.lstat().st_nlink > 1:
        raise CandidateError(f"hardlink in archive path: {name}")
    return path


def _findings(name, data):
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CandidateError(f"non-UTF-8 text source: {name}") from exc
    findings = []
    for line_number, line in enumerate(content.splitlines(), 1):
        for category, pattern in SENSITIVE_PATTERNS:
            if pattern.search(line):
                findings.append(dict(category=category, path=name, line=line_number))
        if MACHINE_PATH.search(line):
            findings.append(dict(category="machine_path", path=name, line=line_number))
    return findings


def _unreviewed_runtime_files(root, names):
    selected = set(names)
    directories = {PurePosixPath(name).parent for name in names
                   if name.startswith("src/") or name.startswith("assets/resource/pipeline/")}
    unknown = []
    for directory in sorted(directories):
        parent = root.joinpath(*directory.parts)
        for path in parent.iterdir():
            if path.suffix in {".py", ".json"} and path.relative_to(root).as_posix() not in selected:
                unknown.append(path.relative_to(root).as_posix())
    return sorted(unknown)




def _gui_binding_reference(value):
    match = re.fullmatch(r"\{\s*(?:Binding|TemplateBinding)\s+([^{}]+)\}", value)
    if not match:
        return False
    parts = [part.strip() for part in match.group(1).split(",")]
    path = re.sub(r"^Path\s*=\s*", "", parts[0])
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", path):
        return False
    allowed = {"Mode": "TwoWay", "UpdateSourceTrigger": "PropertyChanged"}
    seen = set()
    for part in parts[1:]:
        parameter = re.fullmatch(r"(Mode|UpdateSourceTrigger)\s*=\s*(\w+)", part)
        if not parameter or parameter.group(1) in seen or allowed[parameter.group(1)] != parameter.group(2):
            return False
        seen.add(parameter.group(1))
    return True


def _gui_source_findings(name, data):
    findings = _findings(name, data)
    if not name.startswith("gui/MFAAvalonia/") or PurePosixPath(name).suffix not in {".axaml", ".xaml"}:
        return findings
    lines = data.decode("utf-8").splitlines()
    credential_pattern = next(pattern for category, pattern in SENSITIVE_PATTERNS if category == "credential_assignment")
    kept = []
    for finding in findings:
        if finding["category"] != "credential_assignment":
            kept.append(finding)
            continue
        line = lines[finding["line"] - 1]
        attributes = list(re.finditer(r"[\w:.-]+\s*=\s*(?P<quote>['\"])(?P<value>.*?)\1", line))
        hits = list(credential_pattern.finditer(line))
        # Every credential match on this line must be inside a complete,
        # property-only XML binding; one literal match retains the finding.
        for hit in hits:
            attribute = next((item for item in attributes
                              if item.start() <= hit.start() and hit.end() <= item.end()), None)
            if attribute is None or not _gui_binding_reference(attribute.group("value")):
                kept.append(finding)
                break
    return kept


def _gui_source_path(name, binary=False):
    rel = _safe_relative(name)
    if any(part.lower() in GUI_EXCLUDED_PARTS for part in rel.parts):
        raise CandidateError(f"excluded GUI source path: {name}")
    if binary:
        allowed = (name in GUI_EXTRA_BINARY_FILES or
                   len(rel.parts) >= 4 and rel.parts[:2] == ("gui", "MFAAvalonia") and
                   rel.parts[2] in GUI_PROJECTS)
        if not allowed or rel.suffix.lower() not in GUI_BINARY_SUFFIXES:
            raise CandidateError(f"unreviewed GUI binary resource: {name}")
    else:
        allowed = (
            name in GUI_EXTRA_TEXT_FILES or
            len(rel.parts) >= 4 and rel.parts[:2] == ("gui", "MFAAvalonia") and
            rel.parts[2] in GUI_PROJECTS and
            (rel.suffix.lower() in GUI_TEXT_SUFFIXES or rel.name == "NuGet.Config") or
            len(rel.parts) == 3 and rel.parts[:2] == ("src", "pcr_maa") and rel.suffix == ".py" or
            len(rel.parts) == 4 and rel.parts[:3] == ("assets", "resource", "pipeline") and rel.suffix == ".json"
        )
        if not allowed:
            raise CandidateError(f"unreviewed GUI companion text: {name}")
    return rel


def build_gui_source_companion(root, output):
    """Build local matching-source material; never establish distribution eligibility."""
    root = Path(root).resolve(strict=True)
    output = Path(output).absolute()
    inventory_path = _checked_file(root, GUI_SOURCE_INVENTORY, output)
    inventory_bytes = _stable_read(inventory_path)
    try:
        inventory = json.loads(inventory_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateError("invalid GUI source inventory") from exc
    if (not isinstance(inventory, dict) or inventory.get("schema_version") != 1 or
            inventory.get("purpose") != "local_gui_source_companion" or
            inventory.get("distribution_ready") is not False or
            inventory.get("build_entrypoint") != GUI_BUILD_ENTRYPOINT or
            inventory.get("project_roots") != list(GUI_PROJECTS)):
        raise CandidateError("invalid GUI source inventory metadata")
    text_files, binary_files = inventory.get("text_files"), inventory.get("binary_files")
    if (not isinstance(text_files, list) or not isinstance(binary_files, list) or
            not all(isinstance(name, str) for name in text_files + binary_files)):
        raise CandidateError("invalid GUI source inventory files")
    names = text_files + binary_files
    if len(names) != len(set(names)) or GUI_SOURCE_INVENTORY in names:
        raise CandidateError("duplicate GUI source inventory path")
    for name in text_files:
        _gui_source_path(name)
    for name in binary_files:
        _gui_source_path(name, binary=True)
    if not GUI_REQUIRED_FILES.issubset(names):
        raise CandidateError("incomplete GUI desktop/runtime project inventory")
    missing_content = inventory.get("missing_declared_content", [])
    if not isinstance(missing_content, list) or not all(isinstance(name, str) for name in missing_content):
        raise CandidateError("invalid missing GUI content inventory")
    for name in missing_content:
        _gui_source_path(name, binary=True)
        if name in names:
            raise CandidateError(f"missing GUI content also selected: {name}")
    return build_candidate(
        root, output, [*names, GUI_SOURCE_INVENTORY], required_assets=[],
        _gui_source=dict(inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
                         binary_files=binary_files, missing_declared_content=missing_content),
    )


def build_candidate(root, output, allowlist=PUBLIC_FILES, required_assets=REQUIRED_UNPUBLISHABLE,
                    *, _gui_source=None):
    root = Path(root).resolve(strict=True)
    output = Path(output).absolute()
    if output.exists():
        raise CandidateError("output directory already exists")
    if not output.is_relative_to(root):
        raise CandidateError("output must be inside source root")
    current = output
    while current != root:
        if _is_link(current):
            raise CandidateError("symlink in output path")
        current = current.parent
    # Keep the original link/reparse checks, then normalize parent traversal
    # before any reads or mkdir; absolute() alone retains '..' components.
    output = output.resolve()
    if not output.is_relative_to(root):
        raise CandidateError("output must be inside source root")
    names = sorted(allowlist)
    if len(names) != len(set(names)):
        raise CandidateError("duplicate archive path")
    paths = {name: _checked_file(root, name, output) for name in names}
    snapshots = {name: _stable_read(path) for name, path in paths.items()}
    binary_files = set(_gui_source["binary_files"]) if _gui_source else set()
    if _gui_source and hashlib.sha256(snapshots[GUI_SOURCE_INVENTORY]).hexdigest() != _gui_source["inventory_sha256"]:
        raise CandidateError("GUI source inventory changed during snapshot")
    findings = [finding for name, data in snapshots.items() if name not in binary_files
                for finding in (_gui_source_findings(name, data) if _gui_source else _findings(name, data))]
    # A second complete pass detects changes while other files were inspected.
    for name, path in paths.items():
        if hashlib.sha256(_stable_read(path)).digest() != hashlib.sha256(snapshots[name]).digest():
            raise CandidateError(f"unstable source: {name}")
    files = [dict(path=name, sha256=hashlib.sha256(snapshots[name]).hexdigest(),
                  size=len(snapshots[name])) for name in names]
    unreviewed = _unreviewed_runtime_files(root, names)
    missing = list(required_assets)
    blockers = []
    if missing:
        blockers.append("required_public_assets_unavailable")
    if any(item["category"] == "machine_path" for item in findings):
        blockers.append("machine_specific_source")
    if any(item["category"] != "machine_path" for item in findings):
        blockers.append("sensitive_content")
    if unreviewed:
        blockers.append("unreviewed_runtime_files")
    # The public README is reviewed and required by the exact allowlist. It
    # still passes through the same content checks as every other text file.
    report = dict(result="BLOCKED" if blockers else "READY_FOR_REVIEW",
                  files=files, findings=findings, missing_required_assets=missing,
                  unreviewed_runtime_files=unreviewed, blockers=blockers,
                  archive=(("gui-source-companion.zip" if _gui_source else "source-candidate.zip")
                            if "sensitive_content" not in blockers else None))
    if _gui_source:
        source_records = [item for item in files if item["path"] != GUI_SOURCE_INVENTORY]
        snapshot_digest = hashlib.sha256("".join(
            f'{item["sha256"]}  {item["path"]}\n' for item in source_records).encode("utf-8")).hexdigest()
        report.update(purpose="local_gui_source_companion", distribution_ready=False,
                      build_entrypoint=GUI_BUILD_ENTRYPOINT,
                      source_inventory=dict(path=GUI_SOURCE_INVENTORY,
                                            sha256=_gui_source["inventory_sha256"]),
                      source_snapshot_sha256=snapshot_digest,
                      binary_resources=sorted(binary_files),
                      missing_declared_content=_gui_source["missing_declared_content"])
    output.mkdir(parents=True, exist_ok=False)
    if report["archive"]:
        with zipfile.ZipFile(output / report["archive"], "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, snapshots[name])
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True, help="new local candidate directory")
    parser.add_argument("--gui-source-companion", action="store_true",
                        help="local desktop/Agent source companion; not distribution eligibility")
    args = parser.parse_args(argv)
    try:
        report = (build_gui_source_companion(args.root, args.output) if args.gui_source_companion
                  else build_candidate(args.root, args.output))
    except (CandidateError, OSError) as exc:
        print(f"candidate stopped: {exc}", file=sys.stderr)
        return 2
    summary = {"result": report["result"], "file_count": len(report["files"]),
               "blockers": report["blockers"], "output": str(args.output)}
    if args.gui_source_companion:
        summary.update(purpose=report["purpose"], distribution_ready=False)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["result"] == "READY_FOR_REVIEW" else 3


if __name__ == "__main__":
    raise SystemExit(main())
