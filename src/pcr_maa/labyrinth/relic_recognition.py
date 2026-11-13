"""Static relic-icon identity from the offline masterdata and relic pack.

A three-offer relic identity used to be the name OCR; the same offer frame and
its click frame could read the display separator differently, so a correct card
was rejected and the run stopped. Identity is now the icon artwork plus the
catalog facts, and both are versioned resources:

* ``labyrinth-relics.json`` is the masterdata catalog: one row per relic with its
  id, display name, mark, bonus and rarity. One element relic has no mark
  (``mark`` is ``None``); a mark-less relic is a valid identity, it just carries
  no strategy stack.
* ``relic-pack/PROVENANCE.json`` is the icon manifest: every icon is 128x128
  RGBA PNG, addressed relatively, and pinned by sha256, byte count, dimensions
  and pixel format.

Loading is fail-closed: a missing file, a hash or size mismatch, or a catalog
and manifest that disagree about the icon list raises
``BatchStopped('relic_resource_changed')``. There is no fallback that matches an
unknown icon.

Matching samples the icon art at normalized coordinates so the same matcher
serves the 95x95 result popup and the 160x160 three-card tiles. The art is
drawn at a different span and inset in each tile, so a small hierarchical
search over (span, x offset, y offset) finds the drawn art rectangle first: a
12x12 color coarse grid over the whole tile, then two levels of local
refinement. The score then uses the same color/luminance/gradient correlation
shape as ``character_recognition.IconMatcher`` at 20x20. Identity is only
reported when the confidence and the rival margin both pass; otherwise
``facts`` stays ``None``.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import re

import cv2
import numpy as np

from .batch import BatchStopped
from .strategies import MARKS

DATA = Path(__file__).resolve().parents[3] / 'assets/labyrinth'
CATALOG = DATA / 'labyrinth-relics.json'
PACK = DATA / 'relic-pack'
SCHEMA_VERSION = 1
SIZE = 128
CONFIDENCE = .62
RIVAL_MARGIN = .08
COARSE_WIDTH = 12
FINE_WIDTH = 20
# The icon art inside a tile is a square that is scaled and centred by the UI:
# the result popup draws the 128px art at 83px at inset (4,4) of its 95px box,
# the 160px card tile draws the same art at a size and inset that are not
# pinned by the 720p layout, so the search range starts wide and refines.
COARSE_SPANS = tuple(round(value, 4) for value in np.arange(.58, 1.001, .04))
COARSE_OFFSETS = (-.02, 0., .02)
SEARCH_FINALISTS = 2
CANDIDATES = 5
_SHA256 = re.compile(r'[0-9a-f]{64}')


@dataclass(frozen=True)
class RelicFacts:
    id: int
    name: str
    mark: str | None
    bonus: int
    rarity: int


@dataclass(frozen=True)
class RelicIconMatch:
    facts: RelicFacts | None
    score: float
    rival: float
    margin: float
    trusted: bool
    candidates: tuple
    art: tuple | None = None


def _round(value):
    return np.floor(np.asarray(value) + .5).astype(np.int64)


def _changed():
    raise BatchStopped('relic_resource_changed')


def _unknown_frame():
    raise BatchStopped('relic_frame_unknown')


def _text(value):
    if type(value) is not str or not value.strip():
        _changed()
    return value


def _count(value, *, minimum=1):
    if type(value) is not int or value < minimum:
        _changed()
    return value


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        _changed()


def _resolve(value, base):
    """A pack-relative resource path; absolute and escaping paths are refused."""
    if type(value) is not str:
        _changed()
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or any(part in ('..', '.') for part in relative.parts):
        _changed()
    return base.joinpath(*relative.parts)


def _box(value):
    return type(value) in (list, tuple) and len(value) == 4 and all(type(v) is int for v in value)


def _inside(box, shape):
    if not _box(box):
        return False
    x, y, w, h = box
    return w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= shape[1] and y + h <= shape[0]


def _frame(value):
    return type(value) is np.ndarray and value.dtype == np.uint8 and value.ndim == 3 and value.shape[2] == 3


@lru_cache(maxsize=128)
def _near(center, radius, step):
    return tuple(round(float(center) + delta, 4) for delta in np.arange(-radius, radius + step / 2, step))


class RelicCatalog:
    """Validated icon templates plus their masterdata facts.

    Instances are immutable after construction; build them through
    :func:`load_catalog` so one process reuses one validated pack.
    """

    def __init__(self, catalog=CATALOG, pack=PACK):
        self.catalog_path = Path(catalog)
        self.pack_path = Path(pack)
        try:
            self._load()
        except BatchStopped:
            raise
        except Exception:
            raise BatchStopped('relic_resource_changed') from None

    # -- loading ---------------------------------------------------------
    def _load(self):
        raw = _read_json(self.catalog_path)
        if type(raw) is not dict or type(raw.get('schemaVersion')) is not int or raw['schemaVersion'] != SCHEMA_VERSION:
            _changed()
        self.master_version = _text(raw.get('masterVersion'))
        rows = raw.get('relics')
        if type(rows) is not list or not rows:
            _changed()
        facts, refs = [], []
        seen = set()
        for row in rows:
            if type(row) is not dict:
                _changed()
            relic_id = _count(row.get('id'))
            if relic_id in seen:
                _changed()
            seen.add(relic_id)
            name = _text(row.get('name'))
            mark = row.get('mark')
            if mark is not None and (type(mark) is not str or mark not in MARKS):
                _changed()
            if type(row.get('effect')) is not str:
                _changed()
            facts.append(RelicFacts(relic_id, name, mark, _count(row.get('bonus'), minimum=0),
                                    _count(row.get('rarity'))))
            refs.append(_resolve(row.get('icon'), self.catalog_path.parent))
        images = self._icons(refs)
        self.facts = tuple(facts)
        self.by_id = {item.id: item for item in facts}
        self._ids = np.array([item.id for item in facts], dtype=np.int64)
        self._images = np.stack(images)
        self._prepare()

    def _icons(self, refs):
        provenance = _read_json(self.pack_path / 'PROVENANCE.json')
        if type(provenance) is not dict or type(provenance.get('icons')) is not list or not provenance['icons']:
            _changed()
        entries = {}
        for item in provenance['icons']:
            if type(item) is not dict:
                _changed()
            path = _resolve(item.get('path'), self.pack_path)
            if path in entries:
                _changed()
            digest = item.get('sha256')
            if type(digest) is not str or _SHA256.fullmatch(digest) is None:
                _changed()
            if (_count(item.get('width')) != SIZE or _count(item.get('height')) != SIZE
                    or item.get('pixelFormat') != '32bppArgb'):
                _changed()
            try:
                blob = path.read_bytes()
            except OSError:
                _changed()
            if _count(item.get('bytes')) != len(blob) or hashlib.sha256(blob).hexdigest() != digest:
                _changed()
            image = cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            if type(image) is not np.ndarray or image.dtype != np.uint8 or image.shape != (SIZE, SIZE, 4):
                _changed()
            entries[path] = image
        # The catalog and the manifest must name exactly the same icon files.
        if set(refs) != set(entries):
            _changed()
        return [entries[path][:, :, :3][:, :, ::-1] for path in refs]

    def _prepare(self):
        rgb = self._images
        luminance = (rgb[:, :, :, 0].astype(np.int32) * 299 + rgb[:, :, :, 1].astype(np.int32) * 587
                     + rgb[:, :, :, 2].astype(np.int32) * 114 + 500) // 1000
        grids = {}
        for width in (COARSE_WIDTH, FINE_WIDTH):
            xr, yr = np.meshgrid(np.linspace(0, 1, width), np.linspace(0, 1, width))
            xr, yr = xr.ravel(), yr.ravel()
            ix, iy = _round(xr * (SIZE - 1)), _round(yr * (SIZE - 1))
            colors = rgb[:, iy, ix, :].astype(np.float32)
            luma = luminance[:, iy, ix].astype(np.float32)
            gradient = (np.abs(luminance[:, iy, np.minimum(ix + 1, SIZE - 1)]
                               - luminance[:, iy, np.maximum(ix - 1, 0)])
                        + np.abs(luminance[:, np.minimum(iy + 1, SIZE - 1), ix]
                                 - luminance[:, np.maximum(iy - 1, 0), ix])).astype(np.float32)
            grids[width] = dict(xr=xr, yr=yr, colors=_centered(colors),
                                luma=_centered(luma[:, :, None]), gradient=_centered(gradient[:, :, None]))
        self._grids = grids

    @staticmethod
    def _correlation(observed, expected):
        observed = observed - observed.mean(axis=0, keepdims=True)
        numerator = np.einsum('kc,nkc->nc', observed, expected['data'])
        denominator = np.sqrt(expected['energy'] * np.sum(observed ** 2, axis=0))
        return np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 1e-9)

    # -- geometry search -------------------------------------------------
    @staticmethod
    def _rect(box, span, dx, dy):
        x, y, w, h = box
        sw, sh = max(1, int(round(w * span))), max(1, int(round(h * span)))
        return (x + int(round(w * (.5 + dx) - sw / 2)), y + int(round(h * (.5 + dy) - sh / 2)), sw, sh)

    @staticmethod
    @lru_cache(maxsize=128)
    def _candidates(box, shape, spans, dxs, dys):
        # Only layout coordinates are reusable. Every match scores the current frame.
        spec_rects = [((round(float(span), 4), round(float(dx), 4), round(float(dy), 4)),
                       RelicCatalog._rect(box, span, dx, dy))
                      for span in spans for dx in dxs for dy in dys]
        return tuple((spec, rect) for spec, rect in spec_rects if _inside(rect, shape))

    @staticmethod
    def _sample(rect, grid):
        left, top, width, height = rect
        return left + _round(grid['xr'] * (width - 1)), top + _round(grid['yr'] * (height - 1))

    def _color_scores(self, frame, geometries):
        grid = self._grids[COARSE_WIDTH]
        bounds = np.array(geometries, dtype=np.int64)
        left, top, width, height = bounds[:, 0], bounds[:, 1], bounds[:, 2], bounds[:, 3]
        fx = left[:, None] + _round(grid['xr'][None, :] * (width[:, None] - 1))
        fy = top[:, None] + _round(grid['yr'][None, :] * (height[:, None] - 1))
        flat = frame.reshape(-1, 3)
        observed = flat[fy * frame.shape[1] + fx][:, :, ::-1].astype(np.float32)
        observed -= observed.mean(axis=1, keepdims=True)
        observed = observed.transpose(2, 0, 1)
        expected = grid['colors']
        numerator = np.matmul(observed, expected['data'].transpose(2, 1, 0))
        denominator = np.sqrt(expected['energy'].transpose(1, 0)[:, None, :]
                              * np.sum(observed ** 2, axis=2)[:, :, None])
        correlation = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 1e-9)
        return np.clip(correlation.mean(axis=0), 0, None)

    def _search(self, frame, box):
        shape = frame.shape
        pairs = self._candidates(box, shape, COARSE_SPANS, COARSE_OFFSETS, COARSE_OFFSETS)
        if not pairs:
            _unknown_frame()
        coarse = self._color_scores(frame, [rect for _, rect in pairs])
        order = np.argsort(-coarse.max(axis=1), kind='stable')[:SEARCH_FINALISTS]
        refined = []
        for index in order:
            span, dx, dy = pairs[int(index)][0]
            pairs1 = self._candidates(box, shape, _near(span, .02, .02), _near(dx, .03, .01), _near(dy, .03, .01))
            if not pairs1:
                continue
            scores1 = self._color_scores(frame, [rect for _, rect in pairs1])
            refined.append(pairs1[int(np.argmax(scores1.max(axis=1)))])
        if not refined:
            _unknown_frame()
        span, dx, dy = refined[0][0]
        pairs2 = self._candidates(box, shape, _near(span, .02, .01), _near(dx, .01, .005), _near(dy, .01, .005))
        if pairs2:
            scores2 = self._color_scores(frame, [rect for _, rect in pairs2])
            span, dx, dy = pairs2[int(np.argmax(scores2.max(axis=1)))][0]
        pairs3 = self._candidates(box, shape, _near(span, .005, .0025), (dx,), (dy,))
        if pairs3:
            scores3 = self._color_scores(frame, [rect for _, rect in pairs3])
            span, dx, dy = pairs3[int(np.argmax(scores3.max(axis=1)))][0]
        geometries = [self._rect(box, span, dx, dy), refined[0][1]]
        if len(refined) > 1:
            geometries.append(refined[1][1])
        return tuple(dict.fromkeys(rect for rect in geometries if _inside(rect, shape)))

    # -- scoring ---------------------------------------------------------
    def _scores(self, frame, rect):
        grid = self._grids[FINE_WIDTH]
        fx, fy = self._sample(rect, grid)
        height, width = frame.shape[:2]
        observed = frame[fy, fx][:, ::-1].astype(np.float32)
        color = np.clip(self._correlation(observed, grid['colors']).mean(axis=1), 0, None)
        step = max(1, int(round(rect[2] / SIZE)), int(round(rect[3] / SIZE)))

        def luminance(px, py):
            px, py = np.clip(px, 0, width - 1), np.clip(py, 0, height - 1)
            bgr = frame[py, px].astype(np.float32)
            return (bgr[:, 2] * 299 + bgr[:, 1] * 587 + bgr[:, 0] * 114 + 500) / 1000

        actual_lum = luminance(fx, fy)
        actual_grad = (np.abs(luminance(fx + step, fy) - luminance(fx - step, fy))
                       + np.abs(luminance(fx, fy + step) - luminance(fx, fy - step)))
        luma = np.clip(self._correlation(actual_lum[:, None], grid['luma'])[:, 0], 0, None)
        gradient = np.clip(self._correlation(actual_grad[:, None], grid['gradient'])[:, 0], 0, None)
        return np.clip(color * .55 + luma * .25 + gradient * .20, 0, 1)

    # -- matching --------------------------------------------------------
    def match(self, frame, box, *, art=None):
        """Identify the relic icon inside a square tile box of a native frame.

        ``art`` is an optional explicit art rectangle in frame pixels; passing
        it skips the layout search (for a caller that already knows the crop).
        """
        if not _frame(frame) or not _inside(box, frame.shape) or box[2] < 16 or box[3] < 16:
            _unknown_frame()
        if art is not None:
            if not _inside(art, frame.shape):
                _unknown_frame()
            geometries = (tuple(art),)
        else:
            geometries = self._search(frame, tuple(box))
        per_geometry = np.stack([self._scores(frame, rect) for rect in geometries])
        best_geometry = np.argmax(per_geometry, axis=0)
        scores = per_geometry[best_geometry, np.arange(len(self._ids))]
        order = np.argsort(-scores, kind='stable')
        best = int(order[0])
        score = float(scores[best])
        rival = float(scores[order[1]]) if len(order) > 1 else 0.
        margin = score - rival
        trusted = score >= CONFIDENCE and margin >= RIVAL_MARGIN
        candidates = tuple((int(self._ids[index]), float(scores[index])) for index in order[:CANDIDATES])
        art_rect = geometries[int(best_geometry[best])]
        return RelicIconMatch(self.by_id[int(self._ids[best])] if trusted else None, score, rival, margin,
                              trusted, candidates, art_rect)


def _centered(expected):
    data = expected - expected.mean(axis=1, keepdims=True)
    return dict(data=data, energy=np.sum(data ** 2, axis=1))


@lru_cache(maxsize=8)
def _load(catalog, pack):
    return RelicCatalog(catalog, pack)


def load_catalog(catalog=CATALOG, pack=PACK):
    """One validated catalog per (catalog, pack) pair per process."""
    try:
        key, root = Path(catalog).resolve(), Path(pack).resolve()
    except (TypeError, ValueError):
        _changed()
    return _load(key, root)
