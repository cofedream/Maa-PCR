"""Relic icon identity from the masterdata catalog and the provenance-checked pack.

The static identity replaces the name OCR as the relic identity, so the two
failure modes of that path are checked here: a resource pack must be validated
(or refused) as a whole, and an icon match only yields catalog facts when both
the confidence and the rival margin pass. The synthetic pack is drawn in place
and never reads the distributable pack; the real-pack cases paste the shipped
128x128 artwork into the two tile layouts, and the saved-frame replay only runs
when a private frame is supplied.
"""
import hashlib
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.relic_recognition import (
    CONFIDENCE, RIVAL_MARGIN, RelicFacts, load_catalog)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets/labyrinth'
CATALOG = ASSETS / 'labyrinth-relics.json'
PACK = ASSETS / 'relic-pack'
ICONS = PACK / 'icons'
RESULT_ICON_ROI = (355, 150, 95, 95)
CARD_ICON_ROIS = tuple((x, 216, 160, 160) for x in (189, 559, 929))
SIZE = 128
# The real result popup draws the 128px art at 83px at inset (4,4) of its
# 95px tile; the three-card tile layout is not pinned by the 720p layout, so
# the matcher is exercised at several plausible art rectangles.
RESULT_ART = (359, 154, 83, 83)


def _png(image, alpha=True):
    if alpha:
        image = np.dstack([image, np.full(image.shape[:2], 255, np.uint8)])
    ok, blob = cv2.imencode('.png', image)
    assert ok
    return blob.tobytes()


def _draw(pattern):
    image = np.zeros((SIZE, SIZE, 3), np.uint8)
    if pattern == 0:
        image[:] = (200, 30, 30)
        cv2.circle(image, (48, 56), 34, (250, 220, 40), -1)
        cv2.rectangle(image, (82, 80), (116, 114), (40, 220, 220), -1)
        cv2.circle(image, (28, 100), 10, (250, 250, 250), -1)
    elif pattern == 1:
        image[:] = (30, 60, 200)
        cv2.rectangle(image, (24, 24), (104, 104), (40, 220, 80), -1)
        cv2.line(image, (0, 0), (127, 127), (250, 250, 250), 6)
        cv2.circle(image, (104, 26), 12, (240, 30, 30), -1)
    elif pattern == 2:
        image[:] = (30, 180, 90)
        image[::16] = (250, 250, 250)
        image[:, 92:104] = (10, 10, 10)
        cv2.circle(image, (28, 30), 14, (230, 40, 220), -1)
    elif pattern == 3:
        image[:] = (120, 40, 160)
        cv2.line(image, (16, 16), (112, 112), (10, 10, 10), 12)
        cv2.line(image, (112, 16), (16, 112), (10, 10, 10), 12)
        cv2.rectangle(image, (0, 0), (18, 18), (250, 220, 40), -1)
        cv2.rectangle(image, (110, 110), (127, 127), (250, 220, 40), -1)
    elif pattern == 4:
        image[:] = (30, 170, 170)
        cv2.circle(image, (64, 64), 40, (240, 140, 20), 12)
        cv2.rectangle(image, (10, 10), (24, 24), (250, 250, 250), -1)
        cv2.rectangle(image, (104, 104), (118, 118), (220, 30, 30), -1)
    elif pattern == 5:
        image[:] = (30, 170, 170)
        cv2.circle(image, (64, 64), 40, (240, 140, 20), 12)
        cv2.rectangle(image, (10, 10), (24, 24), (250, 250, 250), -1)
        cv2.rectangle(image, (104, 104), (118, 118), (220, 30, 30), -1)
        cv2.circle(image, (64, 20), 8, (250, 250, 250), -1)
    else:
        image[:] = (220, 200, 40)
        cv2.rectangle(image, (32, 24), (96, 48), (10, 10, 10), -1)
        cv2.rectangle(image, (32, 24), (56, 104), (10, 10, 10), -1)
        cv2.circle(image, (100, 20), 12, (30, 60, 220), -1)
    return image


def _relic_rows():
    return [
        dict(id=11001, name='加速玉饰', mark='加速', bonus=1, rarity=1, image=_draw(0)),
        dict(id=12001, name='魔杖咒术', mark='弱体', bonus=1, rarity=1, image=_draw(1)),
        dict(id=13001, name='杀戮之矛', mark='会心', bonus=1, rarity=1, image=_draw(2)),
        dict(id=14001, name='强化宝石', mark='强化', bonus=1, rarity=1, image=_draw(3)),
        dict(id=21001, name='流烈耳饰', mark='加速', bonus=2, rarity=2, image=_draw(4)),
        dict(id=21002, name='星泪辉盾', mark='加速', bonus=2, rarity=2, image=_draw(5)),
        dict(id=30001, name='元素涌动宝珠', mark=None, bonus=0, rarity=3, image=_draw(6)),
    ]


