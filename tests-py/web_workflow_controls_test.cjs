const assert=require('node:assert/strict');
const fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
class Element {
 constructor(tag,text='',cls=''){this.tag=tag;this.textContent=text;this.className=cls;this.children=[];this.dataset={};}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(k,v){this[k]=v;}
 focus(){this.focused=true;}
}
const el=(...args)=>new Element(...args);
const find=(n,p)=>[...(p(n)?[n]:[]),...n.children.flatMap(c=>find(c,p))];
const steps=['merge','check','build','deploy','release'];
const data={wayOfWorking:'local',compileCheck:'check',buildCommand:'build',deployCommand:'deploy',releaseFolder:'dist'};
for(const step of steps){data[step+'Mode']='local';if(step!=='merge')data[step+'Enabled']=true;}
let root,changes=0;
const capability={provider:'github',supported:Object.fromEntries(steps.map(s=>[s,true]))};
const view={querySelector(selector){const [,step,mode]=selector.match(/data-workflow-step="(.*?)".*data-mode="(.*?)"/);return find(root,n=>n.dataset.workflowStep===step).flatMap(n=>find(n,x=>x.dataset.mode===mode))[0];}};
const body=source.slice(source.indexOf('  function workflowFields('),source.indexOf('  async function addProject('));
const fields=new Function('el','t','changed','render','view',body+'return workflowFields;')(el,x=>x,()=>changes++,render,view);
function render(){root=el('form');fields(root,data,capability);}
const row=step=>find(root,n=>n['aria-label']===step[0].toUpperCase()+step.slice(1)&&n.role==='group')[0];
const modes=step=>find(row(step),n=>n.role==='radio');
const input=step=>find(row(step),n=>n.tag==='input'&&n.type!=='checkbox')[0];
render();assert.equal(find(root,n=>n.role==='group').length,5);assert.equal(find(root,n=>n.role==='switch').length,4);assert.equal(input('merge'),undefined);
const select=()=>find(root,n=>n.tag==='select')[0];
select().value='git';select().onchange();assert(steps.every(s=>data[s+'Mode']==='git'));assert(steps.slice(1).every(s=>input(s)===undefined));
modes('check')[0].onclick();assert.equal(data.wayOfWorking,'custom');assert(input('check').required&&!input('check').disabled);
const toggle=find(row('check'),n=>n.role==='switch')[0];toggle.checked=false;toggle.onchange();assert.equal(input('check'),undefined);assert.equal(data.compileCheck,'check');
capability.supported.build=false;render();assert(modes('build').every(n=>n.disabled));assert.equal(data.buildMode,'git');assert.equal(data.wayOfWorking,'custom');
capability.provider=null;render();assert(select().children.slice(1).every(n=>n.disabled));
capability.provider='github';capability.supported.build=true;data.checkEnabled=true;render();
const local=modes('check')[0];assert.equal(local.tabIndex,0);assert.equal(modes('check')[1].tabIndex,-1);
let prevented=false;local.onkeydown({key:'ArrowRight',preventDefault(){prevented=true;}});
assert(prevented);assert.equal(data.checkMode,'git');assert(modes('check')[1].focused);assert.equal(data.compileCheck,'check');
modes('check')[1].onkeydown({key:'Home',preventDefault(){}});assert.equal(data.checkMode,'local');assert(modes('check')[0].focused);
assert(changes>=5);console.log('Workflow modes, switches, fields, unsupported steps and keyboard navigation passed');
