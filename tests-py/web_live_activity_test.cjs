const assert=require('node:assert/strict');
const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
class Node {
 constructor(tag,text='',className=''){this.tagName=tag;this.textContent=text;this.className=className;this.children=[];this.isConnected=true;this.open=false;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(){for(const child of this.children)child.isConnected=false;this.children=[];}
 set innerHTML(_value){throw Error('Recorded output must never become HTML');}
}
const el=(...args)=>new Node(...args),t=value=>value;
const render=new Function('el','t',source.slice(source.indexOf('  function renderLiveActivity('),source.indexOf('  async function attachConsole('))+'return renderLiveActivity;')(el,t);
const run={activity:el('div'),output:el('pre'),project:'selected-project',agent:'selected-agent',initial:true};
const items=[{id:'a',label:'Reading files',command:'cat <script>bad()</script>',output:'<img src=x onerror=bad()>',state:'running',exit:null}];
render(run,items);
const first=run.activity.children[1];assert.equal(first.open,false);
assert.equal(first.children[1].textContent,items[0].command);
assert.equal(first.children[2].textContent,items[0].output);
first.open=true;first.ontoggle();first.scrollTop=25;
render(run,JSON.parse(JSON.stringify(items)));
assert.equal(run.activity.children[1],first,'Unchanged polls preserve selection and scrolling nodes');
assert.equal(first.scrollTop,25);
const completed=[{...items[0],state:'completed',exit:0}];render(run,completed);
assert.equal(run.activity.children[1].open,true,'Expanded details survive changed results');
assert.match(run.activity.children[1].children[0].textContent,/Completed/);
render(run,[]);assert.equal(run.activity.hidden,true);assert.equal(run.activity.children.length,0);
let requested;
const client={request:async route=>{requested=route;return {lines:[['green','Existing milestone']],activity:items};}};
const poll=new Function('client','el','t','renderLiveActivity',source.slice(source.indexOf('  async function pollLive('),source.indexOf('  function renderLogin('))+'return pollLive;')(client,el,t,render);
(async()=>{
 await poll(run);
 assert.equal(requested,'live?project=selected-project&agent=selected-agent');
 assert.equal(run.output.children[0].textContent,'Existing milestone');
 assert.equal(run.activity.hidden,false);
 assert(source.includes('hive:row.hive,activity,output'),'Selected agent console must own its activity container');
 console.log('Recorded activity, escaped details, selection identity and poll preservation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
