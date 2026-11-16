window.addEventListener('load', async () => {
  const wait=async fn=>{for(let i=0;i<200;i++){if(fn())return;await new Promise(r=>setTimeout(r,25));}throw Error('Timeout: '+fn);};
  const assert=(v,m)=>{if(!v)throw Error(m);};
  try {
    await wait(()=>document.querySelector('#update-status button') && document.querySelector('[data-panel="Hives"]'));
    const update=document.querySelector('#update-status');
    const install=[...update.querySelectorAll('button')].find(b=>b.textContent==='Update to v2');
    assert(install,'release offer missing');
    install.click();await wait(()=>document.querySelector('#confirmation-dialog')?.open);
    document.querySelectorAll('#confirmation-dialog button')[1].click();
    await wait(()=>[...update.querySelectorAll('button')].some(b=>b.textContent==='Force update'));
    const installActions=await (await fetch('/test-actions')).json();
    assert(installActions.some(a=>a.action==='/api/updates/install' && a.data.confirmed===true && a.data.version==='v2'),'confirmed install missing');
    assert(update.textContent.includes('Update waiting'),'waiting label missing');
    assert(update.title.includes('schema change') && update.title.includes('Queen · Q · P') && update.title.includes('Build · B · P'),'waiting tooltip missing reason or jobs');
    assert(document.querySelector('#version').textContent==='Running v2','core version missing');
    assert(document.querySelector('.hq-step-count')?.textContent.includes('Version: v1'),'build version missing');
    const plan=document.querySelector('[data-panel="Hives"] .hive-tree-plan .row-menu');
    assert(plan,'plan detail action missing');
    plan.click();await wait(()=>document.querySelector('.plan-version'));
    assert(document.querySelector('.plan-version').textContent.includes('v1'),'plan version missing');
    document.querySelector('#close-detail').click();
    const force=[...update.querySelectorAll('button')].find(b=>b.textContent==='Force update');
    assert(force,'force button missing');
    force.click();await wait(()=>document.querySelector('#confirmation-dialog')?.open);
    assert(document.querySelector('#confirmation-message').textContent.includes('Queens') && document.querySelector('#confirmation-message').textContent.includes('Deploys') && document.querySelector('#confirmation-message').textContent.includes('again'),'warning incomplete');
    document.querySelector('#confirmation-dialog button').click();
    await new Promise(r=>setTimeout(r,100));
    assert(!(await (await fetch('/test-actions')).json()).some(a=>a.action==='/api/updates/force'),'cancel triggered force');
    await wait(()=>[...update.querySelectorAll('button')].some(b=>b.textContent==='Force update' && !b.disabled));
    [...update.querySelectorAll('button')].find(b=>b.textContent==='Force update').click();
    await wait(()=>document.querySelector('#confirmation-dialog')?.open);
    document.querySelectorAll('#confirmation-dialog button')[1].click();
    for(let i=0;i<100;i++){const actions=await (await fetch('/test-actions')).json();if(actions.some(a=>a.action==='/api/updates/force'&&a.data.confirmed===true)){document.body.dataset.testResult='passed';return;}await new Promise(r=>setTimeout(r,25));}
    throw Error('confirmed force missing');
  } catch(e) { document.body.dataset.testResult='failed';document.body.append(document.createTextNode('BROWSER TEST: '+e.stack)); }
});
