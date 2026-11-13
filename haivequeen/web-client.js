/* Browser UI, using only the platform DOM and the authenticated engine API. */
'use strict';
const copy = value => JSON.parse(JSON.stringify(value));
function setValue(object, path, value) {
  let target = object;
  for (const key of path.slice(0, -1)) target = target[key];
  target[path[path.length - 1]] = value;
}
function safeLinks(links) { return links.filter(link => /^https?:\/\//i.test(link)); }
function effectiveCapabilities(provider) {
  const aliases={images:'Image generation',blender:'3D modelling',build:'Build'};
  const selected=(provider.capabilities || []).map(value=>{const name=String(value).trim();return aliases[name.toLowerCase()] || name;});
  if(!provider.capabilitiesVersion){
    if(provider.use!=='capability-only')selected.push('Coding');
    const access=String(provider.access || '').toLowerCase();
    if(!(provider.server || provider.endpoint || provider.local || access==='llm'))selected.push('Queen');
  }
  return [...new Set(selected)];
}
function capabilityRows(provider, names=[]) {
  const selected=effectiveCapabilities(provider), thresholds=provider.capabilityThresholds || {};
  return [...new Set([...names,...selected,...Object.keys(thresholds)])].map(name=>({name,enabled:selected.includes(name),threshold:thresholds[name] ?? 100}));
}
function orchestratorDisabled(action, label) {
  if(action==='start')return label!=='STOPPED';
  if(action==='stop-gracefully')return label==='STOPPED'||label==='STOPPING';
  return false;
}
function toolsFromArgs(args) {
  const index=args.indexOf('--allowedTools');if(index<0 || index+1>=args.length)return [];
  const result=[];let depth=0,current='';
  for(const char of args[index+1]){if(char==='(')depth++;if(char===')')depth=Math.max(0,depth-1);if(char===','&&depth===0){result.push(current.trim());current='';}else current+=char;}
  result.push(current.trim());return result.filter(Boolean);
}
function withTools(args, tools) {
  const out=[...args],index=out.indexOf('--allowedTools');
  if(index>=0 && index+1<out.length)out.splice(index,2);
  if(tools.length)out.splice(index<0?out.length:index,0,'--allowedTools',tools.join(','));
  return out;
}
function planActions(row) {
  if (row.status === 'DONE') return [];
  const out = [];
  if (row.status === 'PAUSED' && row.stopped) out.push('resume');
  if (['DRAFT', 'CANCELLED'].includes(row.status)) out.push('start');
  if (['SENT','PREPARING','QUEUED','RUNNING','WAITING','REVIEW'].includes(row.status)) out.push('stop-gracefully');
  if (row.has_hive && !row.stopped) out.push('force-stop');
  if (row.status === 'DRAFT' || row.stopped) out.push('cancel');
  if (row.status === 'CANCELLED' || row.stopped) out.push('back-to-draft');
  return out;
}
class RenderGate {
  constructor(render) { this.draw=render; this.pending=new Map(); }
  begin(node) {
    const key=node.textContent; let group=this.pending.get(key);
    if(!group){group={count:0,nodes:new Map()};this.pending.set(key,group);}
    group.count++; this.prepare(node);
  }
  prepare(node) {
    const group=this.pending.get(node.textContent);
    if(group){if(!group.nodes.has(node))group.nodes.set(node,node.disabled);node.disabled=true;node.setAttribute('aria-busy','true');}
  }
  end(node) {
    const group=this.pending.get(node.textContent); if(!group || --group.count)return;
    this.pending.delete(node.textContent);
    for(const [item,disabled] of group.nodes){item.disabled=disabled;item.removeAttribute('aria-busy');}
  }
  render() { this.draw(); }
}
class Client {
  constructor(options = {}) {
    Object.assign(this, {csrf:'', fetch:(...args)=>fetch(...args), confirm:text=>confirm(text), notice:()=>{}, onState:()=>{}, onError:()=>{}, project:'', loading:false, refreshWanted:false}, options);
    this.pending=new Map();
  }
  async request(action, data) {
    const options = {credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRF-Token':this.csrf}};
    if (data !== undefined) { options.method='POST'; options.body=JSON.stringify(data); }
    const response = await this.fetch('/api/' + action, options);
    if (response.status === 401) this.onUnauthorized?.();
    const result = await response.json();
    if (!response.ok || result?.ok === false) throw new Error(result.error || result.message || 'Request failed');
    return result;
  }
  async action(action, data={}, warning='') {
    const key=JSON.stringify([action,data]);
    if(this.pending.has(key))return null;
    if (warning && !this.confirm(warning)) {this.notice('Cancelled');return null;}
    this.notice('Working…');
    this.pending.set(key,true);
    try {
      const result = await this.request(action, {...data, ...(warning ? {confirmed:true} : {})});
      this.notice(result?.message || 'Done');
      return result;
    } catch (error) { this.notice(error.message, true); throw error; }
    finally {this.pending.delete(key);}
  }
  async refresh() {
    if (this.loading) { this.refreshWanted=true; return; }
    this.loading=true;
    const project = this.project;
    try {
      const value=await this.request('state' + (project ? '?project='+encodeURIComponent(project) : ''));
      if (project === this.project) { this.state=value; this.onState(value); }
      else this.refreshWanted=true;
    } catch(error) { this.onError(error); }
    finally {
      this.loading=false;
      if (this.refreshWanted) { this.refreshWanted=false; void this.refresh(); }
    }
  }
}

function mount(document, bootstrap) {
  const $ = id => document.getElementById(id);
  const t = text => bootstrap.translations[text] || text;
  const viewLabels = {apiaries:'Projects',hives:'Plans',bees:'Agents',tin:'Tokens in',tout:'Tokens out',ctx:'CONTEXT',
    ctx_limit:'Context limit',start_ctx:'START CONTEXT',tokens_in:'TOTAL IN',tokens_out:'TOTAL OUT',bee_capacity:'Agent slots',
    apiary:'PROJECT',hive:'PLAN',task:'TASK',model:'MODEL',status:'STATUS',runtime:'RUNTIME',queen:'QUEEN',bees_running:'BUSY AGENTS',
    progress:'PROGRESS',done:'Done',implemented:'Implemented',rejected:'Rejected',waiting:'Waiting',total:'Total',name:'Name',
    started:'Started',waiting_for:'Waiting for',error:'Error'};
  const pretty = key => t(bootstrap.labels[key] || viewLabels[key] || key.replace(/([a-z])([A-Z])/g,'$1 $2').replace(/_/g,' '));
  const el = (tag, text, cls) => {const node=document.createElement(tag); if(text!==undefined) node.textContent=String(text); if(cls)node.className=cls; return node;};
  const button = (text, action) => {const node=el('button',t(text)); node.type='button'; renderGate.prepare(node); node.onclick=()=>runButton(node,action); return node;};
  async function runButton(node, action) {
    renderGate.begin(node);
    client.notice('Working…');
    try { await action(); } catch(error) {client.notice(error.message,true);}
    finally {renderGate.end(node);if(client.state)renderStateNow(client.state);}
  }
  const client=new Client({csrf:bootstrap.csrf, confirm:text=>confirm(t(text)), notice:(text,bad=false)=>{ $('notice').textContent=t(text);$('notice').className=bad?'bad':'';},
    onUnauthorized:()=>{location.href='/login';}, onError:error=>client.notice(error.message,true),onState:renderState});
  const renderGate=new RenderGate(()=>renderStateNow(client.state));
  let tab='Dashboard', editor=null, plans=[], questions=[], providerStates=[], updateState={}, overlay=null;
  let planLoading=false, questionLoading=false, providerLoading=false, updateLoading=false;
  let lastAutomaticCheck=0, automaticChecking=false;
  let generation=0, logCleared=0, logFilter='', showNoise=false, logFollow=true, selectedPlan='', selectedAgent='';
  const liveRuns=new Map(), loginRuns=new Map(), jobs=new Map();
  const view=$('app');
  const actions={start:'Start','stop-gracefully':'Stop gracefully','force-stop':'Force stop',resume:'Resume',cancel:'Cancel plan','back-to-draft':'Back to draft'};
  function detail(title, value) {
    overlay={title,value}; renderDetail(); if (!$('detail').open) $('detail').showModal();
  }
  function renderDetail() {
    if (!overlay)return;
    $('detail-body').replaceChildren(el('h2',t(overlay.title)),valueView(overlay.value));
  }
  $('close-detail').onclick=()=>{$('detail').close();overlay=null;};
  function valueView(value) {
    if (value === null || value === undefined) return el('span','–','dim');
    if (typeof value !== 'object')return el('span',value);
    if (Array.isArray(value)) {
      const box=el('div');
      if(value.every(item=>item && typeof item==='object' && !Array.isArray(item)))box.append(table(value));
      else for(const item of value){const line=el('div');line.append(valueView(item));box.append(line);}
      return box;
    }
    const list=el('dl');
    for(const [key,item] of Object.entries(value)){list.append(el('dt',pretty(key)),el('dd'));list.lastChild.append(valueView(item));}
    return list;
  }
  function table(rows, columns, extra) {
    const wrap=el('div',undefined,'table-scroll'), table=el('table'), head=el('tr'), body=el('tbody');
    columns=columns || [...new Set(rows.flatMap(row=>Object.keys(row)))];
    for(const key of columns)head.append(el('th',pretty(key)));
    if(extra)head.append(el('th',t('Actions')));
    const thead=el('thead');thead.append(head);table.append(thead,body);
    for(const row of rows){const tr=el('tr');for(const key of columns){const td=el('td');td.append(valueView(key==='status'?t(row[key]):row[key]));tr.append(td);}if(extra){const td=el('td');extra(td,row);tr.append(td);}body.append(tr);}
    if(!rows.length){const tr=el('tr'),td=el('td',t('none'),'dim');td.colSpan=columns.length+1;tr.append(td);body.append(tr);}
    wrap.append(table);return wrap;
  }
  function section(title, parent=view) {const node=el('section');node.append(el('h2',t(title)));parent.append(node);return node;}
  async function act(action,data,warning) {const result=await client.action(action,data,warning);if(result){void client.refresh();void refreshLists();}return result;}
  function key(row) {return {project:row.project || row.apiary,plan:row.plan_path};}
  function selectTab(name) {
    if(editor?.dirty && !confirm(t('Discard unsaved changes?')))return;
    generation++;editor=null;tab=name;
    for(const node of $('tabs').children)node.setAttribute('aria-selected',String(node.dataset.tab===tab));
    render();void refreshLists();
    if(['General','Providers','Projects'].includes(tab))void loadEditor(tab==='Projects' ? client.state?.projects?.[0]?.name : '');
  }
  for(const name of ['Dashboard','Plans','Questions','General','Providers','Projects']){const node=button(name,()=>selectTab(name));node.dataset.tab=name;node.setAttribute('role','tab');node.setAttribute('aria-selected',String(name===tab));$('tabs').append(node);}
  $('tabs').setAttribute('role','tablist');
  function renderState(state) {
    renderGate.render();
  }
  function renderStateNow(state) {
    $('version').textContent=state.version || '';
    renderControls();
    if(tab==='Dashboard'){
      const positions=[...view.querySelectorAll('.table-scroll,.log,details')].map(node=>({left:node.scrollLeft,top:node.scrollTop,open:node.open}));
      const focused=document.activeElement,selection=focused?.selectionStart;
      const editingFilter=focused?.placeholder===t('Filter');
      render();
      [...view.querySelectorAll('.table-scroll,.log,details')].forEach((node,index)=>{const position=positions[index];if(position){node.scrollLeft=position.left;if(!node.classList.contains('log')||!logFollow)node.scrollTop=position.top;if(node.tagName==='DETAILS')node.open=position.open;}});
      if(editingFilter){const input=view.querySelector('input[placeholder]');input?.focus();if(input)input.setSelectionRange(selection,selection);}
    }
    if(overlay?.tile){overlay.value=state.overlays?.[overlay.tile];renderDetail();}
  }
  function renderControls() {
    const state=client.state || {}, toolbar=el('div',undefined,'toolbar');
    toolbar.append(el('span',t('Orchestrator')+': '+t(state.orchestrator?.label || '…')));
    for(const action of ['start','stop-gracefully','force-stop']){
      const node=button(actions[action],()=>act(action,{},action==='force-stop'?state.controls?.force_warning:t(actions[action]+' HaiveQueen?')));
      node.disabled=node.disabled || orchestratorDisabled(action,state.orchestrator?.label);toolbar.append(node);
    }
    toolbar.append(el('span',t('Web server')),
      button('Stop',async()=>{if(await client.action('web/stop',{},t('Stop the web server? The browser will disconnect.')))client.notice(t('Web server stopped.'));}),
      button('Restart',async()=>{await client.action('web/restart',{},t('Restart the web server? The browser will reconnect.'));}),
      button('Logout',async()=>{const response=await fetch('/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':client.csrf},body:'{}'});if(response.ok)location.href='/login';else throw Error(t('Logout failed'));}));
    $('controls').replaceChildren(toolbar);
  }
  function render() {
    view.replaceChildren();
    if(tab==='Dashboard')renderDashboard();
    if(tab==='Plans')renderPlans();
    if(tab==='Questions')renderQuestions();
    if(['General','Providers','Projects'].includes(tab))renderEditor();
  }
  function projectSelect(onchange, selected, all=true) {
    const select=el('select');select.setAttribute('aria-label',t('Project'));
    if(all){const option=el('option',t('all'));option.value='';select.append(option);}
    for(const project of client.state?.projects || []){const option=el('option',project.name);option.value=project.name;select.append(option);}
    select.value=selected || '';select.onchange=()=>onchange(select.value);return select;
  }
  function renderDashboard() {
    const state=client.state;
    if(!state){view.append(el('p',t('Loading…')));return;}
    const bar=el('div',undefined,'toolbar');bar.append(el('label',t('Project')),projectSelect(value=>{client.project=value;void client.refresh();},client.project));view.append(bar);
    const tiles=el('div',undefined,'tiles');
    for(const [name,value] of Object.entries(state.tiles || {})){
      const tile={tokens_in:'io',tokens_out:'io',start_ctx:'start',bee_capacity:'bees'}[name] || name;
      tiles.append(button(pretty(name)+': '+(typeof value==='object'?(value?.saving ?? '–')+'%':value),()=>{detail(pretty(name),state.overlays?.[tile] || value);overlay.tile=tile;}));
    }
    view.append(tiles);
    const active=section('ACTIVE PLANS');active.append(table(state.hives || [],['name','apiary','status','queen','bees_running','done','implemented','rejected','waiting','total','progress','runtime'],(cell,row)=>{
      cell.append(button('Plan',()=>openPlan(row)),button('Details',()=>detail(row.name,row)));
      if(row.queen_agent)cell.append(button('Live',()=>openLive({...row,agent:row.queen_agent,project:row.apiary})));
      cell.append(button('Filter log',()=>{selectedPlan=row.name;selectedAgent='';render();}));
    }));
    const agents=section('QUEENS AND AGENTS');agents.append(table(state.bees || [],['name','apiary','hive','task','model','ctx','ctx_limit','tin','tout','status','runtime'],(cell,row)=>{
      if(row.agent)cell.append(button('Live',()=>openLive(row)));
      if(row.stop_agent){const stop=button('Force stop',()=>act('agent/force-stop',{project:row.project,agent:row.stop_agent},t('Stop agent %s (%s)? The package stays stopped until you resume it.').replace('%s',row.name).replace('%s',row.task)));stop.disabled=stop.disabled || !!row.stop_disabled;stop.title=row.stop_disabled||'';cell.append(stop);}
      if(row.stopped)cell.append(button('Resume package',()=>act('package/resume',{...key(row),package:row.package || row.task},t('Resume package')+'?')));
      cell.append(button('Details',()=>detail(row.name,row)),button('Filter log',()=>{selectedAgent=row.agent || row.name;selectedPlan='';render();}));
    }));
    const grid=el('div',undefined,'grid');view.append(grid);
    section('Limits',grid).append(table(state.limits || []));
    section('STATUS',grid).append(valueView(state.info));
    section('SYSTEM',grid).append(valueView(state.system));
    section('Activity',grid).append(valueView(state.activity));
    section('Queue',grid).append(valueView(state.queue));
    section('Projects',grid).append(valueView(state.apiaries));
    renderLog();renderUpdates();
  }
  function logRows() {
    const state=client.state || {};
    return (state.log || []).filter((row,index)=>{
      const meta=state.log_meta?.[index] || {};
      return (state.log_ids?.[index] || 0)>logCleared && (showNoise||!meta.noise) && (!selectedAgent||meta.agent===selectedAgent) && (!selectedPlan||meta.hive===selectedPlan) && (!logFilter||row.join(' ').toLowerCase().includes(logFilter.toLowerCase()));
    });
  }
  function renderLog() {
    const box=section('LOG'),bar=el('div',undefined,'toolbar'),search=el('input');search.placeholder=t('Filter');search.value=logFilter;
    search.oninput=()=>{logFilter=search.value;drawLog();};
    const noise=el('input');noise.type='checkbox';noise.checked=showNoise;noise.onchange=()=>{showNoise=noise.checked;drawLog();};
    const noiseLabel=el('label',t('Show routine lines'));noiseLabel.prepend(noise);
    const follow=el('input');follow.type='checkbox';follow.checked=logFollow;follow.onchange=()=>{logFollow=follow.checked;};const followLabel=el('label',t('Auto-scroll'));followLabel.prepend(follow);
    bar.append(search,noiseLabel,followLabel,button('Clear',()=>{logCleared=Math.max(0,...(client.state.log_ids || []));drawLog();}),button('Save log',()=>{
      const link=el('a');link.href=URL.createObjectURL(new Blob([logRows().map(row=>row.join(' ')).join('\n')],{type:'text/plain'}));link.download='haivequeen-log.txt';link.click();URL.revokeObjectURL(link.href);
    }),button('all',()=>{selectedPlan='';selectedAgent='';logFilter='';render();}));
    box.append(bar);const lines=el('pre',undefined,'log');box.append(lines);
    function drawLog(){lines.textContent=logRows().map(row=>row.join(' ')).join('\n');if(logFollow)lines.scrollTop=lines.scrollHeight;}
    drawLog();
  }
  async function openPlan(row) {
    detail(row.name,row);
    const target=$('detail-body'),bar=el('div',undefined,'actions');target.prepend(bar);
    for(const action of planActions(row))bar.append(button(actions[action],()=>act('plan/'+action,key(row),t(actions[action])+' '+row.name+'?')));
    // The active dashboard row and the all-plans row use the same plan menu.
    if(!row.status || !/^[A-Z]+$/.test(row.status)){
      bar.append(button('Stop gracefully',()=>act('plan/stop-gracefully',key(row),t('Stop gracefully')+'?')),
        button('Force stop',()=>act('plan/force-stop',key(row),t('Force stop')+'?')));
      if(row.stopped)bar.append(button('Resume',()=>act('plan/resume',key(row),t('Resume')+'?')));
      bar.append(button('Cancel plan',()=>act('plan/cancel',key(row),t('Cancel plan')+'?')));
    }
    if(row.status!=='DONE')bar.append(button('Release',()=>act('owner/release',key(row),t('Release')+'?')));
    const providers=await client.request('plan/providers?'+new URLSearchParams(key(row)));
    if(target!==$('detail-body') || !target.contains(bar))return;
    for(const provider of providers){
      const node=button(t('Restart Queen on %s').replace('%s',provider.name)+(provider.current?' ✓':''),()=>act('plan/restart-queen',{...key(row),provider:provider.name},t('Restart Queen on %s').replace('%s',provider.name)+'?'));
      node.disabled=provider.selectable===false || node.disabled;
      bar.append(node);
    }
  }
  function renderPlans() {
    const box=section('Plans');box.append(table(plans.map(row=>({...row,started:t(row.has_hive?'yes':'no')})),['apiary','name','status','started','waiting_for','error'],(cell,row)=>cell.append(button('Plan',()=>openPlan(row)))));
  }
  function renderQuestions() {
    const box=section('Questions');
    if(!questions.length)box.append(el('p',t('none open')));
    for(const question of questions){
      const card=el('article',undefined,'card question');card.dataset.question=question.id;
      card.append(el('h3','#'+question.id+' '+question.project+' · '+question.plan_path),el('small',question.created_at || ''),el('pre',question.text || question.question || question.item));
      if(question.recommendation){card.append(el('h3',t('Recommendation')),el('pre',question.recommendation));}
      const answer=el('textarea');answer.setAttribute('aria-label',t('Answer'));card.append(answer);
      if(question.recommendation)card.append(button('Use recommendation',()=>{answer.value=question.recommendation;}));
      const apiary=el('input');apiary.type='checkbox';const label=el('label',t('Apply to Project'));label.prepend(apiary);card.append(label);
      const check=question.item==='owner-check' || (String(question.item).startsWith('owner-check-') && question.item!=='owner-check-answered');
      card.append(button(check?'Save note':'Answer',()=>act('questions/answer',{id:question.id,text:answer.value,apiary:apiary.checked})));
      if(check){
        const packages=el('input');packages.placeholder=t('Rejected packages (comma separated)');card.append(packages);
        const packageList=el('div');for(const name of question.packages || []){const label=el('label',name),toggle=el('input');toggle.type='checkbox';toggle.dataset.package=name;toggle.onchange=()=>{packages.value=[...packageList.querySelectorAll('input:checked')].map(node=>node.dataset.package).join(', ');};label.prepend(toggle);packageList.append(label);}card.append(packageList);
        card.append(button('Accept',()=>act('owner/accept',{...key(question),text:answer.value},t('Accept')+'?')),
          button('Reject',()=>act('owner/reject',{...key(question),text:answer.value,packages:packages.value.split(',').map(x=>x.trim()).filter(Boolean)},t('Reject')+'?')));
      }
      box.append(card);
    }
  }
  function renderUpdates() {
    const box=section('Updates');box.append(valueView(updateState),button('Check now',async()=>{updateState=await client.action('updates/check');render();}));
    if(updateState.available?.version)box.append(button('Update',async()=>{await act('updates/install',{version:updateState.available.version},t(bootstrap.updateConfirm).replace('%s',updateState.available.version));}));
  }
  async function loadEditor(project='') {
    if(tab==='Projects' && !project){client.notice(t('Add Project'));renderEditor();return;}
    const token=++generation, pageTab=tab;
    client.notice('Loading…');
    try {
      const loaded=await client.request('settings/view'+(project?'?project='+encodeURIComponent(project):''));
      if(token!==generation || pageTab!==tab)return;
      editor={...loaded,project,dirty:false};
      if(!project)editor.data=mergeDefaults(bootstrap.general,editor.data);
      client.notice('');render();
    }catch(error){client.notice(error.message,true);}
  }
  function mergeDefaults(defaults, data) {
    const out=copy(defaults);
    for(const [key,value] of Object.entries(data || {}))out[key]=(value && typeof value==='object' && !Array.isArray(value) && out[key] && typeof out[key]==='object' && !Array.isArray(out[key]))?mergeDefaults(out[key],value):copy(value);
    return out;
  }
  function renderEditor() {
    if(tab==='Projects'){
      const bar=el('div',undefined,'toolbar');bar.append(projectSelect(async value=>{if(editor?.dirty&&!confirm(t('Discard unsaved changes?')))return;await loadEditor(value);},editor?.project,false),
        button('Add Project',addProject),button('Remove entry',async()=>{
          if(!editor?.project)return;
          const machine=await client.request('settings/view'),data=copy(machine.data);
          data.projects=(data.projects || []).filter(project=>project.name!==editor.project);
          await client.action('settings/save',{revision:machine.revision,data},t('Remove this Project from the settings?'));
        }));view.append(bar);
    }
    if(!editor){view.append(el('p',t(tab==='Projects' && !(client.state?.projects || []).length?'Add Project':'Loading…')));return;}
    const toolbar=el('div',undefined,'toolbar');view.append(toolbar);
    toolbar.append(button('Save',saveEditor),button('Reload',()=>{if(!editor.dirty || confirm(t('Discard unsaved changes?')))return loadEditor(editor.project);}));
    const form=section(tab), data=editor.data;
    if(tab==='General'){
      const defaults=bootstrap.general;
      for(const [key,value] of Object.entries(defaults))if(!['providers','projects'].includes(key) && data[key]===undefined)data[key]=copy(value);
      if(editor.build){
        const build=el('fieldset');build.append(el('legend',t('Build')));
        const values={buildJobs:{...(typeof data.buildJobs==='object'?data.buildJobs:{}),...editor.build}};
        fields(build,values.buildJobs,['buildJobs'],values);
        for(const input of build.querySelectorAll('input,select'))input.addEventListener('change',()=>{
          const next=values.buildJobs;
          if(!Number.isInteger(next.cores)||next.cores<1||next.cores>editor.cores||!Number.isInteger(next.parallel)||next.parallel<1||next.minFreeRamGB<=0){client.notice(t('Invalid build settings'),true);return;}
          delete next.coreShare;delete next.fixed;data.buildJobs=copy(next);changed();
        });form.append(build);
      }
      fields(form,data,[],data,new Set(['providers','projects','buildJobs']));
      form.append(button('Install /bzz',async()=>{const job=await client.action('tools/install');if(job){job.title='Install /bzz';jobs.set(job.id,job);renderJob(job);}}));
    } else if(tab==='Providers') {
      data.providers ||= [];
      form.append(button('Add provider',()=>{data.providers.push(newProvider());editor.dirty=true;render();}));
      data.providers.forEach((provider,index)=>{
        const card=el('fieldset');card.append(el('legend',provider.name || t('Providers')));
        const preset=el('select');preset.setAttribute('aria-label',t('CLI'));
        for(const [key,value] of Object.entries(bootstrap.presets)){const option=el('option',value.label);option.value=key;preset.append(option);}const custom=el('option',t('Custom CLI'));custom.value='custom';preset.append(custom);
        preset.value=['loginCommand','loginFiles','profileEnv','outputFormat'].some(name=>Object.hasOwn(provider,name))?'custom':Object.keys(bootstrap.presets).find(key=>bootstrap.presets[key].protocol===provider.protocol)||'custom';
        preset.onchange=()=>{const values=bootstrap.presets[preset.value];if(values){const {label,...rest}=values;Object.assign(provider,copy(rest));for(const name of ['loginCommand','loginFiles','profileEnv','outputFormat'])delete provider[name];}else Object.assign(provider,{loginCommand:'',loginFiles:[],profileEnv:{},outputFormat:provider.protocol});editor.dirty=true;render();};card.append(preset);
        const defaults=newProvider(provider.protocol);defaults.slots=data.slots || 2;
        const selectedCapabilities=effectiveCapabilities(provider);
        provider=mergeDefaults(defaults,provider);provider.capabilities=selectedCapabilities;data.providers[index]=provider;
        provider.allowedTools=toolsFromArgs(provider.args);
        fields(card,provider,['providers',index],data,new Set(['capabilities','capabilityThresholds']));
        const capabilities=el('fieldset');capabilities.append(el('legend',t('Capabilities')));
        for(const row of capabilityRows(provider,bootstrap.capabilities)){
          const line=el('label',undefined,'capability-threshold'),check=el('input'),number=el('input');
          check.type='checkbox';check.checked=row.enabled;number.type='number';number.min='1';number.max='100';number.step='1';number.value=row.threshold;number.required=true;
          const name=el('span',t(row.name)), unit=el('span',t('% usage limit'));
          const draw=()=>line.replaceChildren(check,name,...(check.checked?[number,unit]:[]));
          check.onchange=()=>{const selected=new Set(effectiveCapabilities(provider));if(check.checked)selected.add(row.name);else selected.delete(row.name);provider.capabilities=[...selected];provider.capabilitiesVersion=1;draw();changed();};
          number.oninput=()=>{provider.capabilityThresholds ||= {};provider.capabilityThresholds[row.name]=number.value.trim()===''?'':Number(number.value);changed();};
          draw();capabilities.append(line);
        }
        capabilities.append(button('Add own',()=>{const name=prompt(t('Capability name'));if(name?.trim()){provider.capabilities=effectiveCapabilities(provider);if(!provider.capabilities.includes(name.trim()))provider.capabilities.push(name.trim());provider.capabilitiesVersion=1;changed();render();}}));card.append(capabilities);
        card.append(button('Remove entry',()=>{if(confirm(t('Remove entry')+'?')){data.providers.splice(index,1);editor.dirty=true;render();}}));
        const status=providerStates.find(p=>p.name===provider.name);if(status){card.append(el('p',t(status.logged_in?'Login present':'Login missing')));card.append(valueView(status));}
        const models=editor.provider_models?.[provider.name];if(models)card.append(valueView(models));
        card.append(valueView((editor.benchmark || []).filter(row=>row.provider===provider.name)));
        const saved=!editor.dirty;
        for(const operation of ['enable','disable','test']){const node=button({enable:'Enable',disable:'Disable',test:'Test connection'}[operation],async()=>{const current=editor;await act('provider',{provider:provider.name,provider_action:operation});if(current===editor&&!editor.dirty)await loadEditor('');});node.disabled=node.disabled || !saved;node.dataset.savedOnly='true';card.append(node);}
        const login=button('Login',async()=>{
          const run=await client.action('login/start',{provider:provider.name});loginRuns.set(run.id,run);renderLogin(run);
        });login.disabled=login.disabled || !saved;login.dataset.savedOnly='true';card.append(login);
        const benchmark=button('Benchmark Start Context',async()=>{const job=await client.action('benchmark/start',{provider:provider.name},t('This starts the provider CLI twice per Project and uses login tokens. Confirm to continue.'));if(job){jobs.set(job.id,job);renderJob(job);}});benchmark.disabled=benchmark.disabled || !saved;benchmark.dataset.savedOnly='true';card.append(benchmark);
        form.append(card);
      });
    } else {
      editor.data=mergeDefaults({...bootstrap.project,...editor.project_defaults},data);
      if(data.upstreamRules===undefined && editor.upstream)editor.data.upstreamRules=editor.upstream.on;
      editor.place ||= {repository:editor.repository || '',plansFolder:''};
      fields(form,editor.place,[],editor.place);
      fields(form,editor.data,[],editor.data,new Set(['codeRepo','repo','rulesOff','upstreamRules']));
      const knowledge=el('fieldset');knowledge.append(el('legend',t('Knowledge base')));
      knowledge.append(el('p',t('The rule files every agent of this Project gets. A switched-off file is given to no agent.')));
      for(const rule of editor.rules || []){
        const label=el('label',undefined,'field'),toggle=el('input');toggle.type='checkbox';toggle.checked=!(editor.data.rulesOff || []).includes(rule.label);
        toggle.onchange=()=>{const off=new Set(editor.data.rulesOff || []);if(toggle.checked)off.delete(rule.label);else off.add(rule.label);editor.data.rulesOff=[...off];changed();};
        let text=t('%s, %d tokens').replace('%s',rule.path).replace('%d',rule.tokens);
        if(rule.native_clis?.length)text+=' - '+t('still loaded by %s itself').replace('%s',rule.native_clis.join(' / '));
        label.append(el('span',text),toggle);knowledge.append(label);
      }
      if(!(editor.rules || []).length)knowledge.append(el('p',t('No AGENTS.md or CLAUDE.md is checked in to the repository.')));
      if(editor.upstream){
        const label=el('label',undefined,'field'),toggle=el('input');toggle.type='checkbox';toggle.checked=!!editor.data.upstreamRules;toggle.disabled=!editor.upstream.usable;
        toggle.onchange=()=>{editor.data.upstreamRules=toggle.checked;changed();};label.append(el('span',t(bootstrap.labels.upstreamRules)),toggle);knowledge.append(label);
        if(editor.upstream.note)knowledge.append(el('p',t(editor.upstream.note)));
      }
      knowledge.append(el('p',t("HaiveQueen's own role texts are always added.")));form.prepend(knowledge);
    }
    // The unsaved marker survives live dashboard reads.
    const marker=el('span',editor.dirty?t('Unsaved changes'):'');marker.id='dirty-marker';toolbar.append(marker);
  }
  async function addProject() {
    if(editor?.dirty&&!confirm(t('Discard unsaved changes?')))return;
    const machine=await client.request('settings/view');
    detail('Add Project',null);
    const form=el('form'),name=el('input'),repository=el('input'),plansFolder=el('input');
    for(const [node,title] of [[name,'Name'],[repository,'Repository'],[plansFolder,'Plans folder (empty: the repository)']]){
      const label=el('label',undefined,'field');label.append(el('span',t(title)),node);form.append(label);node.required=node!==plansFolder;
    }
    form.append(button('Save',async()=>{
      if(!form.reportValidity())return;
      const data=copy(machine.data);data.projects ||= [];
      data.projects.push({name:name.value.trim(),workspace:(plansFolder.value || repository.value).trim()});
      await client.action('settings/save',{revision:machine.revision,data});
      if(plansFolder.value.trim()){
        const project=name.value.trim(),loaded=await client.request('settings/view?project='+encodeURIComponent(project));
        await client.action('settings/save',{project,revision:loaded.revision,data:loaded.data,place:{repository:repository.value.trim(),plansFolder:plansFolder.value.trim()}});
      }
      $('detail').close();overlay=null;generation++;editor=null;await client.refresh();tab='Projects';await loadEditor(name.value.trim());
    }));form.onsubmit=event=>event.preventDefault();$('detail-body').append(form);
  }
  function newProvider(protocol='claude') {
    const {label,...preset}=bootstrap.presets[protocol] || {protocol,command:protocol,args:[]};
    return {...copy(preset),name:'',access:'subscription',enabled:true,slots:2,use:'preferred',capabilities:['Coding','Queen'],beeMode:preset.beeMode || 'process',
      apiKey:'',address:'',contextWindow:0,contextLimit:0,allowedTools:[],
      classes:{light:{model:'',effort:'medium'},medium:{model:'',effort:'medium'},heavy:{model:'',effort:'medium'}},claudeTools:''};
  }
  function changed() {editor.dirty=true;const marker=$('dirty-marker');if(marker)marker.textContent=t('Unsaved changes');for(const node of view.querySelectorAll('[data-saved-only]'))node.disabled=true;}
  function fields(parent, object, path, root, omit=new Set()) {
    for(const [name,value] of Object.entries(object)){
      if(['password','capabilitiesVersion'].includes(name)||omit.has(name))continue;
      const full=[...path,name];
      if(value && typeof value==='object' && !Array.isArray(value)){
        const group=el('details');group.open=['web','classes','buildJobs'].includes(name);group.append(el('summary',pretty(name)));fields(group,value,full,root);parent.append(group);continue;
      }
      if(Array.isArray(value) && (value.some(item=>typeof item==='object') || ['providers','projects'].includes(name))){arrayEditor(parent,object,name,path,{},root);continue;}
      const label=el('label',undefined,'field');label.append(el('span',t(bootstrap.fieldLabels?.[tab]?.[name] || bootstrap.labels[name] || pretty(name))));
      const choices=bootstrap.choices[name] || (name==='access'?['subscription','api','llm']:name==='language'&&tab==='General'?['English','German']:null);
      let input;
      if(choices){input=el('select');for(const choice of [...new Set([...choices,value])]){const option=el('option',t(bootstrap.choiceLabels?.[name]?.[choice] || choice));option.value=choice;input.append(option);}input.value=value;}
      else if(Array.isArray(value)||String(value).includes('\n')){input=el('textarea');input.value=Array.isArray(value)?value.join('\n'):value;}
      else {input=el('input');input.type=typeof value==='boolean'?'checkbox':typeof value==='number'?'number':/apiKey|providerKey/i.test(name)?'password':'text';if(input.type==='checkbox')input.checked=value;else input.value=value ?? '';if(input.type==='number')input.step='any';}
      input.oninput=input.onchange=()=>{let next=input.value;if(typeof value==='boolean')next=input.checked;else if(typeof value==='number'){if(!input.value.trim()||!Number.isFinite(Number(input.value)))return;next=Number(input.value);}else if(Array.isArray(value))next=name==='args'?input.value.split('\n'):input.value.split('\n').filter(x=>x!=='');setValue(root,full,next);
        if(path[0]==='providers'){
          const provider=root.providers[path[1]];
          if(name==='allowedTools'||name==='args'){
            const other=name==='allowedTools'?'args':'allowedTools';
            if(name==='allowedTools')provider.args=withTools(provider.args,next);else provider.allowedTools=toolsFromArgs(next);
            const otherPath=JSON.stringify(['providers',path[1],other]);
            const otherInput=[...view.querySelectorAll('[data-path]')].find(node=>node.dataset.path===otherPath);
            if(otherInput)otherInput.value=provider[other].join('\n');
          }
          if(name==='model'){
            const offered=editor.provider_models?.[provider.name]?.models;
            if(offered?.length){const entry=provider.classes[path[3]];if(offered.includes(next))delete entry.customModel;else entry.customModel=true;}
          }
        }
        changed();};
      input.dataset.path=JSON.stringify(full);
      if(name==='model'||name==='effort'){
        const provider=path[0]==='providers'?root.providers[path[1]]:null;
        const offered=provider?(editor.provider_models?.[provider.name] || {}):{};
        const items=name==='model'?offered.models:offered.efforts;
        if(items?.length){const list=el('datalist');list.id='choices-'+Math.random().toString(36).slice(2);for(const item of items){const option=el('option');option.value=typeof item==='string'?item:item.id||item.name;list.append(option);}input.setAttribute('list',list.id);label.append(list);}
      }
      label.append(input);const help=bootstrap.fieldHelp?.[tab]?.[name] || bootstrap.help[name];if(help)label.append(el('small',t(help)));parent.append(label);
    }
    const add=button('Add setting',()=>{
      const name=prompt(t('Setting name'));if(!name || ['__proto__','constructor','prototype','password'].includes(name) || Object.hasOwn(object,name))return;
      const value=prompt(t('Value (JSON, or plain text)'));if(value===null)return;
      let parsed;try{parsed=JSON.parse(value);}catch{parsed=value;}
      object[name]=parsed;changed();render();
    });parent.append(add);
  }
  function arrayEditor(parent,object,name,path,template,root) {
    object[name] ||= [];
    const group=el('details');group.append(el('summary',pretty(name)));
    object[name].forEach((item,index)=>{const card=el('fieldset');fields(card,item,[...path,name,index],root);card.append(button('Remove entry',()=>{object[name].splice(index,1);changed();render();}));group.append(card);});
    group.append(button(name==='projects'?'Add Project':'Add entry',()=>{object[name].push(copy(template));changed();render();}));parent.append(group);
  }
  async function saveEditor() {
    const current=editor;
    for(const input of view.querySelectorAll('.capability-threshold input[type="number"]'))if(!input.disabled && !input.reportValidity())return;
    const sent=JSON.stringify([current.data,current.place]);
    const data=copy(current.data);
    if(!current.project)for(const provider of data.providers || [])delete provider.allowedTools;
    const loaded=await client.action('settings/save',{project:current.project || undefined,revision:current.revision,data,...(current.project?{place:current.place}:{})});
    if(!loaded)return;
    if(current!==editor)return;
    if(JSON.stringify([current.data,current.place])!==sent){current.revision=loaded.revision;current.dirty=true;client.notice(t('Saved. Newer edits are still unsaved.'));return;}
    editor={...loaded,project:current.project,dirty:false,rules:current.rules,upstream:current.upstream,workspace:current.workspace,place:current.place,project_defaults:current.project_defaults,provider_models:current.provider_models,benchmark:current.benchmark,build:current.build,cores:current.cores};
    if(!current.project && current.data.language && current.data.language!==bootstrap.language){location.reload();return;}
    render();void client.refresh();
    if(current.data.web)client.notice(t('Saved. Restart the web server to apply changed web settings.'));
  }
  async function openLive(row) {
    const project=row.project || row.apiary,agent=row.agent || row.name,id=JSON.stringify([project,agent]);
    if(liveRuns.has(id))return;
    const panel=el('section',undefined,'live-panel'),title=el('h2',project+' · '+agent),output=el('pre',undefined,'live-output');
    panel.append(title,button('Close',()=>{liveRuns.delete(id);panel.remove();}),output);$('live').append(panel);
    liveRuns.set(id,{project,agent,output,loading:false,initial:true});await pollLive(liveRuns.get(id));
  }
  async function pollLive(run) {
    if(run.loading)return;run.loading=true;
    try{const result=await client.request('live?'+new URLSearchParams({project:run.project,agent:run.agent}));const follow=run.initial || run.output.scrollHeight-run.output.scrollTop-run.output.clientHeight<25;
      run.output.replaceChildren();for(const [colour,text] of result.lines){run.output.append(el('div',text,colour));}if(!result.lines.length)run.output.append(el('span',t('No live output yet.')));if(follow)run.output.scrollTop=run.output.scrollHeight;run.initial=false;
    }catch(error){client.notice(error.message,true);}finally{run.loading=false;}
  }
  function renderLogin(run) {
    let panel=$('login-'+run.id);
    if(!panel){panel=el('section',undefined,'live-panel');panel.id='login-'+run.id;panel.append(el('h2',run.provider+' · '+t('Login')),el('pre'),el('div'));const form=el('form'),input=el('input');input.setAttribute('aria-label',t('Code or input'));input.autocomplete='off';const send=el('button',t('Send'));send.type='submit';form.append(input,send);form.onsubmit=async event=>{event.preventDefault();await runButton(send,async()=>{await client.action('login/input',{id:run.id,text:input.value});input.value='';});};panel.append(form);$('live').append(panel);}
    panel.querySelector('pre').textContent=run.output;
    const links=panel.querySelector('div');links.replaceChildren();
    for(const url of safeLinks(run.links || [])){const link=el('a',url);link.href=url;link.target='_blank';link.rel='noopener noreferrer';links.append(link,el('br'));}
    panel.querySelector('input').disabled=!run.running;panel.querySelector('button').disabled=!run.running;
    if(!run.running){links.append(el('p',t('Login finished')+' ('+run.exit+')'));panel.append(button('Close',()=>{panel.remove();loginRuns.delete(run.id);}));}
  }
  function renderJob(job) {
    let panel=$('job-'+job.id);if(!panel){panel=el('section');panel.id='job-'+job.id;$('live').append(panel);}
    panel.replaceChildren(el('h2',t(job.title || 'Benchmark Start Context')),valueView(job));
    if(!job.running)panel.append(button('Close',()=>{jobs.delete(job.id);panel.remove();}));
  }
  async function refreshLists() {
    const tasks=[];
    if(tab==='Plans'&&!planLoading){planLoading=true;tasks.push(client.request('plans').then(value=>{plans=value;if(tab==='Plans')render();}).finally(()=>planLoading=false));}
    if(tab==='Questions'&&!questionLoading){questionLoading=true;tasks.push(client.request('questions').then(value=>{
      if(JSON.stringify(questions)!==JSON.stringify(value)){// Keep drafts for surviving questions during a live update.
        const drafts=new Map([...view.querySelectorAll('[data-question]')].map(card=>[Number(card.dataset.question),{text:card.querySelector('textarea').value,checked:card.querySelector('input[type=checkbox]').checked,packages:card.querySelector('input:not([type=checkbox])')?.value}]));
        questions=value;if(tab==='Questions'){render();for(const card of view.querySelectorAll('[data-question]')){const draft=drafts.get(Number(card.dataset.question));if(draft){card.querySelector('textarea').value=draft.text;card.querySelector('input[type=checkbox]').checked=draft.checked;const packages=card.querySelector('input:not([type=checkbox])');if(packages)packages.value=draft.packages||'';}}}
      }
    }).finally(()=>questionLoading=false));}
    if(tab==='Providers'&&!providerLoading){providerLoading=true;tasks.push(client.request('providers').then(value=>{if(JSON.stringify(value)!==JSON.stringify(providerStates)){providerStates=value;if(tab==='Providers'&&!editor?.dirty)render();}}).finally(()=>providerLoading=false));}
    if(tab==='Dashboard'&&!updateLoading){updateLoading=true;tasks.push(client.request('updates').then(value=>{updateState=value;}).finally(()=>updateLoading=false));}
    await Promise.allSettled(tasks).then(results=>{for(const result of results)if(result.status==='rejected')client.notice(result.reason.message,true);});
  }
  async function tick() {
    if(!automaticChecking && Date.now()-lastAutomaticCheck>60000){
      lastAutomaticCheck=Date.now();automaticChecking=true;
      void client.request('updates/check',{automatic:true}).then(value=>{if(value)updateState=value;}).catch(error=>client.notice(error.message,true)).finally(()=>automaticChecking=false);
    }
    await Promise.allSettled([client.refresh(),refreshLists(),...Array.from(liveRuns.values(),pollLive),
      ...Array.from(loginRuns.values()).filter(run=>run.running&&!run.loading).map(async run=>{run.loading=true;try{const next=await client.request('login/status?id='+encodeURIComponent(run.id));loginRuns.set(run.id,next);renderLogin(next);if(!next.running){providerStates=await client.request('providers');if(tab==='Providers'&&!editor?.dirty)render();}}catch(error){client.notice(error.message,true);}finally{run.loading=false;}}),
      ...Array.from(jobs.values()).filter(job=>job.running&&!job.loading).map(async job=>{job.loading=true;try{const next=await client.request('jobs/status?id='+encodeURIComponent(job.id));next.title=job.title;jobs.set(job.id,next);renderJob(next);}finally{job.loading=false;}})]);
  }
  window.addEventListener('beforeunload',event=>{if(editor?.dirty){event.preventDefault();event.returnValue='';}});
  render();void tick();setInterval(()=>void tick(),2000);
  return client;
}
if(typeof module!=='undefined')module.exports={Client,RenderGate,planActions,setValue,safeLinks,toolsFromArgs,withTools,orchestratorDisabled,capabilityRows,mount};
if(typeof document!=='undefined')mount(document,JSON.parse(document.getElementById('bootstrap').textContent));