def _write_pack(root, rows=None):
    """Write a catalog + provenance + icons triple; returns (catalog, pack)."""
    rows = _relic_rows() if rows is None else rows
    assets = root / 'assets/labyrinth'
    pack = assets / 'relic-pack'
    icons = pack / 'icons'
    icons.mkdir(parents=True)
    catalog_rows, provenance = [], []
    for row in rows:
        image = row['image']
        blob = _png(image)
        (icons / f"{row['id']}.png").write_bytes(blob)
        catalog_rows.append(dict(id=row['id'], name=row['name'], mark=row['mark'],
                                 bonus=row['bonus'], rarity=row['rarity'],
                                 effect='synthetic effect', icon=f"relic-pack/icons/{row['id']}.png"))
        provenance.append(dict(path=f"icons/{row['id']}.png", sha256=hashlib.sha256(blob).hexdigest(),
                               bytes=len(blob), width=image.shape[1], height=image.shape[0],
                               pixelFormat='32bppArgb', sourceBundle='synthetic'))
    (assets / 'labyrinth-relics.json').write_text(
        json.dumps(dict(schemaVersion=1, masterVersion='202601010000', relics=catalog_rows),
                   ensure_ascii=False), encoding='utf-8')
    (pack / 'PROVENANCE.json').write_text(
        json.dumps(dict(schemaVersion=1, icons=provenance), ensure_ascii=False), encoding='utf-8')
    return assets / 'labyrinth-relics.json', pack


def _patch_json(path, mutate):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    mutate(value)
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def _frame(background=40):
    return np.full((720, 1280, 3), background, np.uint8)


def _place(frame, image, art):
    """Composite a decoded icon image into a frame art rectangle (BGR frame)."""
    x, y, w, h = art
    frame[y:y + h, x:x + w] = cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)


def _image(rows, relic_id):
    return next(row['image'] for row in rows if row['id'] == relic_id)


@pytest.fixture(scope='module')
def synthetic(tmp_path_factory):
    root = tmp_path_factory.mktemp('synthetic-relic-pack')
    return root, _write_pack(root), _relic_rows()


def test_synthetic_pack_matches_the_result_popup_and_the_card_tile(synthetic):
    _, (catalog_path, pack), rows = synthetic
    catalog = load_catalog(catalog_path, pack)
    cases = [
        (13001, RESULT_ICON_ROI, RESULT_ART),
        (21001, CARD_ICON_ROIS[0], (189 + 24, 216 + 24, 112, 112)),
        (30001, CARD_ICON_ROIS[1], CARD_ICON_ROIS[1]),
        (12001, CARD_ICON_ROIS[2], (929 + 8, 216 + 8, 144, 144)),
    ]
    for relic_id, box, art in cases:
        frame = _frame()
        _place(frame, _image(rows, relic_id), art)
        matched = catalog.match(frame, box)
        assert matched.trusted and matched.facts is not None
        assert matched.facts.id == relic_id
        assert matched.score >= CONFIDENCE and matched.margin >= RIVAL_MARGIN
        assert dict(id=matched.facts.id, name=matched.facts.name, mark=matched.facts.mark,
                    bonus=matched.facts.bonus, rarity=matched.facts.rarity) == {
            key: next(row[key] for row in rows if row['id'] == relic_id)
            for key in ('id', 'name', 'mark', 'bonus', 'rarity')}
        assert matched.candidates[0][0] == relic_id


def test_a_markless_relic_keeps_its_catalog_identity(synthetic):
    _, (catalog_path, pack), rows = synthetic
    catalog = load_catalog(catalog_path, pack)
    frame = _frame()
    _place(frame, _image(rows, 30001), RESULT_ART)
    matched = catalog.match(frame, RESULT_ICON_ROI)
    assert matched.trusted and matched.facts.id == 30001
    assert matched.facts.mark is None and matched.facts.bonus == 0


def test_near_identical_icons_do_not_pass_the_rival_margin(synthetic):
    _, (catalog_path, pack), rows = synthetic
    catalog = load_catalog(catalog_path, pack)
    blended = np.clip((_draw(4).astype(np.float32) + _draw(5).astype(np.float32)) / 2, 0, 255).astype(np.uint8)
    frame = _frame()
    _place(frame, blended, RESULT_ART)
    matched = catalog.match(frame, RESULT_ICON_ROI)
    assert not matched.trusted and matched.facts is None
    assert matched.score >= CONFIDENCE      # the icon was recognised ...
    assert matched.margin < RIVAL_MARGIN    # ... but its rival stayed too close


