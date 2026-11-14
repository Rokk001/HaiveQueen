const assert=require('node:assert/strict');
const {resolvePlanActions,Client}=require('../haivequeen/web-client.js');
(async()=>{
 const plan={apiary:'A',plan_path:'p.md',status:'Plan paused',paused:true};
 const requests=[];
 const request=async route=>{requests.push(route);return [
  {apiary:'B',plan_path:'p.md',status:'RUNNING'},
  {apiary:'A',plan_path:'p.md',status:'PAUSED',stopped:true,has_hive:true}];};
 const actions=await resolvePlanActions(plan,request);
 assert(actions.includes('resume'),'Dashboard paused plan must offer Resume');
 assert(!actions.includes('force-stop'));
 assert.deepEqual(requests,['plans']);
 const busy=await resolvePlanActions(plan,async()=>[{apiary:'A',plan_path:'p.md',status:'PAUSED',stopped:false,has_hive:true}]);
 assert(!busy.includes('resume'),'Preserve the existing live-work guard');
 assert(busy.includes('force-stop'));
 const running=await resolvePlanActions({...plan,status:'Running'},async()=>[{apiary:'A',plan_path:'p.md',status:'RUNNING',has_hive:true}]);
 assert(running.includes('stop-gracefully'));
 assert(!running.includes('resume'));
 assert.deepEqual(await resolvePlanActions(plan,async()=>[]),[],'Missing plans must not acquire actions');
 await assert.rejects(resolvePlanActions(plan,async()=>{throw Error('offline');}),/offline/);
 const sent=[],confirmations=[];
 const listed=[{apiary:'A',plan_path:'pause',status:'PAUSED',stopped:true},
  {apiary:'A',plan_path:'run',status:'RUNNING',has_hive:true},
  {apiary:'A',plan_path:'done',status:'DONE'},
  {apiary:'A',plan_path:'draft',status:'DRAFT'},
  {apiary:'A',plan_path:'cancel',status:'CANCELLED'},
  {apiary:'A',plan_path:'draining',status:'PAUSED',stopped:false,has_hive:true}];
 const client=new Client({confirm:async text=>{confirmations.push(text);return true;}});
 client.request=async(route,data)=>{if(route==='plans')return listed;sent.push([route,data]);return {ok:true};};
 await client.plansAction('resume',count=>'Resume '+count+' paused plans?');
 assert.deepEqual(sent,[['plan/resume',{project:'A',plan:'pause',confirmed:true}]]);
 assert(confirmations[0].startsWith('Resume 1 paused plans?'));assert(confirmations[0].includes('A / pause'));
 sent.length=0;
 await client.plansAction('stop-gracefully',count=>'Stop '+count+' plans?');
 assert.deepEqual(sent.map(x=>x[1].plan),['run']);
 sent.length=0;
 await client.plansAction('force-stop',count=>'Force '+count+' plans?');
 assert.deepEqual(sent.map(x=>x[1].plan),['run','draining']);
 sent.length=0;client.confirm=async()=>false;
 await client.plansAction('resume',String);assert.equal(sent.length,0);
 client.confirm=async()=>true;
 client.request=async(route,data)=>{if(route==='plans')return listed;throw Error('server refusal');};
 await assert.rejects(client.plansAction('resume',String),/server refusal/);
 assert.equal(client.pending.size,0);
 let releaseBatch;
 client.request=async route=>{if(route==='plans')return listed;return new Promise(resolve=>{releaseBatch=resolve;});};
 const first=client.plansAction('resume',String);
 while(!releaseBatch)await Promise.resolve();
 assert.equal(await client.plansAction('force-stop',String),null,'Do not overlap bulk actions');
 releaseBatch({ok:true});await first;
 const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
 const body=source.slice(source.indexOf('  function renderControls()'),source.indexOf("  document.addEventListener('click',event=>{const menu="));
 const el=(tag,text='',className='')=>({tag,text,className,children:[],classList:{add(){}},append(...nodes){this.children.push(...nodes);}});
 const root=el('div');root.querySelector=()=>null;root.replaceChildren=(...nodes)=>{root.children=nodes;};
 const render=new Function('client','el','$','t','button','actions','orchestratorDisabled',body+'return renderControls;')(
  {state:{orchestrator:{label:'RUNNING'}}},el,()=>root,x=>x,(text,click)=>({...el('button',text),click}),
  {'start':'Start','stop-gracefully':'Stop gracefully','force-stop':'Force stop','resume':'Resume'},()=>false);
 render();
 const all=node=>[node,...node.children.flatMap(all)];
 const group=all(root).find(node=>node.className==='control-group'&&node.children[0]?.text==='PLANS');
 assert(group,'Controls must contain a PLANS section');
 assert.deepEqual(group.children.slice(1).map(node=>node.text),['Stop gracefully','Force stop','Resume']);
 console.log('Dashboard plan actions use canonical lifecycle state');
})().catch(error=>{console.error(error);process.exitCode=1;});
