const assert=require('node:assert/strict');
const {Client}=require('../haivequeen/web-client.js');
(async()=>{
  const notices=[],states=[];
  let error='New port unavailable. Previous web settings restored.';
  const client=new Client({notice:(...args)=>notices.push(args),onState:value=>states.push(value),
    fetch:async()=>({ok:true,json:async()=>({web_listener_error:error})})});
  await client.refresh();await client.refresh();
  assert.deepEqual(notices,[[error,true]],'show rollback errors once without poll spam');
  assert.equal(states.length,2,'dashboard polling continues after rollback');
  error='';await client.refresh();
  error='Another address unavailable';await client.refresh();
  assert.deepEqual(notices[1],[error,true]);
  console.log('Listener rollback notification passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
