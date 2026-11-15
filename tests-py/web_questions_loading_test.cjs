const assert=require('node:assert/strict');
const fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
const {QuestionDetails}=require('../haivequeen/web-client.js');
async function test(){
 let calls=0,resolve;
 const cache=new QuestionDetails(()=>{calls++;return new Promise(r=>resolve=r);});
 const q={id:1,question:'One',details_pending:true};
 const a=cache.load(q),b=cache.load(q);assert.equal(calls,1);
 resolve({id:1,evidence:{requirements:'real evidence'},packages:['A']});
 await Promise.all([a,b]);assert.equal(cache.get(q).value.packages[0],'A');
 await cache.load(q);assert.equal(calls,1);
 const changed={...q,answer:'new answer'};const next=cache.load(changed);assert.equal(calls,2);resolve({id:1,answer:'new answer'});await next;
 const failed=new QuestionDetails(()=>Promise.reject(Error('offline')));await failed.load(q).catch(()=>{});assert.equal(failed.get(q).error.message,'offline');
 const nodes=[];const el=(tag,text)=>{const n={text,dataset:{},append(){},setAttribute(){}};nodes.push(n);return n;};
 const section=()=>el('section');
 const body=source.slice(source.indexOf('  function renderQuestions()'),source.indexOf('  function renderQuestionsPage()'));
 new Function('section','el','t','questionsLoaded','conversationMobile',body+'renderQuestions();')(section,el,x=>x,false,false);
 assert(nodes.some(n=>n.text==='Loading messages…'));
 assert(!nodes.some(n=>String(n.text).includes('0 pending')||String(n.text).includes('No questions need')));
 console.log('Deferred detail caching and truthful initial loading passed');
}
test().catch(e=>{console.error(e);process.exitCode=1;});
