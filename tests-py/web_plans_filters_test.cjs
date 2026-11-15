const assert=require('node:assert/strict');
const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
assert(source.includes('function filterPlanRows('),'Plans need combinable search, project and status filters');
const document={activeElement:null};
class Node {
 constructor(tag,text=''){this.tagName=tag.toUpperCase();this.textContent=text||'';this.children=[];this.dataset={};this.style={};this.attrs={};this.value='';}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 setAttribute(key,value){this.attrs[key]=value;}
 getBoundingClientRect(){return {height:100};}
 querySelectorAll(){return [];}
 querySelector(selector){const key=selector.match(/data-plan-filter="([^"]+)"/);return key?all(this).find(node=>node.dataset.planFilter===key[1]):null;}
 focus(){document.activeElement=this;}
 setSelectionRange(start,end,direction){this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction;}
}
const all=node=>[node,...node.children.flatMap(all)];
const text=node=>all(node).map(item=>item.textContent).join(' ');
const el=(tag,value)=>new Node(tag,value),view=el('div');view.dataset.tab='Plans';
const button=(label,callback)=>{const node=el('button',label);node.onclick=callback;return node;};
const originalRows=[{apiary:'First project',name:'Acceptance-1',plan_path:'docs/first.md',status:'REVIEW'}, {apiary:'Second project',name:'Acceptance-1',plan_path:'plans/second.md',status:'REVIEW'}, {apiary:'Second project',name:'Acceptance-2',plan_path:'plans/draft.md',status:'DRAFT'}];
let tableRows=[];
const filterCode=source.slice(source.indexOf('  function filterPlanRows('),source.indexOf('  function questionKind('));
const renderCode=source.slice(source.indexOf('  function render() {'),source.indexOf('  function projectSelect('));
const api=new Function('document','window','view','el','button','t','section','table','fitDashboard','client','originalRows',
 "let tab='Plans',questionNavigation=false,logFollow=false,plans=originalRows,planDraft=null;const planFilters={search:'',project:'',status:''};const openPlanEditor=()=>{},openPlan=()=>{};"+filterCode+renderCode+"return {render,filterPlanRows,filters:planFilters,poll:rows=>{plans=rows;render();},switchBack:()=>{view.dataset.tab='Questions';document.activeElement=null;render();}};")
 (document,{scrollX:0,scrollY:0,scrollTo(){}},view,el,button,x=>x,title=>{const node=el('section',title);view.append(node);return node;},rows=>{tableRows=rows;return el('table');},()=>{},{state:{projects:[{name:'First project'},{name:'Second project'}]}},originalRows);
assert.deepEqual(api.filterPlanRows(originalRows,{search:'ACCEPTANCE-1',project:'Second project',status:'REVIEW'}).map(row=>row.plan_path),['plans/second.md']);
assert.equal(api.filterPlanRows(originalRows,{search:'DOCS/FIRST',project:'',status:''}).length,1);
assert.equal(api.filterPlanRows(originalRows,{search:'SECOND PROJECT',project:'',status:''}).length,2);
api.render();
const field=key=>view.querySelector('[data-plan-filter="'+key+'"]');
let search=field('search');search.focus();search.value='Acceptance-1';search.setSelectionRange(3,8,'backward');search.oninput();
assert.equal(document.activeElement,field('search'),'Typing restores search focus after filtering');assert.equal(field('search').selectionStart,3);assert.equal(field('search').selectionEnd,8);assert.equal(field('search').selectionDirection,'backward');
let project=field('project');project.focus();project.value='Second project';project.onchange();let status=field('status');status.focus();status.value='REVIEW';status.onchange();assert.equal(tableRows.length,1);assert.equal(tableRows[0].plan_path,'plans/second.md');assert(text(view).includes('1 of 3 plans'));
search=field('search');search.focus();search.setSelectionRange(2,5,'forward');api.poll([...originalRows]);assert.equal(document.activeElement,field('search'));assert.equal(field('search').value,'Acceptance-1');assert.equal(field('search').selectionEnd,5);assert.equal(field('project').value,'Second project');
api.switchBack();assert.equal(field('search').value,'Acceptance-1');assert.equal(field('status').value,'REVIEW','Tab switches retain the chosen filters');
search=field('search');search.focus();search.oncompositionstart();search.value='Pending composition';search.oninput();api.poll([...originalRows]);assert.equal(field('search'),search,'Polling must not replace an active composition input');search.oncompositionend();assert(text(view).includes('No plans match these filters.'));assert(text(view).includes('0 of 3 plans'));
all(view).find(node=>node.tagName==='BUTTON'&&node.textContent==='Clear filters').onclick();assert.equal(tableRows.length,3);assert.equal(field('search').value,'');assert.equal(field('project').value,'');assert.equal(field('status').value,'');assert.equal(document.activeElement,field('search'));
project=field('project');project.value='Second project';project.onchange();api.poll([originalRows[0]]);assert.equal(field('project').value,'Second project');assert(field('project').children.some(option=>option.value==='Second project'),'Selected option survives a poll with no matching plans');assert(text(view).includes('No plans match these filters.'));
console.log('Plans combined filters, reset, polling selection and composition passed');
