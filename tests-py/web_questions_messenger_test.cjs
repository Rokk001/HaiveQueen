const assert=require('node:assert/strict');
const source=require('node:fs').readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
assert(source.includes('function questionThreads('),'Questions must group real questions into project/plan conversations');
assert(source.includes("key==='messages'"),'Plans must have a visible messages column');
assert(source.includes("className='questions-messenger'"),'Questions must mount the actual messenger layout');
console.log('Messenger integration contract passed');

const helperSource=source.slice(source.indexOf('  function questionThreads('),source.indexOf('  function questionCard('));
const {questionThreads,questionTimeline}=new Function(helperSource+'return {questionThreads,questionTimeline};')();
const records=[
 {id:1,project:'A',plan_path:'first.md',status:'open',question:'First',created_at:'2026-01-01'},
 {id:2,project:'A',plan_path:'first.md',status:'open',question:'Second',created_at:'2026-01-02',answer:'Current note',answered_at:'2026-01-04'},
 {id:3,project:'B',plan_path:'first.md',status:'answered',question:'Other project',created_at:'2026-01-02',answer:'Done',answered_at:'2026-01-03'},
 {id:4,project:'A',plan_path:'first.md',status:'triage',question:'Under triage',created_at:'2026-01-03'}
];
const threads=questionThreads(records,[{apiary:'A',plan_path:'first.md',name:'Friendly title',status:'WAITING'}]);
assert.equal(threads.length,2,'Same path in different projects is a different conversation');
assert.equal(threads[0].name,'Friendly title');assert.equal(threads[0].pending.length,2,'Only open records have an actionable composer');
assert.deepEqual(questionTimeline(records).map(event=>[event.question.id,event.type]),[[1,'question'],[2,'question'],[3,'question'],[3,'answer'],[4,'question'],[2,'answer']]);
assert.equal(questionTimeline(records).filter(event=>event.question.id===2&&event.type==='answer').length,1,'Only latest recorded note exists; do not invent old note messages');
console.log('Real conversation grouping and chronological records passed');
