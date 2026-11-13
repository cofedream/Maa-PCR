import json
from types import SimpleNamespace as NS

import numpy as np
import pytest
import cv2

from pcr_maa import native
from pcr_maa.labyrinth import autoplay, character_recognition, shop_execution
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.timing import Timings


def test_member_boxes_and_empty_team():
    frame = np.full((720, 1280, 3), 245, dtype=np.uint8)
    assert character_recognition.member_slots(frame) == ()
    for x in (68, 214):
        frame[545:674, x:x + 129] = [47, 82, 126]
    boxes = character_recognition.member_slots(frame)
    assert len(boxes) == 2
    for (x, y, w, h), left in zip(boxes, (68, 214)):
        assert abs(x-left) <= 3 and abs(y-545) <= 4 and w == h and abs(w-129) <= 4


def test_blurred_princess_yui_portrait_is_unknown():
    entries, images, _ = character_recognition.load_character_pack()
    index = next(i for i, row in enumerate(entries) if row['unit_icon_id'] == '180231')
    tile = cv2.resize(images[index][..., ::-1], (2, 2), interpolation=cv2.INTER_AREA)
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[206:322, 206:322] = cv2.resize(tile, (116, 116))
    match = character_recognition.IconMatcher().match(frame, (206, 206, 116, 116))
    assert not match.trusted and match.character_id is None


def test_relic_search_reuses_geometry_but_scores_fresh_pixels():
    from pcr_maa.labyrinth import relic_recognition
    catalog = relic_recognition.load_catalog()
    frame = np.zeros((720, 1280, 3), np.uint8)
    box = [100, 200, 128, 128]
    for index in (0, 1, 0):
        frame[200:328,100:228] = catalog._images[index][:,:,::-1]
        match = catalog.match(frame, box)
        assert match.trusted and match.facts.id == catalog.facts[index].id


@pytest.mark.parametrize('deadline,stop,expected', [(.17, False, .17), (1, False, .5), (1, True, .1)])
def test_polling_clock_keeps_stop_and_caller_deadline(monkeypatch, deadline, stop, expected):
    now = [0.]
    monkeypatch.setattr(native, 'active_now', lambda: now[0])
    monkeypatch.setattr(autoplay.time, 'sleep', lambda delta: now.__setitem__(0, now[0] + delta))
    def checkpoint():
        if stop and now[0] >= .1:
            raise BatchStopped('stopped')
    batch = NS(timings=Timings(), checkpoint=checkpoint)
    token = native.RUN_DEADLINE.set(deadline)
    try:
        if stop:
            with pytest.raises(BatchStopped, match='stopped'):
                autoplay._poll_pause(NS(stopping=False), batch, 'unused', 'unknown')
        else:
            autoplay._poll_pause(NS(stopping=False), batch, 'unused', 'unknown')
    finally:
        native.RUN_DEADLINE.reset(token)
    assert now[0] == pytest.approx(expected)


@pytest.mark.parametrize('title,body,guard,skip_shelf', [
    ('购买确认', '购买护珠消耗阿尔法金币2850', {'price': 2850}, True),
    ('购买完成', '购买了护珠消耗阿尔法金币2850', {}, True),
    ('购买确认', '购买护珠消耗阿尔法金币2700', {'price': 2850}, False),
    ('购买确认', '购买护珠', {}, False),
    ('购买确认', '购买护珠消耗阿尔法金币2850 消耗阿尔法金币2700', {}, False),
    ('确认', '购买护珠消耗阿尔法金币2850', {}, False),
])
def test_shop_reads_shelf_on_incomplete_or_changed_quote(title, body, guard, skip_shelf):
    seen = []
    values = {'DialogTitle': title, 'DialogBody': body, 'Confirm': '确认', 'Balance': '3000'}
    def recognize(node, image, *overrides):
        key = node.removeprefix('LabyShopRead_')
        seen.append(key)
        if key not in values:
            return None
        x, y, _, _ = shop_execution._READS[key][1]
        word = dict(text=values[key], box=[x, y, 20, 12], score=.99)
        return NS(hit=True, raw_detail={'filtered': [word]})
    argv = NS(image=np.zeros((720, 1280, 3), dtype=np.uint8),
              custom_recognition_param=json.dumps(guard))
    result = shop_execution.ShopRecognition().analyze(NS(run_recognition=recognize), argv)
    if skip_shelf:
        assert seen == ['DialogTitle', 'DialogBody', 'Confirm'] and result.box is not None
    else:
        assert set(seen) == set(shop_execution._READS)
        if guard:
            assert result.box is None
