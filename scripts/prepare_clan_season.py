"""Prepare a CN clan battle season from the restored master and the previous installed layout.

This offline command writes a new review directory. Its index entry grants no capabilities.
Use replay_clan_season.py to check the candidate against current game frames.
"""
import argparse
from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

if __package__:
    from .inspect_clan_targets import cached_master_identity, database_identity, inspect
    from .replay_clan_season import load_cases, summarize
else:
    from inspect_clan_targets import cached_master_identity, database_identity, inspect
    from replay_clan_season import load_cases, summarize


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pcr_maa.clan_battle_calibration_data import INDEX_PATH, _json, _read, legacy_damage_rois, parse_index

CALIBRATIONS = str(Path(INDEX_PATH).parent.as_posix())
PHASES = {1: 'A', 2: 'B', 3: 'C', 4: 'D', 5: 'E'}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def season_maps(database, day):
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        first = inspect(db, day, 1, include_levels=True)
        event = first['period']
        maps = []
        next_lap = 1
        for segment in first['lap_ranges']:
            start, end = segment['first'], segment['last']
            if next_lap != start or (end is not None and end < start):
                raise ValueError('Clan battle lap ranges overlap or have a gap')
            item = first if start == 1 else inspect(db, day, start, include_levels=True)
            bosses = item['bosses']
            if len({boss['name'] for boss in bosses}) != 5:
                raise ValueError('The layout requires five distinct boss names')
            if any(type(boss['level']) is not int or not 1 <= boss['level'] <= 9999 for boss in bosses):
                raise ValueError('Invalid boss nameplate level')
            maps.append(dict(phase=PHASES.get(item['phase']), phase_number=item['phase'], first=start, last=end, bosses=bosses))
            next_lap = None if end is None else end + 1
    return dict(period=datetime.strptime(event['start_time'].split()[0], '%Y/%m/%d').strftime('%Y-%m'),
                event=event, maps=maps)


def previous_layout(root, current_version, previous_version):
    index_data = (root / INDEX_PATH).read_bytes()
    index = parse_index(root, index_data)
    if index['version'] != 2:
        raise ValueError('The previous calibration index must be version 2')
    options = [entry for entry in index['entries'] if entry['server'] == 'cn-bilibili'
               and entry['frame_size'] == [1280, 720] and entry['content_version'] <= current_version]
    existing = next((entry for entry in options if entry['content_version'] == current_version), None)
    options = [entry for entry in options if entry['capabilities']]
    if previous_version is None and options:
        previous_version = max(entry['content_version'] for entry in options)
    matches = [entry for entry in options if entry['content_version'] == previous_version]
    if len(matches) != 1:
        raise ValueError('Expected one reviewed CN calibration in the installed index')
    entry = matches[0]
    _, data = _read(root, entry['profile_path'], entry['profile_sha256'], limit=4 * 1024 * 1024)
    profile = _json(data)
    if (profile['version'] != 2 or profile['server'] != entry['server']
            or profile['frame_size'] != entry['frame_size'] or profile['calibration_id'] != entry['calibration_id']):
        raise ValueError('Previous layout does not match the selected index entry')
    return entry, profile, index, existing, sha(index_data)


def boss_names(profile):
    if set(profile['boss_targets']) != set('12345'):
        raise ValueError('Expected five boss slots in the calibration')
    return [profile['boss_targets'][str(slot)]['name'] for slot in range(1, 6)]


def name_pattern(names):
    return '^(?:' + '|'.join(map(re.escape, names)) + ')$'


def layout_phases(profile, season, requested, reviewed):
    """A profile has one set of five names and at most one five-level row per phase."""
    known = set(profile['phase_labels'].values()) & set(profile['actions']['select_difficulty']['choices'])
    levels_used = 'nameplate_levels' in profile['pages']['boss_detail']
    maps = season['maps']
    names = tuple(boss_names(profile)) if reviewed else None
    phase_data, unavailable = {}, []
    for phase in dict.fromkeys(row['phase_number'] for row in maps):
        rows = [row for row in maps if row['phase_number'] == phase]
        label = rows[0]['phase']
        name_sets = {tuple(b['name'] for b in row['bosses']) for row in rows}
        level_sets = {tuple(b['level'] for b in row['bosses']) for row in rows}
        if label not in known:
            reason = 'no supported UI layout for this master phase'
        elif len(name_sets) != 1 or levels_used and len(level_sets) != 1:
            reason = 'boss names or required nameplate levels change between laps of this phase'
        else:
            phase_data[label] = name_sets.pop()
            continue
        unavailable.append(dict(phase=label, phase_number=phase, reason=reason))
    if names is None:
        first = next((item[0] for item in requested if item[0] in phase_data), next(iter(phase_data), None))
        names = phase_data.get(first, tuple(b['name'] for b in maps[0]['bosses']))
    allowed = []
    for phase, row_names in phase_data.items():
        if row_names == names:
            allowed.append(phase)
        else:
            unavailable.append(dict(phase=phase, reason='boss names differ from those in the selected layout'))
    if reviewed and (not allowed or any(phase not in allowed for phase in known)):
        raise ValueError('Reviewed layout identity or boss slots do not match the current master phases')
    return list(names), allowed, unavailable


