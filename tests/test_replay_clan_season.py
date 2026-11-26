import pytest
from types import SimpleNamespace

from scripts.replay_clan_season import summarize
from pcr_maa import clan_battle_native as battle
from pcr_maa.clan_battle_calibration import load_calibration
from test_clan_battle_native import calibration, plan
from test_clan_opening import sampled_plan


def test_formal_owner_and_replay_can_call_the_same_frame_field_methods():
    from pcr_maa.clan_battle_daily_native import ClanBattleDailyPage
    box = [0, 0, 10, 10]
    word = {'text': '42', 'score': .99, 'box': box}
    seen = SimpleNamespace(hit=True, raw_detail={'best': word, 'filtered': [word]})
    context = SimpleNamespace(run_recognition=lambda node, image: seen,
                              get_node_data=lambda node: {'expected': '^42$', 'roi': box})
    for reader in (battle.ClanPageFields(), battle.ClanBattlePage({}, {}), ClanBattleDailyPage({}, {})):
        assert reader.hit(context, None, 'number')
        assert reader.word(context, None, 'number') == '42'


@pytest.mark.parametrize(('observations', 'status'), [
    ([], 'missing'),
    ([(True, True)], 'positive_only'),
    ([(False, False)], 'unverified'),
    ([(True, True), (False, False)], 'matched'),
    ([(True, True), (False, True)], 'changed'),
    ([(True, False), (False, False)], 'changed'),
    ([(True, True), (False, None)], 'unverified'),
    ([(True, None), (False, False)], 'unverified'),
])
def test_page_replay_status_requires_observed_positive_and_negative(observations, status):
    cases = [dict(page='target', expect=expected, matched=actual)
             for expected, actual in observations]
    assert summarize(cases, ['target', 'formation']) == {'target': status, 'formation': 'missing'}


def test_rejected_action_is_not_a_negative_page_sample():
    cases = [
        dict(page='formation', expect=True, matched=True),
        dict(page='formation', action='start', expect=False, matched=False),
    ]
    assert summarize(cases, ['formation']) == {'formation': 'positive_only'}


@pytest.mark.parametrize('legacy', [True, False], ids=['legacy-v1', 'october-v2'])
def test_offline_page_and_start_guards_match_nodes_installed_by_owner(legacy, calibration, plan):
    if legacy:
        profile = calibration
    else:
        plan = sampled_plan()
        profile = load_calibration(plan)
    live_nodes = battle.calibration_pipeline(profile, plan)
    for page in profile['pages']:
        replay_nodes = battle.page_recognition_nodes(profile, plan, page)
        assert replay_nodes == {key: live_nodes[key] for key in replay_nodes}
    start = battle.action_label_node('start', profile['actions']['start'], plan)
    assert start == live_nodes['CBActionLabel_start']
    assert start['action'] == 'DoNothing'
    assert start['expected'] == '^(?:模拟战开始|开始模拟战|战斗开始)$'
