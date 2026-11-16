"""Opening-page roster scoping: joint assignment, config field and navigation values.

These are public, offline tests: no private frames, no device input.  The
synthetic frames paste original icon-pack artwork into the measured opening card
grid, exactly like the existing matcher tests paste icons at 720p.
"""
import itertools
import json
from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import numpy as np
import pytest

from pcr_maa.labyrinth import character_recognition as m
from pcr_maa.labyrinth.config import Strategy, Settings, Goal
from pcr_maa.labyrinth.opening import _guild_units

ROOT = Path(__file__).resolve().parents[1]
GUILD5 = ('1049', '1091', '1171', '1200', '1212', '1337', '1011', '1129', '1193')
BOXES = tuple((82 + 141 * column, 188, 130, 130) for column in range(3))


def _brute_max(weights):
    rows = len(weights)
    cols = max((len(row) for row in weights), default=0)
    if not rows or not cols:
        return 0.0
    size = max(rows, cols)
    best = 0.0
    for perm in itertools.permutations(range(size)):
        total = 0.0
        for row in range(rows):
            column = perm[row]
            if column < cols and column < len(weights[row]):
                total += weights[row][column]
        best = max(best, total)
    return best


def test_maximum_assignment_matches_brute_force_on_rectangular_matrices():
    rng = np.random.default_rng(116)
    for rows, cols in ((1, 1), (2, 3), (3, 2), (3, 3), (4, 2), (2, 4)):
        for _ in range(6):
            weights = np.round(rng.random((rows, cols)), 3).tolist()
            assignment = m._maximum_assignment(weights)
            assert len(assignment) == rows
            used = [column for column in assignment if column is not None]
            assert len(used) == len(set(used))
            total = sum(weights[row][column] for row, column in enumerate(assignment)
                        if column is not None and column < cols)
            assert abs(total - _brute_max(weights)) < 1e-9


def _icon_index(character_id, variant):
    entries, images, _ = m.load_character_pack()
    index = next(i for i, entry in enumerate(entries)
                 if entry['character_id'] == character_id and entry['variant'] == variant)
    return images[index]


def _frame_with_icons(cards):
    frame = np.full((720, 1280, 3), 240, np.uint8)
    for box, (character_id, variant) in zip(BOXES, cards):
        x, y, w, h = box
        artwork = cv2.resize(_icon_index(character_id, variant)[..., ::-1], (w - 14, h - 14))
        frame[y + 6:y + 6 + w - 14, x + 6:x + 6 + w - 14] = artwork
    return frame


def test_rank_returns_best_variant_for_every_requested_id():
    frame = _frame_with_icons((('1049', '61'), ('1091', '31'), ('1171', '31')))
    matcher = m.IconMatcher()
    x, y, w, h = BOXES[0]
    ranks = matcher.rank(frame, (x + 6, y + 6, w - 14, h - 14),
                         ids=['1049', '1091', '1171', '1212'])
    assert set(ranks) == {'1049', '1091', '1171', '1212'}
    assert max(ranks.values(), key=lambda candidate: candidate.confidence).character_id == '1049'
    assert ranks['1049'].variant == '61'


def test_recognize_expected_roster_assigns_one_identity_per_card(monkeypatch):
    frame = _frame_with_icons((('1049', '61'), ('1091', '31'), ('1171', '31')))
    monkeypatch.setattr(m, 'detect_grid', lambda *args, **kwargs: BOXES)
    observation = m.CharacterRecognizer().recognize(
        frame, 'opening', expected_roster=['1049', '1091', '1171'],
        observed_count=0)
    cards = sorted(observation['cards'], key=lambda card: (card['box'][1], card['box'][0]))
    assert [card['character_id'] for card in cards] == ['1049', '1091', '1171']
    assert all(card['trusted'] for card in cards)
    assert observation['selected'] == [] and observation['filter'] is None


def test_recognize_expected_roster_rejects_invalid_lists(monkeypatch):
    frame = _frame_with_icons((('1049', '61'),))
    monkeypatch.setattr(m, 'detect_grid', lambda *args, **kwargs: BOXES[:1])
    recognizer = m.CharacterRecognizer()
    for value in (['1049', '1049'], ['105'], ['1049', 1075], [], '1049'):
        with pytest.raises(m.BatchStopped, match='^character_expected_roster_unknown$'):
            recognizer.recognize(frame, 'opening', expected_roster=value, observed_count=0)
    with pytest.raises(m.BatchStopped, match='^character_expected_roster_unknown$'):
        recognizer.recognize(frame, 'battle', expected_roster=['1049'])


