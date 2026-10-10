"""Maa three-choice role rewards: one-frame offers, guarded input, explicit readback.

Native names precede portraits. All three slots must be located; one unsafe
identity may be excluded, never guessed. Selection ACK and actual joining are
recorded separately.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import re,unicodedata
from .. import native
from .batch import BatchStopped
from .roles import Context,load_optimizer,configured_optimizer,strategy_value
from .timing import Timings

# Names directly verified on the private B-server role reward sample.
ALIASES={'步美(怪盗)':'1215','佩可莉姆(圣诞节)':'1279','优衣(礼服)':'1156','古蕾雅':'1094'}

def normalized_name(value):
    value=unicodedata.normalize('NFKC',value)
    return re.sub(r'\s+', '', value.replace('（','(').replace('）',')'))

def resolve_role_name(value,optimizer):
    from .character_recognition import canonical_id,load_character_pack
    name=reward_name_key(value)
    if not name or value.count('(')+value.count('（')!=value.count(')')+value.count('）'):
        raise BatchStopped('role_reward_profile_unknown')
    keys={canonical_id(key) for key,profile in optimizer.profiles.items() if reward_name_key(profile.data.get('displayName',''))==name}
    for person in load_character_pack()[2].values():
        if any(reward_name_key(word)==name for word in (person.get('name',''),person.get('officialName',''),*person.get('aliases',[]))):
            keys.add(canonical_id(person['id']))
    keys.update(key for alias,key in ALIASES.items() if reward_name_key(alias)==name)
    if len(keys)!=1:raise BatchStopped('role_reward_profile_unknown')
    key=next(iter(keys))
    if key not in optimizer.profiles:raise BatchStopped('role_reward_profile_unknown')
    return key

def reward_name_key(value):
    return ''.join(c.lower() for c in normalized_name(value) if c.isalnum())

def reward_name_family(value):
    return reward_name_key(normalized_name(value).split('(',1)[0])

def reward_identity_safe(card):
    # Fixed upstream reward gate; full-roster icon trust alone is insufficient.
    return (card.get('trusted') is True and bool(card.get('character_id')) and bool(card.get('name'))
        and (card.get('identity_source')=='maa_name'
            or card.get('confidence',0)>=.50 and card.get('rival_margin',0)>=.10))

@dataclass(frozen=True)
class RoleOffer:
    slot: int
    character_id: str
    words: tuple

def role_offer_from_words(slot,words,optimizer):
    words=sorted(words,key=lambda w:(w.get('box',[0,0])[1],w.get('box',[0])[0]))
    if not 1<=len(words)<=3:raise BatchStopped('role_reward_names_unknown')
    x=(195,564,932)[slot]
    for word in words:
        box=word.get('box');text=word.get('text')
        if (not isinstance(text,str) or not text.strip() or word.get('score',0)<.85
                or not isinstance(box,list) or len(box)!=4 or any(type(v) is not int for v in box)
                or box[0]<x or box[1]<444 or box[2]<=0 or box[3]<=0
                or box[0]+box[2]>x+160 or box[1]+box[3]>508):raise BatchStopped('role_reward_names_unknown')
    key=resolve_role_name(''.join(w['text'] for w in words),optimizer)
    return RoleOffer(slot,key,tuple(dict(w) for w in words))

def role_offers(receipt,optimizer):
    node=next((n for n in receipt.get('nodes',()) if n.get('name')=='LabyRoleRewardObserved' and n.get('completed')),None)
    raw=node.get('recognition') if node else None
    if receipt.get('native_ok') is not True or not isinstance(raw,list):raise BatchStopped('role_reward_unknown')
    offers=[]
    for slot in range(3):
        rows=[r for r in raw if r.get('name')==f'LabyRoleRewardName{slot}']
        if len(rows)!=1:raise BatchStopped('role_reward_names_unknown')
        offers.append(role_offer_from_words(slot,rows[0].get('detail',{}).get('filtered',()),optimizer))
    if len({o.character_id for o in offers})!=3:raise BatchStopped('role_reward_names_ambiguous')
    return tuple(offers)

def portrait_offers(observation,optimizer):
    cards=observation.get('cards',())
    if observation.get('page')!='reward' or tuple(c.get('slot') for c in cards)!=(0,1,2):raise BatchStopped('role_reward_unknown')
    offers=[]
    for card in cards:
        if not reward_identity_safe(card):continue
        key=card.get('character_id')
        if key not in optimizer.profiles:continue
        offers.append(RoleOffer(card['slot'],key,({'text':card['name']},)))
    if len(offers)<2:raise BatchStopped('role_reward_profile_unknown')
    if len({o.character_id for o in offers})!=len(offers):raise BatchStopped('role_reward_names_ambiguous')
    return tuple(offers)

@contextmanager
def portrait_selection(tasker,characters,offers,slot):
    # Current-frame business identities replace nine old OCR word-box guards.
    node=characters.namespace+'Read';resource=tasker.resource
    selected=next(o for o in offers if o.slot==slot)
    override={node:dict(custom_recognition_param=dict(mode='read',page='reward',expected_slot=slot,expected_role=selected.character_id)),
        'LabyRoleRewardSelectOnce':dict(recognition='And',all_of=[f'LabyRoleRewardButton{slot}',node],
            action='Click',target=True,max_hit=1,next=[],timeout=3000,rate_limit=250)}
    original={k:resource.get_node_data(k) for k in override}
    if any(not v for v in original.values()) or not resource.override_pipeline(override):raise BatchStopped('role_reward_guard_failed')
    try:yield
    finally:
        if not resource.override_pipeline(original):raise BatchStopped('role_reward_guard_restore_failed')

@contextmanager
def selection_guard(tasker,offers,slot):
    if tuple(o.slot for o in offers)!=(0,1,2) or type(slot) is not int or slot not in (0,1,2):raise BatchStopped('role_reward_guard_invalid')
    resource=tasker.resource;override={};guards=[]
    for offer in offers:
        for j in range(3):
            name=f'LabyRoleRewardGuard{offer.slot}{j}';base=resource.get_node_data(name)
            if not base:raise BatchStopped('role_reward_guard_missing')
            word=offer.words[min(j,len(offer.words)-1)];x,y,w,h=word['box']
            override[name]=dict(recognition='OCR',expected='^'+re.escape(word['text'])+'$',roi=[x-3,y-3,w+6,h+6],only_rec=True,threshold=.85,action='DoNothing',next=[],timeout=1500,pre_delay=0,post_delay=0)
            guards.append(name)
    override['LabyRoleRewardSelectOnce']=dict(recognition='And',all_of=[f'LabyRoleRewardButton{slot}','LabyRoleRewardTitle','LabyRoleRewardRemaining']+guards+[f'LabyRoleRewardButton{i}' for i in range(3)],action='Click',target=True,max_hit=1,next=[],timeout=3000,rate_limit=250)
    original={k:resource.get_node_data(k) for k in override}
    if any(not v for v in original.values()) or not resource.override_pipeline(override):raise BatchStopped('role_reward_guard_failed')
    try:yield
    finally:
        if not resource.override_pipeline(original):raise BatchStopped('role_reward_guard_restore_failed')

@contextmanager
def joined_guard(tasker,selected):
    # Read the outcome sentence; the added roster item identifies the selected variant.
    pattern=r'\s*'.join(re.escape(normalized_name(w['text'])).replace(r'\(',r'[（(]').replace(r'\)',r'[）)]') for w in selected.words)
    overrides={
        'LabyRoleRewardJoinedMessage':dict(expected=r'^.*成为了同伴[。.]?$',only_rec=False),
        'LabyRoleRewardJoinedItem':dict(expected=r'^\s*'+pattern+r'\s*$')}
    resource=tasker.resource;original={k:resource.get_node_data(k) for k in overrides}
    if any(not v for v in original.values()) or not resource.override_pipeline(overrides):raise BatchStopped('role_reward_guard_failed')
    try:yield
    finally:
        if not resource.override_pipeline(original):raise BatchStopped('role_reward_guard_restore_failed')

class MaaRoleReward:
    def __init__(self,tasker,stamp,read_joined=None,*,optimizer=None,timings=None,characters=None):
        self.tasker,self.stamp,self.read_joined=tasker,stamp,read_joined
        self.characters=characters
        self.optimizer=optimizer or load_optimizer();self._provided_optimizer=optimizer;self.timings=timings or Timings();self.attempted=set()

    def choose_free(self,batch,enter_id,reward_id,acquired,*,defer_joined=False):
        """Pinned event-free planner: highest rated visible unowned role, then actual invitation."""
        from .character_recognition import CharacterNavigation
        if type(enter_id) is not int or enter_id<=0 or not isinstance(reward_id,str) or not reward_id:
            raise BatchStopped('role_reward_identity_required')
        key=(enter_id,reward_id)
        if key in self.attempted or any(e.get('operation','').startswith('event_free_role_') and
                (e.get('enter_id'),e.get('reward_id'))==key for e in batch.journal.events()):
            raise BatchStopped('role_reward_already_attempted')
        batch.checkpoint();self.attempted.add(key)
        optimizer=self._provided_optimizer or configured_optimizer(getattr(batch.settings,'strategy',None))
        chars=self.characters
        if chars is None:raise BatchStopped('character_callback_unavailable')
        current,receipt=chars.read(batch,'opening')
        required=current.get('required_count')
        if type(required) is not int or not 1<=required<=3 or current['count']!=0 or current['selected']:
            raise BatchStopped('event_free_role_count_unknown')
        selected=[];scrolls=0;same=0;anchor=current
        while len(selected)<required:
            batch.checkpoint()
            cards=[c for c in current['cards'] if c['trusted'] and not c['selected']
                and c['character_id'] not in set(acquired)|set(selected)
                and c['character_id'] in optimizer.profiles and optimizer.profiles[c['character_id']].rating is not None]
            if not cards:
                if scrolls>=24:raise BatchStopped('character_not_visible')
                before=current
                current=CharacterNavigation(chars,self.stamp).move(batch,enter_id,'opening',current,direction='down')
                scrolls+=1
                unchanged=CharacterNavigation.unchanged(anchor,current)
                same=same+1 if unchanged else 0
                if not unchanged:anchor=current
                if same>=2:raise BatchStopped('character_not_visible')
                continue
            card=min(cards,key=lambda c:(-optimizer.profiles[c['character_id']].rating,c['box'][1],c['box'][0]))
            role=card['character_id'];before=sorted(selected);after=sorted(selected+[role])
            fields=dict(enter_id=enter_id,reward_id=reward_id,role_id=role,required_count=required)
            def send(oid):
                batch.checkpoint()
                _,sent=chars.run('ToggleOnce',dict(mode='toggle',page='opening',event_free=True,
                    role_id=role,expected_selected=before),input=True)
                return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
            clicked=batch.transaction('event_free_role_select',fields,send)
            current,receipt=chars.wait_for(batch,'opening',lambda v:v['count']==len(after)
                and v.get('required_count')==required and set(v['selected'])<=set(after))
            batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,native=receipt,
                input_native=clicked['input_native'],selection_verified=True),**fields)
            selected=after;same=0;anchor=current
        fields=dict(enter_id=enter_id,reward_id=reward_id,roles=selected,required_count=required)
        def invite(oid):
            batch.checkpoint()
            _,sent=chars.run('InviteOnce',dict(mode='invite',page='opening',
                expected_selected=selected,required_count=required),input=True)
            return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
        clicked=batch.transaction('event_free_role_invite',fields,invite)
        if defer_joined:
            from .scene import MaaSceneRuntime
            after=MaaSceneRuntime(self.tasker,self.stamp,timings=self.timings).wait_for(batch,{'joined','event_animation'},source='free_role_reward',timeout=12,reason='role_reward_readback_unknown',acquisition_fields=dict(source='maa',enter_id=enter_id,reward_id=reward_id))
            return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,roles_pending=selected,inventory_added=False,selection_pending=True,following_scene=after,native=after.native,input_native=clicked['input_native']),**fields)
        batch.checkpoint();joined,receipt=chars.read(batch,'joined',stable=False)
        if sorted(joined.get('roles_joined',()))!=selected:raise BatchStopped('role_reward_readback_unknown')
        return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,
            roles_joined=selected,inventory_added=True,native=receipt,input_native=clicked['input_native'],
            input_action_verified=True),**fields,inventory_added=True)

    def choose(self,batch,enter_id,reward_id,acquired,context=Context(),*,defer_joined=False):
        if type(enter_id) is not int or enter_id<=0 or not isinstance(reward_id,str) or not reward_id:raise BatchStopped('role_reward_identity_required')
        key=(enter_id,reward_id)
        if key in self.attempted or any(e.get('operation')=='role_reward_select' and (e.get('enter_id'),e.get('reward_id'))==key for e in batch.journal.events()):raise BatchStopped('role_reward_already_attempted')
        batch.checkpoint()

        strategy=getattr(batch.settings,'strategy',None)
        optimizer=self._provided_optimizer or configured_optimizer(strategy)
        if self.characters is not None:
            observation,receipt=self.characters.read(batch,'reward',stable=False)
            offers=portrait_offers(observation,optimizer)
        else:
            with self.timings.measure('page_wait'):receipt=native.run(self.tasker,'LabyRoleRewardObserved')
            offers=role_offers(receipt,optimizer)
        with self.timings.measure('strategy'):ranked=optimizer.choose_role(tuple(o.character_id for o in offers),acquired,context,weights=strategy_value(strategy,'deployment_weights',(1,.35,.2)))
        if not ranked:raise BatchStopped('role_reward_strategy_unknown')
        selected=next(o for o in offers if o.character_id==ranked[0]['character_id'])
        fields=dict(enter_id=enter_id,reward_id=reward_id,role_id=selected.character_id)
        self.attempted.add(key)
        def send(oid):
            batch.checkpoint()
            with self.timings.measure('page_wait'):result=native.run(self.tasker,'LabyRoleRewardSelectOnce',reset_hits=('LabyRoleRewardSelectOnce',))
            if not result['native_ok'] or not any(n['name']=='LabyRoleRewardSelectOnce' and n.get('completed') and n.get('action',{}).get('success') for n in result.get('nodes',())):raise BatchStopped('role_reward_input_unknown')
            return self.stamp(operation_id=oid,**fields,native=result,input_native=result,input_action_verified=True)
        guard=portrait_selection(self.tasker,self.characters,offers,selected.slot) if self.characters is not None else selection_guard(self.tasker,offers,selected.slot)
        with guard:clicked=batch.transaction('role_reward_select',fields,send)
        batch.checkpoint()
        if defer_joined:
            from .scene import MaaSceneRuntime
            runtime=MaaSceneRuntime(self.tasker,self.stamp,timings=self.timings)
            before=tuple(c.get('character_id') for c in observation['cards']) if self.characters is not None else tuple(o.character_id for o in offers)
            def advanced(value):
                if value.kind!='role_reward':return True
                if self.characters is None:return False
                actual=self.characters.probe(batch,'reward')
                if actual is None:return False
                return tuple(c.get('character_id') for c in actual['cards'])!=before or actual.get('remaining',0)<observation.get('remaining',0)
            after=runtime.wait_for(batch,{'role_reward','joined','event_animation'},source='role_reward',accept=advanced,timeout=12,reason='role_reward_readback_unknown',acquisition_fields=dict(source='maa',enter_id=enter_id,reward_id=reward_id) if self.characters is not None and observation.get('remaining')==1 else None)
            return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,roles_pending=[selected.character_id],selection_pending=True,inventory_added=False,following_scene=after,native=after.native,input_native=clicked['input_native']),**fields)
        if self.read_joined is not None:readback=self.read_joined(enter_id,selected.character_id)
        else:
            with joined_guard(self.tasker,selected):
                if self.characters is not None:
                    observation,joined=self.characters.read(batch,'joined',stable=False,expected_role=selected.character_id)
                    verified=observation['roles_joined']==[selected.character_id]
                else:
                    with self.timings.measure('page_wait'):joined=native.run(self.tasker,'LabyRoleRewardJoinedObserved')
                    verified=joined['native_ok']
            readback=dict(verified=verified,role_id=selected.character_id,inventory_added=verified,native=joined)
        if not isinstance(readback,dict) or readback.get('verified') is not True or readback.get('role_id')!=selected.character_id or readback.get('inventory_added') is not True:raise BatchStopped('role_reward_readback_unknown')
        return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,inventory_added=True,
            native=readback,input_native=clicked['input_native'],input_action_verified=True),**fields,inventory_added=True)
