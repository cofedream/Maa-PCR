import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcr_maa.agent_entry import parse_selection


ROOT = Path(__file__).resolve().parents[1]
SURVEYS = {'sanctum_survey': 'SanctumSurvey', 'temple_survey': 'TempleSurvey',
           'tower_cloister': 'TowerCloister'}


def test_sweep_modules_are_opt_in_and_keep_drag_order():
    interface = json.loads((ROOT / 'interface.json').read_text(encoding='utf-8'))
    pipeline = json.loads((ROOT / 'assets/resource/pipeline/alpha_entry.json').read_text(encoding='utf-8'))
    option = interface['option']['AlphaModules']
    defaults = interface['task'][0]['pipeline_override']
    cases = {item['name']: item for item in option['cases']}
    assert not set(option['default_case']) & SURVEYS.keys()
    checked = ['tower_cloister', 'sanctum_survey']
    for module, suffix in SURVEYS.items():
        name = 'PCRAlphaSelect' + suffix
        assert pipeline[name]['attach'] == defaults[name]['attach'] == {'selected': False}
        assert cases[module]['pipeline_override'] == {name: {'attach': {'selected': True}}}
        if module in checked:
            pipeline[name].update(cases[module]['pipeline_override'][name])

    def read_node(name):
        node = dict(pipeline[name])
        node['action'] = {'type': node['action'], 'param': {}}
        return node

    raw = json.dumps(dict(selection_mode='pi_nodes_v1', selected_modules=checked))
    assert parse_selection(raw, context=SimpleNamespace(get_node_data=read_node)) == {
        'version': 1, 'selected_modules': checked, 'rewards_final': False}


def test_old_pi_cannot_schedule_a_missing_survey_node():
    def read_node(name):
        if name in {'PCRAlphaSelect' + suffix for suffix in SURVEYS.values()}:
            return None
        return dict(enabled=False, action=dict(type='DoNothing', param={}), next=[], on_error=[],
                    attach=dict(selected=name == 'PCRAlphaSelectHard'))

    ctx = SimpleNamespace(get_node_data=read_node)
    assert parse_selection('{"selection_mode":"pi_nodes_v1"}', context=ctx)['selected_modules'] == ['hard']
    with pytest.raises(ValueError, match='pi_selection_order_mismatch'):
        parse_selection('{"selection_mode":"pi_nodes_v1","selected_modules":["hard","temple_survey"]}', context=ctx)
