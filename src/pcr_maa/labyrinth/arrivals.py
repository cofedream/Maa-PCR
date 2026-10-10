"""Exact three-character arrivals and a scoped close guard for this modal.

Readback records only the visible arrivals. It does not reconstruct the owned
roster, award marks, assert a following page, or authorize a fresh game capture.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import math
import re
from .batch import BatchStopped
from .rewards import ALIASES, normalized_name
from .roles import load_optimizer

TITLE_ROI = (520, 94, 250, 46)
MESSAGE_ROI = (485, 150, 335, 53)
NAME_ROIS = tuple((415, y, 440, 53) for y in (238, 332, 426))
CLOSE_ROI = (500, 547, 278, 66)
THRESHOLD = .85
_SENTINEL = '^__qualified_character_arrival_required__$'
# Name fact only, copied from pinned cn-bilibili/characters.landosolroster.json:
# officialName=花音, id=1349, catalog displayName=华音. No runtime roster dependency.
# The second mapping is restricted to this complete three-row modal. Root
# visually qualified 咲恋 against link-node-live-01/observe-after.png; fixed OCR
# returned 联恋 (.992415) while the catalog uniquely identifies 咲恋 as 1028.
_LOCAL_ALIASES = {
    '花音': ('1349', '华音', 'cn-bilibili-official-name'),
    '联恋': ('1028', '咲恋', 'qualified-three-character-arrival-ocr'),
}


@dataclass(frozen=True)
class Arrival:
    slot: int
    character_id: str
    raw_text: str
    canonical_name: str
    alias_source: str
    box: tuple[int, int, int, int]
    score: float


def _ocr(expected, roi):
    return dict(recognition='OCR', expected=expected, roi=list(roi), threshold=THRESHOLD,
        action='DoNothing', next=[], timeout=1500, pre_delay=0, post_delay=0)


def arrived_pipeline():
    """Register observation and a dormant close; no generic modal close exists."""
    nodes = {
        'LabyCharactersArrivedTitle': _ocr('^角色加入$', TITLE_ROI),
        'LabyCharactersArrivedMessage': _ocr('^以下角色已加入到伙伴中[。.]?$', MESSAGE_ROI),
        'LabyCharactersArrivedClose': _ocr('^关闭$', CLOSE_ROI),
    }
    for slot, roi in enumerate(NAME_ROIS):
        nodes[f'LabyCharactersArrivedName{slot}'] = _ocr('.+', roi)
        nodes[f'LabyCharactersArrivedGuard{slot}'] = _ocr(_SENTINEL, roi)
    nodes['LabyCharactersArrivedObserved'] = dict(recognition='And',
        all_of=['LabyCharactersArrivedTitle', 'LabyCharactersArrivedMessage']
            + [f'LabyCharactersArrivedName{i}' for i in range(3)]
            + ['LabyCharactersArrivedClose'],
        action='DoNothing', next=[], timeout=3000, pre_delay=0, post_delay=0)
    nodes['LabyCharactersArrivedCloseOnce'] = dict(recognition='And',
        all_of=['LabyCharactersArrivedClose', 'LabyCharactersArrivedTitle',
            'LabyCharactersArrivedMessage'],
        action='Click', target=True, max_hit=1, next=[], timeout=2500,
        rate_limit=250, pre_delay=0, post_delay=0)
    return nodes


def _unknown():
    raise BatchStopped('character_arrivals_unknown')


def _inside(box, roi):
    return (type(box) in (list, tuple) and len(box) == 4
        and all(type(v) is int for v in box) and box[2] > 0 and box[3] > 0
        and box[0] >= roi[0] and box[1] >= roi[1]
        and box[0]+box[2] <= roi[0]+roi[2] and box[1]+box[3] <= roi[1]+roi[3])


def _word(row, roi):
    detail = row.get('detail')
    values = detail.get('filtered') if type(detail) is dict else None
    if (row.get('algorithm') != 'OCR' or type(values) is not list or len(values) != 1
            or type(values[0]) is not dict):
        _unknown()
    word = values[0]
    text, box, score = word.get('text'), word.get('box'), word.get('score')
    if (type(text) is not str or not text.strip() or len(text) > 48
            or any(ord(c) < 32 for c in text) or type(box) is not list or not _inside(box, roi)
            or type(row.get('box')) is not list or not _inside(row['box'], roi) or row['box'] != box
            or type(score) not in (int, float) or not THRESHOLD <= score <= 1
            or not math.isfinite(score) or detail.get('all') != values
            or detail.get('best') != word):
        _unknown()
    return text, tuple(box), float(score)


def _resolve(text):
    name = normalized_name(text)
    optimizer = load_optimizer()
    keys = {key for key, profile in optimizer.profiles.items()
        if normalized_name(profile.data.get('displayName', '')) == name}
    source = 'catalog'
    if name in ALIASES:
        keys.add(ALIASES[name]); source = 'existing-b-server-name'
    if name in _LOCAL_ALIASES:
        key, canonical, source = _LOCAL_ALIASES[name]
        profile = optimizer.profiles.get(key)
        if profile is None or normalized_name(profile.data.get('displayName', '')) != canonical:
            _unknown()
        keys.add(key)
    if len(keys) != 1:
        _unknown()
    key = next(iter(keys))
    if (type(key) is not str or re.fullmatch(r'[1-9][0-9]{3}', key) is None
            or key not in optimizer.profiles):
        _unknown()
    canonical = optimizer.profiles[key].data.get('displayName')
    if type(canonical) is not str or not canonical or len(canonical) > 48:
        _unknown()
    return key, normalized_name(canonical), source


def _validated(arrived):
    if type(arrived) is not tuple or len(arrived) != 3:
        _unknown()
    for slot, item in enumerate(arrived):
        if (type(item) is not Arrival or type(item.slot) is not int or item.slot != slot
                or type(item.raw_text) is not str or type(item.box) is not tuple
                or not _inside(item.box, NAME_ROIS[slot]) or type(item.score) is not float
                or not THRESHOLD <= item.score <= 1 or not math.isfinite(item.score)):
            _unknown()
        word = dict(text=item.raw_text, box=list(item.box), score=item.score)
        _word(dict(algorithm='OCR', box=list(item.box),
            detail=dict(all=[word], best=word, filtered=[word])), NAME_ROIS[slot])
        if (item.character_id, item.canonical_name, item.alias_source) != _resolve(item.raw_text):
            _unknown()
    if len({item.character_id for item in arrived}) != 3:
        raise BatchStopped('character_arrivals_conflicting')
    return arrived


def characters_arrived(receipt, expected=None):
    """Read exactly three distinct arrivals; expected is an optional ordered ID tuple."""
    if (type(receipt) is not dict or receipt.get('native_ok') is not True
            or receipt.get('tasker_inited') is not True
            or type(receipt.get('task_id')) is not int or receipt['task_id'] <= 0):
        _unknown()
    nodes = receipt.get('nodes')
    if (type(nodes) is not list or len(nodes) != 1 or type(nodes[0]) is not dict
            or nodes[0].get('name') != 'LabyCharactersArrivedObserved'
            or nodes[0].get('completed') is not True):
        _unknown()
    raw = nodes[0].get('recognition')
    names = arrived_pipeline()['LabyCharactersArrivedObserved']['all_of']
    if (type(raw) is not list or len(raw) != len(names)
            or any(type(r) is not dict or type(r.get('name')) is not str for r in raw)
            or {r['name'] for r in raw} != set(names)):
        _unknown()
    rows = {r['name']:r for r in raw}
    for name, roi, texts in (
            ('Title', TITLE_ROI, ('角色加入',)),
            ('Message', MESSAGE_ROI, ('以下角色已加入到伙伴中。','以下角色已加入到伙伴中.','以下角色已加入到伙伴中')),
            ('Close', CLOSE_ROI, ('关闭',))):
        if _word(rows['LabyCharactersArrived'+name], roi)[0] not in texts:
            _unknown()
    arrived = []
    for slot, roi in enumerate(NAME_ROIS):
        text, box, score = _word(rows[f'LabyCharactersArrivedName{slot}'], roi)
        key, canonical, source = _resolve(text)
        arrived.append(Arrival(slot, key, text, canonical, source, box, score))
    arrived = _validated(tuple(arrived))
    if expected is not None:
        if (type(expected) is not tuple or len(expected) != 3
                or any(type(key) is not str or re.fullmatch(r'[1-9][0-9]{3}', key) is None for key in expected)
                or len(set(expected)) != 3):
            _unknown()
        if tuple(item.character_id for item in arrived) != expected:
            raise BatchStopped('character_arrivals_mismatch')
    return arrived


def _pattern(text):
    # Recheck the actual OCR reading (including a qualified local misreading),
    # rather than matching any character or applying an OCR alias to other pages.
    parts = ['[（(]' if c == '(' else '[）)]' if c == ')' else re.escape(c)
             for c in normalized_name(text)]
    return r'^\s*' + r'\s*'.join(parts) + r'\s*$'


@contextmanager
def arrived_guard(tasker, arrived):
    """Temporarily qualify this exact three-row close; never reset a hit budget."""
    arrived = _validated(arrived)
    resource = tasker.resource
    overrides = {f'LabyCharactersArrivedGuard{i}':dict(expected=_pattern(item.raw_text))
                 for i, item in enumerate(arrived)}
    original = {name:resource.get_node_data(name) for name in overrides}
    if any(not value for value in original.values()) or not resource.override_pipeline(overrides):
        raise BatchStopped('character_arrivals_guard_failed')
    try:
        yield
    except BaseException:
        try:
            resource.override_pipeline(original)
        except Exception:
            pass
        raise
    else:
        if not resource.override_pipeline(original):
            raise BatchStopped('character_arrivals_guard_restore_failed')

