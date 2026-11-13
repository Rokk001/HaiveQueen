/* Executed by a real headless browser against stand-in API responses. */
window.addEventListener('load', async () => {
  const wait = async test => {
    for(let count=0;count<200;count++){if(test())return;await new Promise(resolve=>setTimeout(resolve,25));}
    throw Error('Timed out: '+test.toString());
  };
  const assert = (value, text) => {if(!value)throw Error(text);};
  const buttons = text => [...document.querySelectorAll('button')].filter(node=>node.textContent===text);
  const click = async text => {const node=buttons(text).find(node=>!node.disabled);assert(node,'Missing enabled button: '+text);node.click();await new Promise(resolve=>setTimeout(resolve,20));};
  const field = path => [...document.querySelectorAll('[data-path]')].find(node=>node.dataset.path===JSON.stringify(path));
  const input = (path, value) => {const node=field(path);assert(node,'Missing field '+path);node.value=value;node.dispatchEvent(new Event('input'));};
  window.confirm=()=>false;
  try {
    if(document.documentElement.lang==='de'){
      assert(buttons('Allgemein').length && buttons('Anbieter').length && buttons('Projekte').length,'Language change did not translate tabs');
      document.body.dataset.testResult='passed';return;
    }
    await wait(()=>document.querySelector('table'));
    assert(document.body.textContent.includes('mock-agent'),'Agent snapshot missing');
    await click('Force stop');
    let requests=await (await fetch('/test-actions')).json();
    assert(!requests.some(row=>row.action==='force-stop'),'Force stop ran without confirmation');
    window.confirm=()=>true;
    await click('Force stop');
    requests=await (await fetch('/test-actions')).json();
    assert(requests.some(row=>row.action==='force-stop'&&row.data.confirmed===true),'Confirmed stop missing');
    await click('General');await wait(()=>field(['web','port']));
    input(['web','port'],'9494');await click('Save');
    await wait(()=>document.getElementById('notice').textContent.includes('Settings changed'));
    assert(field(['web','port']).value==='9494','Conflict lost edited port');
    buttons('Save')[0].click();
    input(['web','port'],'9595');
    await wait(()=>document.getElementById('notice').textContent.includes('Saved.'));
    assert(field(['web','port']).value==='9595','A pending save overwrote newer input');
    assert(document.getElementById('dirty-marker').textContent==='Unsaved changes','New edits marked saved');
    requests=await (await fetch('/test-actions')).json();
    assert(requests.some(row=>row.action==='settings/save'&&row.data.data.web.port===9494&&row.data.revision==='revision-1'),'Typed port or revision missing');
    await click('Providers');await wait(()=>buttons('Login').length);
    await click('Login');await wait(()=>document.querySelector('[id^=login-] a'));
    const panel=document.querySelector('[id^=login-]');
    assert(panel.querySelector('a').href==='https://example.invalid/device','Login URL not clickable');
    assert(panel.textContent.includes('ABCD-EFGH'),'Device code not shown');
    const code=panel.querySelector('input');code.value='return-code';panel.querySelector('form').dispatchEvent(new Event('submit',{cancelable:true}));
    await wait(()=>document.getElementById('notice').textContent==='Done');
    input(['providers',0,'classes','medium','model'],'own-model');
    input(['providers',0,'allowedTools'],'Read\nBash(cmd(a,b))');
    const args=field(['providers',0,'args']);
    input(['providers',0,'args'],args.value+'\n--extra');
    assert(buttons('Login')[0].disabled,'Unsaved provider still offers login');
    await click('Save');await wait(()=>document.getElementById('notice').textContent.includes('Saved.'));
    requests=await (await fetch('/test-actions')).json();
    const provider=requests.filter(row=>row.action==='settings/save').at(-1).data.data.providers[0];
    assert(provider.classes.medium.customModel===true,'Custom model flag missing');
    assert(provider.args.includes('Read,Bash(cmd(a,b))'),'Allowed tools do not reach CLI arguments');
    assert(provider.args.includes(''),'Empty CLI argument was removed');
    assert(!Object.hasOwn(provider,'allowedTools'),'Synthetic tools field leaked into settings');
    await click('Projects');await wait(()=>field(['compileCheck']));
    assert(field(['integrationBranch']).value==='develop','Repository default branch was replaced');
    const rule=[...document.querySelectorAll('label')].find(node=>node.textContent.includes('AGENTS.md, 12 tokens'));
    assert(rule,'Knowledge-base file and token count missing');rule.querySelector('input').click();
    await click('Save');await wait(()=>document.getElementById('notice').textContent==='Done');
    requests=await (await fetch('/test-actions')).json();
    const project=requests.filter(row=>row.action==='settings/save').at(-1).data;
    assert(project.project==='demo' && project.data.rulesOff.includes('AGENTS.md'),'Knowledge-base switch not saved');
    assert(project.place.plansFolder==='demo-plans','Project place missing');
    await click('Questions');await wait(()=>document.querySelector('[data-question]'));
    const answer=document.querySelector('[data-question] textarea');answer.value='Proceed';
    await click('Answer');
    await click('Accept');
    await click('Plans');await wait(()=>buttons('Plan').length);await click('Plan');
    await wait(()=>buttons('Restart Queen on mock ✓').length);
    assert(buttons('Stop gracefully').length>0,'Plan stop missing');
    document.getElementById('close-detail').click();
    await click('Dashboard');await wait(()=>buttons('Live').length);await click('Live');
    await wait(()=>document.querySelector('.live-output')?.textContent.includes('live milestone'));
    await wait(()=>document.querySelector('.live-output')?.textContent.includes('live milestone 2'));
    await click('Check now');await wait(()=>buttons('Update').length);await click('Update');
    requests=await (await fetch('/test-actions')).json();
    for(const action of ['login/input','questions/answer','owner/accept','updates/install'])assert(requests.some(row=>row.action===action),'Missing '+action);
    assert(requests.find(row=>row.action==='login/input').data.text==='return-code','Login input lost');
    await click('General');await wait(()=>field(['language']));
    input(['language'],'German');await click('Save');
    await wait(()=>document.documentElement.lang==='de');
  } catch(error) {document.body.dataset.testResult='failed';document.body.append(document.createTextNode('BROWSER TEST: '+error.stack));}
});
