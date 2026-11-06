"""The GUI Boss filter must stay aligned with the Python route table.

The GUI used to ask for raw boss IDs in a free-text box; it now offers the ten
selectable bosses by name.  That duplicates a fact Python already owns: ``BOSSES``
in ``src/pcr_maa/labyrinth/routes.py`` fixes both the accepted IDs per area and,
crucially, their *order* — ``Policy.boss_matches`` maps a quest ordinal back
through ``BOSSES[area][(ordinal-1)%5]``, so a reordered GUI table would filter a
different boss while still looking valid.

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


def test_gui_table_order_matches_python_bosses():
    for area in (3, 5):
        ids = [boss_id for boss_id, _ in table(area)]
        assert tuple(ids) == BOSSES[area], (
            f'area{area} GUI order {ids} != routes.BOSSES {list(BOSSES[area])}; '
            'the ordinal modulo in Policy.boss_matches depends on this order')
        assert len(set(ids)) == 5


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
