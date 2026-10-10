"""Maa three-card relic observations and a fresh, exact one-shot input guard.

Card names identify only this visible offer; they are not protocol item IDs.
The upstream strategy is reused without assuming unobserved mark stacks are zero.
Acquisition matches the current selected icon and a new quantity result; the native click ACK commits
only the input, before that independent business observation.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import product
import math
import re
import cv2
from maa.custom_recognition import CustomRecognition
from .batch import BatchStopped
from .strategies import ALIASES, MARKS, Relic, choose_relic

TITLE_ROI = (395, 25, 475, 77)
SUBTITLE_ROI = (435, 106, 410, 42)
REMAINING_ROI = (568, 143, 145, 43)
# Keep the full outlined name row and its side padding for line recognition.
NAME_ROIS = tuple((x, 378, 288, 42) for x in (125, 494, 864))
# Text only: exclude the adjacent information icon from line recognition.
ATTRIBUTE_ROIS = tuple((x, 432, 238, 38) for x in (125, 494, 864))
BUTTON_ROIS = tuple((x + 50, 574, 193, 83) for x in (125, 494, 864))
BRIGHT_ROIS = tuple((x, 583, 100, 14) for x in (225, 595, 964))
THRESHOLD = .85


def _ocr(expected, roi):
    return dict(recognition='OCR', expected=expected, roi=list(roi), threshold=THRESHOLD,
                action='DoNothing', next=[], timeout=1500, pre_delay=0, post_delay=0)


def _name_nodes(name, expected, roi):
    # Outlined names need either line recognition or native text detection.
    nodes = {name+suffix: dict(_ocr(expected, roi), only_rec=line)
             for suffix, line in (('Line', True), ('Detected', False))}
    nodes[name+'Native'] = dict(recognition='Or', any_of=list(nodes), action='DoNothing',
        next=[], timeout=1500, pre_delay=0, post_delay=0)
    # Keep each name's native OCR crop out of And's multi-card OCR batch cache.
    nodes[name] = dict(recognition='Custom', custom_recognition='LabyRelicSelectedIconCapture',
        custom_recognition_param=dict(mode='name',node=name+'Native'),
        action='DoNothing',next=[],timeout=1500,pre_delay=0,post_delay=0)
    return nodes


# Pinned upstream choice icon layout at 1280x720. Runtime templates use the
# inner art, excluding the stars/mark/frame, as the existing portrait cache does.
ICON_ROIS = tuple((x, 216, 160, 160) for x in (189, 559, 929))
RESULT_ICON_ROI = (355, 150, 95, 95)
_ICON_IMAGE = 'LabyRelicSelectedIcon.png'


class RelicIconCapture(CustomRecognition):
    """Capture only after the current selection's existing same-frame guards."""
    def __init__(self, *, icon_rois=ICON_ROIS, image_name=_ICON_IMAGE,
                 match_node='LabyRelicObtainedIconNative', match_roi=None):
        super().__init__();self.icon=None
        self.icon_rois=icon_rois;self.image_name=image_name;self.match_node=match_node;self.match_roi=match_roi

    def analyze(self, context, argv):
        import json
        params = json.loads(argv.custom_recognition_param or '{}')
        if params.get('mode') == 'name':
            read=context.run_recognition(params['node'],argv.image)
            raw=read.raw_detail if read is not None else None
            if type(raw) is str:raw=json.loads(raw)
            return self.AnalyzeResult(read.box if read is not None and read.hit else None,raw)
        if params.get('mode') == 'obtained':
            if self.icon is None or not context.override_image(self.image_name, self.icon):
                return self.AnalyzeResult(None, dict(matched=False))
            image=argv.image
            if self.match_roi is not None:
                x,y,w,h=self.match_roi
                image=cv2.resize(image[y:y+h,x:x+w,:3],(80,80),interpolation=cv2.INTER_LINEAR)
            match = context.run_recognition(self.match_node, image)
            return self.AnalyzeResult(match.box if match is not None and match.hit else None,
                dict(match=match.raw_detail if match is not None else None))
        slot = params.get('slot')
        if type(slot) is not int or slot not in (0, 1, 2) or tuple(argv.image.shape[:2]) != (720, 1280):
            return self.AnalyzeResult(None, dict(captured=False))
        x, y, w, h = self.icon_rois[slot]
        icon = cv2.resize(argv.image[y:y+h, x:x+w, :3], (80, 80), interpolation=cv2.INTER_LINEAR)[16:64, 16:64].copy()
        self.icon = icon
        return self.AnalyzeResult([x,y,w,h], dict(captured=True, slot=slot, storage='Maa runtime memory'))


