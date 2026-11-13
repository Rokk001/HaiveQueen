const assert = require('node:assert/strict');
const {capabilityRows} = require('../haivequeen/web-client.js');
const rows = capabilityRows({capabilities:['Coding'], capabilityThresholds:{Queen:42}}, ['Coding','Queen','Build']);
assert.deepEqual(rows, [{name:'Coding', enabled:true, threshold:100}, {name:'Queen', enabled:false, threshold:42}, {name:'Build', enabled:false, threshold:100}]);
assert.equal(capabilityRows({capabilities:['Queen'],capabilityThresholds:{Queen:42}}, ['Queen'])[0].threshold, 42);
const {Client, planActions, setValue, safeLinks, toolsFromArgs, withTools, orchestratorDisabled} = require('../haivequeen/web-client.js');

(async () => {
  const calls = [], notices = [];
  let allow = false;
  const client = new Client({csrf:'token', confirm:()=>allow, notice:x=>notices.push(x),
    fetch:async (url, options)=>{calls.push([url, options]);return {ok:true,json:async()=>({ok:true})}}});
  await client.action('force-stop', {}, 'Everything stops. Unfinished work is done again.');
  assert.equal(calls.length, 0, 'declining must send nothing');
  allow = true;
  await client.action('force-stop', {}, 'Everything stops. Unfinished work is done again.');
  assert.equal(JSON.parse(calls[0][1].body).confirmed, true);
  assert.equal(calls[0][1].headers['X-CSRF-Token'], 'token');
  assert(notices.includes('Working…'));
  let completeAction;
  client.fetch=()=>new Promise(resolve=>{completeAction=()=>resolve({ok:true,json:async()=>({ok:true})})});
  const pending=client.action('start');
  assert.equal(await client.action('start'),null,'A redraw must not allow the same action twice');
  completeAction();await pending;
  assert(planActions({status:'PAUSED',stopped:true}).includes('resume'));
  assert(!planActions({status:'PAUSED',stopped:false}).includes('resume'), 'running work must finish before Resume');
  assert(!planActions({status:'DONE'}).includes('resume'));
  assert(!planActions({status:'RUNNING'}).includes('resume'));
  assert.equal(orchestratorDisabled('force-stop','STOPPED'),false,'Force stop remains available for orphaned work');
  assert.equal(orchestratorDisabled('start','RUNNING'),true);
  assert.equal(orchestratorDisabled('stop-gracefully','STOPPING'),true);
  const data={classes:{medium:{model:'old'}}, slots:2};
  setValue(data,['classes','medium','model'],'new');
  assert.equal(data.classes.medium.model,'new');
  assert.deepEqual(safeLinks(['javascript:alert(1)','https://example.invalid/device','http://example.invalid']),
    ['https://example.invalid/device','http://example.invalid']);
  assert.deepEqual(toolsFromArgs(['--allowedTools','Bash(cmd(a,b)),Read']),['Bash(cmd(a,b))','Read']);
  assert.deepEqual(withTools(['run','--allowedTools','Read','--json'],['Read','Write']),['run','--allowedTools','Read,Write','--json']);
  let finish;
  client.fetch = () => new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({hives:[]})})});
  const first = client.refresh();
  const second = client.refresh();
  assert.equal(client.refreshWanted, true);
  finish();
  await first;
  await second;
  // A queued refresh starts after the pending read rather than blocking actions.
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(client.loading, true);
  finish();
  await new Promise(resolve=>setImmediate(resolve));
  client.fetch=async()=>({ok:false,status:400,json:async()=>({error:'Settings changed since loading; reload before saving'})});
  await assert.rejects(client.request('settings/save',{revision:'old',data:{}}),/changed since loading/);
  console.log('Browser client regressions passed');
})().catch(error=>{console.error(error);process.exitCode=1});
const {RenderGate} = require('../haivequeen/web-client.js');
let renders = 0;
const gate = new RenderGate(() => renders++);
const firstButton = {textContent:'Start', disabled:false, setAttribute(k,v){this[k]=v;}, removeAttribute(k){delete this[k];}};
gate.begin(firstButton);
const redrawnButton = {textContent:'Start', disabled:false, setAttribute(k,v){this[k]=v;}, removeAttribute(k){delete this[k];}};
gate.prepare(redrawnButton);
assert.equal(redrawnButton.disabled, true, 'pending feedback survives a redraw');
assert.equal(redrawnButton['aria-busy'], 'true');
gate.render();
gate.render();
assert.equal(renders, 2, 'live state must render while an action is pending');
gate.end(firstButton);
assert.equal(redrawnButton.disabled, false);

