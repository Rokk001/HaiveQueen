const assert=require('node:assert/strict');
const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
assert(source.includes('plan-overview-card'),'Plans overview must use actionable cards');
const document={activeElement:null};
class Node {
 constructor(tag,value=''){this.tagName=tag.toUpperCase();this.textContent=value==null?'':String(value);this.children=[];this.dataset={};this.style={};this.attrs={};this.value='';}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 setAttribute(key,value){this.attrs[key]=value;}
 getBoundingClientRect(){return {height:100};}
 querySelectorAll(){return [];}
 querySelector(selector){const key=selector.match(/data-plan-filter="([^"]+)"/);return key?all(this).find(node=>node.dataset.planFilter===key[1]):null;}
 focus(){document.activeElement=this;}
 setSelectionRange(start,end,direction){this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction;}
}
const all=node=>[node,...node.children.flatMap(all)],text=node=>all(node).map(item=>item.textContent).join(' ');
const el=(tag,value,cls)=>{const node=new Node(tag,value);node.className=cls||'';return node;},view=el('div');view.dataset.tab='Plans';
const button=(label,callback)=>{const node=el('button',label);node.onclick=callback;return node;};
const rows=[{apiary:'First project',name:'ACCEPTANCE-1',title:'Improve the welcome screen',plan_path:'docs/first.md',status:'REVIEW'}, {apiary:'Second project',name:'ACCEPTANCE-1',title:'Simplify account settings',plan_path:'plans/second.md',status:'WAITING',waiting_for:'Owner decision'}, {apiary:'Second project',name:'DRAFT-1',title:'Design a clear overview',plan_path:'plans/draft.md',status:'DRAFT'}, {apiary:'First project',name:'DONE-1',plan_path:'done.md',status:'DONE'}];
const questions=[{id:1,project:'Second project',plan_path:'plans/second.md',status:'open',item:'scope'}, {id:2,project:'Second project',plan_path:'plans/second.md',status:'open',item:'owner-check'}, {id:3,project:'First project',plan_path:'done.md',status:'answered',item:'owner-check-answered'}];
const opened=[],edited=[],conversations=[],requests=[];let detail={plan:{status:'DRAFT'},text:'# Current draft',revision:'fresh'};
const client={state:{projects:[{name:'First project'},{name:'Second project'}]},request:async route=>{requests.push(route);return detail;}};
const helperCode=source.slice(source.indexOf('  function planQuestions('),source.indexOf('  function questionKind('));
const kindCode=source.slice(source.indexOf('  function questionKind('),source.indexOf('  function questionAnswered('));
const renderCode=source.slice(source.indexOf('  function render() {'),source.indexOf('  function projectSelect('));
const api=new Function('document','window','view','el','button','t','section','fitDashboard','client','rows','openPlan','openPlanEditor','openConversation','key',
 "let tab='Plans',questionNavigation=false,logFollow=false,plans=rows,planDraft=null,questions=[],questionsLoaded=false;const planFilters={search:'',project:'',status:''};"+helperCode+kindCode+renderCode+"return {render,filterPlanRows,filters:planFilters,poll:rows=>{plans=rows;render();},loadQuestions:items=>{questions=items;questionsLoaded=true;render();},switchBack:()=>{view.dataset.tab='Questions';document.activeElement=null;render();}};")
