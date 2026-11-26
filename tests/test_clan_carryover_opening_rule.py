"""Synthetic regression for the user-approved compensation opening policy."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import pytest
from pcr_maa import clan_battle, clan_battle_daily_native as daily
from test_clan_battle_native import plan, calibration, calibration_v2, own_only_profile
from test_clan_battle_daily_native import executable, profile
from test_clan_formal_opening import formal_reader, read
from test_clan_battle_batch import daily_request, Port, run
from test_clan_opening import paused_port


def test_short_carry_applies_initial_controls_without_clock_or_first_ub(plan):
    port = paused_port(plan)
    port.formal_opening = True
    port.action = dict(kind='carryover')
    port.calibration = {'version':2,'daily':{'execution':{'opening':{'battle_speed':2}}}}
    port.start_seconds = 49
    port.state.update(remaining_seconds=None, battle_speed=2, opening_action_kind='carryover')
    port.state.pop('first_ub_evidence', None)
    port.state.pop('first_ub_observation', None)
    plan['set_by_unit'][str(plan['unit_ids'][0])] = False
    result = clan_battle.execute_switches(port, plan)
    assert result['settings_verified'] and result['switch_evidence'] == []
    assert result['opening_evidence']['remaining_seconds'] is None
    assert port.start_seconds == 49 and port.actions == [('set', plan['unit_ids'][0], True, False)]


def test_carry_reader_does_not_read_clock_or_require_first_ub(formal_reader, monkeypatch):
    port, reader, context, image, hits, clock = formal_reader
    port.action['kind'] = port.pending['action']['kind'] = 'carryover'
    reader.begin_opening(port.pending['intent_id'], action_kind='carryover', start_seconds=49)
    reader.opening_started = True
    monkeypatch.setattr(reader, 'read_opening_clock', lambda *a, **k: pytest.fail('carry clock must not be required'))
    result = read(reader, context, image)
    assert result is not None and result.detail['remaining_seconds'] is None
    state = result.detail
    assert state['opening_action_kind'] == 'carryover' and state['opening_start_token'] == port.pending['intent_id']
    params = dict(mode='opening_set', unit_ids=state['unit_ids'], sets=state['sets'], auto=state['auto'],
        battle_speed=state['battle_speed'], slot=0, previous=False, desired=True)
    assert reader.analyze(context, NS(image=image, custom_recognition_param=json.dumps(params))) is not None
    port.pending['intent_id'] = 'other-intent'
    assert read(reader, context, image) is None


def test_carry_pause_pipeline_retains_page_guards_without_clock(executable, plan):
    normal = daily.require_daily_execution(executable, plan)
    carry = daily.require_daily_execution(executable, plan, capability='carryover')
    assert 'CBDailyOpeningStartClock' in normal['CBDailyPauseOpening']['all_of']
    assert 'CBDailyOpeningStartClock' not in carry['CBDailyPauseOpening']['all_of']
    assert 'CBDailyOpeningBinding' in carry['CBDailyPauseOpening']['all_of']
    assert all('CBFormalOpening_opening_guards_'+str(i) in carry['CBDailyPauseOpening']['all_of'] for i in (0,1))


def test_own_carry_requires_carry_but_not_support(executable, plan):
    value = own_only_profile(deepcopy(executable))
    section = value['daily']['execution']
    section['pages'].pop('support')
    section['samples'] = [r for r in section['samples'] if r['page'] != 'support']
    for name in ('open_support','support_back','support_top','support_next'):
        section['actions'].pop(name)
    section['formation_owner'] = None
    nodes = daily.require_daily_execution(value, plan, capability='carryover')
    assert 'CBPage_carry' in nodes and 'CBPage_support' not in nodes
    section['pages'].pop('carry')
    with pytest.raises(ValueError, match='scope_missing'):
        daily.require_daily_execution(value, plan, capability='carryover')


def test_first_seen_current_same_boss_carry_needs_no_appearance_timestamp(tmp_path, daily_request):
    port = Port(daily_request)
    port.issue = {1}
    port.grant_time = 49
    observe = port.observe
    def fresh(binding):
        row = observe(binding)
        for boss in row['bosses']: boss['appeared_at'] = None
        return row
    port.observe = fresh
    result, _ = run(tmp_path, daily_request, port)
    assert result['outcome'] == 'complete' and result['carryover_settled'] == 1
    carry = next(row for row in port.executions if row['action']['kind'] == 'carryover')
    source = port.executions[0]
    assert carry['action']['boss'] == source['action']['boss'] and carry['action']['start_seconds'] == 49
    assert carry['ready']['unit_ids'] == source['ready']['unit_ids']
    assert all(boss['appeared_at'] is None for boss in carry['before']['bosses'])


@pytest.mark.parametrize('change', ['stock','target','day','unknown_start','stop','speed4','speed_bool','missing_pause'])
def test_carry_clock_exemption_keeps_live_binding_and_pause_guards(formal_reader, change):
    from datetime import timedelta
    port, reader, context, image, hits, clock = formal_reader
    port.action['kind'] = port.pending['action']['kind'] = 'carryover'
    reader.begin_opening(port.pending['intent_id'], action_kind='carryover', start_seconds=49)
    reader.opening_started = True
    clock[0] = 'unreadable clock'
    if change == 'stock': port.action = dict(port.action, stock_index=3)
    elif change == 'target': port.page.bound['instance_id'] = 'other-instance'
    elif change == 'day':
        now = port.now()
        port.now = lambda: now + timedelta(days=1)
    elif change == 'unknown_start': port.battle_start = 'not_sent'
    elif change == 'stop': context.tasker.stopping = True
    elif change == 'missing_pause': hits.remove('CBFormalOpening_paused_guards_0')
    else:
        hits.remove('CBFormalOpeningSpeed1')
        hits.add('CBFormalOpeningSpeed4' if change == 'speed4' else 'CBFormalOpeningSpeedTrue')
    assert read(reader, context, image) is None


@pytest.mark.parametrize('change', ['token','kind','team','paused','same_frame'])
def test_carry_pause_binding_keeps_identity_without_freezing_clock(change):
    state = dict(paused=True, same_frame=True, opening_action_kind='carryover',
        opening_start_token='actual-intent', unit_ids=[1,2,3,4,5], remaining_seconds=None)
    other = deepcopy(state)
    other['remaining_seconds'] = 17
    clan_battle.require_frozen_opening(other, state, carryover=True)
    if change == 'token': other['opening_start_token'] = 'wrong'
    elif change == 'kind': other['opening_action_kind'] = 'regular'
    elif change == 'team': other['unit_ids'][0] = 6
    else: other[change] = False
    with pytest.raises(RuntimeError, match='opening_not_verified'):
        clan_battle.require_frozen_opening(other, state, carryover=True)


def test_carry_native_toggle_omits_clock_but_keeps_control_readback(tmp_path, monkeypatch):
    from pcr_maa.clan_battle_native import NativeClanBattle
    from test_clan_battle_native import Registry, receipt
    resource = Registry()
    port = NativeClanBattle(NS(resource=resource), tmp_path)
    port.opening_paused, port.formal_opening, port.action = True, True, dict(kind='carryover')
    state = dict(auto=True, sets=[False]*5, unit_ids=[1,2,3,4,5], battle_speed=2, remaining_seconds=None,
        paused=True, same_frame=True, opening_action_kind='carryover', opening_start_token='intent')
    port.last_controls = deepcopy(state)
    port.read_battle_settings = lambda: deepcopy(state)
    overrides, calls = [], []
    monkeypatch.setattr(resource, 'override_pipeline', lambda nodes: overrides.append(nodes) or True)
    def run(node, **options):
        calls.append(node)
        state['sets'][0] = True
        return receipt(deepcopy(state))
    port.run = run
    actual = port.toggle_set(0, False, True)
    assert actual['sets'] == [True,False,False,False,False] and calls == ['CBOpeningSet']
    for node in overrides[-1].values():
        assert 'remaining_seconds' not in node['custom_recognition_param']


def test_carry_opening_does_not_attempt_first_ub_capture(tmp_path):
    from pcr_maa.clan_battle_native import NativeClanBattle
    from test_clan_battle_native import Registry
    port = NativeClanBattle(NS(resource=Registry()), tmp_path)
    port.formal_opening, port.action = True, dict(kind='carryover')
    port._opening_capture_timed_observer = object()
    state = dict(opening_action_kind='carryover')
    assert port._opening_capture_before_change(None, state) is state


@pytest.fixture
def compact_selection(executable):
    value = own_only_profile(deepcopy(executable))
    section = value['daily']['execution']
    section['pages'].pop('support')
    section['samples'] = [r for r in section['samples'] if r['page'] != 'support']
    for name in ('open_support','support_back','support_top','support_next'):
        section['actions'].pop(name)
    section['formation_owner'] = None
    section['formation_mode'].update(carryover=None, stock_index=None, time=None)
    row = section['carry_slots'][0]
    row = {key:deepcopy(row[key]) for key in ('stock_index','time','members','target')}
    row['members'] = {'slots':row['members']['slots']}
    section['carry_slots'] = [row]
    marker = section['pages']['carry']['labels'][0]
    section['carry_selection'] = dict(slots=[deepcopy(row)], reuse_on=deepcopy(marker), reuse_off=deepcopy(marker))
    section['pages']['carry_selection'] = deepcopy(section['pages']['carry'])
    section['actions']['select_carry']['page'] = 'carry_selection'
    section['actions']['enable_carry_team'] = dict(page='carry_selection',label=deepcopy(marker),target=[1,2,3,4])
    section['actions']['close_carry_selection'] = dict(page='carry_selection',label=deepcopy(marker),target=[1,2,3,4])
    section['samples'].append(dict(next(r for r in section['samples'] if r['page']=='carry'),page='carry_selection'))
    return value


def test_actual_own_carry_layout_uses_independent_selection_and_compact_members(compact_selection, plan):
    nodes = daily.require_daily_execution(compact_selection, plan, capability='carryover')
    assert nodes['CBAction_select_carry']['custom_recognition_param']['page'] == 'carry_selection'
    assert {'CBPage_carry_selection','CBAction_enable_carry_team','CBDailyCarryReuseOn','CBDailyCarryReuseOff'} <= nodes.keys()
    assert not {'CBDailyFormationStock','CBDailyFormationTime','CBDailyFormationCarryover','CBDailyCarry0Owner'} & nodes.keys()


@pytest.mark.parametrize('change', ['missing_page','missing_action','mixed_formation','wrong_members'])
def test_selection_schema_rejects_partial_or_unbound_layout(compact_selection, plan, change):
    section = compact_selection['daily']['execution']
    if change == 'missing_page': section['pages'].pop('carry_selection')
    elif change == 'missing_action': section['actions'].pop('enable_carry_team')
    elif change == 'mixed_formation': section['formation_mode']['time'] = [1,2,3,4]
    else: section['carry_selection']['slots'][0]['members']['slots'].pop()
    with pytest.raises(ValueError): daily.require_daily_execution(compact_selection,plan,capability='carryover')


@pytest.fixture
def selected_carry_binding(plan):
    from datetime import datetime, timezone
    from pcr_maa.game_day import current_game_day
    port = object.__new__(daily.NativeClanDailyAdapter)
    action = dict(kind='carryover', slot=1, stock_index=3, start_seconds=49,
        boss=plan['boss'], phase='B', round=5, instance_id='new-instance')
    stock = dict(stock_index=3,time=49,using_unit=list(plan['unit_ids']),support_unit=None,support_owner_viewer_id=None)
    now = datetime.now(timezone.utc)
    port.now = lambda:now
    port.action, port.plan, port.support_unit_id = action,plan,None
    port._account_binding = object()
    port._account_source = lambda:'same-owner-connection'
    port.account_binding_valid = lambda job:True
    bound = dict(formal=True,**{key:action[key] for key in ('boss','phase','round','instance_id')})
    port._selected_carry = (deepcopy(action),deepcopy(stock),port._account_binding,port._account_source(),current_game_day(now))
    return port,stock,bound


@pytest.mark.parametrize('change', [None,'missing','stock','time','team','support','instance','account','connection','day'])
def test_selected_carry_is_bound_to_actual_stock_team_and_same_owner(selected_carry_binding, change):
    from datetime import timedelta
    port,stock,bound = selected_carry_binding
    if change == 'missing': port._selected_carry = None
    elif change == 'stock': port.action['stock_index'] = 2
    elif change == 'time': port.action['start_seconds'] = 50
    elif change == 'team': port.plan = dict(port.plan,unit_ids=[1,2,3,4,5])
    elif change == 'support': port.support_unit_id = port.plan['unit_ids'][0]
    elif change == 'instance': bound['instance_id'] = 'another-boss-instance'
    elif change == 'account': port._account_binding = object()
    elif change == 'connection': port._account_source = lambda:'new-connection'
    elif change == 'day':
        now = port.now()
        port.now = lambda:now+timedelta(days=1)
    if change:
        with pytest.raises(RuntimeError,match='selected_carry'):
            port.require_selected_carry(bound)
    else:
        assert port.require_selected_carry(bound) == stock and stock['time']==49


@pytest.mark.parametrize('change', [None,'reuse_off','time','team','inventory','account'])
def test_select_carry_requires_live_inventory_and_reuse_checkbox(compact_selection,plan,selected_carry_binding,change):
    port,stock,bound = selected_carry_binding
    port.stocks=[deepcopy(stock)]
    value=dict(stocks=[dict(deepcopy(stock),target=[10,20,3,4])],reuse_team=True,evidence={'frame_pixel_sha256':'a'*64})
    page=daily.ClanBattleDailyExecutionPage(compact_selection,plan,port)
    page.read=lambda *args:deepcopy(value)
    page.hit=lambda *args:True
    if change=='reuse_off': value['reuse_team']=False
    elif change=='time': value['stocks'][0]['time']=50
    elif change=='team': value['stocks'][0]['using_unit'][0]=999001
    elif change=='inventory': port.stocks=[]
    elif change=='account': port.account_binding_valid=lambda job:False
    if change:
        with pytest.raises(RuntimeError,match='carry_changed'): page.action(None,None,'select_carry')
    else:
        assert page.action(None,None,'select_carry') == [10,20,3,4]
        assert port.require_selected_carry(bound)['evidence']==value['evidence']


def test_carry_cancel_returns_via_selection_without_another_start(plan):
    port=object.__new__(daily.NativeClanDailyAdapter)
    port.plan,port.action=plan,dict(kind='carryover')
    port.calibration={'daily':{'execution':{'pages':{},'carry_selection':{}}}}
    port.account_binding_valid=lambda job:True
    calls=[]
    def wait(names,**kwargs):
        calls.append(('wait',names))
        return 'CBPage_formation' if len(calls)==1 else names[0]
    port.wait=wait
    port.run=lambda name,**kwargs:calls.append(('run',name))
    port.enter_lobby()
    assert [value for kind,value in calls if kind=='run']==[
        'CBAction_cancel_formation','CBAction_close_carry_selection','CBAction_back_to_lobby']


@pytest.mark.parametrize('kind', ['regular','carryover'])
def test_only_carry_after_start_omits_opening_age_while_start_stays_fresh(formal_reader,kind,monkeypatch):
    from datetime import timedelta
    port,reader,context,image,_,_=formal_reader
    port.action['kind']=port.pending['action']['kind']=kind
    before=deepcopy(port.pending)
    now=port.now()
    port.now=lambda:now+timedelta(seconds=120)
    if kind=='carryover':
        assert port.opening_binding(pausing=True)==before
    else:
        with pytest.raises(RuntimeError,match='opening_binding_changed'):port.opening_binding(pausing=True)
    monkeypatch.setattr(port.page,'read',lambda *args:{})
    monkeypatch.setattr(port.page,'hit',lambda *args:True)
    with pytest.raises(RuntimeError,match='final_start_binding_changed'):
        port.page.action(context,image,'start')
    assert port.pending==before


@pytest.fixture
def october_carry_scenario(plan):
    from pcr_maa.clan_battle_calibration import load_calibration
    plan = dict(plan, period='2026-10', content_version='202610141448')
    value = load_calibration(plan, capability='carryover')
    assert value['daily']['execution']['formation_mode']['carryover'] is None
    assert 'carry_selection' in value['daily']['execution']
    return plan, value


def test_current_profile_nonempty_observe_does_not_require_empty_inventory(october_carry_scenario):
    from datetime import datetime,timezone
    from pcr_maa.game_day import current_game_day
    from test_clan_battle_native import receipt
    plan, current_carry_profile = october_carry_scenario
    port=object.__new__(daily.NativeClanDailyAdapter)
    now=datetime(2026,10,28,6,tzinfo=timezone.utc)
    port.now=lambda:now
    binding=dict(account_id=plan['account_id'],server=plan['server'],period=plan['period'],game_day=current_game_day(now),batch_id='synthetic-batch')
    port.calibration,port.plan=current_carry_profile,plan
    port.pending,port.action=None,None
    port._account_binding=object()
    port._account_source=lambda:'same-owner-connection'
    port.account_binding_valid=lambda _:True
    port.account_receipt={'source':'synthetic-account'}
    port.boss_history={}
    port.enter_lobby=lambda:None
    port.recover_inheritance=lambda:None
    regular={slot:dict(intent=dict(intent_id=str(slot)*64,ready=dict(unit_ids=[uid+(slot-3)*10000 for uid in plan['unit_ids']],support_unit=None))) for slot in (1,2,3)}
    port.bind_history(binding,regular)
    stock=dict(stock_index=3,time=49,using_unit=list(plan['unit_ids']),support_unit=None,support_owner_viewer_id=None)
    inventory=dict(page='carry',stocks=[stock],evidence={'frame_pixel_sha256':'a'*64})
    lobby=dict(page='lobby',period=plan['period'],phase='D',carry_count=1,cp=900,remaining_count=0,
        bosses=[dict(boss=n,round=25,phase='D',available=True) for n in range(1,6)])
    calls=[]
    def read(page):
        value=deepcopy(lobby if page=='lobby' else inventory)
        native_receipt=receipt(value)
        native_receipt['nodes'][-1].update(name='CBPage_'+page,completed=True)
        return value,native_receipt
    port.read_page=read
    def wait(names,**kwargs):
        page='carry' if 'CBPage_carry' in names else 'lobby'
        port.last_receipt=read(page)[1]
        return 'CBPage_'+page
    port.wait=wait
    port.run=lambda node,**kwargs:calls.append(node)
    observed=port._observe_once(binding)
    assert observed['carry_stock_indices']==[3] and observed['remaining_count']==0
    assert port.stocks==[stock] and port._regular_empty_observation is None
    assert observed['reset_evidence']['inventory']['nodes'][-1]['name']=='CBPage_carry'
    assert calls==['CBAction_open_carry','CBAction_close_carry']


@pytest.mark.parametrize('fault',[None,'expired','owner','wrong_team'])
def test_current_profile_carry_start_reuses_selection_and_keeps_start_guards(october_carry_scenario,selected_carry_binding,fault):
    import numpy as np
    from datetime import timedelta
    from pcr_maa.game_day import current_game_day
    from test_clan_battle_native import context_for
    port,stock,bound=selected_carry_binding
    plan, current_carry_profile = october_carry_scenario
    port.plan = plan
    nodes=daily.require_daily_execution(current_carry_profile,plan,capability='carryover')
    labels=current_carry_profile['daily']['execution']['pages']['formation']['labels']
    hits={f'CBDaily_formation_labels_{i}' for i in range(len(labels))}|{'CBDailyFormationRegular','CBDailyActionLabel_start'}
    context=context_for(nodes,{},hits)
    page=daily.ClanBattleDailyExecutionPage(current_carry_profile,plan,port)
    page.bound,page.current=bound,'formation'
    team=dict(bound,battle_kind='carryover',member_count=5,unit_ids=list(plan['unit_ids']),stars=list(plan['stars']),support_unit_id=None,support_owner_viewer_id=None)
    port.characters=NS(formation=lambda *args:deepcopy(team))
    ready=dict(unit_ids=list(plan['unit_ids']),stars=list(plan['stars']),support_unit=None,support_owner_viewer_id=None)
    port.pending=dict(action=deepcopy(port.action),observed_at=port.now().isoformat(),game_day=current_game_day(port.now()),ready=ready)
    port.require_regular_empty_inventory=lambda *args:pytest.fail('carry must not enter ordinary empty-stock guard')
    calls=[]
    require=port.require_selected_carry
    port.require_selected_carry=lambda value:calls.append('carry') or require(value)
    if fault=='expired':
        now=port.now();port.now=lambda:now+timedelta(seconds=61)
    elif fault=='owner':port._account_binding=object()
    elif fault=='wrong_team':team['unit_ids'][0]=999001
    if fault:
        with pytest.raises(RuntimeError):page.action(context,np.zeros((720,1280,3),np.uint8),'start')
        assert page.expected!='battle'
    else:
        target=page.action(context,np.zeros((720,1280,3),np.uint8),'start')
        assert target==current_carry_profile['daily']['execution']['actions']['start']['target']
        assert page.expected=='battle' and len(calls)>=2
