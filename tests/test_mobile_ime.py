"""WebKit event-sequence regression using the shipped Selkies input class."""
import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

PATCH = base64.b64decode('__PATCH_B64__').decode()
core = Path('/usr/share/selkies/selkies-dashboard/src/selkies-core.js').read_text()
position = core.index('_handleMobileInput(')
class_name = list(re.finditer(r'class\s+([\w$]+)',core[:position]))[-1].group(1)
entry = re.search(r'([\w$]+)\(window.streamingMode\);\s*$',core)
assert entry, 'Unexpected upstream bootstrap'
core = core[:entry.start()] + 'window.__TestInputClass='+class_name+';'
page_html = '''<!doctype html><meta charset="utf-8"><div id="overlayInput" tabindex="0"></div>
<input id="keyboard-input-assist" autocomplete="off"><script src="/patch.js"></script><script type="module" src="/core.js"></script>'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = {'/':page_html,'/patch.js':PATCH,'/core.js':core}.get(self.path)
        if data is None:self.send_error(404);return
        payload=data.encode()
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8' if self.path=='/' else 'application/javascript; charset=utf-8')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers();self.wfile.write(payload)
    def log_message(self,*args):pass

server=ThreadingHTTPServer(('127.0.0.1',8060),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
with sync_playwright() as pw:
    browser=pw.webkit.launch(headless=True)
    context=browser.new_context(**pw.devices['iPhone 13'])
    page=context.new_page()
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://127.0.0.1:8060/',wait_until='load')
    assert page.evaluate('typeof window.__TestInputClass')=='function',errors
    result=page.evaluate('''async () => {
        const input=document.getElementById('keyboard-input-assist');
        const overlay=document.getElementById('overlayInput');
        let sent=[];
        const remote=new window.__TestInputClass(overlay,message=>sent.push(message));
        window.addEventListener('keydown',e=>remote._handleKeyDown(e),true);
        window.addEventListener('keyup',e=>remote._handleKeyUp(e),true);
        input.addEventListener('input',e=>remote._handleMobileInput(e));
        input.addEventListener('input',()=>{if(input.value){remote._typeString(input.value);input.value=''}});
        input.addEventListener('keydown',e=>{
            const key=e.key==='Enter'?65293:e.key==='Backspace'?65288:null;
            if(key!==null){remote._guac_press(key);remote._guac_release(key);e.preventDefault()}
        });
        const tick=()=>new Promise(resolve=>setTimeout(resolve,20));
        const composition=(type,data)=>input.dispatchEvent(new CompositionEvent(type,{bubbles:true,data}));
        const text=(value,data,type='insertCompositionText',composing=true)=>{
            input.value=value;
            input.dispatchEvent(new InputEvent('input',{bubbles:true,data,inputType:type,isComposing:composing}));
        };
        const key=(value,code,composing=false)=>{
            for(const type of ['keydown','keyup'])input.dispatchEvent(new KeyboardEvent(type,{bubbles:true,cancelable:true,key:value,code:value,keyCode:code,isComposing:composing}));
        };
        const down=()=>sent.filter(message=>message.startsWith('kd,')).map(message=>Number(message.split(',')[1]));
        const expected=word=>Array.from(word).map(char=>char.codePointAt(0)>255?0x01000000|char.codePointAt(0):char.codePointAt(0));
        const check=(name,word)=>{
            const actual=down(),want=expected(word);
            if(JSON.stringify(actual)!==JSON.stringify(want))throw new Error(name+': '+JSON.stringify({actual,want,sent}));
            if(input.value!=='')throw new Error(name+': helper not cleared after commit');
        };
        const cases=[];
        input.focus();
        composition('compositionstart','');
        for(const value of ['n','ni','nih','niha','nihao']){
            composition('compositionupdate',value);
            text(value,value);
            key('Unidentified',229,true);
        }
        if(sent.length)throw new Error('Nine-key pre-edit leaked to remote');
        text('你好','你好');
        composition('compositionend','你好');
        await tick();check('final input before compositionend','你好');cases.push('nine-key pinyin deferred until final Chinese selection');

        sent=[];composition('compositionstart','');text('ceshi','ceshi');
        composition('compositionend','测试');
        text('测试','测试','insertFromComposition',false);
        await tick();check('final input after compositionend','测试');cases.push('final input after compositionend sent once');

        sent=[];composition('compositionstart','');text('zhongwen','zhongwen','insertCompositionText',false);
        composition('compositionend','中文');
        await tick();check('no trailing final input','中文');cases.push('missing trailing input and false isComposing flag handled');

        sent=[];composition('compositionstart','');text('ni','ni');key('Backspace',8,false);key('Enter',13,false);
        if(sent.length)throw new Error('Composition Enter/Backspace leaked to remote');
        composition('compositionend','你');
        await tick();check('candidate confirmation does not send message','你');cases.push('IME Enter and Backspace stay local');

        sent=[];composition('compositionstart','');text('quxiao','quxiao');composition('compositionend','');
        await tick();check('cancelled composition','');cases.push('cancelled composition sends nothing');

        sent=[];
        for(const word of ['好','好']){composition('compositionstart','');text('hao','hao');composition('compositionend',word);await tick()}
        check('repeated identical commits','好好');cases.push('consecutive identical words preserved');

        sent=[];
        for(const word of ['你','好']){composition('compositionstart','');text('pending','pending');composition('compositionend',word)}
        await tick();check('immediate consecutive compositions','你好');cases.push('rapid consecutive selections preserve both words');

        sent=[];text('abc','abc','insertText',false);await tick();check('normal English input','abc');cases.push('English remains single-delivery');

        sent=[];key('Enter',13);await tick();
        if(JSON.stringify(down())!==JSON.stringify([65293]))throw new Error('Non-IME Enter duplicated: '+JSON.stringify(sent));
        sent=[];key('Backspace',8);await tick();
        if(JSON.stringify(down())!==JSON.stringify([65288]))throw new Error('Non-IME Backspace duplicated: '+JSON.stringify(sent));
        cases.push('non-IME Enter and Backspace delivered once');

        let outside=0;
        overlay.addEventListener('compositionupdate',()=>outside++);
        overlay.dispatchEvent(new CompositionEvent('compositionupdate',{bubbles:true,data:'desktop'}));
        if(outside!==1)throw new Error('Desktop composition intercepted');
        cases.push('desktop composition events unaffected');
        return cases;
    }''')
    for case in result:print('PASS:',case,flush=True)
    assert not errors,errors
    browser.close()
server.shutdown()
