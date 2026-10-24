"""Isolated iPhone WebKit UI and real upstream file-sender regression.

Run inside the WebKit SDK image; embed only public deployment assets.
The WebSocket fixture saves to temporary memory, never real WeChat data.
"""
import asyncio
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import websockets
from playwright.sync_api import sync_playwright

assets = json.loads(base64.b64decode('__ASSETS_B64__'))
core = Path('/usr/share/selkies/selkies-dashboard/src/selkies-core.js').read_text()
change = core[core.index('async function Ri('):core.index('const oi=async',core.index('async function Ri('))]
sender = core[core.index('function li('):core.index('function zi(',core.index('function li('))]
fixture_js = '''let k=false,s=new WebSocket('ws://127.0.0.1:8082'),ot={};const Pt=0,Bt=65536;
window.testSocket=s;window.testReady=false;s.onopen=()=>window.testReady=true;
''' + change + sender + '''
document.getElementById('globalFileInput').addEventListener('change',Ri);
window.leakedClicks=0;window.addEventListener('click',()=>window.leakedClicks++);
'''
saved = {}
requests = []
mode = {'refuse': False}
ready = threading.Event()

async def receiver(peer):
    name, data = None, bytearray()
    async for message in peer:
        if isinstance(message, str) and message.startswith('FILE_UPLOAD_START:'):
            name, expected = message[len('FILE_UPLOAD_START:'):].rsplit(':',1)
            expected = int(expected)
            assert '/' not in name and '\\' not in name and not name.startswith('.')
            data = bytearray()
        elif isinstance(message, bytes):
            assert message[0] == 1 and name is not None
            data.extend(message[1:])
        elif isinstance(message, str) and message.startswith('FILE_UPLOAD_END:'):
            assert len(data) == expected
            # Force the client to wait for actual server persistence.
            await asyncio.sleep(.2)
            if not mode['refuse']:
                saved[name] = bytes(data)
            name = None

async def ws_server():
    async with websockets.serve(receiver,'127.0.0.1',8082):
        ready.set()
        await asyncio.Event().wait()

