/* Run against an isolated preview server and a browser started for this test.
   node tests-py/web_mobile_cdp_test.cjs <debug-port> <preview-port> */
const assert=require('node:assert/strict');
(async()=>{
 const [debug,preview]=process.argv.slice(2);
 const tabs=await(await fetch(`http://127.0.0.1:${debug}/json`)).json();
 const page=tabs.find(tab=>tab.type==='page');assert(page,'No isolated browser page');
 const ws=new WebSocket(page.webSocketDebuggerUrl);await new Promise(r=>ws.onopen=r);
 let id=0;const pending=new Map();ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}};
 const send=(method,params={})=>new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});ws.send(JSON.stringify({id:key,method,params}));});
 const evaluate=async expression=>{const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(result.exceptionDetails.text);return result.result.value;};
 const wait=async expression=>{for(let n=0;n<100;n++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,50));}throw Error('Timed out: '+expression);};
 try{
  for(const width of [320,390,400,768,1280]){
   await send('Emulation.setDeviceMetricsOverride',{width,height:843,deviceScaleFactor:1,mobile:width<701});
   await send('Page.navigate',{url:`http://127.0.0.1:${preview}`});await wait(`!!document.querySelector('.model-limit')`);
   const sizes=await evaluate(`JSON.stringify({width:innerWidth,scroll:document.documentElement.scrollWidth,plans:document.querySelector('[data-panel="Hives"]').getBoundingClientRect().top,limits:document.querySelector('[data-panel="Limits"]').getBoundingClientRect().top,wrap:getComputedStyle(document.querySelector('.hives-list')).maxHeight,card:document.querySelector('.hive-tree-plan').getBoundingClientRect().height})`);
   const metrics=JSON.parse(sizes);assert(metrics.scroll<=width,JSON.stringify(metrics));
   if(width<=700){assert(metrics.plans<metrics.limits);assert.equal(metrics.wrap,'none');assert(metrics.card<320,'Plan card is unnecessarily tall');
    assert(await evaluate(`(()=>{const strip=document.querySelector('.tiles');return strip.scrollHeight<=strip.clientHeight+1})()`),'Metric strip has a vertical scrollbar');}
   await evaluate(`document.querySelector('#settings-toggle').click()`);await wait(`!document.querySelector('#settings-subnav').hidden`);
   assert(await evaluate(`(()=>{const r=document.querySelector('#settings-subnav').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth})()`),'Settings menu outside viewport');
   await evaluate(`document.querySelector('[data-tab="Providers"]').click()`);await wait(`!!document.querySelector('.git-connection')`);
   await evaluate(`document.querySelector('.git-connections').open=true;document.querySelector('.git-connection').open=true`);
   assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth`),'Provider overflow at '+width);
   assert(await evaluate(`!!document.querySelector('.git-connection .access-status')`));
   await evaluate(`document.querySelector('[data-tab="Projects"]').click()`);await wait(`!!document.querySelector('.workflow-step')`);
   assert.equal(await evaluate(`document.querySelectorAll('.workflow-step').length`),5);
   assert.equal(await evaluate(`document.querySelectorAll('.workflow-switch input').length`),4);
   assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth`),'Workflow overflow');
   await evaluate(`(()=>{const way=document.querySelector('[aria-label="Way of working"]');way.value='git';way.dispatchEvent(new Event('change'))})()`);
   assert(await evaluate(`document.querySelectorAll('.workflow-field input').length===0`),'Git fields must be disabled');
   await evaluate(`document.querySelector('[aria-label="Check location"] [data-mode="local"]').click()`);
   assert.equal(await evaluate(`document.querySelector('[aria-label="Way of working"]').value`),'custom');
   assert(await evaluate(`document.querySelector('.workflow-step[aria-label="Check"] .workflow-field input').required`),'Enabled local command must be required');
   await evaluate(`document.querySelector('[aria-label="Check on"]').click()`);
   assert(await evaluate(`!document.querySelector('.workflow-step[aria-label="Check"] .workflow-field input')`),'Off command must be disabled');
   // Discard test-only edits through the application confirmation.
   await evaluate(`document.querySelector('[data-tab="Releases"]').click()`);
   await wait(`document.querySelector('#confirmation-dialog').open`);
   await evaluate(`document.querySelector('#confirmation-dialog .confirm-primary').click()`);
   await evaluate(`document.querySelector('[data-tab="Releases"]').click()`);await wait(`!!document.querySelector('.release-card')`);
   assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth`),'Release overflow');
   await evaluate(`document.querySelector('[data-tab="Dashboard"]').click()`);await wait(`!!document.querySelector('.row-menu')`);
   await evaluate(`document.querySelector('.row-menu').click()`);await wait(`document.querySelector('#detail').open`);
   assert(await evaluate(`(()=>{const r=document.querySelector('#detail').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.height<=innerHeight})()`),'Detail outside viewport');
   console.log('Mobile/tablet/desktop layout passed at '+width+'px');
  }
 }finally{ws.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