// Exercise the mounted provider editor with DOM-like controls and the real handlers.
async function providerThresholdUI() {
  class Element {
    constructor(tag) { this.tagName=tag.toUpperCase(); this.children=[]; this.dataset={}; this.className=''; this.attributes={}; this._text=''; this._value=''; this.classList={contains:name=>this.className.split(' ').includes(name)}; }
    set textContent(value) { this._text=String(value); this.children=[]; }
    get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
    set value(value) { this._value=String(value); } get value() { return this._value; }
    append(...nodes) { for(const node of nodes) { node.parentNode=this; this.children.push(node); } }
    replaceChildren(...nodes) { this.children=[]; this._text=''; this.append(...nodes); }
    remove() { if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this); }
    setAttribute(key,value) { this.attributes[key]=value; } removeAttribute(key) { delete this.attributes[key]; }
    addEventListener() {}
    get lastChild() { return this.children.at(-1); }
    querySelectorAll(selector) {
      const all=[]; const walk=node=>{for(const child of node.children){all.push(child);walk(child);}}; walk(this);
      if(selector==='[data-saved-only]')return all.filter(node=>'savedOnly' in node.dataset);
      if(selector==='.capability-threshold input[type="number"]')return all.filter(node=>node.tagName==='INPUT' && node.type==='number' && node.parentNode?.className==='capability-threshold');
      return [];
    }
    reportValidity() { const n=Number(this.value); return this.value.trim()!=='' && Number.isInteger(n) && n>=Number(this.min) && n<=Number(this.max); }
  }
  const ids={}; for(const id of ['app','tabs','notice','detail','detail-body','close-detail','version','controls','live','dirty-marker'])ids[id]=new Element('div');
  const document={getElementById:id=>ids[id] || null,createElement:tag=>new Element(tag)};
  const saved=[]; const provider={name:'sample',protocol:'test',args:[],capabilities:['Coding'],capabilitiesVersion:1,capabilityThresholds:{Coding:72,Queen:38}};
  const settings={revision:'r1',data:{providers:[provider]}};
  global.window={addEventListener(){}}; global.setInterval=()=>0; global.confirm=()=>true;
  global.fetch=async (url,options={})=>{let value={}; if(url.includes('settings/view'))value=settings; else if(url.includes('settings/save')){saved.push(JSON.parse(options.body));value={revision:'r2',data:saved.at(-1).data};} else if(url.endsWith('/providers'))value=[]; return {ok:true,json:async()=>value};};
  require('../haivequeen/web-client.js').mount(document,{translations:{},labels:{},choices:{},help:{},general:{providers:[]},presets:{},capabilities:['Coding','Queen','Build']});
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  ids.tabs.children.find(node=>node.dataset.tab==='Providers').onclick(); await flush(); await flush();
  const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]);
  const row=name=>descendants(ids.app).find(node=>node.className==='capability-threshold' && node.children[1]?.textContent===name);
  const number=name=>row(name).children.find(node=>node.tagName==='INPUT' && node.type==='number');
  const toggle=(name,checked)=>{const check=row(name).children[0];check.checked=checked;check.onchange();};
  const save=async()=>{descendants(ids.app).find(node=>node.tagName==='BUTTON' && node.textContent==='Save').onclick();await flush();};
  assert.equal(number('Coding').value,'72');
  assert.equal(number('Queen'),undefined,'disabled capability has no threshold field');
  toggle('Queen',true); assert.equal(number('Queen').value,'38','retained value returns');
  toggle('Queen',false); assert.equal(number('Queen'),undefined);
  toggle('Queen',true); assert.equal(number('Queen').value,'38');
  toggle('Build',true); assert.equal(number('Build').value,'100','new threshold defaults to 100');
  for(const invalid of ['','0','101','1.5']){const input=number('Coding');input.value=invalid;input.oninput();await save();assert.equal(saved.length,0,`invalid ${invalid} must not save`);}
  const input=number('Coding');input.value='85';input.oninput();toggle('Queen',false);await save();
  assert.equal(saved.length,1);assert.deepEqual(saved[0].data.providers[0].capabilityThresholds,{Coding:85,Queen:38});
}
providerThresholdUI().catch(error=>{console.error(error);process.exitCode=1});
