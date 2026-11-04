"""Per-event choice overrides; offline config/decision checks only."""
from pathlib import Path
import json,sys
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from pcr_maa.labyrinth.config import Strategy
from pcr_maa.labyrinth.events import Choice,Planner
from pcr_maa.labyrinth.strategies import MARKS

CATALOGUE=json.loads((ROOT/'assets/labyrinth/event-recognition.json').read_text(encoding='utf-8'))
CASES=[(int(event['id']),[int(choice['id']) for choice in event['choices']]) for event in CATALOGUE['events']]

def button(choice_id,slot,enabled=1.0):return Choice(choice_id,slot,(0,0,20,20),enabled)

def planner(overrides,utilities,*,chain_policy='auto'):
    value=Planner(object(),chain_policy=chain_policy,event_choice_overrides=overrides)
    value.utilities=lambda ids,*a,**k:{choice_id:utilities.get(choice_id,0) for choice_id in ids}
    return value

def decide(value,event_id,choice_ids,*,rejected=frozenset()):
    choices=[button(choice_id,index) for index,choice_id in enumerate(choice_ids)]
    return value.decide(event_id,choices,(),object(),dict.fromkeys(MARKS,0),trusted=True,rejected=rejected)

@pytest.mark.parametrize('event_id,choice_ids',CASES)
def test_every_catalogue_choice_can_be_configured(event_id,choice_ids):
    for choice_id in choice_ids:
        # Give the configured choice the lowest model value; the override must still win.
        decision=decide(planner({event_id:choice_id},{cid:(9 if cid!=choice_id else 0) for cid in choice_ids}),event_id,choice_ids)
        assert decision.action_safe is True and decision.choice.choice_id==choice_id

@pytest.mark.parametrize('event_id,choice_ids',CASES)
def test_every_catalogue_choice_falls_back_when_rejected(event_id,choice_ids):
    if len(choice_ids)<2:return
    first,second=choice_ids[:2]
    decision=decide(planner({event_id:first},{first:9,second:1}),event_id,choice_ids,rejected=frozenset((first,)))
    assert decision.action_safe is True and decision.choice.choice_id==second

def test_disabled_configured_choice_falls_back_to_the_value_model():
    value=planner({1105:11051},{11051:9,11052:1})
    choices=[button(11051,0,.2),button(11052,1)]
    decision=value.decide(1105,choices,(),object(),dict.fromkeys(MARKS,0),trusted=True)
    assert decision.action_safe is True and decision.choice.choice_id==11052

def test_per_event_override_wins_over_the_chain_fallback():
    decision=decide(planner({2315:23152},{23152:1,23151:9},chain_policy='continue'),2315,[23152,23151])
    assert decision.action_safe is True and decision.choice.choice_id==23152
    decision=decide(planner({2315:23151},{23152:9,23151:1},chain_policy='stop'),2315,[23152,23151])
    assert decision.action_safe is True and decision.choice.choice_id==23151

def test_disabled_override_falls_back_to_the_chain_policy():
    value=planner({2315:23151},{23152:1,23151:9},chain_policy='stop')
    choices=[button(23152,0),button(23151,1,.2)]
    decision=value.decide(2315,choices,(),object(),dict.fromkeys(MARKS,0),trusted=True)
    assert decision.action_safe is True and decision.choice.choice_id==23152

def test_strategy_round_trips_event_choice_overrides():
    value=Strategy.parse({'event_choice_overrides':{'1105':11052,'2315':23152}})
    assert value.event_choice_overrides=={'1105':11052,'2315':23152}
    assert Strategy.parse(value.payload())==value
    assert Strategy().event_choice_overrides=={} and Strategy.parse({}).event_choice_overrides=={}

@pytest.mark.parametrize('value',[[],{'1105':'11052'},{'110':11052},{'abcd':11052},{1105:11052},{'1105':1},{'1105':100000}])
def test_strategy_rejects_invalid_event_choice_overrides(value):
    with pytest.raises(ValueError,match='invalid_event_choice_overrides'):Strategy.parse({'event_choice_overrides':value})

def test_strategy_rejects_oversized_event_choice_overrides():
    with pytest.raises(ValueError,match='invalid_event_choice_overrides'):
        Strategy.parse({'event_choice_overrides':{str(1000+index):11051 for index in range(101)}})

@pytest.mark.parametrize('value',[5,{'1105':'abc'},[('a','b')]])
def test_planner_rejects_invalid_choice_override_shapes(value):
    with pytest.raises(ValueError,match='invalid_event_choice_overrides'):Planner(object(),event_choice_overrides=value)
