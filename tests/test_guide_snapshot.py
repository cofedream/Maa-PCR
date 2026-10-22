import asyncio
import copy
import json
from pathlib import Path
import runpy
from types import SimpleNamespace as NS

import pytest
from pcr_maa.guide_snapshot import collect, normalize, SnapshotFailure


def load():
    return dict(user_info=dict(viewer_id=123), unit_list=[dict(id=100101, unit_level=365,
        promotion_level=38, unit_rarity=6, battle_rarity=3,
        union_burst=[dict(skill_level=365)], main_skill=[dict(skill_level=365), dict(skill_level=365)],
        ex_skill=[dict(skill_level=365)], unique_equip_slot=[dict(id=13001, is_slot=True, enhancement_level=350)])],
        access_key='synthetic-secret-not-for-output', item_list=[dict(id=1, stock=99)])


def test_projection_only_requested_units_and_unknowns():
    source = load()
    source['unit_list'].append(dict(id=999999, private='not selected'))
    result = normalize(source, 123, '202610141431', [100101, 110101])
    assert len(result['units']) == 1 and result['units'][0]['level'] == 365
    assert result['units'][0]['rarity'] == 6
    assert result['units'][0]['unique_equipment'][0]['level'] == 350
    assert result['ownership_complete'] is False
    assert {'units.110101', 'account', 'units.100101.battle_rarity', 'units.100101.unique_equipment.2'} <= set(result['unknown_fields'])
    text = json.dumps(result)
    assert all(key not in text for key in ('access_key', 'synthetic-secret', 'item_list', '999999'))
    assert source['access_key'] == 'synthetic-secret-not-for-output'


def test_all_owned_roster_keeps_timestamp_and_only_equipped_data():
    source = progress_source()
    for uid in (101001, 134901, 129701, 116701, 134701, 135501, 104701):
        row = copy.deepcopy(source['unit_list'][0]); row['id'] = uid
        source['unit_list'].append(row)
    result = normalize(source, 123, '202610141431', observed_at='2026-10-19T00:00:00Z')
    assert result['ownership_complete'] is True and len(result['units']) == 8
    assert result['observed_at'] == '2026-10-19T00:00:00Z'
    assert {u['unit_id'] for u in result['units']} >= {101001, 134901}
    assert all(u['ex_equipment'][0]['equipment_id'] == 4101301 for u in result['units'])
    assert all(k not in json.dumps(result) for k in ('access_key', 'item_list', 'serial_id', 'user_ex_equip', 'unrelated-inventory'))


def test_large_roster_wire_v2_and_legacy_selection():
    script = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/read-guide-snapshot.py'))
    request = dict(version=2, account_id=123, content_version='202610141431', credentials=dict(username='synthetic', password='synthetic'))
    assert script['parse'](request)['version'] == 2
    with pytest.raises(SnapshotFailure): script['parse'](dict(request, unit_ids=[100101]))
    source = load(); source['unit_list'] = [dict(source['unit_list'][0], id=i+100000) for i in range(2000)]
    result = normalize(source, 123, '202610141431')
    assert len(result['units']) == 2000 and len(json.dumps(result)) > 262144
    assert normalize(source, 123, '202610141431', [100001])['ownership_complete'] is False


def test_absent_fields_are_unknown_not_zero():
    source = load(); row = source['unit_list'][0]
    row['unit_level'] = None; row['unique_equip_slot'] = None; row['main_skill'] = None
    result = normalize(source, 123, '202610141431', [100101])
    assert result['units'][0]['level'] is None
    assert {'units.100101.level', 'units.100101.skills.s1', 'units.100101.unique_equipment.1'} <= set(result['unknown_fields'])


