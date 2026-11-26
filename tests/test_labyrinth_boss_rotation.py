"""Boss quest_id rotation must agree with the CN master data.

``routes.Policy.boss_matches`` derives the boss from the quest id instead of
reading the database, so the derivation has to stay locked to real master rows.
The fixture below was produced from master ``202611131613``
(``labyrinth_quest_data``/``labyrinth_wave_group_data``/``labyrinth_enemy_parameter``,
``quest_type=3`` only). When the game reshuffles the rotation this test fails
instead of silently filtering the wrong boss.

Ordinals above 20 are synthetic: they prove the rotation no longer carries the
old hard-coded 20-round cap.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa.labyrinth.routes import BOSSES, Node, Policy

MASTER_BOSS_QUESTS = (
    (3, 770330101, 301206), (3, 770330102, 301206),
    (3, 770330201, 303306), (3, 770330202, 303306),
    (3, 770330301, 319604), (3, 770330302, 319604),
    (3, 770330401, 312505), (3, 770330402, 312505),
    (3, 770330501, 306604), (3, 770330502, 306604),
    (3, 770330601, 301206), (3, 770330701, 303306),
    (3, 770330801, 319604), (3, 770330901, 312505),
    (3, 770331001, 306604), (3, 770331101, 301206),
    (3, 770331201, 303306), (3, 770331301, 319604),
    (3, 770331401, 312505), (3, 770331501, 306604),
    (3, 770331601, 301206), (3, 770331701, 303306),
    (3, 770331801, 319604), (3, 770331901, 312505),
    (3, 770332001, 306604),
    (5, 770530101, 302501), (5, 770530201, 301701),
    (5, 770530301, 310103), (5, 770530401, 315004),
    (5, 770530501, 319401), (5, 770530601, 302501),
    (5, 770530701, 301701), (5, 770530801, 310103),
    (5, 770530901, 315004), (5, 770531001, 319401),
    (5, 770531101, 302501), (5, 770531201, 301701),
    (5, 770531301, 310103), (5, 770531401, 315004),
    (5, 770531501, 319401), (5, 770531601, 302501),
    (5, 770531701, 301701), (5, 770531801, 310103),
    (5, 770531901, 315004), (5, 770532001, 319401),
)


def boss_node(area, quest_id):
    return Node(1, area, 7, 1, 8, (), quest_id)


def policy_for(area, selected):
    return Policy(**{f'area{area}_bosses': (selected,)})


class BossRotationTests(unittest.TestCase):
    def test_master_rows_map_to_their_real_boss(self):
        for area, quest_id, unit_id in MASTER_BOSS_QUESTS:
            with self.subTest(area=area, quest_id=quest_id):
                node = boss_node(area, quest_id)
                self.assertTrue(policy_for(area, unit_id).boss_matches(area, (node,)))
                other = next(b for b in BOSSES[area] if b != unit_id)
                self.assertFalse(policy_for(area, other).boss_matches(area, (node,)))

    def test_rotation_continues_past_the_old_twenty_cap(self):
        for area, prefix in ((3, 7703), (5, 7705)):
            for ordinal in (21, 25, 35):
                quest_id = prefix * 100000 + 30000 + ordinal * 100 + 1
                expected = BOSSES[area][(ordinal - 1) % len(BOSSES[area])]
                with self.subTest(area=area, ordinal=ordinal):
                    self.assertTrue(policy_for(area, expected).boss_matches(area, (boss_node(area, quest_id),)))

    def test_ex_quest_prefix_is_not_a_boss_quest(self):
        # 7705 2xxxx is the EX battle numbering, not a boss rotation row.
        node = boss_node(5, 770520101)
        self.assertFalse(policy_for(5, BOSSES[5][0]).boss_matches(5, (node,)))

    def test_empty_filter_accepts_any_boss(self):
        self.assertTrue(Policy(area3_bosses=(), area5_bosses=()).boss_matches(3, (boss_node(3, 770330101),)))


if __name__ == '__main__':
    unittest.main()
