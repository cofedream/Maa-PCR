"""Run118: shared EX registration survives new nodes and repeated observations."""
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from pcr_maa import native
from pcr_maa.replay import SavedImages
from pcr_maa.labyrinth.autoplay import MaaRunActions
from pcr_maa.labyrinth.encounters import MaaEncounter
from pcr_maa.labyrinth.scene import install_scene
from pcr_maa.labyrinth.timing import Timings
from test_labyrinth_character_port import offline

ROOT=Path(__file__).resolve().parents[1]
SCREEN=ROOT/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/68f2362acc2c4431b236902b27b29e62/acceptance-ending/screen.png'


def test_actual118_shared_ex_caller_new_node_retry_and_new_adapter(tmp_path,monkeypatch,offline):
    if not SCREEN.is_file():pytest.skip('private run118 evidence is excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(SCREEN)]])
    resource=native.load_resource();install_scene(resource)
    owner=native.bind(resource,controller)
    observed=[]
    batch=NS(timings=Timings(),checkpoint=lambda:None,
        observation=lambda value,**fields:observed.append(value))
    stamp=lambda **fields:fields
    # Execute the production shared caller without constructing unrelated rewards.
    actions=object.__new__(MaaRunActions)
    actions.tasker,actions.stamp,actions.batch=owner,stamp,batch
    actions.enter_id=118;actions.context_basis={}
    try:
        for block in (30503,40503,40503):
            actions.observe_encounter(NS(kind='ex_challenge'),NS(block_id=block))
            assert actions.encounter.id=='ghost_lord' and actions.encounter_targets==1
        adapter=MaaEncounter(owner,stamp)
        assert adapter.observe_ex(batch,118,50503)['encounter_id']=='ghost_lord'
        assert adapter.callback is resource._labyrinth_encounter_observer
        assert len(observed)==4 and all(v['native']['native_ok'] for v in observed)
        assert controller.clicks==[] and controller.swipes==[]
    finally:assert native.wait_job(owner.post_stop(),seconds=10).succeeded


def test_shared_resource_keeps_callback_only_and_fresh_adapter_binding():
    class Resource:
        def __init__(self):self.names={};self.pipelines=[]
        def register_custom_recognition(self,name,callback):
            assert name not in self.names
            self.names[name]=callback;return True
        def override_pipeline(self,nodes):self.pipelines.append(nodes);return True
    resource=Resource();first_owner=NS(_resource_holder=resource)
    second_owner=NS(_resource_holder=resource)
    first_stamp=lambda **fields:fields
    second_stamp=lambda **fields:dict(fields,second=True)
    first=MaaEncounter(first_owner,first_stamp)
    second=MaaEncounter(second_owner,second_stamp)
    assert first.callback is second.callback and first.namespace==second.namespace
    assert second.tasker is second_owner and second.stamp is second_stamp
    assert len(resource.names)==len(resource.pipelines)==1
    independent=MaaEncounter(NS(_resource_holder=Resource()),first_stamp)
    assert independent.callback is not first.callback and independent.namespace!=first.namespace


def test_noncombat_context_excludes_previous_encounter_and_effective_roster(monkeypatch):
    actions=object.__new__(MaaRunActions)
    actions.stacks={'守备':4};actions.encounter=NS(targets=3)
    actions.encounter_targets=2;actions.effective_ids=('1054','1075')
    actions.batch=NS(settings=NS(strategy=NS(prefer_pure_boss_damage_system=True)),timings=Timings())
    actions.combat_kind='ex_challenge'
    context=actions._context()
    assert context.defense==4 and context.targets==1
    assert context.encounter is None and context.effective_ids==()
    assert not context.boss and not context.prefer_single
    combat=actions._context(NS(block_type=3),combat=True)
    assert combat.encounter is actions.encounter and combat.effective_ids==('1054','1075')
    assert combat.targets==2 and combat.defense==4 and combat.prefer_single and not combat.boss
    boss=actions._context(NS(block_type=8),combat=True)
    assert boss.boss and boss.prefer_single and boss.encounter is actions.encounter
    event=actions._context(NS(block_type=5))
    assert event.targets==2 and event.defense==4 and event.encounter is None and event.effective_ids==()
    from pcr_maa.labyrinth import shop_execution
    captured=[]
    class Event:
        def __init__(self,*args,**kwargs):pass
        def run(self,*args,context,**kwargs):captured.append(context);return {}
    monkeypatch.setattr(shop_execution,'MaaEvent',Event)
    actions.tasker=NS();actions.stamp=lambda **fields:fields
    actions.owned=['1054'];actions.relic_focus=None
    actions.event(NS(enter_id=147),NS(block_id=20201,block_type=5,area=2),NS(kind='event'))
    assert len(captured)==1 and captured[0].targets==2 and captured[0].encounter is None
