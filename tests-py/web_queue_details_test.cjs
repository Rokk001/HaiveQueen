const assert=require('node:assert/strict');
const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
const start=source.indexOf('  async function openQueuedPlan(');
assert(start>=0);
const body=source.slice(start,source.indexOf('  async function openPlan(',start));
(async()=>{
 const selected=[],errors=[];
 const correct={apiary:'A',plan_path:'plan.md',name:'Plan',total:12};
 const open=new Function('client','openPlan','detail','t',body+'return openQueuedPlan;')(
  {request:async()=>[{apiary:'B',plan_path:'plan.md'},correct]},
  async row=>selected.push(row),(title,message)=>errors.push(message),x=>x);
 await open({project:'A',plan_path:'plan.md'});
 assert.equal(selected[0],correct);
 await open({project:'A',plan_path:'missing.md'});
 assert.equal(errors.length,1);
 assert.equal(selected.length,1);
 console.log('Queue opens matching full plan details and handles missing plans');
})().catch(e=>{console.error(e);process.exitCode=1;});
