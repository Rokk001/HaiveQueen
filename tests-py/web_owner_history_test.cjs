const assert=require('node:assert/strict');
const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
const start=source.indexOf("  void client.request('jobs/status').then(history=>");
assert(start>=0,'restore persisted owner actions on page load');
const loader=source.slice(start,source.indexOf('  void tick();',start)).replace('void client.request','return client.request');
const renderer=source.slice(source.indexOf('  function renderJob('),source.indexOf('  async function refreshLists()'));
const logs=source.slice(source.indexOf('  function renderLog()'),source.indexOf('  function planDocument('));
(async()=>{
 for(const tab of ['Dashboard','Questions','Releases','Logs']){
  const nodes=new Map();
  function el(tag,text){const node={tag,text,children:[],append(...items){for(const item of items){item.remove();item.parent=this;this.children.push(item);}},prepend(item){item.remove();item.parent=this;this.children.unshift(item);},remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);this.parent=null;},replaceChildren(...items){for(const item of [...this.children])item.remove();this.append(...items);}};Object.defineProperty(node,'id',{set(id){nodes.set(id,node);}});return node;}
  const live=el('aside'),view=el('main');nodes.set('live',live);
  const jobs=new Map([['already',{id:'already'}]]),promotions=new Map();
  const saved=[{id:'old',key:'tools:install',running:false,result:{done:true}},
    {id:'interrupted',running:false,error:'Outcome unknown'},
    {id:'active',running:true},{id:'already',running:true},
    {id:'release',key:'release-promotion:demo',running:false,result:{pending:true},started:2}];
  const client={request:async path=>{assert.equal(path,'jobs/status');return saved;},notice:message=>assert.fail(message)};
  const run=new Function('client','el','t','jobs','$','releasePromotions','tab','view','button','section','logRows','detail',
    'let ownerHistory=null,logFilter="",showNoise=false,logFollow=true; const render=()=>{if(tab==="Logs")renderLog();};'+renderer+logs+
    'return {load:async()=>{'+loader+'},showLogs:renderLog,history:()=>ownerHistory};');
  const api=run(client,el,x=>x,jobs,id=>nodes.get(id),promotions,tab,view,label=>el('button',label),()=>{const box=el('section');view.append(box);return box;},()=>[],()=>{});
  await api.load();
  assert.deepEqual(live.children,[nodes.get('job-active')],tab+': only active restored actions belong in the global area');
  assert.equal(promotions.get('demo').id,'release','Pending publication state must still restore');
  assert.equal(jobs.get('old').result.done,true);
  const history=api.history();assert(history&&!history.open,'History stays collapsed initially');
  assert(history.children.includes(nodes.get('job-old')));
  assert(history.children.includes(nodes.get('job-interrupted')),'Historical failures remain available');
  if(tab==='Logs')assert.equal(history.parent,view,'Late history refreshes the open Logs tab');
  else assert(!history.parent,'History is detached outside Logs');
  view.replaceChildren();api.showLogs();assert.equal(history.parent,view,'Logs explicitly mounts saved history');
  history.open=true;view.replaceChildren();assert.equal(history.parent,null,'Navigating away detaches history');
  api.showLogs();assert.equal(history.parent,view);assert.equal(history.open,true,'Returning to Logs retains expansion');
 }
 console.log('Owner history stays in Logs; active actions and publication state restore');
})().catch(error=>{console.error(error);process.exitCode=1;});
