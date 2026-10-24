"""WebKit regression using the entire shipped Selkies client and a fake transport.

Only the video decoder and remote peer are fixtures; real startup, settings,
canvas styling and touch coordinate mapping run unchanged. No real chat data.
"""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

PATCH = base64.b64decode('__PATCH_B64__').decode()
CORE = Path('/usr/share/selkies/selkies-dashboard/src/selkies-core.js').read_text()
HTML = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body,#app{width:100%;height:100%;margin:0;overflow:hidden}.video-container{position:relative;width:100vw;height:calc(var(--vh,1vh)*100)}#overlayInput{opacity:0;z-index:2}#videoCanvas{z-index:1}.hidden{display:none}</style>
<script src="/fixtures.js"></script><script src="/patch.js"></script><script type="module" src="/core.js"></script>
</head><body><div id="app"></div><div id="root"></div></body></html>'''
FIXTURE = '''
window.messages=[];window.viewportState={width:innerWidth,height:innerHeight,scale:1,offsetTop:0,offsetLeft:0};
const viewport=new EventTarget();for(const key of Object.keys(viewportState))Object.defineProperty(viewport,key,{get:()=>viewportState[key]});
Object.defineProperty(window,'visualViewport',{value:viewport,configurable:true});
window.VideoDecoder=class {
    constructor(){this.state='unconfigured'}
    static async isConfigSupported(config){return {supported:true,config}}
    configure(){this.state='configured'}close(){this.state='closed'}reset(){}decode(){}
};
window.WebSocket=class extends EventTarget {
    static CONNECTING=0;static OPEN=1;static CLOSING=2;static CLOSED=3;
    constructor(){super();this.readyState=0;this.bufferedAmount=0;
        setTimeout(()=>{this.readyState=1;this.onopen?.(new Event('open'));this.onmessage?.({data:'MODE websockets'})},20)}
    send(data){if(typeof data==='string')window.messages.push(data)}
    close(){this.readyState=3}
};
'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = {'/':HTML,'/patch.js':PATCH,'/core.js':CORE,'/fixtures.js':FIXTURE,'/manifest.json':'{"name":"Fixture"}'}.get(self.path)
        if data is None:self.send_error(404);return
        data=data.encode();self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8' if self.path=='/' else 'application/json' if self.path.endswith('.json') else 'application/javascript; charset=utf-8')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def log_message(self,*_):pass

