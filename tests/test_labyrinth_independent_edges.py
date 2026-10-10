"""Pure offline endpoint regressions; no native resource or device is created."""
from pcr_maa.labyrinth import character_recognition as cr
from pcr_maa.labyrinth.encounters import scan_effective


def view(position, *, signature, filter="ALL", role=None):
    return dict(page="battle", filter=filter, stable=True, count=0, boss_tab=None,
        selected=[], members=[], signature=signature,
        unknown_visual_fingerprint=signature,
        cards=[dict(slot_id="available_row_1_1", character_id=role,
                    trusted=role is not None, selected=False)],
        scrollbar=dict(visible=True, can_scroll=True, position=position))


def test_same_signature_and_stationary_unknown_animation_still_end_edge(monkeypatch):
    nav=cr.CharacterNavigation(None, None)
    for signatures in (("same", "same"), ("animation-a", "animation-b")):
        calls=[]
        source=view(.5, signature=signatures[0])
        def move(*args, **options):
            calls.append(options)
            return view(.5, signature=signatures[min(len(calls), 1)])
        monkeypatch.setattr(nav, "move", move)
        assert nav.edge(None, 1, "battle", source, "up")["scrollbar"]["position"] == .5
        assert len(calls) == 2
    assert not nav.unchanged(view(.5, signature="a", role="1001"),
                             view(.5, signature="b", role="1002"))


def test_unknown_edge_requires_two_probes_without_cumulative_scroll_drift(monkeypatch):
    nav=cr.CharacterNavigation(None, None)
    positions=iter((.485, .470, .455, .440, .440, .440))
    calls=[]
    def move(*args, **options):
        calls.append(options)
        return view(next(positions), signature="different-portrait-" + str(len(calls)))
    monkeypatch.setattr(nav, "move", move)
    actual=nav.edge(None, 1, "battle", view(.5, signature="initial"), "up")
    assert len(calls) == 6
    assert actual["scrollbar"]["position"] == .440


def test_seek_does_not_call_slowly_moving_unknown_candidates_not_found(monkeypatch):
    nav=cr.CharacterNavigation(None, None)
    down_positions=iter((.015, .030, .045))
    calls=[]
    def move(batch, enter, page, current, *, direction=None, **options):
        calls.append(direction)
        if direction == "up":
            return view(0, signature="top")
        position=next(down_positions)
        return view(position, signature="different-portrait-" + str(len(calls)),
                    role="1001" if position >= .04 else None)
    monkeypatch.setattr(nav, "move", move)
    actual, found=nav.seek(None, 1, "battle", view(0, signature="top"), ("1001",))
    assert [card["character_id"] for card in found] == ["1001"]
    assert actual["scrollbar"]["position"] == .045
    assert calls == ["up", "up", "down", "down", "down"]


def test_effective_scan_does_not_certify_incomplete_empty_ids(monkeypatch):
    calls, events=[], []
    down_positions=iter((.015, .030, .045, .045, .045))
    class Navigation(cr.CharacterNavigation):
        def edge(self, batch, enter, page, current, direction, **options):
            return view(0, signature="top", filter="EFFECTIVE_EFFECT")
        def move(self, batch, enter, page, current, *, filter=None, direction=None, **options):
            calls.append(filter or direction)
            if filter is not None:
                return view(0, signature="restored-" + filter, filter=filter)
            position=next(down_positions)
            return view(position, signature="different-portrait-" + str(len(calls)),
                        filter="EFFECTIVE_EFFECT", role="1001" if position >= .04 else None)
    class Batch:
        def observation(self, value, **options):
            events.append(value)
            return value
    class Characters:
        def read(self, *args, **options):
            return view(0, signature="initial"), {}
    monkeypatch.setattr(cr, "CharacterNavigation", Navigation)
    ids=scan_effective(Characters(), Batch(), lambda **fields:fields, 1, 20101,
                       page="battle", roster=("1001",))
    assert ids == ("1001",)
    assert calls == ["EFFECTIVE_EFFECT", "down", "down", "down", "down", "down", "ALL"]
    assert events[-1]["effective_scan_verified"] is True


def test_free_invitation_does_not_abort_on_one_small_scroll(monkeypatch):
    from types import SimpleNamespace as NS
    from test_labyrinth_event_free_roles import Characters, harness
    from pcr_maa.labyrinth.rewards import MaaRoleReward
    batch, stamp = harness()
    def opening(position):
        result = view(position, signature=str(position), role='1' if position >= .04 else None)
        result.update(page='opening', required_count=1)
        result['cards'][0]['box'] = [50, 160, 100, 100]
        return result
    class SlowCharacters(Characters):
        def read(self, batch, page, **options):
            if page == 'opening' and not self.selected:
                return opening(0), dict(native_ok=True)
            return super().read(batch, page, **options)
    characters = SlowCharacters(1)
    positions = iter((.015, .030, .045))
    calls = []
    def move(*args, **options):
        calls.append(options['direction'])
        return opening(next(positions))
    monkeypatch.setattr(cr.CharacterNavigation, 'move', move)
    result = MaaRoleReward(NS(), stamp, characters=characters,
        optimizer=NS(profiles={'1': NS(rating=2)})).choose_free(batch, 1, 'slow-free', ())
    assert result['roles_joined'] == ['1'] and calls == ['down'] * 3
    assert [call[0] for call in characters.calls] == ['ToggleOnce', 'InviteOnce']


def test_roster_scan_keeps_later_trusted_cards_during_slow_scroll(monkeypatch):
    from pcr_maa.labyrinth.roster import MaaRoster
    events = []
    source = view(0, signature='top', role='1001')
    positions = iter((.015, .030, .045, .045, .045))
    class Navigation(cr.CharacterNavigation):
        def edge(self, *args, **options):
            return source
        def move(self, *args, **options):
            position = next(positions)
            return view(position, signature=str(position), role='1002' if position >= .04 else '1001')
    class Characters:
        def read(self, *args, **options):
            return source, dict(native_ok=True)
    class Batch:
        def observation(self, value, **options):
            events.append(value)
    monkeypatch.setattr(cr, 'CharacterNavigation', Navigation)
    result = MaaRoster(None, lambda **fields: fields, characters=Characters()).scan(Batch(), 1, page='battle')
    assert result['roles'] == ['1001', '1002'] and result['complete_roster_verified']