def test_pack_resource_mismatches_are_fail_closed(tmp_path):
    def build():
        root = tmp_path / f'case{len(list(tmp_path.iterdir()))}'
        return root, _write_pack(root)

    def corrupt_hash(catalog_path, pack):
        _patch_json(pack / 'PROVENANCE.json',
                    lambda value: value['icons'][0].__setitem__('sha256', '0' * 64))

    def corrupt_bytes(catalog_path, pack):
        _patch_json(pack / 'PROVENANCE.json',
                    lambda value: value['icons'][0].__setitem__('bytes', value['icons'][0]['bytes'] + 1))

    def corrupt_format(catalog_path, pack):
        _patch_json(pack / 'PROVENANCE.json',
                    lambda value: value['icons'][0].__setitem__('pixelFormat', '24bppRgb'))

    def missing_icon(catalog_path, pack):
        (pack / 'icons/11001.png').unlink()

    def provenance_extra(catalog_path, pack):
        rows = _relic_rows()
        extra = _png(_draw(0))
        (pack / 'icons/99999.png').write_bytes(extra)
        _patch_json(pack / 'PROVENANCE.json', lambda value: value['icons'].append(dict(
            path='icons/99999.png', sha256=hashlib.sha256(extra).hexdigest(), bytes=len(extra),
            width=SIZE, height=SIZE, pixelFormat='32bppArgb', sourceBundle='synthetic')))

    def missing_manifest(catalog_path, pack):
        (pack / 'PROVENANCE.json').unlink()

    def wrong_dimensions(catalog_path, pack):
        image = _draw(0)[:64, :64]
        blob = _png(image)
        (pack / 'icons/11001.png').write_bytes(blob)
        _patch_json(pack / 'PROVENANCE.json', lambda value: value['icons'][0].update(
            sha256=hashlib.sha256(blob).hexdigest(), bytes=len(blob), width=64, height=64))

    for tamper in (corrupt_hash, corrupt_bytes, corrupt_format, missing_icon, provenance_extra,
                   missing_manifest, wrong_dimensions):
        _, (catalog_path, pack) = build()
        tamper(catalog_path, pack)
        with pytest.raises(BatchStopped, match='relic_resource_changed'):
            load_catalog(catalog_path, pack)


def test_absent_catalog_or_manifest_is_fail_closed(tmp_path):
    catalog_path, pack = _write_pack(tmp_path / 'pack')
    with pytest.raises(BatchStopped, match='relic_resource_changed'):
        load_catalog(tmp_path / 'nowhere/labyrinth-relics.json', pack)
    with pytest.raises(BatchStopped, match='relic_resource_changed'):
        load_catalog(catalog_path, tmp_path / 'pack/assets/nowhere')


def test_invalid_frames_and_boxes_are_refused(synthetic):
    _, (catalog_path, pack), rows = synthetic
    catalog = load_catalog(catalog_path, pack)
    frame = _frame()
    _place(frame, _image(rows, 13001), RESULT_ART)
    assert catalog.match(frame, RESULT_ICON_ROI).trusted
    for bad_frame in (None, np.zeros((720, 1280), np.uint8), np.zeros((720, 1280, 4), np.uint8),
                      np.zeros((720, 1280, 3), np.float32)):
        with pytest.raises(BatchStopped, match='relic_frame_unknown'):
            catalog.match(bad_frame, RESULT_ICON_ROI)
    for bad_box in (None, (355, 150, 95), (355, 150, 0, 95), (355, 150, 95, -1),
                    (-1, 150, 95, 95), (1250, 150, 95, 95), (355, 660, 95, 95),
                    (355, 150, 95.0, 95), (True, 150, 95, 95)):
        with pytest.raises(BatchStopped, match='relic_frame_unknown'):
            catalog.match(frame, bad_box)
    for bad_art in ((355, 150, 95), (355, 150, 0, 95), (1250, 150, 95, 95)):
        with pytest.raises(BatchStopped, match='relic_frame_unknown'):
            catalog.match(frame, RESULT_ICON_ROI, art=bad_art)


def test_catalog_instances_are_reused_for_identical_paths(tmp_path):
    catalog_path, pack = _write_pack(tmp_path / 'first')
    first = load_catalog(catalog_path, pack)
    assert load_catalog(catalog_path, pack) is first
    assert load_catalog(str(catalog_path), str(pack)) is first
    other_catalog, other_pack = _write_pack(tmp_path / 'second')
    other = load_catalog(other_catalog, other_pack)
    assert other is not first


