"""Bundled calibration contract; synthetic frames do not qualify a real device."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from test_clan_battle_native import calibration, calibration_v2, plan
from test_clan_battle_daily_native import executable, profile
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


def test_shipped_registry_is_pinned_and_each_profile_is_valid(plan):
    path = native.ROOT / module.INDEX_PATH
    assert hashlib.sha256(path.read_bytes()).hexdigest() == module.INDEX_SHA256
    index = json.loads(path.read_text(encoding='utf-8'))
    if not index['entries']:
        with pytest.raises(RuntimeError, match='calibration_missing'):
            module.load_calibration(plan)
    for entry in index['entries']:
        profile = module.read_profile(native.ROOT, entry)
        for capability in entry.get('capabilities', ['simulation']):
            if capability != 'timed':
                targets = profile.get(f'{capability}_targets', [{}])
                assert targets
                for target in targets:
                    selected = dict(plan, server=entry['server'], content_version=entry['content_version'])
                    selected.update(target)
                    assert module.load_calibration(selected, capability=capability)['calibration_id'] == entry['calibration_id']


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


@pytest.fixture
def v2_bundle(bundle, monkeypatch):
    root, profile, entry, index, save = bundle
    index['version'] = profile['version'] = 2
    entry['capabilities'] = ['simulation']
    # This fixture isolates loader authority from the independently tested page schema.
    monkeypatch.setattr('pcr_maa.clan_battle_native.calibration_pipeline', lambda *_, **kwargs: {})
    save()
    return bundle


@pytest.mark.parametrize('capability', ['simulation', 'formal', 'carryover'])
def test_v2_battle_capabilities_are_granted_independently(v2_bundle, plan, capability, monkeypatch):
    root, profile, entry, _, save = v2_bundle
    monkeypatch.setattr('pcr_maa.clan_battle_daily_native.require_daily_execution', lambda *_, **kwargs: {})
    entry['capabilities'] = ['formal', 'carryover'] if capability == 'carryover' else [capability]
    save()
    assert module.load_calibration(plan, root=root, capability=capability)['version'] == 2
    missing = 'formal' if capability == 'simulation' else 'simulation'
    with pytest.raises(RuntimeError, match='capability_missing:' + missing):
        module.load_calibration(plan, root=root, capability=missing)


@pytest.mark.parametrize('capabilities', [['carryover'], ['simulation', 'simulation'], ['unknown'],
    [True], ['simulation', {}], 'simulation', None])
def test_v2_capabilities_must_be_known_unique_and_complete(v2_bundle, plan, capabilities):
    root, _, entry, _, save = v2_bundle
    entry['capabilities'] = capabilities
    save()
    with pytest.raises(ValueError, match='capabilities_invalid'):
        module.load_calibration(plan, root=root)


@pytest.mark.parametrize('capability', ['timed', '', None, 'auto'])
def test_additional_timing_capability_cannot_replace_battle_mode(v2_bundle, plan, capability):
    root, *_ = v2_bundle
    with pytest.raises(ValueError, match='capability_invalid'):
        module.load_calibration(plan, root=root, capability=capability)


def test_v2_empty_capabilities_grant_no_execution(v2_bundle, plan):
    root, _, entry, _, save = v2_bundle
    entry['capabilities'] = []
    save()
    with pytest.raises(RuntimeError, match='capability_missing:simulation'):
        module.load_calibration(plan, root=root)


def test_v2_axis_requires_timed_in_addition_to_simulation(v2_bundle, plan):
    root, *_ = v2_bundle
    plan['switches'] = [dict(remaining_seconds=85, auto=False, set_by_unit={})]
    with pytest.raises(RuntimeError, match='capability_missing:timed'):
        module.load_calibration(plan, root=root)


def test_v2_opening_auto_off_without_axis_does_not_claim_timing(v2_bundle, plan):
    root, *_ = v2_bundle
    plan['auto'] = False
    assert 'timing_measurements' not in module.load_calibration(plan, root=root)


@pytest.mark.parametrize('capabilities', [['simulation', 'timed'], ['formal'], ['formal', 'carryover']])
def test_v2_index_cannot_promote_legacy_profile(bundle, plan, capabilities):
    root, _, entry, index, save = bundle
    index['version'] = 2
    entry['capabilities'] = capabilities
    save()
    capability = 'simulation' if 'simulation' in capabilities else 'formal'
    with pytest.raises(ValueError, match='legacy_capability_invalid'):
        module.load_calibration(plan, root=root, capability=capability)


@pytest.fixture
def timed_bundle(v2_bundle):
    root, profile, entry, _, save = v2_bundle
    entry['capabilities'] = ['simulation', 'timed']
    path = root / 'assets/resource/clan-battle-calibrations/timing.json'
    measured = dict(kind='owner_native_clan_timing', calibration_id=profile['calibration_id'],
        battle_hud=profile['battle_hud'], observation_scope='full_read_tick',
        action_scope='toggle_dispatch_and_full_team_readback', battle_speed=1,
        game_second_ms=[1000] * 20, observation_intervals_ms=[10] * 20, action_latencies_ms=[50] * 20)
    def save_measurement():
        path.write_text(json.dumps(measured), encoding='utf-8')
        profile['timing_measurements'] = dict(path=path.relative_to(root).as_posix(),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        save()
    save_measurement()
    return v2_bundle, path, measured, save_measurement


def test_timing_is_loaded_only_from_bound_install_file(timed_bundle, plan):
    (root, profile, *_), path, _, _ = timed_bundle
    plan['switches'] = [dict(remaining_seconds=85, auto=False, set_by_unit={})]
    loaded = module.load_calibration(plan, root=root)
    assert loaded['timing_measurements']['path'] == str(path)
    assert not Path(profile['timing_measurements']['path']).is_absolute()


@pytest.mark.parametrize('field,value', [('battle_speed', 2), ('battle_speed', True),
    ('game_second_ms', [1000] * 19), ('observation_intervals_ms', [10] * 19),
    ('action_latencies_ms', [50] * 19), ('calibration_id', 'another-profile'),
    ('observation_scope', 'saved_frame_only')])
def test_incomplete_or_different_timing_measurements_are_not_qualification(timed_bundle, plan, field, value):
    (root, *_), _, measured, save = timed_bundle
    measured[field] = value
    save()
    with pytest.raises(ValueError, match='timing_not_calibrated'):
        module.load_calibration(plan, root=root)


@pytest.mark.parametrize('path', ['../outside.json', '//server/timing.json',
    'C:/timing.json', 'assets/timing.json:stream'])
def test_timing_binding_cannot_escape_install(timed_bundle, plan, path):
    (root, profile, _, _, save), *_ = timed_bundle
    profile['timing_measurements']['path'] = path
    save()
    with pytest.raises(ValueError, match='path_invalid'):
        module.load_calibration(plan, root=root)


def test_changed_timing_bytes_are_rejected(timed_bundle, plan):
    (root, *_), path, _, _ = timed_bundle
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='file_changed'):
        module.load_calibration(plan, root=root)


def test_duplicate_timing_json_field_is_rejected_even_when_digest_matches(timed_bundle, plan):
    (root, profile, _, _, save), path, _, _ = timed_bundle
    path.write_text('{"battle_speed": 1, "battle_speed": 2}', encoding='utf-8')
    profile['timing_measurements']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    save()
    with pytest.raises(ValueError, match='duplicate_field'):
        module.load_calibration(plan, root=root)


@pytest.fixture
def template_bundle(bundle):
    root, profile, _, _, save = bundle
    sample = root / profile['samples'][0]['path']
    names = ['calibration/synthetic-one.png', 'calibration/synthetic-two.png']
    for name in names:
        target = root / 'assets/resource/image' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(sample.read_bytes())
    spec = dict(recognition='TemplateMatch', roi=[1, 1, 2, 2], template=names,
        sha256=[profile['samples'][0]['sha256']] * 2)
    profile['pages']['target']['labels'][0] = spec
    save()
    return bundle, spec


@pytest.mark.parametrize('array', [False, True])
def test_template_variants_keep_each_digest_bound(template_bundle, plan, array):
    (root, _, _, _, save), spec = template_bundle
    if not array:
        spec.update(template=spec['template'][0], sha256=spec['sha256'][0])
        save()
    loaded = module.load_calibration(plan, root=root)
    assert loaded['pages']['target']['labels'][0] == spec


@pytest.mark.parametrize('names,digests', [([], []), (['a'] * 17, ['0' * 64] * 17),
    (['a', 'b'], ['0' * 64]), (['a'], '0' * 64), ([1], ['0' * 64]),
    (['a'], [None])])
def test_template_variant_shape_is_strict(template_bundle, plan, names, digests):
    (root, _, _, _, save), spec = template_bundle
    spec.update(template=names, sha256=digests)
    save()
    with pytest.raises(ValueError):
        module.load_calibration(plan, root=root)


@pytest.mark.parametrize('failure', ['path', 'digest', 'bytes'])
def test_every_template_variant_is_checked(template_bundle, plan, failure):
    (root, _, _, _, save), spec = template_bundle
    if failure == 'path':
        spec['template'][1] = '../../../../../outside.png'
    elif failure == 'digest':
        spec['sha256'][1] = '0' * 64
    else:
        path = root / 'assets/resource/image' / spec['template'][1]
        path.write_bytes(path.read_bytes() + b'changed')
    save()
    with pytest.raises(ValueError, match='path_invalid' if failure == 'path' else 'file_changed'):
        module.load_calibration(plan, root=root)


@pytest.fixture
def daily_bundle(v2_bundle, profile):
    root, bundled, _, _, save = v2_bundle
    for field in ('daily', 'boss_targets', 'phase_labels'):
        bundled[field] = deepcopy(profile[field])
    for sample in bundled['daily']['samples']:
        sample.update(path=bundled['samples'][0]['path'], sha256=bundled['samples'][0]['sha256'])
    save()
    return v2_bundle


@pytest.fixture
def daily_execution_bundle(daily_bundle, executable, monkeypatch):
    root, bundled, _, _, save = daily_bundle
    bundled['daily'] = deepcopy(executable['daily'])
    # Loader-only fixture supplies the shared gestures now read by daily validation.
    monkeypatch.setattr('pcr_maa.clan_battle_native.calibration_pipeline', lambda *_, **kwargs: {
        'CBAction_' + name: dict(action='Swipe', begin=executable['actions'][name]['begin'],
            end=executable['actions'][name]['end'], duration=400)
        for name in ('characters_top', 'characters_next')})
    opening = bundled['daily']['execution']['opening']
    for section in (bundled['daily'], bundled['daily']['execution'], opening, opening['inheritance']):
        for sample in section['samples']:
            sample.update(path=bundled['samples'][0]['path'], sha256=bundled['samples'][0]['sha256'])
    shipped = json.loads((native.ROOT / module.INDEX_PATH).read_text(encoding='utf-8'))
    october = next(entry for entry in shipped['entries'] if entry['content_version'] == '202610141448')
    official = json.loads((native.ROOT / october['profile_path']).read_text(encoding='utf-8'))['daily']['execution']
    formal_sample = next(s for s in official['samples'] if s['page'] == 'result')
    target = root / formal_sample['path']
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((native.ROOT / formal_sample['path']).read_bytes())
    execution = bundled['daily']['execution']
    next(s for s in execution['samples'] if s['page'] == 'result').update(formal_sample)
    result = execution['pages']['result']
    result['fields']['damage'] = [80, 45, 332, 42]
    result['damage_line'] = dict(sample_sha256=formal_sample['sha256'], verify_roi=[88, 47, 324, 38])
    for key in ('battle_selected', 'radio_on', 'radio_off'):
        marker = opening['inheritance'][key]
        target = root / 'assets/resource/image' / marker['template']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((native.ROOT / 'assets/resource/image' / marker['template']).read_bytes())
    save()
    return daily_bundle


def test_optional_readonly_daily_pages_are_compiled_and_samples_resolved(daily_bundle, plan):
    root, profile, *_ = daily_bundle
    loaded = module.load_calibration(plan, root=root)
    assert all(Path(row['path']).is_absolute() and Path(row['path']).is_relative_to(root)
        for row in loaded['daily']['samples'])
    assert all(not Path(row['path']).is_absolute() for row in profile['daily']['samples'])


@pytest.mark.parametrize('capability', ['formal', 'carryover'])
def test_formal_capability_requires_daily_execution_evidence(daily_bundle, plan, capability):
    root, profile, entry, _, save = daily_bundle
    entry['capabilities'] = ['formal', 'carryover']
    save()
    with pytest.raises(ValueError, match='daily_execution_calibration_missing'):
        module.load_calibration(plan, root=root, capability=capability)
    profile.pop('daily')
    save()
    with pytest.raises(ValueError, match='daily_calibration_invalid'):
        module.load_calibration(plan, root=root, capability=capability)


@pytest.mark.parametrize('capability', ['formal', 'carryover'])
def test_formal_loader_resolves_all_execution_samples(daily_execution_bundle, plan, capability):
    root, profile, entry, _, save = daily_execution_bundle
    entry['capabilities'] = ['formal', 'carryover']
    save()
    loaded = module.load_calibration(plan, root=root, capability=capability)
    for source, section in ((profile['daily'], loaded['daily']),
            (profile['daily']['execution'], loaded['daily']['execution'])):
        assert all(Path(row['path']).is_absolute() and Path(row['path']).is_relative_to(root)
            for row in section['samples'])
        assert all(not Path(row['path']).is_absolute() for row in source['samples'])


@pytest.mark.parametrize('section', ['daily', 'execution'])
@pytest.mark.parametrize('failure', ['path', 'digest', 'sample_shape'])
def test_nested_daily_samples_keep_install_boundaries(daily_execution_bundle, plan, section, failure):
    root, profile, _, _, save = daily_execution_bundle
    samples = profile['daily']['samples'] if section == 'daily' else profile['daily']['execution']['samples']
    if failure == 'path':
        samples[0]['path'] = '//server/private.png'
    elif failure == 'digest':
        samples[0]['sha256'] = '0' * 64
    else:
        samples[0]['verified'] = True
    save()
    with pytest.raises(ValueError, match={'path': 'path_invalid', 'digest': 'file_changed',
            'sample_shape': 'sample_invalid'}[failure]):
        module.load_calibration(plan, root=root)


@pytest.mark.parametrize('failure', ['roi', 'pattern', 'execution'])
def test_daily_configuration_is_checked_even_for_simulation(daily_execution_bundle, plan, failure):
    root, profile, _, _, save = daily_execution_bundle
    if failure == 'roi':
        profile['daily']['pages']['lobby']['fields']['cp'] = [0, 0, 5000, 50]
    elif failure == 'pattern':
        profile['daily']['pages']['lobby']['labels'][0]['expected'] = 'unanchored'
    else:
        profile['daily']['execution'].pop('actions')
    save()
    with pytest.raises(ValueError, match={'roi': 'roi_invalid', 'pattern': 'label_invalid',
            'execution': 'daily_execution_calibration_missing'}[failure]):
        module.load_calibration(plan, root=root)


@pytest.fixture
def page_bundle_v2(bundle, calibration_v2):
    root, profile, entry, index, save = bundle
    sample_path = profile['samples'][0]['path']
    profile.clear()
    profile.update(deepcopy(calibration_v2), evidence_kind='device_saved_frame')
    for sample in profile['samples']:
        sample['path'] = sample_path
    index['version'] = 2
    entry['capabilities'] = ['simulation']
    save()
    return bundle


def test_explicit_own_only_profile_can_omit_support_layout(page_bundle_v2, plan, monkeypatch):
    from pcr_maa import clan_battle_native
    pipeline = clan_battle_native.calibration_pipeline
    # This scope fixture has synthetic result digits; live OCR calibration is checked separately.
    monkeypatch.setattr(clan_battle_native, 'calibration_pipeline', lambda value, *args, **kwargs:
        pipeline(dict(value, evidence_kind='offline_replay'), *args, **kwargs))
    root, profile, _, _, save = page_bundle_v2
    profile['support_modes'] = ['none']
    profile.pop('support')
    for action in ('open_support', 'support_back', 'support_top', 'support_next'):
        profile['actions'].pop(action)
    save()
    assert 'support' not in module.load_calibration(plan, root=root)
    with pytest.raises(RuntimeError, match='support_mode_uncalibrated'):
        module.load_calibration(dict(plan, support_mode='specified'), root=root)


@pytest.mark.parametrize('scope', [None, [], ['none', 'specified'], 'none', [False]])
def test_omitting_support_requires_exact_own_only_scope(page_bundle_v2, plan, scope):
    root, profile, _, _, save = page_bundle_v2
    profile.pop('support')
    if scope is not None:
        profile['support_modes'] = scope
    save()
    with pytest.raises(ValueError, match='not_reviewed_device_profile'):
        module.load_calibration(plan, root=root)


@pytest.mark.parametrize('scope', [[], ['none', 'specified'], 'none', [False]])
def test_unsupported_scope_is_rejected_with_support_layout_present(page_bundle_v2, plan, scope):
    root, profile, _, _, save = page_bundle_v2
    profile['support_modes'] = scope
    save()
    with pytest.raises(ValueError, match='support_scope_invalid'):
        module.load_calibration(plan, root=root)


@pytest.mark.parametrize('name,count', [('start_confirmation', 3), ('carry_start_confirmation', 3), ('result_reward', 2)])
@pytest.mark.parametrize('case', ['resolved', 'tampered', 'not_dict'])
def test_optional_execution_dialog_samples_resolve_and_keep_hash_binding(daily_execution_bundle, plan, name, count, case):
    root, profile, entry, _, save = daily_execution_bundle
    sample = dict(profile['samples'][0], page=name)
    dialog = dict(labels=[dict(roi=[10, 10, 100, 30], expected='^离线对话框$') for _ in range(count)],
        target=[10, 10, 20, 20], samples=[sample])
    profile['daily']['execution'][name] = dialog
    entry['capabilities'] = ['formal']
    if case == 'tampered': sample['sha256'] = '0' * 64
    elif case == 'not_dict': profile['daily']['execution'][name] = []
    save()
    if case != 'resolved':
        with pytest.raises(ValueError, match='file_changed' if case == 'tampered' else 'dialog_calibration_invalid'):
            module.load_calibration(plan, root=root, capability='formal')
        return
    loaded = module.load_calibration(plan, root=root, capability='formal')
    path = Path(loaded['daily']['execution'][name]['samples'][0]['path'])
    assert path.is_absolute() and path.is_relative_to(root)
    assert path == root / sample['path']
    assert not Path(sample['path']).is_absolute()
