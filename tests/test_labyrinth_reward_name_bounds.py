import json
from pathlib import Path

import pytest

from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.rewards import role_offer_from_words
from pcr_maa.labyrinth.roles import load_optimizer


@pytest.mark.parametrize(('slot', 'name_x'), [(0, 154), (1, 523), (2, 893)])
def test_long_reward_name_fits_its_own_card(slot, name_x):
    path = Path(__file__).resolve().parents[1] / 'assets/resource/pipeline/labyrinth.json'
    node = json.loads(path.read_bytes())[f'LabyRoleRewardName{slot}']
    left, top, width, height = node['roi']
    word = dict(box=[name_x, 463, 231, 27], text='未奏希&美美&镜华', score=.985)
    assert left <= name_x and name_x + 231 <= left + width
    assert top <= 463 and 490 <= top + height
    optimizer = load_optimizer()
    assert role_offer_from_words(slot, [word], optimizer).character_id == '1808'
    for x in (left - 1, left + width - 230):
        with pytest.raises(BatchStopped, match='role_reward_names_unknown'):
            role_offer_from_words(slot, [dict(word, box=[x, 463, 231, 27])], optimizer)