def test_explicit_art_rectangle_skips_the_layout_search(synthetic):
    _, (catalog_path, pack), rows = synthetic
    catalog = load_catalog(catalog_path, pack)
    frame = _frame()
    _place(frame, _image(rows, 14001), (359, 157, 80, 80))
    matched = catalog.match(frame, RESULT_ICON_ROI, art=(359, 157, 80, 80))
    assert matched.trusted and matched.facts.id == 14001
    assert matched.art == (359, 157, 80, 80)


REAL_READY = (CATALOG.exists() and (PACK / 'PROVENANCE.json').exists()
              and (ICONS / '35004.png').exists())
needs_real_pack = pytest.mark.skipif(not REAL_READY, reason='relic image pack is not distributed here')


def _real_icon(relic_id):
    raw = (ICONS / f'{relic_id}.png').read_bytes()
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    assert image is not None and image.shape == (SIZE, SIZE, 4)
    return image[:, :, :3]


@needs_real_pack
def test_real_pack_facts_come_from_the_catalog():
    catalog = load_catalog()
    assert catalog.by_id[35004] == RelicFacts(35004, '幽玄煌装·绝魔', '守备', 3, 3)
    assert catalog.by_id[30001] == RelicFacts(30001, '元素涌动宝珠', None, 0, 3)
    assert set(catalog.by_id) >= {11001, 13001, 31006, 35004, 50005}


@needs_real_pack
@pytest.mark.parametrize('relic_id,box,art', [
    (35004, RESULT_ICON_ROI, RESULT_ART),
    (31006, RESULT_ICON_ROI, RESULT_ART),
    (35004, CARD_ICON_ROIS[0], (189 + 24, 216 + 24, 112, 112)),
    (11001, CARD_ICON_ROIS[0], CARD_ICON_ROIS[0]),
    (13001, CARD_ICON_ROIS[1], (559 + 8, 216 + 8, 144, 144)),
    (50005, CARD_ICON_ROIS[2], (929 + 16, 216 + 16, 128, 128)),
])
def test_real_pack_identifies_pasted_result_and_card_icons(relic_id, box, art):
    catalog = load_catalog()
    frame = _frame()
    _place(frame, _real_icon(relic_id), art)
    matched = catalog.match(frame, box)
    assert matched.trusted and matched.facts is not None
    assert matched.facts.id == relic_id
    assert matched.score >= CONFIDENCE and matched.margin >= RIVAL_MARGIN
    assert matched.candidates[0][0] == relic_id


@needs_real_pack
@pytest.mark.parametrize('frame', [
    np.full((720, 1280, 3), 240, np.uint8),
    np.full((720, 1280, 3), 20, np.uint8),
    np.random.default_rng(7).integers(0, 256, (720, 1280, 3), dtype=np.uint8),
])
def test_real_pack_refuses_frames_without_a_known_icon(frame):
    matched = load_catalog().match(frame, RESULT_ICON_ROI)
    assert not matched.trusted and matched.facts is None


@needs_real_pack
def test_real_pack_single_frame_match_stays_far_below_a_second():
    catalog = load_catalog()
    frame = _frame(background=240)
    _place(frame, _real_icon(35004), RESULT_ART)
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        catalog.match(frame, RESULT_ICON_ROI)
        timings.append((time.perf_counter() - started) * 1000)
    assert sorted(timings)[len(timings) // 2] < 100


@pytest.mark.private_replay
def test_saved_result_frame_identifies_the_obtained_relic():
    source = os.environ.get('PCR_RELIC_FRAME')
    if not source:
        pytest.skip('private saved relic result frame is not distributed')
    image = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and image.shape == (720, 1280, 3)
    matched = load_catalog().match(image, RESULT_ICON_ROI)
    assert matched.trusted and matched.facts is not None
    assert matched.facts.id == 35004 and matched.facts.name == '幽玄煌装·绝魔'
    assert matched.score >= CONFIDENCE and matched.margin >= RIVAL_MARGIN


@pytest.mark.private_replay
def test_saved_non_icon_frame_keeps_the_identity_untrusted():
    source = os.environ.get('PCR_RELIC_FRAME_NEGATIVE')
    if not source:
        pytest.skip('private saved non-icon frame is not distributed')
    image = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and image.shape == (720, 1280, 3)
    matched = load_catalog().match(image, RESULT_ICON_ROI)
    assert not matched.trusted and matched.facts is None
