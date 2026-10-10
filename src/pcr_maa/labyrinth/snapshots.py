"""Normalize fixed protocol responses and compare complete openings.

Resume has block_id, not enter_id or difficulty; use a fresh top from the same
session to bind it, rather than filling identity fields from configuration.
"""
from dataclasses import dataclass
import hashlib,json
from .routes import Node,Policy,select_route

def canonical_map(values):
    try:
        if type(values) is not list or not values: raise ValueError()
        nodes=[Node.parse(v) for v in values]
        if len({n.block_id for n in nodes})!=len(nodes): raise ValueError()
        return tuple((n.block_id,n.area,n.column,n.row,n.block_type,n.quest_id,tuple(sorted(n.next_ids)))
                     for n in sorted(nodes,key=lambda n:n.block_id))
    except (ValueError,TypeError,KeyError): raise ValueError('map_invalid') from None

@dataclass(frozen=True)
class OpeningSnapshot:
    account_id:int
    enter_id:int
    guild_id:int
    difficulty:int
    nodes:tuple[Node,...]
    protocol_current_block_id:int|None=0
    protocol_current_block_source:str="legacy_unspecified"

    @classmethod
    def read(cls,account_id,enter_id,top,resume):
        return cls._read(account_id,enter_id,top,resume)

    @classmethod
    def read_created(cls,account_id,enter_id,top,resume,*,enter_ack):
        """Pure ACK/map validation; only the Owner can grant creation authority."""
        if (type(enter_ack) is not dict or type(enter_ack.get("enter_id")) is not int
                or enter_ack["enter_id"]!=enter_id or type(resume) is not dict
                or canonical_map(enter_ack.get("map_list"))!=canonical_map(resume.get("map_list"))):
            raise ValueError("opening_ack_unknown")
        return cls._read(account_id,enter_id,top,resume,created=True)

    @classmethod
    def _read(cls,account_id,enter_id,top,resume,*,created=False):
        if type(account_id) is not int or account_id<=0 or type(enter_id) is not int or enter_id<=0:
            raise ValueError('opening_identity_required')
        if type(top) is not dict or type(resume) is not dict or type(top.get('enter_id')) is not int or top['enter_id']!=enter_id:
            raise ValueError('opening_changed')
        guild=top.get('guild_id');difficulty=top.get('difficulty')
        if (type(guild) is not int or not 1<=guild<=5 or type(difficulty) is not int or not 1<=difficulty<=5
                or type(resume.get('guild_id')) is not int or resume['guild_id']!=guild):
            raise ValueError('opening_identity_unknown')
        block=resume.get('block_id')
        # Absent block_id and explicit null both denote no movement.
        if block is not None and (type(block) is not int or block<0 or not created and block!=0):
            raise ValueError('mid_run_not_supported')
        raw=resume.get('map_list');canonical_map(raw)
        nodes=tuple(Node.parse(v) for v in raw)
        if created:
            start=cls(account_id,enter_id,guild,difficulty,nodes).initial_start_block_id
            if block not in (None,0,start):raise ValueError("mid_run_not_supported")
        route=select_route(enter_id,nodes,difficulty,Policy(perfect_start=False,allowance=3,area3_bosses=(),area5_bosses=()),
                           enforce_allowance=False,require_terminal_boss=True)
        for area in (3,) if difficulty==1 else (3,5):
            last=max(n.column for n in nodes if n.area==area)
            if not any(n.area==area and n.column==last and n.block_type==8 and n.quest_id for n in nodes):
                raise ValueError('boss_map_incomplete')
        if route is None: raise ValueError('map_unreachable')
        return cls(account_id,enter_id,guild,difficulty,nodes,block,"protocol_resume")

    @property
    def initial_start_block_id(self):
        first=min((n.column for n in self.nodes if n.area==1),default=None)
        starts=[n for n in self.nodes if n.area==1 and n.column==first]
        if len(starts)!=1 or starts[0].block_type!=1:raise ValueError("initial_start_unknown")
        return starts[0].block_id

    @property
    def fingerprint(self):
        value=(self.account_id,self.enter_id,self.guild_id,self.difficulty,canonical_map([n.payload() for n in self.nodes]))
        return hashlib.sha256(json.dumps(value,separators=(',',':')).encode()).hexdigest()

    def payload(self):
        return dict(enter_id=self.enter_id,guild_id=self.guild_id,difficulty=self.difficulty,current_block_id=0,
                    current_block_id_basis="route_initial_sentinel",
                    protocol_current_block_id=self.protocol_current_block_id,
                    protocol_current_block_source=self.protocol_current_block_source,
                    initial_start_block_id=self.initial_start_block_id,
                    source='protocol',complete=True,map_list=[n.payload() for n in self.nodes],map_sha256=self.fingerprint)