def relic_pipeline():
    """Resource/Context overrides only: native OCR and ColorMatch, no game assets."""
    nodes = {
        'LabyRelicTitle': _ocr('^获得道具的机会[！!]$', TITLE_ROI),
        'LabyRelicSubtitle': _ocr('^请选择要获得的迷宫遗物[。.]?$', SUBTITLE_ROI),
        'LabyRelicRemaining': _ocr(r'^还剩\s*1\s*次$', REMAINING_ROI),
        'LabyRelicGuardRequired': _ocr('^__qualified_relic_offer_required__$', TITLE_ROI),
    }
    names = ['LabyRelicTitle', 'LabyRelicSubtitle', 'LabyRelicRemaining']
    for slot in range(3):
        for kind, expected, roi in (
                ('Name', '.+', NAME_ROIS[slot]), ('Attribute', '.+', ATTRIBUTE_ROIS[slot]),
                ('Button', '^选择$', BUTTON_ROIS[slot])):
            name = f'LabyRelic{kind}{slot}'
            if kind == 'Name':nodes.update(_name_nodes(name, expected, roi))
            else:
                nodes[name] = _ocr(expected, roi)
                if kind == 'Attribute':nodes[name]['only_rec'] = True
            names.append(name)
        name = f'LabyRelicBright{slot}'
        nodes[name] = dict(recognition='ColorMatch', roi=list(BRIGHT_ROIS[slot]), method=40,
                           lower=[85, 70, 220], upper=[130, 255, 255], count=800,
                           connected=True, action='DoNothing', next=[], timeout=1500,
                           pre_delay=0, post_delay=0)
        names.append(name)
        for kind in ('Name', 'Attribute'):
            name = f'LabyRelicGuard{kind}{slot}0'
            if kind == 'Name':nodes.update(_name_nodes(name, '^__qualified_relic_offer_required__$', TITLE_ROI))
            else:nodes[name] = _ocr('^__qualified_relic_offer_required__$', TITLE_ROI)
    nodes['LabyRelicSelectedIconCapture'] = dict(recognition='Custom', custom_recognition='LabyRelicSelectedIconCapture',
        action='DoNothing', next=[], timeout=1500, pre_delay=0, post_delay=0)
    nodes['LabyRelicObtainedIcon'] = dict(recognition='Custom', custom_recognition='LabyRelicSelectedIconCapture',
        custom_recognition_param=dict(mode='obtained'), action='DoNothing', next=[],
        timeout=1500, pre_delay=0, post_delay=0)
    nodes['LabyRelicObtainedExactItem'] = _ocr(_pattern('__qualified_relic_name_required__×1'), RESULT_ITEM_ROI)
    nodes['LabyRelicObtainedIdentity'] = dict(recognition='Or',
        any_of=['LabyRelicObtainedExactItem','LabyRelicObtainedIcon'],
        action='DoNothing',next=[],timeout=1500,pre_delay=0,post_delay=0)
    nodes['LabyRelicObtainedIconNative'] = dict(recognition='TemplateMatch', template=_ICON_IMAGE,
        roi=list(RESULT_ICON_ROI), method=5, threshold=THRESHOLD, action='DoNothing',
        next=[], timeout=1500, pre_delay=0, post_delay=0)
    nodes['LabyRelicObserved'] = dict(recognition='And', all_of=names,
        action='DoNothing', next=[], timeout=3000, pre_delay=0, post_delay=0)
    # Deliberately unusable until all three offers are supplied to the exact guard.
    nodes['LabyRelicSelectOnce'] = dict(recognition='And', all_of=['LabyRelicGuardRequired'],
        action='Click', target=True, max_hit=1, next=[], timeout=2500, rate_limit=250,
        pre_delay=0, post_delay=0)
    nodes.update(result_pipeline())
    return nodes


@dataclass(frozen=True)
class Word:
    text: str
    box: tuple[int, int, int, int]
    score: float


@dataclass(frozen=True)
class RelicOffer:
    slot: int
    name: str
    mark: str
    bonus: int
    current: int
    name_words: tuple[Word, ...]
    attribute_words: tuple[Word, ...]
    button_words: tuple[Word, ...]


def _normalized(text):
    return re.sub(r'\s+', '', text.translate(str.maketrans({'（':'(', '）':')',
        '：':':', '＋':'+', '！':'!', '。':'.'})))


def _inside(box, roi):
    return (type(box) in (list, tuple) and len(box) == 4
        and all(type(v) is int for v in box) and box[2] > 0 and box[3] > 0
        and box[0] >= roi[0] and box[1] >= roi[1]
        and box[0] + box[2] <= roi[0] + roi[2]
        and box[1] + box[3] <= roi[1] + roi[3])


def _words(detail, roi, *, maximum=1):
    raw = detail.get('filtered') if type(detail) is dict else None
    if type(raw) is not list or not 1 <= len(raw) <= maximum:
        raise BatchStopped('relic_offers_unknown')
    words = []
    for word in raw:
        if type(word) is not dict:
            raise BatchStopped('relic_offers_unknown')
        text, box, score = word.get('text'), word.get('box'), word.get('score')
        if (type(text) is not str or not text.strip() or len(text) > 64
                or type(score) not in (int, float) or not math.isfinite(score)
                or not THRESHOLD <= score <= 1 or not _inside(box, roi)):
            raise BatchStopped('relic_offers_unknown')
        words.append(Word(text, tuple(box), float(score)))
    # Attribute components are a single horizontal line even when OCR y differs.
    words.sort(key=lambda w: (w.box[0], w.box[1]))
    if len({w.text for w in words}) != len(words):
        raise BatchStopped('relic_offers_ambiguous')
    for a, b in zip(words, words[1:]):
        if a.box[0] + a.box[2] > b.box[0] or abs(a.box[1] - b.box[1]) > 12:
            raise BatchStopped('relic_offers_ambiguous')
    return tuple(words)


def _attribute(words, *, require_current=True):
    text = _normalized(words if isinstance(words,str) else ''.join(w.text for w in words))
    # Upstream reads the mark/bonus and explicit 当前 counter independently.
    # OCR bracket duplication is not a different relic or stack count.
    marks=re.findall(r'([^\W\d_]{1,8})\+([1-9][0-9]?)(?![0-9.])',text)
    current=re.findall(r'当前[^0-9+\-]{0,5}([0-9]{1,3})(?![0-9.])',text)
    if len(marks)!=1 or require_current and len(current)!=1:
        raise BatchStopped('relic_attribute_unknown')
    mark = ALIASES.get(marks[0][0], marks[0][0])
    if mark not in MARKS:
        raise BatchStopped('relic_mark_unknown')
    return mark, int(marks[0][1]), int(current[0]) if len(current)==1 else None


