/* Executed by a real headless browser against stand-in API responses. */

window.addEventListener('load', async () => {

  const wait = async test => {

    for(let count=0;count<200;count++){if(test())return;await new Promise(resolve=>setTimeout(resolve,25));}

    throw Error('Timed out: '+test.toString());

  };

  const assert = (value, text) => {if(!value)throw Error(text);};

  const buttons = text => [...document.querySelectorAll('button')].filter(node=>node.textContent===text || node.dataset.tab===text);

  const click = async text => {const node=buttons(text).find(node=>!node.disabled);assert(node,'Missing enabled button: '+text);node.click();await new Promise(resolve=>setTimeout(resolve,20));const dialog=document.getElementById('confirmation-dialog');if(dialog?.open){warning=document.getElementById('confirmation-message').textContent;dialog.querySelectorAll('button')[confirmResult?1:0].click();await new Promise(resolve=>setTimeout(resolve,20));}await wait(()=>!node.disabled);};

  const field = path => [...document.querySelectorAll('[data-path]')].find(node=>node.dataset.path===JSON.stringify(path));

  const input = (path, value) => {const node=field(path);assert(node,'Missing field '+path);node.value=value;node.dispatchEvent(new Event('input'));};

  let confirmResult=false,warning='';

  window.confirm=()=>{throw Error('Native confirmation must not be used');};

  try {

    await wait(()=>document.querySelector('[data-panel="QUEENS AND AGENTS"] h2'));

    assert(document.querySelector('[data-panel="QUEENS AND AGENTS"] h2').textContent.startsWith('Hive'),'Hive heading missing');
    assert(!document.querySelector('.dashboard [data-panel="STATUS"]'),'Redundant status panel must be absent');
    assert(!document.querySelector('[data-panel="SYSTEM"] button'),'System card must not contain redundant details action');
    assert(document.querySelector('.quota-label')?.textContent,'Quota reserve label missing');

    await wait(()=>document.querySelector('.model-limit .slot-line.limit-help'));
    const slotHelp=document.querySelector('.model-limit .slot-line.limit-help');
    assert(slotHelp.dataset.help.includes('filled cell')||slotHelp.dataset.help.includes('volle Wabe'),'Original GUI slot explanation missing');
    slotHelp.focus();assert(!document.getElementById('tile-preview').hidden,'Slot tooltip must open on keyboard focus');
    slotHelp.click();assert(document.getElementById('detail').open,'Slot explanation must open on tap');
    document.getElementById('detail').close();

    if(document.documentElement.lang==='de'){

      assert(buttons('Allgemein').length && buttons('Anbieter').length && buttons('Projekte').length,'Language change did not translate tabs');

      await wait(()=>buttons('Sofort stoppen').length);

      warning='';confirmResult=false;

      await click('Sofort stoppen');

      assert(warning.startsWith('Sofort stoppen beendet alle'),'Force stop warning was not translated');

      assert(document.body.textContent.includes('LÄUFT'),'Orchestrator state was not translated');

      assert([...document.querySelectorAll('th')].some(node=>node.textContent==='LAUFZEIT'),'Runtime heading was not translated');

      assert([...document.querySelectorAll('td')].some(node=>node.textContent==='läuft'),'Agent state was not translated');

      assert([...document.querySelectorAll('td')].some(node=>node.textContent==='running'),'A user model name was translated');

      await click('Einstellungen');

      await click('Anbieter');await wait(()=>field(['providers',0,'access']));

      const access=field(['providers',0,'access']);

      assert([...access.options].some(node=>node.value==='subscription'&&node.textContent==='Abo'),'Access choice lost its desktop label');

      assert(document.body.textContent.includes('Login vorhanden'),'Provider login status missing');

      await click('Einstellungen');

      await click('Projekte');await wait(()=>field(['compileCheck']));

      assert(document.body.textContent.includes('wird von Codex CLI selbst geladen'),'Native CLI rule warning missing');

      document.body.dataset.testResult='passed';return;

    }

    await wait(()=>document.querySelector('table'));

    const settingsMenu=document.querySelector('#settings-subnav');

    assert(settingsMenu.hidden,'Settings must start collapsed');

    await click('Settings');

    assert(!settingsMenu.hidden && settingsMenu.querySelectorAll('[data-tab]').length===3,'Settings children missing');

    assert(getComputedStyle(settingsMenu).position==='absolute','Settings must open as an overlay dropdown');

    assert(settingsMenu.getBoundingClientRect().width<=200,'Settings dropdown is too wide');

    assert(document.querySelector('#settings-toggle').getAttribute('aria-expanded')==='true','Settings expansion state missing');

    await click('Settings');

    assert(settingsMenu.hidden,'Settings must collapse on second click');

    assert(document.body.textContent.includes('mock-agent'),'Agent snapshot missing');

    assert(document.querySelector('.header-queen')?.complete,'Header emblem missing');

    assert(document.querySelector('#app.dashboard'),'Dashboard layout missing');

    const projectMenu=document.querySelector('button[aria-label="Actions · Project · demo"]');

    const queenMenu=document.querySelector('button[aria-label="Actions · Queen · Test Queen"]');

    assert(projectMenu && queenMenu,'Project or Queen action menu missing');

    queenMenu.click();await wait(()=>document.querySelector('#detail').open);

    assert(document.querySelector('#detail .entity-console'),'Queen embedded console missing');
    assert(!document.querySelector('#detail .entity-all'),'Agent details must not repeat in an accordion');
    assert(document.querySelector('#agent-technical').hidden,'Technical information must start hidden');
    document.querySelector('#agent-tab-1').click();
    assert(document.querySelector('#agent-overview').hidden&&!document.querySelector('#agent-technical').hidden,'Technical tab did not switch panels');
    document.querySelector('#agent-tab-0').click();
    assert(!document.querySelector('#agent-overview').hidden,'Overview did not return');

    document.querySelector('#close-detail').click();

    projectMenu.click();await wait(()=>document.querySelector('#detail').open);

    assert([...document.querySelectorAll('#detail button')].some(node=>node.textContent==='Settings'),'Project settings action missing');

    document.querySelector('#close-detail').click();

    assert(!document.querySelector('#app select[aria-label="Project"]'),'Dashboard project filter remains');

    assert(!document.querySelector('[data-panel="Activity"]'),'Activity panel remains');

    assert(document.querySelector('.dashboard-primary').children.length===2,'Left column must contain only two panels');

    assert(document.querySelector('[data-tab="Questions"] .question-badge')?.textContent==='2','Question badge missing');

    assert(!document.querySelector('[data-panel="Queue"] button'),'Queue questions action remains');

    const headings=panel=>[...document.querySelectorAll(`[data-panel="${panel}"] th`)].map(node=>node.textContent).join('|');

    assert(headings('ACTIVE PLANS')==='#|PLAN|PROJECT|QUEEN|AGENTS|PROGRESS|STATUS|RUNTIME','Plan columns differ from desktop GUI');

    assert(headings('QUEENS AND AGENTS')==='#|AGENT|MODEL|PLAN|TASK|CONTEXT|TOKENS|STATUS|RUNTIME','Agent columns differ from desktop GUI');

    assert(document.querySelector('tr.child-agent .tree-branch'),'Agent hierarchy missing');

    assert(!document.querySelector('#app [data-panel="Projects"]'),'Redundant Projects dashboard panel remains');

    assert(document.querySelector('.dashboard-primary [data-panel="ACTIVE PLANS"]'),'Independent plan column missing');

    assert(!document.querySelector('[data-panel="LOG"]'),'Logs should have their own tab');

    await click('Logs');

    assert(document.querySelector('#app.logs-view [data-panel="LOG"]'),'Logs navigation missing');

    await click('Dashboard');

    document.querySelector('button[aria-label="Actions · mock-agent"]').click();

    await click('Filter log');

    assert(document.querySelector('[data-tab="Logs"]').getAttribute('aria-selected')==='true','Filter log must open Logs');

    await click('Dashboard');

    assert(document.querySelector('.dashboard-secondary [data-panel="SYSTEM"]'),'Compact system panel missing');

    assert(document.querySelector('td[data-label]'),'Mobile table labels missing');

    const limits=document.querySelector('.model-limits');

    assert(limits.children.length===3,'Three provider rows missing');

    assert(limits.scrollWidth<=limits.clientWidth+1,'Limits overflow horizontally');

    assert(limits.scrollHeight<=limits.clientHeight+1,'Three provider rows require scrolling');

    const metric=document.querySelector('[data-tile="bees"]');

    metric.focus();assert(!document.querySelector('#tile-preview').hidden,'Metric focus preview missing');

    metric.click();await wait(()=>document.querySelector('#detail').open);

    assert(document.querySelector('#detail .tile-content'),'Metric detail content missing');

    document.querySelector('#close-detail').click();

    assert(document.querySelector('.dashboard-secondary').firstElementChild.dataset.panel==='Limits','Limits must lead the sidebar');

    assert(document.documentElement.scrollWidth <= window.innerWidth,'Page overflows viewport');

    await click('Force stop');

    let requests=await (await fetch('/test-actions')).json();

    assert(!requests.some(row=>row.action==='force-stop'),'Force stop ran without confirmation');

    confirmResult=true;

    await click('Force stop');

    requests=await (await fetch('/test-actions')).json();

    assert(requests.some(row=>row.action==='force-stop'&&row.data.confirmed===true),'Confirmed stop missing');

    if(document.querySelector('#settings-subnav').hidden)await click('Settings');

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

    if(document.querySelector('#settings-subnav').hidden)await click('Settings');

    await click('Providers');await wait(()=>buttons('Login').length);
    await wait(()=>buttons('Save access token').length);
    assert(document.body.textContent.includes('One access token is shared'),'Shared token login is not explained');
    assert(!buttons('Sign in in browser').length,'Deferred Git device login is offered');
    assert(!document.querySelector('[aria-label="Registered public OAuth client ID"]'),'Deferred OAuth registration is offered');
    const gitToken=document.querySelector('[aria-label="Access token"]');
    assert(gitToken.type==='password','Git token is not masked');
    gitToken.value='test-token';await click('Save access token');
    await wait(()=>document.body.textContent.includes('Current method: Access token'));
    requests=await (await fetch('/test-actions')).json();
    assert(requests.some(row=>row.action==='git-login/token'&&row.data.provider==='example'&&row.data.token==='test-token'),'Provider token was not saved');
    assert(!requests.some(row=>row.action==='git-login/start'||row.action==='git-login/poll'),'Deferred Git device endpoint was used');
    assert(document.querySelector('[aria-label="Access token"]').value==='','Saved token remains in the form');

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

    if(document.querySelector('#settings-subnav').hidden)await click('Settings');

    await click('Projects');await wait(()=>field(['compileCheck']));
    const way=document.querySelector('[aria-label="Way of working"]');
    assert(way && way.options.length===3,'Workflow choices missing');
    assert([...document.querySelectorAll('legend')].filter(node=>['Merge','Check','Build','Deploy','Release'].includes(node.textContent)).length===5,'Workflow rows missing');
    assert(!document.querySelector('[aria-label="Merge on"]'),'Merge must not have an off switch');
    assert(!document.getElementById('app').textContent.includes('Publish release at integration'),'Legacy release switch remains');
    assert(!field(['compileCheck']).disabled && field(['compileCheck']).required,'Enabled local check must require its command');
    assert(field(['buildCommand']).disabled,'Disabled build command must be greyed');
    way.value='git';way.dispatchEvent(new Event('change'));
    assert(field(['compileCheck']).disabled,'Provider check command must be greyed');
    assert(document.querySelector('[aria-label="Release location"]').disabled,'Unsupported provider release must be greyed');
    assert(document.querySelector('[aria-label="Way of working"]').value==='git','Unsupported step must not change the choice');
    const checkMode=document.querySelector('[aria-label="Check location"]');checkMode.value='local';checkMode.dispatchEvent(new Event('change'));
    assert(document.querySelector('[aria-label="Way of working"]').value==='custom','Manual change must select Custom');
    document.querySelector('[aria-label="Way of working"]').value='local';document.querySelector('[aria-label="Way of working"]').dispatchEvent(new Event('change'));

    assert(field(['integrationBranch']).value==='develop','Repository default branch was replaced');

    const rule=[...document.querySelectorAll('label')].find(node=>node.textContent.includes('AGENTS.md, 12 tokens'));

    assert(rule,'Knowledge-base file and token count missing');rule.querySelector('input').click();
    await click('Save');await wait(()=>document.getElementById('notice').textContent==='Saved.');

    requests=await (await fetch('/test-actions')).json();

    const project=requests.filter(row=>row.action==='settings/save').at(-1).data;

    assert(project.project==='demo' && project.data.rulesOff.includes('AGENTS.md'),'Knowledge-base switch not saved');

    assert(project.place.plansFolder==='demo-plans','Project place missing');
    await click('Reload');await wait(()=>document.getElementById('dirty-marker').textContent==='');
    assert(document.querySelector('[aria-label="Way of working"]').value==='local','Saved workflow choice was not reloaded');
    const selectProject=document.querySelector('[aria-label="Project"]');selectProject.value='local-only';selectProject.dispatchEvent(new Event('change'));
    await wait(()=>document.body.textContent.includes('Only Local is available.'));
    assert([...document.querySelector('[aria-label="Way of working"]').options].filter(option=>option.value!=='local').every(option=>option.disabled),'Unsupported remote offers nonlocal choice');
    assert(['Merge','Check','Build','Deploy','Release'].every(name=>document.querySelector('[aria-label="'+name+' location"]').disabled),'Unsupported remote offers provider toggles');
    await click('Releases');await wait(()=>buttons('Publish as full release').length);
    assert(buttons('Publish as full release').length===1,'Full release must not offer promotion');
    for(const value of ['v2.0.1-dev.3','2026-01-02','a'.repeat(40),'true'])assert(document.body.textContent.includes(value),'Missing release metadata '+value);
    await click('Publish as full release');
    requests=await (await fetch('/test-actions')).json();
    const promotion=requests.find(row=>row.action==='releases/promote');
    assert(promotion.data.tag==='v2.0.1-dev.3' && promotion.data.confirmed===true && !promotion.data.commit,'Promotion must send selected tag and confirmation only');

    await click('Questions');await wait(()=>document.querySelector('[data-question]'));

    const answer=document.querySelector('[data-question] textarea');answer.value='Proceed';

    await click('Answer');

    await click('Accept');

    await wait(()=>!document.querySelector('[data-tab="Questions"] .question-badge'));

    await click('Plans');await wait(()=>buttons('Plan').length);

    assert([...document.querySelectorAll('th')].some(node=>node.textContent==='Started'),'Desktop plan Started column missing');

    await click('Plan');

    await wait(()=>document.querySelector('.plan-document h2')?.textContent==='Mock plan');

    assert(!document.querySelector('.plan-document script'),'Plan text must not execute HTML');
    assert(!document.querySelector('#detail .entity-all'),'Plan must not repeat details in an accordion');
    document.querySelector('#plan-tab-1').click();
    assert(!document.querySelector('#plan-panel-1').hidden&&document.querySelector('#plan-panel-0').hidden,'Plan text tab must show the document');
    document.querySelector('#plan-tab-0').click();

    await wait(()=>buttons('Restart Queen on mock ✓').length);

    assert(buttons('Restart Queen on mock ✓')[0].disabled,'Unavailable current provider must be marked and disabled');

    assert(buttons('Restart Queen on excluded')[0]?.disabled,'Excluded provider must be visible and disabled');

    assert(buttons('Restart Queen on available').some(node=>!node.disabled),'Available provider must remain selectable');

    assert(buttons('Stop gracefully').length>0,'Plan stop missing');

    document.getElementById('close-detail').click();

    await click('Dashboard');document.querySelector('button[aria-label="Actions · mock-agent"]').click();await wait(()=>document.querySelector('#detail .live-output'));

    await wait(()=>document.querySelector('.live-output')?.textContent.includes('live milestone'));

    await wait(()=>document.querySelector('.live-output')?.textContent.includes('live milestone 2'));

    document.getElementById('close-detail').click();

    await click('Check now');await wait(()=>buttons('Update').length);await click('Update');

    requests=await (await fetch('/test-actions')).json();

    for(const action of ['login/input','questions/answer','owner/accept','updates/install'])assert(requests.some(row=>row.action===action),'Missing '+action);

    assert(requests.find(row=>row.action==='login/input').data.text==='return-code','Login input lost');

    if(document.querySelector('#settings-subnav').hidden)await click('Settings');

    await click('General');await wait(()=>field(['language']));

    input(['language'],'German');await click('Save');

    await wait(()=>document.documentElement.lang==='de');

  } catch(error) {document.body.dataset.testResult='failed';document.body.append(document.createTextNode('BROWSER TEST: '+error.stack));}

});
