import json
from pathlib import Path
from xml.etree import ElementTree


GUI = Path(__file__).resolve().parents[1] / "gui" / "MFAAvalonia"


def test_affected_dependencies_are_pinned_to_patched_compatible_versions():
    core = ElementTree.parse(GUI / "MFAAvalonia" / "MFAAvalonia.csproj")
    desktop = ElementTree.parse(GUI / "MFAAvalonia.Desktop" / "MFAAvalonia.Desktop.csproj")
    references = {item.attrib["Include"]: item.attrib.get("Version")
                  for project in (core, desktop)
                  for item in project.iter("PackageReference") if "Include" in item.attrib}
    assert references["MailKit"] == "4.16.0"
    assert references["SharpCompress"] == "0.48.0"
    assert references["Tmds.DBus.Protocol"] == "0.21.3"
    for project in ("MFAAvalonia", "MFAAvalonia.Desktop", "PcrUpdate.Tests"):
        graphs = json.loads((GUI / project / "packages.lock.json").read_text(encoding="utf-8"))["dependencies"]
        graph = graphs["net10.0"]
        for package, version in {"MailKit": "4.16.0", "MimeKit": "4.16.0", "SharpCompress": "0.48.0"}.items():
            assert graph[package]["resolved"] == version
            assert graph[package]["contentHash"]
        if project == "MFAAvalonia.Desktop":
            assert graph["Tmds.DBus.Protocol"]["resolved"] == "0.21.3"
            assert graph["Tmds.DBus.Protocol"]["type"] == "Direct"