def _offer(slot, name_words, attribute_words, button_words):
    name = _normalized(''.join(w.text for w in name_words))
    if not name or len(name) > 48 or any(ord(c) < 32 for c in name):
        raise BatchStopped('relic_name_unknown')
    if _normalized(''.join(w.text for w in button_words)) != '选择':
        raise BatchStopped('relic_button_unknown')
    mark, bonus, current = _attribute(attribute_words)
    return RelicOffer(slot, name, mark, bonus, current, name_words, attribute_words, button_words)


def _validate_offers(offers):
    if type(offers) not in (tuple, list) or len(offers) != 3:
        raise BatchStopped('relic_offers_unknown')
    parsed = []
    for slot, offer in enumerate(offers):
        if type(offer) is not RelicOffer or type(offer.slot) is not int or offer.slot != slot:
            raise BatchStopped('relic_offers_unknown')
        groups = []
        for words, roi, maximum in ((offer.name_words, NAME_ROIS[slot], 2),
                (offer.attribute_words, ATTRIBUTE_ROIS[slot], 2),
                (offer.button_words, BUTTON_ROIS[slot], 1)):
            if (type(words) is not tuple or any(type(w) is not Word or type(w.box) is not tuple
                    or not _inside(w.box, roi) for w in words)):
                raise BatchStopped('relic_offers_unknown')
            groups.append(_words(dict(filtered=[dict(text=w.text, box=list(w.box), score=w.score)
                for w in words]), roi, maximum=maximum))
        if (type(offer.name) is not str or type(offer.mark) is not str
                or type(offer.bonus) is not int or type(offer.current) is not int
                or _offer(slot, *groups) != offer):
            raise BatchStopped('relic_offers_unknown')
        parsed.append(offer)
    if len({o.name for o in parsed}) != 3:
        raise BatchStopped('relic_offers_ambiguous')
    observed = {}
    for offer in parsed:
        if offer.mark in observed and observed[offer.mark] != offer.current:
            raise BatchStopped('relic_stacks_conflicting')
        observed[offer.mark] = offer.current
    return tuple(parsed), observed


def relic_offers(receipt):
    """Parse one completed native And receipt, never partial/best-only OCR."""
    if type(receipt) is not dict or receipt.get('native_ok') is not True:
        raise BatchStopped('relic_offers_unknown')
    nodes = receipt.get('nodes')
    if type(nodes) is not list or any(type(n) is not dict for n in nodes):
        raise BatchStopped('relic_offers_unknown')
    matches = [n for n in nodes if n.get('name') == 'LabyRelicObserved']
    if len(matches) != 1 or matches[0].get('completed') is not True:
        raise BatchStopped('relic_offers_unknown')
    raw = matches[0].get('recognition')
    expected = set(relic_pipeline()['LabyRelicObserved']['all_of'])
    if (type(raw) is not list or any(type(r) is not dict or type(r.get('name')) is not str for r in raw)
            or len(raw) != len(expected) or {r.get('name') for r in raw} != expected):
        raise BatchStopped('relic_offers_unknown')
    rows = {r['name']:r.get('detail') for r in raw}
    for label, roi, text in (('Title', TITLE_ROI, '获得道具的机会!'),
            ('Subtitle', SUBTITLE_ROI, '请选择要获得的迷宫遗物.'),
            ('Remaining', REMAINING_ROI, '还剩1次')):
        words = _words(rows['LabyRelic'+label], roi)
        if _normalized(words[0].text).rstrip('.') != text.rstrip('.'):
            raise BatchStopped('relic_page_unknown')
    offers = []
    for slot in range(3):
        bright = rows[f'LabyRelicBright{slot}']
        values = bright.get('filtered') if type(bright) is dict else None
        if (type(values) is not list or len(values) != 1 or type(values[0]) is not dict
                or not _inside(values[0].get('box'), BRIGHT_ROIS[slot])
                or type(values[0].get('count')) is not int
                or not 800 <= values[0]['count'] <= values[0]['box'][2] * values[0]['box'][3]):
            raise BatchStopped('relic_button_unknown')
        name = rows[f'LabyRelicName{slot}']
        if type(name) is dict:
            values=name.get('filtered')
            if type(values) is list and len(values)==1 and type(values[0]) is dict and type(values[0].get('detail')) in (dict,list):
                name=values[0]['detail']
        if type(name) is list:
            # Maa Or short-circuits: earlier OCR alternatives missed, last hit.
            if (not 1 <= len(name) <= 2 or any(type(r) is not dict or r.get('algorithm') != 'OCR'
                    or r.get('name') != f'LabyRelicName{slot}'+suffix
                    for r, suffix in zip(name, ('Line', 'Detected')))
                    or any(r.get('box') is not None for r in name[:-1])):
                raise BatchStopped('relic_offers_unknown')
            name = name[-1].get('detail')
        offers.append(_offer(slot,
            _words(name, NAME_ROIS[slot], maximum=2),
            _words(rows[f'LabyRelicAttribute{slot}'], ATTRIBUTE_ROIS[slot], maximum=2),
            _words(rows[f'LabyRelicButton{slot}'], BUTTON_ROIS[slot])))
    return _validate_offers(offers)[0]


