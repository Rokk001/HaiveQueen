const assert=require('node:assert/strict');
const {planProcessGroups}=require('../haivequeen/web-client.js');
const plans=[{apiary:'a',plan_path:'same.md',name:'First',orch:{chain:[['Check','running']]}},{apiary:'b',plan_path:'same.md',name:'Second'}];
const processes=[{apiary:'a',plan_path:'same.md',kind:'orch',name:'HQ'},{project:'a',plan_path:'same.md',kind:'queen',agent:'q',name:'Queen'},{project:'a',plan_path:'same.md',kind:'bee',name:'\u2013'},{project:'b',plan_path:'same.md',kind:'bee',agent:'b',name:'Bee'}];
const groups=planProcessGroups(plans,processes);
assert.equal(groups.length,2);assert.equal(groups[0].processes.length,2);assert.equal(groups[1].processes.length,1);
assert.equal(groups[0].summary.orch,null);assert.deepEqual(plans[0].orch.chain,[['Check','running']]);
assert.equal(planProcessGroups(plans,[])[0].summary.orch,plans[0].orch);
console.log('Plan/process grouping passed');
