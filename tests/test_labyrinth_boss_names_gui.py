"""The GUI Boss filter must stay aligned with the Python route table.

The GUI used to ask for raw boss IDs in a free-text box; it now offers the ten
selectable bosses by name and saves the corresponding IDs.  Two facts must stay
aligned with Python: the accepted IDs per area (``BOSSES`` in
``src/pcr_maa/labyrinth/routes.py``), and -- because the user clicks a *name* --
each name/ID pairing, which must match the game master data; a mislabeled row
silently filters a different boss.  The ``BOSSES`` row order is only kept in
sync so the two tables are easy to compare: ``Policy.boss_matches`` tests ID
membership, not order.

This guard reads the C# table, the defaults, and the XAML wiring, so drift fails
on Linux without a .NET toolchain.  GUI compilation and rendering stay with the
Windows harness (PcrProfileSwitch.Tests LabyrinthBossFilterChecks) and CI.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa.labyrinth.routes import BOSSES, Policy

SETTINGS = ROOT / 'gui/MFAAvalonia/MFAAvalonia/Pcr/PcrLabyrinthSettings.cs'
VIEW = ROOT / 'gui/MFAAvalonia/MFAAvalonia/Pcr/PcrLabyrinthView.axaml'
ROW = re.compile(r'\(\s*(\d{6})\s*,\s*"([^"]+)"\s*\)')

# 官方名称来自 data/labyrinth/master-cn 的 labyrinth_enemy_parameter（master 202611021739）；
# 名称与 ID 配错时用户会按错误的名字勾选，实际存下的是另一个首领的 ID。
MASTER_NAMES = {
    301206: '巨型魔像', 303306: '暗黑滴水嘴兽', 319604: '冰霜魔狼',
    312505: '厄勒克特拉夫人', 306604: '毒液沙罗曼蛇',
    302501: '奇美拉', 301701: '炸脖龙', 310103: '愤怒巨龙',
    315004: '领主哥布林', 319401: '究极守护者',
}


def _source():
    return SETTINGS.read_text(encoding='utf-8')


def table(area):
    block = re.search(
        rf'public static readonly \(int Id, string Name\)\[\] Area{area} = \{{(.*?)\}};',
        _source(), re.S)
    assert block, f'Area{area} boss table missing from PcrLabyrinthSettings.cs'
    return [(int(boss_id), name) for boss_id, name in ROW.findall(block.group(1))]


def defaults():
    match = re.search(
        r'area==3 \? new\[\]\{([\d,]+)\} : area==5 \? new\[\]\{([\d,]+)\}', _source())
    assert match, 'PcrLabyrinthBossTable.DefaultIds expression missing'
    return (tuple(int(value) for value in match.group(1).split(',')),
            tuple(int(value) for value in match.group(2).split(',')))


def test_gui_table_ids_match_python_bosses():
    for area in (3, 5):
        ids = [boss_id for boss_id, _ in table(area)]
        assert tuple(ids) == BOSSES[area], (
            f'area{area} GUI ids {ids} != routes.BOSSES {list(BOSSES[area])}; '
            'keep the two tables aligned (Policy matches by id, not order)')
        assert len(set(ids)) == 5


def test_gui_names_match_the_game_master_names():
    rows = {boss_id: name for area in (3, 5) for boss_id, name in table(area)}
    assert rows == MASTER_NAMES, (
        'GUI boss labels drifted from labyrinth_enemy_parameter: '
        + repr({boss_id: (rows.get(boss_id), name)
                for boss_id, name in MASTER_NAMES.items() if rows.get(boss_id) != name}))


def test_every_gui_row_is_accepted_by_the_policy_contract():
    for area in (3, 5):
        ids = [boss_id for boss_id, _ in table(area)]
        # 全选与单选都必须通过 Policy 校验，否则 GUI 能存出 Python 拒绝的配置。
        Policy(**{f'area{area}_bosses': tuple(ids)})
        for boss_id in ids:
            Policy(**{f'area{area}_bosses': (boss_id,)})


def test_gui_defaults_match_the_policy_defaults():
    area3, area5 = defaults()
    assert area3 == Policy().area3_bosses
    assert area5 == Policy().area5_bosses


def test_gui_names_are_distinct_and_nonempty():
    for area in (3, 5):
        names = [name for _, name in table(area)]
        assert all(name.strip() for name in names)
        assert len(set(names)) == len(names)


def test_raw_boss_id_inputs_are_gone():
    settings, view = _source(), VIEW.read_text(encoding='utf-8')
    for removed in ('Area3BossIds', 'Area5BossIds'):
        assert removed not in settings
        assert removed not in view


def test_view_offers_two_checkbox_lists_with_shared_hint():
    view = VIEW.read_text(encoding='utf-8')
    for binding in ('LabyrinthSettings.Area3Bosses', 'LabyrinthSettings.Area5Bosses'):
        assert binding in view
    assert view.count('IsChecked="{Binding Selected, Mode=TwoWay}"') == 2
    assert '全部不勾表示不限制' in view
    # 提示行原先手写的 ID 文案已由表与标签取代，留着就是第二份会漂移的事实。
    assert '厄勒克特拉夫人 312505' not in view
