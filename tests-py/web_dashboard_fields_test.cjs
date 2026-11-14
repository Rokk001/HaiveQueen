const assert=require('node:assert/strict');
const fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
class Element {
 constructor(tag,text='',cls=''){this.tag=tag;this.textContent=text;this.className=cls;this.children=[];this.dataset={};this.classList={add:()=>{}};}
 append(...nodes){this.children.push(...nodes);}
 prepend(...nodes){this.children.unshift(...nodes);}
 setAttribute(k,v){this[k]=v;}
}
const el=(...args)=>new Element(...args);
const body=source.slice(source.indexOf('  function table('),source.indexOf('  function section('));
const table=new Function('el','t','pretty','valueView','isPackagePlaceholder','filterLink','button','openPlan','openAgent','client','tokens','meter',body+'return table;')(el,x=>x,x=>x,v=>el('span',v??'–'),()=>false,(s,type,row)=>{const a=el('a',s);a.filterType=type;return a;},s=>el('button',s),()=>{},()=>{},{state:{}},String,()=>el('progress'));
const rows=[{kind:'orch',name:'HQ',apiary:'demo',model:'–',ctx:0,ctx_limit:0,queen:'-'},{kind:'queen',name:'Ada',apiary:'demo',model:'Model A',ctx:0,ctx_limit:100,queen:'Ada'}];
const rendered=table(rows,['model','ctx','queen','apiary'],null,{compact:true});
const find=(n,p)=>[...(p(n)?[n]:[]),...n.children.flatMap(c=>find(c,p))];
for(const label of ['No model','No context value available','No Queen assigned'])assert(find(rendered,n=>n['aria-label']===label).length,label);
assert(find(rendered,n=>n.tag==='progress').length===1,'Real zero context retains meter');
assert(find(rendered,n=>n.textContent==='Model A').length===1);
assert(find(rendered,n=>n.filterType==='project').length===2);
const flat=table(rows,['name','model'],null,{compact:true,hierarchy:true,tree:true});
assert.equal(find(flat,n=>['table','thead','tbody','tr','td','th'].includes(n.tag)).length,0,'Tree rows must not contain tables');
console.log('Dashboard missing values and project column passed');

const hierarchy=table(rows,['name'],null,{compact:true,hierarchy:true});
assert.equal(find(hierarchy,n=>n['aria-label']==='Open details · Queen · Ada').length,1,'Queen details retain their accessible role label');

const metrics=table([{kind:'queen',tin:309200,tout:2600,ctx:18000,ctx_limit:100000}],['tokens','ctx']);
assert.equal(find(metrics,n=>n.className==='cyan token-values').length,1,'Token pair must have a dedicated nonwrapping layout');

const limitsCode=source.slice(source.indexOf('  function limitsView('),source.indexOf('  function planProgress('));
const limitsView=new Function('el','t','limitHelp','meter',limitsCode+'return limitsView;')(el,x=>x,(node,title,description)=>{node.help=description;return node;},()=>el('progress'));
const reason='The configured server does not answer';
const limits=limitsView([{name:'Local',model:'Local model',reserve:{text:'+99%',level:'ok'},slots:{offline:'offline',reason},weekly:{text:'no limit'}}]);
assert.equal(find(limits,n=>n.textContent===reason).length,0,'Offline reason belongs in details, not the overview');
assert.equal(find(limits,n=>n.textContent==='no limit').length,0,'Offline overview needs only its status');
assert.equal(find(limits,n=>n.textContent==='offline'&&n.help===reason).length,1,'Offline details remain available');
