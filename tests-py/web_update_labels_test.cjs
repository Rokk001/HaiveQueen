const assert=require('node:assert/strict');
const fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
const helpers=source.slice(source.indexOf('  function displayVersion('),source.indexOf('  function renderState(state)'));
const renderBody=source.slice(source.indexOf('  function renderUpdates()'),source.indexOf('  async function loadEditor('));
const headerBody=source.slice(source.indexOf('  function renderStateNow(state) {')+'  function renderStateNow(state) {'.length,source.indexOf('    renderControls();renderUpdates();'));
function node(text){return {textContent:text,children:[],classList:{add(){}},append(...items){this.children.push(...items)},replaceChildren(){this.children=[]}};}
async function check(version,target){
 const box=node(''),header=node(''),calls=[];
 const state={version},updateState={available:{version:target}};
 const $=id=>id==='version'?header:box;
 const act=async(...args)=>calls.push(args);
 new Function('$','t','state',helpers+headerBody)($,x=>x,state);
 new Function('$','t','updateState','el','button','act','bootstrap','client',helpers+renderBody+'renderUpdates();')($,x=>x,updateState,(_,text)=>node(text),(text,click)=>Object.assign(node(text),{click}),act,{updateConfirm:'Install %s?'},{});
 assert.equal(header.textContent,'Running v0.2.44');
 assert.deepEqual(box.children.map(x=>x.textContent),['Update to v0.2.45','Check now']);
 await box.children[0].click();
 assert.equal(calls[0][0],'updates/install');
 assert.equal(calls[0][1].version,target,'Preserve the original API version');
}
function checkInstalling(){
 const box=node('');
 const updateState={available:{version:'v0.2.48'},installing:{version:'v0.2.48',after_row:12,since:'2026-01-01T12:00:00'}};
 new Function('$','t','updateState','el','button','act','bootstrap','client',helpers+renderBody+'renderUpdates();')(()=>box,x=>x,updateState,(_,text)=>node(text),(text,click)=>Object.assign(node(text),{click}),()=>{throw Error('Rendering must not install');},{},{});
 assert.deepEqual(box.children.map(x=>x.textContent),['installing version v0.2.48 – waiting for running agents','Check now']);
 assert.equal(box.children[1].disabled,true,'Checking stays disabled while installing');
}
(async()=>{await check('0.2.44','0.2.45');await check('v0.2.44','v0.2.45');checkInstalling();console.log('Update labels passed');})().catch(e=>{console.error(e);process.exitCode=1;});
