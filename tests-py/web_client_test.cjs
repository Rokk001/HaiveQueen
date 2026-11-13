const assert = require('node:assert/strict');
const {capabilityRows} = require('../haivequeen/web-client.js');
const rows = capabilityRows({capabilitiesVersion:1,capabilities:['Coding'], capabilityThresholds:{Queen:42}}, ['Coding','Queen','Build']);
assert.deepEqual(rows, [{name:'Coding', enabled:true, threshold:100}, {name:'Queen', enabled:false, threshold:42}, {name:'Build', enabled:false, threshold:100}]);
assert.equal(capabilityRows({capabilities:['Queen'],capabilityThresholds:{Queen:42}}, ['Queen'])[0].threshold, 42);
assert.deepEqual(capabilityRows({use:'capability-only',capabilities:['Build']},['Coding','Queen','Build']).map(row=>row.enabled),[false,true,true]);
assert.deepEqual(capabilityRows({access:'llm',capabilities:['Build']},['Coding','Queen','Build']).map(row=>row.enabled),[true,false,true]);
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
const {switchProviderAccess,providerCli} = require('../haivequeen/web-client.js');
{
  const presets={claude:{protocol:'claude',command:'claude',args:[],label:'Claude Code'},codex:{protocol:'codex',command:'codex',args:[],label:'Codex CLI'}};
  const provider={name:'existing',protocol:'codex',access:'subscription',classes:{medium:{model:'cloud-model',effort:'high',contextLimitTokens:1000}},capabilityThresholds:{Coding:90}};
  const stash={};
  switchProviderAccess(provider,'llm',stash,presets,['claude','codex']);
  assert.equal(provider.classes.medium.model,undefined);
  provider.address='http://localhost:1234';provider.apiKey='local-key';provider.classes.medium.model='local-model';
  switchProviderAccess(provider,'subscription',stash,presets,['claude','codex','custom']);
  assert.equal(provider.address,undefined);assert.equal(provider.apiKey,undefined);assert.equal(provider.classes.medium.model,'cloud-model');
  assert.equal(provider.classes.medium.effort,'high');assert.equal(provider.limit.source,'codex');
  switchProviderAccess(provider,'llm',stash,presets,['claude','codex']);
  assert.equal(provider.address,'http://localhost:1234');assert.equal(provider.apiKey,'local-key');assert.equal(provider.classes.medium.model,'local-model');assert.equal(provider.limit.source,'none');
  assert.deepEqual(provider.capabilityThresholds,{Coding:90});assert.equal(provider.name,'existing');
  provider.outputFormat='codex';switchProviderAccess(provider,'api',stash,presets,['claude','codex']);
  assert.equal(providerCli(provider,presets),'claude');assert.equal(provider.classes.medium.model,undefined);
}
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
async function providerThresholdUI(legacy=false) {
  class Element {
    constructor(tag) { this.tagName=tag.toUpperCase(); this.children=[]; this.dataset={}; this.className=''; this.style={};this.attributes={}; this._text=''; this._value=''; this.classList={contains:name=>this.className.split(' ').includes(name),add:name=>{this.className+=' '+name;},toggle(){}}; }
    set textContent(value) { this._text=String(value); this.children=[]; }
    get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
    set value(value) { this._value=String(value); } get value() { return this._value; }
    append(...nodes) { for(const node of nodes) { node.parentNode=this; this.children.push(node); } }
    replaceChildren(...nodes) { this.children=[]; this._text=''; this.append(...nodes); }
    remove() { if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this); }
    setAttribute(key,value) { this.attributes[key]=value; } removeAttribute(key) { delete this.attributes[key]; }
    addEventListener() {}
    getBoundingClientRect(){return {height:0,top:0};}
    contains(node){return node===this||this.children.some(child=>child.contains(node));}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    prepend(...nodes){for(const node of nodes)node.parentNode=this;this.children.unshift(...nodes);}
    get lastChild() { return this.children.at(-1); }
    querySelectorAll(selector) {
      const all=[]; const walk=node=>{for(const child of node.children){all.push(child);walk(child);}}; walk(this);
      if(selector==='h2'||selector==='details')return all.filter(node=>node.tagName===selector.toUpperCase());
      if(selector==='[data-tab]')return all.filter(node=>node.dataset.tab);
      if(selector==='[data-tab="Questions"]')return all.filter(node=>node.dataset.tab==='Questions');
      if(selector.startsWith('[data-panel=')){const name=selector.match(/data-panel=["']([^"']+)/)?.[1];return all.filter(node=>node.dataset.panel===name);}
      if(selector==='[data-saved-only]')return all.filter(node=>'savedOnly' in node.dataset);
      if(selector==='[data-path]')return all.filter(node=>'path' in node.dataset);
      if(selector==='.capability-threshold input[type="number"]')return all.filter(node=>node.tagName==='INPUT' && node.type==='number' && node.parentNode?.className==='capability-threshold');
      return [];
    }
    reportValidity() { const n=Number(this.value); return this.value.trim()!=='' && Number.isInteger(n) && n>=Number(this.min) && n<=Number(this.max); }
  }
  const descendantsFor=node=>node.children.flatMap(child=>[child,...descendantsFor(child)]);
  const ids={}; for(const id of ['app','tabs','notice','detail','detail-body','close-detail','version','controls','live','dirty-marker','update-status'])ids[id]=new Element('div');
  const document={body:new Element('body'),getElementById:id=>ids[id] || descendantsFor(document.body).find(node=>node.id===id) || descendantsFor(ids.tabs).find(node=>node.id===id) || null,createElement:tag=>new Element(tag),addEventListener(){},querySelector:()=>new Element('header')};
  const saved=[]; const provider={name:'sample',protocol:'test',args:[],capabilities:['Coding'],capabilitiesVersion:1,capabilityThresholds:{Coding:72,Queen:38}};
  if(legacy){provider.capabilities=['Build'];delete provider.capabilitiesVersion;provider.capabilityThresholds={Queen:38};}
  const settings={revision:'r1',data:{providers:[provider]}};
  global.window={addEventListener(){},scrollTo(){},scrollX:0,scrollY:0,innerHeight:900}; global.ResizeObserver=class {observe(){}}; global.setInterval=()=>0; global.confirm=()=>true;
  global.fetch=async (url,options={})=>{let value={}; if(url.endsWith('/state'))return new Promise(()=>{}); if(url.includes('settings/view'))value=settings; else if(url.includes('settings/save')){saved.push(JSON.parse(options.body));value={revision:'r2',data:saved.at(-1).data};} else if(url.endsWith('/providers'))value=[]; return {ok:true,json:async()=>value};};
  require('../haivequeen/web-client.js').mount(document,{translations:{},labels:{},choices:{},help:{},general:{providers:[]},presets:{claude:{label:'Claude Code',protocol:'claude',command:'claude',args:[],beeMode:'subagent'}},accessClis:{subscription:['claude','custom'],api:['claude'],llm:['claude']},capabilities:['Coding','Queen','Build']});
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  descendantsFor(ids.tabs).find(node=>node.dataset.tab==='Providers').onclick(); await flush(); await flush();
  const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]);
  const row=name=>descendants(ids.app).find(node=>node.className==='capability-threshold' && node.children[1]?.textContent===name);
  const number=name=>row(name).children.find(node=>node.tagName==='INPUT' && node.type==='number');
  const nodes=descendants(ids.app);
  const advanced=nodes.find(node=>node.tagName==='DETAILS' && node.children[0]?.textContent==='Advanced');
  assert(advanced && !advanced.open,'provider advanced fields must start collapsed');
  assert(nodes.some(node=>node.className==='provider-models'),'models need three dedicated class rows');
  assert(!nodes.some(node=>node.dataset.path===JSON.stringify(['providers',0,'address'])),'subscription must not show local server fields');
  const toggle=(name,checked)=>{const check=row(name).children[0];check.checked=checked;check.onchange();};
  const save=async()=>{descendants(ids.app).find(node=>node.tagName==='BUTTON' && node.textContent==='Save').onclick();await flush();};
  if(legacy){
    assert.equal(row('Coding').children[0].checked,true,'legacy Coding is enabled');
    assert.equal(row('Queen').children[0].checked,true,'legacy Queen is enabled');
    assert.equal(number('Coding').value,'100');assert.equal(number('Queen').value,'38');
    toggle('Build',false);await save();
    assert.deepEqual(saved[0].data.providers[0].capabilities.sort(),['Coding','Queen']);
    assert.equal(saved[0].data.providers[0].capabilitiesVersion,1);return;
  }
  const field=key=>descendants(ids.app).find(node=>node.dataset.path===JSON.stringify(['providers',0,key]));
  field('access').value='llm';field('access').onchange();await flush();
  assert(field('address'),'existing provider exposes address immediately after choosing Local LLM');
  assert(field('apiKey'),'Local LLM offers a masked key');assert.equal(field('apiKey').type,'password');
  assert(!descendants(ids.app).some(node=>node.tagName==='BUTTON'&&node.textContent==='Login'));
  field('address').value='http://localhost:1234';field('address').oninput();
  field('access').value='subscription';field('access').onchange();await flush();
  assert(!field('address'));assert(!field('apiKey'));
  field('access').value='llm';field('access').onchange();await flush();assert.equal(field('address').value,'http://localhost:1234');
  field('access').value='subscription';field('access').onchange();await flush();
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
providerThresholdUI().then(()=>providerThresholdUI(true)).catch(error=>{console.error(error);process.exitCode=1});
const {dashboardRows} = require('../haivequeen/web-client.js');
const filterState = {
  hives: [{name:'Plan', apiary:'A', plan_path:'plan.md'}, {name:'Plan', apiary:'B', plan_path:'plan.md'}],
  bees: [
    {name:'Queen',kind:'queen',apiary:'A',plan_path:'plan.md',agent:'queen-a'},
    {name:'Agent',kind:'bee',apiary:'A',plan_path:'plan.md',agent:'agent-a'},
    {name:'Sibling',kind:'bee',apiary:'A',plan_path:'plan.md',agent:'sibling-a'},
    {name:'Agent',kind:'bee',apiary:'B',plan_path:'plan.md',agent:'agent-b'}
  ]
};
assert.equal(dashboardRows(filterState,null).bees.length,4);
assert.equal(dashboardRows(filterState,{type:'project',project:'A'}).bees.length,3);
assert.equal(dashboardRows(filterState,{type:'plan',project:'B',plan:'plan.md'}).hives.length,1);
assert.equal(dashboardRows(filterState,{type:'queen',project:'A',plan:'plan.md'}).bees.length,3);
assert.deepEqual(dashboardRows(filterState,{type:'agent',project:'A',plan:'plan.md',agent:'agent-a',name:'Agent'}).bees.map(row=>row.name),['Queen','Agent']);
assert.equal(dashboardRows(filterState,{type:'project',project:'Missing'}).hives.length,0);
assert.equal(filterState.bees.length,4,'Filtering must not mutate live snapshots');
const {dashboardMetrics} = require('../haivequeen/web-client.js');
const metricRows=dashboardMetrics({bees:4,bee_capacity:16,tokens_in:120,tokens_out:30,start_ctx:{saving:-63}},String);
assert.equal(metricRows.find(row=>row.key==='bees').value,'4 / 16');
assert.equal(metricRows.find(row=>row.key==='io').value,'120 / 30');
assert.equal(metricRows.find(row=>row.key==='start').value,'-63%');
assert.equal(metricRows.length,7);
(async()=>{
  let resolveConfirmation;
  const sent=[];
  const confirmClient=new Client({confirm:()=>new Promise(resolve=>{resolveConfirmation=resolve;}),fetch:async(url,options)=>{sent.push([url,options]);return {ok:true,json:async()=>({ok:true})};}});
  const cancelled=confirmClient.action('force-stop',{},'Stop?');
  assert.equal(sent.length,0,'Wait for the internal confirmation');
  assert.equal(await confirmClient.action('force-stop',{},'Stop?'),null,'Only one pending confirmation for the action');
  resolveConfirmation(false);await cancelled;assert.equal(sent.length,0);
  const approved=confirmClient.action('force-stop',{},'Stop?');
  resolveConfirmation(true);await approved;
  assert.equal(sent.length,1);assert.equal(JSON.parse(sent[0][1].body).confirmed,true);
})().catch(error=>{console.error(error);process.exitCode=1;});

// Requested slow header animation runs while visible and pauses in background tabs.
{
  const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
  const body=source.slice(source.indexOf('  function animateHeader(){'),source.indexOf('  animateHeader();'));
  let tick,visibilityChange;const strokes=[];
  const context={clearRect(){},createRadialGradient(){return {addColorStop(){}};},fillRect(){},beginPath(){},moveTo(){},lineTo(){},closePath(){},fill(){},arc(){},setTransform(){},stroke(){strokes.push(this.strokeStyle);}};
  let toggle;const canvas={clientWidth:1000,clientHeight:104,getContext:()=>context,parentElement:{append(node){toggle=node;}}};
  const browserDocument={hidden:false,addEventListener(event,fn){if(event==='visibilitychange')visibilityChange=fn;},createElement(){return {setAttribute(){}};}};
  const run=new Function('$','window','document','ResizeObserver','requestAnimationFrame','cancelAnimationFrame','performance',body+'animateHeader();');
  run(()=>canvas,{devicePixelRatio:1},browserDocument,class {observe(){}},fn=>{tick=fn;return 1;},()=>{tick=null;},{now:()=>0});
  const first=strokes.slice();strokes.length=0;tick(1000);
  assert.notDeepEqual(strokes,first,'Honeycomb must visibly animate');
  browserDocument.hidden=true;visibilityChange();assert.equal(tick,null,'Hidden tab must stop animation');
  browserDocument.hidden=false;visibilityChange();assert.equal(typeof tick,'function','Visible tab must resume animation');
  assert.equal(toggle,undefined,'Decorative header must not add an animation button');
}

{
 const {orchestrationFor}=require('../haivequeen/web-client.js');
 const row={kind:'orch',project:'A',plan_path:'same.md'};
 const hives=[{apiary:'B',plan_path:'same.md',orch:{chain:[['Merge','done']]}},{apiary:'A',plan_path:'same.md',orch:{phase:'fix',chain:[['Merge','done'],['Check','red'],['Push','todo']]}}];
 const info=orchestrationFor(row,hives);assert.equal(info.n,2);assert.equal(info.m,3);assert.equal(info.phase,'fix');assert.equal(info.chain[1][1],'red');
 assert.equal(orchestrationFor({...row,kind:'bee'},hives),null);
 assert.equal(orchestrationFor({...row,project:'missing'},hives),null);
}

