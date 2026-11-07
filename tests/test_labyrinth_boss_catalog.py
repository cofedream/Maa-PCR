"""Boss 目录必须与 Python 路线表、游戏 master 名称和图标溯源保持一致。

GUI 的 Boss 筛选来自 ``assets/labyrinth/labyrinth-bosses.json``（名称/ID/区域/轮转序号/图标），
Python 的 ``routes.BOSSES`` 仍只保存 ID，且顺序就是协议轮转。两边一旦漂移，用户按名称勾选
就会静默筛到另一个首领，所以这里把三件事锁在一起：

- JSON 的 id 与轮转顺序 == ``routes.BOSSES``；
- JSON 的 name == 游戏 master 数据 ``labyrinth_enemy_parameter``（master 202611021739）；
- JSON 的 icon 文件存在，且与 ``boss-pack/PROVENANCE.json`` 的 sha256/尺寸一致。

C# 侧不再保存第二份 id/名称表；GUI 运行期加载与 Bitmap 解码由
``PcrProfileSwitch.Tests`` 的 ``LabyrinthBossFilterChecks`` 覆盖。
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa.labyrinth.routes import BOSSES, Policy

ASSETS = ROOT / 'assets/labyrinth'
CATALOG = ASSETS / 'labyrinth-bosses.json'
PACK = ASSETS / 'boss-pack'
SETTINGS = ROOT / 'gui/MFAAvalonia/MFAAvalonia/Pcr/PcrLabyrinthSettings.cs'
LOADER = ROOT / 'gui/MFAAvalonia/MFAAvalonia/Pcr/PcrLabyrinthBosses.cs'
VIEW = ROOT / 'gui/MFAAvalonia/MFAAvalonia/Pcr/PcrLabyrinthView.axaml'
ICON_NAME = re.compile(r'^boss-pack/icons/([0-9]{6})\.png$')

# 官方名称来自 data/labyrinth/master-cn 的 labyrinth_enemy_parameter（master 202611021739）。
# 名称与 ID 配错时用户会按错误的名字勾选，实际存下的是另一个首领的 ID。
MASTER_VERSION = '202611021739'
MASTER_NAMES = {
    301206: '巨型魔像', 303306: '暗黑滴水嘴兽', 319604: '冰霜魔狼',
    312505: '厄勒克特拉夫人', 306604: '毒液沙罗曼蛇',
    302501: '奇美拉', 301701: '炸脖龙', 310103: '愤怒巨龙',
    315004: '领主哥布林', 319401: '究极守护者',
}


def catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def provenance():
    return json.loads((PACK / 'PROVENANCE.json').read_text(encoding='utf-8'))


def rows(area):
    return sorted((row for row in catalog()['bosses'] if row['area'] == area),
                  key=lambda row: row['ordinal'])


def test_catalog_schema_and_rotation_match_python_bosses():
    data = catalog()
    assert data['schemaVersion'] == 1
    assert data['masterVersion'] == MASTER_VERSION
    assert len(data['bosses']) == 10
    assert {row['area'] for row in data['bosses']} == {3, 5}
    for area in (3, 5):
        area_rows = rows(area)
        assert len(area_rows) == 5
        # 顺序即协议轮转 (quest 序号 - 1) % 5；Python 的 BOSSES 是同一顺序。
        assert [row['ordinal'] for row in area_rows] == [1, 2, 3, 4, 5]
        assert tuple(row['id'] for row in area_rows) == BOSSES[area]
    assert len({row['id'] for row in data['bosses']}) == 10


def test_catalog_names_match_the_game_master_names():
    assert {row['id']: row['name'] for row in catalog()['bosses']} == MASTER_NAMES


def test_every_catalog_row_is_accepted_by_the_policy_contract():
    for area in (3, 5):
        ids = tuple(row['id'] for row in rows(area))
        # 全选与单选都必须通过 Policy 校验，否则 GUI 能存出 Python 拒绝的配置。
        Policy(**{f'area{area}_bosses': ids})
        for boss_id in ids:
            Policy(**{f'area{area}_bosses': (boss_id,)})


def test_catalog_defaults_match_the_policy_defaults():
    selection = catalog()['defaultSelection']
    assert set(selection) == {'3', '5'}
    assert tuple(selection['3']) == Policy().area3_bosses
    assert tuple(selection['5']) == Policy().area5_bosses
    for area in (3, 5):
        assert set(selection[str(area)]) <= {row['id'] for row in rows(area)}


def test_icons_exist_and_match_provenance():
    prov = provenance()
    assert prov['schemaVersion'] == 1
    assert prov['masterVersion'] == catalog()['masterVersion'] == MASTER_VERSION
    assert prov['distribution_permission_not_inferred'] is True
    records = {row['path']: row for row in prov['icons']}
    assert len(records) == 10
    referenced = set()
    for row in catalog()['bosses']:
        match = ICON_NAME.match(row['icon'])
        assert match and match.group(1) == str(row['id']), row['icon']
        path = ASSETS / row['icon']
        assert path.is_file(), f'缺少 Boss 图标：{row["icon"]}'
        raw = path.read_bytes()
        assert raw[:8] == b'\x89PNG\r\n\x1a\n'
        width, height = int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')
        record = records[row['icon'][len('boss-pack/'):]]
        referenced.add(record['path'])
        assert record['sha256'] == hashlib.sha256(raw).hexdigest()
        assert record['bytes'] == len(raw) == path.stat().st_size
        assert (width, height) == (record['width'], record['height']) == (128, 128)
    # 双向一致：没有未引用的图标，也没有引用缺溯源的图标。
    assert referenced == set(records)


def test_csharp_no_longer_keeps_a_second_boss_table():
    settings = SETTINGS.read_text(encoding='utf-8')
    assert 'PcrLabyrinthBossCatalog.Load()' in settings
    assert 'PcrLabyrinthBossTable' not in settings
    for name in MASTER_NAMES.values():
        assert name not in settings, f'Boss 名称仍在 C# 里重复：{name}'
    loader = LOADER.read_text(encoding='utf-8')
    assert 'labyrinth-bosses.json' in loader
    assert 'boss-pack/icons/' in loader


def test_view_offers_two_checkbox_lists_with_icons_and_shared_hint():
    view = VIEW.read_text(encoding='utf-8')
    for binding in ('LabyrinthSettings.Area3Bosses', 'LabyrinthSettings.Area5Bosses'):
        assert binding in view
    # 开局角色选择也有图标绑定；这里只看两个 Boss 区域的列表块。
    for area in (3, 5):
        start = view.index(f'Name="Area{area}BossInput"')
        block = view[start:view.index('</ItemsControl>', start)]
        assert 'IsChecked="{Binding Selected, Mode=TwoWay}"' in block
        assert 'Source="{Binding Icon}"' in block
        assert 'Text="{Binding Label}"' in block
    assert '全部不勾表示不限制' in view
    # 提示行原先手写的 ID 文案已由目录与图标取代，留着就是第二份会漂移的事实。
    assert '厄勒克特拉夫人 312505' not in view