def update_identities(profile, season, target_names, stages):
    before = boss_names(profile)
    for slot, name in enumerate(target_names, 1):
        profile['boss_targets'][str(slot)]['name'] = name
    for page in ('boss_detail', 'target'):
        labels = [label for label in profile['pages'][page]['labels']
                  if label.get('expected') == name_pattern(before)]
        if len(labels) != 1:
            raise ValueError(f'Cannot locate unique boss name guard on {page}; review this layout')
        labels[0]['expected'] = name_pattern(target_names)
    profile['phase_labels'] = {label: phase for label, phase in profile['phase_labels'].items() if phase in stages}
    choices = profile['actions']['select_difficulty']['choices']
    profile['actions']['select_difficulty']['choices'] = {phase: spec for phase, spec in choices.items() if phase in stages}
    plate = profile['pages']['boss_detail'].get('nameplate_levels')
    if plate is not None:
        levels = {}
        for row in season['maps']:
            phase = row['phase']
            if phase not in profile['phase_labels'].values():
                continue
            numbers = [b['level'] for b in row['bosses']]
            if phase in levels and levels[phase] != numbers:
                raise ValueError(f'One {phase} nameplate layout cannot represent levels that change between laps')
            levels[phase] = numbers
        profile['pages']['boss_detail']['nameplate_levels'] = levels


def compare_layout(before, after):
    """List differences at a stable profile path, treating lists of guards/templates as single values."""
    def visit(old, new, path):
        if old == new:
            return
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(old.keys() | new.keys()):
                child = f'{path}.{key}' if path else key
                if key not in old or key not in new:
                    yield child
                else:
                    yield from visit(old[key], new[key], child)
        else:
            yield path
    return list(visit(before, after, ''))


def targets_for(profile, season, targets, *, allow_speeds=True):
    choices = profile['actions']['select_difficulty']['choices']
    phases = set(profile['phase_labels'].values()) & set(choices)
    seen = set()
    if not isinstance(targets, list) or len(targets) > 100:
        raise ValueError('Expected a target list of at most 100 entries')
    for row in targets:
        optional = {'speeds'} if allow_speeds else set()
        if (not isinstance(row, dict) or set(row) - optional != {'period', 'phase', 'boss'} or row['period'] != season['period']
                or row['phase'] not in phases or type(row['boss']) is not int or not 1 <= row['boss'] <= 5):
            raise ValueError(f'Target does not match the current season or usable layout: {row}')
        if 'speeds' in row:
            speeds = row['speeds']
            if (type(speeds) is not list or not speeds or any(type(n) is not int or n not in (1, 2, 4) for n in speeds)
                    or len(set(speeds)) != len(speeds) or not set(speeds) <= set(profile['auto_battle']['speeds'])):
                raise ValueError(f'Invalid simulation speeds for {row["phase"]}{row["boss"]}')
        key = (row['phase'], row['boss'])
        if key in seen:
            raise ValueError(f'Duplicate simulation target: {key}')
        seen.add(key)
    return targets


def inherit_damage_layout(result, samples, excluded=()):
    if 'damage_line' in result:
        return
    reference = next((s for s in samples if s['page'] == 'result' and s['sha256'] not in excluded), None)
    if reference is None:
        raise ValueError('Missing original result sample for the inherited damage layout')
    primary, verification = legacy_damage_rois(result['fields']['damage'])
    result['fields']['damage'] = primary
    result['damage_line'] = dict(sample_sha256=reference['sha256'], verify_roi=verification)