def test_equipment_count_and_breakthrough_preserve_missing_and_false():
    source = load(); row = source['unit_list'][0]
    row['equip_slot'] = [dict(id=i+1, is_slot=i<4) for i in range(6)]
    row['exceed_stage'] = 1
    result = normalize(source, 123, '202610141431', [100101])
    assert result['units'][0]['equipment_count'] == 4 and result['units'][0]['limit_break'] is True
    assert 'units.100101.limit_break' not in result['unknown_fields']
    row['equip_slot'] = [dict(is_slot=False) for _ in range(6)]; row['exceed_stage'] = 0
    result = normalize(source, 123, '202610141431', [100101])
    assert result['units'][0]['equipment_count'] == 0 and result['units'][0]['limit_break'] is False
    row['equip_slot'] = row['equip_slot'][:4]; row['exceed_stage'] = 2
    result = normalize(source, 123, '202610141431', [100101])
    assert result['units'][0]['equipment_count'] is None and result['units'][0]['limit_break'] is None
    assert {'units.100101.equipment_count', 'units.100101.limit_break'} <= set(result['unknown_fields'])
    row.pop('equip_slot'); row.pop('exceed_stage')
    result = normalize(source, 123, '202610141431', [100101])
    assert result['units'][0]['equipment_count'] is None and result['units'][0]['limit_break'] is None


@pytest.mark.parametrize('patch', [
    dict(equip_slot=[dict(is_slot=1)]*6),
    dict(equip_slot=[dict(is_slot=True,id=0)]*6),
    dict(equip_slot=[dict(is_slot=False)]*7),
    dict(exceed_stage=True), dict(exceed_stage=-1),
])
def test_bad_equipment_or_breakthrough_rejected(patch):
    source = load(); source['unit_list'][0].update(patch)
    with pytest.raises(SnapshotFailure): normalize(source, 123, '202610141431', [100101])


def progress_source():
    source = load()
    source['princess_knight_info'] = dict(talent_skill_last_enhanced_page_node_list=[
        dict(node_id=n, enhance_level=5) for n in (443, 456, 469)],
        team_skill_latest_node=dict(node_id=75, enhance_level=1))
    source['unit_list'][0]['ex_equip_slot'] = [dict(serial_id=11), dict(serial_id=0), dict(serial_id=12)]
    source['unit_list'][0]['cb_ex_equip_slot'] = [dict(serial_id=999)]
    source['user_ex_equip'] = [dict(serial_id=11, ex_equipment_id=4101301, enhancement_pt=6000, rank=2),
        dict(serial_id=99, ex_equipment_id=4101302, enhancement_pt=0, rank=0, private='unrelated-inventory')]
    return source


def detailed_source():
    from pcr_maa.guide_snapshot import reference
    rules = reference('202610141431', 'account-progress')
    source = progress_source()
    source['princess_knight_info']['talent_level_info_list'] = [
        dict(talent_id=n, total_point=rules['attribute_levels'][399][0]) for n in range(1, 6)]
    source['unit_role_list'] = [dict(unit_role_id=n, **{f'{key}_{slot}': value
        for slot in range(1, 5) for key, value in (('slot_level', 3), ('enhance_level', 2))}) for n in range(1, 9)]
    return source


def test_full_attribute_and_role_projection_keeps_only_normalized_fields():
    source = detailed_source()
    source['unit_role_list'][0]['inventory'] = 'not retained'
    value = normalize(source, 123, '202610141431', observed_at='2026-10-19T00:00:00Z')
    assert [x['level'] for x in value['account']['attribute_levels']] == [400] * 5
    assert len(value['account']['unit_roles']) == 8
    assert all(len(x['slots']) == 4 for x in value['account']['unit_roles'])
    assert value['account']['unit_roles'][0]['slots'][0] == dict(slot=1,slot_level=3,enhance_level=2)
    assert value['observed_at'] == '2026-10-19T00:00:00Z'
    assert all(x not in json.dumps(value) for x in ('not retained','access_key','unit_role_list','talent_level_info_list'))


def test_all_attribute_thresholds_and_unknown_above_verified_table():
    from pcr_maa.guide_snapshot import detailed_progress, reference
    rules=reference('202610141431','account-progress')
    for i,(point,level) in enumerate(rules['attribute_levels']):
        for actual,expected in ((point,level),(point-1,level-1)) if i else ((0,1),):
            value=detailed_progress(dict(princess_knight_info=dict(talent_level_info_list=[dict(talent_id=1,total_point=actual)])),rules,[])
            assert value['attribute_levels'][0]['level']==expected
    source=detailed_source();source['princess_knight_info']['talent_level_info_list'][0]['total_point']=rules['attribute_levels'][-1][0]+1
    value=normalize(source,123,'202610141431')
    assert value['account']['attribute_levels'][0]['level'] is None
    assert 'account.attribute_levels.1.level' in value['unknown_fields']


