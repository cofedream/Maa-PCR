import gc
from types import SimpleNamespace
import weakref
import pytest
from pcr_maa.guide_trial_native import NativeGuideTrial


class Registry:
    def __init__(self):
        self.callbacks = {}
        self.nodes = {}

    def override_pipeline(self, nodes):
        self.nodes.update(nodes)
        return True

    def register_custom_recognition(self, name, callback):
        if name in self.callbacks:
            return False
        self.callbacks[name] = callback
        return True


@pytest.fixture
def configure_plan(monkeypatch):
    from pcr_maa import guide_identity, guide_trial_characters
    class Callback:
        pass
    monkeypatch.setattr(guide_identity, 'require_source_team', lambda _: {})
    monkeypatch.setattr(guide_trial_characters, 'GuideTrialCharacters', lambda _, **kwargs: Callback())
    return dict(stage='红焰深域 7-3', account_id='123456789')


def test_registered_callback_lives_with_owning_resource(configure_plan, tmp_path):
    owned, view = Registry(), Registry()
    tasker = SimpleNamespace(_resource_holder=owned, resource=view)
    port = NativeGuideTrial(tasker, tmp_path)
    port.configure(configure_plan)
    assert view.nodes['GTBattleStart']['next']==['GTBattleReadSettings']
    reference = weakref.ref(port.characters)
    assert owned.callbacks['GuideTrialCharacters'] is reference()
    assert not view.callbacks
    del port
    gc.collect()
    assert reference() is not None


def test_duplicate_registration_stops_without_replacing_old_callback(configure_plan, tmp_path):
    owned = Registry()
    tasker = SimpleNamespace(_resource_holder=owned, resource=Registry())
    first = NativeGuideTrial(tasker, tmp_path)
    first.configure(configure_plan)
    reference = weakref.ref(first.characters)
    del first
    gc.collect()
    with pytest.raises(RuntimeError, match='callback_registration_failed'):
        NativeGuideTrial(tasker, tmp_path).configure(configure_plan)
    assert owned.callbacks['GuideTrialCharacters'] is reference()


def test_borrowed_owner_still_registers_in_its_scope(configure_plan, tmp_path):
    registry = Registry()
    port = NativeGuideTrial(SimpleNamespace(resource=registry), tmp_path)
    port.configure(configure_plan)
    assert registry.callbacks['GuideTrialCharacters'] is port.characters
