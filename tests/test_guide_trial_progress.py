from copy import deepcopy
import json
from types import SimpleNamespace as NS
import pytest

from pcr_maa.guide_trial import execute_trial
from pcr_maa.guide_trial_progress import authorize_progress_observation, stage_order
from pcr_maa.preset_execution import Journal
from test_guide_trial import Port
from test_guide_third_stage import third_plan


def request(journal, plan):
    return authorize_progress_observation(journal, plan,
        text='I completed fire 8-1. Navigate only to the profile and map; do not enter a stage or battle.',
        reported_completed_stage='红焰深域 8-1')


class ObservePort(Port):
    observed = 0
    def observe_progress(self, plan):
        self.observed += 1
        self.checkpoint()
        return dict(source='maa_owner_map_observation', account_id=plan['account_id'], battle_started=False)


def pending(tmp_path):
    journal = Journal(tmp_path)
    p = third_plan()
    execute_trial(Port(('unknown',)), journal, p)
    return journal, p


def test_observation_keeps_pending_bytes_and_never_starts_or_settles(tmp_path):
    journal, p = pending(tmp_path)
    before = journal.path.read_bytes()
    grant = request(journal, p)
    port = ObservePort()
    result = execute_trial(port, journal, p)
    assert result['outcome'] == 'progress_observed'
    assert result['original_result_unknown'] and not result['settings_verified']
    assert not result['consumption_sent'] and not result['settlement_verified']
    assert result['attempts_used'] == 1 and port.observed == 1
    assert port.prepared == port.starts == 0 and not port.returned
    assert journal.path.read_bytes() == before
    assert (tmp_path / f"progress-observation-{grant['intent_id']}-claimed.json").is_file()
    again = ObservePort()
    assert execute_trial(again, journal, p)['error'] == 'guide_trial_previous_result_unknown'
    assert again.observed == again.starts == again.prepared == 0
    with pytest.raises(FileExistsError):
        request(journal, p)


@pytest.mark.parametrize('field,value', [('stage', '红焰深域 7-4'), ('account_id', '987654321'), ('source_hash', 'b'*64)])
def test_observation_request_cannot_switch_binding(tmp_path, field, value):
    journal, p = pending(tmp_path)
    request(journal, p)
    changed = deepcopy(p)
    changed[field] = value
    if field == 'stage':
        from pcr_maa.guide_identity import SOURCE_74_SHA256
        changed['source_image_sha256'] = SOURCE_74_SHA256
    port = ObservePort()
    with pytest.raises(ValueError, match='binding_invalid'):
        execute_trial(port, journal, changed)
    assert port.observed == port.prepared == port.starts == 0
    assert len(journal.events()) == 1


@pytest.mark.parametrize('field,value', [('navigation_only', False), ('source', 'inference'),
                                      ('text', ''), ('reported_completed_stage', '红焰深域 7-4')])
def test_observation_rejects_invalid_confirmation(tmp_path, field, value):
    journal, p = pending(tmp_path)
    grant = request(journal, p)
    path = tmp_path / f"progress-observation-{grant['intent_id']}.json"
    grant['confirmation'][field] = value
    path.write_text(json.dumps(grant), encoding='utf-8')
    port = ObservePort()
    with pytest.raises(ValueError, match='request_invalid'):
        execute_trial(port, journal, p)
    assert port.observed == port.starts == 0


def test_observation_stop_is_consumed_without_changing_attempt(tmp_path):
    journal, p = pending(tmp_path)
    request(journal, p)
    before = journal.path.read_bytes()
    result = execute_trial(ObservePort(stop=True), journal, p)
    assert result['error'] == 'stopped' and result['attempts_used'] == 1
    assert journal.path.read_bytes() == before
    port = ObservePort()
    assert execute_trial(port, journal, p)['error'] == 'guide_trial_previous_result_unknown'
    assert port.observed == 0


def test_map_navigation_reuses_normal_route_and_stops_before_stage(tmp_path, monkeypatch):
    from pcr_maa.guide_trial_native import NativeGuideTrial
    from pcr_maa.labyrinth.profile import PROFILE_NODE
    port = NativeGuideTrial(NS(resource=NS(override_pipeline=lambda _: None)), tmp_path)
    calls = []
    port.configure = lambda p: None
    port.run = lambda name, **kwargs: calls.append(name) or {'account': third_plan()['account_id']}
    scenes = iter(['LabyClientGameHomeObserved', 'LabyClientAdventureObserved',
        'LabyClientMainMenuObserved', PROFILE_NODE, 'LabyClientMainMenuObserved',
        'LabyClientAdventureObserved', 'GTDeepHome', 'GTFireMap'])
    def wait(names, **kwargs):
        scene = next(scenes)
        assert scene in names
        if len(calls) == 0:
            assert not {'GTDefeat', 'GTFormation', 'GTStageDetail', 'LabyClientTitleObserved'} & set(names)
        return scene
    port.wait = wait
    monkeypatch.setattr('pcr_maa.guide_trial_native.profile_player_id', lambda r: r['account'])
    port.prepare_map(third_plan(), observation_only=True)
    assert port.account == third_plan()['account_id']
    assert calls == ['LabyClientHomeToAdventure', 'LabyClientOpenMainMenuOnce', 'LabyClientOpenProfileOnce',
        PROFILE_NODE, 'LabyClientProfileBackOnce', 'LabyClientMenuToAdventureOnce', 'GTOpenDeep', 'GTOpenFire']


@pytest.mark.parametrize('node', ['GTOpenStage', 'GTChallenge', 'GTBattleStart', 'GTBattleAllSet', 'LabyClientTitleStartOnce'])
def test_progress_native_guard_rejects_stage_battle_and_login(node, tmp_path):
    from pcr_maa.guide_trial_native import NativeGuideTrial
    port = NativeGuideTrial(None, tmp_path)
    port.checkpoint = lambda: None
    port.progress_observation_only = True
    with pytest.raises(RuntimeError, match='scope_violation'):
        port.run(node, click=True)


def test_stage_order_is_not_lexical():
    assert stage_order('红焰深域 8-1') > stage_order('红焰深域 7-10')
    with pytest.raises(ValueError):
        stage_order('苍波深域 8-1')