def test_missing_and_partial_progress_never_becomes_zero():
    source=detailed_source();source['princess_knight_info']['talent_level_info_list'].pop()
    source['unit_role_list'].pop();source['unit_role_list'][0].pop('enhance_level_2')
    source['unit_role_list'][0].update(slot_level_1=0,enhance_level_1=0)
    value=normalize(source,123,'202610141431')
    assert {'account.attribute_levels.5','account.unit_roles.8','account.unit_roles.1.slots.2.enhance_level'} <= set(value['unknown_fields'])
    assert value['account']['unit_roles'][0]['slots'][1]['enhance_level'] is None
    assert value['account']['unit_roles'][0]['slots'][0] == dict(slot=1,slot_level=0,enhance_level=0)
    source.pop('princess_knight_info');value=normalize(source,123,'202610141431')
    assert value['account']['unit_roles'] and value['account']['attribute_levels'] is None
    assert 'account' not in value['unknown_fields']
    source.pop('unit_role_list');value=normalize(source,123,'202610141431')
    assert value['account']['unit_roles'] is None and 'account.unit_roles' in value['unknown_fields']
    value=normalize(detailed_source(),123,'202610151431')
    assert value['account']['attribute_levels'] is None and value['account']['unit_roles'] is None


@pytest.mark.parametrize('patch', [
    lambda s:s['princess_knight_info']['talent_level_info_list'][0].update(total_point=True),
    lambda s:s['princess_knight_info']['talent_level_info_list'][0].update(total_point=-1),
    lambda s:s['princess_knight_info']['talent_level_info_list'][0].update(talent_id=6),
    lambda s:s['princess_knight_info']['talent_level_info_list'][0].update(talent_id=2),
    lambda s:s['unit_role_list'][0].update(unit_role_id=2),
    lambda s:s['unit_role_list'][0].update(slot_level_1=True),
    lambda s:s['unit_role_list'][0].update(slot_level_1=6),
    lambda s:s['unit_role_list'][0].update(slot_level_1=0,enhance_level_1=1),
    lambda s:s['unit_role_list'][0].update(enhance_level_1=-1),
    lambda s:s.update(unit_role_list='not a list'),
])
def test_bad_detailed_progress_rejects_entire_read(patch):
    source=detailed_source();patch(source)
    with pytest.raises(SnapshotFailure):normalize(source,123,'202610141431')


def test_skill_progress_and_ex_serial_resolution():
    result = normalize(progress_source(), 123, '202610141431', [100101])
    assert result['account']['attribute_skill'] == dict(page=5, completed_merge=3)
    assert result['account']['master_skill'] == dict(node=75)
    assert len(result['account']['mapping_sha256']) == 64
    equipment = result['units'][0]['ex_equipment']
    assert equipment[0] == dict(slot=1,is_equipped=True,equipment_id=4101301,name='贵族之光',
        rarity=3,rank=2,enhancement_level=5,enhancement_pt=6000,clan_battle_equipment=False,
        effects='战斗开始后在90秒内提升自身的物理攻击力3%。；无随机附加词条')
    assert equipment[1] == dict(slot=2,is_equipped=False)
    assert 'units.100101.ex_equipment.3' in result['unknown_fields']
    text = json.dumps(result)
    assert all(v not in text for v in ('unrelated-inventory','4101302','serial_id','cb_ex_equip_slot'))


def test_partial_skill_progress_and_zero_ex_level():
    source = progress_source()
    source['princess_knight_info']['talent_skill_last_enhanced_page_node_list'][-1]['enhance_level'] = 2
    source['princess_knight_info']['team_skill_latest_node']['enhance_level'] = 0
    source['user_ex_equip'][0].update(enhancement_pt=0,rank=0)
    result = normalize(source,123,'202610141431',[100101])
    assert result['account']['attribute_skill']['completed_merge'] == 2
    assert result['account']['master_skill']['node'] == 74
    assert result['units'][0]['ex_equipment'][0]['enhancement_level'] == 0


