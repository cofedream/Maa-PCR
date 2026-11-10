"""Offline server, SDK-window and native-channel navigation regressions."""
import asyncio
import hashlib
from types import SimpleNamespace as NS

import pytest

from pcr_maa import native
from pcr_maa.labyrinth.app_version import parse_package_version
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.game_port import MaaGamePort
from pcr_maa.labyrinth.game_session import MaaGameSession, game_session_pipeline
from pcr_maa.labyrinth.protocol import LabyrinthClient, ProtocolFailure
from pcr_maa.labyrinth.servers import SERVERS, get_server
from pcr_maa.labyrinth.session import ChannelPrompts, channel_window


def auth_stub(monkeypatch, client, *, risk=False, viewer=123):
    calls=[]
    async def request(value):
        name=type(value).__name__
        calls.append((name,client.auth.root,dict(client.auth.headers),value.dict()))
        if name=='SourceIniIndexRequest':return NS(server=[client.server.root[8:-1]])
        if name=='SourceIniGetMaintenanceStatusRequest':
            return NS(required_manifest_ver='202610251200',res_ver='10002201')
        if name=='ToolSdkLoginRequest':
            client.auth.viewer_id=viewer
            return NS(is_risk=risk)
        if name=='CheckGameStartRequest':return NS(now_tutorial=True)
        if name in ('LoadIndexRequest','HomeIndexRequest'):return NS(profile='synthetic')
        pytest.fail('unexpected authentication request')
    monkeypatch.setattr(client.auth,'request',request)
    from autopcr_sync.core import readonly
    async def reject_sdk(*_):pytest.fail('channel login touched Bilibili SDK')
    monkeypatch.setattr(readonly,'login1',reject_sdk)
    return calls


@pytest.mark.parametrize('server',[s for s in SERVERS if s!='cn-bilibili'])
def test_channel_login_uses_uid_key_channel_gateway_and_preserves_identity(monkeypatch,server,tmp_path):
    closed=[]
    def close(selected,account):closed.append((selected,account));return True
    client=LabyrinthClient(' 987654 ',' synthetic-key ',server=server,before_game_login=close,cache_root=tmp_path)
    calls=auth_stub(monkeypatch,client)
    try:
        asyncio.run(client.login(123))
        assert client.phase=='protocol' and client.auth.viewer_id==123
        assert client.auth.username==client.auth.password==''
        assert [name for name,*_ in calls]==['SourceIniIndexRequest','SourceIniGetMaintenanceStatusRequest',
            'ToolSdkLoginRequest','CheckGameStartRequest','LoadIndexRequest','HomeIndexRequest']
        assert all(root==get_server(server).root for _,root,_,_ in calls)
        assert all(headers['PLATFORM-ID']=='4' and headers['RES-KEY']==get_server(server).res_key
            and headers['DEVICE-ID']==hashlib.md5(b'987654').hexdigest() for _,_,headers,_ in calls)
        body=calls[2][3]
        assert (body['uid'],body['access_key'],body['platform'],body['channel_id'])==('987654','synthetic-key','2','1')
        assert closed==([(server,123)] if get_server(server).closes_client_before_login else [])
        assert not (tmp_path/'data/auth').exists()
    finally:client.close()


@pytest.mark.parametrize('server',[None,'','bilibili','cn-unknown',{},False])
def test_unknown_server_never_defaults_to_bilibili(server):
    with pytest.raises(ProtocolFailure,match='^unknown_game_server$'):
        LabyrinthClient('synthetic','synthetic',server=server)


@pytest.mark.parametrize('username,password',[(' ','token'),('uid',' '),('uid\n','key'),('uid','key\x00')])
def test_channel_credentials_are_validated_before_authentication(username,password):
    with pytest.raises(ProtocolFailure,match='^channel_credentials_invalid$'):
        LabyrinthClient(username,password,server='cn-xiaomi')


def test_bilibili_password_keeps_whitespace_and_uses_bilibili_headers():
    client=LabyrinthClient('user',' password ')
    try:
        assert not client.server.channel and client.auth.password==' password '
        assert client.auth.headers['PLATFORM-ID']=='2'
        assert client.auth.root==get_server().root
    finally:client.close()


