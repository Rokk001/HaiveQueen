const assert=require('node:assert/strict');
const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
class Node {
 constructor(tag,text=''){this.tagName=tag.toUpperCase();this.children=[];this.textContent=text;}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(){}
}
const el=(tag,text='')=>new Node(tag,text);
const text=node=>node.textContent+node.children.map(text).join('');
const all=(node,tag)=>[...(node.tagName===tag?[node]:[]),...node.children.flatMap(n=>all(n,tag))];
const body=source.slice(source.indexOf('  function planDocument(text)'),source.indexOf('  function entityFrame'));
const {planDocument,packageCards}=new Function('el','document','t','meter',body+'return {planDocument,packageCards};')(el,{createTextNode:value=>el('#text',value)},x=>x,(n,m)=>{const p=el('progress');p.value=n;p.max=m;return p;});
const doc=planDocument('# Title\n\n**Important** and `code` <script>bad()</script>\n\n1. First\n2. Second\n\n| Name | State |\n| --- | --- |\n| Build | Done |\n\n```\nline 1\nline 2\n```'.replace(/\\n/g,'\n'));
assert.equal(all(doc,'STRONG')[0].textContent,'Important');
assert.equal(all(doc,'OL')[0].children.length,2);
assert.equal(all(doc,'TABLE').length,1);
assert.equal(all(doc,'PRE')[0].textContent,'line 1\nline 2\n');
assert.equal(all(doc,'SCRIPT').length,0,'Plan content must remain inert text');
const cards=packageCards([{id:'P1',title:'Implement adapter',status:'done',step:'4/4',task:'Verify output',depends_on:['P0'],requirements:['A-1'],agent:'Worker',verify:'code'}]);
assert(text(cards).includes('Implement adapter'));
assert(text(cards).includes('Completed'));
assert(text(cards).includes('Verify output'));
assert(text(cards).includes('P0'));
assert.equal(all(cards,'PROGRESS')[0].value,4);
assert.equal(all(cards,'PROGRESS')[0].max,4);
console.log('Plan reader and package cards passed');

{
 const clicked=[];
 const body=source.slice(source.indexOf('  function orchestrationView('),source.indexOf('  function limitsView('));
 const view=new Function('el','t','showStepLog',body+'return orchestrationView;')(el,x=>x,(button,row,step)=>clicked.push([row,step]));
 const row={name:'Plan',project:'Project',plan_path:'plan.md'};
 const node=view({chain:[['Merge','done'],['Check','running'],['Build','waiting']],n:2,m:3},row);
 const buttons=all(node,'BUTTON');
 buttons.forEach(button=>button.onclick());
 assert.deepEqual(clicked,[[row,'Merge'],[row,'Check'],[row,'Build']]);
 assert(buttons[1].textContent.startsWith('▶'));
 assert(buttons[2].textContent.startsWith('◷'));
}
