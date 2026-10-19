"""Shared joined identities; synthetic receipts never prove live acquisition."""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace as NS

import numpy as np
import pytest

from pcr_maa.labyrinth import character_recognition as cr
from pcr_maa.labyrinth.batch import BatchStopped, Binding
from pcr_maa.labyrinth.portrait_cache import joined_portrait_readback


def match(role, trusted=True):
    return cr.IconMatch(role, role, '11', .9, .2, .7, trusted, (), role+'11')


def callback(monkeypatch, roles=('1025','1308','1145'), names=('铃莓','铃莓 (春日）','联恋 (圣诞节）'),trusted=True):
    def recognize(name, image):
        if name.startswith(('LabyDateChanged','LabyDisconnect')): return NS(hit=False)
        if 'JoinedName' not in name: return NS(hit=True)
        slot=int(name[-1])
        if names[slot] is None:return NS(hit=False)
        word=dict(text=names[slot],score=.92,box=[420,238+94*slot,140,30])
        return NS(hit=True,raw_detail=dict(all=[word],filtered=[word],best=word))
    matcher=NS(match=lambda image,box,**kw:match(roles[(box[1]-233)//94],trusted))
    matcher.refine=lambda image,box,value,**kw:value
    monkeypatch.setattr(cr,'card_presence',lambda image,box:.2 if (box[1]-233)//94<len(roles) else 0)
    context=NS(tasker=NS(stopping=False),run_recognition=recognize)
    return cr.CharacterRecognition('LabyCharacters'+'a'*32,NS(matcher=matcher,stopped=lambda:False)).analyze(
        context,NS(image=np.full((720,1280,3),255,np.uint8),custom_recognition_param='{"mode":"read","page":"joined"}'))


def receipt(detail):
    name='LabyCharacters'+'a'*32+'Read'
    row=dict(box=[0,0,1,1],detail=detail)
    return dict(native_ok=True,tasker_inited=True,task_id=1,nodes=[dict(name=name,completed=True,
        action=dict(name=name,success=True),recognition=dict(all=[row],filtered=[row],best=row))])


def test_independent_portraits_survive_unmapped_name_typo(monkeypatch):
    result=callback(monkeypatch)
    assert result is not None
    layout,people=joined_portrait_readback(receipt(result.detail))
    assert layout=='joined3' and [p.character_id for p in people]==['1025','1308','1145']
    assert people[2].raw_text=='联恋 (圣诞节）' and people[2].alias_source=='full-pack-portrait'


def test_valid_name_conflicting_with_trusted_portrait_stops(monkeypatch):
    assert callback(monkeypatch,names=('铃莓','铃莓 (春日）','咲恋')) is None


def test_duplicate_portraits_cannot_be_silently_deduplicated(monkeypatch):
    assert callback(monkeypatch,roles=('1025','1025','1145')) is None


@pytest.mark.parametrize('field,value',[('trusted',False),('confidence',.1),('rival_margin',0),('character_id','1000'),('slot',2),('unit_icon_id',{})])
def test_joined_receipt_rejects_changed_identity_evidence(monkeypatch,field,value):
    result=callback(monkeypatch);data=deepcopy(result.detail)
    data['portraits'][0][field]=value
    with pytest.raises(BatchStopped):joined_portrait_readback(receipt(data))


def test_expected_roles_wrapper_does_not_supply_actual_ids(monkeypatch):
    result=callback(monkeypatch);data=deepcopy(result.detail)
    data['roles_joined']=['1001','1002','1003']
    with pytest.raises(BatchStopped):joined_portrait_readback(receipt(data))


@pytest.mark.parametrize('count',[1,2,3])
def test_missing_names_can_use_independent_portraits(monkeypatch,count):
    result=callback(monkeypatch,roles=('1025','1308','1145')[:count],names=(None,)*count)
    layout,people=joined_portrait_readback(receipt(result.detail))
    assert layout==f'joined{count}' and len(people)==count and all(p.raw_text=='' for p in people)


def test_untrusted_art_requires_exact_native_name(monkeypatch):
    result=callback(monkeypatch,trusted=False,names=('铃莓','铃莓 (春日）','咲恋 (圣诞节）'))
    _,people=joined_portrait_readback(receipt(result.detail))
    assert all(p.alias_source=='upstream-complete-catalog-name' for p in people)
    assert callback(monkeypatch,trusted=False) is None


def test_opening_uses_shared_reader_with_original_deadline(monkeypatch):
    from pcr_maa import native
    from pcr_maa.labyrinth import opening,scene
    from types import SimpleNamespace as NS
    result=callback(monkeypatch);evidence=receipt(result.detail);calls=[]
    def read(batch,page,**kw):
        calls.append((page,kw));assert native.RUN_DEADLINE.get()<float('inf')
        return result.detail,evidence
    monkeypatch.setattr(scene,'MaaSceneRuntime',lambda *a,**kw:NS(wait_for=lambda *a,**kw:None))
    port=opening.MaaOpening(NS(),lambda **kw:kw,characters=NS(read=read))
    port._run=lambda *a,**kw:pytest.fail('active opening must use shared reader')
    assert port._wait_invitation_joined(NS(checkpoint=lambda:None,stopped=lambda:False),1,{'operation_id':'a'})==evidence
    assert calls==[('joined',dict(stable=False,timeout=3))]


def test_handoff_accepts_independent_shared_receipt_and_rejects_wrapper_ids(monkeypatch):
    from pcr_maa.labyrinth.acquisition import ProtocolAcquisitionPort
    result=callback(monkeypatch);evidence=receipt(result.detail);binding=Binding(123,'offline-joined')
    def fail(reason):raise BatchStopped(reason)
    port=NS(binding=binding,_fail=fail)
    def step(frame,name,ack=None):
        return dict(verified=True,binding=binding.payload(),source='maa',enter_id=1,frame=frame,
            native=ack or dict(native_ok=True,tasker_inited=True,task_id=1,nodes=[dict(name=name,completed=True)]))
    value=dict(source='maa',enter_id=1,empty=step(2,'LabyOpeningEmptyObserved'),
        joined=step(3,'',evidence),closed=step(4,'LabyMapObserved'),expected_roles=['1001'])
    actual=ProtocolAcquisitionPort._invitation(port,value,NS(enter_id=1),5,1)
    assert [r['character_id'] for r in actual]==['1025','1308','1145']
    assert all(r['name_score'] is None for r in actual)
    value['joined']['native']['nodes'][0]['recognition']['best']['detail']['roles_joined']=['1001']
    with pytest.raises(BatchStopped):ProtocolAcquisitionPort._invitation(port,value,NS(enter_id=1),5,1)


def test_repeated_invalid_receipts_never_gain_trust(monkeypatch):
    result=callback(monkeypatch);data=result.detail
    for i in range(2000):
        altered=deepcopy(data)
        altered['portraits'][i%3]['rival_margin']=0
        with pytest.raises(BatchStopped):joined_portrait_readback(receipt(altered))