def test_channel_game_risk_never_opens_bilibili_captcha(monkeypatch):
    client=LabyrinthClient('uid','key',server='cn-huawei')
    calls=auth_stub(monkeypatch,client,risk=True)
    async def captcha(*_):pytest.fail('channel attempted a Bilibili captcha')
    monkeypatch.setattr(client,'_verify_captcha',captcha)
    with pytest.raises(ProtocolFailure,match='^channel_game_verification_required$'):
        asyncio.run(client.login(123))
    assert [call[0] for call in calls][-1]=='ToolSdkLoginRequest'
    assert client.phase=='closed' and not client.auth.headers


def test_channel_account_mismatch_closes_before_any_business_request(monkeypatch):
    client=LabyrinthClient('uid','key',server='cn-huawei')
    calls=auth_stub(monkeypatch,client,viewer=999)
    with pytest.raises(ProtocolFailure,match='^account_mismatch$'):asyncio.run(client.login(123))
    assert calls[-1][0]=='ToolSdkLoginRequest' and client.phase=='closed'
    assert client.auth.username==client.auth.password=='' and not client.auth.headers


@pytest.mark.parametrize('close',[None,lambda *_:False])
def test_sensitive_channel_refuses_sdk_login_without_verified_force_stop(monkeypatch,close):
    client=LabyrinthClient('uid','key',server='cn-mumu',before_game_login=close)
    calls=auth_stub(monkeypatch,client)
    with pytest.raises(ProtocolFailure,match='^channel_client_close_(required|failed)$'):
        asyncio.run(client.login(123))
    assert not any(row[0]=='ToolSdkLoginRequest' for row in calls)


def test_each_sensitive_protocol_resume_closes_the_same_client_before_sdk_login(monkeypatch):
    closures=[]
    client=LabyrinthClient('uid','key',server='cn-tencent',before_game_login=lambda *v:closures.append(v) or True)
    auth_stub(monkeypatch,client)
    async def exercise():
        await client.login(123)
        await client.suspend_for_game()
        await client.resume_protocol()
    try:
        asyncio.run(exercise())
        assert closures==[('cn-tencent',123),('cn-tencent',123)] and client.phase=='protocol'
    finally:client.close()


@pytest.mark.parametrize('error,expected',[
    (BatchStopped('stopped'),'stopped'),
    (RuntimeError('synthetic private details'),'channel_client_close_failed'),
])
def test_force_stop_failure_keeps_fixed_public_error_and_never_logs_in(monkeypatch,error,expected):
    def close(*_):raise error
    client=LabyrinthClient('uid','key',server='cn-4399',before_game_login=close)
    calls=auth_stub(monkeypatch,client)
    with pytest.raises(ProtocolFailure,match='^'+expected+'$'):asyncio.run(client.login(123))
    assert not any(call[0]=='ToolSdkLoginRequest' for call in calls)
    assert client.phase=='closed' and not client.auth.headers


def test_channel_server_list_cannot_redirect_authentication_to_bilibili(monkeypatch):
    client=LabyrinthClient('uid','key',server='cn-huawei')
    async def wrong_server(request):
        assert type(request).__name__=='SourceIniIndexRequest'
        return NS(server=['l3-prod-all-gs-gzlj.bilibiligame.net'])
    monkeypatch.setattr(client.auth,'request',wrong_server)
    with pytest.raises(ProtocolFailure,match='^server_rejected$'):asyncio.run(client.login(123))
    assert client.phase=='closed'


@pytest.mark.parametrize('server',SERVERS)
def test_actual_selected_package_supplies_app_version_and_pipeline(server):
    selected=get_server(server)
    output=f'Packages:\n Package [{selected.package}] (abc):\n  versionName=12.0.1\n'
    assert parse_package_version(output,server=server)=='12.0.1'
    if selected.channel:
        with pytest.raises(ValueError):parse_package_version(output)
    pipeline=game_session_pipeline(123,server=server)
    assert ('LabyGameSessionColdStartObserved' in pipeline)==selected.channel


def window(*,package='com.bilibili.priconne.aligames',duplicate=False,title='登录历史',target='最近登录',portrait=False):
    size='[0,0][720,1280]' if portrait else '[0,0][1280,720]'
    item=f'<node package="{package}" text="{target}" clickable="false" bounds="[300,220][440,260]"/>'
    return (f'<?xml version="1.0"?><hierarchy rotation="0"><node package="{package}" bounds="{size}">'
        f'<node package="{package}" text="{title}" bounds="[300,100][600,150]"/>'
        f'<node package="{package}" clickable="true" bounds="[260,200][800,300]">{item}</node>'
        f'{item if duplicate else ""}</node></hierarchy>')