def choose_offer(offers, observed_stacks=None, *, locked_focus=None, area=None, priority=(), **policy):
    """Choose only if every saturated completion of missing stacks agrees on slot.

    Additional observed_stacks must be independently observed by the caller.
    Returning None means the strategy needs more stack evidence, not a fallback.
    """
    offers, stacks = _validate_offers(offers)
    if observed_stacks is not None:
        if (type(observed_stacks) is not dict or set(observed_stacks) - set(MARKS)
                or any(type(v) is not int or v < 0 for v in observed_stacks.values())):
            raise BatchStopped('relic_stacks_unknown')
        for mark, value in observed_stacks.items():
            if mark in stacks and stacks[mark] != value:
                raise BatchStopped('relic_stacks_conflicting')
            stacks[mark] = value
    if (locked_focus is not None and locked_focus not in MARKS
            or area is not None and (type(area) is not int or not 1 <= area <= 5)
            or type(priority) not in (tuple, list) or any(type(m) is not str for m in priority)
            or len(set(priority)) != len(priority) or set(priority) - set(MARKS)):
        raise BatchStopped('relic_strategy_unknown')
    # These labels are local visible slots, deliberately never protocol item IDs.
    candidates = tuple(Relic('screen-slot:'+str(o.slot), o.mark, o.bonus) for o in offers)
    missing = tuple(mark for mark in MARKS if mark not in stacks)
    selected = None
    for values in product(range(16), repeat=len(missing)):
        complete = dict(stacks);complete.update(zip(missing, values))
        choice = choose_relic(complete, candidates, locked_focus=locked_focus,
                              area=area, priority=tuple(priority), **policy)
        if choice is None:
            return None
        slot = next(o.slot for o, candidate in zip(offers, candidates) if candidate is choice.choice)
        if selected is not None and slot != selected:
            return None
        selected = slot
    return offers[selected] if selected is not None else None


def _pattern(text):
    # Full/half-width punctuation is the same semantic OCR identity; numbers,
    # names and marks stay exact. No broad "any relic" guard is enabled.
    characters = []
    for c in _normalized(text):
        characters.append({'(':'[（(]', ')':'[）)]', ':':'[:：]', '+':'[+＋]',
            '!':'[!！]', '.':'[.。]'}.get(c, re.escape(c)))
    return r'^\s*' + r'\s*'.join(characters) + r'\s*$'


def selection_override(offers, slot):
    """Build current identity guards for the selected offer; does not reset a spent Maa hit budget."""
    offers, _ = _validate_offers(offers)
    if type(slot) is not int or slot not in (0, 1, 2):
        raise BatchStopped('relic_guard_invalid')
    overrides = {};guards = []
    for offer in (offers[slot],):
        marks='|'.join(re.escape(mark) for mark in sorted({offer.mark}|{raw for raw,mark in ALIASES.items() if mark==offer.mark}))
        attribute=(r'^[^0-9]*(?:'+marks+r')\s*[+＋]\s*'+str(offer.bonus)
            +r'[^0-9]*当前[^0-9+\-]{0,5}'+str(offer.current)+r'(?![0-9])[^0-9]*$')
        for kind,pattern,roi in (('Name',_pattern(offer.name),NAME_ROIS[slot]),('Attribute',attribute,ATTRIBUTE_ROIS[slot])):
            name=f'LabyRelicGuard{kind}{offer.slot}0'
            if kind == 'Name':overrides.update(_name_nodes(name, pattern, roi))
            else:overrides[name]=dict(_ocr(pattern,roi), only_rec=True)
            guards.append(name)
    first = f'LabyRelicButton{slot}'
    rest = ['LabyRelicTitle', 'LabyRelicSubtitle', 'LabyRelicRemaining'] + guards
    rest += [f'LabyRelicBright{slot}']
    overrides['LabyRelicSelectOnce'] = dict(recognition='And', all_of=[first]+rest,
        action='Click', target=True, max_hit=1, next=[], timeout=2500, rate_limit=250,
        pre_delay=0, post_delay=0)
    return overrides


@contextmanager
def selection_guard(tasker, offers, slot, *, icon=False):
    """Restore scoped overrides; caller owns durable intent and fresh readback."""
    overrides = selection_override(offers, slot)
    if icon:
        overrides['LabyRelicSelectedIconCapture'] = dict(custom_recognition_param=dict(slot=slot))
        overrides['LabyRelicSelectOnce']['all_of'].append('LabyRelicSelectedIconCapture')
    resource = tasker.resource
    original = {name:resource.get_node_data(name) for name in overrides}
    if any(not value for value in original.values()) or not resource.override_pipeline(overrides):
        raise BatchStopped('relic_guard_failed')
    try:
        yield
    except BaseException:
        # Preserve the original failure if a blocked borrowed Context cannot
        # restore; that facade already prohibits all further input.
        try:
            resource.override_pipeline(original)
        except Exception:
            pass
        raise
    else:
        if not resource.override_pipeline(original):
            raise BatchStopped('relic_guard_restore_failed')


RESULT_TITLE_ROI = (540, 33, 205, 43)
RESULT_MESSAGE_ROI = (505, 83, 280, 40)
RESULT_ITEM_ROI = (450, 174, 380, 57)
RESULT_CLOSE_ROI = (500, 608, 278, 65)


def _result_name(selected):
    value = selected.name if type(selected) is RelicOffer else selected
    if (type(value) is not str or not value.strip() or len(value) > 48
            or any(ord(c) < 32 for c in value) or '×' in value):
        raise BatchStopped('relic_identity_required')
    return _normalized(value)


