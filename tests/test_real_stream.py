"""Run ONLY in the disposable color-pattern desktop, never the live WeChat container.

Requires the isolated-fixture marker, real X display, encoder, Nginx and WebKit.
No video decoder mock: check actual colored pixels and continued playback.
"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

assert Path('/run/wechat-isolated-stream-fixture').is_file(), 'Refusing to connect to a non-fixture desktop'
PIXELS = '''() => {const c=document.getElementById('videoCanvas');if(!c||!c.width||!c.height)return false;const d=c.getContext('2d').getImageData(Math.min(100,c.width-1),Math.min(100,c.height-1),1,1).data;return d[0]+d[1]+d[2]>50}'''

with sync_playwright() as p:
    browser=p.webkit.launch(headless=True)
    for encoder,drop_first in [('jpeg',False),('x264enc',False),('jpeg',True)]:
        device=dict(p.devices['iPhone 13']);device['viewport']={'width':402,'height':714}
        context=browser.new_context(**device)
        page=context.new_page();logs=[];errors=[];frames={'count':0,'bytes':0};sent=[]
        page.on('console',lambda m:logs.append(m.text))
        page.on('pageerror',lambda e:errors.append(str(e)))
        def received(payload):
            if isinstance(payload,bytes):frames['count']+=1;frames['bytes']+=len(payload)
        def websocket(ws):
            ws.on('framereceived',received)
            ws.on('framesent',lambda data:sent.append(data) if isinstance(data,str) and (data.startswith(('SETTINGS,','r,','s,','START','STOP'))) else None)
        page.on('websocket',websocket)
        context.add_init_script("const pre=location.href.split('#')[0].replace(/[^a-zA-Z0-9.-_]/g,'_')+'_';localStorage.setItem(pre+'encoder',"+json.dumps(encoder)+");")
        if drop_first:
            context.add_init_script('''const OriginalWebSocket=window.WebSocket;let acceptVideo=false;
                window.WebSocket=class extends OriginalWebSocket {
                    constructor(...args){super(...args);this.addEventListener('message',event=>{
                        if(!acceptVideo && typeof event.data!=='string')event.stopImmediatePropagation();
                    })}
                    send(data){if(data==='START_VIDEO')acceptVideo=true;super.send(data)}
                };''')
        page.goto('http://127.0.0.1:3000/',wait_until='domcontentloaded')
        try:
            page.wait_for_function(PIXELS,timeout=18000)
            page.wait_for_timeout(2500)
            painted=page.evaluate(PIXELS)
        except Exception:
            painted=False
        print(json.dumps({'encoder':encoder,'drop_first':drop_first,'painted':painted,'frames':frames,'errors':errors,
            'canvas':page.evaluate('(()=>{const c=document.getElementById("videoCanvas");return c?{w:c.width,h:c.height,css:c.getBoundingClientRect().toJSON()}:null})()'),
            'sent':sent[-8:] if not painted else [],'console':logs[-12:] if not painted else []},ensure_ascii=False),flush=True)
        assert painted,encoder+' failed to render real video'
        assert page.evaluate('document.getElementById("videoCanvas").width')==480
        assert not any(m.startswith('r,') for m in sent),'Redundant initial resize reset the decoder'
        if drop_first:
            assert sent.count('STOP_VIDEO')==1 and sent.count('START_VIDEO')==1,sent
            print('PASS: dropped initial frames recover once on the real static desktop',flush=True)
        for viewport in [{'width':844,'height':390},{'width':402,'height':714}]:
            page.set_viewport_size(viewport)
            page.wait_for_timeout(1200)
            page.wait_for_function(PIXELS,timeout=15000)
            print('PASS: '+encoder+' real video after orientation '+str(viewport),flush=True)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function(PIXELS,timeout=15000)
        page.wait_for_timeout(1500)
        assert page.evaluate(PIXELS)
        assert not any('VideoDecoder is closed' in message for message in logs)
        print('PASS: '+encoder+' real video survives reload on a static desktop',flush=True)
        context.close()
    browser.close()
