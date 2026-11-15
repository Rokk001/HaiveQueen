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
const setup=new Function('el','button','t','client','view','questionDrafts','$','detail','section','act','key','valueView','openQueuedPlan','selectTab','refreshLists','requestConfirm','table',
 "const planFilters={search:'',project:'',status:''};let questionsLoaded=true;let planDraft=null,questionFilter='open',questionFocus=null,questionNavigation=false,selectedThread=null,conversationMobile=false,questions=[],plans=[];"+body+"return {openPlanEditor,renderPlans,renderQuestions,showQuestion,planMessageButton,getThread:()=>selectedThread,setPlans:value=>plans=value,setQuestions:value=>questions=value,setFilter:value=>{questionFilter=value;questionFocus=null;selectedThread=null;}};");
const api=setup(el,button,x=>x,client,view,drafts,()=>dialog,()=>{},()=>{section=el('section');return section;},async(route,data)=>{actions.push([route,data]);return {ok:true};},q=>({project:q.project,plan:q.plan_path}),value=>el('pre',JSON.stringify(value)),()=>{},()=>{},()=>{},async()=>{confirmations++;return allowDiscard;},()=>el('table'));
(async()=>{
 api.setQuestions([{id:1,item:'owner-check',status:'open',answer:'Previous note',answered_at:'today',project:'Demo',plan_path:'p.md',question:'Review result\n\nReport: still pending',packages:['P1','P2']}]);api.renderQuestions();
 assert(!all(section,'HEADER')[0].scrolled,'Background render must not move the viewport');
 api.showQuestion({id:1});api.renderQuestions();assert(all(section,'H2').some(node=>node.focused));assert(all(section,'HEADER')[0].scrolled,'Explicit question navigation scrolls to the target');
 api.renderQuestions();assert(!all(section,'HEADER')[0].scrolled,'Polling after navigation must not repeat scrolling');
 assert.equal(all(section,'SELECT').length,0,'One pending question needs no selector');
 assert(content(section).includes('Previous note'));assert(content(section).includes('Report: still pending'));
 assert(!content(section).includes('Apply answer to the whole project'),'Scope is ignored for acceptance notes so must not be offered');
 const answer=all(section,'TEXTAREA')[0];answer.value='Check P2';answer.oninput();
 const packages=all(section,'INPUT').find(node=>node.attrs['aria-label']==='Rejected packages (comma separated)');packages.value='P2';packages.oninput();
 api.renderQuestions();assert.equal(all(section,'TEXTAREA')[0].value,'Check P2');assert.equal(all(section,'INPUT').find(node=>node.attrs['aria-label']==='Rejected packages (comma separated)').value,'P2');
 await all(section,'BUTTON').find(node=>node.textContent==='Request changes').onclick();assert.deepEqual(actions.pop(),['owner/reject',{project:'Demo',plan:'p.md',text:'Check P2',packages:['P2']}]);
 api.setQuestions([{id:2,status:'answered',answer:'Approved option B',answered_at:'today',project:'Demo',question:'Choose'}]);api.setFilter('answered');api.renderQuestions();assert(content(section).includes('Approved option B'));assert.equal(all(section,'TEXTAREA').length,0);
 await api.openPlanEditor();assert(all(dialog,'H2').some(node=>node.id==='detail-title'),'Editor dialog must retain its accessible title');const projectSelect=all(dialog,'SELECT')[0],filename=all(dialog,'INPUT').find(node=>node.attrs['aria-label']==='Plan file');
 projectSelect.value='Other';projectSelect.onchange();assert.equal(filename.value,'plans/new-PLAN.md','Automatic path follows selected project');
 filename.value='plans/custom.md';filename.oninput();projectSelect.value='Demo';projectSelect.onchange();assert.equal(filename.value,'plans/custom.md','Manual filename must not be overwritten');
 const markdown=all(dialog,'TEXTAREA')[0];assert.match(markdown.value,/^# /,'Fresh plans must offer a Markdown title template');const initialTemplate=markdown.value;markdown.value='# New plan\n\nRequirement';markdown.oninput();
 allowDiscard=false;await api.openPlanEditor({status:'DRAFT',apiary:'Demo',plan_path:'another.md'},{text:'# Different',revision:'different'});assert.equal(all(dialog,'TEXTAREA')[0].value,'# New plan\n\nRequirement','Refusing replacement retains new draft');assert.equal(confirmations,1);allowDiscard=true;
 await all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();assert.equal(requests[0][0],'plans/create');assert.equal(requests[0][1].content,'# New plan\n\nRequirement');assert.equal(actions.length,0,'Saving must not submit');
 markdown.value+=' edited';markdown.oninput();assert(all(dialog,'P').some(node=>node.textContent==='Unsaved changes'),'Editing a saved draft must replace saved status');
 await all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();
 await all(dialog,'BUTTON').find(node=>node.textContent==='Submit plan').onclick();assert.deepEqual(actions.pop(),['plan/start',{project:'Demo',plan:'docs/plans/new-PLAN.md'}]);
 await api.openPlanEditor({status:'DRAFT',apiary:'Demo',plan_path:'p.md'},{text:'# Existing',revision:'old'});
 assert.equal(all(dialog,'TEXTAREA')[0].value,'# Existing','Opening an existing draft must not replace its content with the template');
 const imported=all(dialog,'INPUT').find(node=>node.type==='file');imported.files=[{name:'import.md',text:async()=>'# Imported\n\nRequirement'}];await imported.onchange();
 assert.equal(all(dialog,'TEXTAREA')[0].value,'# Imported\n\nRequirement');
 saveFails=true;await all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();assert.equal(all(dialog,'TEXTAREA')[0].value,'# Imported\n\nRequirement','Validation errors must preserve the imported text');
 assert.equal(all(dialog,'SELECT')[0].disabled,true,'An existing draft stays project-locked after failed save');
 saveFails=false;await all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();assert.equal(requests.at(-1)[0],'plans/save');assert.equal(requests.at(-1)[1].revision,'old');
 await api.openPlanEditor();assert.equal(all(dialog,'TEXTAREA')[0].value,'# Imported\n\nRequirement','Closing and reopening preserves draft');
 const previousConfirmations=confirmations;await api.openPlanEditor(null,null,true);assert.equal(all(dialog,'TEXTAREA')[0].value,initialTemplate,'Fresh creation offers template without changing saved draft');assert.equal(confirmations,previousConfirmations,'Saved draft needs no discard confirmation');
 api.setPlans([{apiary:'Demo',plan_path:'multi.md',name:'Readable plan title',status:'WAITING'}]);
 api.setQuestions([{id:10,status:'open',project:'Demo',plan_path:'multi.md',question:'First pending'},{id:11,status:'open',project:'Demo',plan_path:'multi.md',question:'Second pending'},{id:13,status:'open',project:'Second complete project name',plan_path:'second.md',question:'Second project needs answer'},{id:12,status:'answered',project:'Other',plan_path:'done.md',question:'Finished question',answer:'Recorded response'}]);
 api.showQuestion({id:10,project:'Demo',plan_path:'multi.md'});api.renderQuestions();
 assert.equal(all(section,'TEXTAREA').length,1,'Messenger must mount only one actionable composer');
 assert(content(section).includes('Readable plan title'));const chooser=all(section,'SELECT')[0];assert.equal(chooser.children.length,2);chooser.value='11';chooser.onchange();assert.equal(all(section,'TEXTAREA').length,1);assert(content(section).includes('Second pending'));
 const selectedDraft=all(section,'TEXTAREA')[0];selectedDraft.value='Preserved second answer';selectedDraft.oninput();
 api.planMessageButton({apiary:'Demo',plan_path:'multi.md',name:'Readable plan title'}).onclick();api.renderQuestions();assert.equal(api.getThread(),JSON.stringify(['Demo','multi.md']),'Plan messages route to same real conversation');
 const chooserAgain=all(section,'SELECT')[0];chooserAgain.value='11';chooserAgain.onchange();assert.equal(all(section,'TEXTAREA')[0].value,'Preserved second answer');
 all(section,'BUTTON').find(node=>node.textContent==='Completed').onclick();assert(!content(section).includes('First pending'),'Completed filter must not retain an invisible pending conversation');assert(content(section).includes('Recorded response'));assert.equal(all(section,'TEXTAREA').length,0);
 all(section,'BUTTON').find(node=>node.textContent==='Needs you').onclick();
 all(section,'BUTTON').filter(node=>node.className==='messenger-thread')[1].onclick();api.renderQuestions();
 assert.equal(all(section,'BUTTON').find(node=>node.textContent==='Needs you').attrs['aria-pressed'],'true','Selecting an inbox thread preserves Needs you filter');
 assert(!content(section).includes('Finished question'),'Sidebar selection must not reveal unrelated completed conversations');
 assert.equal(all(section,'BUTTON').find(node=>node.className==='messenger-back').textContent,'Back to Needs you','Mobile back names the actual active filter');
 const chatHeader=all(section,'HEADER')[0];assert(content(chatHeader).includes('ProjectSecond complete project name'));assert(content(chatHeader).includes('Plansecond'));assert(content(chatHeader).includes('View: Needs you'));
 api.renderQuestions();assert.equal(all(section,'BUTTON').find(node=>node.className==='messenger-back').textContent,'Back to Needs you','Polling retains visible filter context');
 all(section,'BUTTON').find(node=>node.className==='messenger-back').onclick();assert.equal(all(section,'BUTTON').find(node=>node.textContent==='Needs you').attrs['aria-pressed'],'true');assert.equal(section.dataset.mobileChat,'false','Mobile back returns to the conversation list');
 await api.openPlanEditor(null,null,true);
 const racingProject=all(dialog,'SELECT')[0],racingFile=all(dialog,'INPUT').find(node=>node.attrs['aria-label']==='Plan file'),racingContent=all(dialog,'TEXTAREA')[0];
 const originalAction=client.action;let finishCreate,createSnapshot;
 client.action=(route,data)=>{assert.equal(route,'plans/create');createSnapshot=data;return new Promise(resolve=>finishCreate=resolve);};
 const pendingSave=all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();
 assert.equal(racingProject.disabled,true,'Project must be locked while create is in flight');assert.equal(racingFile.disabled,true,'Filename must be locked while create is in flight');
 // A user change only dispatches from an enabled control. Attempt to switch project/path.
 if(!racingProject.disabled){racingProject.value='Other';racingProject.onchange();}
 if(!racingFile.disabled){racingFile.value='other.md';racingFile.oninput();}
 racingContent.value='# Newer unsaved content';racingContent.oninput();
 finishCreate({plan:createSnapshot.filename,revision:'created'});await pendingSave;
 assert.equal(racingProject.value,createSnapshot.project);assert.equal(racingFile.value,createSnapshot.filename);
 assert.equal(racingContent.value,'# Newer unsaved content');assert.equal(all(dialog,'BUTTON').find(node=>node.textContent==='Submit plan').disabled,true,'Edits during create must remain unsaved');
 assert(content(dialog).includes('Saved. Newer edits are still unsaved.'));
 client.action=originalAction;await all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();assert.equal(requests.at(-1)[1].project,createSnapshot.project);assert.equal(requests.at(-1)[1].revision,'created');
 await api.openPlanEditor(null,null,true);const failingProject=all(dialog,'SELECT')[0],failingFile=all(dialog,'INPUT').find(node=>node.attrs['aria-label']==='Plan file');
 client.action=()=>new Promise(resolve=>finishCreate=resolve);const failingSave=all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();assert(failingProject.disabled&&failingFile.disabled);finishCreate(null);await failingSave;assert.equal(failingProject.disabled,false);assert.equal(failingFile.disabled,false,'Failed creation must unlock its original controls');client.action=originalAction;
 await api.openPlanEditor(null,null,true);
 let reopenedCreateCalls=0;client.action=(route,data)=>{reopenedCreateCalls++;createSnapshot=data;return new Promise(resolve=>finishCreate=resolve);};
 const closedSave=all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();dialog.close();await api.openPlanEditor();
 const reopenedProject=all(dialog,'SELECT')[0],reopenedFile=all(dialog,'INPUT').find(node=>node.attrs['aria-label']==='Plan file');
 assert(reopenedProject.disabled&&reopenedFile.disabled,'Reopened draft must retain its in-flight target locks');assert.equal(all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').disabled,true,'Reopening must not allow duplicate saves');
 const duplicateSave=all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();assert.equal(reopenedCreateCalls,1,'Draft state rejects a duplicate save even if its callback is invoked');await duplicateSave;
 if(!reopenedProject.disabled){reopenedProject.value='Other';reopenedProject.onchange();}
 const reopenedContent=all(dialog,'TEXTAREA')[0];reopenedContent.value='# Edited after reopening';reopenedContent.oninput();finishCreate({plan:createSnapshot.filename,revision:'reopened'});await closedSave;
 assert.equal(reopenedProject.value,createSnapshot.project);assert(reopenedProject.disabled&&reopenedFile.disabled,'Completed creation locks the reopened controls');assert(content(dialog).includes('Saved. Newer edits are still unsaved.'));assert.equal(all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').disabled,false);
 client.action=originalAction;await all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();assert.equal(requests.at(-1)[1].revision,'reopened');assert.equal(requests.at(-1)[1].content,'# Edited after reopening');
 await api.openPlanEditor(null,null,true);client.action=()=>new Promise(resolve=>finishCreate=resolve);const reopenedFailure=all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').onclick();dialog.close();await api.openPlanEditor();const failedReopenedProject=all(dialog,'SELECT')[0],failedReopenedFile=all(dialog,'INPUT').find(node=>node.attrs['aria-label']==='Plan file');assert(failedReopenedProject.disabled&&failedReopenedFile.disabled);finishCreate(null);await reopenedFailure;assert.equal(failedReopenedProject.disabled,false);assert.equal(failedReopenedFile.disabled,false,'Failure unlocks the currently reopened editor');assert.equal(all(dialog,'BUTTON').find(node=>node.textContent==='Save draft').disabled,false);client.action=originalAction;
 const loadedState=client.state;client.state=undefined;const priorDialog=dialog.children;
 api.renderPlans();assert.equal(all(section,'BUTTON').find(node=>node.textContent==='New plan').disabled,true,'New plan waits for startup project data');
 await api.openPlanEditor(null,null,true);assert.equal(dialog.children,priorDialog,'Defensive callback does not open an empty-project editor');
 client.state=loadedState;api.renderPlans();assert.equal(all(section,'BUTTON').find(node=>node.textContent==='New plan').disabled,false,'Loaded project state enables New plan');
 await api.openPlanEditor(null,null,true);assert.equal(all(dialog,'SELECT')[0].value,'Demo','Startup opens with a loaded registered project');
 const readyDialog=dialog.children;client.state={projects:[]};let projectNotice='';client.notice=message=>projectNotice=message;await api.openPlanEditor(null,null,true);assert.equal(dialog.children,readyDialog,'No registered projects must not open an empty-project editor');assert(projectNotice.includes('project'));client.state=loadedState;
 const stateHandler=source.slice(source.indexOf('  function renderStateNow('),source.indexOf('  function renderControls('));let plansRedraw=0;
 const versionHelper=source.match(/  function displayVersion\(version\) \{[^\n]+\}/)?.[0]||'';
 new Function('tab','$','renderControls','renderUpdates','render','overlay','renderDetail','el','t',versionHelper+stateHandler+'renderStateNow({});')('Plans',()=>({querySelector:()=>({querySelector:()=>null,setAttribute(){}})}),()=>{},()=>{},()=>plansRedraw++,null,()=>{},el,x=>x);
 assert.equal(plansRedraw,1,'Arrival of startup state redraws Plans to unlock New plan');
 console.log('Question draft, history, acceptance and plan editor behavior passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