def result_pipeline(selected=None):
    """Exact obtained modal and one-shot close; closing has no navigation next."""
    name = '__qualified_relic_name_required__' if selected is None else _result_name(selected)
    nodes = {
        'LabyRelicObtainedTitle': _ocr('^获得道具$', RESULT_TITLE_ROI),
        'LabyRelicObtainedMessage': _ocr('^获得了以下道具[。.]?$', RESULT_MESSAGE_ROI),
        'LabyRelicObtainedItem': _ocr(_pattern(name+'×1'), RESULT_ITEM_ROI),
        'LabyRelicObtainedClose': _ocr('^关闭$', RESULT_CLOSE_ROI),
        # One fresh, read-only observation; the caller classifies the next page.
        # This does not assert that the modal closed, or that a map was reached.
        'LabyRelicFollowingObserved': _ocr('.+', (0, 0, 1280, 720)),
    }
    observed = ['LabyRelicObtainedTitle', 'LabyRelicObtainedMessage',
                'LabyRelicObtainedItem', 'LabyRelicObtainedClose']
    nodes['LabyRelicObtainedObserved'] = dict(recognition='And', all_of=observed,
        action='DoNothing', next=[], timeout=3000, pre_delay=0, post_delay=0)
    nodes['LabyRelicObtainedCloseOnce'] = dict(recognition='And',
        all_of=['LabyRelicObtainedClose']+observed[:-1], action='Click', target=True,
        max_hit=1, next=[], timeout=2500, rate_limit=250, pre_delay=0, post_delay=0)
    nodes['LabyRelicResultCloseOnce']=dict(recognition='And',all_of=['LabyRelicObtainedClose','LabyRelicObtainedTitle','LabyRelicObtainedMessage'],action='Click',target=True,max_hit=1,next=[],timeout=2500,pre_delay=0,post_delay=0)
    return nodes


def relic_obtained(receipt, selected, *, icon=False):
    """Independent same-frame identity and item ×1 proof."""
    name = _result_name(selected)
    if type(receipt) is not dict or receipt.get('native_ok') is not True:
        raise BatchStopped('relic_result_unknown')
    nodes = receipt.get('nodes')
    if type(nodes) is not list or any(type(n) is not dict for n in nodes):
        raise BatchStopped('relic_result_unknown')
    found = [n for n in nodes if n.get('name') == 'LabyRelicObtainedObserved']
    if len(found) != 1 or found[0].get('completed') is not True:
        raise BatchStopped('relic_result_unknown')
    raw = found[0].get('recognition')
    expected = set(result_pipeline()['LabyRelicObtainedObserved']['all_of'])
    if icon:expected.add('LabyRelicObtainedIdentity')
    if (type(raw) is not list or any(type(r) is not dict or type(r.get('name')) is not str for r in raw)
            or len(raw) != len(expected) or {r['name'] for r in raw} != expected):
        raise BatchStopped('relic_result_unknown')
    rows = {r['name']:r.get('detail') for r in raw}
    try:
        for label, roi, texts in (
                ('Title', RESULT_TITLE_ROI, ('获得道具',)),
                ('Message', RESULT_MESSAGE_ROI, ('获得了以下道具.', '获得了以下道具')),
                ('Close', RESULT_CLOSE_ROI, ('关闭',))):
            words = _words(rows['LabyRelicObtained'+label], roi)
            if _normalized(words[0].text) not in texts:
                raise BatchStopped('relic_result_unknown')
        item = _words(rows['LabyRelicObtainedItem'], RESULT_ITEM_ROI, maximum=2)
    except BatchStopped:
        raise BatchStopped('relic_result_unknown') from None
    text = _normalized(''.join(w.text for w in item))
    identity_method = 'exact_ocr'
    if icon:
        identity = rows['LabyRelicObtainedIdentity']
        names = ['LabyRelicObtainedExactItem','LabyRelicObtainedIcon']
        if (type(identity) is not list or not 1<=len(identity)<=2
                or any(type(row) is not dict for row in identity)
                or [row.get('name') for row in identity] != names[:len(identity)]
                or any(row.get('box') is not None for row in identity[:-1])):
            raise BatchStopped('relic_result_unknown')
        if identity[-1]['name'] == names[0]:
            exact = _words(identity[-1].get('detail'), RESULT_ITEM_ROI, maximum=2)
            if _normalized(''.join(w.text for w in exact)) != name+'×1':
                raise BatchStopped('relic_result_mismatch')
        else:
            identity_method = 'native_observed_icon'
            raw = identity[-1].get('detail')
            observed = raw.get('filtered', []) if type(raw) is dict else []
            detail = observed[0].get('detail') if type(observed) is list and len(observed)==1 and type(observed[0]) is dict else None
            match = detail.get('match') if type(detail) is dict else None
            matches = match.get('filtered', []) if type(match) is dict else []
            if (type(matches) is not list or len(matches) != 1 or type(matches[0]) is not dict
                    or not _inside(matches[0].get('box'), RESULT_ICON_ROI)
                    or type(matches[0].get('score')) not in (int,float)
                    or not math.isfinite(matches[0]['score']) or matches[0]['score'] < THRESHOLD):
                raise BatchStopped('relic_result_mismatch')
        if re.fullmatch(r'.+×1', text) is None:
            raise BatchStopped('relic_result_mismatch')
    elif text != name+'×1':
        raise BatchStopped('relic_result_mismatch')
    return dict(verified=True, relic_name=name, observed_result_text=text, quantity=1,
                inventory_added=True, identity_method=identity_method, native=receipt)


@contextmanager
def obtained_guard(tasker, selected, *, icon=False):
    overrides = {'LabyRelicObtainedItem':dict(expected=r'^.+[×]\s*1$' if icon else _pattern(_result_name(selected)+'×1'))}
    if icon:
        overrides['LabyRelicObtainedExactItem'] = dict(expected=_pattern(_result_name(selected)+'×1'))
        overrides['LabyRelicObtainedObserved'] = dict(all_of=result_pipeline()['LabyRelicObtainedObserved']['all_of']+['LabyRelicObtainedIdentity'])
    resource = tasker.resource
    original = {key:resource.get_node_data(key) for key in overrides}
    if any(not value for value in original.values()) or not resource.override_pipeline(overrides):
        raise BatchStopped('relic_result_guard_failed')
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
            raise BatchStopped('relic_result_guard_restore_failed')


