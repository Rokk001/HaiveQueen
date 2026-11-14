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
