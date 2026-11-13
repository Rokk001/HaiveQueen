const assert = require('node:assert/strict');
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
