"""Maa link-imprint offers, fixed upstream priority, and exact one-shot input.

Attribute priority is ported from LabyrinthLinkChoiceRecognition.kt
(CC BY-NC-SA 4.0): fire > dark > light > wind > water. It uses no roster,
relic, pool, or team-quality inputs. The upstream session's unknown first-slot
fallback is excluded: incomplete/unknown choices stop here.

This layout supports three distinct observed attributes. Complete positive
quantities and remaining-choice counts are read from the current frame. Random imprints are not confirmed recruited roles.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import re
from .batch import BatchStopped
from .relics import (Word, THRESHOLD, TITLE_ROI, SUBTITLE_ROI, REMAINING_ROI,
                     _ocr, _words, _normalized, _pattern, _inside, RESULT_TITLE_ROI,
                     RESULT_MESSAGE_ROI, RESULT_ITEM_ROI, RESULT_CLOSE_ROI)

ELEMENT_PRIORITY = ('火', '暗', '光', '风', '水')
def _positive(value):
    return type(value) is int and 1<=value<=2**63-1

def _positive_text(value):
    if type(value) is not str or re.fullmatch(r'[1-9][0-9]{0,18}',value) is None:
        raise BatchStopped('link_quantity_unknown')
    result=int(value)
    if not _positive(result):raise BatchStopped('link_quantity_unknown')
    return result
NAME_ROIS = tuple((x,454,215,41) for x in (140,510,880))
# Full card text line, including all quantity digits; never crop its last digit.
QUANTITY_ROIS = tuple((x,450,275,47) for x in (135,505,875))
BUTTON_ROIS = tuple((x+40,542,195,92) for x in (135,505,875))
BRIGHT_ROIS = tuple((x+85,552,100,14) for x in (135,505,875))


@dataclass(frozen=True)
class LinkOffer:
    slot: int
    element: str
    quantity: int
    quantity_marker: str
    name_words: tuple[Word, ...]
    quantity_words: tuple[Word, ...]
    button_words: tuple[Word, ...]
    remaining: int = 1


def link_pipeline():
    nodes = {
        'LabyLinkTitle':_ocr('^获得道具的机会[！!]$',TITLE_ROI),
        'LabyLinkSubtitle':_ocr('^请选择要获得的连结印记[。.]$',SUBTITLE_ROI),
        'LabyLinkRemaining':_ocr(r'^还剩\s*[1-9][0-9]{0,18}\s*次$',REMAINING_ROI),
        'LabyLinkGuardRequired':_ocr('^__qualified_link_offer_required__$',TITLE_ROI),
    }
    observed = ['LabyLinkTitle','LabyLinkSubtitle','LabyLinkRemaining']
    for slot in range(3):
        for kind,expected,roi in (('Name',r'^[火暗光风水]属性的随机印记$',NAME_ROIS[slot]),
                ('Quantity',r'^[火暗光风水]属性的随机印记[×?？xX*]?[0-9]+$',QUANTITY_ROIS[slot]),('Button','^选择$',BUTTON_ROIS[slot])):
            name=f'LabyLink{kind}{slot}';nodes[name]=_ocr(expected,roi)
            if kind=='Quantity':nodes[name]['only_rec']=True
            observed.append(name)
        bright=f'LabyLinkBright{slot}'
        nodes[bright]=dict(recognition='ColorMatch',roi=list(BRIGHT_ROIS[slot]),method=40,
            lower=[85,70,220],upper=[130,255,255],count=800,connected=True,
            action='DoNothing',next=[],timeout=1500,pre_delay=0,post_delay=0)
        observed.append(bright)
        for part in range(2):
            nodes[f'LabyLinkGuardName{slot}{part}']=_ocr('^__qualified_link_offer_required__$',TITLE_ROI)
        nodes[f'LabyLinkGuardQuantity{slot}']=_ocr('^__qualified_link_offer_required__$',TITLE_ROI)
    nodes['LabyLinkObserved']=dict(recognition='And',all_of=observed,action='DoNothing',next=[],
        timeout=3000,pre_delay=0,post_delay=0)
    nodes['LabyLinkSelectOnce']=dict(recognition='And',all_of=['LabyLinkGuardRequired'],
        action='Click',target=True,max_hit=1,next=[],timeout=2500,rate_limit=250,
        pre_delay=0,post_delay=0)
    return nodes


def _link_words(detail,roi,*,maximum=1):
    try:return _words(detail,roi,maximum=maximum)
    except BatchStopped:raise BatchStopped('link_offers_unknown') from None


def _offer(slot,name_words,quantity_words,button_words,remaining=1):
    text=_normalized(''.join(w.text for w in name_words))
    element=re.fullmatch(r'([火暗光风水])属性的随机印记',text)
    if element is None:raise BatchStopped('link_element_unknown')
    quantity=_normalized(''.join(w.text for w in quantity_words))
    count=re.fullmatch(r'([火暗光风水])属性的随机印记([×?？xX*]?)([0-9]+)',quantity)
    if count is None or count[1]!=element[1]:
        raise BatchStopped('link_quantity_unknown')
    if _normalized(''.join(w.text for w in button_words))!='选择':
        raise BatchStopped('link_button_unknown')
    return LinkOffer(slot,element[1],_positive_text(count[3]),count[2],name_words,quantity_words,button_words,remaining)


def _validate_offers(offers):
    if type(offers) not in (tuple,list) or len(offers)!=3:
        raise BatchStopped('link_offers_unknown')
    for slot,offer in enumerate(offers):
        if (type(offer) is not LinkOffer or type(offer.slot) is not int or offer.slot!=slot
                or type(offer.element) is not str or type(offer.quantity) is not int
                or type(offer.quantity_marker) is not str or not _positive(offer.remaining)):
            raise BatchStopped('link_offers_unknown')
        groups=[]
        for words,roi,maximum in ((offer.name_words,NAME_ROIS[slot],2),
                (offer.quantity_words,QUANTITY_ROIS[slot],1),(offer.button_words,BUTTON_ROIS[slot],1)):
            if (type(words) is not tuple or any(type(w) is not Word or type(w.box) is not tuple
                    or not _inside(w.box,roi) for w in words)):
                raise BatchStopped('link_offers_unknown')
            groups.append(_link_words(dict(filtered=[dict(text=w.text,box=list(w.box),score=w.score)
                for w in words]),roi,maximum=maximum))
        if _offer(slot,*groups,remaining=offer.remaining)!=offer:raise BatchStopped('link_offers_unknown')
    if len({o.remaining for o in offers})!=1:raise BatchStopped('link_remaining_unknown')
    if len({o.element for o in offers})!=3:raise BatchStopped('link_elements_ambiguous')
    return tuple(offers)


def link_offers(receipt):
    """One same-frame native page; quantities are read independently of × OCR."""
    if type(receipt) is not dict or receipt.get('native_ok') is not True:
        raise BatchStopped('link_offers_unknown')
    nodes=receipt.get('nodes')
    if type(nodes) is not list or any(type(n) is not dict for n in nodes):
        raise BatchStopped('link_offers_unknown')
    found=[n for n in nodes if n.get('name')=='LabyLinkObserved']
    if len(found)!=1 or found[0].get('completed') is not True:
        raise BatchStopped('link_offers_unknown')
    raw=found[0].get('recognition');expected=set(link_pipeline()['LabyLinkObserved']['all_of'])
    if (type(raw) is not list or any(type(r) is not dict or type(r.get('name')) is not str for r in raw)
            or len(raw)!=len(expected) or {r['name'] for r in raw}!=expected):
        raise BatchStopped('link_offers_unknown')
    rows={r['name']:r.get('detail') for r in raw}
    for label,roi,text in (('Title',TITLE_ROI,'获得道具的机会!'),
            ('Subtitle',SUBTITLE_ROI,'请选择要获得的连结印记.')):
        words=_link_words(rows['LabyLink'+label],roi)
        if _normalized(words[0].text)!=text:raise BatchStopped('link_page_unknown')
    words=_link_words(rows['LabyLinkRemaining'],REMAINING_ROI)
    match=re.fullmatch(r'还剩([1-9][0-9]{0,18})次',_normalized(words[0].text))
    if match is None:raise BatchStopped('link_remaining_unknown')
    remaining=_positive_text(match[1])
    offers=[]
    for slot in range(3):
        bright=rows[f'LabyLinkBright{slot}']
        values=bright.get('filtered') if type(bright) is dict else None
        if (type(values) is not list or len(values)!=1 or type(values[0]) is not dict
                or not _inside(values[0].get('box'),BRIGHT_ROIS[slot])
                or type(values[0].get('count')) is not int
                or not 800<=values[0]['count']<=values[0]['box'][2]*values[0]['box'][3]):
            raise BatchStopped('link_button_unknown')
        offers.append(_offer(slot,_link_words(rows[f'LabyLinkName{slot}'],NAME_ROIS[slot],maximum=2),
            _link_words(rows[f'LabyLinkQuantity{slot}'],QUANTITY_ROIS[slot]),
            _link_words(rows[f'LabyLinkButton{slot}'],BUTTON_ROIS[slot]),remaining))
    return _validate_offers(offers)


def preferred_choice(offers,ordered_elements=ELEMENT_PRIORITY):
    """Upstream fixed-attribute policy; no unknown first-slot fallback.

    Unique recognized attributes make the upstream same-attribute confidence
    tiebreaker unnecessary. A full five-element permutation mirrors its config.
    """
    offers=_validate_offers(offers)
    if (type(ordered_elements) not in (tuple,list) or len(ordered_elements)!=5
            or any(type(e) is not str for e in ordered_elements)
            or set(ordered_elements)!=set(ELEMENT_PRIORITY)):
        raise BatchStopped('link_priority_unknown')
    return min(offers,key=lambda offer:ordered_elements.index(offer.element))


def selection_override(offers,slot):
    offers=_validate_offers(offers)
    if type(slot) is not int or slot not in (0,1,2):raise BatchStopped('link_guard_invalid')
    overrides={'LabyLinkRemaining':_ocr(r'^还剩\s*'+str(offers[0].remaining)+r'\s*次$',REMAINING_ROI)};guards=[]
    for offer in (offers[slot],):
        groups=[(f'LabyLinkGuardName{offer.slot}{part}',offer.name_words[min(part,len(offer.name_words)-1)])
                for part in range(2)]
        groups.append((f'LabyLinkGuardQuantity{offer.slot}',offer.quantity_words[0]))
        for name,word in groups:
            x,y,w,h=word.box
            roi=QUANTITY_ROIS[offer.slot] if 'Quantity' in name else NAME_ROIS[offer.slot]
            overrides[name]=_ocr(_pattern(word.text),roi)
            overrides[name]['only_rec']=False;guards.append(name)
    first=f'LabyLinkButton{slot}'
    rest=['LabyLinkTitle','LabyLinkSubtitle','LabyLinkRemaining']+guards
    rest += [f'LabyLinkBright{slot}']
    overrides['LabyLinkSelectOnce']=dict(recognition='And',all_of=[first]+rest,
        action='Click',target=True,max_hit=1,next=[],timeout=2500,rate_limit=250,
        pre_delay=0,post_delay=0)
    return overrides


@contextmanager
def selection_guard(tasker,offers,slot):
    """Fresh selected-card identity guard; caller owns the durable intent/result reader."""
    overrides=selection_override(offers,slot);resource=tasker.resource
    original={key:resource.get_node_data(key) for key in overrides}
    if any(not value for value in original.values()) or not resource.override_pipeline(overrides):
        raise BatchStopped('link_guard_failed')
    try:yield
    except BaseException:
        try:resource.override_pipeline(original)
        except Exception:pass
        raise
    else:
        if not resource.override_pipeline(original):raise BatchStopped('link_guard_restore_failed')


# Obtained imprints are items; this page does not prove recruited characters.
def _result_element(selected):
    if type(selected) is LinkOffer:
        if not _positive(selected.quantity):
            raise BatchStopped('link_identity_required')
        selected = selected.element
    if type(selected) is not str or selected not in ELEMENT_PRIORITY:
        raise BatchStopped('link_identity_required')
    return selected


def _result_quantity(selected,quantity=None):
    if type(selected) is LinkOffer:
        if quantity is not None and quantity!=selected.quantity:raise BatchStopped('link_identity_required')
        quantity=selected.quantity
    if not _positive(quantity):raise BatchStopped('link_identity_required')
    return quantity

def result_pipeline(selected=None,*,quantity=None):
    element = '__qualified_link_element_required__' if selected is None else _result_element(selected)
    count = '__qualified_link_quantity_required__' if selected is None else str(_result_quantity(selected,quantity))
    item = element+'属性的随机印记×'+count
    nodes = {
        'LabyLinkObtainedTitle': _ocr('^获得道具$', RESULT_TITLE_ROI),
        'LabyLinkObtainedMessage': _ocr('^获得了以下道具[。.]?$', RESULT_MESSAGE_ROI),
        'LabyLinkObtainedItem': _ocr(_pattern(item), RESULT_ITEM_ROI),
        'LabyLinkObtainedClose': _ocr('^关闭$', RESULT_CLOSE_ROI),
        'LabyLinkFollowingObserved': _ocr('.+', (0, 0, 1280, 720)),
    }
    observed = ['LabyLinkObtainedTitle', 'LabyLinkObtainedMessage',
                'LabyLinkObtainedItem', 'LabyLinkObtainedClose']
    nodes['LabyLinkObtainedObserved'] = dict(recognition='And', all_of=observed,
        action='DoNothing', next=[], timeout=3000, pre_delay=0, post_delay=0)
    nodes['LabyLinkObtainedCloseOnce'] = dict(recognition='And',
        all_of=['LabyLinkObtainedClose']+observed[:-1], action='Click', target=True,
        max_hit=1, next=[], timeout=2500, rate_limit=250, pre_delay=0, post_delay=0)
    nodes['LabyLinkResultCloseOnce']=dict(recognition='And',all_of=['LabyLinkObtainedClose','LabyLinkObtainedTitle','LabyLinkObtainedMessage'],action='Click',target=True,max_hit=1,next=[],timeout=2500,pre_delay=0,post_delay=0)
    return nodes


def link_obtained(receipt, selected, *, quantity=None):
    """Exact same-frame selected attribute and full quantity; no role inference."""
    element = _result_element(selected);quantity = _result_quantity(selected,quantity)
    if type(receipt) is not dict or receipt.get('native_ok') is not True:
        raise BatchStopped('link_result_unknown')
    nodes = receipt.get('nodes')
    if type(nodes) is not list or any(type(n) is not dict for n in nodes):
        raise BatchStopped('link_result_unknown')
    found = [n for n in nodes if n.get('name') == 'LabyLinkObtainedObserved']
    if len(found) != 1 or found[0].get('completed') is not True:
        raise BatchStopped('link_result_unknown')
    raw = found[0].get('recognition')
    expected = set(result_pipeline()['LabyLinkObtainedObserved']['all_of'])
    if (type(raw) is not list or any(type(r) is not dict or type(r.get('name')) is not str for r in raw)
            or len(raw) != len(expected) or {r['name'] for r in raw} != expected):
        raise BatchStopped('link_result_unknown')
    rows = {r['name']:r.get('detail') for r in raw}
    try:
        for label, roi, texts in (
                ('Title', RESULT_TITLE_ROI, ('获得道具',)),
                ('Message', RESULT_MESSAGE_ROI, ('获得了以下道具.', '获得了以下道具')),
                ('Close', RESULT_CLOSE_ROI, ('关闭',))):
            words = _words(rows['LabyLinkObtained'+label], roi)
            if _normalized(words[0].text) not in texts:
                raise BatchStopped('link_result_unknown')
        item = _words(rows['LabyLinkObtainedItem'], RESULT_ITEM_ROI, maximum=2)
    except BatchStopped:
        raise BatchStopped('link_result_unknown') from None
    name = element+'属性的随机印记'
    if _normalized(''.join(w.text for w in item)) != name+'×'+str(quantity):
        raise BatchStopped('link_result_mismatch')
    return dict(verified=True, element=element, imprint_name=name, quantity=quantity,
                imprints_obtained=True, native=receipt)


@contextmanager
def obtained_guard(tasker, selected, *, quantity=None):
    item = _result_element(selected)+'属性的随机印记×'+str(_result_quantity(selected,quantity))
    overrides = {'LabyLinkObtainedItem':dict(expected=_pattern(item))}
    resource = tasker.resource
    original = {key:resource.get_node_data(key) for key in overrides}
    if any(not value for value in original.values()) or not resource.override_pipeline(overrides):
        raise BatchStopped('link_result_guard_failed')
    try:yield
    except BaseException:
        try:resource.override_pipeline(original)
        except Exception:pass
        raise
    else:
        if not resource.override_pipeline(original):raise BatchStopped('link_result_guard_restore_failed')


class MaaLinkReward:
    """Durable imprint choice and exact item proof, with no role acquisition flow.

    Load link_pipeline() and result_pipeline() first. Only a new persisted intent
    may rearm the two dedicated input nodes. Unknown results and recorded reward
    identities cannot resend, including through a newly constructed adapter.
    """
    def __init__(self, tasker, stamp, *, timings=None):
        from .timing import Timings
        self.tasker, self.stamp = tasker, stamp
        self.timings = timings or Timings()
        self.attempted = set()

    def _run(self, node, *, input=False):
        from .. import native
        with self.timings.measure('page_wait'):
            receipt = native.run(self.tasker, node, reset_hits=(node,) if input else ())
        nodes = receipt.get('nodes') if type(receipt) is dict else None
        found = [n for n in nodes if type(n) is dict and n.get('name') == node] if type(nodes) is list else []
        if (type(receipt) is not dict or receipt.get('native_ok') is not True
                or len(found) != 1 or found[0].get('completed') is not True
                or input and (type(found[0].get('action')) is not dict
                              or found[0]['action'].get('success') is not True)):
            raise BatchStopped('link_input_unknown' if input else 'link_result_unknown')
        return receipt

    def choose(self, batch, enter_id, reward_id, *, ordered_elements=ELEMENT_PRIORITY):
        if (type(enter_id) is not int or enter_id <= 0 or type(reward_id) is not str
                or not reward_id or len(reward_id) > 128 or any(ord(c) < 32 for c in reward_id)):
            raise BatchStopped('link_reward_identity_required')
        key = (enter_id, reward_id)
        batch.checkpoint()
        events = batch.journal.events()
        if key in self.attempted or any(e.get('operation') == 'link_reward_select'
                and e.get('binding') == batch.binding.payload()
                and (e.get('enter_id'), e.get('reward_id')) == key for e in events):
            raise BatchStopped('link_reward_already_attempted')
        if events and events[-1]['state'] == 'in_flight':
            raise BatchStopped('blocked_in_flight')
        offers = link_offers(self._run('LabyLinkObserved'))
        with self.timings.measure('strategy'):
            selected = preferred_choice(offers, ordered_elements)
        fields = dict(enter_id=enter_id, reward_id=reward_id, slot=selected.slot,
                      element=selected.element, imprint_name=selected.element+'属性的随机印记',
                      quantity=selected.quantity)
        self.attempted.add(key)
        def send(operation_id):
            batch.checkpoint()
            selection = self._run('LabyLinkSelectOnce', input=True)
            return self.stamp(operation_id=operation_id, **fields,native=selection,
                input_native=selection,input_action_verified=True)
        with selection_guard(self.tasker, offers, selected.slot):
            clicked=batch.transaction('link_reward_select',fields,send)
        batch.checkpoint()
        with obtained_guard(self.tasker, selected):
            obtained = link_obtained(self._run('LabyLinkObtainedObserved'), selected)
        confirmed=batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,
            imprints_obtained=True,input_native=clicked['input_native'],input_action_verified=True,
            native=dict(selection=clicked['native'],obtained=obtained)),**fields,imprints_obtained=True)
        close_fields=dict(enter_id=enter_id,reward_id=reward_id,phase='reward_close')
        def close_result(oid):
            batch.checkpoint();close=self._run('LabyLinkResultCloseOnce',input=True)
            return self.stamp(operation_id=oid,**close_fields,native=close,input_native=close,
                input_action_verified=True,client_page_verified=False)
        closed=batch.transaction('link_reward_close',close_fields,close_result)
        batch.checkpoint();following=self._run('LabyLinkFollowingObserved')
        batch.observation(self.stamp(**close_fields,native=following,client_page_verified=True),**close_fields)
        from .relics import item_reward_values
        return dict(confirmed,closed_reward_signature=item_reward_values(confirmed['imprint_name']+'×'+str(confirmed['quantity']))['signature'],native=dict(confirmed['native'],close=closed['native'],
            following_observation=following,following_page='unclassified'))