def test_missing_future_and_noncontiguous_progress_stays_unknown():
    source = progress_source()
    source['princess_knight_info']['talent_skill_last_enhanced_page_node_list'].pop(0)
    source['princess_knight_info']['team_skill_latest_node']['node_id'] = 999999
    result = normalize(source,123,'202610141431',[100101])
    assert result['account']['attribute_skill'] is None and result['account']['master_skill'] is None
    assert {'account.attribute_skill','account.master_skill'} <= set(result['unknown_fields'])
    source['unit_list'][0]['ex_equip_slot'] = None
    result = normalize(source,123,'202610151431',[100101])
    assert 'account' in result['unknown_fields'] and 'units.100101.ex_equipment' in result['unknown_fields']


@pytest.mark.parametrize('change', [
    lambda s: s['princess_knight_info'].update(team_skill_latest_node=dict(node_id=75,enhance_level=True)),
    lambda s: s['princess_knight_info'].update(team_skill_latest_node=dict(node_id=75,enhance_level=2)),
    lambda s: s['princess_knight_info']['talent_skill_last_enhanced_page_node_list'].append(dict(node_id=443,enhance_level=5)),
    lambda s: s['princess_knight_info']['talent_skill_last_enhanced_page_node_list'].append(dict(node_id=404,enhance_level=5)),
    lambda s: s['unit_list'][0]['ex_equip_slot'][0].update(serial_id=True),
    lambda s: s['unit_list'][0]['ex_equip_slot'][0].update(ex_equipment_id=4101302),
    lambda s: s['unit_list'][0]['ex_equip_slot'][1].update(ex_equipment_id=4101301),
    lambda s: s['user_ex_equip'].append(copy.deepcopy(s['user_ex_equip'][0])),
])
def test_progress_and_ex_invalid_input_rejected(change):
    source=progress_source(); change(source)
    with pytest.raises(SnapshotFailure): normalize(source,123,'202610141431',[100101])


@pytest.mark.parametrize('change', [
    lambda x: x['user_info'].update(viewer_id=124),
    lambda x: x['user_info'].update(viewer_id=123.0),
    lambda x: x.update(unit_list=None),
    lambda x: x['unit_list'].append(copy.deepcopy(x['unit_list'][0])),
    lambda x: x['unit_list'][0].update(unit_level=True),
    lambda x: x['unit_list'][0].update(unit_rarity=7),
    lambda x: x['unit_list'][0].update(main_skill=[{}, {}, {}]),
    lambda x: x['unit_list'][0].update(unique_equip_slot=[dict(is_slot=True, id=0, enhancement_level=1)]),
])
def test_malformed_or_wrong_account_rejected(change):
    source = load(); change(source)
    with pytest.raises(SnapshotFailure): normalize(source, 123, '202610141431', [100101])


def test_existing_login_returns_load_and_no_business_routes(monkeypatch):
    from pcr_maa.labyrinth.protocol import LabyrinthClient
    client = LabyrinthClient('synthetic', 'synthetic')
    from autopcr_sync.core import readonly
    from autopcr_sync.model.responses import LoadIndexResponse
    payload = LoadIndexResponse.parse_obj(load())
    calls = []
    async def sdk(*args): return dict(code=0, uid=1, access_key='synthetic-key')
    async def request(req):
        name = type(req).__name__; calls.append(name)
        if name == 'SourceIniIndexRequest': return NS(server=['l3-prod-all-gs-gzlj.bilibiligame.net'])
        if name == 'SourceIniGetMaintenanceStatusRequest': return NS(required_manifest_ver='202610141431', res_ver='10002200')
        client.auth.viewer_id = 123
        if name == 'ToolSdkLoginRequest': return NS(is_risk=False)
        if name == 'CheckGameStartRequest': return NS(now_tutorial=True)
        if name == 'LoadIndexRequest': return payload
        assert name == 'HomeIndexRequest'
        return NS()
    monkeypatch.setattr(readonly, 'login1', sdk)
    monkeypatch.setattr(client.auth, 'request', request)
    result = asyncio.run(collect('synthetic', 'synthetic', 123, '202610141431', [100101], client_factory=lambda *a, **k: client))
    assert result['units'][0]['rank'] == 38
    assert calls == ['SourceIniIndexRequest', 'SourceIniGetMaintenanceStatusRequest', 'ToolSdkLoginRequest',
        'CheckGameStartRequest', 'LoadIndexRequest', 'HomeIndexRequest']
    assert client.phase == 'closed' and not client.auth.headers and not client._sdk_access_key


