"""遗物目录必须与游戏 master 数据、图标溯源和 mark 词表保持一致。

GUI 的遗物展示来自 ``assets/labyrinth/labyrinth-relics.json``（id/名称/mark/加成/稀有度/效果/图标），
图标来自 ``assets/labyrinth/relic-pack``。名称、mark 与加成数一旦漂移，用户看到的就是另一个遗物的
属性，所以这里把三件事锁在一起：

- JSON 的行数/名称/mark/加成一 == 游戏 master 数据 ``labyrinth_relic``（master 202611021739）；
- mark 只能取 ``strategies.MARKS`` 的 加速/会心/守备/强化/弱体（mark 系列 削弱/暴击 先按 ALIASES 归一）；
- JSON 的 icon 文件存在，且与 ``relic-pack/PROVENANCE.json`` 的 sha256/尺寸一致。
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa.labyrinth.strategies import MARKS

ASSETS = ROOT / 'assets/labyrinth'
CATALOG = ASSETS / 'labyrinth-relics.json'
PACK = ASSETS / 'relic-pack'
ICON_NAME = re.compile(r'^relic-pack/icons/([0-9]{5,6})\.png$')

# 官方名称/mark/加成数来自 data/labyrinth/master-cn 的 labyrinth_relic 与
# labyrinth_relic_mark_series（master 202611021739）。mark 是遗物固有属性：
# 30001 元素涌动宝珠 在 master 里 relic_mark_id=0、relic_mark_count=0，没有标记归类。
MASTER_VERSION = '202611021739'
MASTER = {
    11001: ('加速玉饰', '加速', 1), 11002: ('水莲之盾', '加速', 1), 11003: ('青辉之环', '加速', 1),
    11004: ('冷玉之杖', '加速', 1), 11005: ('踏风鞋', '加速', 1), 12001: ('紫晶琉璃', '弱体', 1),
    12002: ('魔杖咒术', '弱体', 1), 12003: ('暗剑毒药', '弱体', 1), 12004: ('破守帽退散', '弱体', 1),
    12005: ('崩刀山月', '弱体', 1), 13001: ('杀戮之矛', '会心', 1), 13002: ('赏花之刀', '会心', 1),
    13003: ('裂空拳套', '会心', 1), 13004: ('暴击弧弓', '会心', 1), 13005: ('噬魂手斧', '会心', 1),
    14001: ('强化宝石', '强化', 1), 14002: ('抗铁护甲', '强化', 1), 14003: ('抗魔战裙', '强化', 1),
    14004: ('蛇纹之剑', '强化', 1), 14005: ('防守铠甲', '强化', 1), 15001: ('回生杖', '守备', 1),
    15002: ('丰命指环', '守备', 1), 15003: ('铁防甲', '守备', 1), 15004: ('魔封衣', '守备', 1),
    15005: ('再生之手镯', '守备', 1), 21001: ('流烈耳饰', '加速', 2), 21002: ('星泪辉盾', '加速', 2),
    21003: ('苍珠手镯', '加速', 2), 21004: ('星明仪杖', '加速', 2), 21005: ('疾风翼靴', '加速', 2),
    22001: ('暗至宝圣物', '弱体', 2), 22002: ('终焉邪眼杖', '弱体', 2), 22003: ('影刃剑幽影', '弱体', 2),
    22004: ('空灵玫瑰饰帽', '弱体', 2), 22005: ('霹雳斩赤刃', '弱体', 2), 23001: ('致命烈矛', '会心', 2),
    23002: ('绯红太刀', '会心', 2), 23003: ('爆裂手甲', '会心', 2), 23004: ('苍穹飞弓', '会心', 2),
    23005: ('炼狱战斧', '会心', 2), 24001: ('进化之耳环', '强化', 2), 24002: ('红反钢护甲', '强化', 2),
    24003: ('圣减术次元战裙', '强化', 2), 24004: ('炎王巨剑', '强化', 2), 24005: ('樱坚牢战衣', '强化', 2),
    25001: ('创世绿杖', '守备', 2), 25002: ('空命护珠', '守备', 2), 25003: ('钢铁坚铠', '守备', 2),
    25004: ('魔护灵装', '守备', 2), 25005: ('绿护之盾戒', '守备', 2), 30001: ('元素涌动宝珠', None, 0),
    31001: ('瞬闪蝶月饰', '加速', 3), 31002: ('海宝华辉盾', '加速', 3), 31003: ('碧贵石圣戒', '加速', 3),
    31004: ('永天镜王笏', '加速', 3), 31005: ('炽天使翔靴', '加速', 3), 31006: ('风神的护风恩宠', '加速', 5),
    32001: ('幻兽饰诱惑', '弱体', 3), 32002: ('深渊蚀杖诅咒', '弱体', 3), 32003: ('灭刻凶刃剧毒', '弱体', 3),
    32004: ('灿然御缠阿诺拉·贝拉', '弱体', 3), 32005: ('断壁锁刃粉碎', '弱体', 3), 32006: ('霜影虚装深渊君主', '弱体', 5),
    33001: ('绝杀战戟', '会心', 3), 33002: ('凤凰利刃', '会心', 3), 33003: ('地脉陨灭', '会心', 3),
    33004: ('审判弓矢', '会心', 3), 33005: ('收获之刻', '会心', 3), 33006: ('天启纪元', '会心', 5),
    34001: ('极醒护身符', '强化', 3), 34002: ('拒断之刃白银', '强化', 3), 34003: ('绯华荣华石榴石', '强化', 3),
    34004: ('霸光帝龙之荣耀', '强化', 3), 34005: ('华舞玲珑春抚子', '强化', 3), 34006: ('天戴冠巅峰之剑', '强化', 5),
    35001: ('妖精灵杖·愈光', '守备', 3), 35002: ('泉涌命环·丰饶', '守备', 3), 35003: ('白光辉铠·断壁', '守备', 3),
    35004: ('幽玄煌装·绝魔', '守备', 3), 35005: ('轮回之翡翠珠戒', '守备', 3), 35006: ('天命授环·伊邪那', '守备', 5),
    40001: ('公主之力', '守备', 1), 40002: ('星幽增幅', '强化', 1), 40003: ('咲恋救济院誓约', '加速', 1),
    40004: ('信仰骑士', '会心', 1), 40005: ('拉比林斯权能', '弱体', 1), 50001: ('公主魅惑', '强化', 1),
    50002: ('星辉吐息', '守备', 1), 50003: ('咲恋救济院贵族', '会心', 1), 50004: ('女君庆典', '弱体', 1),
    50005: ('拉比林斯之主', '加速', 1),
}
# 线上 master 没有条目、因此不收录的客户端图标（未上线或占位）。
EXCLUDED = {40006, 40007, 40008, 40009, 40010, 50006, 50007, 50008, 50009, 50010, 999999}
EFFECTS = {
    11001: '技能值上升量＋5',
    30001: '属性加成＋100％',
    35004: '每20秒展开魔法攻击无效化屏障（【守备】数×1500无效）',
    50005: '挑战开始时随机获得1个★1的迷宫遗物',
}


def catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def provenance():
    return json.loads((PACK / 'PROVENANCE.json').read_text(encoding='utf-8'))


def rows():
    return catalog()['relics']


def test_catalog_schema_and_rows_match_the_game_master():
    data = catalog()
    assert data['schemaVersion'] == 1
    assert data['masterVersion'] == MASTER_VERSION
    assert set(data) == {'schemaVersion', 'masterVersion', 'note', 'relics'}
    assert 'master' in data['note']
    items = data['relics']
    assert len(items) == len(MASTER) == 91
    # 按 id 升序，id 不重复；字段集合固定，缺字段就是 GUI 少显示一列。
    assert [row['id'] for row in items] == sorted(MASTER)
    assert len({row['id'] for row in items}) == 91
    for row in items:
        assert set(row) == {'id', 'name', 'mark', 'bonus', 'rarity', 'effect', 'icon'}
        assert type(row['id']) is int and type(row['rarity']) is int and 1 <= row['rarity'] <= 3
        assert type(row['bonus']) is int and 0 <= row['bonus'] <= 5
        assert type(row['name']) is str and row['name']
        assert type(row['effect']) is str and row['effect']
    assert {row['id']: (row['name'], row['mark'], row['bonus']) for row in items} == MASTER


def test_catalog_marks_use_the_python_mark_vocabulary():
    marks = [row['mark'] for row in rows()]
    # mark 系列里的 削弱/暴击 已归一成 弱体/会心；词表外的新 mark 必须先改 strategies.MARKS。
    assert set(marks) - {None} == set(MARKS)
    assert marks.count(None) == 1
    assert next(row for row in rows() if row['mark'] is None)['id'] == 30001
    assert {mark: marks.count(mark) for mark in MARKS} == {mark: 18 for mark in MARKS}


def test_catalog_effects_are_the_game_master_descriptions():
    # 35004 的【守备】+3 与实机 2026-11-07 失败帧的【守备+3】一致，是 mark/bonus 的对照锚点。
    assert {row['id']: row['effect'] for row in rows() if row['id'] in EFFECTS} == EFFECTS
    assert all(row['effect'] == row['effect'].strip() for row in rows())


def test_icons_exist_and_match_provenance():
    prov = provenance()
    assert prov['schemaVersion'] == 1
    assert prov['pack'] == 'labyrinth-relic-pack'
    assert prov['masterVersion'] == catalog()['masterVersion'] == MASTER_VERSION
    assert prov['distribution_permission_not_inferred'] is True
    assert prov['private_captures_included'] is False and prov['private_uid_included'] is False
    records = {row['path']: row for row in prov['icons']}
    assert len(records) == 91
    referenced = set()
    for row in rows():
        match = ICON_NAME.fullmatch(row['icon'])
        assert match and int(match.group(1)) == row['id'], row['icon']
        assert row['icon'].isascii()
        path = ASSETS / row['icon']
        assert path.is_file(), f'缺少遗物图标：{row["icon"]}'
        raw = path.read_bytes()
        assert raw[:8] == b'\x89PNG\r\n\x1a\n'
        width, height = int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')
        record = records[row['icon'][len('relic-pack/'):]]
        referenced.add(record['path'])
        assert record['sha256'] == hashlib.sha256(raw).hexdigest()
        assert record['bytes'] == len(raw) == path.stat().st_size
        assert (width, height) == (record['width'], record['height']) == (128, 128)
        assert record['pixelFormat'] == '32bppArgb'
        assert record['sourceBundle'] == f'icon_labyrinth_icon_relic_{row["id"]}.unity3d'
    # 双向一致：没有未引用的图标，也没有引用缺溯源的图标。
    assert referenced == set(records)


def test_pack_contains_only_the_masterdata_relics():
    paths = sorted((PACK / 'icons').glob('*'))
    assert {path.name for path in paths} == {f'{relic_id}.png' for relic_id in MASTER}
    # ASCII 文件名：中文名只留在 PROVENANCE/origin 里，运行包不依赖中文路径。
    assert all(path.name.isascii() and path.stem.isdigit() for path in paths)
    assert not {f'{relic_id}.png' for relic_id in EXCLUDED} & {path.name for path in paths}
    assert len(EXCLUDED) == 11
    assert (PACK / 'PROVENANCE.json').is_file()


def test_provenance_mirrors_the_boss_pack_and_records_the_client_source():
    prov, boss = provenance(), json.loads(
        (ASSETS / 'boss-pack/PROVENANCE.json').read_text(encoding='utf-8'))
    # 结构与字段照抄 boss-pack，打包/审计脚本才能用同一套读取逻辑。
    assert set(prov) == set(boss)
    assert set(prov['icons'][0]) == set(boss['icons'][0])
    assert prov['sourceIndex']['file'] == '索引.csv'
    assert re.fullmatch(r'[0-9a-f]{64}', prov['sourceIndex']['sha256'])
    assert type(prov['sourceIndex']['bytes']) is int and prov['sourceIndex']['bytes'] > 0
    assert prov['author'] == 'MAA PCR contributors'
    assert '11.7.2' in prov['origin'] and 'com.bilibili.priconne' in prov['origin']
    assert 'icon_labyrinth_icon_relic_' in prov['origin']
    assert '40006-40010' in prov['origin'] and '50006-50010' in prov['origin']
    assert '999999' in prov['origin']