def test_sdk_prompt_requires_same_window_title_exact_unique_target_and_client():
    pkg=get_server('cn-aligames').package
    match,blocked=channel_window(window(),pkg,'cn-aligames')
    assert not blocked and match.ancestor_hops==1
    assert match.target==(300,220,440,260) and match.click==(260,200,800,300)
    assert channel_window(window(target='登录其他手机号'),pkg,'cn-aligames')==(None,True)
    assert channel_window(window(title='通知'),pkg,'cn-aligames')==(None,False)
    assert channel_window(window(duplicate=True),pkg,'cn-aligames')==(None,True)
    with pytest.raises(ValueError):channel_window(window(),'another.client','cn-aligames')
    assert channel_window(window(package=get_server('cn-huawei').package),get_server('cn-huawei').package,'cn-huawei')==(None,False)


@pytest.mark.parametrize('server',['cn-vivo','cn-4399','cn-aligames'])
def test_only_observed_rotation_channels_wait_for_portrait_sdk_without_click(server):
    pkg=get_server(server).package
    assert channel_window(window(package=pkg,portrait=True),pkg,server)==(None,True)
    other=get_server('cn-xiaomi').package
    with pytest.raises(ValueError,match='rotation'):channel_window(window(package=other,portrait=True),other,'cn-xiaomi')


class FakeBatch:
    stopped=staticmethod(lambda:False)
    def __init__(self):self.operations=[]
    def checkpoint(self):
        if self.stopped():raise BatchStopped('stopped')
    def transaction(self,operation,fields,send):
        self.operations.append((operation,fields));return send('a'*32)


def native_receipt(node,*,package=None):
    raw={} if package is None else dict(package=package)
    return dict(tasker_inited=True,task_id=1,native_ok=True,nodes=[dict(name=node,completed=True,
        action=dict(name=node,success=True,raw=raw))])


def prompts(monkeypatch):
    clock=[10.]
    monkeypatch.setattr('pcr_maa.labyrinth.session.time.monotonic',lambda:clock[0])
    tasker=NS(resource=NS(override_pipeline=lambda _:True))
    value=ChannelPrompts(tasker,lambda **fields:fields,'cn-aligames')
    pkg=value.server.package
    match=channel_window(window(),pkg,'cn-aligames')[0]
    monkeypatch.setattr(value,'_read',lambda batch:(match,False))
    inputs=[]
    monkeypatch.setattr(native,'run',lambda tasker,node,**_:inputs.append(node) or native_receipt(node))
    return value,FakeBatch(),clock,inputs,match


def test_prompt_needs_two_polls_rechecks_and_rate_limits_three_total_taps(monkeypatch):
    value,batch,clock,inputs,_=prompts(monkeypatch)
    for current,count in [(10,0),(10.6,1),(11.2,1),(11.8,1),(15,2),(15.6,2),(20,3)]:
        clock[0]=current
        assert value.poll(batch,dict(enter_id=1)) and len(inputs)==count
    clock[0]=20.6
    assert value.poll(batch,dict(enter_id=1))
    clock[0]=25
    with pytest.raises(BatchStopped,match='^channel_login_prompt_exhausted$'):value.poll(batch,dict(enter_id=1))
    assert len(inputs)==len(batch.operations)==3
    assert all(op=='client_channel_prompt' for op,_ in batch.operations)


def test_prompt_changed_between_observation_and_dispatch_sends_no_click(monkeypatch):
    value,batch,clock,inputs,match=prompts(monkeypatch)
    value.poll(batch,dict(enter_id=1));clock[0]+=.6
    reads=iter([(match,False),(None,False)])
    monkeypatch.setattr(value,'_read',lambda batch:next(reads))
    assert value.poll(batch,dict(enter_id=1))
    assert not inputs and not batch.operations


def test_stop_during_sdk_read_never_dispatches_input(monkeypatch):
    value,batch,clock,inputs,match=prompts(monkeypatch)
    value.poll(batch,dict(enter_id=1));clock[0]+=.6
    def read(_):batch.stopped=lambda:True;return match,False
    monkeypatch.setattr(value,'_read',read)
    with pytest.raises(BatchStopped,match='^stopped$'):value.poll(batch,dict(enter_id=1))
    assert not inputs and not batch.operations


