"""Normalized owned roster from the existing authenticated LoadIndex response.

No battle, shop, daily-task or cultivation endpoint is called. Login itself can
invalidate the game client's session; the UI must say so before starting.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


class SnapshotFailure(ValueError):
    pass


def positive(value, maximum=100_000_000):
    if type(value) is not int or not 0 < value <= maximum:
        raise SnapshotFailure('invalid_snapshot')
    return value


def selection(value):
    if type(value) is not list or not 1 <= len(value) <= 5:
        raise SnapshotFailure('select_one_team')
    result = [positive(item) for item in value]
    if len(set(result)) != len(result):
        raise SnapshotFailure('select_one_team')
    return result


def version(value):
    content_version = value
    if not isinstance(content_version, str) or len(content_version) != 12 or not content_version.isascii() or not content_version.isdecimal():
        raise SnapshotFailure('content_update_required')
    return content_version


def reference(content_version, family='content'):
    # shortcut: add a verified mapping when the official content version changes.
    hashes = {'content': {'202610141431': 'cff2ddcb060848be56b8b27dc7d659ba515bb722aee0c8e6e9345cefd05e67e9'},
              'account-progress': {'202610141431': '807905b07d97bdb9755e8361ace682554d025d899adab9ee0daf421419e7fd1c'}}[family]
    if content_version not in hashes:
        return None
    path = Path(__file__).resolve().parents[2] / 'assets/guide' / f'{family}-{content_version}.json'
    if not path.is_file():
        return None
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != hashes[content_version]:
        raise SnapshotFailure('invalid_snapshot')
    result = json.loads(data)
    if result['content_version'] != content_version:
        raise SnapshotFailure('invalid_snapshot')
    result['sha256'] = hashes[content_version]
    return result


def bounded_rows(value, maximum):
    if type(value) is not list or len(value) > maximum or any(type(r) is not dict for r in value):
        raise SnapshotFailure('invalid_snapshot')
    return value


def nonnegative(value, maximum=100_000_000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise SnapshotFailure('invalid_snapshot')
    return value


def account_progress(data, rules, unknown):
    result = dict(attribute_skill=None, master_skill=None)
    if rules is not None:
        result['mapping_sha256'] = rules['sha256']
    knight = data.get('princess_knight_info')
    if knight is None or rules is None:
        unknown.extend(['account.attribute_skill', 'account.master_skill'])
        if rules is None or data.get('unit_role_list') is None:
            unknown.append('account')
        return result
    if type(knight) is not dict:
        raise SnapshotFailure('invalid_snapshot')
    nodes = knight.get('talent_skill_last_enhanced_page_node_list')
    if nodes:
        acquired = {}
        for item in bounded_rows(nodes, 1000):
            node = positive(item.get('node_id'))
            if node in acquired:
                raise SnapshotFailure('invalid_snapshot')
            acquired[node] = nonnegative(item.get('enhance_level'), 5)
        if all(str(node) in rules['talent_nodes'] for node in acquired):
            mapped = [(rules['talent_nodes'][str(node)], level) for node, level in acquired.items()]
            if any(level > definition[1] for definition, level in mapped):
                raise SnapshotFailure('invalid_snapshot')
            pages = {definition[0] for definition, _ in mapped}
            if len(pages) != 1:
                raise SnapshotFailure('invalid_snapshot')
            page = pages.pop()
            merges = sorted(definition[2] for definition, level in mapped if definition[2] and level == definition[1])
            if merges == list(range(1, len(merges) + 1)):
                result['attribute_skill'] = dict(page=page, completed_merge=len(merges))
    elif nodes is not None:
        bounded_rows(nodes, 1000)
    latest = knight.get('team_skill_latest_node')
    if latest is not None:
        if type(latest) is not dict:
            raise SnapshotFailure('invalid_snapshot')
        node = nonnegative(latest.get('node_id'))
        level = nonnegative(latest.get('enhance_level'), 5)
        if node == level == 0:
            result['master_skill'] = dict(node=0)
        elif str(node) in rules['master_nodes']:
            previous, maximum = rules['master_nodes'][str(node)]
            if level > maximum:
                raise SnapshotFailure('invalid_snapshot')
            if maximum == 1:
                result['master_skill'] = dict(node=node if level == maximum else previous)
    for field, value in result.items():
        if value is None:
            unknown.append('account.' + field)
    return result


def detailed_progress(data, rules, unknown):
    result = dict(attribute_levels=None, unit_roles=None)
    if rules is None:
        unknown.extend('account.' + field for field in result)
        return result
    result['progress_mapping_sha256'] = rules['sha256']
    knight = data.get('princess_knight_info')
    if knight is not None and type(knight) is not dict:
        raise SnapshotFailure('invalid_snapshot')
    levels = knight.get('talent_level_info_list') if knight is not None else None
    if levels is not None:
        acquired = {}
        for row in bounded_rows(levels, 5):
            talent = positive(row.get('talent_id'), 5)
            if talent in acquired:
                raise SnapshotFailure('invalid_snapshot')
            points = row.get('total_point')
            level = None
            if points is not None:
                nonnegative(points)
                if points <= rules['attribute_levels'][-1][0]:
                    level = max(n for threshold, n in rules['attribute_levels'] if threshold <= points)
            if level is None:
                unknown.append(f'account.attribute_levels.{talent}.level')
            acquired[talent] = dict(talent_id=talent, total_point=points, level=level)
        result['attribute_levels'] = [acquired[n] for n in sorted(acquired)]
        unknown.extend(f'account.attribute_levels.{n}' for n in range(1, 6) if n not in acquired)
    else:
        unknown.append('account.attribute_levels')
    roles = data.get('unit_role_list')
    if roles is not None:
        acquired = {}
        for row in bounded_rows(roles, 8):
            role = positive(row.get('unit_role_id'), 8)
            if role in acquired:
                raise SnapshotFailure('invalid_snapshot')
            slots = []
            for slot in range(1, 5):
                path = f'account.unit_roles.{role}.slots.{slot}'
                level, enhance = row.get(f'slot_level_{slot}'), row.get(f'enhance_level_{slot}')
                for name, value in (('slot_level', level), ('enhance_level', enhance)):
                    if value is None:
                        unknown.append(path + '.' + name)
                    else:
                        nonnegative(value, 5)
                if level is not None and enhance is not None and (level, enhance) != (0, 0):
                    valid = rules['roles'][str(role)]['slots'][str(slot)].get(str(level), [])
                    if enhance not in valid:
                        raise SnapshotFailure('invalid_snapshot')
                slots.append(dict(slot=slot, slot_level=level, enhance_level=enhance))
            acquired[role] = dict(role_id=role, slots=slots)
        result['unit_roles'] = [acquired[n] for n in sorted(acquired)]
        unknown.extend(f'account.unit_roles.{n}' for n in range(1, 9) if n not in acquired)
    else:
        unknown.append('account.unit_roles')
    return result


def battle_rarity(source):
    raw, unlocked = source.get('battle_rarity'), source.get('unit_rarity')
    if raw is None or unlocked is None:
        return None
    nonnegative(raw, 6); positive(unlocked, 6)
    # AutoPCR's attribute calculation uses unlocked rarity for the explicit zero sentinel.
    if raw == 0 or raw == unlocked:
        return unlocked
    return raw if unlocked == 5 and raw in (3, 4) else None


def equipment_effects(item, meta, rules, path, unknown):
    skills = '；'.join(meta['skills']) or '无附带技能'
    group = meta['sub_status_group']
    entries = item.get('sub_status')
    if group is None:
        if entries not in (None, []):
            raise SnapshotFailure('invalid_snapshot')
        return skills + '；无随机附加词条'
    if entries is None:
        unknown.append(path + '.effects')
        return None
    totals, slots = {}, set()
    for row in bounded_rows(entries, 4):
        slot = positive(row.get('slot_number'), 4)
        stat = positive(row.get('status'), 1000)
        step = positive(row.get('step'), 5)
        if slot in slots:
            raise SnapshotFailure('invalid_snapshot')
        slots.add(slot)
        values = rules['ex_sub_status'].get(group, {}).get(str(stat))
        if values is None:
            unknown.append(path + '.effects')
            return None
        totals[stat] = totals.get(stat, 0) + values[step - 1]
    if slots != {1, 2, 3, 4}:
        unknown.append(path + '.effects')
        return None
    names = {1:'生命值',2:'物理攻击',3:'物理防御',4:'魔法攻击',5:'魔法防御',6:'物理暴击',7:'魔法暴击',12:'物理贯通',13:'魔法贯通'}
    if not totals.keys() <= names.keys():
        unknown.append(path + '.effects')
        return None
    terms = [f'{names[stat]} +{value} 点' if stat in (12,13) else f'{names[stat]} +{value / 100:g}%'
        for stat,value in sorted(totals.items())]
    return skills + '；附加：' + '、'.join(terms)


def extra_equipment(source, inventory, rules, prefix, unknown):
    slots = source.get('ex_equip_slot')
    if slots is None:
        unknown.append(prefix + '.ex_equipment')
        return []
    result = []
    for index, slot in enumerate(bounded_rows(slots, 3), 1):
        path = prefix + f'.ex_equipment.{index}'
        serial = slot.get('serial_id')
        if serial is None:
            unknown.append(path)
            continue
        nonnegative(serial, 2**63 - 1)
        if serial == 0:
            if slot.get('ex_equipment_id') not in (None, 0):
                raise SnapshotFailure('invalid_snapshot')
            result.append(dict(slot=index, is_equipped=False))
            continue
        item = inventory.get(serial)
        if item is None or rules is None:
            unknown.append(path)
            continue
        identifier = positive(item.get('ex_equipment_id'))
        if slot.get('ex_equipment_id') not in (None, 0, identifier):
            raise SnapshotFailure('invalid_snapshot')
        meta = rules['ex_equipment'].get(str(identifier))
        if meta is None or item.get('enhancement_pt') is None or item.get('rank') is None:
            unknown.append(path)
            continue
        points = nonnegative(item['enhancement_pt']); rank = nonnegative(item['rank'], 100)
        levels = [r for r in rules['ex_enhancement'].get(str(meta['rarity']), []) if r[0] <= points]
        if any(r[2] > rank for r in levels):
            unknown.append(path)
            continue
        result.append(dict(slot=index, is_equipped=True, equipment_id=identifier, name=meta['name'],
            rarity=meta['rarity'], rank=rank, enhancement_level=max((r[1] for r in levels), default=0),
            enhancement_pt=points, clan_battle_equipment=bool(meta['clan_battle']),
            effects=equipment_effects(item, meta, rules, path, unknown)))
    for index in range(len(slots) + 1, 4):
        unknown.append(prefix + f'.ex_equipment.{index}')
    return result


def normalize(load, account_id, content_version, unit_ids=None, *, observed_at=None):
    requested = selection(unit_ids) if unit_ids is not None else None
    positive(account_id, 2**63 - 1)
    version(content_version)
    # JSON round-trip converts the existing Pydantic enum values to plain integers.
    data = json.loads(load.json(by_alias=True)) if hasattr(load, 'json') else load
    if (type(data) is not dict or type(data.get('user_info')) is not dict
        or type(data['user_info'].get('viewer_id')) is not int or data['user_info']['viewer_id'] != account_id):
        raise SnapshotFailure('account_mismatch')
    rows = data.get('unit_list')
    if type(rows) is not list or len(rows) > 2000:
        raise SnapshotFailure('invalid_snapshot')
    roster = {}
    for row in rows:
        if type(row) is not dict:
            raise SnapshotFailure('invalid_snapshot')
        uid = positive(row.get('id'))
        if uid in roster:
            raise SnapshotFailure('invalid_snapshot')
        roster[uid] = row
    complete = requested is None
    if complete:
        requested = sorted(roster)
    units, unknown = [], ['battle_settings']
    rules = reference(content_version)
    account = account_progress(data, rules, unknown)
    account.update(detailed_progress(data, reference(content_version, 'account-progress'), unknown))
    # Resolve only equipped normal EX slots, never persist the inventory.
    wanted = set()
    for uid in requested:
        slots = roster.get(uid, {}).get('ex_equip_slot')
        if slots is not None:
            for slot in bounded_rows(slots, 3):
                if slot.get('serial_id') is not None:
                    wanted.add(nonnegative(slot['serial_id'], 2**63 - 1))
    inventory = {}
    if data.get('user_ex_equip') is not None and wanted:
        for item in bounded_rows(data['user_ex_equip'], 100_000):
            serial = item.get('serial_id')
            if type(serial) is int and serial in wanted and serial > 0:
                if serial in inventory:
                    raise SnapshotFailure('invalid_snapshot')
                inventory[serial] = item
    for uid in requested:
        prefix = f'units.{uid}'
        if uid not in roster:
            unknown.append(prefix)
            continue
        source = roster[uid]
        unit = dict(unit_id=uid, skills={}, unique_equipment=[])
        def number(value, path, minimum=0, maximum=100_000):
            if value is None:
                unknown.append(prefix + '.' + path)
                return None
            if type(value) is not int or not minimum <= value <= maximum:
                raise SnapshotFailure('invalid_snapshot')
            return value
        for dest, src, maximum in (('level', 'unit_level', 100_000), ('rank', 'promotion_level', 100_000), ('rarity', 'unit_rarity', 6)):
            unit[dest] = number(source.get(src), dest, 1, maximum)
        slots = source.get('equip_slot')
        if slots is not None and (type(slots) is not list or len(slots) > 6 or any(type(s) is not dict for s in slots)):
            raise SnapshotFailure('invalid_snapshot')
        if slots is not None and any(s.get('is_slot') is not None and type(s['is_slot']) is not bool for s in slots):
            raise SnapshotFailure('invalid_snapshot')
        unit['equipment_count'] = None
        if slots is not None and len(slots) == 6 and all(type(s.get('is_slot')) is bool for s in slots):
            for slot in slots:
                if slot['is_slot']:
                    positive(slot.get('id'))
            unit['equipment_count'] = sum(slot['is_slot'] for slot in slots)
        else:
            unknown.append(prefix + '.equipment_count')
        stage = number(source.get('exceed_stage'), 'limit_break')
        unit['limit_break'] = bool(stage) if stage in (0, 1) else None
        if stage is not None and stage not in (0, 1):
            unknown.append(prefix + '.limit_break')
        unit['battle_rarity'] = battle_rarity(source)
        if unit['battle_rarity'] is None:
            unknown.append(prefix + '.battle_rarity')
        unit['ex_equipment'] = extra_equipment(source, inventory, rules, prefix, unknown)
        for field, names in (('union_burst', ('ub',)), ('main_skill', ('s1', 's2')), ('ex_skill', ('ex',))):
            entries = source.get(field)
            if entries is not None and (type(entries) is not list or len(entries) > len(names) or any(type(e) is not dict for e in entries)):
                raise SnapshotFailure('invalid_snapshot')
            for index, name in enumerate(names):
                value = entries[index].get('skill_level') if entries and index < len(entries) else None
                unit['skills'][name] = number(value, 'skills.' + name)
        equipment = source.get('unique_equip_slot')
        if equipment is not None and (type(equipment) is not list or len(equipment) > 2 or any(type(e) is not dict for e in equipment)):
            raise SnapshotFailure('invalid_snapshot')
        for slot in (1, 2):
            path = f'unique_equipment.{slot}'
            item = equipment[slot - 1] if equipment and slot <= len(equipment) else None
            if item is None or type(item.get('is_slot')) is not bool:
                unknown.append(prefix + '.' + path)
                continue
            equipped = item['is_slot']
            identifier = positive(item.get('id')) if equipped else 0
            level = number(item.get('enhancement_level'), path + '.level') if equipped else 0
            unit['unique_equipment'].append(dict(slot=slot, equipment_id=identifier, is_equipped=equipped, level=level))
        units.append(unit)
    return dict(schema_version=1, server='cn-bilibili', account_id=str(account_id), content_version=content_version,
        observed_at=observed_at or datetime.now(timezone.utc).isoformat(), ownership_complete=complete,
        units=units, account=account, unknown_fields=unknown)


async def collect(username, password, account_id, content_version, unit_ids=None, *, app_version=None, stop=lambda: False, client_factory=None, on_captcha=None):
    if unit_ids is not None:
        selection(unit_ids)
    positive(account_id, 2**63 - 1)
    version(content_version)
    if stop():
        raise SnapshotFailure('stopped')
    if client_factory is None:
        from .labyrinth.protocol import LabyrinthClient
        client_factory = LabyrinthClient
    from .labyrinth.captcha import HumanCaptcha
    client = client_factory(username, password, app_version=app_version,
        manual_captcha=HumanCaptcha(on_captcha) if on_captcha is not None else None)
    username = password = ''
    task = None
    try:
        task = asyncio.create_task(client.login(account_id))
        async with asyncio.timeout(800 if on_captcha is not None else 190):
            while not task.done():
                if stop():
                    raise SnapshotFailure('stopped')
                await asyncio.wait({task}, timeout=.1)
            load = await task
        if stop():
            raise SnapshotFailure('stopped')
        if client.auth.content_version != content_version:
            raise SnapshotFailure('content_update_required')
        return normalize(load, account_id, content_version, unit_ids)
    finally:
        client.close()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
