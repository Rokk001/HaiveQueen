const assert=require('node:assert/strict');
const fs=require('node:fs');
const {copyOutput}=require('../haivequeen/web-client.js');
const source=fs.readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
class Element {
 constructor(tag,text='',cls=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.className=cls;this.classList={add:value=>this.className+=' '+value};}
 append(...children){this.children.push(...children);}
 setAttribute(key,value){this[key]=value;}
}
const el=(...args)=>new Element(...args);
const all=(node,tag)=>[...(node.tagName===tag?[node]:[]),...node.children.flatMap(child=>all(child,tag))];
const text=node=>node.textContent+node.children.map(text).join('');
(async()=>{
 const copied=[];const target={tagName:'PRE',textContent:'old',innerText:'first\nsecond'};
 await copyOutput(target,{}, {clipboard:{writeText:async value=>copied.push(value)}});
 target.innerText='new live line';await copyOutput(target,{}, {clipboard:{writeText:async value=>copied.push(value)}});
 await copyOutput({tagName:'TEXTAREA',value:'edited draft',textContent:'initial'}, {},{clipboard:{writeText:async value=>copied.push(value)}});
 assert.deepEqual(copied,['first\nsecond','new live line','edited draft']);
 let removed=false,focused=false,restored=false,modalUsed=false;
 const temporary={style:{},setAttribute(){},select(){assert.equal(this.value,'log');},remove(){removed=true;}};
 const document={activeElement:{selectionStart:2,selectionEnd:4,focus(options){focused=options.preventScroll;},setSelectionRange(a,b){restored=a===2&&b===4;}},createElement:()=>temporary,execCommand:name=>name==='copy',body:{append(){throw Error('Modal fallback must not use inert body');}}};
 const modal={append(){modalUsed=true;}};
 await copyOutput({tagName:'PRE',textContent:'log',closest:()=>modal},document,{clipboard:{writeText:async()=>{throw Error('Denied');}}});
 assert(removed&&focused&&restored&&modalUsed);
 document.execCommand=()=>false;removed=false;
 await assert.rejects(copyOutput({tagName:'PRE',textContent:'log',closest:()=>modal},document,{}),/Copy failed/);assert(removed);
 // Render actual release implementation with a minimal DOM, including empty/error states.
 const body=source.slice(source.indexOf('  async function loadReleases()'),source.indexOf('  function renderDashboard()'));
 const boxes=[],actions=[];
 const api=new Function('el','section','projectSelect','button','t','client','jobs','renderJob',`
 let releaseProject='demo',releaseData=null,releaseError='',releaseRequest=0,tab='Releases';
 function render(){renderReleases();}
 ${body}
 return {loadReleases,renderReleases,setData:value=>releaseData=value};`)(el,title=>{const box=el('section',title);boxes.push(box);return box;},()=>el('select'),(title,action)=>{const b=el('button',title);b.onclick=action;return b;},x=>x,{request:async()=>{throw Error('Service unavailable');},action:async(name,data)=>{actions.push([name,data]);return null;}},new Map(),()=>{});
 await api.loadReleases();assert(text(boxes.at(-1)).includes('Could not load releases'));assert(text(boxes.at(-1)).includes('Service unavailable'));
 // A fresh view for release success states.
 const success=new Function('el','section','projectSelect','button','t','client','jobs','renderJob',`
 let releaseProject='demo',releaseData=null,releaseError='',releaseRequest=0,tab='Releases';function render(){renderReleases();}${body}
 return {loadReleases,setData:value=>releaseData=value,renderReleases};`)(el,title=>{const box=el('section',title);boxes.push(box);return box;},()=>el('select'),(title,action)=>{const b=el('button',title);b.onclick=action;return b;},x=>x,{request:async()=>({releases:[]}),action:async(name,data)=>{actions.push([name,data]);return null;}},new Map(),()=>{});
 await success.loadReleases();assert(text(boxes.at(-1)).includes('No releases yet'));
 success.setData({releases:[{version:'v2-dev',tag:'v2-dev',full_tag:'v2',prerelease:true,commit:'abc',date:'2026-01-01'},{version:'v1',prerelease:false}]});success.renderReleases();
 assert.equal(all(boxes.at(-1),'ARTICLE').length,2);assert(text(boxes.at(-1)).includes('Stable'));assert(text(boxes.at(-1)).includes('Prerelease'));
 const promote=all(boxes.at(-1),'BUTTON').filter(b=>b.textContent==='Publish as full release');assert.equal(promote.length,1);await promote[0].onclick();assert.deepEqual(actions[0],['releases/promote',{project:'demo',tag:'v2-dev',confirmed:true}]);
 // Exercise the browser helper when a confirmation arrives later than 20 ms.
 const testSource=fs.readFileSync(require.resolve('./web_browser_test.js'),'utf8');
 const clickBody=testSource.slice(testSource.indexOf('  const click ='),testSource.indexOf('  const field ='));
 let open=false,confirmed=false;const button={disabled:false,isConnected:true,click(){this.disabled=true;setTimeout(()=>open=true,60);}};
 const dialog={get open(){return open;},querySelectorAll:()=>[{}, {click(){confirmed=true;open=false;button.disabled=false;}}]};
 const click=new Function('buttons','assert','wait','document',`let confirmResult=true,warning='';${clickBody}return click;`)(()=>[button],assert,async predicate=>{for(let n=0;n<100;n++){if(predicate())return;await new Promise(r=>setTimeout(r,5));}throw Error('Timeout');},{getElementById:id=>id==='confirmation-dialog'?dialog:{textContent:'Accept?'}});
 await click('Accept');assert(confirmed);
 console.log('CI/CD release states, promotion, live clipboard, HTTP fallback and delayed confirmation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
