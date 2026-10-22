"""Load only reviewed, bundled calibration for one AUTO simulation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

INDEX_PATH = 'assets/resource/clan-battle-calibrations/index.json'
# Updated with reviewed bundle contents, never with a player's local configuration.
INDEX_SHA256 = '7c1051f6e2bc3943b7c1dbe4da229694455681b8875a346c99279e555edb733f'
HEX = re.compile(r'[0-9a-f]{64}')
ENTRY_KEYS = {'calibration_id', 'server', 'content_version', 'frame_size', 'profile_path', 'profile_sha256'}
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


def load_calibration(plan, *, root=None):
    """The optional root is a Python test dependency, never a task/config field."""
    from . import native
    if plan.get('auto') is not True or plan.get('switches') != []:
        raise RuntimeError('clan_battle_calibration_auto_only')
    root = Path(native.ROOT if root is None else root).resolve()
    if not _path(root, INDEX_PATH).is_file():
        raise RuntimeError('clan_battle_calibration_missing')
    _, data = _read(root, INDEX_PATH, INDEX_SHA256, limit=1024 * 1024)
    index = _json(data)
    if (type(index) is not dict or set(index) != {'version', 'entries'}
            or type(index['version']) is not int or index['version'] != 1
            or type(index['entries']) is not list or len(index['entries']) > 100):
        raise ValueError('clan_battle_calibration_index_invalid')
    matches, identities = [], set()
    for entry in index['entries']:
        if (type(entry) is not dict or set(entry) != ENTRY_KEYS
                or type(entry['calibration_id']) is not str or not entry['calibration_id']
                or entry['server'] != 'cn-bilibili'
                or type(entry['content_version']) is not str
                or re.fullmatch('[0-9]{12}', entry['content_version']) is None
                or entry['frame_size'] != [1280, 720]
                or any(type(n) is not int for n in entry['frame_size'])):
            raise ValueError('clan_battle_calibration_index_invalid')
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
    _, data = _read(root, entry['profile_path'], entry['profile_sha256'], limit=4 * 1024 * 1024)
    calibration = _json(data)
    if (type(calibration) is not dict or set(calibration) - {'timing_measurements'} != PROFILE_KEYS
            or calibration.get('evidence_kind') != 'device_saved_frame'
            or type(calibration.get('version')) is not int or calibration['version'] != 1
            or calibration.get('calibration_id') != entry['calibration_id']
            or calibration.get('server') != entry['server']
            or calibration.get('frame_size') != entry['frame_size']):
        raise ValueError('clan_battle_calibration_not_reviewed_device_profile')
    calibration = deepcopy(calibration)
    samples = calibration.get('samples')
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
                name = value.get('template')
                if type(name) is not str:
                    raise ValueError('clan_battle_calibration_template_invalid')
                _path(root / 'assets/resource/image', name)
                _read(root, 'assets/resource/image/' + name, value.get('sha256'), limit=4 * 1024 * 1024)
            for item in value.values():
                templates(item)
        elif type(value) is list:
            for item in value:
                templates(item)
    templates(calibration)
    # AUTO has no timing dependency; a bundled profile cannot grant timed actions.
    calibration.pop('timing_measurements', None)
    from .clan_battle_native import calibration_pipeline
    calibration_pipeline(calibration, plan)
    return calibration