def session_page(node,page):
    names=['LabyDateChangedObserved','LabyDisconnect6002Observed','LabyClientTitleObserved',
        'LabyClientGameHomeObserved','LabyClientAdventureObserved']
    upto=names.index(page)+1
    return dict(tasker_inited=True,task_id=1,native_ok=True,nodes=[dict(name=node,completed=True,
        recognition=[dict(name=name,algorithm='And',box=[0,0,1,1] if i==upto-1 else None)
            for i,name in enumerate(names[:upto])])])


@pytest.mark.parametrize('pages,expected',[
    (['LabyClientGameHomeObserved','LabyClientGameHomeObserved'],'channel_client_close_failed'),
    (['LabyDisconnect6002Observed'],'channel_client_close_failed'),
    (['LabyClientTitleObserved'],None),
])
def test_cold_start_requires_actual_title_before_accepting_in_game_pages(monkeypatch,pages,expected):
    session=MaaGameSession(NS(stopping=False),lambda **v:v,server='cn-mumu')
    session.cold_start_required=True
    queue=iter(pages)
    monkeypatch.setattr(session,'_channel_observe',lambda batch,node,**_:session_page(node,next(queue)))
    if expected:
        with pytest.raises(BatchStopped,match='^'+expected+'$'):session._entry(FakeBatch(),recovery=True)
    else:
        page,_=session._entry(FakeBatch(),recovery=True)
        assert page=='title' and not session.cold_start_required


def test_channel_title_retry_waits_five_seconds_and_requires_fresh_expected_identity(monkeypatch):
    session=MaaGameSession(NS(stopping=False,resource=NS(override_pipeline=lambda _:True)),
        lambda **v:v,server='cn-xiaomi')
    session.opening_snapshot=NS(enter_id=1)
    batch=FakeBatch();batch.binding=NS(account_id=123)
    clock=[0.]
    monkeypatch.setattr('pcr_maa.labyrinth.game_session.time.monotonic',lambda:clock[0])
    pages=iter([(1,'LabyClientTitleObserved'),(6,'LabyClientTitleObserved'),
        (6.5,'LabyClientAfterTitleHomeObserved'),(7,'LabyClientAfterTitleHomeObserved')])
    def observe(batch,node,**_):
        clock[0],page=next(pages)
        names=['LabyDateChangedObserved','LabyDisconnect6002Observed','LabyClientNoticeObserved',
            'LabyClientAnnouncementObserved','LabyClientAfterTitleHomeObserved','LabyClientDownloadErrorObserved','LabyClientDataDownloadObserved','LabyClientTitleObserved']
        upto=names.index(page)+1
        return dict(tasker_inited=True,task_id=1,native_ok=True,nodes=[dict(name=node,completed=True,
            recognition=[dict(name=name,algorithm='And',box=[0,0,1,1] if name==page else None)
                for name in names[:upto]])])
    monkeypatch.setattr(session,'_channel_observe',observe)
    identities=[]
    monkeypatch.setattr(session,'_title',lambda batch,expected:identities.append(expected) or (123,{'fresh_title':True}))
    monkeypatch.setattr(session,'_input',lambda batch,node:native_receipt(node))
    monkeypatch.setattr(native,'run',lambda tasker,node,**_:native_receipt(node))
    page,result=session._after_title(batch)
    assert page=='client_home' and result['title_stability']['consecutive_frames']==2
    assert identities==[123] and [op for op,_ in batch.operations]==['client_title_start']


def test_late_channel_title_frame_never_sends_another_title_click(monkeypatch):
    session=MaaGameSession(NS(stopping=False,resource=NS(override_pipeline=lambda _:True)),
        lambda **v:v,server='cn-xiaomi')
    batch=FakeBatch();clock=[0.]
    monkeypatch.setattr('pcr_maa.labyrinth.game_session.time.monotonic',lambda:clock[0])
    def observe(batch,node,**_):
        clock[0]=76
        names=['LabyDateChangedObserved','LabyDisconnect6002Observed','LabyClientNoticeObserved',
            'LabyClientAnnouncementObserved','LabyClientAfterTitleHomeObserved','LabyClientDownloadErrorObserved','LabyClientDataDownloadObserved','LabyClientTitleObserved']
        return dict(tasker_inited=True,task_id=1,native_ok=True,nodes=[dict(name=node,completed=True,
            recognition=[dict(name=name,algorithm='And',box=[0,0,1,1] if name==names[-1] else None) for name in names])])
    monkeypatch.setattr(session,'_channel_observe',observe)
    monkeypatch.setattr(session,'_input',lambda *_:pytest.fail('late title input'))
    with pytest.raises(BatchStopped,match='^game_session_readback_unknown$'):session._after_title(batch)
    assert not batch.operations