class MaaRelicReward:
    """One durable selection intent, verified acquisition, dedicated modal close.

    A new reward can rearm only its selection/close nodes after its new intent is
    persisted. The same reward, unknown result or pending journal is never sent
    again. The next page stays unclassified: area rewards are not maps.
    """
    def __init__(self, tasker, stamp, *, timings=None):
        from .timing import Timings
        self.tasker, self.stamp = tasker, stamp
        self.timings = timings or Timings()
        self.attempted = set()
        from .scene import runtime_resource
        resource = runtime_resource(tasker)
        self.icon_capture = getattr(resource, '_labyrinth_relic_icon_capture', None)
        if self.icon_capture is None:
            self.icon_capture = RelicIconCapture()
            if not resource.register_custom_recognition('LabyRelicSelectedIconCapture', self.icon_capture):
                raise BatchStopped('relic_runtime_unavailable')
            resource._labyrinth_relic_icon_capture = self.icon_capture

    def _run(self, node, *, input=False):
        from .. import native
        with self.timings.measure('page_wait'):
            receipt = native.run(self.tasker, node, reset_hits=(node,) if input else ())
        nodes = receipt.get('nodes') if type(receipt) is dict else None
        matches = [n for n in nodes if type(n) is dict and n.get('name') == node] if type(nodes) is list else []
        if (type(receipt) is not dict or receipt.get('native_ok') is not True
                or len(matches) != 1 or matches[0].get('completed') is not True
                or input and (type(matches[0].get('action')) is not dict
                              or matches[0]['action'].get('success') is not True)):
            raise BatchStopped('relic_input_unknown' if input else 'relic_result_unknown')
        return receipt

    def choose(self, batch, enter_id, reward_id, area, observed_stacks=None, *, locked_focus=None, priority=(), offers=None):
        if (type(enter_id) is not int or enter_id <= 0 or type(reward_id) is not str
                or not reward_id or len(reward_id) > 128 or any(ord(c) < 32 for c in reward_id)):
            raise BatchStopped('relic_reward_identity_required')
        key = (enter_id, reward_id)
        if key in self.attempted or any(e.get('operation')=='relic_reward_select'
                and e.get('binding')==batch.binding.payload()
                and (e.get('enter_id'),e.get('reward_id'))==key for e in batch.journal.events()):
            raise BatchStopped('relic_reward_already_attempted')
        batch.checkpoint()
        if offers is None:offers = relic_offers(self._run('LabyRelicObserved'))
        else:offers,_ = _validate_offers(offers)
        from .roles import strategy_value
        strategy=getattr(batch.settings,'strategy',None)
        policy={key:strategy_value(strategy,key,default) for key,default in (('baseline_last_area',3),('baseline_target',4),('debuff_pivot_minimum',12),('debuff_pivot_tolerance',2))}
        priority=priority or strategy_value(strategy,'relic_mark_priority',())
        with self.timings.measure('strategy'):
            selected = choose_offer(offers, observed_stacks, area=area,
                                    locked_focus=locked_focus, priority=priority, **policy)
        if selected is None:
            raise BatchStopped('relic_stacks_incomplete')
        fields = dict(enter_id=enter_id, reward_id=reward_id, slot=selected.slot,
                      relic_name=selected.name, mark=selected.mark, bonus=selected.bonus,
                      quantity=1)
        self.attempted.add(key)
        def send(operation_id):
            batch.checkpoint()
            selection = self._run('LabyRelicSelectOnce', input=True)
            return self.stamp(operation_id=operation_id, **fields,native=selection,
                input_native=selection,input_action_verified=True)
        with selection_guard(self.tasker, offers, selected.slot, icon=True):
            clicked=batch.transaction('relic_reward_select',fields,send)
        batch.checkpoint()
        with obtained_guard(self.tasker, selected, icon=True):
            obtained = relic_obtained(self._run('LabyRelicObtainedObserved'), selected, icon=True)
        confirmed=batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,
            inventory_added=True,input_native=clicked['input_native'],input_action_verified=True,
            native=dict(selection=clicked['native'],obtained=obtained)),**fields,inventory_added=True)
        close_fields=dict(enter_id=enter_id,reward_id=reward_id,phase='reward_close')
        def close_result(oid):
            batch.checkpoint();close=self._run('LabyRelicResultCloseOnce',input=True)
            return self.stamp(operation_id=oid,**close_fields,native=close,input_native=close,
                input_action_verified=True,client_page_verified=False)
        closed=batch.transaction('relic_reward_close',close_fields,close_result)
        batch.checkpoint();following=self._run('LabyRelicFollowingObserved')
        batch.observation(self.stamp(**close_fields,native=following,client_page_verified=False),**close_fields)
        calibrated=dict(observed_stacks or {});calibrated.update({o.mark:o.current for o in offers})
        decision=choose_relic(calibrated,[Relic(str(o.slot),o.mark,o.bonus) for o in offers],locked_focus=locked_focus,area=area,priority=priority,**policy)
        return dict(confirmed,closed_reward_signature=item_reward_values(obtained['observed_result_text'])['signature'],
            focus=locked_focus if decision.phase=='baseline' else decision.focus,native=dict(confirmed['native'],close=closed['native'],
            following_observation=following,following_page='unclassified'))


# Read-only fixed upstream compact-map calibration. It has five mark slots;
# missing/clipped counters stay unknown. No panel is opened by these readers.
MAP_MARK_HUES={'强化':16.,'会心':50.,'守备':112.,'加速':200.,'弱体':285.}
MAP_MARK_ROIS=tuple((round((310+i*88)*2/3),620,round(82*2/3),round(110*2/3)) for i in range(5))
MAP_DIGIT_ROIS=tuple((round((360+i*88)*2/3),round(970*2/3),32,32) for i in range(5))
MAP_TEMPLATE_DIGIT_ROIS=tuple((round((380+i*88)*2/3),652,21,29) for i in range(5))
BUSINESS_NAME='LabyrinthRelicBusinessRead'


