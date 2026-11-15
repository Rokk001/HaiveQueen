const assert=require('node:assert/strict');
const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
const {LogPager,testTimingNotice}=require('../haivequeen/web-client.js');
class Element {
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.isConnected=true;this.open=true;this.classList={add(){}};this.scrollTop=0;this.clientHeight=100;this.scrollHeight=100;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  insertBefore(node,before){this.children.splice(this.children.indexOf(before),0,node);}
  setAttribute(name,value){this[name]=value;}
  querySelector(tag){return this.children.find(n=>n.tag===tag);}
  addEventListener(name,fn){this[name]=fn;}
}
const el=(tag,text)=>new Element(tag,text);
const all=(node,tag)=>[...(node.tag===tag?[node]:[]),...node.children.flatMap(n=>all(n,tag))];
const nodes={'detail':el('dialog'),'detail-body':el('body')};
const pending=[],timers=new Map();let timerId=0;
const client={request(url){return new Promise((resolve,reject)=>pending.push({url,resolve,reject}));}};
const body=source.slice(source.indexOf('  function showStepLog('),source.indexOf('  function orchestrationView('));
const show=new Function('el','$','detail','t','client','key','LogPager','setTimeout','clearTimeout','testTimingNotice','document',
  'let stepLogTimer;'+body+'return showStepLog;')(el,id=>nodes[id],()=>{},x=>x,client,()=>({project:'demo',plan:'plan.md'}),LogPager,
    fn=>{timers.set(++timerId,fn);return timerId;},id=>timers.delete(id),testTimingNotice,{createElement:el});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const reply=(request,output,cursor,more=false,extra={})=>request.resolve({status:'running',reason:'',run:'run1',source:'',output,cursor,more,references:[],...extra});
const tick=()=>{const entry=timers.entries().next().value;assert(entry,'poll timer expected');timers.delete(entry[0]);entry[1]();};
(async()=>{
  show(null,{name:'Plan'},'Build');
  reply(pending.shift(),'first',[100,0],true);await flush();
  const root=nodes['detail-body'],output=all(root,'pre')[0],buttons=all(root,'button');
  const [first,next,follow]=buttons;
  assert.equal(output.textContent,'first');
  first.onclick();
  assert.equal(new URLSearchParams(pending[0].url.split('?')[1]).get('cursor'),'null');
  reply(pending.shift(),'history',[50,0],true);await flush();
  assert.equal(output.textContent,'history');assert.equal(timers.size,0,'history reading must not auto-advance');
  next.onclick();assert.equal(new URLSearchParams(pending[0].url.split('?')[1]).get('cursor'),'[50,0]');
  reply(pending.shift(),'next history',[100,0]);await flush();
  assert.equal(output.textContent,'next history');assert.equal(next.disabled,true);
  follow.onclick();const stale=pending.shift();
  const select=all(root,'select')[0];select.value='file-token';select.onchange();
  reply(stale,'stale console',[200,0]);await flush();
  assert.notEqual(output.textContent,'stale console');tick();
  assert.equal(new URLSearchParams(pending[0].url.split('?')[1]).get('source'),'file-token');
  reply(pending.shift(),'file output',[20,0],false,{source:'file-token',references:[{source:'file-token',path:'out/file.txt'}]});await flush();
  assert.equal(output.textContent,'file output');
  tick();reply(pending.shift(),'',[20,0],false,{source:'file-token'});await flush();
  assert.equal(output.textContent,'file output','empty poll retains selected file output');
  tick();reply(pending.shift(),'new attempt',[30,0],false,{run:'run2',reset:true});await flush();
  assert.equal(output.textContent,'new attempt');assert.equal(select.value,'');
  nodes.detail.open=false;nodes.detail.close();assert.equal(timers.size,0);
  console.log('Step log paging interactions passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