@pytest.mark.parametrize('qualified',[True,False])
def test_channel_poll_miss_requires_complete_non_input_native_evidence(monkeypatch,qualified):
    overrides=[]
    resource=NS(get_node_data=lambda _:dict(timeout=75000),override_pipeline=lambda value:overrides.append(value) or True)
    session=MaaGameSession(NS(stopping=False,resource=resource),lambda **v:v,server='cn-xiaomi')
    monkeypatch.setattr(session.channel_prompts,'poll',lambda *_:False)
    node='LabyGameSessionEntryObserved';calls=[]
    def run(*_):
        calls.append(node)
        if len(calls)>1:return native_receipt(node)
        return dict(tasker_inited=True,task_id=1,native_ok=False,nodes=[],native_evidence=dict(
            source='native_task_events',task_id=1,started=True,finished=True,
            complete=qualified,recognition_failed=True,action_started=False))
    monkeypatch.setattr(native,'run',run)
    if qualified:
        assert session._channel_observe(FakeBatch(),node,seconds=5)['native_ok'] is True
        assert len(calls)==2
    else:
        with pytest.raises(BatchStopped,match='^game_session_readback_unknown$'):
            session._channel_observe(FakeBatch(),node,seconds=5)
        assert len(calls)==1
    assert overrides[-1]=={node:dict(timeout=75000)}


@pytest.fixture(scope='module')
def offline_native(tmp_path_factory):
    import numpy as np
    from maa.tasker import Tasker
    from pcr_maa.replay import SavedImages
    native.RUNTIME=tmp_path_factory.mktemp('channel-native')
    class Controller(SavedImages):
        def __init__(self):
            super().__init__([[np.zeros((720,1280,3),np.uint8)]])
            self.stops=[];self.commands=[];self.pids=''
        def stop_app(self,package):self.stops.append(package);return True
        def shell(self,command,timeout):
            self.commands.append(command)
            if command.startswith('pidof '):return self.pids
            if command=='dumpsys window windows':
                return 'mCurrentFocus=Window{abc u0 com.bilibili.priconne.aligames/.MainActivity}'
            if command=='uiautomator dump /dev/tty':return window()
            pytest.fail('unapproved offline command')
    controller=Controller();resource=native.load_resource()
    assert native.wait_job(controller.post_connection()).succeeded
    tasker=Tasker();assert tasker.bind(resource,controller)
    tasker._resource_holder=resource
    yield tasker,controller
    assert native.wait_job(tasker.post_stop()).succeeded


def test_real_official_sdk_executes_only_selected_force_stop_and_verifies_absent_pid(offline_native):
    tasker,controller=offline_native
    game=MaaGamePort(tasker,lambda **v:v,server='cn-mumu')
    assert game.prepare_protocol_login('cn-mumu',123)
    assert controller.stops==[get_server('cn-mumu').package]
    assert controller.commands==['pidof '+get_server('cn-mumu').package]
    assert game._hard_closed==('cn-mumu',123,get_server('cn-mumu').package)
    controller.pids='1234'
    with pytest.raises(BatchStopped,match='^channel_client_close_failed$'):
        game.prepare_protocol_login('cn-mumu',123)
    assert game._hard_closed is None
    controller.pids=''


def test_real_sdk_window_dump_rechecks_precede_one_bounded_maafw_click(offline_native):
    tasker,controller=offline_native
    assistant=ChannelPrompts(tasker,lambda **v:v,'cn-aligames')
    batch=FakeBatch()
    assert assistant.poll(batch,dict(enter_id=1))
    import time
    time.sleep(.51)
    assert assistant.poll(batch,dict(enter_id=1))
    assert len(controller.clicks)==1 and not controller.swipes
    x,y=controller.clicks[0]
    assert 260<=x<800 and 200<=y<300
    assert [op for op,_ in batch.operations]==['client_channel_prompt']
