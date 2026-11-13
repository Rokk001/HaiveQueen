const assert = require('node:assert/strict');
const {Client, planActions, setValue, safeLinks, toolsFromArgs, withTools} = require('../haivequeen/web-client.js');

(async () => {
  const calls = [], notices = [];
  let allow = false;
  const client = new Client({csrf:'token', confirm:()=>allow, notice:x=>notices.push(x),
    fetch:async (url, options)=>{calls.push([url, options]);return {ok:true,json:async()=>({ok:true})}}});
  await client.action('force-stop', {}, 'Everything stops. Unfinished work is done again.');
  assert.equal(calls.length, 0, 'declining must send nothing');
  allow = true;
  await client.action('force-stop', {}, 'Everything stops. Unfinished work is done again.');
  assert.equal(JSON.parse(calls[0][1].body).confirmed, true);
  assert.equal(calls[0][1].headers['X-CSRF-Token'], 'token');
  assert(notices.includes('Working…'));
  let completeAction;
  client.fetch=()=>new Promise(resolve=>{completeAction=()=>resolve({ok:true,json:async()=>({ok:true})})});
  const pending=client.action('start');
  assert.equal(await client.action('start'),null,'A redraw must not allow the same action twice');
  completeAction();await pending;
  assert(planActions({status:'PAUSED',stopped:true}).includes('resume'));
  assert(!planActions({status:'DONE'}).includes('resume'));
  assert(!planActions({status:'RUNNING'}).includes('resume'));
  const data={classes:{medium:{model:'old'}}, slots:2};
  setValue(data,['classes','medium','model'],'new');
  assert.equal(data.classes.medium.model,'new');
  assert.deepEqual(safeLinks(['javascript:alert(1)','https://example.invalid/device','http://example.invalid']),
    ['https://example.invalid/device','http://example.invalid']);
  assert.deepEqual(toolsFromArgs(['--allowedTools','Bash(cmd(a,b)),Read']),['Bash(cmd(a,b))','Read']);
  assert.deepEqual(withTools(['run','--allowedTools','Read','--json'],['Read','Write']),['run','--allowedTools','Read,Write','--json']);
  let finish;
  client.fetch = () => new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({hives:[]})})});
  const first = client.refresh();
  const second = client.refresh();
  assert.equal(client.refreshWanted, true);
  finish();
  await first;
  await second;
  // A queued refresh starts after the pending read rather than blocking actions.
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(client.loading, true);
  finish();
  await new Promise(resolve=>setImmediate(resolve));
  client.fetch=async()=>({ok:false,status:400,json:async()=>({error:'Settings changed since loading; reload before saving'})});
  await assert.rejects(client.request('settings/save',{revision:'old',data:{}}),/changed since loading/);
  console.log('Browser client regressions passed');
})().catch(error=>{console.error(error);process.exitCode=1});