threading.Thread(target=lambda:asyncio.run(ws_server()),daemon=True).start()
assert ready.wait(5)
html = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/tools.css"><script src="/tools.js"></script></head><body>
<button class="virtual-keyboard-button">old keyboard</button><input id="keyboard-input-assist">
<div id="overlayInput"></div><input type="file" id="globalFileInput" hidden>
<script src="/fixture.js"></script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = {'/': html, '/tools.js': assets['mobile-tools.js'], '/tools.css': assets['mobile-tools.css'], '/fixture.js': fixture_js}.get(self.path)
        if data is None:self.send_error(404);return
        data = data.encode()
        self.send_response(200)
        self.send_header('Content-Type','text/css' if self.path.endswith('.css') else 'text/html; charset=utf-8' if self.path=='/' else 'application/javascript; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)
    def do_HEAD(self):
        name = unquote(self.path.removeprefix('/files/'))
        requests.append(name)
        data = saved.get(name)
        self.send_response(200 if data is not None else 404)
        if data is not None:self.send_header('Content-Length',str(len(data)))
        self.end_headers()
    def log_message(self,*_):pass

server = ThreadingHTTPServer(('127.0.0.1',8060),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
with sync_playwright() as pw:
    browser = pw.webkit.launch(headless=True)
    context = browser.new_context(**pw.devices['iPhone 13'])
    page = context.new_page()
    errors = []
    page.on('pageerror',lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8060/',wait_until='load')
    page.wait_for_function('window.testReady')
    toggle = page.locator('#wechat-tool-toggle')
    assert not page.locator('.virtual-keyboard-button').is_visible()
    toggle.tap()
    assert toggle.get_attribute('aria-expanded') == 'true'
    assert page.locator('#wechat-tool-actions button').count() == 3
    page.screenshot(path='/tmp/mobile-tools-preview.png')
    page.locator('[data-tool="keyboard"]').tap()
    assert page.evaluate('document.activeElement.id') == 'keyboard-input-assist'
    assert toggle.get_attribute('aria-expanded') == 'false'
    assert page.evaluate('window.leakedClicks') == 0
    print('PASS: replaced button, expand/collapse, keyboard focus and no remote-click leakage',flush=True)

    before = toggle.bounding_box()
    # Mouse pointer shares the exact pointer-event drag handlers used by touch.
    page.mouse.move(before['x']+28,before['y']+28)
    page.mouse.down();page.mouse.move(100,350,steps=12);page.mouse.up()
    after = toggle.bounding_box()
    assert abs(after['x']-before['x']) > 40
    assert toggle.get_attribute('aria-expanded') == 'false', 'Drag toggled menu'
    page.reload();page.wait_for_function('window.testReady')
    restored = toggle.bounding_box()
    assert abs(after['x']-restored['x']) < 2 and abs(after['y']-restored['y']) < 2
    page.set_viewport_size({'width':844,'height':250})
    toggle.tap()
    for button in page.locator('#wechat-tool-actions button').all():
        box=button.bounding_box()
        assert box['x']>=0 and box['x']+box['width']<=844 and box['y']>=0 and box['y']+box['height']<=250,box
    toggle.tap()
    page.set_viewport_size({'width':390,'height':664})
    # A menu opened from the middle must also fit when the keyboard reduces height.
    page.evaluate("localStorage.setItem('wechat.mobile-tools.position.v1',JSON.stringify({x:.5,y:.5}))")
    page.reload();page.wait_for_function('window.testReady')
    for size in ({'width':390,'height':350},{'width':390,'height':250},{'width':390,'height':664}):
        page.set_viewport_size(size)
        toggle.tap()
        for button in page.locator('#wechat-tool-actions button').all():
            box=button.bounding_box()
            assert box['x']>=0 and box['x']+box['width']<=size['width'] and box['y']>=0 and box['y']+box['height']<=size['height'],box
        toggle.tap()
    print('PASS: dragging persists through reload; short landscape menu stays inside viewport',flush=True)

    toggle.tap()
    with page.expect_file_chooser() as photo_chooser:
        page.locator('[data-tool="photo"]').tap()
    assert photo_chooser.value.is_multiple()
    assert page.locator('#wechat-photo-picker').get_attribute('accept') == 'image/*'
    photo_bytes = b'photo-fixture-bytes'
    photo_chooser.value.set_files({'name':'中文照片.jpg','mimeType':'image/jpeg','buffer':photo_bytes})
    page.wait_for_function('document.querySelector("#wechat-upload-notice p").textContent.startsWith("已上传 1 项")')
    photo_path=next(name for name in saved if name.endswith('_中文照片.jpg'))
    assert saved[photo_path] == photo_bytes
    assert requests.count(photo_path)>=2, 'Client declared success before disk confirmation'
    print('PASS: native multi-photo picker, actual Selkies WebSocket sender and disk confirmation',flush=True)

    toggle.tap()
    with page.expect_file_chooser() as file_chooser:
        page.locator('[data-tool="file"]').tap()
    assert not page.locator('#wechat-file-picker').get_attribute('accept')
    content1='文件内容\n'.encode()
    content2=b'x'*150000
    file_chooser.value.set_files([
        {'name':'报告:测试.txt','mimeType':'text/plain','buffer':content1},
        {'name':'报告:测试.txt','mimeType':'text/plain','buffer':content2},
        {'name':'empty.txt','mimeType':'text/plain','buffer':b''}])
    page.wait_for_function('document.querySelector("#wechat-upload-notice p").textContent.startsWith("已上传 3 项")')
    file_paths=[name for name in saved if name != photo_path]
    assert len(file_paths)==3 and len(set(file_paths))==3
    assert all(':' not in name for name in file_paths)
    assert sorted(len(saved[name]) for name in file_paths)==sorted([len(content1),len(content2),0])
    assert set(saved[name] for name in file_paths)=={content1,content2,b''}
    print('PASS: file picker, multi-file/chunk transfer, zero-byte files, safe names and no overwrites',flush=True)

    count=len(saved)
    page.evaluate('''() => {const p=document.getElementById('wechat-file-picker');const d=new DataTransfer();d.items.add(new File([new Blob([new ArrayBuffer(101*1024*1024)])],'large.bin'));p.files=d.files;p.dispatchEvent(new Event('change'));}''')
    assert '100 MiB' in page.locator('#wechat-upload-notice p').text_content()
    assert len(saved)==count
    page.evaluate('''() => {const p=document.getElementById('wechat-file-picker');const d=new DataTransfer();for(let i=0;i<21;i++)d.items.add(new File(['x'],'f'+i));p.files=d.files;p.dispatchEvent(new Event('change'));}''')
    assert '20 项' in page.locator('#wechat-upload-notice p').text_content()
    assert len(saved)==count
    print('PASS: oversized files and excessive batches rejected before transfer',flush=True)

    mode['refuse']=True
    page.locator('#wechat-file-picker').set_input_files({'name':'not-saved.txt','mimeType':'text/plain','buffer':b'do-not-save'})
    page.wait_for_function('document.querySelector("#wechat-upload-notice p").textContent.includes("尚未确认")',timeout=30000)
    assert len(saved)==count
    mode['refuse']=False
    print('PASS: server-side save failure never reported as successful upload',flush=True)

    page.evaluate('window.testSocket.close()')
    page.wait_for_function('window.testSocket.readyState===3')
    page.locator('#wechat-file-picker').set_input_files({'name':'offline.txt','mimeType':'text/plain','buffer':b'test'})
    page.wait_for_function('document.querySelector("#wechat-upload-notice p").textContent.includes("上传失败")')
    assert len(saved)==count
    assert not errors,errors
    print('PASS: offline upload reports failure instead of success; no browser errors',flush=True)
    browser.close()
server.shutdown()
