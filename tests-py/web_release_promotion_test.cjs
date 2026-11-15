const assert=require('node:assert/strict');
const {startReleasePromotion,releaseJobStatus}=require('../haivequeen/web-client.js');
(async()=>{
 let resolve,requests=0;const states=[];
 const client={confirm:async()=>true,action:()=>{requests++;return new Promise(r=>resolve=r);}};
 const pending=startReleasePromotion(client,'demo',{tag:'v1.2.3-alpha.1',full_tag:'v1.2.3'},s=>states.push(s));
 await Promise.resolve();assert.equal(states[0].running,true);assert.equal(requests,1);
 assert.match(releaseJobStatus(states[0]).text,/Starting/);
 resolve({id:'job',key:'release-promotion:demo',running:true});await pending;
 assert.equal(states.at(-1).id,'job');
 const errorStates=[];
 await startReleasePromotion({confirm:async()=>true,action:async()=>{throw Error('scope denied');}},'demo',{tag:'pre',full_tag:'full'},s=>errorStates.push(s));
 assert.equal(releaseJobStatus(errorStates.at(-1)).error,true);assert.match(releaseJobStatus(errorStates.at(-1)).text,/scope denied/);
 assert.equal(releaseJobStatus({running:false,result:{message:'Workflow requested'}}).text,'Workflow requested');
 let touched=false;await startReleasePromotion({confirm:async()=>false,action:()=>{touched=true;}},'demo',{full_tag:'full'},()=>{touched=true;});assert.equal(touched,false);
 const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
 const render=source.slice(source.indexOf('  function renderReleases()'),source.indexOf('  function renderDashboard()'));
 for(const job of [{running:true,progress:'Publishing now'}, {running:false,error:'Permission denied'}]){
   const nodes=[];
   const el=(tag,text)=>{const n={text,children:[],attrs:{},classList:{add(){}},append(...v){this.children.push(...v);},setAttribute(k,v){this.attrs[k]=v;}};nodes.push(n);return n;};
   new Function('section','el','t','releasePromotions','releaseProject','projectSelect','button','loadReleases','releaseError','releaseData','releaseJobStatus',render+'renderReleases();')(()=>el('section'),el,x=>x,new Map([['demo',job]]),'demo',()=>el('select'),()=>el('button'),()=>{},'',null,releaseJobStatus);
   assert(nodes.some(n=>n.text===(job.error||job.progress)&&n.attrs.role===(job.error?'alert':'status')),'Progress/error must be in the Releases panel');
 }
 console.log('Immediate promotion feedback, errors, completion and cancellation passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
