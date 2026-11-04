"""The local GUI companion inventory must match the GUI product tree.

``docs/gui-source-files.json`` is the exact path list that
``scripts/build_source_candidate.py`` copies into the local source-only GUI
companion.  Nothing compared it with the tree, so it silently fell behind: the
four ``PcrCaptcha*`` files and every product source added since r24 were absent,
and a companion built from the list could not compile (2026-11-04 review).

Policy: the private C# acceptance harness projects (directories ending in
``.Tests``) are deliberately excluded and are recorded as such in the inventory
notes; every other GUI product source with a text/binary suffix must be listed.
No device, network or build is used here.
"""
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / 'docs/gui-source-files.json'
HARNESS_SUFFIX = '.tests'


def _builder():
    spec = importlib.util.spec_from_file_location(
        'build_source_candidate', ROOT / 'scripts' / 'build_source_candidate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def inventory():
    return json.loads(INVENTORY.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def listed(inventory):
    return {name: 'text' for name in inventory['text_files']} | {
        name: 'binary' for name in inventory['binary_files']}


@pytest.fixture(scope='module')
def tree():
    """Every GUI product source outside the excluded build and harness directories."""
    builder = _builder()
    gui = ROOT / 'gui'
    found = {}
    for path in gui.rglob('*'):
        if not path.is_file():
            continue
        parts = [part.lower() for part in path.relative_to(gui).parts]
        if set(parts) & builder.GUI_EXCLUDED_PARTS:
            continue
        if any(part.endswith(HARNESS_SUFFIX) for part in parts):
            continue
        suffix = path.suffix.lower()
        if suffix in builder.GUI_TEXT_SUFFIXES:
            found[path.relative_to(ROOT).as_posix()] = 'text'
        elif suffix in builder.GUI_BINARY_SUFFIXES:
            found[path.relative_to(ROOT).as_posix()] = 'binary'
    return found


def test_inventory_selects_every_gui_product_source(listed, tree):
    assert sorted(set(tree) - set(listed)) == [], (
        'GUI product sources are missing from docs/gui-source-files.json')


def test_inventory_selects_only_existing_files_with_the_declared_kind(listed, tree):
    absent = sorted(name for name in listed
                    if name.startswith('gui/') and not (ROOT / name).is_file())
    miscategorised = sorted(name for name, kind in listed.items()
                            if name in tree and kind != tree[name])
    assert absent == [], 'docs/gui-source-files.json lists absent GUI files'
    assert miscategorised == [], 'docs/gui-source-files.json miscategorises GUI files'


def project_references():
    """Files the GUI product projects consume by explicit (non-wildcard) path."""
    pattern = re.compile(r'<(?:EmbeddedResource|AvaloniaResource|None|Content|Compile)\s+Include="([^"]+)"')
    found = set()
    for project in (ROOT / 'gui').rglob('*.csproj'):
        if any(part.lower().endswith(HARNESS_SUFFIX) for part in project.parts):
            continue
        text = project.read_text(encoding='utf-8', errors='replace')
        for include in pattern.findall(text):
            if '*' in include:
                continue
            target = (project.parent / include.replace('\\', '/')).resolve()
            if target.is_file():
                found.add(os.path.relpath(target, ROOT).replace(os.sep, '/'))
    return found


def test_inventory_selects_every_file_a_gui_project_references(listed):
    assert sorted(project_references() - set(listed)) == [], (
        'files consumed by the GUI projects are missing from docs/gui-source-files.json')


def test_the_companion_builder_accepts_every_listed_path(inventory):
    builder = _builder()
    rejected = []
    for name in inventory['text_files']:
        try:
            builder._gui_source_path(name)
        except Exception:
            rejected.append(name)
    for name in inventory['binary_files']:
        try:
            builder._gui_source_path(name, binary=True)
        except Exception:
            rejected.append(name)
    names = inventory['text_files'] + inventory['binary_files']
    assert rejected == [], 'the GUI companion builder rejects these reviewed paths'
    assert len(names) == len(set(names))
    assert builder.GUI_REQUIRED_FILES <= set(names)


def test_acceptance_harness_projects_stay_excluded(inventory, listed):
    assert [name for name in listed if '/PcrProfileSwitch.Tests/' in name] == []
    assert any('*.Tests' in note for note in inventory['notes']), (
        'the *.Tests exclusion must stay documented in the inventory notes')
