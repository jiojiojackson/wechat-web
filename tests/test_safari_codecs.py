"""Actual JPEG decode/draw with shipped Selkies and missing Safari APIs.

Only the WebSocket peer is simulated. JPEG bytes come from a generated solid
color fixture. The real ImageBitmap/HTMLImageElement paths draw actual pixels.
"""
import base64
import io
import json
import struct
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image
from playwright.sync_api import sync_playwright

assets=json.loads(base64.b64decode('__ASSETS_B64__'))
core=Path('/usr/share/selkies/selkies-dashboard/src/selkies-core.js').read_text()
out=io.BytesIO();Image.new('RGB',(480,854),(35,95,175)).save(out,format='JPEG',quality=90)
packet=base64.b64encode(struct.pack('!BBHH',3,0,1,0)+out.getvalue()).decode()
fixture='''
const mode=new URLSearchParams(location.search).get('case');
window.originalImageDecoder=window.ImageDecoder;
if(mode!=='native')Object.defineProperty(window,'ImageDecoder',{value:undefined,configurable:true,writable:true});
if(mode==='image-element')Object.defineProperty(window,'createImageBitmap',{value:undefined,configurable:true,writable:true});
const prefix=location.href.split('#')[0].replace(/[^a-zA-Z0-9.-_]/g,'_')+'_';localStorage.setItem(prefix+'encoder','jpeg');
const encoded=Uint8Array.from(atob('''+json.dumps(packet)+'''),x=>x.charCodeAt(0));
window.packetCount=0;
window.WebSocket=class extends EventTarget {
 static CONNECTING=0;static OPEN=1;static CLOSING=2;static CLOSED=3;
 constructor(){super();this.readyState=0;this.bufferedAmount=0;
  setTimeout(()=>{this.readyState=1;this.onopen?.(new Event('open'));this.onmessage?.({data:'MODE websockets'});
   this.timer=setInterval(()=>{window.packetCount++;this.onmessage?.({data:encoded.buffer.slice(0)})},100)},20)}
 send(){}close(){clearInterval(this.timer);this.readyState=3}
};
'''
html='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body,#app{width:100%;height:100%;margin:0;overflow:hidden}.video-container{position:relative;width:100vw;height:calc(var(--vh,1vh)*100)}#overlayInput{opacity:0;z-index:2}#videoCanvas{z-index:1}.hidden{display:none}</style>
<script src="/fixture.js"></script><script src="/compat.js"></script><script src="/viewport.js"></script><script type="module" src="/core.js"></script>
</head><body><div id="app"></div><div id="root"></div></body></html>'''

class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  path=urlsplit(self.path).path
  text={'/':html,'/fixture.js':fixture,'/compat.js':assets['safari-codecs.js'],'/viewport.js':assets['mobile-viewport.js'],'/core.js':core,'/manifest.json':'{"name":"JPEG fixture"}'}.get(path)
  if text is None:self.send_error(404);return
  body=text.encode();self.send_response(200)
  self.send_header('Content-Type','text/html; charset=utf-8' if path=='/' else 'application/json' if path.endswith('.json') else 'application/javascript; charset=utf-8')
  self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*_):pass

server=ThreadingHTTPServer(('127.0.0.1',8060),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
painted='''() => {const c=document.getElementById('videoCanvas');if(!c||!c.width)return false;const p=c.getContext('2d').getImageData(100,100,1,1).data;return Math.abs(p[0]-35)<8&&Math.abs(p[1]-95)<8&&Math.abs(p[2]-175)<8}'''
with sync_playwright() as pw:
 browser=pw.webkit.launch(headless=True)
 for mode in ['without-fix','bitmap','image-element','native']:
  device=dict(pw.devices['iPhone 13']);device['viewport']={'width':402,'height':714}
  context=browser.new_context(**device);page=context.new_page();errors=[];logs=[]
  page.on('pageerror',lambda e:errors.append(str(e)));page.on('console',lambda m:logs.append(m.text))
  if mode=='without-fix':page.route('**/compat.js',lambda route:route.fulfill(status=200,content_type='application/javascript',body=''))
  page.goto('http://127.0.0.1:8060/?case='+mode,wait_until='load')
  page.wait_for_function('window.packetCount>=4')
  if mode=='without-fix':
   assert not page.evaluate(painted)
   assert any('ImageDecoder API not supported' in line for line in logs),logs[-10:]
   print('PASS: reproduced black canvas despite incoming JPEG frames when ImageDecoder is absent',flush=True)
  else:
   page.wait_for_function(painted,timeout=8000)
   assert page.locator('#status-display').evaluate("e=>e.classList.contains('hidden')")
   assert page.evaluate('document.getElementById("videoCanvas").width')==480
   if mode=='native':assert page.evaluate('window.ImageDecoder===window.originalImageDecoder')
   print('PASS: actual JPEG decoding and Selkies canvas draw via '+mode,flush=True)
  assert not errors,errors
  context.close()
 browser.close()
server.shutdown()
