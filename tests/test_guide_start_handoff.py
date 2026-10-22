import json
from types import SimpleNamespace as NS
import pytest
from pcr_maa.guide_trial_characters import GuideTrialCharacters
from pcr_maa.guide_trial import execute_trial
from pcr_maa.preset_execution import Journal
from test_guide_opening import candidate, SimulatedMaa


@pytest.mark.parametrize('mode', ['read', 'start'])
@pytest.mark.parametrize('visible', [True, False])
def test_current_frame_stars_without_unrelated_ocr(monkeypatch, mode, visible):
    plan = candidate()
    reader = GuideTrialCharacters.__new__(GuideTrialCharacters)
    reader.plan, reader.team = plan, plan['unit_ids']
    reader.source_ids = set(reader.team)
    ids = list(reversed(reader.team))
    reader.read = lambda image: [dict(unit_id=uid) for uid in ids]
    monkeypatch.setattr('pcr_maa.guide_trial_characters.member_slots', lambda image: [[0,0,1,1]]*5)
    calls = []
    def recognize(name, image):
        calls.append(name)
        hit = True
        if name.startswith('GTStar'):
            slot, star = map(int, name[6:].split('_'))
            hit = visible and dict(zip(plan['unit_ids'], plan['stars']))[ids[slot-1]] == star
        return NS(hit=hit, box=[1000,550,180,70])
    context = NS(tasker=NS(stopping=False), run_recognition=recognize)
    result = reader.analyze(context, NS(image=None, custom_recognition_param=json.dumps(dict(mode=mode))))
    assert len([n for n in calls if n.startswith('GTStar')]) == 10
    assert not any(n.startswith('GTStar') and n[-1] in '1234' for n in calls)
    if mode == 'start' and not visible:
        assert result is None
    else:
        assert result.detail['stars'] == (list(reversed(plan['stars'])) if visible else [None]*5)


@pytest.mark.parametrize('order', [(0,1,2,3,4), (4,1,3,0,2)])
def test_existing_target_team_reaches_result_without_reselection(tmp_path, order):
    class Port(SimulatedMaa):
        def prepare(self, plan):
            super().prepare(plan)
            self.form_team()
        def read_team(self):
            return dict(super().read_team(), member_count=5)
    port = Port(tmp_path, order=order, auto=True, initial=[True]*5)
    report = execute_trial(port, Journal(tmp_path/'ledger'), candidate())
    assert report['outcome'] == 'victory' and port.returned
    assert [name for name,click in port.calls if click] == ['GTBattleStart', 'GTBattleToggleSet']
    assert report['attempts_used'] == 1


@pytest.mark.parametrize('count',[None,0,4,6])
def test_target_portraits_with_conflicting_slot_count_do_not_clear(tmp_path,count):
    class Port(SimulatedMaa):
        def prepare(self,plan):
            super().prepare(plan)
            self.form_team()
        def read_team(self):
            return dict(super().read_team(),member_count=count)
    port=Port(tmp_path);journal=Journal(tmp_path/'ledger')
    report=execute_trial(port,journal,candidate())
    assert report['error']=='guide_trial_member_count_unknown'
    assert port.calls==[] and journal.events()==[] and report['attempts_used']==0


@pytest.mark.parametrize('wanted', range(1,7))
@pytest.mark.parametrize('actual', [None,3,5,6,'ambiguous'])
def test_start_requires_current_correct_unambiguous_stars(monkeypatch,wanted,actual):
    plan=candidate();plan['stars']=[wanted]*5
    reader=GuideTrialCharacters.__new__(GuideTrialCharacters)
    reader.plan=plan;reader.team=plan['unit_ids'];reader.source_ids=set(reader.team)
    reader.read=lambda image:[dict(unit_id=uid) for uid in reader.team]
    monkeypatch.setattr('pcr_maa.guide_trial_characters.member_slots',lambda image:[[0,0,1,1]]*5)
    def recognize(name,image):
        hit=True if not name.startswith('GTStar') else (int(name[-1]) in (5,6) if actual=='ambiguous' else int(name[-1])==actual)
        return NS(hit=hit,box=[1000,550,180,70])
    context=NS(tasker=NS(stopping=False),run_recognition=recognize)
    result=reader.analyze(context,NS(image=None,custom_recognition_param='{"mode":"start"}'))
    assert (result is not None) is (wanted==actual)