def relic_business_pipeline():
    # Fixed upstream OCR_SCALE=4; native OCR reads the same cropped counter.
    nodes={'LabyRelicMapDigitScaled':_ocr(r'^[0-9]{1,2}$',(0,0,128,128))}
    for suffix,pattern,roi in (('Title','^获得道具$',RESULT_TITLE_ROI),('Message','^获得了以下道具[。.]?$',RESULT_MESSAGE_ROI),('Item','.+',RESULT_ITEM_ROI),('Close','^关闭$',RESULT_CLOSE_ROI)):
        nodes['LabyRelicItemRead'+suffix]=_ocr(pattern,roi)
    for mode in ('map','item'):
        nodes['LabyRelicBusiness'+mode.title()]=dict(recognition='Custom',custom_recognition=BUSINESS_NAME,custom_recognition_param=dict(mode=mode),roi=[0,0,1280,720],action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)
    return nodes


def map_relic_mark(image,roi):
    """Pinned NodeRelicStackResolver colour votes/panel support, one current image."""
    import numpy as np
    x,y,w,h=roi;hsv=cv2.cvtColor(image[y:y+h,x:x+w,:3].astype(np.float32)/255,cv2.COLOR_BGR2HSV)
    strip=hsv[round(h*.82)::3,::3]
    if strip.size==0 or np.mean((strip[:,:,2]>=.58)&(strip[:,:,1]<=.38))<.32:return None
    samples=hsv[h//10:h*68//100:3,w//10:w*75//100:3]
    colored=samples[(samples[:,:,1]>=.45)&(samples[:,:,2]>=.30)]
    if samples.size==0 or len(colored)/(samples.shape[0]*samples.shape[1])<.18:return None
    votes={mark:0 for mark in MAP_MARK_HUES}
    for hue in colored[:,0]:
        distances={mark:min(abs(float(hue)-center),360-abs(float(hue)-center)) for mark,center in MAP_MARK_HUES.items()}
        mark=min(distances,key=distances.get)
        if distances[mark]<=48:votes[mark]+=1
    mark=max(votes,key=votes.get)
    return mark if votes[mark]/len(colored)>=.65 else None


def map_small_counter(image,roi):
    """Pinned compact-map 1-3 enclosed white glyphs and original .40/.08 limits."""
    import numpy as np
    x,y,w,h=roi;crop=image[y:y+h,x:x+w,:3]
    raw=(crop.min(axis=2)>=220).astype(np.uint8)
    count,labels,stats,_=cv2.connectedComponentsWithStats(raw,connectivity=4)
    enclosed=[i for i in range(1,count) if stats[i,4]>=2 and stats[i,0]>0 and stats[i,1]>0
        and stats[i,0]+stats[i,2]<w and stats[i,1]+stats[i,3]<h]
    normalized=np.zeros((24,24),bool)
    if enclosed:
        selected=max(enclosed,key=lambda i:stats[i,4]);left,top,sw,sh,_=stats[selected]
        scale=min(20/sw,20/sh);tw=int(sw*scale+.5);th=int(sh*scale+.5)
        glyph=(labels[top:top+sh,left:left+sw]==selected).astype(np.uint8)
        normalized[(24-th)//2:(24-th)//2+th,(24-tw)//2:(24-tw)//2+tw]=cv2.resize(glyph,(tw,th),interpolation=cv2.INTER_NEAREST).astype(bool)
    # Exact upstream 24x24 patterns, compressed as (first row,last row,x,width).
    patterns={1:((2,4,13,1),(5,5,12,2),(6,6,9,5),(7,7,11,3),(8,21,12,2)),
        2:((2,6,11,4),(7,15,13,4),(16,17,11,4),(18,19,9,6),(20,21,6,5)),
        3:((2,4,14,2),(5,6,14,4),(7,15,16,4),(16,17,14,4),(18,19,11,5),(20,21,4,10))}
    scores=[]
    for digit,spans in patterns.items():
        template=np.zeros((24,24),bool)
        for first,last,left,width in spans:template[first:last+1,left:left+width]=True
        scores.append((float(np.count_nonzero(normalized&template))/np.count_nonzero(normalized|template),digit))
    scores.sort(reverse=True);score,digit=scores[0];margin=score-scores[1][0]
    return dict(value=digit if score>=.40 and margin>=.08 else None,score=score,margin=margin)


def map_relic_values(image,digits,*,digit_rois=MAP_DIGIT_ROIS,templates=None):
    observed={mark:None for mark in MARKS};conflicts=set();slots=[]
    for i,roi in enumerate(MAP_MARK_ROIS):
        mark=map_relic_mark(image,roi)
        if mark is None:continue
        value=None
        try:
            words=_words(digits.get(i),digit_rois[i]);text=words[0].text.strip()
            if re.fullmatch(r'[0-9]{1,2}',text) and 0<=int(text)<=30:value=int(text)
        except BatchStopped:pass
        template=(templates or {}).get(i,{}).get('value')
        if template is not None:
            value=None if value in (1,2,3) and value!=template else template if value is None else value
        slots.append(dict(slot=i,mark=mark,value=value))
        if value is not None:
            if observed[mark] is not None and observed[mark]!=value:conflicts.add(mark)
            observed[mark]=value
    for mark in conflicts:observed[mark]=None
    values={mark:value for mark,value in observed.items() if value is not None}
    return dict(values=values,observed=observed,slots=slots,signature=tuple(sorted(values.items())))


def map_relic_consensus(previous,current):
    """Two independent read-only results agree on values, never pixels or OCR boxes."""
    left=previous.get('values',{}) if isinstance(previous,dict) else {}
    right=current.get('values',{}) if isinstance(current,dict) else {}
    return {mark:value for mark,value in right.items() if mark in MARKS and type(value) is int and 0<=value<=30 and left.get(mark)==value}


def item_reward_values(text):
    """Actual result rows; translated names alone never supply relic metadata."""
    rows=tuple(_normalized(row) for row in text.splitlines() if row.strip())
    acquisitions=[];items=[]
    for row in rows:
        name=row;quantity=None
        match=re.fullmatch(r'(.+)×([1-9][0-9]{0,18})',row)
        if match and int(match[2])<=2**63-1:
            name,quantity=match[1],int(match[2])
            try:mark,bonus,_=_attribute(name,require_current=False)
            except BatchStopped:pass
            else:
                if 1<=bonus<=30:acquisitions.append(dict(name=name,mark=mark,bonus=bonus,quantity=quantity,rarity=None))
        items.append(dict(name=name,quantity=quantity))
    return dict(signature=('获得道具',*rows) if rows else None,item_text='\n'.join(rows),
        item_name=items[0]['name'] if len(items)==1 else None,quantity=items[0]['quantity'] if len(items)==1 else None,
        items=items,relic_acquisitions=acquisitions)


def _item_body_words(raw):
    # Current-frame line grouping reuses strict native word validation; no
    # cross-frame OCR rectangle or pixel comparison enters the signature.
    words=raw.get('filtered') if isinstance(raw,dict) else None
    if not isinstance(words,list) or not 1<=len(words)<=16:raise BatchStopped('item_body_unknown')
    rows=[]
    for word in sorted(words,key=lambda w:(w.get('box',[0,0])[1],w.get('box',[0])[0])):
        if not isinstance(word,dict) or not _inside(word.get('box'),RESULT_ITEM_ROI):raise BatchStopped('item_body_unknown')
        if not rows or abs(word['box'][1]-rows[-1][0]['box'][1])>12:rows.append([])
        rows[-1].append(word)
    return '\n'.join(''.join(word.text for word in _words(dict(filtered=row),RESULT_ITEM_ROI,maximum=8)) for row in rows)


class RelicBusinessRecognition(CustomRecognition):
    def __init__(self):super().__init__();self.stopped=None

    def analyze(self,context,argv):
        import json
        from .autoplay import _detail
        if self.stopped is not None and self.stopped():return None
        if tuple(argv.image.shape[:2])!=(720,1280):return None
        params=json.loads(argv.custom_recognition_param or '{}')
        if params.get('mode')=='map':
            scene=context.run_recognition('LabySceneObserved',argv.image)
            page=_detail(getattr(scene,'raw_detail',None))
            if getattr(scene,'hit',False) is not True or not page or page.get('kind')!='map':return None
            digits={}
            for i,(x,y,w,h) in enumerate(MAP_DIGIT_ROIS):
                cropped=cv2.resize(argv.image[y:y+h,x:x+w],(w*4,h*4),interpolation=cv2.INTER_LINEAR)
                digits[i]=getattr(context.run_recognition('LabyRelicMapDigitScaled',cropped),'raw_detail',None)
            templates={i:map_small_counter(argv.image,roi) for i,roi in enumerate(MAP_TEMPLATE_DIGIT_ROIS)}
            value=map_relic_values(argv.image,digits,digit_rois=((0,0,128,128),)*5,templates=templates)
            value.update(digit_native=digits,digit_templates=templates)
        elif params.get('mode')=='item':
            for suffix,roi,texts in (('Title',RESULT_TITLE_ROI,('获得道具',)),('Message',RESULT_MESSAGE_ROI,('获得了以下道具','获得了以下道具.')),('Close',RESULT_CLOSE_ROI,('关闭',))):
                result=context.run_recognition('LabyRelicItemRead'+suffix,argv.image)
                try:words=_words(getattr(result,'raw_detail',None),roi)
                except BatchStopped:return None
                if getattr(result,'hit',False) is not True or _normalized(words[0].text) not in texts:return None
            result=context.run_recognition('LabyRelicItemReadItem',argv.image)
            try:text=_item_body_words(getattr(result,'raw_detail',None))
            except (BatchStopped,AttributeError,TypeError,IndexError):return None
            if getattr(result,'hit',False) is not True:return None
            value=item_reward_values(text)
        else:return None
        return self.AnalyzeResult([0,0,1280,720],dict(value,status='observed',same_frame=True))


def _read_relic_business(tasker,batch,mode,*,timings=None):
    from .. import native
    from .scene import runtime_resource
    from .autoplay import _detail
    resource=runtime_resource(tasker);observer=getattr(resource,'_labyrinth_relic_business_observer',None)
    if observer is None:
        observer=RelicBusinessRecognition()
        if not resource.override_pipeline(relic_business_pipeline()) or not resource.register_custom_recognition(BUSINESS_NAME,observer):raise BatchStopped('relic_read_install_failed')
        resource._labyrinth_relic_business_observer=observer
    observer.stopped=batch.stopped;batch.checkpoint()
    receipt=native.run(tasker,'LabyRelicBusiness'+mode.title());batch.checkpoint()
    node=next((row for row in receipt.get('nodes',[]) if row.get('name')=='LabyRelicBusiness'+mode.title() and row.get('completed') is True),None)
    value=_detail(node.get('recognition')) if receipt.get('native_ok') is True and node is not None else None
    if not value or value.get('status')!='observed':value=dict(values={},observed={mark:None for mark in MARKS},signature=None,relic_acquisitions=[])
    return dict(value,native=receipt)


def read_map_relic_stacks(tasker,batch,*,timings=None):return _read_relic_business(tasker,batch,'map',timings=timings)

def read_item_reward(tasker,batch,*,timings=None):return _read_relic_business(tasker,batch,'item',timings=timings)