def prepare(database, manifest, day, root, previous_version, layout_path, requested, *, master_cache=None):
    if master_cache is None:
        identity = database_identity(database, manifest)
    else:
        database, identity = cached_master_identity(master_cache)
    version = str(identity['content_version'])
    if not re.fullmatch(r'[0-9]{12}', version):
        raise ValueError('Manifest has no valid CN content version')
    season = season_maps(database, day)
    previous, baseline, index, existing, index_sha = previous_layout(root, version, previous_version)
    calibration_id = f'cn-bilibili-{version}-clan-simulation'
    profile = deepcopy(baseline)
    original_layout_bytes = None
    source_profile = None
    if layout_path:
        original_layout_bytes = layout_path.read_bytes()
        source_profile = _json(original_layout_bytes)
        profile = deepcopy(source_profile)
        if (profile['version'] != 2 or profile['server'] != 'cn-bilibili' or profile['frame_size'] != [1280, 720]
                or profile['calibration_id'] != calibration_id):
            raise ValueError('Reviewed layout identity does not match the current master')
    requested = requested or []
    if any(re.fullmatch(r'[ABCDE][1-5]', item) is None for item in requested) or len(set(requested)) != len(requested):
        raise ValueError('Expected distinct targets in A1–E5')
    names, phases, exclusions = layout_phases(profile, season, requested, source_profile is not None)
    missing = [item for item in requested if item[0] not in phases]
    profile['calibration_id'] = calibration_id
    update_identities(profile, season, names, phases)
    if source_profile is not None and profile != source_profile:
        raise ValueError('Reviewed phase choices, boss labels or nameplate levels differ from the current master')
    if source_profile is None:
        # A new season starts with no formal qualification, even on the same master version.
        profile.pop('formal_targets', None)
        profile.pop('carryover_targets', None)
        if (previous['content_version'] == '202610141448'
                and previous['calibration_id'] == 'cn-bilibili-202610141448-clan-simulation'):
            inherit_damage_layout(profile['pages']['result'], profile['samples'])
            formal = profile.get('daily', {}).get('execution', {})
            if 'result' in formal.get('pages', {}):
                simulation_samples = {s['sha256'] for s in profile['samples'] if s['page'] == 'result'}
                inherit_damage_layout(formal['pages']['result'], formal['samples'], simulation_samples)
    else:
        for name in ('formal_targets', 'carryover_targets'):
            if name in profile:
                if not profile[name]:
                    raise ValueError(f'Empty {name} in reviewed layout')
                targets_for(profile, season, profile[name], allow_speeds=False)
    available = targets_for(profile, season, profile.get('simulation_targets', [])) if source_profile else []
    if missing:
        targets = []
    elif requested:
        targets = []
        for item in requested:
            target = dict(period=season['period'], phase=item[0], boss=int(item[1]))
            if source_profile and 'simulation_targets' in source_profile:
                target = next((row for row in available if row['phase'] == item[0] and row['boss'] == int(item[1])), None)
                if target is None:
                    raise ValueError(f'{item} is absent from the supplied reviewed layout')
            targets.append(target)
    else:
        targets = available
    profile['simulation_targets'] = targets_for(profile, season, targets)
    profile_data = (original_layout_bytes if original_layout_bytes is not None
                    and profile == source_profile else json_bytes(profile))
    entry = dict(calibration_id=calibration_id, server='cn-bilibili', content_version=version, frame_size=[1280, 720],
                 profile_path=(existing['profile_path'] if existing else f'{CALIBRATIONS}/cn-bilibili-{version}/profile.json'),
                 profile_sha256=sha(profile_data), capabilities=[])
    if existing is None:
        index['entries'].append(entry)
    else:
        index['entries'][index['entries'].index(existing)] = entry
    candidate_index = json_bytes(index)
    parse_index(root, candidate_index)
    report = dict(version=1, database=identity, season=season,
                  previous={k: previous[k] for k in ('content_version', 'profile_path', 'profile_sha256')},
                  source_index_sha256=index_sha, candidate_index_sha256=sha(candidate_index),
                  index_update=dict(action='replace' if existing else 'append',
                                    existing_profile_sha256=existing['profile_sha256'] if existing else None),
                  candidate=dict(source='reviewed_layout' if layout_path else 'inherited',
                                 profile_sha256=entry['profile_sha256'], registered_capabilities=[],
                                 simulation_targets=targets, unavailable_targets=missing, input_layout_sha256=(sha(original_layout_bytes)
                                 if original_layout_bytes is not None else previous['profile_sha256'])),
                  layout_unavailable=exclusions,
                  boss_changes=[dict(slot=i, previous=old, current=new) for i, (old, new)
                                in enumerate(zip(boss_names(baseline), names), 1) if old != new],
                  changed_fields=compare_layout(baseline, profile),
                  unchanged_page_definitions=[p for p, spec in profile['pages'].items() if spec == baseline['pages'].get(p)],
                  pending_checks=['Replay current labelled frames against inherited and changed layouts',
                                  'Bind current result, formation and opening samples before the normal GUI trial',
                                  'Verify each requested target and capability through its GUI entry',
                                  'Review distributable assets before adding the entry to the package index'])
    return {'profile.json': profile_data, 'index-entry.json': json_bytes(entry), 'index.json': candidate_index,
            'turnover.json': json_bytes(report)}


def save_output(output, files):
    if output.exists():
        if output.is_dir() and all((output / name).is_file() and (output / name).read_bytes() == data
                                   for name, data in files.items()):
            return
        raise ValueError('Use a new candidate output directory; its contents already differ')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as stage:
        ready = Path(stage) / 'candidate'
        ready.mkdir()
        for name, data in files.items():
            (ready / name).write_bytes(data)
        ready.rename(output)


