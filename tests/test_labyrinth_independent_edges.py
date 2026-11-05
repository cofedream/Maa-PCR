"""Pure offline endpoint regressions; no native resource or device is created."""
import pytest

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


def formation(cards, *, page='battle', filter='ALL', signature='same', members_only=False):
    value = dict(page=page, filter=filter, stable=True, count=0, boss_tab=None,
        selected=[], members=[], signature=signature,
        unknown_visual_fingerprint=signature, cards=cards)
    if members_only:
        value['members_only'] = True
    return value


def row(roles, *, top):
    return [dict(slot_id='available_row_1_' + str(index), character_id=role,
                 trusted=True, selected=False, box=[82 + 141 * (index - 1), top, 130, 130])
            for index, role in enumerate(roles, 1)]


def test_formation_complete_requires_a_short_top_row_list():
    assert cr.formation_complete(formation(row(('1001', '1002', '1003'), top=162)))
    assert cr.formation_complete(formation(row(('1001',), top=249), page='boss'))
    # A scrolled view and a full 8x2 grid can still hide cards above or below.
    assert not cr.formation_complete(formation(row(('1001',), top=231)))
    assert not cr.formation_complete(formation(row(('1001',) * 16, top=162)))
    # Only the battle/boss grid and a complete full read are eligible.
    assert not cr.formation_complete(formation(row(('1001',), top=162), page='roster'))
    assert not cr.formation_complete(formation(row(('1001',), top=162), members_only=True))


def test_short_effective_list_skips_the_formation_sweep(monkeypatch):
    filtered = formation(row(('1001', '1002', '1003'), top=162),
                         filter='EFFECTIVE_EFFECT', signature='filtered')
    restored = formation(row(('1001', '1002'), top=162), filter='ALL', signature='restored')
    calls, events = [], []
    class Navigation(cr.CharacterNavigation):
        def edge(self, *args, **options):
            raise AssertionError('formation sweep must be skipped')
        def move(self, batch, enter, page, current, *, filter=None, direction=None, **options):
            calls.append(filter or direction)
            if filter == 'EFFECTIVE_EFFECT':
                return filtered
            if filter == 'ALL':
                return restored
            raise AssertionError(direction)
    class Batch:
        def observation(self, value, **options):
            events.append(value)
            return value
    class Characters:
        def read(self, *args, **options):
            return formation([], filter='ALL', signature='initial'), {}
    monkeypatch.setattr(cr, 'CharacterNavigation', Navigation)
    ids = scan_effective(Characters(), Batch(), lambda **fields: fields, 1, 20101,
                         page='battle', roster=('1001', '1002'))
    assert calls == ['EFFECTIVE_EFFECT', 'ALL']
    assert ids == ('1001', '1002')
    assert events[-1]['effective_scan_verified'] is True
    assert events[-1]['effective_scan_skipped_unsafe'] is True
    assert events[-1]['filter_restored'] is True


def boss_bottom(partial, *, position=.986, known='1001', last=None):
    cards = row((partial,), top=211) + row((known, last), top=356)
    for card in cards:
        card['trusted'] = card['character_id'] is not None
    value = formation(cards, page='boss', filter='EFFECTIVE_EFFECT', signature=str(cards))
    value['scrollbar'] = dict(visible=True, can_scroll=True, position=position)
    return value


def test_end_scan_consumes_new_trusted_card_on_last_read(monkeypatch):
    from types import SimpleNamespace as NS
    initial = boss_bottom('1003')
    settled = iter((boss_bottom('1004'), boss_bottom('1005', last='1006')))
    calls = []
    def move(self, batch, enter, page, current, *, filter=None, direction=None, **kwargs):
        calls.append(filter or direction)
        return dict(current, filter=filter) if filter else next(settled)
    monkeypatch.setattr(cr.CharacterNavigation, 'edge', lambda self, *args, **kw: initial)
    monkeypatch.setattr(cr.CharacterNavigation, 'move', move)
    observed = scan_effective(NS(read=lambda *a, **k: (initial, {})),
        NS(observation=lambda value, **k: value), lambda **kw: kw, 1, 20101,
        page='boss', roster=('1001','1003','1004','1005','1006'))
    assert observed == ('1001','1003','1004','1005','1006')
    assert calls == ['down', 'down', 'ALL']


def test_directional_edge_requires_fixed_whole_cards_and_matching_context():
    before, after = boss_bottom('1003'), boss_bottom('1004')
    same = cr.CharacterNavigation.same_scroll_view
    assert same(before, after, 'down')
    assert not same(before, after, 'up')
    assert not same(boss_bottom('1003', position=.5), boss_bottom('1004', position=.5), 'down')
    assert not same(before, boss_bottom('1004', known='1009'), 'down')
    assert not same(before, dict(after, filter='ALL'), 'down')
    assert not same(before, dict(after, boss_tab=2), 'down')
    assert not same(boss_bottom('1003', known=None), boss_bottom('1004', known=None), 'down')
    after = boss_bottom('1004')
    after['cards'][1]['box'][1] -= 25
    assert not same(before, after, 'down')


@pytest.mark.parametrize('page', ['opening', 'battle', 'boss', 'roster'])
@pytest.mark.parametrize('direction,position', [('up', .015), ('down', .985)])
def test_scroll_edges_ignore_only_the_clipped_row(page, direction, position):
    def view(partial):
        value = boss_bottom(partial, position=position)
        value['page'] = page
        for card in value['cards']:
            card['box'][1] += cr.VIEWPORTS[page][1] - cr.VIEWPORTS['boss'][1]
        return value
    assert cr.CharacterNavigation.same_scroll_view(view('1004'),view('1005'),direction)


def test_conflicting_whole_card_after_unknown_resets_edge_confirmation(monkeypatch):
    nav = cr.CharacterNavigation(None, None)
    reads = [boss_bottom('1003', last=role) for role in ('1007', '1008', '1008', '1008')]
    calls = []
    def move(*args, **kwargs):
        calls.append(kwargs['direction'])
        return reads[len(calls)-1]
    monkeypatch.setattr(nav, 'move', move)
    assert nav.edge(None, 1, 'boss', boss_bottom('1003'), 'down') == reads[-1]
    assert calls == ['down'] * 4
