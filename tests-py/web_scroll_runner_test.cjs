const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {createRequire}=require('node:module');
const file=path.join(__dirname,'web_scroll_layout_test.cjs');
const localRequire=createRequire(file),profiles=new Set();
const fakeFs={...fs,existsSync:()=>true,mkdtempSync:()=>path.join(require('node:os').tmpdir(),'hq-scroll-test-probe'),writeFileSync:()=>{},rmSync:()=>{}};
function load(name){
 if(name==='node:fs')return fakeFs;
 if(name==='node:child_process')return {spawnSync(_browser,args){
  const profile=args.find(arg=>arg.startsWith('--user-data-dir='));
  assert(!profiles.has(profile),'Each viewport needs a fresh Chromium profile to avoid forwarding to an exiting browser');
  profiles.add(profile);
  const url=new URL(args.at(-1));assert.equal(url.protocol,'file:');
  const result={pipelineFits:true,listFits:true,shortFits:true,longScrolls:true,auto:'auto'};
  return {status:0,stdout:'<pre id="result">'+JSON.stringify(result)+'</pre>',stderr:''};
 }};
 return localRequire(name);
}
load.resolve=localRequire.resolve;
vm.runInNewContext(fs.readFileSync(file,'utf8'),{require:load,process,console});
assert.equal(profiles.size,3);
