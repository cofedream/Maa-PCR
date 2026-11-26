"""Inspect one CN clan battle lap in a restored master database for calibration review."""
import argparse
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3


def _one(rows, label):
    if len(rows) != 1:
        raise ValueError(f'Expected one {label}, found {len(rows)}')
    return rows[0]


def inspect(db, day, lap):
    if lap < 1:
        raise ValueError('Lap must be positive')
    db.row_factory = sqlite3.Row
    period = _one(db.execute("""
        SELECT clan_battle_id, start_time, end_time FROM clan_battle_period
        WHERE ? BETWEEN substr(start_time, 1, 10) AND substr(end_time, 1, 10)
    """, (day.strftime('%Y/%m/%d'),)).fetchall(), 'battle period for this CN calendar date')
    battle_id = period['clan_battle_id']
    maps = db.execute("""
        SELECT * FROM clan_battle_2_map_data WHERE clan_battle_id = ?
        AND lap_num_from > 0 ORDER BY lap_num_from
    """, (battle_id,)).fetchall()
    selected = _one([row for row in maps if row['lap_num_from'] <= lap
                     and (row['lap_num_to'] == -1 or lap <= row['lap_num_to'])], 'map for this lap')
    bosses = []
    for slot in range(1, 6):
        boss = _one(db.execute("""
            SELECT b.order_num AS slot, b.boss_id, w.wave_group_id, e.enemy_id, e.unit_id,
                   e.name, u.unit_name
            FROM clan_battle_2_boss_data b
            JOIN wave_group_data w ON w.wave_group_id = ?
            JOIN enemy_parameter e ON e.enemy_id = w.enemy_id_1
            JOIN unit_enemy_data u ON u.unit_id = e.unit_id
            WHERE b.boss_id = ? AND b.clan_battle_id = ? AND b.difficulty = ? AND b.order_num = ?
              AND w.enemy_id_2 = 0 AND w.enemy_id_3 = 0 AND w.enemy_id_4 = 0 AND w.enemy_id_5 = 0
        """, (selected[f'wave_group_id_{slot}'], selected[f'boss_id_{slot}'], battle_id,
              selected['difficulty'], slot)).fetchall(), f'single-enemy Boss in slot {slot}')
        if not boss['name'] or boss['name'] != boss['unit_name']:
            raise ValueError(f'Unconfirmed enemy name for slot {slot}')
        bosses.append({key: boss[key] for key in ('slot', 'boss_id', 'wave_group_id', 'enemy_id', 'unit_id', 'name')})
    return {
        'cn_calendar_date': day.isoformat(), 'period': dict(period), 'lap': lap, 'phase': selected['phase'],
        'lap_ranges': [{'phase': row['phase'], 'first': row['lap_num_from'],
                        'last': None if row['lap_num_to'] == -1 else row['lap_num_to']} for row in maps],
        'bosses': bosses,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='existing, restored CN master SQLite database')
    parser.add_argument('--date', required=True, type=date.fromisoformat, help='CN calendar date (YYYY-MM-DD)')
    parser.add_argument('--lap', required=True, type=int)
    parser.add_argument('--manifest', type=Path, help='optional release manifest to verify file size and digest')
    args = parser.parse_args()
    try:
        with args.database.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        identity = {'sha256': digest}
        if args.manifest:
            manifest = json.loads(args.manifest.read_bytes())
            if (manifest['checksum_sha256'] != digest or manifest['size_bytes'] != args.database.stat().st_size):
                raise ValueError('Database does not match the supplied manifest')
            identity['content_version'] = manifest['db_version']
        with closing(sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            result = inspect(db, args.date, args.lap)
    except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps({'database': identity, **result}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
