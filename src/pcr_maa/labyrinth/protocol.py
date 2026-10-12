"""Independent fixed-scope Bilibili labyrinth client.

Reuses the existing read-only authentication/wire primitives without changing
its request classes or allowlist. No general URL/body entry point is exposed.
Credentials, SID and SDK tokens are never written to disk or error messages.
"""
import asyncio
import base64
import hashlib
import random
import secrets
from enum import Enum
from contextlib import asynccontextmanager
from pathlib import Path
import sys
from .routes import Node

class ProtocolFailure(RuntimeError):
    pass

SDK_LOGIN_TIMEOUT_SECONDS = 40
AUTH_REQUEST_TIMEOUT_SECONDS = 20

class Endpoint(Enum):
    TOP='labyrinth/top'
    ENTER='labyrinth/enter'
    RESUME='labyrinth/resume'
    RETIRE='labyrinth/retire'

class LabyrinthClient:
    def __init__(self,username,password,*,content_version,http=None,manual_captcha=None,captcha_http=None):
        # Package belongs to this checkout/player. Never search arbitrary PATHs.
        vendor=Path(__file__).resolve().parents[3]/'vendor/autopcr_sync'
        if str(vendor) not in sys.path: sys.path.insert(0,str(vendor))
        from autopcr_sync.core.readonly import ReadOnlyClient
        self.auth=ReadOnlyClient(username,password,content_version=content_version)
        self.http=http
        self.manual_captcha=manual_captcha
        self.captcha_http=captcha_http
        self.logged_in=False
        self.lock=asyncio.Lock()
        self._phase='new'
        self._active_task=None
        self._sdk_uid=self._sdk_access_key=''
        self._expected_viewer_id=None
        self._session_generation=None

    @property
    def phase(self):
        """Explicit Owner phase; no session material is exposed."""
        return self._phase

    @property
    def session_generation(self):
        """Memory-only identity for this authentication; compare with is, never serialize/log."""
        return self._session_generation

    @staticmethod
    def _positive_id(value):
        return type(value) is int and 0<value<=9223372036854775807

    @asynccontextmanager
    async def _operation(self,phase=None):
        # Reject concurrent work rather than queueing a request from an obsolete
        # protocol/game phase. A close from the Owner cancels this sole operation.
        if self._phase=='closed':raise ProtocolFailure('session_closed')
        if self.lock.locked():raise ProtocolFailure('operation_in_progress')
        async with self.lock:
            if self._phase=='closed':raise ProtocolFailure('session_closed')
            if phase is not None and self._phase!=phase:raise ProtocolFailure('phase_rejected')
            if phase is None and not self.logged_in:raise ProtocolFailure('not_authenticated')
            self._active_task=asyncio.current_task()
            try:yield
            finally:self._active_task=None

    async def _verify_captcha(self,stage):
        from .captcha import start_challenge,Proof,CaptchaFailure
        if self.manual_captcha is None:raise ProtocolFailure('manual_verification_required')
        try:
            challenge=await start_challenge(self.captcha_http)
            proof=await self.manual_captcha.solve(challenge,stage)
            if type(proof) is not Proof:raise CaptchaFailure('captcha_proof_invalid')
            return proof.checked(challenge)
        except CaptchaFailure as error:
            # A callback must not turn arbitrary exception text into a GUI log.
            safe={'captcha_response_invalid','captcha_type_unsupported','captcha_proof_invalid',
                'captcha_network_failed','captcha_network_or_protocol_failed','captcha_request_invalid',
                'captcha_cancelled','captcha_timeout'}
            raise ProtocolFailure(str(error) if str(error) in safe else 'captcha_failed') from None
        except Exception:
            raise ProtocolFailure('captcha_failed') from None

    def _check_open(self):
        if self._phase=='closed':
            self.close()
            raise ProtocolFailure('session_closed')

    async def _auth_request(self,request,expected_viewer_id):
        self._check_open()
        try:result=await asyncio.wait_for(self.auth.request(request),AUTH_REQUEST_TIMEOUT_SECONDS)
        except TimeoutError:
            self.close();raise ProtocolFailure('protocol_login_timeout') from None
        # The read-only primitive can update headers while a close is pending.
        self._check_open()
        viewer=self.auth.viewer_id
        if viewer and (not self._positive_id(viewer) or viewer!=expected_viewer_id):
            raise ProtocolFailure('account_mismatch')
        return result

    async def _login_game(self,uid,access_key,expected_viewer_id):
        from autopcr_sync.model.sdkrequests import ToolSdkLoginRequest, CheckGameStartRequest
        from autopcr_sync.model.requests import LoadIndexRequest,HomeIndexRequest
        proof=None
        try:
            sdk=await self._auth_request(ToolSdkLoginRequest(uid=uid,access_key=access_key,platform='2',channel_id='1'),expected_viewer_id)
            if sdk.is_risk:
                proof=await self._verify_captcha('game')
                sdk=await self._auth_request(ToolSdkLoginRequest(uid=uid,access_key=access_key,
                    platform='2',channel_id='1',challenge=proof.challenge,validate=proof.validate,
                    seccode=proof.validate+'|jordan',captcha_type='1',image_token='',captcha_code=''),expected_viewer_id)
                proof=None
                if sdk.is_risk:raise ProtocolFailure('captcha_rejected')
            started=await self._auth_request(CheckGameStartRequest(apptype=0,campaign_data='',campaign_user=random.randint(0,100000)&~1),expected_viewer_id)
            if not started.now_tutorial:raise ProtocolFailure('tutorial_incomplete')
            await self._auth_request(LoadIndexRequest(carrier='OPPO'),expected_viewer_id)
            await self._auth_request(HomeIndexRequest(message_id=1,gold_history=0,is_first=1,tips_id_list=[]),expected_viewer_id)
            if not self._positive_id(self.auth.viewer_id) or self.auth.viewer_id!=expected_viewer_id:
                raise ProtocolFailure('account_mismatch')
        finally:proof=None

    async def login(self,expected_viewer_id):
        if not self._positive_id(expected_viewer_id):
            if self._phase=='new' and not self.lock.locked():self.close()
            raise ProtocolFailure('account_identity_required')
        from autopcr_sync.core.readonly import login1, SyncFailure
        from autopcr_sync.sdk.bsgamesdk import login2
        from autopcr_sync.model.sdkrequests import SourceIniIndexRequest, SourceIniGetMaintenanceStatusRequest
        response={}
        proof=None
        async with self._operation('new'):
            self._session_generation=None
            self._phase='authenticating'
            self.auth.viewer_id=0
            try:
                response=await asyncio.wait_for(login1(self.auth.username,self.auth.password),SDK_LOGIN_TIMEOUT_SECONDS)
                self._check_open()
                if type(response) is not dict:raise ProtocolFailure('sdk_login_failed')
                if response.get('code')==200000:
                    response.clear()
                    proof=await self._verify_captcha('sdk')
                    self._check_open()
                    response=await asyncio.wait_for(login2(self.auth.username,self.auth.password,proof.challenge,proof.gt_user_id,proof.validate),SDK_LOGIN_TIMEOUT_SECONDS)
                    self._check_open()
                    proof=None
                    if type(response) is not dict:raise ProtocolFailure('sdk_login_failed')
                    if response.get('code')==200000:raise ProtocolFailure('captcha_rejected')
                uid=response.get('uid');key=response.get('access_key')
                if (type(response.get('code')) is not int or response['code']!=0
                        or type(uid) not in (str,int) or type(uid) is int and not self._positive_id(uid)
                        or not 0<len(str(uid))<=512 or type(key) is not str or not 0<len(key)<=4096):
                    raise ProtocolFailure('sdk_login_failed')
                uid=str(uid)
                # Password is not needed for protocol/game handoffs. Do not
                # retain it while doing game startup or future protocol phases.
                self.auth.username=self.auth.password=''
                servers=await self._auth_request(SourceIniIndexRequest(),expected_viewer_id)
                if not servers.server:raise ProtocolFailure('server_rejected')
                self.auth.root=self.auth.validate_root('https://'+servers.server[0].replace('\t',''))
                for attempt in range(2):
                    try:
                        maintenance=await self._auth_request(SourceIniGetMaintenanceStatusRequest(),expected_viewer_id);break
                    except SyncFailure as error:
                        if str(error)!='app_version_changed' or attempt:raise
                if str(maintenance.required_manifest_ver)!=self.auth.content_version:raise ProtocolFailure('content_update_required')
                self.auth.headers['MANIFEST-VER']=str(maintenance.required_manifest_ver)
                await self._login_game(uid,key,expected_viewer_id)
                self._check_open()
                # Retained only for this Owner lifetime, after fresh identity
                # confirmation. No token cache, file, or password reuse exists.
                self._sdk_uid,self._sdk_access_key=uid,key
                self._expected_viewer_id=expected_viewer_id
                self.logged_in=True
                self._session_generation=object()
                self._phase='protocol'
            except ProtocolFailure:
                self.close();raise
            except asyncio.CancelledError:
                self.close();raise
            except TimeoutError:
                self.close();raise ProtocolFailure('sdk_login_timeout') from None
            except Exception:
                self.close();raise ProtocolFailure('login_failed') from None
            finally:
                self.auth.username=self.auth.password=''
                if type(response) is dict:response.clear()
                proof=None
                uid=key=None

    async def suspend_for_game(self):
        """Stop protocol business before Maa restores the game client."""
        async with self._operation('protocol'):
            if not self.logged_in or not self._sdk_uid or not self._sdk_access_key:
                self.close();raise ProtocolFailure('session_not_resumable')
            self.logged_in=False
            self.auth.headers.pop('SID',None)
            self.auth.headers.pop('REQUEST-ID',None)
            self.auth.viewer_id=0
            self._phase='game'
            return True

    async def resume_protocol(self):
        """Explicit fresh SDK game login; never replay a labyrinth operation."""
        async with self._operation('game'):
            self._session_generation=None
            if not self._sdk_uid or not self._sdk_access_key or not self._positive_id(self._expected_viewer_id):
                self.close();raise ProtocolFailure('session_not_resumable')
            self._phase='resuming'
            self.auth.headers.pop('SID',None)
            self.auth.headers.pop('REQUEST-ID',None)
            self.auth.viewer_id=0
            try:
                await self._login_game(self._sdk_uid,self._sdk_access_key,self._expected_viewer_id)
                self._check_open()
                self.logged_in=True
                self._session_generation=object()
                self._phase='protocol'
                return True
            except ProtocolFailure:
                self.close();raise
            except asyncio.CancelledError:
                self.close();raise
            except Exception:
                self.close();raise ProtocolFailure('protocol_resume_failed') from None

    @staticmethod
    def _request_fields(endpoint,fields):
        if type(endpoint) is not Endpoint:raise ProtocolFailure('endpoint_rejected')
        keys={Endpoint.TOP:set(),Endpoint.ENTER:{'guild_id','difficulty'},Endpoint.RESUME:{'enter_id'},Endpoint.RETIRE:{'enter_id'}}[endpoint]
        if type(fields) is not dict or set(fields)!=keys or any(not LabyrinthClient._positive_id(v) for v in fields.values()):
            raise ProtocolFailure('request_rejected')

    async def _request(self,endpoint,fields):
        async with self._operation():
            self._request_fields(endpoint,fields)
            return await self._request_locked(endpoint,fields)

    async def _request_locked(self,endpoint,fields):
        # Caller owns the one operation lock, including retire's readback.
        self._check_open()
        if not self.logged_in:raise ProtocolFailure('not_authenticated')
        import msgpack,requests
        key=secrets.token_hex(16).encode('ascii')
        data=dict(fields,viewer_id=base64.b64encode(self.auth.encrypt(str(self.auth.viewer_id).encode(),key)).decode('ascii'))
        body=self.auth.encrypt(msgpack.packb(data,use_bin_type=False),key)
        try:
            response=await asyncio.to_thread(self.http or requests.post,self.auth.validate_root(self.auth.root)+endpoint.value,
                data=body,headers=dict(self.auth.headers),timeout=(5,10),allow_redirects=False)
            self._check_open()
            if response.status_code!=200 or len(response.content)>16*1024*1024:raise ProtocolFailure('network_failed')
            raw=self.auth.no_null_key(self.auth.unpack(response.content))
            header=raw.get('data_headers');result=raw.get('data')
            if type(header) is not dict or type(result) is not dict:raise ProtocolFailure('response_invalid')
            if header.get('result_code') not in (0,1) or result.get('server_error'):raise ProtocolFailure('request_failed')
            # Never silently switch accounts after authentication.
            viewer=header.get('viewer_id')
            if viewer:
                if type(viewer) is str and viewer.isascii() and viewer.isdecimal() and len(viewer)<=19:viewer=int(viewer)
                if not self._positive_id(viewer) or viewer!=self.auth.viewer_id:raise ProtocolFailure('account_mismatch')
            if header.get('sid'):self.auth.headers['SID']=hashlib.md5((header['sid']+'c!SID!n').encode()).hexdigest()
            if header.get('request_id'):self.auth.headers['REQUEST-ID']=header['request_id']
            return result
        except ProtocolFailure:
            self.close();raise
        except asyncio.CancelledError:
            self.close();raise
        except Exception:
            self.close();raise ProtocolFailure('network_or_protocol_failed') from None

    @staticmethod
    def _top(value):
        if type(value) is not dict:raise ProtocolFailure('top_invalid')
        if 'enter_id' in value:
            enter=value['enter_id']
            if enter is not None and (type(enter) is not int or not 0<=enter<=9223372036854775807):
                raise ProtocolFailure('top_invalid')
        else:
            # Idle TOP can omit enter_id while retaining the last guild/difficulty.
            for field in ('labyrinth_point','labyrinth_reward_received_point','labyrinth_enhance_point'):
                counter=value.get(field)
                if type(counter) is not int or not 0<=counter<=9223372036854775807:
                    raise ProtocolFailure('top_invalid')
            for field in ('guild_id','difficulty'):
                choice=value.get(field)
                if choice is not None and (type(choice) is not int or not 0<=choice<=9223372036854775807):
                    raise ProtocolFailure('top_invalid')
        return value

    async def top(self):
        async with self._operation():
            try:return self._top(await self._request_locked(Endpoint.TOP,{}))
            except ProtocolFailure:self.close();raise

    async def enter(self,guild_id,difficulty):
        if type(guild_id) is not int or not 1<=guild_id<=5 or type(difficulty) is not int or not 1<=difficulty<=5:raise ProtocolFailure('request_rejected')
        async with self._operation():
            try:
                value=await self._request_locked(Endpoint.ENTER,dict(guild_id=guild_id,difficulty=difficulty))
                if not self._positive_id(value.get('enter_id')):raise ProtocolFailure('opening_invalid')
                value['map_list']=self._map(value.get('map_list'))
                return value
            except ProtocolFailure:self.close();raise

    async def resume(self,enter_id):
        async with self._operation():
            self._request_fields(Endpoint.RESUME,dict(enter_id=enter_id))
            try:
                value=await self._request_locked(Endpoint.RESUME,dict(enter_id=enter_id))
                value['map_list']=self._map(value.get('map_list'))
                return value
            except ProtocolFailure:self.close();raise

    async def retire(self,enter_id):
        async with self._operation():
            self._request_fields(Endpoint.RETIRE,dict(enter_id=enter_id))
            await self._request_locked(Endpoint.RETIRE,dict(enter_id=enter_id))
            # This is a fresh readback, never a retry of the consuming request.
            # Any unknown readback closes the SDK session and leaves the Owner's
            # persisted intent pending, so resume_protocol cannot replay it.
            try:
                if self._top(await self._request_locked(Endpoint.TOP,{})).get('enter_id') not in (None,0):
                    raise ProtocolFailure('retire_result_unknown')
                return True
            except ProtocolFailure:self.close();raise

    @staticmethod
    def _map(value):
        try:
            if type(value) is not list or not value or len(value)>1000:raise ValueError()
            return [Node.parse(n).payload() for n in value]
        except (ValueError,TypeError):raise ProtocolFailure('map_invalid') from None

    def close(self):
        """Terminal stop: credentials and session headers cannot be recovered."""
        self._phase='closed'
        self._session_generation=None
        self.logged_in=False
        self._sdk_uid=self._sdk_access_key=''
        self._expected_viewer_id=None
        self.auth.username=self.auth.password=''
        self.auth.viewer_id=0
        self.auth.headers.clear()
        task=self._active_task
        if task is not None and not task.done():
            try:current=asyncio.current_task()
            except RuntimeError:current=None
            if task is not current:
                try:task.get_loop().call_soon_threadsafe(task.cancel)
                except RuntimeError:pass
