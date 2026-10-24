"""Run only in an isolated, networkless throwaway container; uses disposable keys."""
import base64
import asyncio
import importlib.util
import os
import pwd
import shutil
import subprocess
import socket
import socketserver
import select
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import jwt
import websockets
from cryptography.hazmat.primitives.asymmetric import rsa

os.environ['WECHAT_ACCESS_AUD'] = 'integration-test-audience'
os.environ['WECHAT_ACCESS_TEAM'] = 'test-team'
os.environ['WECHAT_DOMAIN'] = 'wechat.example.com'
os.environ['WECHAT_ALLOWED_EMAILS'] = 'test@example.invalid'
spec = importlib.util.spec_from_file_location('guard', '/usr/local/lib/wechat-access-guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
guard.signing_key = lambda _kid: key.public_key()
server = guard.Server(('127.0.0.1', 8090), guard.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
websocket_ready = threading.Event()
async def fixture_websocket(peer):
    for name in ('Authorization', 'Cookie', 'Cf-Access-Jwt-Assertion'):
        assert name not in peer.request.headers, f'Credential header leaked to desktop backend: {name}'
    await peer.send('MODE websockets')
    async for message in peer:
        await peer.send(message)
async def websocket_fixture():
    async with websockets.serve(fixture_websocket, '127.0.0.1', 8082):
        websocket_ready.set()
        await asyncio.Event().wait()
threading.Thread(target=lambda: asyncio.run(websocket_fixture()), daemon=True).start()
assert websocket_ready.wait(5)

password = 'disposable-integration-password'
account = pwd.getpwnam('abc')
Path('/run/secrets').mkdir(parents=True, exist_ok=True)
Path('/run/secrets/web-password').write_text(password+'\n')
Path('/run/secrets/web-password').chmod(0o400)
Path('/run/secrets/session-key').write_text(os.urandom(32).hex()+'\n')
Path('/run/secrets/session-key').chmod(0o400)
shutil.copytree('/usr/share/selkies/selkies-dashboard', '/usr/share/selkies/web', dirs_exist_ok=True)
for path in ('/config', '/config/Desktop'):
    Path(path).mkdir(exist_ok=True)
    os.chown(path, account.pw_uid, account.pw_gid)
    Path(path).chmod(0o700)
probe = Path('/config/Desktop/probe.txt')
probe.write_bytes('文件下载验证\n'.encode())
os.chown(probe,account.pw_uid,account.pw_gid)
probe.chmod(0o600)
private = Path('/config/private.txt')
private.write_text('outside-upload-directory')
os.chown(private,account.pw_uid,account.pw_gid)
private.chmod(0o600)
Path('/config/Desktop/symlink.txt').symlink_to(private)
subprocess.run(['bash', '/custom-cont-init.d/99-wechat-hardening'], check=True)
nginx = subprocess.Popen(['nginx','-g','daemon off;'])

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args): return None
opener = urllib.request.build_opener(NoRedirect)
now = int(time.time())
token = jwt.encode(dict(iss=guard.ISSUER, aud=guard.AUDIENCE, sub='test-user', email='test@example.invalid',
                        type='app', iat=now, nbf=now, exp=now+300), key, algorithm='RS256', headers={'kid':'test-key'})
basic = 'Basic '+base64.b64encode(('wechat:'+password).encode()).decode()
headers = {'Authorization':basic,'Cf-Access-Jwt-Assertion':token}
def request(path, custom=None):
    try:r = opener.open(urllib.request.Request('http://127.0.0.1:3000'+path,headers=headers if custom is None else custom),timeout=10)
    except urllib.error.HTTPError as e:r=e
    return r.status,dict(r.headers),r.read()
try:
    for _ in range(40):
        try:
            status,login_headers,_=request('/')
            if status == 200:break
        except urllib.error.URLError:pass
        time.sleep(.05)
    assert status == 200
    assert 'WWW-Authenticate' not in login_headers
    cookie=login_headers['Set-Cookie']
    for attribute in ('Secure','HttpOnly','SameSite=Lax','Path=/','Max-Age=2592000'):
        assert attribute in cookie
    print('PASS: valid JWT plus password opens frontend and issues protected persistent cookie',flush=True)
    assert request('/',{})[0] == 403
    status,challenge,_=request('/',{'Cf-Access-Jwt-Assertion':token})
    assert status == 401 and 'Basic realm="WeChat"' in challenge['WWW-Authenticate']
    assert request('/',{'Authorization':basic})[0] == 403
    assert request('/',dict(headers,Origin='https://attacker.invalid'))[0] == 403
    assert request('/',dict(headers,**{'Cf-Access-Jwt-Assertion':'forged'}))[0] == 403
    assert request('/_access_verify')[0] == 404
    print('PASS: missing credentials, forged token and cross-origin requests rejected',flush=True)
    headers={'Cf-Access-Jwt-Assertion':token,'Cookie':cookie.split(';',1)[0]}
    for _ in range(5):
        status,h,_=request('/')
        assert status == 200 and 'WWW-Authenticate' not in h
    assert request('/',{'Cookie':headers['Cookie']})[0] == 403
    assert request('/',dict(headers,Cookie='__Host-wechat_session=forged'))[0] == 401
    print('PASS: refresh without Authorization header; cookie alone cannot bypass Access',flush=True)
    status,_,html=request('/')
    assert status==200
    assert html.index(b'/wechat-mobile-ime.js?v=1') < html.index(b'<script type="module"')
    status,script_headers,script_body=request('/wechat-mobile-ime.js?v=1')
    assert status==200 and 'javascript' in script_headers.get('Content-Type','')
    assert b'compositionend' in script_body
    print('PASS: mobile IME fix loads before upstream module and remains authenticated',flush=True)
    for asset,mime in (('wechat-mobile-tools.js','javascript'),('wechat-mobile-tools.css','text/css'),('wechat-mobile-viewport.js','javascript'),('wechat-safari-codecs.js','javascript')):
        version='2' if asset=='wechat-mobile-viewport.js' else '1'
        assert html.index(('/'+asset+'?v='+version).encode()) < html.index(b'<script type="module"')
        assert request('/'+asset,{})[0] == 403
        status,h,body=request('/'+asset)
        assert status==200 and mime in h.get('Content-Type','') and body
    assert html.index(b'name="viewport"') < html.index(b'/wechat-mobile-viewport.js?v=2')
    assert b'/wechat-mobile-viewport.js?v=1' not in html
    print('PASS: floating mobile controls load before upstream module and require identity',flush=True)
    async def check_websocket_routes():
        for path in ('/websockets', '/websocket'):
            async with websockets.connect('ws://127.0.0.1:3000'+path, additional_headers=headers,
                                          origin=guard.ORIGIN, proxy=None) as peer:
                assert await asyncio.wait_for(peer.recv(),timeout=5) == 'MODE websockets'
                await peer.send('gateway-websocket-roundtrip')
                assert await asyncio.wait_for(peer.recv(),timeout=5) == 'gateway-websocket-roundtrip'
        for custom, origin in (({'Authorization':basic},guard.ORIGIN),(headers,'https://attacker.invalid')):
            try:
                async with websockets.connect('ws://127.0.0.1:3000/websockets', additional_headers=custom,
                                              origin=origin, proxy=None):
                    raise AssertionError('Unauthorized WebSocket accepted')
            except websockets.exceptions.InvalidStatus as error:
                assert error.response.status_code == 403
    asyncio.run(check_websocket_routes())
    print('PASS: frontend WebSocket upgrade and roundtrip through gateway; invalid identity/origin blocked',flush=True)
    status,h,body=request('/files/')
    print('Directory response:', status, h.get('Content-Disposition'), flush=True)
    assert status == 200 and 'Content-Disposition' not in h
    status,h,body=request('/files/probe.txt')
    print('Download response:', status, h.get('Content-Disposition'), 'bytes:',len(body), flush=True)
    assert status == 200 and body == probe.read_bytes() and h['Content-Disposition']=='attachment'
    symlink_status=request('/files/symlink.txt')[0]
    traversal_status=request('/files../private.txt')[0]
    print('Blocked path responses:',symlink_status,traversal_status,flush=True)
    assert symlink_status in (403,404)
    assert traversal_status == 404
    print('PASS: private directory listing and Chinese file download; symlink and alias escape blocked',flush=True)
    if os.environ.get('WECHAT_WEBKIT_TEST') == '1':
        from playwright.sync_api import sync_playwright
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                        '-keyout','/tmp/webkit-test.key','-out','/tmp/webkit-test.crt',
                        '-subj','/CN=wechat.example.com'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        site=Path('/etc/nginx/sites-available/default')
        site.write_text(site.read_text()+'''\nserver {
            listen 443 ssl;
            server_name wechat.example.com;
            ssl_certificate /tmp/webkit-test.crt;
            ssl_certificate_key /tmp/webkit-test.key;
            location / {
                proxy_pass http://127.0.0.1:3000;
                proxy_http_version 1.1;
                proxy_set_header Host wechat.example.com;
                proxy_set_header Upgrade $http_upgrade;
                proxy_set_header Connection "upgrade";
                proxy_set_header Cf-Access-Jwt-Assertion "'''+token+'''";
            }
        }\n''')
        Path('/usr/share/selkies/web/session-browser-check.html').write_text('<!doctype html><title>Session test</title><p>Session test</p>')
        subprocess.run(['nginx','-t'],check=True)
        subprocess.run(['nginx','-s','reload'],check=True)
        time.sleep(.3)
        # WebKit's network process need not share the fixture's hosts resolver.
        # A loopback-only CONNECT proxy keeps the real HTTPS hostname without DNS or Internet access.
        class FixtureProxy(socketserver.StreamRequestHandler):
            def handle(self):
                first=self.rfile.readline(4096)
                while self.rfile.readline(4096) not in (b'\r\n',b'\n',b''):
                    pass
                if not first.startswith(b'CONNECT wechat.example.com:443 '):
                    self.wfile.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                    return
                try:
                    with socket.create_connection(('127.0.0.1',443),timeout=5) as upstream:
                        self.wfile.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
                        self.wfile.flush()
                        upstream.settimeout(None)
                        while True:
                            readable,_,_=select.select([self.connection,upstream],[],[],30)
                            if not readable:return
                            for source in readable:
                                data=source.recv(65536)
                                if not data:return
                                (upstream if source is self.connection else self.connection).sendall(data)
                except OSError:
                    pass
        class ProxyServer(socketserver.ThreadingTCPServer):
            allow_reuse_address=True
            daemon_threads=True
        proxy=ProxyServer(('127.0.0.1',8877),FixtureProxy)
        threading.Thread(target=proxy.serve_forever,daemon=True).start()
        with sync_playwright() as playwright:
            first=playwright.webkit.launch(headless=True,proxy={'server':'http://127.0.0.1:8877'})
            context=first.new_context(**playwright.devices['iPhone 13'],ignore_https_errors=True,
                                      http_credentials={'username':'wechat','password':password})
            page=context.new_page()
            response=page.goto('https://wechat.example.com/session-browser-check.html',wait_until='domcontentloaded')
            assert response.status==200
            cookies=context.cookies()
            session=next(c for c in cookies if c['name']==guard.SESSION_COOKIE)
            assert session['secure'] and session['httpOnly'] and session['sameSite']=='Lax'
            assert session['path']=='/' and session['domain']=='wechat.example.com'
            assert session['expires']>time.time()+29*86400
            state=context.storage_state()
            first.close()
            # A fresh WebKit process has no cached HTTP Basic credentials at all.
            second=playwright.webkit.launch(headless=True,proxy={'server':'http://127.0.0.1:8877'})
            context=second.new_context(**playwright.devices['iPhone 13'],ignore_https_errors=True,storage_state=state)
            page=context.new_page()
            for _ in range(3):
                response=page.goto('https://wechat.example.com/session-browser-check.html',wait_until='domcontentloaded')
                assert response.status==200
                response=page.reload(wait_until='domcontentloaded')
                assert response.status==200
                result=page.evaluate('''() => new Promise((resolve,reject) => {
                    const ws=new WebSocket('wss://'+location.host+'/websockets');
                    const timer=setTimeout(()=>{ws.close();reject(new Error('WebSocket timeout'))},8000);
                    ws.onerror=()=>{clearTimeout(timer);reject(new Error('WebSocket authentication failed'))};
                    ws.onmessage=e=>{
                        if(e.data==='MODE websockets') ws.send('webkit-session-roundtrip');
                        else if(e.data==='webkit-session-roundtrip') {clearTimeout(timer);ws.close();resolve('ok')}
                    };
                })''')
                assert result=='ok'
            second.close()
        print('PASS: iPhone-emulated WebKit saves first-party cookie; fresh browser process refreshes and reconnects without credentials',flush=True)
finally:
    nginx.terminate()
    nginx.wait(timeout=5)
    server.shutdown()