def attach_replay(files, frames, replay):
    """Link the current saved-frame run without changing the profile tested by the reader."""
    report = _json(files['turnover.json'])
    frame_bytes, replay_bytes = frames.read_bytes(), replay.read_bytes()
    manifest, observed = load_cases(frames), _json(replay_bytes)
    if manifest != _json(frame_bytes):
        raise ValueError('Frame manifest changed during verification')
    if (observed.get('kind') != 'clan_season_layout_replay' or type(observed.get('device_inputs')) is not int
            or observed['device_inputs'] != 0 or observed.get('capabilities') != []
            or observed.get('input_profile_sha256') != sha(files['profile.json'])
            or observed.get('manifest_sha256') != sha(frame_bytes)):
        raise ValueError('Replay does not match this candidate and frame manifest')
    for key, value in [('period', report['season']['period']), ('content_version', report['database']['content_version'])]:
        if manifest.get(key) != value or observed.get(key) != value:
            raise ValueError(f'Replay {key} mismatch')
    checked = {}
    for case, row in zip(manifest['cases'], observed['cases'], strict=True):
        path = (frames.parent / case['file']).resolve(strict=True)
        if path not in checked:
            with path.open('rb') as source:
                checked[path] = hashlib.file_digest(source, 'sha256').hexdigest()
        declared = {key: value for key, value in row.items() if key not in {'sha256', 'matched', 'failed', 'error', 'fields'}}
        expected = {**case, 'file': path.name}
        if (row.get('sha256') != checked[path] or json.dumps(declared, sort_keys=True) != json.dumps(expected, sort_keys=True)
                or 'matched' not in row or row['matched'] is not None and type(row['matched']) is not bool):
            raise ValueError('Replay case or frame changed')
    covered = set(_json(files['profile.json'])['pages']) | {r['page'] for r in observed['cases']}
    pages = summarize(observed['cases'], sorted(covered))
    action_cases = [dict(page=f"{r['page']}/{r['action']}", expect=r['expect'], matched=r['matched'])
                    for r in observed['cases'] if 'action' in r]
    actions = summarize(action_cases, sorted({r['page'] for r in action_cases}))
    if not observed['cases'] or observed.get('pages') != pages or observed.get('actions') != actions:
        raise ValueError('Replay summary disagrees with its cases')
    report['layout_replay'] = dict(sha256=sha(replay_bytes), manifest_sha256=sha(frame_bytes), pages=pages,
                                   actions=actions, cases=len(observed['cases']), unique_frames=len(checked))
    if 'pending_checks' in report:
        report['pending_checks'][0] = 'Review changed pages, missing cases and actions without both frame outcomes'
    files['turnover.json'] = json_bytes(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, nargs='?', help='upstream CN SQLite master, paired with --manifest')
    parser.add_argument('--manifest', type=Path, help='upstream SHA-256, size and content version')
    parser.add_argument('--master-cache', type=Path, help='installation root with a verified data/labyrinth/master-cn cache')
    parser.add_argument('--date', type=date.fromisoformat,
                        default=datetime.now(timezone(timedelta(hours=8))).date(), help='CN date (default: today)')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1], help='baseline installation')
    parser.add_argument('--previous-version', help='defaults to the latest installed calibration up to the master version')
    parser.add_argument('--layout-profile', type=Path, help='optional reviewed current layout overriding inherited geometry')
    parser.add_argument('--target', action='append', help='candidate simulation target, e.g. D1 (repeatable)')
    parser.add_argument('--frames', type=Path, help='labeled current frame manifest paired with --replay-report')
    parser.add_argument('--replay-report', type=Path, help='attach real replay results for this exact profile')
    parser.add_argument('--output', type=Path, required=True, help='new review directory')
    args = parser.parse_args(argv)
    if args.master_cache is None and not (args.database and args.manifest):
        parser.error('Pass --master-cache <installation> or a database and --manifest')
    if args.master_cache is not None and (args.database or args.manifest):
        parser.error('--master-cache cannot be combined with a database or --manifest')
    try:
        files = prepare(args.database, args.manifest, args.date, args.root.resolve(), args.previous_version,
                        args.layout_profile, args.target, master_cache=args.master_cache)
        if bool(args.frames) != bool(args.replay_report):
            raise ValueError('Pass both --frames and --replay-report')
        if args.frames:
            attach_replay(files, args.frames, args.replay_report)
        save_output(args.output, files)
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'{exc}\n')
    print(args.output / 'turnover.json')
    if json.loads(files['turnover.json'])['candidate']['unavailable_targets']:
        parser.exit(2, 'The layout does not cover all requested targets. See turnover.json.\n')


if __name__ == '__main__':
    main()
