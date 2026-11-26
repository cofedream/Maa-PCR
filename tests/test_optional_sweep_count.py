from types import SimpleNamespace

import numpy as np
import pytest

from pcr_maa.optional_sweep_count import adjust_button, button, enabled_button, extreme_button


def detail(text='使用 2 张'):
    box = [955, 465, 93, 32]
    words = [dict(text=text, box=box, score=.98)]
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[460:502, 920:1080] = (250, 150, 80)
    return box, words, SimpleNamespace(image=image)


def draw_sign(image, cx, cy, plus, *, gold=True):
    yy, xx = np.ogrid[:720, :1280]
    radius = (xx - cx) ** 2 + (yy - cy) ** 2
    image[radius <= 30**2] = (140, 193, 231) if gold else (210, 210, 210)
    image[radius <= 26**2] = (249, 249, 249)
    image[cy-2:cy+3, cx-12:cx+13] = (255, 160, 107)
    if plus:
        image[cy-12:cy+13, cx-2:cx+3] = (255, 160, 107)


@pytest.mark.parametrize('increase,x', [(True, 1120), (False, 880)])
def test_one_enabled_sign_on_requested_side(increase, x):
    box, words, argv = detail()
    draw_sign(argv.image, x, 480, increase)
    assert button(words) == (2, box)
    assert adjust_button(None, argv, words, box, increase) == [x-3, 477, 7, 7]
    assert adjust_button(None, argv, words, box, not increase) is None


def test_disabled_ticket_duplicate_or_wrong_shape_refuses():
    box, words, argv = detail()
    draw_sign(argv.image, 1120, 480, True)
    assert button(words * 2) is None
    assert button([dict(words[0], score=.82)]) is None
    assert button([dict(words[0], text='使用0张')]) is None
    argv.image[460:502, 920:1080] = (175, 86, 50)
    assert not enabled_button(argv, box)
    assert adjust_button(None, argv, words, box, True) is None
    box, words, argv = detail()
    draw_sign(argv.image, 1120, 480, False)
    assert adjust_button(None, argv, words, box, True) is None
    draw_sign(argv.image, 1120, 480, True, gold=False)
    assert adjust_button(None, argv, words, box, True) is None


def test_second_valid_sign_in_search_area_rejects_ambiguity():
    box, words, argv = detail()
    draw_sign(argv.image, 1085, 480, True)
    draw_sign(argv.image, 1175, 480, True)
    assert adjust_button(None, argv, words, box, True) is None


def test_min_max_must_be_unique_and_on_the_correct_side_of_the_current_count():
    box, words, _ = detail()
    lo = dict(text='MIN', score=.99, box=[815, 470, 34, 18])
    hi = dict(text='MAX', score=.99, box=[1155, 470, 40, 18])
    assert extreme_button(words + [lo, hi], box, minimum=True) == lo['box']
    assert extreme_button(words + [lo, hi], box, minimum=False) == hi['box']
    assert extreme_button(words + [lo, lo], box, minimum=True) is None
    assert extreme_button(words + [dict(lo, box=hi['box'])], box, minimum=True) is None
    assert extreme_button([dict(words[0], text='使用1张'), lo], box, minimum=True) is None
