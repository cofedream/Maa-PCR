"""Bundled calibration contract; synthetic frames do not qualify a real device."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from test_clan_battle_native import calibration, plan
from pcr_maa import clan_battle_calibration as module, native


@pytest.fixture
def bundle(calibration, plan, tmp_path, monkeypatch):
    root = tmp_path / 'install'
    root.mkdir()
    monkeypatch.setattr(native, 'RESOURCE', root / 'assets/resource')
    profile = deepcopy(calibration)
    profile['evidence_kind'] = 'device_saved_frame'
    sample_path = root / 'assets/resource/clan-battle-calibrations/samples/synthetic.png'
    sample_path.parent.mkdir(parents=True)
    sample_path.write_bytes(Path(calibration['samples'][0]['path']).read_bytes())
    for sample in profile['samples']:
        sample['path'] = sample_path.relative_to(root).as_posix()
    entry = dict(calibration_id=profile['calibration_id'], server=plan['server'],
        content_version='202610141431', frame_size=[1280, 720],
        profile_path='assets/resource/clan-battle-calibrations/synthetic.json', profile_sha256='')
    plan['content_version'] = entry['content_version']
    index = dict(version=1, entries=[entry])

    def save():
        profile_path = root / entry['profile_path']
        profile_path.write_text(json.dumps(profile), encoding='utf-8')
        entry['profile_sha256'] = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        index_path = root / module.INDEX_PATH
        index_path.write_text(json.dumps(index), encoding='utf-8')
        monkeypatch.setattr(module, 'INDEX_SHA256', hashlib.sha256(index_path.read_bytes()).hexdigest())
    save()
    return root, profile, entry, index, save


def test_shipped_registry_is_empty_and_pinned(plan):
    path = native.ROOT / module.INDEX_PATH
    assert hashlib.sha256(path.read_bytes()).hexdigest() == module.INDEX_SHA256
    assert json.loads(path.read_text(encoding='utf-8')) == {'version': 1, 'entries': []}
    with pytest.raises(RuntimeError, match='calibration_missing'):
        module.load_calibration(plan)


def test_loads_only_reviewed_bundled_profile_and_resolves_samples(bundle, plan):
    root, profile, _, _, _ = bundle
    loaded = module.load_calibration(plan, root=root)
    assert loaded['evidence_kind'] == 'device_saved_frame'
    assert all(Path(row['path']).is_relative_to(root) and Path(row['path']).is_file()
               for row in loaded['samples'])
    assert all(not Path(row['path']).is_absolute() for row in profile['samples'])


@pytest.mark.parametrize('auto,switches', [(False, []), (True, [{'remaining_seconds': 85}])])
def test_auto_only_loader_cannot_grant_timed_or_manual_battle(bundle, plan, auto, switches):
    root, *_ = bundle
    plan.update(auto=auto, switches=switches)
    with pytest.raises(RuntimeError, match='calibration_auto_only'):
        module.load_calibration(plan, root=root)


@pytest.mark.parametrize('kind', ['offline_replay', 'validated', None])
def test_replay_or_self_declared_profile_is_not_production_qualification(bundle, plan, kind):
    root, profile, _, _, save = bundle
    profile['evidence_kind'] = kind
    save()
    with pytest.raises(ValueError, match='not_reviewed_device_profile'):
        module.load_calibration(plan, root=root)


def test_self_filled_validated_flag_is_not_a_profile_field(bundle, plan):
    root, profile, _, _, save = bundle
    profile['validated'] = True
    save()
    with pytest.raises(ValueError, match='not_reviewed_device_profile'):
        module.load_calibration(plan, root=root)


@pytest.mark.parametrize('file', ['index', 'profile', 'sample'])
def test_changed_bundle_bytes_are_rejected(bundle, plan, file):
    root, profile, entry, _, _ = bundle
    relative = {'index': module.INDEX_PATH, 'profile': entry['profile_path'],
                'sample': profile['samples'][0]['path']}[file]
    path = root / relative
    path.write_bytes(path.read_bytes() + b' changed')
    with pytest.raises(ValueError, match='file_changed'):
        module.load_calibration(plan, root=root)


def test_other_content_version_has_no_matching_profile(bundle, plan):
    root, *_ = bundle
    plan['content_version'] = '202610141432'
    with pytest.raises(RuntimeError, match='calibration_missing'):
        module.load_calibration(plan, root=root)


def test_duplicate_profile_identity_is_ambiguous(bundle, plan):
    root, _, entry, index, save = bundle
    index['entries'].append(deepcopy(entry))
    save()
    with pytest.raises(ValueError, match='index_ambiguous'):
        module.load_calibration(plan, root=root)


@pytest.mark.parametrize('path', ['../outside.png', 'C:/outside.png', '//server/share/a.png',
    r'assets\resource\sample.png', 'assets/resource/sample.png:stream'])
def test_profile_cannot_read_outside_install_or_ads(bundle, plan, path):
    root, profile, _, _, save = bundle
    profile['samples'][0]['path'] = path
    save()
    with pytest.raises(ValueError, match='path_invalid'):
        module.load_calibration(plan, root=root)


def test_external_template_is_rejected_before_native_pipeline(bundle, plan):
    root, profile, _, _, save = bundle
    profile['pages']['target']['labels'][0] = dict(recognition='TemplateMatch', roi=[1, 1, 2, 2],
        template='../../../../outside.png', sha256='0'*64)
    save()
    with pytest.raises(ValueError, match='path_invalid'):
        module.load_calibration(plan, root=root)


def test_missing_return_calibration_is_not_an_executable_profile(bundle, plan):
    root, profile, _, _, save = bundle
    profile['actions'].pop('return_to_target')
    save()
    with pytest.raises(ValueError, match='actions_missing'):
        module.load_calibration(plan, root=root)


def test_auto_profile_does_not_load_timing_file_even_if_listed(bundle, plan):
    root, profile, _, _, save = bundle
    profile['timing_measurements'] = dict(path='//server/untrusted.json', sha256='0'*64)
    save()
    assert 'timing_measurements' not in module.load_calibration(plan, root=root)
