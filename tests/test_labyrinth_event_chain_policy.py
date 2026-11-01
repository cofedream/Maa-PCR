"""Configurable chain-event decisions; offline config/decision checks only."""
from pathlib import Path
import json,sys
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from pcr_maa.labyrinth.config import Strategy
from pcr_maa.labyrinth.events import CHAIN_DECISIONS,CHAIN_POLICIES,Choice,Planner
from pcr_maa.labyrinth.strategies import MARKS

CHAINS=((2315,23151,23152),(3315,33151,33152),(2316,23161,23162),(3316,33161,33162))

def button(choice_id,slot,enabled=1.0):return Choice(choice_id,slot,(0,0,20,20),enabled)

def planner(policy,utilities):
    value=Planner(object(),chain_policy=policy)
    value.utilities=lambda ids,*a,**k:{choice_id:utilities[choice_id] for choice_id in ids}
    return value

@pytest.mark.parametrize('event,continue_id,stop_id',CHAINS)
@pytest.mark.parametrize('policy',CHAIN_POLICIES)
def test_chain_policy_selects_the_configured_branch(event,continue_id,stop_id,policy):
    decision=planner(policy,{stop_id:5,continue_id:3}).decide(event,[button(stop_id,0),button(continue_id,1)],(),object(),dict.fromkeys(MARKS,0),trusted=True)
    expected=continue_id if policy=='continue' else stop_id
    assert decision.action_safe is True and decision.choice.choice_id==expected

@pytest.mark.parametrize('event,continue_id,stop_id',CHAINS)
def test_continue_policy_falls_back_when_the_continue_button_was_rejected(event,continue_id,stop_id):
    decision=planner('continue',{stop_id:5,continue_id:3}).decide(event,[button(stop_id,0),button(continue_id,1)],(),object(),dict.fromkeys(MARKS,0),trusted=True,rejected=frozenset((continue_id,)))
    assert decision.action_safe is True and decision.choice.choice_id==stop_id

def test_continue_policy_never_authorizes_a_disabled_continue_button():
    decision=planner('continue',{23151:3,23152:5}).decide(2315,[button(23152,0),button(23151,1,.2)],(),object(),dict.fromkeys(MARKS,0),trusted=True)
    assert decision.action_safe is True and decision.choice.choice_id==23152

def test_single_option_chain_event_has_no_stop_branch():
    decision=planner('stop',{23141:4}).decide(2314,[button(23141,0)],(),object(),dict.fromkeys(MARKS,0),trusted=True)
    assert decision.action_safe is True and decision.choice.choice_id==23141

def test_chain_decisions_only_cover_two_option_chain_tails():
    data=json.loads((ROOT/'assets/labyrinth/event-recognition.json').read_text(encoding='utf-8'))
    known={(int(event['id']),int(choice['id'])):event for event in data['events'] for choice in event['choices']}
    assert set(CHAIN_DECISIONS)=={2315,3315,2316,3316}
    for event,branches in CHAIN_DECISIONS.items():
        assert set(branches)=={'continue','stop'}
        for choice_id in branches['continue']+branches['stop']:
            assert (event,choice_id) in known
        assert len(known[(event,branches['continue'][0])]['choices'])==2

@pytest.mark.parametrize('policy',CHAIN_POLICIES)
def test_strategy_round_trips_the_chain_policy(policy):
    value=Strategy.parse({'event_chain_policy':policy})
    assert value.event_chain_policy==policy and Strategy.parse(value.payload())==value

def test_strategy_defaults_auto_and_rejects_unknown_chain_policy():
    assert Strategy().event_chain_policy==CHAIN_POLICIES[0] and Strategy.parse({}).event_chain_policy==CHAIN_POLICIES[0]
    for value in ('always','AUTO',1,None):
        with pytest.raises(ValueError,match='invalid_event_chain_policy'):Strategy.parse({'event_chain_policy':value})
    with pytest.raises(ValueError,match='invalid_event_chain_policy'):Planner(object(),chain_policy='always')
