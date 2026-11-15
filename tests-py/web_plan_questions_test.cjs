const assert=require('node:assert/strict');
const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
const start=source.indexOf('  function questionKind(');
assert(start>=0,'Question kind must be determined from actual metadata');
const helpers=new Function(source.slice(start,source.indexOf('  function renderQuestions()',start))+'return {questionKind,questionAnswered};')();
assert.equal(helpers.questionKind({item:'owner-check'}),'Acceptance');
assert.equal(helpers.questionKind({item:'owner-check-answered',answered_at:'today'}),'Acceptance');
assert.equal(helpers.questionKind({kind:'decision'}),'Decision');
assert.equal(helpers.questionKind({item:'technical-choice'}),'Question');
assert.equal(helpers.questionAnswered({answered_at:'today'}),true);
assert.equal(helpers.questionAnswered({answer:''}),false);
assert.equal(helpers.questionAnswered({status:'open',answered_at:'today',answer:'note'}),false,'Saving acceptance note must keep check open');
assert(source.includes('questionDrafts.set'),'Drafts must survive rerender and navigation');
assert(source.includes("client.request('questions?history=1')"),'History must be loaded');
assert(source.includes("client.action(current.plan?'plans/save':'plans/create'"),'Draft saving must be distinct from submitting');
assert(source.includes("act('plan/start'"),'Submission must remain explicit');
assert(source.includes("row.status==='DRAFT'"),'Only drafts can be edited');
assert(source.includes("button('Release readiness gate'"),'Release must clearly name the readiness gate');
console.log('Plan authoring and Questions contracts passed');