(document,{scrollX:0,scrollY:0,scrollTo(){}},view,el,button,x=>x,title=>{const node=el('section',title);view.append(node);return node;},()=>{},client,rows,row=>opened.push(row),(row,result)=>edited.push([row,result]),(project,plan)=>conversations.push([project,plan]),row=>({project:row.project||row.apiary,plan:row.plan_path}));
const field=key=>view.querySelector('[data-plan-filter="'+key+'"]'),cards=()=>all(view).filter(node=>node.className==='plan-overview-card'),chip=(type,value)=>all(view).find(node=>node.dataset[type]===value),action=(node,label)=>all(node).find(item=>item.tagName==='BUTTON'&&item.textContent===label);
(async()=>{
 api.render();assert.equal(all(view).filter(node=>['TABLE','SELECT'].includes(node.tagName)).length,0,'Overview must not fall back to the old table and dropdowns');assert(chip('planStatus','needs-you').disabled);assert(text(view).includes('Loading messages…'));assert(!text(view).includes('No open questions'));
 api.loadQuestions(questions);assert.equal(cards().length,4);assert(text(cards()[0]).includes('Improve the welcome screen'));assert(text(cards()[0]).includes('Project · First project'));assert(text(cards()[1]).includes('Owner decision'));assert(text(cards()[1]).includes('1 question · 1 acceptance'));assert(text(cards()[2]).includes('No open questions'));assert(!action(cards()[2],'Conversation'));
 assert.deepEqual(api.filterPlanRows(rows,{search:'WELCOME',project:'First project',status:'REVIEW'},questions).map(row=>row.plan_path),['docs/first.md']);assert.equal(api.filterPlanRows(rows,{search:'PLANS/SECOND',project:'',status:'active'},questions).length,1);assert.equal(api.filterPlanRows(rows,{search:'SECOND PROJECT',project:'',status:'needs-you'},questions).length,1);
 chip('planStatus','needs-you').onclick();assert.equal(cards().length,1,'Review status alone must not invent a pending question');await action(cards()[0],'Messages · 1 question · 1 acceptance').onclick();assert.deepEqual(conversations.pop(),['Second project','plans/second.md']);
 action(view,'Clear filters').onclick();await action(cards()[0],'View result').onclick();assert.equal(opened.at(-1).plan_path,'docs/first.md');await action(cards()[2],'Edit draft').onclick();assert(requests.at(-1).includes('plan=plans%2Fdraft.md'));assert.equal(edited.at(-1)[1].revision,'fresh');
 detail={plan:{status:'RUNNING'},text:'# Already submitted',revision:'new'};await action(cards()[2],'Edit draft').onclick();assert.equal(opened.at(-1).status,'RUNNING');assert.equal(edited.length,1,'Stale draft state must not open an editor');
 let search=field('search');search.focus();search.value='ACCEPTANCE-1';search.setSelectionRange(2,8,'backward');search.oninput();assert.equal(document.activeElement,field('search'));assert.equal(field('search').selectionStart,2);assert.equal(field('search').selectionEnd,8);assert.equal(field('search').selectionDirection,'backward');
 chip('planProject','Second project').onclick();chip('planStatus','active').onclick();assert.equal(cards().length,1);assert(text(view).includes('1 of 4 plans'));search=field('search');search.focus();search.setSelectionRange(3,6,'forward');api.poll([...rows]);assert.equal(document.activeElement,field('search'));assert.equal(field('search').selectionEnd,6);assert.equal(chip('planProject','Second project').attrs['aria-pressed'],'true');api.switchBack();assert.equal(field('search').value,'ACCEPTANCE-1');assert.equal(chip('planStatus','active').attrs['aria-pressed'],'true');
 search=field('search');search.focus();search.oncompositionstart();search.value='Unfinished input';search.oninput();api.poll([...rows]);assert.equal(field('search'),search);search.oncompositionend();assert(text(view).includes('No plans match these filters.'));action(view,'Clear filters').onclick();assert.equal(cards().length,4);assert.equal(field('search').value,'');assert.equal(document.activeElement,field('search'));
 chip('planProject','Second project').onclick();api.poll([rows[0]]);assert.equal(chip('planProject','Second project').attrs['aria-pressed'],'true');assert(text(view).includes('No plans match these filters.'));
 action(view,'Clear filters').onclick();api.poll([{...rows[2],missing:true},{...rows[2],plan_path:'recovery.md',recovery_error:'Recovery pending'}]);assert(!all(view).some(node=>node.tagName==='BUTTON'&&node.textContent==='Edit draft'));assert.equal(all(view).filter(node=>node.tagName==='BUTTON'&&node.textContent==='View plan').length,2);
 console.log('Actionable Plans cards, real message state, actions, chips and polling preservation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
