"""The reviewed index is a build input. Both runtime pins are independent of installed data."""
from hashlib import sha256
import json
import shutil

import pytest

from scripts import sync_clan_calibration_index as pins
from test_clan_battle_calibration import bundle
from test_clan_battle_native import calibration, plan


@pytest.fixture
def source(bundle):
    root, *_ = bundle
    for name in (pins.PYTHON_PIN, pins.GUI_PIN):
        (root / name).parent.mkdir(parents=True)
    pins.sync(root, write=True)
    return root


def current(root):
    return {path: (root / path).read_bytes() for path in (pins.PYTHON_PIN, pins.GUI_PIN)}


def test_verified_checkout_matches_both_runtime_pins():
    assert pins.sync(pins.ROOT) == sha256((pins.ROOT / pins.INDEX_PATH).read_bytes()).hexdigest()


def test_rollover_changes_both_sources_or_stops_build(source, bundle):
    _, _, first_entry, index, _ = bundle
    previous = current(source)
    index['entries'].append(dict(first_entry, content_version='202611131613'))
    (source / pins.INDEX_PATH).write_text(json.dumps(index), encoding='utf-8')
    with pytest.raises(ValueError, match='calibration pins out of date'):
        pins.sync(source)
    assert current(source) == previous
    digest = pins.sync(source, write=True)
    actual = current(source)
    assert all(digest.encode() in data and data != previous[path] for path, data in actual.items())
    assert pins.sync(source) == pins.sync(source, write=True) == digest
    assert current(source) == actual


@pytest.mark.parametrize('generated', [pins.GUI_PIN, pins.PYTHON_PIN])
def test_missing_or_changed_generated_pin_is_detected_then_recreated(source, generated):
    expected = current(source)
    path = source / generated
    for missing in (True, False):
        if missing:
            path.unlink()
        else:
            path.write_bytes(b'stale generated source')
        with pytest.raises(ValueError, match='out of date'):
            pins.sync(source)
        pins.sync(source, write=True)
        assert current(source) == expected


@pytest.mark.parametrize('damaged', ['profile', 'sample', 'template', 'duplicate_index_key', 'unknown_capability'])
def test_generator_cannot_bless_damaged_inputs(source, bundle, damaged):
    _, profile, entry, index, save = bundle
    previous = current(source)
    if damaged == 'template':
        image = source / 'assets/resource/image/boss.png'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'changed template')
        profile['pages']['result']['labels'][0] = dict(recognition='TemplateMatch', template='boss.png', sha256='0'*64)
        save()
    elif damaged == 'duplicate_index_key':
        (source / pins.INDEX_PATH).write_bytes(b'{"version":2,"entries":[],"entries":[]}')
    elif damaged == 'unknown_capability':
        index['version'] = 2
        entry['capabilities'] = ['unreviewed']
        save()
    else:
        path = entry['profile_path'] if damaged == 'profile' else profile['samples'][0]['path']
        with (source / path).open('ab') as stream:
            stream.write(b'changed')
    with pytest.raises(ValueError):
        pins.sync(source, write=True)
    assert current(source) == previous


def test_release_checks_staged_files_and_both_compiled_inputs(source, bundle, tmp_path):
    digest = pins.sync(source)
    player = tmp_path / 'player'
    gui = tmp_path / 'build-source'
    shutil.copytree(source / 'assets', player / 'assets')
    for checkout, name in [(gui, pins.GUI_PIN), (player, pins.PYTHON_PIN)]:
        target = checkout / name
        target.parent.mkdir(parents=True)
        shutil.copyfile(source / name, target)
    pins.check_packaged_index(player, gui, digest)
    for name, checkout in [(pins.GUI_PIN, gui), (pins.PYTHON_PIN, player), (pins.INDEX_PATH, player)]:
        file = checkout / name
        with file.open('ab') as stream:
            stream.write(b' ')
        with pytest.raises(ValueError, match='differs from build preflight'):
            pins.check_packaged_index(player, gui, digest)
        shutil.copyfile(source / name, file)
    _, profile, *_ = bundle
    (player / profile['samples'][0]['path']).unlink()
    with pytest.raises(ValueError, match='file_missing'):
        pins.check_packaged_index(player, gui, digest)