def test_recognize_unknown_card_never_acquires_a_roster_identity(monkeypatch):
    frame = _frame_with_icons((('1064', '61'), ('1091', '31'), ('1171', '31')))
    monkeypatch.setattr(m, 'detect_grid', lambda *args, **kwargs: BOXES)
    observation = m.CharacterRecognizer().recognize(
        frame, 'opening', expected_roster=['1049', '1091', '1171'], observed_count=0)
    cards = sorted(observation['cards'], key=lambda card: (card['box'][1], card['box'][0]))
    assert cards[0]['trusted'] is False and cards[0]['character_id'] is None
    assert [card['character_id'] for card in cards[1:]] == ['1091', '1171']
    # 1064 is outside the roster; it must not steal a roster identity.
    assert '1064' not in {card['character_id'] for card in cards}


def test_assignment_resolves_duplicate_best_candidates_to_distinct_ids(monkeypatch):
    def candidate(character_id, score):
        return m.IconCandidate(character_id, character_id, '31', score,
                               character_id, character_id + '31')

    ranks = {
        BOXES[0]: {'1049': candidate('1049', .90), '1091': candidate('1091', .60)},
        BOXES[1]: {'1049': candidate('1049', .85), '1091': candidate('1091', .80)},
    }
    class Matcher:
        __slots__ = ()
        names = {'1049': None, '1091': None}
        def rank(self, frame, box, *, ids, mask, attribute, _prepared):
            return ranks[(box[0] - 6, box[1] - 6, 130, 130)]
    frame = np.full((720, 1280, 3), 240, np.uint8)
    monkeypatch.setattr(m, 'detect_grid', lambda *args, **kwargs: BOXES[:2])
    monkeypatch.setattr(m, 'card_presence', lambda *args, **kwargs: 1.)
    observation = m.CharacterRecognizer(matcher=Matcher()).recognize(
        frame, 'opening', expected_roster=['1049', '1091'], observed_count=0)
    cards = sorted(observation['cards'], key=lambda card: (card['box'][1], card['box'][0]))
    # Independent argmax would give both cards 1049; the assignment keeps them distinct.
    assert [card['character_id'] for card in cards] == ['1049', '1091']
    assert all(card['trusted'] for card in cards)
    # The second card failed the local margin (1049 .85 > 1091 .80) because 1049 is
    # already claimed by the trusted first card; elimination confirms the remaining id.
    assert cards[1]['elimination'] is True
    assert cards[1]['local_rival_margin'] < m.RIVAL_MARGIN
    assert cards[1]['rival_margin'] >= m.RIVAL_MARGIN


def test_elimination_requires_confidence_floor(monkeypatch):
    def candidate(character_id, score):
        return m.IconCandidate(character_id, character_id, '31', score,
                               character_id, character_id + '31')

    ranks = {
        BOXES[0]: {'1049': candidate('1049', .90), '1091': candidate('1091', .10)},
        BOXES[1]: {'1049': candidate('1049', .60), '1091': candidate('1091', .30)},
    }
    class Matcher:
        __slots__ = ()
        names = {'1049': None, '1091': None}
        def rank(self, frame, box, *, ids, mask, attribute, _prepared):
            return ranks[(box[0] - 6, box[1] - 6, 130, 130)]
    frame = np.full((720, 1280, 3), 240, np.uint8)
    monkeypatch.setattr(m, 'detect_grid', lambda *args, **kwargs: BOXES[:2])
    monkeypatch.setattr(m, 'card_presence', lambda *args, **kwargs: 1.)
    observation = m.CharacterRecognizer(matcher=Matcher()).recognize(
        frame, 'opening', expected_roster=['1049', '1091'], observed_count=0)
    cards = sorted(observation['cards'], key=lambda card: (card['box'][1], card['box'][0]))
    # 1049 is claimed, but the remaining id is below CONFIDENCE; elimination must not
    # turn a weak card into a roster identity.
    assert [card['character_id'] for card in cards] == ['1049', None]
    assert cards[1]['trusted'] is False and 'elimination' not in cards[1]


