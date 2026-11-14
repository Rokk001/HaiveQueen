const assert=require('node:assert/strict');
const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
const start=source.indexOf("  void client.request('jobs/status').then(history=>");
assert(start>=0,'restore persisted owner actions on page load');
const body=source.slice(start,source.indexOf('  void tick();',start)).replace('void client.request','return client.request');
const nodes=new Map();
function el(tag,text){return {tag,text,children:[],append(...items){for(const item of items){if(item.parent)item.parent.children=item.parent.children.filter(x=>x!==item);item.parent=this;this.children.push(item);}}};}
nodes.set('live',el('section'));
const jobs=new Map([['already',{id:'already'}]]),rendered=[];
const client={request:async path=>{assert.equal(path,'jobs/status');return [
  {id:'old',key:'tools:install',running:false,result:{done:true}},
  {id:'interrupted',running:false,error:'Outcome unknown'},
  {id:'active',running:true}, {id:'already',running:true}
];},notice:message=>assert.fail(message)};
const renderJob=job=>{rendered.push(job.id);const panel=el('section',job.error);nodes.set('job-'+job.id,panel);nodes.get('live').append(panel);};
(async()=>{
  await new Function('client','el','t','jobs','renderJob','$',body)(client,el,x=>x,jobs,renderJob,id=>nodes.get(id));
  assert.deepEqual(rendered,['old','interrupted','active']);
  const history=nodes.get('live').children.find(node=>node.tag==='details');
  assert(history&&!history.open,'past actions remain collapsed');
  assert(history.children.includes(nodes.get('job-old')));
  assert(history.children.includes(nodes.get('job-interrupted')));
  assert(nodes.get('live').children.includes(nodes.get('job-active')));
  assert.equal(jobs.get('old').result.done,true);
  console.log('Owner action history restoration passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
