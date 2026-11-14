const assert=require('node:assert/strict');
const fs=require('node:fs');
const {credentialStatus,loginStatus}=require('../haivequeen/web-client.js');
assert.equal(credentialStatus({method:'existing access'}).tone,'ok');
assert.equal(credentialStatus({method:'token'}).tone,'ok');
assert.equal(credentialStatus({method:''}).tone,'neutral');
assert.equal(credentialStatus({method:'token',status_error:'expired'}).tone,'error');
assert.equal(loginStatus({logged_in:true}).tone,'ok');
assert.equal(loginStatus({logged_in:false}).tone,'warn');
assert.equal(loginStatus(undefined).tone,'neutral');
const source=fs.readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
const body=source.slice(source.indexOf('  function fitDashboard(){'),source.indexOf("  window.addEventListener('resize',fitDashboard)"));
for(const width of [320,400,768,1100]){
 const plans={style:{maxHeight:'250px'},querySelectorAll(){throw Error('Mobile must not allocate a desktop scroll height');}},hive={style:{maxHeight:'500px'}};
 const primary={style:{height:'800px'},querySelector:s=>s.includes('ACTIVE PLANS')?plans:hive};
 const fit=new Function('view','window',`const tab='Dashboard';${body}return fitDashboard;`)({querySelector:()=>primary},{innerWidth:width});
 fit();assert.equal(primary.style.height,'');assert.equal(plans.style.maxHeight,'');assert.equal(hive.style.maxHeight,'');
}
console.log('Credential badges and mobile height reset passed');
