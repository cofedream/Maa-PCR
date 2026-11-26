from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from scripts.inspect_clan_targets import inspect


@pytest.fixture
def db():
    conn = sqlite3.connect(':memory:')
    conn.executescript("""
        CREATE TABLE clan_battle_period (clan_battle_id, start_time, end_time);
        INSERT INTO clan_battle_period VALUES (9, '2026/11/25 5:00:00', '2026/11/29 23:59:59');
        CREATE TABLE clan_battle_2_map_data (clan_battle_id, phase, difficulty, lap_num_from, lap_num_to,
            boss_id_1, boss_id_2, boss_id_3, boss_id_4, boss_id_5,
            wave_group_id_1, wave_group_id_2, wave_group_id_3, wave_group_id_4, wave_group_id_5);
        CREATE TABLE clan_battle_2_boss_data (clan_battle_id, difficulty, order_num, boss_id);
        CREATE TABLE wave_group_data (wave_group_id, enemy_id_1, enemy_id_2, enemy_id_3, enemy_id_4, enemy_id_5);
        CREATE TABLE enemy_parameter (enemy_id, unit_id, name);
        CREATE TABLE unit_enemy_data (unit_id, unit_name);
    """)
    for phase, first, last in [(1, -2, -2), (3, 7, 22), (4, 23, -1)]:
        conn.execute('INSERT INTO clan_battle_2_map_data VALUES (9, ?, 1, ?, ?, 5,4,3,2,1, 11,12,13,14,15)',
                     (phase, first, last))
    for slot in range(1, 6):
        conn.execute('INSERT INTO clan_battle_2_boss_data VALUES (9, 1, ?, ?)', (slot, 6-slot))
        conn.execute('INSERT INTO wave_group_data VALUES (?, ?, 0,0,0,0)', (10+slot, 20+slot))
        conn.execute('INSERT INTO enemy_parameter VALUES (?, ?, ?)', (20+slot, 30+slot, f'首领{slot}'))
        conn.execute('INSERT INTO unit_enemy_data VALUES (?, ?)', (30+slot, f'首领{slot}'))
    yield conn
    conn.close()


@pytest.mark.parametrize('day,lap,phase', [(25, 22, 3), (29, 23, 4), (26, 200, 4)])
def test_calendar_boundaries_and_lap_specific_targets(db, day, lap, phase):
    result = inspect(db, date(2026, 11, day), lap)
    assert result['phase'] == phase
    assert result['lap_ranges'] == [{'phase': 3, 'first': 7, 'last': 22}, {'phase': 4, 'first': 23, 'last': None}]
    assert result['bosses'][0] == dict(slot=1, boss_id=5, wave_group_id=11, enemy_id=21, unit_id=31, name='首领1')
    assert [boss['name'] for boss in result['bosses']] == [f'首领{i}' for i in range(1, 6)]


@pytest.mark.parametrize('statement', [
    'DELETE FROM clan_battle_2_boss_data WHERE order_num=5',
    'UPDATE wave_group_data SET enemy_id_2=21 WHERE wave_group_id=12',
    "UPDATE unit_enemy_data SET unit_name='未知' WHERE unit_id=33",
    'INSERT INTO clan_battle_2_map_data SELECT * FROM clan_battle_2_map_data WHERE phase=3',
    'INSERT INTO clan_battle_period SELECT * FROM clan_battle_period',
])
def test_incomplete_or_ambiguous_bindings_are_rejected(db, statement):
    db.execute(statement)
    with pytest.raises(ValueError):
        inspect(db, date(2026, 11, 26), 14)


def test_closed_period_and_unavailable_lap_are_rejected(db):
    with pytest.raises(ValueError, match='period'):
        inspect(db, date(2026, 11, 30), 14)
    with pytest.raises(ValueError, match='map'):
        inspect(db, date(2026, 11, 26), 6)


def test_readonly_cli_checks_manifest_and_never_creates_missing_database(db, tmp_path):
    path = tmp_path / 'master #1.db'
    db.commit()
    with sqlite3.connect(path) as copy:
        db.backup(copy)
    copy.close()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = tmp_path / 'manifest.json'
    metadata = dict(checksum_sha256=digest, size_bytes=path.stat().st_size, db_version='202611131613')
    script = Path(__file__).resolve().parents[1] / 'scripts/inspect_clan_targets.py'
    command = [sys.executable, '-X', 'utf8', '-B', str(script), str(path), '--manifest',
               str(manifest), '--date', '2026-11-26', '--lap', '14']
    manifest.write_text(json.dumps(metadata), encoding='utf-8')
    result = subprocess.run(command, capture_output=True, check=True)
    assert json.loads(result.stdout)['database'] == {'sha256': digest, 'content_version': '202611131613'}
    manifest.write_text(json.dumps(dict(metadata, checksum_sha256='f' * 64)), encoding='utf-8')
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    path.unlink()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert not path.exists()
