const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawnSync}=require('node:child_process');
const source=fs.readFileSync(require.resolve('../haivequeen/web-client.js'),'utf8');
const body=source.slice(source.indexOf('  function fitDashboard(){'),source.indexOf("  window.addEventListener('resize',fitDashboard)"));
const list={style:{},getBoundingClientRect:()=>({top:100})};
const primary={style:{},querySelector:()=>list};
new Function('view','window',`const tab='Dashboard';${body}return fitDashboard;`)({querySelector:()=>primary},{innerWidth:1280,innerHeight:900,scrollY:300})();
assert.equal(list.style.maxHeight,'784px','Scrolled header must not consume available viewport space twice');
const browser=process.env.HQ_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
assert(fs.existsSync(browser),'Set HQ_TEST_BROWSER to a Chromium executable');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'hq-scroll-test-'));
try{
 const css=fs.readFileSync(require.resolve('../haivequeen/web-client.css'),'utf8');
 const cells='<div class="hive-tree-cells"><div><div><div>'+['name','model','task','ctx','tokens','status','runtime'].map(c=>`<div data-column="${c}">${c==='task'?'<div class="hq-pipeline"><div class="hq-steps">'+['Merge','Check','Build','Push','Deploy','Release'].map(s=>`<button class="hq-step">${s}</button>`).join('')+'</div><span class="hq-step-count">4 / 6</span></div>':c}</div>`).join('')+'</div></div></div></div>';
 const html=`<style>${css}</style><main style="width:900px;max-width:100%;margin:0"><ul class="hives-list hives-tree" style="max-height:300px"><li class="hive-tree-row">${cells}</li></ul></main><pre id="result"></pre><script>
 const list=document.querySelector('.hives-list');
 const result={shortFits:list.scrollHeight<=list.clientHeight,pipelineFits:[...document.querySelectorAll('.hq-pipeline,.hq-steps')].every(n=>n.scrollWidth<=n.clientWidth),listFits:list.scrollWidth<=list.clientWidth};
 const row=list.firstElementChild.outerHTML;list.innerHTML=row.repeat(30);
 result.longScrolls=list.scrollHeight>list.clientHeight;result.auto=getComputedStyle(list).overflowY;
 document.querySelector('#result').textContent=JSON.stringify(result);
 </script>`;
 const file=path.join(temp,'layout.html');fs.writeFileSync(file,html);
 const environment={...process.env};
 for(const key of ['HOME','USERPROFILE','LOCALAPPDATA','APPDATA','XDG_DATA_HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME'])environment[key]=temp;
 for(const width of [1280,768,400]){
 const proc=spawnSync(browser,['--headless','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+path.join(temp,'profile'),'--window-size='+width+',900','--dump-dom','file:///'+file.replaceAll('\\','/')],{encoding:'utf8',timeout:30000,windowsHide:true,env:environment});
 assert.equal(proc.status,0,proc.error?.message||proc.stderr);
 const match=proc.stdout.match(/<pre id="result">([^<]+)<\/pre>/);assert(match,proc.stdout);
 const result=JSON.parse(match[1]);
 assert.equal(result.pipelineFits,true,'Pipeline steps must wrap without horizontal scrolling');
 assert.equal(result.listFits,true,'Hives must fit the available width');
 assert.equal(result.shortFits,true,'Short content must not scroll');
 assert.equal(result.longScrolls,true,'Long content remains accessible');
 assert.equal(result.auto,'auto');
 }
 console.log('Viewport height and real-browser overflow checks passed at desktop, tablet and mobile sizes');
}finally{assert(path.resolve(temp).startsWith(path.resolve(os.tmpdir())+path.sep+'hq-scroll-test-'));fs.rmSync(temp,{recursive:true,force:true,maxRetries:5,retryDelay:100});}