def test_stop_cancels_single_login_without_retry():
    class Client:
        def __init__(self): self.calls = 0; self.closed = False; self.auth = NS(content_version='202610141431')
        async def login(self, account):
            self.calls += 1
            await asyncio.Event().wait()
        def close(self): self.closed = True
    client = Client()
    with pytest.raises(SnapshotFailure, match='stopped'):
        asyncio.run(collect('synthetic', 'synthetic', 123, '202610141431', [100101],
            stop=lambda: client.calls > 0, client_factory=lambda *a, **k: client))
    assert client.calls == 1 and client.closed


@pytest.mark.parametrize('ids', [[], [True], [1, 1], [1, 2, 3, 4, 5, 6]])
def test_invalid_selection_never_constructs_login(ids):
    def factory(*args, **kwargs): pytest.fail('login must not be constructed')
    with pytest.raises(SnapshotFailure):
        asyncio.run(collect('synthetic', 'synthetic', 123, '202610141431', ids, client_factory=factory))


def test_worker_sanitizes_failure_and_clears_request(monkeypatch):
    import io
    import sys
    script = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/read-guide-snapshot.py'))
    request = dict(version=1, account_id=123, content_version='202610141431', unit_ids=[100101],
        credentials=dict(username='synthetic-user', password='private-synthetic-value'))
    stdin = io.StringIO(json.dumps(request) + '\n'); stdout = io.StringIO()
    monkeypatch.setattr(sys, 'stdin', stdin); monkeypatch.setattr(sys, 'stdout', stdout)
    async def failed(*args, **kwargs): raise RuntimeError('private-synthetic-value')
    assert script['main'](collector=failed) == 2
    assert json.loads(stdout.getvalue()) == dict(kind='result', outcome='snapshot_failed')


def test_worker_eof_revokes_before_client_creation(monkeypatch):
    import io
    import sys
    script = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/read-guide-snapshot.py'))
    request = dict(version=1, account_id=123, content_version='202610141431', unit_ids=[100101],
        credentials=dict(username='synthetic-user', password='synthetic-password'))
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request) + '\n'))
    stdout = io.StringIO(); monkeypatch.setattr(sys, 'stdout', stdout)
    async def bounded(*args, stop, on_captcha):
        for _ in range(100):
            if stop(): break
            await asyncio.sleep(.01)
        assert stop()
        def forbidden(*a, **k): pytest.fail('owner EOF must prevent login')
        return await collect(*args, stop=stop, client_factory=forbidden)
    assert script['main'](collector=bounded) == 2
    assert json.loads(stdout.getvalue()) == dict(kind='result', outcome='stopped')


def test_worker_emits_only_valid_captcha_page_then_result(monkeypatch):
    import io
    import sys
    script = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/read-guide-snapshot.py'))
    request = dict(version=1, account_id=123, content_version='202610141431', unit_ids=[100101],
        credentials=dict(username='synthetic-user', password='synthetic-password'))
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request) + '\n'))
    stdout = io.StringIO(); monkeypatch.setattr(sys, 'stdout', stdout)
    async def verified(*args, stop, on_captcha):
        on_captcha('http://127.0.0.1:12345/' + 'a'*43 + '/', 'sdk')
        print('private-synthetic-value')
        return normalize(load(), 123, '202610141431', [100101])
    assert script['main'](collector=verified) == 0
    messages = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [m['kind'] for m in messages] == ['captcha', 'result']
    assert messages[0]['stage'] == 'sdk' and messages[1]['outcome'] == 'complete'
    assert 'private-synthetic-value' not in stdout.getvalue() and 'synthetic-password' not in stdout.getvalue()


