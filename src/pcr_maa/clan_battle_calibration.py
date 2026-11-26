"""Load reviewed, bundled calibration for the requested battle capability."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

INDEX_PATH = 'assets/resource/clan-battle-calibrations/index.json'
# Updated with reviewed bundle contents, never with a player's local configuration.
INDEX_SHA256 = 'b006c5b9810d9ff9a0ebe378e7107611f632d790e9d7f2e1a014b3a4bd48a214'
HEX = re.compile(r'[0-9a-f]{64}')
ENTRY_KEYS = {'calibration_id', 'server', 'content_version', 'frame_size', 'profile_path', 'profile_sha256'}
CAPABILITIES = {'simulation', 'timed', 'formal', 'carryover'}
PROFILE_KEYS = {'version', 'server', 'frame_size', 'calibration_id', 'evidence_kind',
    'battle_hud', 'pages', 'actions', 'formation', 'support', 'samples'}


def _path(root, relative):
    if (type(relative) is not str or not relative or '\\' in relative or ':' in relative
            or PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts):
        raise ValueError('clan_battle_calibration_path_invalid')
    target = root / relative
    resolved = target.resolve()
    if not resolved.is_relative_to(root) or resolved != target or target.is_symlink():
        raise ValueError('clan_battle_calibration_path_invalid')
    return target


def _read(root, relative, digest, *, limit):
    target = _path(root, relative)
    if type(digest) is not str or HEX.fullmatch(digest) is None:
        raise ValueError('clan_battle_calibration_digest_invalid')
    if not target.is_file() or not 0 < target.stat().st_size <= limit:
        raise ValueError('clan_battle_calibration_file_missing')
    data = target.read_bytes()
    if len(data) > limit or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('clan_battle_calibration_file_changed')
    return target, data


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('clan_battle_calibration_duplicate_field')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('clan_battle_calibration_invalid_json')
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('clan_battle_calibration_invalid_json') from exc


def load_calibration(plan, *, root=None, capability='simulation'):
    """The optional root is a Python test dependency, never a task/config field."""
    from . import native
    if capability not in ('simulation', 'formal', 'carryover'):
        raise ValueError('clan_battle_calibration_capability_invalid')
    root = Path(native.ROOT if root is None else root).resolve()
    if not _path(root, INDEX_PATH).is_file():
        raise RuntimeError('clan_battle_calibration_missing')
    _, data = _read(root, INDEX_PATH, INDEX_SHA256, limit=1024 * 1024)
    index = _json(data)
    if (type(index) is not dict or set(index) != {'version', 'entries'}
            or type(index['version']) is not int or index['version'] not in (1, 2)
            or type(index['entries']) is not list or len(index['entries']) > 100):
        raise ValueError('clan_battle_calibration_index_invalid')
    matches, identities = [], set()
    entry_keys = ENTRY_KEYS | ({'capabilities'} if index['version'] == 2 else set())
    for entry in index['entries']:
        if (type(entry) is not dict or set(entry) != entry_keys
                or type(entry['calibration_id']) is not str or not entry['calibration_id']
                or entry['server'] != 'cn-bilibili'
                or type(entry['content_version']) is not str
                or re.fullmatch('[0-9]{12}', entry['content_version']) is None
                or entry['frame_size'] != [1280, 720]
                or any(type(n) is not int for n in entry['frame_size'])):
            raise ValueError('clan_battle_calibration_index_invalid')
        if index['version'] == 2:
            capabilities = entry['capabilities']
            if (type(capabilities) is not list
                    or any(type(value) is not str or value not in CAPABILITIES for value in capabilities)
                    or len(set(capabilities)) != len(capabilities)
                    or 'carryover' in capabilities and 'formal' not in capabilities):
                raise ValueError('clan_battle_calibration_capabilities_invalid')
        _path(root, entry['profile_path'])
        if type(entry['profile_sha256']) is not str or HEX.fullmatch(entry['profile_sha256']) is None:
            raise ValueError('clan_battle_calibration_digest_invalid')
        identity = entry['server'], entry['content_version'], tuple(entry['frame_size'])
        if identity in identities:
            raise ValueError('clan_battle_calibration_index_ambiguous')
        identities.add(identity)
        if entry['server'] == plan.get('server') and entry['content_version'] == plan.get('content_version'):
            matches.append(entry)
    if not matches:
        raise RuntimeError('clan_battle_calibration_missing')
    entry = matches[0]
    capabilities = entry.get('capabilities', ['simulation'])
    if capability not in capabilities:
        raise RuntimeError('clan_battle_calibration_capability_missing:' + capability)
    _, data = _read(root, entry['profile_path'], entry['profile_sha256'], limit=4 * 1024 * 1024)
    calibration = _json(data)
    profile_options = {'timing_measurements'}
    profile_keys = PROFILE_KEYS
    if type(calibration) is dict and calibration.get('version') == 2:
        profile_options |= {'boss_targets', 'phase_labels', 'daily', 'support_modes', 'auto_battle', 'simulation_targets'}
        if calibration.get('support_modes') == ['none']:
            profile_keys = PROFILE_KEYS - {'support'}
            profile_options.add('support')
    if (type(calibration) is not dict or set(calibration) - profile_options != profile_keys
            or calibration.get('evidence_kind') != 'device_saved_frame'
            or type(calibration.get('version')) is not int or calibration['version'] not in (1, 2)
            or calibration.get('calibration_id') != entry['calibration_id']
            or calibration.get('server') != entry['server']
            or calibration.get('frame_size') != entry['frame_size']):
        raise ValueError('clan_battle_calibration_not_reviewed_device_profile')
    if index['version'] == 1 or calibration['version'] == 1:
        if set(capabilities) - {'simulation'}:
            raise ValueError('clan_battle_calibration_legacy_capability_invalid')
        if plan.get('auto') is not True or plan.get('switches') != []:
            raise RuntimeError('clan_battle_calibration_auto_only')
    elif plan.get('switches') and 'timed' not in capabilities:
        raise RuntimeError('clan_battle_calibration_capability_missing:timed')
    calibration = deepcopy(calibration)
    sample_sections = [calibration]
    if 'auto_battle' in calibration:
        if type(calibration['auto_battle']) is not dict:
            raise ValueError('clan_battle_auto_calibration_invalid')
        sample_sections.append(calibration['auto_battle'])
        if 'inheritance' in calibration['auto_battle']:
            if type(calibration['auto_battle']['inheritance']) is not dict:
                raise ValueError('clan_battle_inheritance_calibration_invalid')
            sample_sections.append(calibration['auto_battle']['inheritance'])
    if 'daily' in calibration:
        daily = calibration['daily']
        if type(daily) is not dict:
            raise ValueError('clan_battle_daily_calibration_invalid')
        sample_sections.append(daily)
        if 'execution' in daily:
            if type(daily['execution']) is not dict:
                raise ValueError('clan_battle_daily_execution_calibration_missing')
            sample_sections.append(daily['execution'])
            for name in ('start_confirmation', 'carry_start_confirmation', 'result_reward'):
                if name in daily['execution']:
                    if type(daily['execution'][name]) is not dict:
                        raise ValueError('clan_battle_daily_dialog_calibration_invalid:' + name)
                    sample_sections.append(daily['execution'][name])
            if 'opening' in daily['execution']:
                opening = daily['execution']['opening']
                if type(opening) is not dict or type(opening.get('inheritance')) is not dict:
                    raise ValueError('clan_battle_daily_opening_calibration_missing')
                sample_sections.extend((opening, opening['inheritance']))
    for section in sample_sections:
        samples = section.get('samples')
        if type(samples) is not list or not samples:
            raise ValueError('clan_battle_calibration_samples_missing')
        for sample in samples:
            if type(sample) is not dict or set(sample) != {'page', 'path', 'sha256'}:
                raise ValueError('clan_battle_calibration_sample_invalid')
            path, _ = _read(root, sample['path'], sample['sha256'], limit=32 * 1024 * 1024)
            sample['path'] = str(path)
    def templates(value):
        if type(value) is dict:
            if value.get('recognition') == 'TemplateMatch':
                name, digest = value.get('template'), value.get('sha256')
                names, digests = (name, digest) if type(name) is list else ([name], [digest])
                if (type(names) is not list or not 1 <= len(names) <= 16
                        or type(digests) is not list or len(names) != len(digests)):
                    raise ValueError('clan_battle_calibration_template_invalid')
                for filename, expected_digest in zip(names, digests):
                    if type(filename) is not str:
                        raise ValueError('clan_battle_calibration_template_invalid')
                    _path(root / 'assets/resource/image', filename)
                    _read(root, 'assets/resource/image/' + filename, expected_digest, limit=4 * 1024 * 1024)
            for item in value.values():
                templates(item)
        elif type(value) is list:
            for item in value:
                templates(item)
    for name, section in calibration.items():
        # The simulation target list contains data only. Its own schema handles invalid rows.
        if name != 'simulation_targets':
            templates(section)
    if 'timed' in capabilities:
        binding = calibration.get('timing_measurements')
        if type(binding) is not dict or set(binding) != {'path', 'sha256'}:
            raise ValueError('clan_battle_calibration_timing_missing')
        path, data = _read(root, binding['path'], binding['sha256'], limit=1024 * 1024)
        _json(data)
        binding['path'] = str(path)
    else:
        calibration.pop('timing_measurements', None)
    from .clan_battle_native import calibration_pipeline, timing_capability
    calibration_pipeline(calibration, plan, capability=capability)
    if 'daily' in calibration or capability in ('formal', 'carryover') or 'carryover' in capabilities:
        from .clan_battle_daily_native import daily_pipeline, require_daily_execution
        if capability in ('formal', 'carryover') or 'carryover' in capabilities or calibration['daily'].get('version') == 2:
            scope = 'carryover' if 'carryover' in capabilities else capability
            require_daily_execution(calibration, plan, require_scope=capability in ('formal', 'carryover'), capability=scope)
        else:
            daily_pipeline(calibration, plan)
    if 'timed' in capabilities and timing_capability(calibration).get('validated') is not True:
        raise ValueError('clan_battle_timing_not_calibrated')
    return calibration