server=ThreadingHTTPServer(('127.0.0.1',8060),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()

with sync_playwright() as pw:
    browser=pw.webkit.launch(headless=True)
    context=browser.new_context(**pw.devices['iPhone 13'])
    page=context.new_page()
    errors=[]
    page.on('pageerror',lambda e: errors.append(str(e)))
    page.goto('http://127.0.0.1:8060/',wait_until='load')
    initial_height=page.evaluate('innerHeight')
    page.wait_for_function('window.messages.some(m=>m.startsWith("SETTINGS,"))')
    page.wait_for_timeout(500)
    settings=page.evaluate('JSON.parse(window.messages.find(m=>m.startsWith("SETTINGS,")).slice(9))')
    assert settings['manual_width']==480,settings
    assert 1000>settings['manual_height']>=560,settings
    assert settings['useCssScaling'] and settings['is_manual_resolution_mode']
    assert settings['scaling_dpi']==96
    assert page.evaluate('window.devicePixelRatio')==3
    print('PASS: real upstream first SETTINGS uses logical phone resolution, not 3x pixel dimensions',flush=True)

    page.wait_for_function('window.webrtcInput !== null')
    dims=page.evaluate('''() => {const c=document.getElementById('videoCanvas'),r=c.getBoundingClientRect();return {width:c.width,height:c.height,cssWidth:r.width,cssHeight:r.height,left:r.left,top:r.top}}''')
    assert dims['width']==480 and dims['height']==settings['manual_height'],dims
    assert abs(dims['cssWidth']-390)<2 and dims['cssHeight']<=844,dims
    print('PASS: canvas buffer and visible plane fit phone; fewer pixels make the native UI larger',flush=True)
    page.evaluate('''() => {for(let i=0;i<10;i++)window.postMessage({type:'pipelineStatusUpdate',video:true,audio:true},location.origin)}''')
    page.wait_for_timeout(300)
    assert page.evaluate('window.messages.filter(m=>m.startsWith("r,")||m.startsWith("s,")).length')==0
    print('PASS: repeated pipeline status never resets first-frame decoders',flush=True)
    page.wait_for_timeout(3900)
    assert page.evaluate('window.messages.filter(m=>m==="STOP_VIDEO").length')==1
    assert page.evaluate('window.messages.filter(m=>m==="START_VIDEO").length')==1
    print('PASS: missing first frame triggers exactly one video-only recovery',flush=True)

    # Check the actual Selkies touch mapper against the transformed canvas plane.
    point=page.evaluate('''() => {const r=document.getElementById('videoCanvas').getBoundingClientRect();window.webrtcInput._calculateTouchCoordinates({clientX:r.left+r.width/2,clientY:r.top+r.height/2});return {x:window.webrtcInput.x,y:window.webrtcInput.y}}''')
    assert abs(point['x']-240)<=1 and abs(point['y']-dims['height']/2)<=1,point
    print('PASS: native touch coordinates still match the displayed desktop',flush=True)

    old_count=page.evaluate('window.messages.filter(m=>m.startsWith("r,")).length')
    page.evaluate('''() => {document.getElementById('keyboard-input-assist').focus();viewportState.height=480;visualViewport.dispatchEvent(new Event('resize'));}''')
    page.wait_for_timeout(400)
    assert page.evaluate('window.messages.filter(m=>m.startsWith("r,")).length')==old_count
    focused=page.evaluate('''() => {const r=document.getElementById('videoCanvas').getBoundingClientRect();return {width:r.width,height:r.height,bottom:r.bottom,top:r.top}}''')
    assert abs(focused['width']-dims['cssWidth'])<1 and abs(focused['height']-dims['cssHeight'])<1
    assert abs(focused['bottom']-480)<2 and focused['top']<0,focused
    point=page.evaluate('''() => {window.webrtcInput._calculateTouchCoordinates({clientX:195,clientY:470});return {x:window.webrtcInput.x,y:window.webrtcInput.y}}''')
    expected_y=(470-focused['top'])/focused['height']*dims['height']
    assert abs(point['x']-240)<=1 and abs(point['y']-expected_y)<=1,point
    print('PASS: keyboard opening preserves font size and server resolution, keeps bottom input area visible',flush=True)

    page.set_viewport_size({'width':390,'height':480})
    page.wait_for_timeout(400)
    assert page.evaluate('window.messages.filter(m=>m.startsWith("r,")).length')==old_count
    page.set_viewport_size({'width':390,'height':initial_height})
    page.wait_for_timeout(400)
    print('PASS: keyboard can resize both layout and visual viewport without resizing the desktop',flush=True)

    page.evaluate('''height => {document.getElementById('keyboard-input-assist').blur();viewportState.height=height;visualViewport.dispatchEvent(new Event('resize'));}''',initial_height)
    page.wait_for_timeout(400)
    assert abs(page.locator('#videoCanvas').bounding_box()['y']-dims['top'])<2
    assert page.evaluate('window.messages.filter(m=>m.startsWith("r,")).length')==old_count
    print('PASS: keyboard dismissal restores the full desktop without reconnecting',flush=True)

    page.set_viewport_size({'width':844,'height':390})
    page.evaluate('''() => {viewportState.width=844;viewportState.height=390;visualViewport.dispatchEvent(new Event('resize'));}''')
    page.wait_for_timeout(500)
    last=page.evaluate('window.messages.filter(m=>m.startsWith("r,")).at(-1)')
    assert last.startswith('r,1212x560,'),last
    landscape=page.locator('#videoCanvas').bounding_box()
    assert landscape['width']<=844 and landscape['height']<=390,landscape
    assert page.evaluate('window.messages.filter(m=>m.startsWith("r,")).length')<=old_count+2
    print('PASS: orientation change recalculates one bounded logical resolution and fits landscape',flush=True)
    assert not errors,errors
    context.close()

    desktop=browser.new_context(viewport={'width':1366,'height':768},device_scale_factor=2,is_mobile=False,has_touch=False)
    page=desktop.new_page();page.goto('http://127.0.0.1:8060/',wait_until='load')
    page.wait_for_function('window.messages.some(m=>m.startsWith("SETTINGS,"))')
    settings=page.evaluate('JSON.parse(window.messages.find(m=>m.startsWith("SETTINGS,")).slice(9))')
    assert not settings['is_manual_resolution_mode'] and not settings['useCssScaling'],settings
    assert settings['initialClientWidth']==2732 and settings['initialClientHeight']==1536,settings
    assert not page.evaluate('document.documentElement.classList.contains("wechat-phone-view")')
    print('PASS: desktop retains the original high-resolution settings',flush=True)
    desktop.close()

    shared=browser.new_context(**pw.devices['iPhone 13'])
    page=shared.new_page();page.goto('http://127.0.0.1:8060/#shared',wait_until='load')
    assert not page.evaluate('document.documentElement.classList.contains("wechat-phone-view")')
    print('PASS: secondary/shared view cannot auto-resize the primary desktop',flush=True)
    shared.close();browser.close()
server.shutdown()