def test_stop_closes_active_manual_captcha_page():
    from pcr_maa.labyrinth.captcha import Challenge
    opened = []; clients = []
    class Client:
        def __init__(self, *args, manual_captcha, **kwargs):
            self.manual = manual_captcha; self.closed = False; clients.append(self)
        async def login(self, account):
            await self.manual.solve(Challenge('synthetic_gt','synthetic_challenge','synthetic_user'), 'sdk')
        def close(self): self.closed = True
    with pytest.raises(SnapshotFailure, match='stopped'):
        asyncio.run(collect('synthetic','synthetic',123,'202610141431',[100101],
            client_factory=Client, on_captcha=lambda url, stage: opened.append(url), stop=lambda: bool(opened)))
    assert clients[0].closed and len(opened) == 1
    from urllib.request import urlopen
    from urllib.error import URLError
    with pytest.raises(URLError): urlopen(opened[0],timeout=1)


def test_unknown_content_never_reuses_progress_mapping():
    result = normalize(load(), 123, '209901010101', [100101])
    assert result['account'] == dict(attribute_skill=None, master_skill=None, attribute_levels=None, unit_roles=None)
    assert {'account.attribute_levels', 'account.unit_roles'} <= set(result['unknown_fields'])
    assert 'account' in result['unknown_fields']


def test_modified_reference_is_rejected(monkeypatch):
    from pcr_maa.guide_snapshot import reference
    monkeypatch.setattr(Path, 'read_bytes', lambda self: b'{}')
    with pytest.raises(SnapshotFailure, match='invalid_snapshot'):
        reference('202610141431')


def test_clan_marked_item_in_normal_slot_is_visible_not_missing():
    from pcr_maa.guide_snapshot import extra_equipment, reference
    unknown = []
    result = extra_equipment({'ex_equip_slot': [{'serial_id': 1}]},
        {1: dict(ex_equipment_id=4201351, rank=2, enhancement_pt=6000)},
        reference('202610141431'), 'units.116701', unknown)
    assert result[0]['name'] == '【行会】旅者的命运'
    assert result[0]['enhancement_level'] == 5 and result[0]['clan_battle_equipment'] is True
    assert 'units.116701.ex_equipment.1' not in unknown
    assert result[0]['effects'] == '无附带技能；无随机附加词条'


@pytest.mark.parametrize('unlocked,raw,expected', [(5,0,5),(6,0,6),(5,3,3),(5,4,4),(5,5,5),(6,3,None),(3,None,None),(None,0,None)])
def test_selected_battle_rarity(unlocked,raw,expected):
    source=load(); source['unit_list'][0].update(unit_rarity=unlocked,battle_rarity=raw)
    result=normalize(source,123,'202610141431',[100101])
    assert result['units'][0]['battle_rarity']==expected
    assert ('units.100101.battle_rarity' in result['unknown_fields']) == (expected is None)


@pytest.mark.parametrize('raw', [True,-1,7,'0'])
def test_invalid_battle_rarity_rejected(raw):
    source=load();source['unit_list'][0]['battle_rarity']=raw
    with pytest.raises(SnapshotFailure):normalize(source,123,'202610141431',[100101])


def test_rainbow_effects_decode_values_and_preserve_unknown():
    from pcr_maa.guide_snapshot import equipment_effects,reference
    rules=reference('202610141431');meta=rules['ex_equipment']['4104501'];unknown=[]
    item={'sub_status':[dict(slot_number=i,status=12,step=s) for i,s in enumerate((5,5,4,5),1)]}
    assert '物理贯通 +11 点' in equipment_effects(item,meta,rules,'ex',unknown)
    item['sub_status'][1].update(status=6)
    text=equipment_effects(item,meta,rules,'ex',unknown)
    assert '物理贯通 +8 点' in text and '物理暴击 +4%' in text
    item['sub_status'].pop()
    assert equipment_effects(item,meta,rules,'ex',unknown) is None and 'ex.effects' in unknown
    assert equipment_effects({},meta,rules,'ex',[]) is None


@pytest.mark.parametrize('entries', [
    [dict(slot_number=1,status=12,step=0)],
    [dict(slot_number=1,status=12,step=True)],
    [dict(slot_number=1,status=12,step=5)]*2,
])
def test_invalid_sub_status_rejected(entries):
    from pcr_maa.guide_snapshot import equipment_effects,reference
    rules=reference('202610141431')
    with pytest.raises(SnapshotFailure):equipment_effects({'sub_status':entries},rules['ex_equipment']['4104501'],rules,'ex',[])
