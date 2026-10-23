"""The retired single-guide trial cannot be selected by current or saved tasks."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcr_maa import gui_contract, supported_daily
from pcr_maa.agent_entry import parse_selection


def test_published_task_options_exclude_guide_trial():
    interface = json.loads((Path(__file__).resolve().parents[1] / 'interface.json').read_text(encoding='utf-8'))
    assert 'guide_trial' not in [case['name'] for case in interface['option']['AlphaModules']['cases']]


@pytest.mark.parametrize('modules,final', [(['guide_trial'], False), (['guide_trial', 'rewards'], False),
    (['guide_trial'], True)])
def test_saved_trial_selection_is_rejected(modules, final):
    with pytest.raises(ValueError, match='^guide_trial_removed$'):
        gui_contract.make_combination(modules, rewards_final=final)


@pytest.mark.parametrize('encoding', ['selection', 'flags', 'pi', 'pi_ordered'])
def test_all_agent_selection_encodings_reject_retired_trial(encoding):
    value = dict(version=1, selected_modules=['guide_trial'], rewards_final=False)
    if encoding == 'flags':
        value = dict(version=1, module_flags={name: name == 'guide_trial' for name in supported_daily.MODULE_ORDER},
                     rewards_final=False)
    if encoding.startswith('pi'):
        value = dict(selection_mode='pi_nodes_v1')
        if encoding == 'pi_ordered':
            value['selected_modules'] = ['guide_trial']

    def node(name):
        return dict(enabled=False, action=dict(type='DoNothing', param={}), next=[], on_error=[],
                    attach=dict(selected=name == 'PCRAlphaSelectGuideTrial'))

    with pytest.raises(ValueError, match='^guide_trial_removed$'):
        parse_selection(json.dumps(value), context=SimpleNamespace(get_node_data=node))
