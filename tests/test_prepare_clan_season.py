"""Exercise the operator's season-turnover CLI without a game or a private master."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'prepare_clan_season.py'
INDEX = Path('assets/resource/clan-battle-calibrations/index.json')
OLD_VERSION = '202610141448'
NEW_VERSION = '202611131613'
NAMES = ['甲+龙', '乙.鸟', '丙(树)', '丁[狼]', '戊射手']


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refresh_manifest(root):
    db = root / 'master #1.db'
    write(root / 'manifest.json', dict(db_version=NEW_VERSION, checksum_sha256=digest(db),
                                      size_bytes=db.stat().st_size))


def change_db(root, sql):
    conn = sqlite3.connect(root / 'master #1.db')
    try:
        with conn:
            conn.executescript(sql)
    finally:
        conn.close()
    refresh_manifest(root)


@pytest.fixture
def season(tmp_path):
    # A private copy allows the tests to exercise stale metadata and edited candidates.
    index = read(ROOT / INDEX)
    entry = next(row for row in index['entries'] if row['content_version'] == OLD_VERSION)
    old = tmp_path / entry['profile_path']
    old.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / entry['profile_path'], old)
    write(tmp_path / INDEX, dict(version=2, entries=[entry]))

    conn = sqlite3.connect(tmp_path / 'master #1.db')
    try:
        with conn:
            conn.executescript("""
                CREATE TABLE clan_battle_period (clan_battle_id, start_time, end_time);
                INSERT INTO clan_battle_period VALUES (9, '2026/11/25 5:00:00', '2026/11/29 23:59:59');
                CREATE TABLE clan_battle_2_map_data (clan_battle_id, phase, difficulty, lap_num_from, lap_num_to,
                    boss_id_1, boss_id_2, boss_id_3, boss_id_4, boss_id_5,
                    wave_group_id_1, wave_group_id_2, wave_group_id_3, wave_group_id_4, wave_group_id_5);
                CREATE TABLE clan_battle_2_boss_data (clan_battle_id, difficulty, order_num, boss_id);
                CREATE TABLE wave_group_data (wave_group_id, enemy_id_1, enemy_id_2, enemy_id_3, enemy_id_4, enemy_id_5);
                CREATE TABLE enemy_parameter (enemy_id, unit_id, name, level);
                CREATE TABLE unit_enemy_data (unit_id, unit_name);
            """)
            for phase, first, last in [(2, 1, 1), (2, 2, 6), (3, 7, 22), (4, 23, -1)]:
                numbers = [phase * 10 + slot for slot in range(1, 6)]
                conn.execute('INSERT INTO clan_battle_2_map_data VALUES (9, ?, ?, ?, ?, ?,?,?,?,?, ?,?,?,?,?)',
                             [phase, phase, first, last, *numbers, *numbers])
            for phase in (2, 3, 4):
                for slot, name in enumerate(NAMES, start=1):
                    enemy = phase * 10 + slot
                    conn.execute('INSERT INTO clan_battle_2_boss_data VALUES (9, ?, ?, ?)', (phase, slot, enemy))
                    conn.execute('INSERT INTO wave_group_data VALUES (?, ?, 0,0,0,0)', (enemy, enemy))
                    conn.execute('INSERT INTO enemy_parameter VALUES (?, ?, ?, ?)',
                                 (enemy, enemy, name, 100 * phase + slot))
                    conn.execute('INSERT INTO unit_enemy_data VALUES (?, ?)', (enemy, name))
    finally:
        conn.close()
    refresh_manifest(tmp_path)
    return tmp_path


def run(root, *args, output='candidate', check=True, day='2026-11-26', previous=OLD_VERSION):
    command = [sys.executable, '-X', 'utf8', '-B', str(SCRIPT), str(root / 'master #1.db'),
               '--manifest', str(root / 'manifest.json'), '--date', day,
               '--root', str(root), '--output', str(root / '.runtime' / output), *map(str, args)]
    if previous is not None:
        command.extend(['--previous-version', previous])
    result = subprocess.run(command, cwd=root, capture_output=True, encoding='utf-8', timeout=30)
    if check:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, result.stdout
    return root / '.runtime' / output


def test_turnover_reads_all_laps_and_reuses_reviewed_geometry(season):
    old_entry = read(season / INDEX)['entries'][0]
    original = read(season / old_entry['profile_path'])
    before = {path: digest(season / path) for path in (INDEX, old_entry['profile_path'], 'master #1.db')}
    out = run(season, '--target', 'C1', '--target', 'D5')
    candidate = read(out / 'profile.json')
    entry = read(out / 'index-entry.json')
    report = read(out / 'turnover.json')
    assert entry['content_version'] == NEW_VERSION and entry['capabilities'] == []
    assert entry['calibration_id'] == candidate['calibration_id']
    assert entry['profile_sha256'] == report['candidate']['profile_sha256'] == digest(out / 'profile.json')
    assert report['candidate']['registered_capabilities'] == []
    assert report['candidate']['source'] == 'inherited'
    assert report['index_update'] == dict(action='append', existing_profile_sha256=None)
    assert read(out / 'index.json') == dict(version=2, entries=[old_entry, entry])
    assert report['source_index_sha256'] == digest(season / INDEX)
    assert report['candidate_index_sha256'] == digest(out / 'index.json')
    assert report['season']['period'] == '2026-11'
    rows = report['season']['maps']
    assert [(r['phase'], r['first'], r['last']) for r in rows] == [
        ('B', 1, 1), ('B', 2, 6), ('C', 7, 22), ('D', 23, None)]
    assert [boss['level'] for boss in rows[-1]['bosses']] == [401, 402, 403, 404, 405]
    assert [boss['slot'] for boss in rows[-1]['bosses']] == list(range(1, 6))
    assert candidate['pages']['adventure'] == original['pages']['adventure']
    assert candidate['formation'] == original['formation']
    result = candidate['pages']['result']
    assert result['fields']['damage'] == [80, 45, 332, 42]
    assert result['damage_line']['verify_roi'] == [88, 47, 324, 38]
    result_frames = [sample['sha256'] for sample in candidate['samples'] if sample['page'] == 'result']
    assert result['damage_line']['sample_sha256'] in result_frames
    formal = candidate['daily']['execution']
    formal_result = formal['pages']['result']
    assert formal_result['fields']['damage'] == [80, 45, 332, 42]
    assert formal_result['damage_line']['verify_roi'] == [88, 47, 324, 38]
    formal_sample = formal_result['damage_line']['sample_sha256']
    assert formal_sample in {s['sha256'] for s in formal['samples'] if s['page'] == 'result'}
    assert formal_sample not in result_frames
    assert candidate['simulation_targets'] == [
        dict(period='2026-11', phase='C', boss=1), dict(period='2026-11', phase='D', boss=5)]
    for slot, name in enumerate(NAMES, start=1):
        assert candidate['boss_targets'][str(slot)] == dict(original['boss_targets'][str(slot)], name=name)
    for page in ('boss_detail', 'target'):
        labels = candidate['pages'][page]['labels']
        patterns = [rule['expected'] for rule in labels if rule.get('recognition') == 'OCR']
        assert any(all(re.fullmatch(pattern, name) for name in NAMES)
                   and not re.fullmatch(pattern, '乙任鸟') for pattern in patterns)
    assert {path: digest(season / path) for path in before} == before


def test_repeat_is_identical_and_new_output_does_not_replace_an_existing_candidate(season):
    out = run(season, '--target', 'C1')
    files = {path.name: path.read_bytes() for path in out.iterdir() if path.is_file()}
    run(season, '--target', 'C1')
    assert {name: (out / name).read_bytes() for name in files} == files
    run(season, '--target', 'D5', check=False)
    assert {name: (out / name).read_bytes() for name in files} == files


@pytest.mark.parametrize('sql', [
    'DELETE FROM clan_battle_period',
    'UPDATE clan_battle_2_map_data SET lap_num_from=6 WHERE phase=3',
    'UPDATE clan_battle_2_map_data SET lap_num_from=8 WHERE phase=3',
    "UPDATE enemy_parameter SET name='甲+龙' WHERE enemy_id IN (22,32,42); "
    "UPDATE unit_enemy_data SET unit_name='甲+龙' WHERE unit_id IN (22,32,42)",
])
def test_wrong_or_unrepresentable_master_stops_without_writing_a_candidate(season, sql):
    change_db(season, sql)
    assert not (run(season, check=False) / 'profile.json').exists()


def test_manifest_and_historical_profile_bytes_must_match(season):
    entry = read(season / INDEX)['entries'][0]
    manifest = read(season / 'manifest.json')
    write(season / 'manifest.json', dict(manifest, checksum_sha256='0' * 64))
    assert not (run(season, check=False) / 'profile.json').exists()
    write(season / 'manifest.json', manifest)
    old = season / entry['profile_path']
    old.write_bytes(old.read_bytes() + b' ')
    assert not (run(season, check=False) / 'profile.json').exists()


def test_old_simulation_target_qualification_does_not_roll_over(season):
    index = read(season / INDEX)
    entry = index['entries'][0]
    path = season / entry['profile_path']
    profile = read(path)
    profile['simulation_targets'] = [dict(period='2026-10', phase='D', boss=1)]
    profile['formal_targets'] = [dict(period='2026-10', phase='D', boss=1)]
    profile['carryover_targets'] = [dict(period='2026-10', phase='D', boss=1)]
    write(path, profile)
    entry['profile_sha256'] = digest(path)
    write(season / INDEX, index)
    unscoped = run(season, output='unscoped')
    assert read(unscoped / 'profile.json')['simulation_targets'] == []
    assert not read(unscoped / 'profile.json').get('formal_targets')
    assert not read(unscoped / 'profile.json').get('carryover_targets')
    assert read(unscoped / 'index-entry.json')['capabilities'] == []
    out = run(season, '--target', 'C1')
    assert read(out / 'profile.json')['simulation_targets'] == [dict(period='2026-11', phase='C', boss=1)]
    assert read(out / 'index-entry.json')['capabilities'] == []


def test_reviewed_current_layout_preserves_fix_and_rejects_stale_owner_or_wrong_name(season):
    old_entry = read(season / INDEX)['entries'][0]
    old_profile = season / old_entry['profile_path']
    assert not (run(season, '--layout-profile', old_profile, check=False) / 'profile.json').exists()
    out = run(season, '--target', 'C1')
    reviewed = read(out / 'profile.json')
    revised = list(reviewed['pages']['target']['fields']['boss'])
    revised[0] += 2
    reviewed['pages']['target']['fields']['boss'] = revised
    layout = season / 'reviewed.json'
    write(layout, reviewed)
    second = run(season, '--layout-profile', layout, '--target', 'C1', output='updated')
    assert read(second / 'profile.json')['pages']['target']['fields']['boss'] == revised
    assert read(second / 'turnover.json')['candidate']['source'] == 'reviewed_layout'
    assert read(second / 'index-entry.json')['capabilities'] == []
    reviewed['boss_targets']['1']['name'] = '错王'
    write(layout, reviewed)
    assert not (run(season, '--layout-profile', layout, output='wrong', check=False) / 'profile.json').exists()


def test_profile_with_levelled_nameplates_must_match_every_registered_stage(season):
    out = run(season, '--target', 'C1')
    current = read(out / 'profile.json')
    stages = {'B': 2, 'C': 3, 'D': 4}
    levels = {key: [100 * phase + slot for slot in range(1, 6)] for key, phase in stages.items()}
    current['pages']['boss_detail']['nameplate_levels'] = levels
    layout = season / 'plates.json'
    write(layout, current)
    checked = run(season, '--layout-profile', layout, output='plates')
    assert (checked / 'profile.json').read_bytes() == layout.read_bytes()
    levels['D'][0] -= 1
    write(layout, current)
    assert not (run(season, '--layout-profile', layout, output='wrong-level', check=False) / 'profile.json').exists()


@pytest.mark.parametrize('targets', [('Z1',), ('C1', 'C1'), ('C0',)])
def test_scope_rejects_unknown_stage_duplicate_slot_and_invalid_slot(season, targets):
    flags = [arg for target in targets for arg in ('--target', target)]
    assert not (run(season, *flags, check=False) / 'index-entry.json').exists()


@pytest.mark.parametrize('previous', [None, NEW_VERSION])
def test_same_master_can_cover_another_battle_period_without_adding_an_index_key(season, previous):
    # The upstream snapshot can already contain next month's schedule before it is released again.
    change_db(season, """
        INSERT INTO clan_battle_period VALUES (8, '2026/10/25 5:00:00', '2026/10/29 23:59:59');
    """)
    index = read(season / INDEX)
    old = index['entries'][0]
    profile = read(season / old['profile_path'])
    profile['calibration_id'] = old['calibration_id'].replace(OLD_VERSION, NEW_VERSION)
    profile['simulation_targets'] = [dict(period='2026-10', phase='C', boss=1)]
    existing = dict(old, content_version=NEW_VERSION, calibration_id=profile['calibration_id'],
                    profile_path=old['profile_path'].replace(OLD_VERSION, NEW_VERSION))
    existing_path = season / existing['profile_path']
    existing_path.parent.mkdir(parents=True)
    write(existing_path, profile)
    existing['profile_sha256'] = digest(existing_path)
    index['entries'].append(existing)
    write(season / INDEX, index)
    out = run(season, '--target', 'D5', previous=previous)
    report = read(out / 'turnover.json')
    assert report['previous']['content_version'] == NEW_VERSION
    assert report['index_update'] == dict(action='replace', existing_profile_sha256=existing['profile_sha256'])
    assert read(out / 'index.json')['entries'] == [old, read(out / 'index-entry.json')]
    assert report['source_index_sha256'] == digest(season / INDEX)
    assert report['candidate_index_sha256'] == digest(out / 'index.json')
    assert read(out / 'profile.json')['simulation_targets'] == [dict(period='2026-11', phase='D', boss=5)]
    assert read(out / 'index-entry.json')['capabilities'] == []
    assert read(season / INDEX) == index and digest(existing_path) == existing['profile_sha256']


def test_narrowing_reviewed_scope_keeps_speed_limits_and_rejects_unreviewed_targets(season):
    original = run(season, '--target', 'C1', '--target', 'D5')
    profile = read(original / 'profile.json')
    rows = profile['simulation_targets']
    rows[0]['speeds'] = [1]
    rows[1]['speeds'] = [2]
    saved = season / 'reviewed-speeds.json'
    write(saved, profile)
    fit = run(season, '--layout-profile', saved, '--target', 'C1', output='fit')
    assert read(fit / 'profile.json')['simulation_targets'] == [rows[0]]
    assert not (run(season, '--layout-profile', saved, '--target', 'D1', output='unverified',
                    check=False) / 'index-entry.json').exists()
    for invalid in ([4], [0], [1, 1], [], [True]):
        rows[0]['speeds'] = invalid
        write(saved, profile)
        assert not (run(season, '--layout-profile', saved, output='invalid-speed', check=False)
                    / 'index-entry.json').exists()


def test_duplicate_json_fields_are_rejected_before_the_reviewed_profile_is_copied(season):
    out = run(season, '--target', 'C1')
    data = (out / 'profile.json').read_bytes()
    assert data.startswith(b'{')
    duplicate = season / 'duplicate-server.json'
    duplicate.write_bytes(b'{"server":"other-server",' + data[1:])
    assert not (run(season, '--layout-profile', duplicate, output='ambiguous',
                    check=False) / 'index-entry.json').exists()


def test_data_only_phase_does_not_block_a_target_with_a_known_layout(season):
    change_db(season, 'UPDATE clan_battle_2_map_data SET phase=1 WHERE lap_num_from=1')
    out = run(season, '--target', 'D2')
    report = read(out / 'turnover.json')
    assert [row['phase'] for row in report['season']['maps']] == ['A', 'B', 'C', 'D']
    assert any(row['phase'] == 'A' and row['reason'] for row in report['layout_unavailable'])
    assert read(out / 'profile.json')['simulation_targets'] == [dict(period='2026-11', phase='D', boss=2)]


def test_only_compatible_stage_is_proposed_when_the_boss_slot_changes(season):
    change_db(season, """
        UPDATE enemy_parameter SET name='异相甲龙' WHERE enemy_id=41;
        UPDATE unit_enemy_data SET unit_name='异相甲龙' WHERE unit_id=41;
    """)
    out = run(season, '--target', 'C1')
    report = read(out / 'turnover.json')
    assert report['season']['maps'][-1]['bosses'][0]['name'] == '异相甲龙'
    assert any(row['phase'] == 'D' and row['reason'] for row in report['layout_unavailable'])
    candidate = read(out / 'profile.json')
    assert candidate['boss_targets']['1']['name'] == NAMES[0]
    assert candidate['simulation_targets'] == [dict(period='2026-11', phase='C', boss=1)]
    both = run(season, '--target', 'C1', '--target', 'D1', check=False, output='incompatible')
    assert len(read(both / 'turnover.json')['season']['maps']) == 4
    assert read(both / 'turnover.json')['candidate']['unavailable_targets']
    assert read(both / 'profile.json')['simulation_targets'] == []
    assert read(both / 'index-entry.json')['capabilities'] == []


@pytest.mark.parametrize('mode_targets', ['formal_targets', 'carryover_targets'])
def test_current_reviewed_mode_scope_survives_without_granting_an_index_capability(season, mode_targets):
    initial = run(season, '--target', 'D2')
    profile = read(initial / 'profile.json')
    profile['formal_targets'] = [dict(period='2026-11', phase='D', boss=2)]
    profile['carryover_targets'] = [dict(period='2026-11', phase='D', boss=3)]
    current = season / 'current-mode-limits.json'
    write(current, profile)
    fit = run(season, '--layout-profile', current, output='current-modes')
    assert (fit / 'profile.json').read_bytes() == current.read_bytes()
    assert read(fit / 'index.json')['entries'][-1]['capabilities'] == []
    for invalid in [dict(period='2026-10', phase='D', boss=2),
                    dict(period='2026-11', phase='D', boss=2, speeds=[1])]:
        profile[mode_targets] = [invalid]
        write(current, profile)
        assert not (run(season, '--layout-profile', current, output='bad-mode',
                        check=False) / 'index.json').exists()