def test_elimination_does_not_hide_mutual_tie(monkeypatch):
    def candidate(character_id, score):
        return m.IconCandidate(character_id, character_id, '31', score,
                               character_id, character_id + '31')

    ranks = {
        BOXES[0]: {'1049': candidate('1049', .60), '1091': candidate('1091', .59)},
        BOXES[1]: {'1049': candidate('1049', .59), '1091': candidate('1091', .58)},
    }
    class Matcher:
        __slots__ = ()
        names = {'1049': None, '1091': None}
        def rank(self, frame, box, *, ids, mask, attribute, _prepared):
            return ranks[(box[0] - 6, box[1] - 6, 130, 130)]
    frame = np.full((720, 1280, 3), 240, np.uint8)
    monkeypatch.setattr(m, 'detect_grid', lambda *args, **kwargs: BOXES[:2])
    monkeypatch.setattr(m, 'card_presence', lambda *args, **kwargs: 1.)
    observation = m.CharacterRecognizer(matcher=Matcher()).recognize(
        frame, 'opening', expected_roster=['1049', '1091'], observed_count=0)
    # No card passes the local gate, so no id is claimed and elimination cannot run.
    assert all(card['trusted'] is False and card['character_id'] is None
               for card in observation['cards'])


def test_strategy_guild_units_round_trip_and_validation():
    strategy = Strategy.parse({'guild_units': {'5': ['1049', '1091', '1171']}})
    assert strategy.guild_units['5'] == ('1049', '1091', '1171')
    assert strategy.payload()['guild_units'] == {'5': ['1049', '1091', '1171']}
    settings = Settings.parse({'version': 1, 'goals': [{'guild_id': 5, 'clears': 1}],
                               'strategy': {'guild_units': {'5': ['1049']}}})
    assert settings.strategy.guild_units['5'] == ('1049',)
    payload = settings.payload()
    assert Settings.parse(json.loads(json.dumps(payload))).strategy.guild_units['5'] == ('1049',)
    legacy = Settings.parse({'version': 1, 'goals': [{'guild_id': 5, 'clears': 1}]})
    assert dict(legacy.strategy.guild_units) == {}
    for value in ({'11': ['1049']}, {'5': []}, {'5': ['1049', '1049']},
                  {'5': [1049]}, {'5': ['10']}, {'5': 'x'}):
        with pytest.raises(ValueError, match='^invalid_guild_units$'):
            Strategy.parse({'guild_units': value})


def test_opening_guild_units_helper_is_optional_and_type_safe():
    units = ('1049', '1091')
    assert _guild_units(NS(strategy=NS(guild_units={'5': units})), 5) == units
    assert _guild_units(NS(strategy=NS(guild_units={'5': list(units)})), 5) == units
    assert _guild_units(NS(strategy=None), 5) is None
    assert _guild_units(NS(), 5) is None
    assert _guild_units(NS(strategy=NS(guild_units={})), 5) is None
    assert _guild_units(NS(strategy=NS(guild_units={'4': units})), 5) is None
    assert _guild_units(NS(strategy=NS(guild_units={'5': []})), 5) is None
    assert _guild_units(NS(strategy=NS(guild_units={'5': ('1049',)})), '5') is None


def test_opening_guild_units_survives_real_settings_parsing():
    # Settings.parse freezes the guild map (MappingProxyType), so the helper must
    # accept any Mapping: an exact-dict check returned None for every real GUI
    # configuration and silently dropped expected_roster from the whole
    # invitation path (2026-11-04 review).
    settings = Settings.parse({'version': 1, 'goals': [{'guild_id': 5, 'clears': 1}],
                               'strategy': {'guild_units': {'5': ['1049', '1091', '1171']}}})
    assert type(settings.strategy.guild_units) is not dict
    assert _guild_units(settings, 5) == ('1049', '1091', '1171')
    assert _guild_units(settings, 4) is None
    assert _guild_units(Settings.parse({'version': 1, 'goals': [{'guild_id': 5, 'clears': 1}]}), 5) is None
