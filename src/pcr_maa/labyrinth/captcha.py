"""Human-only Geetest bridge; adapted from upstream GeetestPageBuilder (CC BY-NC-SA 4.0).

Only temporary public challenge material enters the browser. Credentials and SDK
access keys stay in the login process. Proofs are one-use and never logged.
"""
import asyncio,json,re,secrets,threading
from dataclasses import dataclass,field
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

class CaptchaFailure(RuntimeError):pass

@dataclass(frozen=True,repr=False)
class Challenge:
    gt:str=field(repr=False)
    challenge:str=field(repr=False)
    gt_user_id:str=field(repr=False)
    @classmethod
    def parse(cls,value):
        if type(value) is not dict or type(value.get('code')) is not int or value['code']!=0:
            raise CaptchaFailure('captcha_response_invalid')
        fields=[value.get(k) for k in ('gt','challenge','gt_user_id')]
        if any(type(x) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,512}',x) for x in fields):
            raise CaptchaFailure('captcha_response_invalid')
        if value.get('captcha_type','1') not in ('1',1):raise CaptchaFailure('captcha_type_unsupported')
        return cls(*fields)

@dataclass(frozen=True,repr=False)
class Proof:
    challenge:str=field(repr=False)
    gt_user_id:str=field(repr=False)
    validate:str=field(repr=False)
    def checked(self,challenge):
        if (self.challenge!=challenge.challenge or self.gt_user_id!=challenge.gt_user_id
                or type(self.validate) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,4096}',self.validate)):
            raise CaptchaFailure('captcha_proof_invalid')
        return self

async def start_challenge(http=None):
    # Fixed independent SDK endpoint, without widening autopcr_sync SDK_PATHS.
    from autopcr_sync.sdk.bsgamesdk import modolcaptch,setsign
    import requests
    data=setsign(json.loads(modolcaptch)).encode('utf-8')
    try:
        response=await asyncio.to_thread(http or requests.post,
            'https://line1-sdk-center-login-sh.biligame.net/api/client/start_captcha',data=data,
            headers={'User-Agent':'Mozilla/5.0 BSGameSDK','Content-Type':'application/x-www-form-urlencoded'},
            timeout=(5,10),allow_redirects=False)
        if response.status_code!=200 or len(response.content)>2*1024*1024:
            raise CaptchaFailure('captcha_network_failed')
        return Challenge.parse(json.loads(response.content))
    except CaptchaFailure:raise
    except Exception:raise CaptchaFailure('captcha_network_or_protocol_failed') from None

class HumanCaptcha:
    """One ephemeral loopback page per actual server challenge; no solver."""
    def __init__(self,on_ready,*,timeout=300):
        if not callable(on_ready) or type(timeout) not in (int,float) or not 0<timeout<=600:
            raise ValueError('captcha_ui_required')
        self.on_ready=on_ready;self.timeout=timeout

    async def solve(self,challenge,stage):
        if type(challenge) is not Challenge or stage not in ('sdk','game'):
            raise CaptchaFailure('captcha_request_invalid')
        with CaptchaPage(challenge) as page:
            self.on_ready(page.url,stage)
            completed=await asyncio.to_thread(page.ready.wait,self.timeout)
            if not completed:raise CaptchaFailure('captcha_timeout')
            return page.proof()

