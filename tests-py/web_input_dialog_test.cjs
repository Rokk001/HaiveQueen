const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('haivequeen/web-client.js','utf8');
assert(!/\bprompt\(/.test(source),'Settings must not open native browser prompts');
let dialog;
const el=(tag,text)=>({tag,text,children:[],value:'',open:false,events:{},append(...nodes){this.children.push(...nodes);},setAttribute(){},addEventListener(name,fn){this.events[name]=fn;},showModal(){this.open=true;},close(){this.open=false;},remove(){this.removed=true;},focus(){this.focused=true;}});
const context={el,t:x=>x,document:{body:{append(node){dialog=node;}}}};vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('  function requestInput('),source.indexOf('  function animateHeader(')),context);
(async()=>{
 const first=context.requestInput('Capability name');
 const form=dialog.children[0],input=form.children[1];input.value='image-generation';
 form.onsubmit({preventDefault(){}});assert.equal(await first,'image-generation');assert(dialog.removed);
 const second=context.requestInput('Setting name');dialog.events.cancel({preventDefault(){}});assert.equal(await second,null);
 const third=context.requestInput('Value');dialog.children[0].children[2].children[0].onclick();assert.equal(await third,null);
 console.log('Internal input dialogs passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
