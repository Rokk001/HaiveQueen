const assert=require('node:assert/strict');
const {hiveTree,pendingQueue}=require('../haivequeen/web-client.js');
const plans=[{apiary:'p',name:'Plan',plan_path:'a.md'}];
const rows=[{project:'p',plan_path:'a.md',kind:'queen',agent:'q',name:'Q'},{project:'p',plan_path:'a.md',kind:'bee',agent:'a',name:'A'},{project:'p',plan_path:'a.md',kind:'bee',agent:'s',parent_agent:'a',name:'S'},{project:'p',plan_path:'a.md',kind:'bee',name:'–'}];
const tree=hiveTree(plans,rows,[{name:'p'},{name:'empty'}]);
assert.deepEqual(tree.map(n=>[n.type,n.depth]),[['project',0],['plan',1],['queen',2],['bee',3],['bee',4],['project',0]]);
assert.equal(new Set(tree.map(n=>n.key)).size,tree.length);
assert.equal(tree[4].parent,tree[3].key);
const queue=pendingQueue({queued:99,items:[{project:'p',plan_path:'a.md',status:'QUEUED'},{project:'p',plan_path:'b.md',status:'in_run'},{project:'p',plan_path:'c.md',status:'DRAFT'},{project:'p',plan_path:'c.md',status:'DRAFT'},{project:'p',plan_path:'d.md',status:'queued'},{project:'p',plan_path:'e.md',status:'ready'}]},plans);
assert.equal(queue.queued,2);assert.equal(queue.pending,1);assert.equal(queue.drafts,1);
assert.deepEqual(queue.items.map(i=>i.status),['DRAFT','QUEUED']);
console.log('Tree hierarchy and pending queue passed');

const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
const fragment=source.slice(source.indexOf("    const queueList=el('ol'"),source.indexOf("    const columns=el('div'",source.indexOf("    const queueList=el('ol'")));
const el=(tag,text,cls)=>({tag,text,cls,children:[],attrs:{},append(...items){this.children.push(...items);},setAttribute(k,v){this.attrs[k]=v;}});
const panel=el('section'),opened=[];
const items=[{project:'Demo',plan_path:'plans/STARTUP-PLAN.md',title:'Demo: long description '.repeat(20),status:'DRAFT'},
 {project:'Other',plan_path:'plans/STARTUP-PLAN.md',title:'Another plan',status:'QUEUED'}];
new Function('el','button','queue','queueData','t','openQueuedPlan',fragment)(el,(text,fn)=>({...el('button',text),onclick:fn}),panel,{items},x=>x,item=>opened.push(item));
const list=panel.children[0],row=list.children[0].children[0],heading=row.children[0];
assert.equal(heading.children[0].children[1].text,'STARTUP','Use a compact plan identity, not a paragraph as heading');
assert.equal(heading.children[1].text,'Draft');
assert.equal(row.children[1].cls,'queue-description');
assert.equal(row.children[1].text,items[0].title,'Retain the complete description for accessible rendering');
assert.equal(row.attrs['aria-haspopup'],'dialog');assert(row.attrs['aria-label'].includes(items[0].title));
assert.equal(row.title,undefined,'No hover tooltip required');
row.onclick();list.children[1].children[0].onclick();assert.deepEqual(opened,items,'Same filename in different projects still opens the correct plan');
console.log('Compact queue rendering and plan navigation passed');