class CaptchaPage:
    def __init__(self,challenge):
        if type(challenge) is not Challenge:raise CaptchaFailure('captcha_request_invalid')
        self.challenge=challenge;self.ready=threading.Event();self.lock=threading.Lock()
        self.value=None;self.error=None;self.nonce=secrets.token_urlsafe(32)
        self.path='/'+self.nonce+'/';page=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def setup(self):
                super().setup();self.connection.settimeout(3)
            def allowed(self):return self.headers.get('Host')==page.host
            def reply(self,status,body=b'',mime='text/plain; charset=utf-8'):
                self.send_response(status);self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
                self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Content-Security-Policy',page.csp);self.end_headers();self.wfile.write(body)
            def do_GET(self):
                if not self.allowed() or self.path!=page.path:self.reply(404);return
                with page.lock:
                    if page.ready.is_set():self.reply(410);return
                    body=page.html().encode('utf-8')
                self.reply(200,body,'text/html; charset=utf-8')
            def do_POST(self):
                if (not self.allowed() or self.headers.get('Origin')!=page.origin
                        or self.path not in (page.path+'complete',page.path+'cancel')
                        or self.headers.get('Content-Type')!='application/json'
                        or self.headers.get('Transfer-Encoding') is not None):self.reply(403);return
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=8192:raise ValueError()
                    raw=json.loads(self.rfile.read(size))
                    if self.path.endswith('/cancel'):
                        if raw!={}:raise ValueError()
                        value=None;error='captcha_cancelled'
                    else:
                        if type(raw) is not dict or set(raw)!={'validate'}:raise ValueError()
                        value=Proof(page.challenge.challenge,page.challenge.gt_user_id,raw['validate']).checked(page.challenge)
                        error=None
                except Exception:self.reply(400);return
                with page.lock:
                    if page.ready.is_set():self.reply(409);return
                    page.value=value;page.error=error;page.ready.set()
                self.reply(200,b'{"accepted":true}','application/json')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.host='127.0.0.1:'+str(self.server.server_port)
        self.origin='http://'+self.host;self.url=self.origin+self.path
        self.csp=("default-src 'none'; script-src 'nonce-"+self.nonce+"' https://static.geetest.com https://*.geetest.com; "
                  "style-src 'unsafe-inline' https://*.geetest.com; img-src data: https://*.geetest.com; "
                  "connect-src 'self' https://*.geetest.com; frame-src https://*.geetest.com; "
                  "font-src https://*.geetest.com; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.05},daemon=True)
    def __enter__(self):self.thread.start();return self
    def __exit__(self,*args):
        self.ready.set()  # wake any executor waiter when owner cancels
        self.server.shutdown();self.server.server_close();self.thread.join(2)
        with self.lock:self.value=None;self.challenge=None
    def proof(self):
        with self.lock:
            if self.error:raise CaptchaFailure(self.error)
            if self.value is None:raise CaptchaFailure('captcha_proof_invalid')
            return self.value.checked(self.challenge)
    def html(self):
        options=json.dumps(dict(gt=self.challenge.gt,challenge=self.challenge.challenge,offline=False,
            new_captcha=True,product='popup',https=True,width='100%',lang='zh-cn'),ensure_ascii=True)
        # Challenge parser rejects script markup. Never interpolate account/password.
        return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>黎明界bot · 人工安全验证</title>
<style>body{font:16px/1.7 system-ui,sans-serif;background:#f5f7fa;color:#23324a;margin:0;padding:40px 20px}
main{max-width:440px;margin:auto;background:white;border-radius:16px;padding:28px}h1{font-size:23px}
button{margin-top:24px;padding:9px 20px}#captcha{min-height:70px}</style><main><h1>人工安全验证</h1>
<p>请手动完成官方极验验证。登录账号和密码不会传入此页面。</p>
<p id="status">正在加载验证组件…</p><div id="captcha"></div><button id="cancel">取消本次登录</button></main>
<script nonce="""+json.dumps(self.nonce)+""" src="https://static.geetest.com/static/tools/gt.js"></script>
<script nonce="""+json.dumps(self.nonce)+""">
const status=document.getElementById('status');let sent=false;
async function submit(path,body){if(sent)return;sent=true;
try{const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
if(!response.ok)throw Error();status.textContent=path==='cancel'?'已取消本次登录。':'验证结果已交回登录进程，可以关闭此页。';}
catch(_){status.textContent='验证结果未能交回；请关闭此页并查看登录窗口。';}}
document.getElementById('cancel').onclick=()=>submit('cancel',{});
if(typeof initGeetest!=='function'){status.textContent='官方组件加载失败，请取消本次登录。';}
else initGeetest("""+options+""",function(captcha){captcha.appendTo('#captcha');
captcha.onReady(()=>status.textContent='请点击下方按钮，手动完成验证。');
captcha.onSuccess(()=>{const result=captcha.getValidate();
if(result&&result.geetest_validate)submit('complete',{validate:result.geetest_validate});
else status.textContent='验证结果为空，请取消本次登录。';});
captcha.onError(()=>status.textContent='验证组件加载失败，请取消本次登录。');});
</script></html>"""