// Exercise rendered controls and their actual payloads, including refresh survival.
class Node {
 constructor(tag,text=''){this.tagName=tag.toUpperCase();this.textContent=text||'';this.children=[];this.dataset={};this.attrs={};this.value='';this.disabled=false;}
 append(...nodes){this.children.push(...nodes);}
 prepend(...nodes){this.children.unshift(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 setAttribute(key,value){this.attrs[key]=value;}
 addEventListener(name,fn){this['listener_'+name]=fn;}
 close(){}
 focus(){this.focused=true;}
 scrollIntoView(){this.scrolled=true;}
}
const el=(tag,text)=>new Node(tag,text),all=(node,tag)=>[...(node.tagName===tag?[node]:[]),...node.children.flatMap(child=>all(child,tag))];
const content=node=>node.textContent+node.children.map(content).join('');
const button=(label,fn)=>{const node=el('button',label);node.onclick=fn;return node;};
const body=source.slice(source.indexOf('  function showQuestion('),source.indexOf('  function renderUpdates('));
const actions=[],requests=[],dialog=el('div'),view=el('div'),drafts=new Map();let section;
let saveFails=false,allowDiscard=true,confirmations=0;
const client={state:{projects:[{name:'Demo',plan_globs:['docs/plans/*.md'],suggested_plan_folder:'docs/plans'},{name:'Other',plan_globs:['plans/*.md'],suggested_plan_folder:'plans'}]},notice:()=>{},action:async(route,data)=>{requests.push([route,data]);if(saveFails)return null;return {plan:'docs/plans/new-PLAN.md',revision:'r1'};}};
const setup=new Function('el','button','t','client','view','questionDrafts','$','detail','section','act','key','valueView','openQueuedPlan','selectTab','refreshLists','requestConfirm',
 "let planDraft=null,questionFilter='open',questionFocus=null,questionNavigation=false,questions=[],plans=[];"+body+"return {openPlanEditor,renderQuestions,showQuestion,setQuestions:value=>questions=value,setFilter:value=>{questionFilter=value;questionFocus=null;}};");
const api=setup(el,button,x=>x,client,view,drafts,()=>dialog,()=>{},()=>{section=el('section');return section;},async(route,data)=>{actions.push([route,data]);return {ok:true};},q=>({project:q.project,plan:q.plan_path}),value=>el('pre',JSON.stringify(value)),()=>{},()=>{},()=>{},async()=>{confirmations++;return allowDiscard;});
(async()=>{
 api.setQuestions([{id:1,item:'owner-check',status:'open',answer:'Previous note',answered_at:'today',project:'Demo',plan_path:'p.md',question:'Review result\n\nReport: still pending',packages:['P1','P2']}]);api.renderQuestions();
 assert(!all(section,'ARTICLE')[0].scrolled,'Background render must not move the viewport');
 api.showQuestion({id:1});api.renderQuestions();assert(all(section,'ARTICLE')[0].focused);assert(all(section,'ARTICLE')[0].scrolled,'Explicit question navigation scrolls to the target');
 api.renderQuestions();assert(!all(section,'ARTICLE')[0].scrolled,'Polling after navigation must not repeat scrolling');
 assert(content(section).includes('Previous note'));assert(content(section).includes('Report: still pending'));
 assert(!content(section).includes('Apply answer to the whole project'),'Scope is ignored for acceptance notes so must not be offered');
 const answer=all(section,'TEXTAREA')[0];answer.value='Check P2';answer.oninput();
 const packages=all(section,'INPUT').find(node=>node.attrs['aria-label']==='Rejected packages (comma separated)');packages.value='P2';packages.oninput();
 api.renderQuestions();assert.equal(all(section,'TEXTAREA')[0].value,'Check P2');assert.equal(all(section,'INPUT').find(node=>node.attrs['aria-label']==='Rejected packages (comma separated)').value,'P2');
 await all(section,'BUTTON').find(node=>node.textContent==='Reject').onclick();assert.deepEqual(actions.pop(),['owner/reject',{project:'Demo',plan:'p.md',text:'Check P2',packages:['P2']}]);
 api.setQuestions([{id:2,status:'answered',answer:'Approved option B',answered_at:'today',project:'Demo',question:'Choose'}]);api.setFilter('answered');api.renderQuestions();assert(content(section).includes('Approved option B'));assert.equal(all(section,'TEXTAREA').length,0);
 await api.openPlanEditor();assert(all(dialog,'H2').some(node=>node.id==='detail-title'),'Editor dialog must retain its accessible title');const projectSelect=all(dialog,'SELECT')[0],filename=all(dialog,'INPUT').find(node=>node.attrs['aria-label']==='Plan file');
 projectSelect.value='Other';projectSelect.onchange();assert.equal(filename.value,'plans/new-PLAN.md','Automatic path follows selected project');
 filename.value='plans/custom.md';filename.oninput();projectSelect.value='Demo';projectSelect.onchange();assert.equal(filename.value,'plans/custom.md','Manual filename must not be overwritten');
 const markdown=all(dialog,'TEXTAREA')[0];markdown.value='# New plan\n\nRequirement';markdown.oninput();
 allowDiscard=false;await api.openPlanEditor({status:'DRAFT',apiary:'Demo',plan_path:'another.md'},{text:'# Different',revision:'different'});assert.equal(all(dialog,'TEXTAREA')[0].value,'# New plan\n\nRequirement','Refusing replacement retains new draft');assert.equal(confirmations,1);allowDiscard=true;
 await all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();assert.equal(requests[0][0],'plans/create');assert.equal(requests[0][1].content,'# New plan\n\nRequirement');assert.equal(actions.length,0,'Saving must not submit');
 await all(dialog,'BUTTON').find(node=>node.textContent==='Submit plan').onclick();assert.deepEqual(actions.pop(),['plan/start',{project:'Demo',plan:'docs/plans/new-PLAN.md'}]);
 await api.openPlanEditor({status:'DRAFT',apiary:'Demo',plan_path:'p.md'},{text:'# Existing',revision:'old'});
 const imported=all(dialog,'INPUT').find(node=>node.type==='file');imported.files=[{name:'import.md',text:async()=>'# Imported\n\nRequirement'}];await imported.onchange();
 assert.equal(all(dialog,'TEXTAREA')[0].value,'# Imported\n\nRequirement');
 saveFails=true;await all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();assert.equal(all(dialog,'TEXTAREA')[0].value,'# Imported\n\nRequirement','Validation errors must preserve the imported text');
 saveFails=false;await all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();assert.equal(requests.at(-1)[0],'plans/save');assert.equal(requests.at(-1)[1].revision,'old');
 await api.openPlanEditor();assert.equal(all(dialog,'TEXTAREA')[0].value,'# Imported\n\nRequirement','Closing and reopening preserves draft');
 const previousConfirmations=confirmations;await api.openPlanEditor(null,null,true);assert.equal(all(dialog,'TEXTAREA')[0].value,'','A saved draft does not prevent creating another plan');assert.equal(confirmations,previousConfirmations,'Saved draft needs no discard confirmation');
 console.log('Question draft, history, acceptance and plan editor behavior passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
