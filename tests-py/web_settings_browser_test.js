window.addEventListener('load',async()=>{
  const assert=(value,message)=>{if(!value)throw Error(message);};
  const wait=async check=>{for(let n=0;n<200;n++){if(check())return;await new Promise(resolve=>setTimeout(resolve,25));}throw Error('Settings did not load');};
  const click=async text=>{const node=[...document.querySelectorAll('button')].find(node=>node.textContent===text);assert(node,'Missing button '+text);node.click();await wait(()=>!node.disabled||!node.isConnected);};
  try {
    await wait(()=>document.querySelector('[data-tile]'));
    for(const tab of ['System','AI providers','Projects']){
      await click('Settings');await click(tab);await wait(()=>document.querySelector('.settings-categories'));
      const categories=[...document.querySelectorAll('.settings-categories button')];
      for(const category of categories){await click(category.textContent);assert(category.getAttribute('aria-pressed')==='true','Category not selected');assert(document.documentElement.scrollWidth<=innerWidth,'Horizontal overflow: '+tab+' / '+category.textContent);}
      const search=document.querySelector('.settings-search');search.value='no-such-setting-xyz';search.dispatchEvent(new Event('input'));
      assert(!document.querySelector('.settings-empty').hidden,'Missing empty search state');
      search.value='';search.dispatchEvent(new Event('input'));await click(categories[0].textContent);
    }
    await click('Workflow and checks');
    assert(document.querySelector('.settings-workflow-summary').textContent.includes('Check: Local'),'Missing workflow summary');
    document.body.dataset.testResult='passed';
  }catch(error){document.body.dataset.testResult='failed';document.body.append('BROWSER TEST: '+error.stack);}
});
