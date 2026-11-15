function testTimingNotice(document,t,text,threshold,record=null) {
  const make=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node;};
  const rows=[];
  for(const line of text.split(/\r?\n/)){
    // Only explicit duration records, never overall run times or arbitrary status text.
    let m=line.match(/^\s*(\d+(?:\.\d+)?)s\s+(call|setup|teardown)\s+(.+)$/);
    if(m){rows.push({name:m[3]+' ('+m[2]+')',seconds:Number(m[1])});continue;}
    m=line.match(/^\s*(\d+(?:\.\d+)?)s\s+(test\S+\s+\(.+\))\s*$/);
    if(m){rows.push({name:m[2],seconds:Number(m[1])});continue;}
    m=line.match(/^\s*(\S+\.py)\s+exit=-?\d+\s+seconds=(\d+(?:\.\d+)?)\s*$/);
    if(m)rows.push({name:m[1],seconds:Number(m[2])});
  }
  if(record && !record.unavailable){rows.length=0;rows.push(...record.rows);}
  const slow=rows.filter(row=>Number.isFinite(row.seconds)&&row.seconds>=threshold).sort((a,b)=>b.seconds-a.seconds);
  const box=document.createElement('details');box.className='test-timing-notice';
  if(record && !record.unavailable){
    box.append(make('summary',t('Slow tests')+': '+record.count+' · ≥ '+threshold+' s'));
    box.append(make('p',t(record.complete?'Measured durations from the entire recorded console output.':'Indexing recorded test durations…')));
    if(!record.measured)box.append(make('p',t('No individual test durations available')));
    if(record.omitted)box.append(make('p',t('Additional measurements remain in the original log')+': '+record.omitted));
  } else box.append(make('summary',slow.length?t('Slow tests')+': '+slow.length+' · ≥ '+threshold+' s':t(rows.length?'No tests above the threshold in this excerpt':'No individual test durations available in this excerpt')));
  if(!record || record.unavailable)box.append(make('p',t('Measured durations in the loaded log excerpt only. A long duration does not prove unnecessary waiting. No additional tests are started.')));
  if(slow.length){const list=document.createElement('ul');for(const row of slow)list.append(make('li',row.name+' · '+row.seconds+' s'));box.append(list);}
  return box;
}
class LogPager {
  constructor(){this.run='';this.source='';this.restart();}
  restart(cursor=null){const carry=cursor&&cursor===this.cursor?this.continuation:'';this.cursor=cursor;this.text='';this.more=false;this.continuation=carry||'';}
  request(){return {run:this.run,source:this.source,cursor:JSON.stringify(this.cursor),continuation:this.continuation};}
  accept(result){
    if(result.reset || (this.run && this.run!==result.run)){this.restart();this.source=result.source||'';}
    this.run=result.run;this.cursor=result.cursor;this.more=result.more;
    this.continuation=result.continuation||'';
    this.text=(this.text+(result.output||'')).slice(-65536);
  }
}
function dashboardLoading(document,t) {
  const make=(tag,cls,text)=>{const node=document.createElement(tag);node.className=cls;if(text)node.textContent=t(text);return node;};
  const root=make('section','dashboard-loading');root.setAttribute('aria-busy','true');
  const status=make('div','loading-status','Loading your hive…');status.setAttribute('role','status');
  const emblem=make('span','loading-honeycomb');emblem.setAttribute('aria-hidden','true');status.append(emblem);root.append(status);
  const placeholders=make('div','loading-placeholders');placeholders.setAttribute('aria-hidden','true');
  const metrics=make('div','loading-metrics');
  for(let i=0;i<7;i++){const tile=make('div','loading-metric');tile.append(make('span','loading-block'),make('span','loading-line'));metrics.append(tile);}
  const columns=make('div','loading-columns'),main=make('div','loading-panel'),side=make('div','loading-sidebar');
  main.append(make('span','loading-line loading-title'));
  for(let i=0;i<5;i++)main.append(make('div','loading-row'));
  for(let i=0;i<3;i++){const panel=make('div','loading-panel');panel.append(make('span','loading-line loading-title'),make('div','loading-row'));side.append(panel);}
  columns.append(main,side);placeholders.append(metrics,columns);root.append(placeholders);return root;
}
function orchestrationFor(row,hives=[]) {
  if(row.kind!=='orch')return null;
  const info=row.orch||hives.find(plan=>plan.apiary===(row.apiary||row.project)&&plan.plan_path===row.plan_path)?.orch;
  if(!info)return null;
  const chain=info.chain||[],index=chain.findIndex(([,state])=>state==='running'||state==='red');
  return {...info,chain,n:info.n??(index>=0?index+1:chain.filter(([,state])=>state==='done').length),m:info.m??chain.length};
}
/* Browser UI, using only the platform DOM and the authenticated engine API. */
'use strict';
const copy = value => JSON.parse(JSON.stringify(value));
function isPackagePlaceholder(row) {
  return row.kind==='bee' && !row.agent && !row.embedded && (!row.name || row.name==='–');
}
function visibleHiveRows(rows) {
  return rows.filter(row=>!isPackagePlaceholder(row));
}
function planProcessGroups(plans,rows) {
  const visible=visibleHiveRows(rows);
  return plans.map(plan=>{
    const project=plan.apiary||plan.project;
    const processes=visible.filter(row=>(row.apiary||row.project)===project&&row.plan_path===plan.plan_path);
    return {key:JSON.stringify([project,plan.plan_path]),plan,processes,
      summary:{...plan,orch:processes.some(row=>row.kind==='orch')?null:plan.orch}};
  });
}
function pendingQueue(data,plans=[]) {
  const key=row=>JSON.stringify([row.project||row.apiary,(row.plan_path||'').replaceAll('\\','/')]);
  const active=new Set(plans.map(key)),seen=new Set();
  const items=(data.items||[]).filter(row=>{
    const status=String(row.status||'').toUpperCase(),id=key(row);
    if(!['DRAFT','SENT','QUEUED'].includes(status)||active.has(id)||seen.has(id))return false;
    seen.add(id);return true;
  }).map(row=>({...row,status:String(row.status).toUpperCase()}));
  return {items,queued:items.length,drafts:items.filter(row=>row.status==='DRAFT').length,pending:items.filter(row=>row.status!=='DRAFT').length};
}
function hiveTree(plans,rows,projects=[]) {
  const result=[],groups=planProcessGroups(plans,rows);
  const names=[...new Set([...projects.map(p=>p.name),...plans.map(p=>p.apiary||p.project)])].filter(Boolean);
  for(const project of names){
    const projectKey=JSON.stringify(['project',project]);
    result.push({key:projectKey,parent:null,type:'project',depth:0,row:{name:project,apiary:project}});
    for(const group of groups.filter(g=>(g.plan.apiary||g.plan.project)===project)){
      const planKey=JSON.stringify(['plan',project,group.plan.plan_path]);
      result.push({key:planKey,parent:projectKey,type:'plan',depth:1,row:group.summary});
      const processes=group.processes,keys=new Map(processes.map((r,i)=>[r,JSON.stringify(['process',project,group.plan.plan_path,r.agent||r.stop_agent||r.name,r.kind,r.task||''])]));
      const parents=new Map();
      for(const row of processes){
        const explicit=row.parent_agent||row.parent;
        const parent=explicit?processes.find(p=>p!==row&&(p.agent===explicit||p.name===explicit)):
          !['queen','orch'].includes(row.kind)?processes.find(p=>p.kind===(row.kind==='fix'?'orch':'queen')):null;
        parents.set(row,parent);
      }
      const visited=new Set();
      function append(row,parent,depth){
        if(visited.has(row))return;visited.add(row);
        result.push({key:keys.get(row),parent,type:row.kind,depth,row});
        for(const child of processes.filter(p=>parents.get(p)===row))append(child,keys.get(row),depth+1);
      }
      for(const row of processes.filter(p=>!parents.get(p)))append(row,planKey,2);
      for(const row of processes)if(!visited.has(row))append(row,planKey,2);
    }
  }
  return result;
}
function dashboardPlanHeight({available,planChrome,gap,header,rows,hiveHeight}) {
  const minimum=header+rows.slice(0,3).reduce((sum,height)=>sum+height,0)+1;
  const full=header+rows.reduce((sum,height)=>sum+height,0)+1;
  return Math.ceil(Math.max(minimum,Math.min(full,available-planChrome-gap-hiveHeight)));
}
function hiveCounts(rows) {
  return {queens:rows.filter(row=>row.kind==='queen').length,
    agents:rows.filter(row=>!['queen','orch'].includes(row.kind)&&!isPackagePlaceholder(row)).length,
    packages:rows.filter(isPackagePlaceholder).length};
}
function providerCli(provider,presets) {
  return ['loginCommand','loginFiles','profileEnv','outputFormat'].some(key=>Object.hasOwn(provider,key))?'custom':Object.keys(presets).find(key=>presets[key].protocol===provider.protocol)||'custom';
}
function applyProviderCli(provider,key,presets) {
  const preset=presets[key];
  if(preset){const {label,beeMode,...values}=preset;Object.assign(provider,copy(values));provider.beeMode ||= beeMode;for(const field of ['loginCommand','loginFiles','profileEnv','outputFormat'])delete provider[field];}
  else provider.outputFormat ??= '';
  provider.limit={...(typeof provider.limit==='object'?provider.limit:{}),source:(provider.access||'subscription')==='subscription'&&['claude','codex'].includes(key)?key:'none'};
}
function switchProviderAccess(provider,access,stash,presets,offered) {
  const old=provider.access||'subscription';
  if(old===access)return;
  const models=()=>Object.fromEntries(Object.entries(provider.classes||{}).map(([key,value])=>[key,Object.fromEntries(Object.entries(value).filter(([name])=>['model','customModel'].includes(name)))]));
  stash[old]={values:Object.fromEntries(['apiKey','address','contextWindow'].filter(key=>Object.hasOwn(provider,key)).map(key=>[key,provider[key]])),models:models()};
  for(const key of ['apiKey','address','contextWindow','server'])delete provider[key];
  provider.access=access;
  const held=stash[access] || (old!=='llm'&&access!=='llm'?{models:stash[old].models}:{});
  Object.assign(provider,copy(held.values||{}));
  let selectedModels=held.models||{},key=providerCli(provider,presets);
  if(!offered.includes(key)){key=offered[0];selectedModels={};}
  if(key!==providerCli(provider,presets))applyProviderCli(provider,key,presets);
  provider.limit={...(typeof provider.limit==='object'?provider.limit:{}),source:access==='subscription'&&['claude','codex'].includes(key)?key:'none'};
  for(const [name,entry] of Object.entries(provider.classes||{})){delete entry.model;delete entry.customModel;Object.assign(entry,copy(selectedModels[name]||{}));}
}
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
  if (row.status === 'DONE' || row.missing || row.recovery_error) return [];
  const out = [];
  if (row.status === 'PAUSED' && row.stopped) out.push('resume');
  if (['DRAFT', 'CANCELLED'].includes(row.status)) out.push('start');
  if (['SENT','PREPARING','QUEUED','RUNNING','WAITING','REVIEW'].includes(row.status)) out.push('stop-gracefully');
  if (row.has_hive && !row.stopped) out.push('force-stop');
  if (row.status === 'DRAFT' || row.stopped) out.push('cancel');
  if (row.status === 'CANCELLED' || row.stopped) out.push('back-to-draft');
  return out;
}
async function resolvePlanActions(row, request) {
  if (/^[A-Z]+$/.test(row.status||'')) return planActions(row);
  const plans=await request('plans');
  const match=plans.find(plan=>(plan.apiary||plan.project)===(row.apiary||row.project) && plan.plan_path===row.plan_path);
  return match?planActions(match):[];
}
function dashboardMetrics(tiles,format) {
  return [
    {key:'apiaries',label:'PROJECTS',value:String(tiles.apiaries??0),note:'active',icon:'t_apiaries'},
    {key:'hives',label:'PLANS',value:String(tiles.hives??0),note:'active',icon:'t_hives'},
    {key:'queens',label:'QUEENS',value:String(tiles.queens??0),note:'online',icon:'t_queens'},
    {key:'orchestrators',label:'Orchestrator',value:String(tiles.orchestrators??0),note:'active',icon:'icon_system'},
    {key:'bees',label:'BUSY AGENTS',value:(tiles.bees??0)+' / '+(tiles.bee_capacity??0),note:'active',icon:'t_bees'},
    {key:'io',label:'TOTAL I/O',value:format(tiles.tokens_in??0)+' / '+format(tiles.tokens_out??0),note:'in / out (today)',icon:'t_io'},
    {key:'start',label:'START CONTEXT',value:tiles.start_ctx?.saving==null?'no benchmark':tiles.start_ctx.saving+'%',note:'Start context per agent start',icon:'t_start'}
  ];
}
function dashboardRows(state, filter) {
  const hives=state.hives||[],bees=state.bees||[];
  if(!filter)return {hives,bees};
  const sameProject=row=>(row.apiary||row.project)===filter.project;
  const matches=row=>sameProject(row) && (filter.type==='project' || (row.plan_path||row.hive||row.name)===filter.plan);
  return {hives:hives.filter(matches),bees:bees.filter(row=>matches(row) && (filter.type!=='agent' || ['queen','orch'].includes(row.kind) || (filter.agent?row.agent===filter.agent:row.name===filter.name && row.task===filter.task)))};
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
    Object.assign(this, {csrf:'', fetch:(...args)=>fetch(...args), confirm:()=>false, notice:()=>{}, onState:()=>{}, onError:()=>{}, project:'', loading:false, refreshWanted:false}, options);
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
    this.pending.set(key,true);
    try {
      if (warning && !await this.confirm(warning)) {this.notice('Cancelled');return null;}
      this.notice('Working…');
      const result = await this.request(action, {...data, ...(warning ? {confirmed:true} : {})});
      this.notice(result?.message || 'Done');
      return result;
    } catch (error) { this.notice(error.message, true); throw error; }
    finally {this.pending.delete(key);}
  }
  async plansAction(action, warning) {
    const lock='plans/batch';
    if(this.pending.has(lock))return null;
    this.pending.set(lock,true);
    try {
      const selected=await this.request('plans/control-preview',{operation:action});
      if(!selected.length)return {count:0};
      const names=selected.map(row=>row.project+' / '+row.plan).join('\n');
      if(!await this.confirm(warning(selected.length)+'\n\n'+names))return null;
      return await this.request('plans/control',{operation:action,targets:selected,confirmed:true});
    } finally {this.pending.delete(lock);}
  }
  async refresh() {
    if (this.loading) { this.refreshWanted=true; return; }
    this.loading=true;
    const project = this.project;
    try {
      const value=await this.request('state' + (project ? '?project='+encodeURIComponent(project) : ''));
      if (project === this.project) {
        this.state=value; this.onState(value);
        if(value.web_listener_error && value.web_listener_error!==this.listenerError)this.notice(value.web_listener_error,true);
        this.listenerError=value.web_listener_error || '';
      }
      else this.refreshWanted=true;
    } catch(error) { this.onError(error); }
    finally {
      this.loading=false;
      if (this.refreshWanted) { this.refreshWanted=false; void this.refresh(); }
    }
  }
}
function credentialStatus(provider) {
  if(provider.error||provider.status_error)return {label:'Check access',tone:'error',icon:'!'};
  if(['device','bearer','token','pat','existing access'].includes(provider.method))return {label:'Access present',tone:'ok',icon:'\u2713'};
  return {label:'Not configured',tone:'neutral',icon:'\u2014'};
}
function loginStatus(status) {
  if(!status||typeof status.logged_in!=='boolean')return {label:'Login status unknown',tone:'neutral',icon:'?'};
  return status.logged_in?{label:'Login present',tone:'ok',icon:'\u2713'}:{label:'Login missing',tone:'warn',icon:'!'};
}
async function copyOutput(target, document, navigator) {
  const text=target.tagName==='TEXTAREA'?target.value:(target.innerText??target.textContent);
  if(navigator?.clipboard?.writeText){try{await navigator.clipboard.writeText(text);return;}catch(_error){/* HTTP or denied clipboard: use the selection fallback. */}}
  const active=document.activeElement, selection=document.getSelection?.();
  const ranges=selection?Array.from({length:selection.rangeCount},(_,i)=>selection.getRangeAt(i).cloneRange()):[];
  const start=active?.selectionStart,end=active?.selectionEnd;
  const temporary=document.createElement('textarea');temporary.value=text;
  temporary.style.cssText='position:fixed;left:-10000px;top:0';temporary.setAttribute('aria-hidden','true');
  // A modal dialog makes body siblings inert, so keep the fallback inside it.
  const host=target.closest?.('dialog[open]') || document.body;host.append(temporary);
  try{temporary.select();if(!document.execCommand('copy'))throw Error('Copy failed. Select the text and copy it manually.');}
  finally{temporary.remove();active?.focus?.({preventScroll:true});if(typeof start==='number')active.setSelectionRange?.(start,end);if(selection){selection.removeAllRanges();for(const range of ranges)selection.addRange(range);}}
}
function installCopyButtons(document,navigator,t,notice) {
  const decorated=new WeakSet();
  function decorate(root){
    const targets=[...(root.matches?.('pre,textarea')?[root]:[]),...(root.querySelectorAll?.('pre,textarea')||[])];
    for(const target of targets){
      if(decorated.has(target)||!target.isConnected||target.getAttribute('aria-hidden')==='true')continue;
      decorated.add(target);
      const bar=document.createElement('div');bar.className='copy-tools';
      const button=document.createElement('button');button.type='button';button.className='copy-output';
      const icon=document.createElement('span');icon.className='copy-icon';icon.setAttribute('aria-hidden','true');
      const label=document.createElement('span');label.textContent=t('Copy');
      button.setAttribute('aria-label',t('Copy text'));button.title=t('Copy text');button.append(icon,label);bar.append(button);
      const status=document.createElement('span');status.className='copy-status';status.setAttribute('role','status');bar.append(status);
      button.onclick=async event=>{event.preventDefault();event.stopPropagation();button.disabled=true;try{await copyOutput(target,document,navigator);status.textContent=t('Copied');}catch(error){status.textContent=t('Copy failed');notice(t(error.message),true);}finally{button.disabled=false;}setTimeout(()=>{status.textContent='';},2500);};
      target.parentNode.insertBefore(bar,target.tagName==='TEXTAREA'?target.nextSibling:target);
    }
  }
  decorate(document.body);
  const observer=new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes)if(node.nodeType===1)decorate(node);});
  observer.observe(document.body,{childList:true,subtree:true});return observer;
}
function settingsCategory(scope,key) {
  const groups=scope==='Projects'?{
    'Basics':['name','colour','priority'],
    'Agent rules':['ownerTopics'],
    'Git and cleanup':['integrationBranch','worktreeLocation','worktreePrefix','deleteFinishedWorktrees','deleteIntegratedBranches'],
    'Workflow and checks':['nextVersion','releasePostfix','slowTestSeconds'],
  }:{
    'Resources':['slots','buildJobs','ramReserveMb','minFreeRamGB','maxConcurrentCompileChecks'],
    'Agent control':['queenClass','diagnosisClass','beesPerQueen','escalateAfter','roundMinutes','hives','excludedProviders','providerRecheckMinutes'],
    'Web access':['web'],
    'Data and retention':['retention','exportDir','slowTestSeconds'],
    'Updates':['update'],
    'Basics':['language','owner'],
  };
  return Object.keys(groups).find(group=>groups[group].includes(key))||'Advanced';
}
function workflowSummary(data, t=x=>x) {
  const parts=[t('Merge')+': '+t(data.mergeMode==='git'?'Git provider':'Local')];
  for(const [key,title] of [['check','Check'],['build','Build']])if(data[key+'Enabled'])parts.push(t(title)+': '+t(data[key+'Mode']==='git'?'Git provider':'Local'));
  parts.push(t('Push')+': '+(data.integrationBranch||t('default branch')));
  for(const [key,title] of [['deploy','Deploy'],['release','Release']])if(data[key+'Enabled'])parts.push(t(title)+': '+t(data[key+'Mode']==='git'?'Git provider':'Local'));
  return parts.join(' → ');
}

function mount(document, bootstrap) {
  const $ = id => document.getElementById(id);
  const t = text => bootstrap.translations[text] || text;
  const viewLabels = {apiaries:'Projects',hives:'Plans',queens:'QUEENS',orchestrators:'Orchestrator',bees:'Agents',tin:'Tokens in',tout:'Tokens out',ctx:'CONTEXT',
    ctx_limit:'Context limit',start_ctx:'START CONTEXT',tokens_in:'TOTAL IN',tokens_out:'TOTAL OUT',bee_capacity:'Agent slots',
    apiary:'PROJECT',hive:'PLAN',task:'TASK',model:'MODEL',status:'STATUS',runtime:'RUNTIME',queen:'QUEEN',bees_running:'BUSY AGENTS',
    progress:'PROGRESS',done:'Done',implemented:'Implemented',rejected:'Rejected',waiting:'Waiting',total:'Total',name:'Name',
    started:'Started',waiting_for:'Waiting for',error:'Error'};
  const pretty = key => t(bootstrap.labels[key] || viewLabels[key] || key.replace(/([a-z])([A-Z])/g,'$1 $2').replace(/_/g,' '));
  const el = (tag, text, cls) => {const node=document.createElement(tag); if(text!==undefined) node.textContent=String(text); if(cls)node.className=cls; return node;};
  const statusBadge = status => {
    const badge=el('span',undefined,'access-status access-'+status.tone);
    const icon=el('span',status.icon);icon.setAttribute('aria-hidden','true');badge.append(icon,el('span',t(status.label)));return badge;
  };
  const button = (text, action) => {const node=el('button',t(text)); node.type='button'; renderGate.prepare(node); node.onclick=()=>runButton(node,action); return node;};
  async function runButton(node, action) {
    renderGate.begin(node);
    try { await action(); } catch(error) {client.notice(error.message,true);}
    finally {renderGate.end(node);if(client.state)renderStateNow(client.state);}
  }
  const confirmation=el('dialog',undefined,'confirmation-dialog');confirmation.id='confirmation-dialog';confirmation.setAttribute('aria-labelledby','confirmation-title');confirmation.setAttribute('aria-describedby','confirmation-message');document.body.append(confirmation);
  function requestConfirm(message){
    if(confirmation.open)return Promise.resolve(false);
    const title=el('h2',t('Confirm action'));title.id='confirmation-title';
    const text=el('p',message);text.id='confirmation-message';
    const footer=el('div',undefined,'confirmation-actions');
    const cancel=el('button',t('Cancel')),approve=el('button',t('Confirm'));
    cancel.type=approve.type='button';approve.className='confirm-primary';
    footer.append(cancel,approve);
    confirmation.replaceChildren(title,text,footer);confirmation.returnValue='cancel';
    return new Promise(resolve=>{
      let settled=false;
      const finish=accepted=>{
        if(settled)return;settled=true;
        confirmation.removeEventListener('close',closed);confirmation.removeEventListener('cancel',cancelled);
        if(confirmation.open)confirmation.close(accepted?'confirm':'cancel');
        resolve(accepted);
      };
      const closed=()=>{if(!confirmation.open)finish(confirmation.returnValue==='confirm');};
      const cancelled=event=>{event.preventDefault();finish(false);};
      // Resolve button actions immediately, even when rendering delays the close event.
      cancel.onclick=()=>finish(false);approve.onclick=()=>finish(true);
      confirmation.addEventListener('close',closed);confirmation.addEventListener('cancel',cancelled);
      confirmation.showModal();cancel.focus();
    });
  }
  function requestInput(message){
    const dialog=el('dialog',undefined,'confirmation-dialog'),form=el('form');
    const label=el('label',message),input=el('input');input.type='text';input.id='setting-input';label.htmlFor=input.id;
    dialog.setAttribute('aria-label',message);
    const footer=el('div',undefined,'confirmation-actions'),cancel=el('button',t('Cancel')),save=el('button',t('Save'));
    cancel.type='button';save.type='submit';save.className='confirm-primary';footer.append(cancel,save);
    form.append(label,input,footer);dialog.append(form);document.body.append(dialog);
    return new Promise(resolve=>{
      let settled=false;const finish=value=>{if(settled)return;settled=true;dialog.close();dialog.remove();resolve(value);};
      form.onsubmit=event=>{event.preventDefault();finish(input.value);};cancel.onclick=()=>finish(null);
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(null);});
      dialog.addEventListener('close',()=>finish(null));dialog.showModal();input.focus();
    });
  }
  function animateHeader(){
    const canvas=$('honeycomb');if(!canvas)return;
    const context=canvas.getContext('2d');if(!context)return;
    let width=0,height=0,frame,last=0;
    const radius=30,dy=Math.sqrt(3)*radius;
    function hex(x,y){
      context.beginPath();for(let i=0;i<6;i++){const a=i*Math.PI/3;const px=x+radius*Math.cos(a),py=y+radius*Math.sin(a);if(i)context.lineTo(px,py);else context.moveTo(px,py);}context.closePath();
    }
    function draw(time){
      context.clearRect(0,0,width,height);
      const seconds=time/1000,travel=(seconds*70)%(width+400)-200;
      const halo=context.createRadialGradient(travel,height*.5,0,travel,height*.5,210);
      halo.addColorStop(0,'rgba(255,182,20,.24)');halo.addColorStop(1,'rgba(255,182,20,0)');
      context.fillStyle=halo;context.fillRect(0,0,width,height);
      for(let column=0;column<width/45+1;column++)for(let row=-1;row<height/dy+1;row++){
        const x=column*45,y=row*dy+(column%2)*dy/2;
        const pulse=Math.pow((Math.sin(seconds*.65-column*.72+row*1.3)+1)/2,5);
        const wave=Math.exp(-Math.pow((x-travel+y*.6)/85,2));
        const glow=Math.min(1,pulse*.3+wave*.5);
        hex(x,y);context.fillStyle='rgba(248,176,24,'+(glow*.12)+')';context.fill();
        context.strokeStyle='rgba(255,201,48,'+(.13+glow*.72)+')';context.lineWidth=.8+glow*.7;
        context.shadowColor='#ffc62d';context.shadowBlur=glow*6;context.stroke();context.shadowBlur=0;
        // A light travels around selected cell edges, with a short fading tail.
        if((column+row*3)%9===0){
          const position=(seconds*.45+column*.17+row*.3+100)%6;
          for(let tail=6;tail>=0;tail--){
            const pos=(position-tail*.065+6)%6,index=Math.floor(pos),fraction=pos-index;
            const a=index*Math.PI/3,b=(index+1)*Math.PI/3;
            const px=x+radius*(Math.cos(a)*(1-fraction)+Math.cos(b)*fraction);
            const py=y+radius*(Math.sin(a)*(1-fraction)+Math.sin(b)*fraction);
            context.beginPath();context.arc(px,py,tail===0?1.9:1.2,0,Math.PI*2);
            context.fillStyle='rgba(255,225,135,'+(.9-tail*.12)+')';
            context.shadowColor='#ffb900';context.shadowBlur=tail===0?12:4;context.fill();
          }
          context.shadowBlur=0;
        }
      }
    }
    function resize(){width=canvas.clientWidth;height=canvas.clientHeight;const scale=Math.min(window.devicePixelRatio||1,2);canvas.width=width*scale;canvas.height=height*scale;context.setTransform(scale,0,0,scale,0,0);draw(performance.now());}
    function tick(time){if(time-last>=32){draw(time);last=time;}frame=requestAnimationFrame(tick);}
    function update(){cancelAnimationFrame(frame);if(!document.hidden)frame=requestAnimationFrame(tick);}
    new ResizeObserver(resize).observe(canvas);document.addEventListener('visibilitychange',update);resize();update();
  }
  animateHeader();
  const client=new Client({csrf:bootstrap.csrf, confirm:text=>requestConfirm(t(text)), notice:(text,bad=false)=>{ $('notice').textContent=t(text);$('notice').className=bad?'bad':'';},
    onUnauthorized:()=>{location.href='/login';}, onError:error=>client.notice(error.message,true),onState:renderState});
  const renderGate=new RenderGate(()=>renderStateNow(client.state));
  let tab='Dashboard', editor=null, plans=[], questions=[], providerStates=[], updateState={}, overlay=null;
  const questionDrafts=new Map();
  let questionFilter='open', questionFocus=null, questionNavigation=false, selectedThread=null, conversationMobile=false, planDraft=null;
  let releaseProject='', releaseData=null, releaseError='', releaseRequest=0;
  let planLoading=false, questionLoading=false, providerLoading=false, updateLoading=false;
  let lastAutomaticCheck=0, automaticChecking=false;
  let dashboardFilter=null;
  const collapsedHives=new Set();
  let generation=0, logCleared=0, logFilter='', showNoise=false, logFollow=true, selectedPlan='', selectedAgent='';
  const liveRuns=new Map(), loginRuns=new Map(), jobs=new Map();
  const view=$('app');
  $('detail').addEventListener('close',()=>{overlay=null;liveRuns.delete('detail');$('detail').classList.remove('entity-dialog');});
  const actions={start:'Start','stop-gracefully':'Stop gracefully','force-stop':'Force stop',resume:'Resume',cancel:'Cancel plan','back-to-draft':'Back to draft'};
  function detail(title, value) {
    liveRuns.delete('detail');$('detail').classList.remove('entity-dialog');overlay={title,value}; renderDetail(); if (!$('detail').open) $('detail').showModal();
  }
  const tokens = value => value==null?'–':new Intl.NumberFormat(bootstrap.language==='German'?'de':'en',{notation:'compact',maximumFractionDigits:1}).format(value);
  function meter(value, max=100, label='', colour='gold') {
    const node=el('progress',undefined,colour);node.max=Math.max(1,max);node.value=Math.max(0,Number(value)||0);node.setAttribute('aria-label',label);return node;
  }
  function tileContent(key, value) {
    const rows=key==='orchestrators'?(client.state?.bees||[]).filter(row=>row.kind==='orch'):Array.isArray(value)?value:value?.rows || [];
    const box=el('div',undefined,'tile-content');
    if(!rows.length){box.append(el('p',t(key==='start'?'no benchmark yet - run it in the settings':'none'),'dim'));return box;}
    if(key==='start'){
      box.append(el('p',t('Start context per agent start')));
      const scale=Math.max(1,...rows.flatMap(row=>[Number(row.own)||0,Number(row.hq)||0]));
      const groups={1:'Per provider (average over its Projects)',2:'Per Project (average over the providers measured there)',3:'Per provider within each Project'};
      let block,group;
      for(const row of rows){
        if(row.block!==block){block=row.block;group=null;box.append(el('h3',t(groups[block]||'START CONTEXT')));}
        if(row.group && row.group!==group){group=row.group;box.append(el('h4',group));}
        const comparison=el('div',undefined,'context-comparison');comparison.append(el('strong',row.name));
        if(row.missing){comparison.append(el('span',t('no benchmark yet - run it in the settings'),'dim'));}
        else{
          for(const [label,key,colour] of [['WITHOUT HaiveQueen','own','gold'],['WITH HaiveQueen','hq','cyan']]){
            const line=el('div',undefined,'comparison-bar');line.append(el('span',t(label)),meter(row[key],scale,t(label),colour),el('span',tokens(row[key])));comparison.append(line);
          }
          comparison.append(el('span',t('Saving')+' '+(row.saving>0?'+':'')+row.saving+'%','cyan'));
          if(row.date)comparison.append(el('small',row.date));
        }
        box.append(comparison);
      }
      return box;
    }
    const headings={apiaries:['Project','Tokens in','Tokens out'],hives:['Plan','Project','Tokens in','Tokens out','per finished package'],queens:['Queen','Plan','Project','Tokens in','Tokens out'],bees:['Agent','Plan','Project','Tokens in','Tokens out'],io:['MODEL','Tokens in','Tokens out','fresh in','cached in']};
    const columns={apiaries:['name','tin','tout'],hives:['name','apiary','tin','tout','per_package'],queens:['name','hive','apiary','tin','tout'],bees:['name','hive','apiary','tin','tout'],io:['model','tin','tout','fresh','cached']};
    const keys=columns[key];
    if(!keys)return valueView(rows);
    const display=rows.map(row=>Object.fromEntries(keys.map((field,index)=>[t(headings[key][index]),field==='per_package'?(row[field]?.map(tokens).join(' / ')||'–'):['tin','tout','fresh','cached'].includes(field)?tokens(row[field]):row[field]])));
    box.append(table(display));return box;
  }
  const tilePreview=el('aside',undefined,'tile-preview');tilePreview.hidden=true;tilePreview.id='tile-preview';tilePreview.setAttribute('role','tooltip');document.body.append(tilePreview);
  let previewTimer, scrollingPreview=false;
  function hideTilePreview(){clearTimeout(previewTimer);tilePreview.hidden=true;}
  // A stationary pointer must not activate help as content scrolls beneath it.
  function retainHelp(node){if(node.title){node.dataset.hoverHelp=node.title;node.removeAttribute('title');}}
  function scrollingHelp(){scrollingPreview=true;hideTilePreview();for(const node of document.querySelectorAll('[title]'))retainHelp(node);}
  document.addEventListener('scroll',scrollingHelp,{capture:true,passive:true});
  document.addEventListener('wheel',scrollingHelp,{capture:true,passive:true});
  document.addEventListener('touchmove',scrollingHelp,{capture:true,passive:true});
  document.addEventListener('pointermove',()=>{scrollingPreview=false;},{passive:true});
  document.addEventListener('pointerover',event=>{
    const node=event.target.closest?.('[title],[data-hover-help]');if(!node)return;
    retainHelp(node);if(scrollingPreview||event.pointerType!=='mouse')return;
    clearTimeout(previewTimer);previewTimer=setTimeout(()=>{
      if(scrollingPreview||!node.isConnected)return;
      tilePreview.replaceChildren(el('p',node.dataset.hoverHelp));tilePreview.hidden=false;
      const r=node.getBoundingClientRect();tilePreview.style.left=Math.max(8,Math.min(r.left,window.innerWidth-tilePreview.offsetWidth-8))+'px';tilePreview.style.top=Math.max(8,Math.min(r.bottom+6,window.innerHeight-tilePreview.offsetHeight-8))+'px';
    },600);
  });
  document.addEventListener('pointerout',event=>{if(event.target.closest?.('[data-hover-help]'))hideTilePreview();});
  function closeTilePreview(){clearTimeout(previewTimer);previewTimer=setTimeout(()=>{tilePreview.hidden=true;},180);}
  tilePreview.onpointerenter=()=>clearTimeout(previewTimer);tilePreview.onpointerleave=closeTilePreview;
  document.addEventListener('keydown',event=>{if(event.key==='Escape')hideTilePreview();});
  function showTile(title,key) {
    hideTilePreview();overlay={title,tile:key,value:client.state?.overlays?.[key]};renderDetail();if(!$('detail').open)$('detail').showModal();
  }
  function limitHelp(node,title,text){
    if(!text)return node;
    node.classList.add('limit-help');node.tabIndex=0;node.setAttribute('role','button');node.setAttribute('aria-label',title);node.dataset.help=text;
    const show=()=>{clearTimeout(previewTimer);tilePreview.replaceChildren(el('h2',title),el('p',text));tilePreview.hidden=false;const r=node.getBoundingClientRect();tilePreview.style.left=Math.max(8,Math.min(r.left,window.innerWidth-tilePreview.offsetWidth-8))+'px';tilePreview.style.top=Math.max(8,Math.min(r.bottom+6,window.innerHeight-tilePreview.offsetHeight-8))+'px';};
    node.onpointerenter=event=>{if(event.pointerType==='mouse'&&!scrollingPreview){clearTimeout(previewTimer);previewTimer=setTimeout(show,600);}};node.onpointerleave=closeTilePreview;node.onfocus=()=>{if(!scrollingPreview)show();};node.onblur=closeTilePreview;
    node.onclick=()=>{hideTilePreview();detail(title,text);};node.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();node.click();}};
    return node;
  }
  let stepLogTimer;
  function showStepLog(anchor,row,step){
    clearTimeout(stepLogTimer);
    detail(step+' · '+(row.hive||row.name||''),{});
    $('detail').classList.add('entity-dialog');
    const body=$('detail-body');body.className='step-log-detail';body.replaceChildren();
    const header=el('div',undefined,'entity-header'),title=el('h2',step+' · '+(row.hive||row.name||''));title.id='detail-title';
    header.append(el('small','HQ · '+(row.apiary||row.project||'')),title);
    const status=el('p',t('Loading…'),'step-log-status'),panel=el('section',undefined,'entity-console');
    panel.append(el('h3',step+' · '+t('Live')));
    const output=el('pre',undefined,'live-output');output.setAttribute('aria-label',step+' log');panel.append(output);body.append(header,status,panel);
    const sources=el('div',undefined,'step-log-sources'),runInfo=el('p',undefined,'step-log-run'),fileInfo=el('p',undefined,'dim');
    const sourceSelect=el('select');sourceSelect.setAttribute('aria-label','Log source');
    sources.append(el('label','Log source'),sourceSelect);body.insertBefore(runInfo,panel);panel.insertBefore(sources,output);panel.insertBefore(fileInfo,output);
    let latest=null, selected='',following=true,loading=false,revision=0,autoSource=true;
    const pager=new LogPager(),knownSources=new Map();
    const timings=el('div');panel.insertBefore(timings,output);
    const paging=el('div',undefined,'step-log-sources'),firstPage=el('button',t('Start of log')),nextPage=el('button',t('Next page')),followLive=el('button',t('Follow live'));
    for(const button of [firstPage,nextPage,followLive])button.type='button';
    paging.append(firstPage,nextPage,followLive);panel.insertBefore(paging,output);
    const date=value=>value?new Date(value).toLocaleString():t('Unknown');
    function display(){
      if(!latest)return;
      const file=knownSources.get(selected);
      const timingNotice=testTimingNotice(document,t,pager.text,latest.slow_test_seconds,latest.test_timings);
      timingNotice.open=timings.firstElementChild?.open||false;timings.replaceChildren(timingNotice);
      const follow=output.scrollTop+output.clientHeight>=output.scrollHeight-24;
      const text=([pager.text,!file&&latest.reason&&!pager.text.includes(latest.reason)?latest.reason:''].filter(Boolean).join('\n\n')||t('No log output available yet.'));
      fileInfo.textContent=(file?file.path+' · '+t('File updated')+': '+date(file.modified_at)+' · '+t(file.archived?'Archived snapshot from this run.':'File currently on disk; not an archived snapshot.'):t('Recorded console output for the selected run.'))+' · '+t('Recent output is limited; use Start of log and Next page for full history.');
      nextPage.disabled=!pager.more;followLive.setAttribute('aria-pressed',String(following));
      if(output.textContent!==text){output.textContent=text;if(follow)output.scrollTop=output.scrollHeight;}
    }
    function navigate(cursor,follow){revision++;following=follow;pager.restart(cursor);clearTimeout(stepLogTimer);void refresh();}
    sourceSelect.onchange=()=>{autoSource=false;selected=sourceSelect.value;pager.source=selected;navigate(null,true);};
    firstPage.onclick=()=>{autoSource=false;navigate(null,false);};
    nextPage.onclick=()=>navigate(pager.cursor,false);
    followLive.onclick=()=>{following=true;clearTimeout(stepLogTimer);void refresh();};
    const dialog=$('detail');
    async function refresh(){
      if(!dialog.open||!output.isConnected||loading)return;
      loading=true;const requestedRevision=revision;
      try{
        const result=await client.request('plan/step-log?'+new URLSearchParams({...key(row),step,...pager.request()}));
        if(!dialog.open||!output.isConnected||requestedRevision!==revision)return;
        status.textContent=t({done:'Completed',running:'Running',red:'Failed',todo:'Pending',waiting:'Waiting',cancelled:'Cancelled',unknown:'Unknown'}[result.status]||result.status)+(!result.historical&&result.exit_code!=null?' · Exit '+result.exit_code:'')+(result.historical?' · '+t('Previous run output'):'');
        status.className='step-log-status '+({done:'ok',running:'warn',red:'bad',todo:'dim',waiting:'warn',cancelled:'dim',unknown:'dim'}[result.status]||'dim');
        if(result.reset||(pager.run&&pager.run!==result.run)){knownSources.clear();selected=result.source||'';}
        pager.accept(result);latest=result;
        for(const item of result.references||[]){knownSources.set(item.source,item);if(knownSources.size>128)knownSources.delete(knownSources.keys().next().value);}
        if(autoSource&&result.references?.length){autoSource=false;selected=result.references[0].source;pager.source=selected;navigate(null,true);return;}
        runInfo.textContent=(result.historical?t('Previous run — not the current attempt'):result.status==='running'?t('Current run'):t('Latest recorded run'))+
          ' · '+t('Started')+': '+date(result.started_at)+' · '+t('Ended')+': '+date(result.ended_at)+(result.exit_code!=null?' · '+t('Run result')+': '+(result.exit_code===0?t('Completed'):t('Failed'))+' · Exit '+result.exit_code:'');
        panel.querySelector('h3').textContent=step+' · '+(result.historical?t('Previous run'):result.status==='running'?t('Live'):t('Recorded logs'));
        const choices=[{path:'',label:t('Console output')},...Array.from(knownSources.values()).map(item=>({path:item.source,label:t('Referenced file')+': '+item.path}))];
        if(!choices.some(item=>item.path===selected))selected='';
        const signature=JSON.stringify(choices);
        if(sourceSelect.dataset.choices!==signature){
          sourceSelect.replaceChildren(...choices.map(item=>{const option=el('option',item.label);option.value=item.path;return option;}));
          sourceSelect.dataset.choices=signature;
        }
        sourceSelect.value=selected;display();
      }catch(error){if(output.isConnected)status.textContent=error.message;}
      finally{
        loading=false;
        if(dialog.open&&output.isConnected&&(following||requestedRevision!==revision))stepLogTimer=setTimeout(refresh,requestedRevision!==revision||pager.more?100:2000);
      }
    }
    dialog.addEventListener('close',()=>clearTimeout(stepLogTimer),{once:true});refresh();
  }
  function orchestrationView(info,row){
    const box=el('div',undefined,'hq-pipeline');
    const chain=el('div',undefined,'hq-steps');
    for(const [name,state] of info.chain){
      const label={done:'Completed',running:'Running',red:'Failed',todo:'Pending',waiting:'Waiting'}[state]||state;
      const step=el('button',({done:'✓ ',running:'▶ ',red:'✕ ',todo:'○ ',waiting:'◷ '}[state]||'')+t(name),'hq-step '+state);
      step.type='button';step.title=t(name)+' · '+t(label);step.setAttribute('aria-label',step.title);step.onclick=()=>showStepLog(step,row,name);chain.append(step);
    }
    box.append(chain,el('small',`${info.n} / ${info.m}`+(info.phase==='fix'?' · '+t('Fix Agent'):''),'hq-step-count'));return box;
  }
  function limitsView(rows) {
    const box=el('div',undefined,'model-limits');
    for(const row of rows){
      const item=el('article',undefined,'model-limit'),provider=row.name||row.provider;
      const name=el('div',undefined,'limit-name');name.append(el('strong',provider));
      if(row.next)name.append(limitHelp(el('span',t('NEXT'),'next-provider'),t('NEXT'),t('Provider selected for the next assignment.')));
      name.append(el('small',row.model));
      const reserve=row.reserve||{},slots=row.slots||{},weekly=row.weekly||{};
      const explanation=el('div',undefined,'limit-explanation');explanation.append(el('small',t('Quota reserve'),'quota-label'));
      explanation.append(limitHelp(el('span',reserve.text||row.remaining||'–',reserve.level||'dim'),t('Quota reserve'),[reserve.hover,reserve.age].filter(Boolean).join(' · ')));
      const capacity=el('div',undefined,'limit-capacity');
      const slotTip=t('Agent slots of %s: %d of %d in use. A filled cell is a running agent, an empty cell a free slot.').replace('%s',provider).replace('%d',slots.used||0).replace('%d',slots.total||0);
      if(slots.offline||slots.none){capacity.append(limitHelp(el('span',slots.offline||slots.none,slots.offline?'bad':'dim'),provider,slots.hover||slots.reason||slotTip));}
      else{
        const cells=el('div',undefined,'slot-cells');cells.setAttribute('aria-hidden','true');
        for(let i=0;i<Math.min(slots.total||0,16);i++)cells.append(el('span',undefined,'slot'+(i<(slots.used||0)?' used':'')+(slots.blocked?' blocked':'')));
        const slotLine=el('div',undefined,'slot-line');slotLine.append(cells,el('small',`${slots.used||0} / ${slots.total||0}`+(slots.extra?' +'+slots.extra:'')));
        capacity.append(limitHelp(slotLine,t('Agent slots'),[slotTip,slots.hover,slots.reason].filter(Boolean).join(' ')));
      }
      if(slots.offline){item.append(name,explanation,capacity);box.append(item);continue;}
      if(slots.running)capacity.append(el('small',slots.running));if(slots.reason)capacity.append(el('small',slots.reason,'bad'));
      const windows=el('div',undefined,'quota-windows');
      if(weekly.text){const week=el('div');if(weekly.pct!=null)week.append(meter(weekly.pct,100,weekly.text,weekly.level));week.append(el('small',weekly.text));windows.append(limitHelp(week,t('Weekly usage'),[t('Used quota in the 7-day window; the date is its reset time.'),weekly.text,reserve.age].filter(Boolean).join(' ')));}
      if(row.five){const five=el('div'),match=/5h (\d+)/.exec(row.five);if(match){const pct=Number(match[1]);five.append(meter(pct,100,row.five,pct<60?'ok':pct<=85?'warn':'bad'));}five.append(el('small',row.five));windows.append(limitHelp(five,t('5-hour usage'),t('Used quota in the 5-hour window; the time is its reset time.')+' '+row.five));}
      capacity.append(windows);item.append(name,explanation,capacity);box.append(item);
    }
    if(!rows.length)box.append(el('p',t('none'),'dim'));return box;
  }
  function planProgress(row) {
    const box=el('div',undefined,'plan-progress');const track=el('span',undefined,'segmented-progress');
    track.setAttribute('role','progressbar');track.setAttribute('aria-label',t('PROGRESS'));track.setAttribute('aria-valuemin','0');track.setAttribute('aria-valuemax','100');track.setAttribute('aria-valuenow',String(row.progress||0));
    for(let i=0;i<7;i++)track.append(el('i',undefined,i<Math.round((row.progress||0)*7/100)?'filled':''));
    box.append(track,el('span',`${row.done??0}/${row.total??0}`));
    box.title=[t('PROGRESS')+': '+(row.progress||0)+'%',...['done','implemented','rejected','waiting'].map(key=>pretty(key)+': '+(row[key]||0)),row.waiting_reasons||''].filter(Boolean).join(' · ');
    const details=el('details');details.append(el('summary',t('Details')),el('div',box.title));box.append(details);return box;
  }
  function renderDetail() {
    if (!overlay)return;
    const heading=el('h2',t(overlay.title));heading.id='detail-title';
    $('detail-body').replaceChildren(heading,overlay.tile?tileContent(overlay.tile,overlay.value):valueView(overlay.value));
  }
  $('close-detail').onclick=()=>{$('detail').close();overlay=null;};
  function valueView(value) {
    if (value === null || value === undefined) return el('span','–','dim');
    if (typeof value !== 'object')return el('span',value);
    if (Array.isArray(value)) {
      if(value.length===2 && typeof value[0]==='string' && ['text','dim','gold','cyan','green','red','yellow','blue','warn','bad','ok'].includes(value[1]))return el('div',value[0],value[1]);
      if(value.length===2 && typeof value[0]==='string' && Array.isArray(value[1])){
        const group=el('div',undefined,'status-group');group.append(el('h3',t(value[0])),valueView(value[1]));return group;
      }
      const box=el('div');
      if(value.every(item=>item && typeof item==='object' && !Array.isArray(item)))box.append(table(value));
      else for(const item of value){const line=el('div');line.append(valueView(item));box.append(line);}
      return box;
    }
    const list=el('dl');
    for(const [key,item] of Object.entries(value)){list.append(el('dt',pretty(key)),el('dd'));list.lastChild.append(valueView(item));}
    return list;
  }
  function filterLink(text,type,row) {
    const link=el('a',text,'dashboard-link');link.href='#dashboard';
    link.onclick=event=>{
      event.preventDefault();
      dashboardFilter={type,label:text,project:row.apiary||row.project,plan:row.plan_path||row.hive||row.name,agent:row.agent,name:row.name,task:row.task};
      tilePreview.hidden=true;$('detail').close();render();
      view.querySelector('.clear-filter')?.focus({preventScroll:true});
    };
    return link;
  }
  function table(rows, columns, extra, options={}) {
    function missingValue(label){
      const icon=el('span',undefined,'missing-value-icon');icon.title=t(label);icon.setAttribute('role','img');icon.setAttribute('aria-label',t(label));return icon;
    }
    const wrap=el('div',undefined,options.tree?'hive-tree-cells':'table-scroll'), table=el(options.tree?'div':'table'), head=el('tr'), body=el(options.tree?'div':'tbody');
    columns=columns || [...new Set(rows.flatMap(row=>Object.keys(row)))];
    for(const key of columns)head.append(el('th',t(options.labels?.[key]||pretty(key))));
    if(extra && !options.compact)head.append(el('th',t('Actions')));
    const thead=el('thead');thead.append(head);if(!options.tree)table.append(thead);table.append(body);
    for(const [index,row] of rows.entries()){const tr=el(options.tree?'div':'tr');tr.dataset.kind=row.kind||'';for(const key of columns){const td=el(options.tree?'div':'td');td.dataset.column=key;td.dataset.label=t(options.labels?.[key]||pretty(key));
      if(options.tree && !options.hierarchy && ['model','ctx','tokens'].includes(key)){}
      else if(options.tree && key==='task' && !options.hierarchy)td.append(planProgress(row));
      else if(key==='messages')td.append(planMessageButton(row));
      else if(key==='number')td.append(el('span',index+1));
      else if(options.hierarchy && isPackagePlaceholder(row) && key==='name'){
        const label=filterLink(t('Package'),'plan',row);label.prepend(el('span','└ ','tree-branch'));
        td.append(label);tr.classList.add('child-agent');
      }
      else if(options.hierarchy && isPackagePlaceholder(row) && ['ctx','tokens','runtime','model'].includes(key))td.append(el('span','–','dim'));
      else if(key==='name' && options.compact){
        const label=filterLink(row.name,options.hierarchy?(['queen','orch'].includes(row.kind)?'queen':'agent'):'plan',row);
        const menu=button('⋯',()=>options.hierarchy?openAgent(row):openPlan(row));menu.className='row-menu';menu.textContent='';menu.title=t('Open details');menu.setAttribute('aria-label',t('Open details')+' · '+(options.hierarchy && row.kind==='queen'?t('Queen')+' · ':'')+row.name);
        label.classList.add('row-name');
        if(options.hierarchy){
          const parent=rows.some(other=>['queen','orch'].includes(other.kind) && other!==row && (other.apiary||other.project)===(row.apiary||row.project) && other.plan_path===row.plan_path);
          if(['queen','orch'].includes(row.kind)){
            if(row.kind==='orch')label.textContent='HQ';
            const role=el('span',row.kind==='queen'?'Queen · ':'','parent-symbol');
            if(row.kind==='orch'){const icon=el('span',undefined,'system-icon icon-icon_system');icon.setAttribute('aria-hidden','true');role.append(icon);}
            if(row.kind==='queen'){const crown=el('span',undefined,'queen-icon');crown.setAttribute('aria-hidden','true');role.prepend(crown);}label.prepend(role);
          }
          else if(options.tree || parent || row.kind==='fix'){label.prepend(el('span',row.kind==='fix'?'Fix Agent · ':row.embedded?'Subagent · ':'Agent · ','tree-branch'));tr.classList.add('child-agent');}
        }
        td.append(label,menu);
      }
      else if(options.compact && ['apiary','hive','queen'].includes(key) && row[key] && !['–','-'].includes(row[key])){
        td.append(filterLink(row[key],{apiary:'project',hive:'plan',queen:'queen'}[key],row));
        if(['apiary','queen','hive'].includes(key)){
          const menu=button('⋯',()=>{
            const project=row.apiary||row.project;
            const actions=el('div',undefined,'actions');
            if(key==='hive'){return openPlan(client.state.hives?.find(plan=>plan.apiary===project && plan.plan_path===row.plan_path)||{...row,name:row.hive});}
            if(key==='apiary'){
              detail(project,client.state.projects?.find(item=>item.name===project)||{name:project});
              actions.append(button('Settings',()=>{$('detail').close();selectTab('Projects',project);}));
            }else{
              const queen=client.state.bees?.find(item=>['queen','orch'].includes(item.kind) && (item.apiary||item.project)===project && item.plan_path===row.plan_path);
              return openAgent(queen||{...row,name:row.queen,kind:'queen',agent:row.queen_agent,model:row.queen_model,ctx:row.queen_ctx,ctx_limit:row.queen_ctx_limit,tin:row.queen_tin,tout:row.queen_tout});
            }
            $('detail-body').prepend(actions);
          });
          menu.className='row-menu';menu.textContent='';menu.title=t('Open details');menu.setAttribute('aria-label',t('Open details')+' · '+t({apiary:'Project',queen:'Queen',hive:'Plan'}[key])+' · '+row[key]);td.append(menu);
        }
      }
      else if(['model','queen'].includes(key) && (!row[key] || ['\u2013','-'].includes(row[key]))){td.append(missingValue(key==='model'?'No model':'No Queen assigned'));}
      else if(key==='tokens')td.append(el('span',tokens(row.tin)+' / '+tokens(row.tout),'cyan token-values'));
      else if(key==='bees_running')td.append(el('span',`${row.bees_running||0}/${row.bees_total??row.bees_running??0}`));
      else if(key==='task' && row.kind==='orch' && orchestrationFor(row,client.state.hives)){td.append(orchestrationView(orchestrationFor(row,client.state.hives),row));}
      else if(key==='task' && row.step){td.classList.add('task-progress');const inline=el('div',undefined,'task-inline');inline.append(el('span',row.task),meter((row.step_frac||0)*100,100,row.step),el('small',row.step,'cyan'));td.append(inline);}
      else if(key==='progress' && Number.isFinite(Number(row[key]))){td.append(planProgress(row));}else if(key==='ctx'){td.classList.add('context-cell');td.append(row.ctx_limit?meter(100*(row.ctx||0)/row.ctx_limit,100,t('CONTEXT')):missingValue('No context value available'));if(row.ctx_limit)td.append(el('small',Math.round(100*(row.ctx||0)/row.ctx_limit)+'%'));td.title=tokens(row.ctx)+' / '+tokens(row.ctx_limit);}
      else if(key==='status' && row.missing){td.append(el('span',t('Plan file missing'),'warn'));}
      else if(key==='status' && row.orch?.chain && !row.paused && !options.hierarchy){td.append(orchestrationView(row.orch,row));}
      else if(key==='status'){const state=String(row[key]||'');const tone=/^(running|active)$/i.test(state)?'running':/fail|error|reject/i.test(state)?'error':/wait|paus|stop|pending/i.test(state)?'waiting':'neutral';td.append(el('span',t(state),'state-badge state-'+tone));
        if(row.paused && !options.hierarchy){const resume=button('Resume',async()=>{
          const available=await resolvePlanActions(row,route=>client.request(route));
          if(!available.includes('resume'))throw Error(t('The plan cannot resume yet. Refresh its status.'));
          await act('plan/resume',key(row),t('Resume')+' '+row.name+'?');
        });resume.classList.add('plan-resume');td.append(resume);}}
      else td.append(valueView(row[key]));tr.append(td);}if(extra && !options.compact){const td=el('td');td.className='row-actions';td.dataset.label=t('Actions');extra(td,row);tr.append(td);}body.append(tr);}
    if(!rows.length){const tr=el('tr'),td=el('td',t('none'),'dim');td.colSpan=columns.length+1;tr.append(td);body.append(tr);}
    wrap.append(table);return wrap;
  }
  function section(title, parent=view) {
    const node=el('section'),heading=el('h2',title==='QUEENS AND AGENTS'?'Hive':t(title));node.dataset.panel=title;
    const icon={ 'ACTIVE PLANS':'panel_icon','QUEENS AND AGENTS':'t_queens',Limits:'icon_system',SYSTEM:'icon_system',Hives:'t_queens',Queue:'icon_queue',STATUS:'panel_icon',LOG:'icon_logs'}[title];
    if(icon){const image=el('span',undefined,'panel-icon icon-'+icon);image.setAttribute('aria-hidden','true');heading.prepend(image);}
    node.append(heading);parent.append(node);return node;
  }
  async function act(action,data,warning) {const result=await client.action(action,data,warning);if(result){void client.refresh();void refreshLists();}return result;}
  function key(row) {return {project:row.project || row.apiary,plan:row.plan_path};}
  async function selectTab(name, project) {
    if(editor?.dirty && !await requestConfirm(t('Discard unsaved changes?')))return;
    generation++;editor=null;tab=name;
    for(const node of $('tabs').querySelectorAll('[data-tab]'))node.setAttribute('aria-selected',String(node.dataset.tab===tab));
    const inSettings=['Projects','Providers','Git connections','General'].includes(tab);
    settingsToggle.classList.toggle('active',inSettings);
    settingsMenu.hidden=true;settingsToggle.setAttribute('aria-expanded','false');
    render();void refreshLists();
    if(tab==='Releases'){releaseProject ||= client.state?.projects?.[0]?.name || '';void loadReleases();}
    if(['Projects','Providers','Git connections','General'].includes(tab))void loadEditor(tab==='Projects' ? (project??client.state?.projects?.[0]?.name) : '');
  }
  for(const name of ['Dashboard','Plans','Questions','Logs','Releases']){
    const node=button(name,()=>selectTab(name));node.dataset.tab=name;node.setAttribute('role','tab');node.setAttribute('aria-selected',String(name===tab));$('tabs').append(node);
  }
  const settingsMenu=el('div',undefined,'settings-subnav');settingsMenu.id='settings-subnav';settingsMenu.hidden=true;settingsMenu.setAttribute('role','group');settingsMenu.setAttribute('aria-label',t('Settings'));
  const settingsToggle=button('Settings',()=>{
    settingsMenu.hidden=!settingsMenu.hidden;settingsToggle.setAttribute('aria-expanded',String(!settingsMenu.hidden));
  });
  settingsToggle.id='settings-toggle';settingsToggle.setAttribute('aria-expanded','false');settingsToggle.setAttribute('aria-controls','settings-subnav');
  const settingsGroup=el('div',undefined,'settings-menu');settingsGroup.append(settingsToggle,settingsMenu);$('tabs').append(settingsGroup);
  for(const name of ['Projects','Providers','Git connections','General']){
    const node=button(({General:'System',Providers:'AI providers'})[name]||name,()=>selectTab(name));node.dataset.tab=name;node.setAttribute('role','tab');node.setAttribute('aria-selected','false');settingsMenu.append(node);
  }
  document.addEventListener('click',event=>{if(!settingsGroup.contains(event.target)){settingsMenu.hidden=true;settingsToggle.setAttribute('aria-expanded','false');}});
  settingsToggle.addEventListener('keydown',event=>{
    if(event.key==='ArrowDown'){event.preventDefault();settingsMenu.hidden=false;settingsToggle.setAttribute('aria-expanded','true');settingsMenu.querySelector('button').focus();}
    if(event.key==='Escape'){settingsMenu.hidden=true;settingsToggle.setAttribute('aria-expanded','false');}
  });
  settingsMenu.addEventListener('keydown',event=>{if(event.key==='Escape'){settingsMenu.hidden=true;settingsToggle.setAttribute('aria-expanded','false');settingsToggle.focus();}});
  function displayVersion(version) { return 'v'+String(version || '0').replace(/^v/i,''); }
  function renderState(state) {
    renderGate.render();
  }
  function renderStateNow(state) {
    $('version').textContent=t('Running %s').replace('%s',displayVersion(state.version));
    renderControls();renderUpdates();
    const questionTab=$('tabs').querySelector('[data-tab="Questions"]');
    const count=Math.max(0,Number(state.queue?.open??state.queue?.questions??0));
    questionTab.querySelector('.question-badge')?.remove();
    if(count){const badge=el('span',count,'question-badge');questionTab.append(badge);}
    questionTab.setAttribute('aria-label',t('Questions')+' ('+count+')');
    if(tab==='Dashboard' || tab==='Logs')render();
    if(overlay?.tile){overlay.value=state.overlays?.[overlay.tile];renderDetail();}
  }
  function renderControls() {
    const state=client.state||{},label=state.orchestrator?.label||'',toolbar=el('div',undefined,'toolbar');
    const status=el('div',undefined,'orchestrator-status '+({RUNNING:'is-running',STOPPING:'is-stopping',STOPPED:'is-stopped'}[label]||'is-stopped'));
    status.append(el('span',t('Orchestrator'),'status-name'),el('span',t(label||'…'),'status-value'));
    const menu=el('details',undefined,'controls-menu');menu.open=!!$('controls').querySelector('details')?.open;
    menu.append(el('summary',t('Controls')));const panel=el('div',undefined,'controls-popover');
    const orchestrator=el('div',undefined,'control-group');orchestrator.append(el('h3',t('Orchestrator')));
    for(const action of ['start','stop-gracefully','force-stop']){
      const node=button(actions[action],()=>act(action,{},action==='force-stop'?state.controls?.force_warning:t(actions[action]+' HaiveQueen?')));
      node.disabled=node.disabled||orchestratorDisabled(action,label);if(action==='force-stop')node.classList.add('danger-action');orchestrator.append(node);
    }
    const planControls=el('div',undefined,'control-group');planControls.append(el('h3',t('PLANS')));
    for(const action of ['stop-gracefully','force-stop','resume']){
      const warnings={'stop-gracefully':'Pause %s plans? Running work continues; no new work starts.',
        'force-stop':'Force stop %s plans? Their running work will end immediately.',
        resume:'Resume %s paused plans?'};
      const node=button(actions[action],async()=>{
        try {const result=await client.plansAction(action,count=>t(warnings[action]).replace('%s',count));
          if(result)client.notice(t('Plans processed: %s').replace('%s',result.count));
        } finally {void client.refresh();void refreshLists();}
      });
      if(action==='force-stop')node.classList.add('danger-action');planControls.append(node);
    }
    const web=el('div',undefined,'control-group');web.append(el('h3',t('Web server')),
      button('Stop',async()=>{if(await client.action('web/stop',{},t('Stop the web server? The browser will disconnect.')))client.notice(t('Web server stopped.'));}),
      button('Restart',async()=>{await client.action('web/restart',{},t('Restart the web server? The browser will reconnect.'));}));
    panel.append(orchestrator,planControls,web,button('Logout',async()=>{const response=await fetch('/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':client.csrf},body:'{}'});if(response.ok)location.href='/login';else throw Error(t('Logout failed'));}));
    menu.append(panel);toolbar.append(status,menu);$('controls').replaceChildren(toolbar);
  }
  document.addEventListener('click',event=>{const menu=$('controls').querySelector('details');if(menu && !menu.contains(event.target))menu.open=false;});
  $('controls').addEventListener('keydown',event=>{if(event.key==='Escape'){const menu=$('controls').querySelector('details');if(menu){menu.open=false;menu.querySelector('summary').focus();}}});
  function render() {
    const sameTab=view.dataset.tab===tab,pageX=window.scrollX,pageY=window.scrollY,navigatingQuestion=tab==='Questions' && questionNavigation;
    const positions=sameTab?[...view.querySelectorAll('.table-scroll,.hives-list,.log,details')].map(node=>({left:node.scrollLeft,top:node.scrollTop,open:node.open})):[];
    const focused=document.activeElement,selection=focused?.selectionStart,editingFilter=sameTab && focused?.placeholder===t('Filter');
    if(sameTab)view.style.minHeight=view.getBoundingClientRect().height+'px';
    view.dataset.tab=tab;view.replaceChildren();
    view.className=tab==='Dashboard'?'dashboard':tab==='Logs'?'logs-view':'editor-view';
    if(tab==='Dashboard')renderDashboard();
    if(tab==='Logs')renderLog();
    if(tab==='Plans')renderPlans();
    if(tab==='Questions')renderQuestions();
    if(tab==='Releases')renderReleases();
    if(['Projects','Providers','Git connections','General'].includes(tab))renderEditor();
    [...view.querySelectorAll('.table-scroll,.hives-list,.log,details')].forEach((node,index)=>{if(positions[index] && node.tagName==='DETAILS')node.open=positions[index].open;});
    fitDashboard();
    [...view.querySelectorAll('.table-scroll,.hives-list,.log,details')].forEach((node,index)=>{const position=positions[index];if(position){node.scrollLeft=position.left;if(!node.classList.contains('log')||!logFollow)node.scrollTop=position.top;}});
    view.style.minHeight='';
    if(sameTab && !navigatingQuestion)window.scrollTo({left:pageX,top:pageY,behavior:'instant'});
    if(editingFilter){const input=view.querySelector('input[placeholder]');input?.focus({preventScroll:true});input?.setSelectionRange(selection,selection);}
  }
  function projectSelect(onchange, selected, all=true) {
    const select=el('select');select.setAttribute('aria-label',t('Project'));
    if(all){const option=el('option',t('all'));option.value='';select.append(option);}
    for(const project of client.state?.projects || []){const option=el('option',project.name);option.value=project.name;select.append(option);}
    select.value=selected || '';select.onchange=()=>onchange(select.value);return select;
  }
  async function loadReleases() {
    const project=releaseProject,request=++releaseRequest;releaseData=null;releaseError='';
    if(tab==='Releases')render();
    if(!project)return;
    try{const data=await client.request('releases?'+new URLSearchParams({project}));if(request===releaseRequest)releaseData=data;}
    catch(error){if(request===releaseRequest)releaseError=error.message;}
    finally{if(request===releaseRequest&&tab==='Releases')render();}
  }
  function renderReleases() {
    const box=section('Releases');box.classList.add('releases-panel');
    const toolbar=el('div',undefined,'release-toolbar'),label=el('label',t('Project'));
    label.append(projectSelect(value=>{releaseProject=value;void loadReleases();},releaseProject,false));
    toolbar.append(label,button('Reload',loadReleases));box.append(toolbar);
    const empty=(title,message,bad=false)=>{const state=el('div',undefined,'release-empty');state.setAttribute('role',bad?'alert':'status');state.append(el('h3',t(title)),el('p',t(message),'dim'));box.append(state);};
    if(!releaseProject){empty('Select a project','Choose a project to see its published releases.');return;}
    if(releaseError){empty('Could not load releases',releaseError,true);box.append(button('Try again',loadReleases));return;}
    if(!releaseData){empty('Loading releases…','Fetching release history for the selected project.');return;}
    const releases=releaseData.releases||[];
    if(!releases.length){empty('No releases yet',releaseData.reason||'This project has no published releases.');return;}
    if(releaseData.reason)box.append(el('p',t(releaseData.reason),'dim'));
    box.append(el('p',releases.length+' '+t('Releases'),'dim'));
    const list=el('div',undefined,'release-list');box.append(list);
    for(const row of releases){
      const card=el('article',undefined,'release-card'),heading=el('div',undefined,'release-heading');
      heading.append(el('h3',row.version||row.tag),el('span',t(row.prerelease?'Prerelease':'Stable'),row.prerelease?'release-badge warn':'release-badge ok'));card.append(heading);
      const facts=el('dl',undefined,'release-facts');facts.append(el('dt',t('Date')),el('dd',row.date||'–'),el('dt',t('Commit')));
      const commit=el('dd',row.commit||'–','release-commit');facts.append(commit);card.append(facts);
      if(row.prerelease&&row.full_tag)card.append(button('Publish as full release',async()=>{
        const job=await client.action('releases/promote',{project:releaseProject,tag:row.tag,confirmed:true},t('Publish as full release')+' '+row.full_tag+'?');
        if(job){job.title='Publish as full release';jobs.set(job.id,job);renderJob(job);}
      }));
      list.append(card);
    }
  }
  function renderDashboard() {
    const state=client.state;
    if(!state){view.append(dashboardLoading(document,t));return;}
    const filtered=dashboardRows(state,dashboardFilter);
    if(dashboardFilter){
      const filterBar=el('div',undefined,'dashboard-filter');filterBar.setAttribute('role','status');
      filterBar.append(el('span',t('Filter')+' · '+t({project:'Project',plan:'Plan',queen:'Queen',agent:'Agent'}[dashboardFilter.type])+': '+dashboardFilter.label));
      const clear=button('Clear filter',()=>{dashboardFilter=null;render();});clear.className='clear-filter';filterBar.append(clear);view.append(filterBar);
    }
    const tiles=el('div',undefined,'tiles');
    for(const item of dashboardMetrics(state.tiles||{},tokens)){
      const tile=item.key,title=t(item.label),metric=button('',()=>showTile(title,tile));
      metric.dataset.tile=tile;metric.setAttribute('aria-haspopup','dialog');
      const icon=el('span',undefined,'dashboard-icon icon-'+item.icon);icon.setAttribute('aria-hidden','true');
      const content=el('span',undefined,'metric-copy');content.append(el('span',title,'metric-label'),el('strong',t(item.value)),el('small',t(item.note)));
      metric.append(icon,content);tiles.append(metric);
    }
    view.append(tiles);
    const active=section('Hives');active.classList.add('hives-panel');
    const tree=hiveTree(filtered.hives,filtered.bees,dashboardFilter?[]:state.projects||[]);
    const list=el('ul',undefined,'hives-list hives-tree');active.append(list);
    const header=el('li',undefined,'hive-tree-heading');
    for(const title of ['Project / Plan / Process','MODEL','Progress / Task','CONTEXT','TOKENS','STATUS','RUNTIME'])header.append(el('span',t(title)));
    list.append(header);
    const nodes=new Map(tree.map(node=>[node.key,node]));
    function hidden(node){const parent=nodes.get(node.parent);return !!parent&&(collapsedHives.has(parent.key)||hidden(parent));}
    const rendered=[];
    function updateTree(){for(const {node,line,toggle} of rendered){line.hidden=hidden(node);if(toggle){const open=!collapsedHives.has(node.key);toggle.textContent=open?'▾':'▸';toggle.setAttribute('aria-expanded',String(open));}}}
    for(const node of tree){
      const line=el('li',undefined,'hive-tree-row hive-tree-'+node.type);line.style.setProperty('--depth',node.depth);line.dataset.key=node.key;
      const hasChildren=tree.some(child=>child.parent===node.key);
      let toggle;
      if(node.type==='project'){
        const name=el('div',undefined,'hive-project-name');name.append(filterLink(node.row.name,'project',node.row));
        const info=button('',()=>{detail(node.row.name,state.projects?.find(p=>p.name===node.row.name)||node.row);const actions=el('div',undefined,'actions');actions.append(button('Settings',()=>{$('detail').close();selectTab('Projects',node.row.name);}));$('detail-body').prepend(actions);});info.className='row-menu';info.title=t('Open details');info.setAttribute('aria-label',t('Open details')+' · '+t('Project')+' · '+node.row.name);name.append(info);line.append(name);
        line.append(el('span',`${tree.filter(child=>child.parent===node.key).length} ${t('Plans')}`,'dim hive-project-count'));
      }else{
        const cells=table([node.row],['name','model','task','ctx','tokens','status','runtime'],null,{compact:true,hierarchy:node.type!=='plan',tree:true,labels:{task:node.type==='plan'?'PROGRESS':'TASK',tokens:'TOKENS'}});
        line.append(cells);
      }
      if(hasChildren){toggle=button('',()=>{if(collapsedHives.has(node.key))collapsedHives.delete(node.key);else collapsedHives.add(node.key);updateTree();});toggle.className='hive-tree-toggle';toggle.setAttribute('aria-label',t('Expand or collapse')+' · '+node.row.name);line.prepend(toggle);}
      rendered.push({node,line,toggle});list.append(line);
    }
    updateTree();
    if(!tree.length)list.append(el('li',t('No plans to display.'),'dim'));
    const grid=el('div',undefined,'grid');view.append(grid);
    section('Limits',grid).append(limitsView(state.limits || []));
    const system=section('SYSTEM',grid);
    for(const [label,key] of [['CPU','cpu'],['RAM','ram_pct'],['Disk','disk_pct']]){
      const line=el('div',undefined,'system-meter'),value=state.system?.[key];
      const meter=el('progress');meter.max=100;meter.value=Number(value)||0;meter.setAttribute('aria-label',label);
      const labelNode=el('span',label);const icon=el('span',undefined,'system-icon icon-icon_'+label.toLowerCase());icon.setAttribute('aria-hidden','true');labelNode.prepend(icon);meter.className=value>=86?'bad':value>=70?'warn':'ok';line.append(labelNode,meter,el('span',value==null?'–':Math.round(value)+'%'));system.append(line);
    }
    const queue=section('Queue',grid),queueData=pendingQueue(state.queue||{},state.hives||[]);
    const queueCount=el('p',undefined,'queue-count');queueCount.append(el('strong',String(queueData.queued??0)),el('span',`${queueData.pending??0} ${t('Queued')} \u00b7 ${queueData.drafts??0} ${t('Drafts')}`));queue.append(queueCount);
    const queueList=el('ol',undefined,'queue-list');
    for(const item of queueData.items||[]){
      const entry=el('li'),heading=el('div',undefined,'queue-entry-heading');
      const name=item.title||(item.plan_path||'').split(/[\\/]/).pop()?.replace(/\.md$/i,'')||item.branch||('#'+item.id);
      const link=button(name,()=>openQueuedPlan(item));link.className='queue-plan-link';link.title=item.plan_path;link.setAttribute('aria-haspopup','dialog');
      heading.append(link,el('span',t({DRAFT:'Draft',SENT:'Submitted',QUEUED:'Queued'}[item.status]||item.status),'queue-state '+(item.status==='DRAFT'?'dim':'warn')));
      entry.append(heading,el('small',item.project,'dim'));
      queueList.append(entry);
    }
    if(!queueList.children.length)queue.append(el('p',t('Queue is empty.'),'dim'));
    else queue.append(queueList);
    const columns=el('div',undefined,'dashboard-columns');
    const primary=el('div',undefined,'dashboard-primary');
    const secondary=el('div',undefined,'dashboard-secondary');
    primary.append(active);
    secondary.append(grid.querySelector('[data-panel="Limits"]'),grid.querySelector('[data-panel="SYSTEM"]'),grid.querySelector('[data-panel="Queue"]'));
    grid.remove();columns.append(primary,secondary);view.append(columns);
    renderUpdates();
  }
  function fitDashboard(){
    if(tab!=='Dashboard')return;
    const primary=view.querySelector('.dashboard-primary');
    const list=primary?.querySelector('.hives-list');
    if(!list)return;
    primary.style.height='';
    list.style.maxHeight=window.innerWidth<=1100?'':Math.max(200,window.innerHeight-Math.max(0,list.getBoundingClientRect().top)-16)+'px';
  }
  window.addEventListener('resize',fitDashboard);
  let dashboardScrollFrame=0;
  window.addEventListener('scroll',()=>{
    if(!dashboardScrollFrame)dashboardScrollFrame=requestAnimationFrame(()=>{dashboardScrollFrame=0;fitDashboard();});
  },{passive:true});
  const dashboardResize=new ResizeObserver(()=>requestAnimationFrame(fitDashboard));
  for(const node of [document.querySelector('header'),$('tabs'),$('notice')])dashboardResize.observe(node);
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
  function planDocument(text) {
    const doc=el('div',undefined,'plan-document');
    const inline=(node,value)=>{
      for(const part of String(value).split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g)){
        if(part.startsWith('**')&&part.endsWith('**'))node.append(el('strong',part.slice(2,-2)));
        else if(part.startsWith('`')&&part.endsWith('`'))node.append(el('code',part.slice(1,-1)));
        else {const link=part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);if(link){const a=el('a',link[1]);a.href=link[2];a.target='_blank';a.rel='noopener noreferrer';node.append(a);}else node.append(document.createTextNode(part));}
      }
      return node;
    };
    const lines=String(text).split(/\r?\n/);let list=null,code=null,paragraph=[];
    const flush=()=>{if(paragraph.length){doc.append(inline(el('p'),paragraph.join(' ')));paragraph=[];}};
    for(let i=0;i<lines.length;i++){
      const line=lines[i];
      if(/^\s*```/.test(line)){flush();list=null;if(code)code=null;else{code=el('pre',undefined,'plan-code');doc.append(code);}continue;}
      if(code){code.textContent+=line+'\n';continue;}
      if(line.includes('|')&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1]||'')){
        flush();list=null;const wrap=el('div',undefined,'plan-document-table'),grid=el('table');wrap.append(grid);doc.append(wrap);
        const cells=value=>value.trim().replace(/^\||\|$/g,'').split('|').map(x=>x.trim());
        const head=el('tr');for(const value of cells(line))head.append(inline(el('th'),value));const thead=el('thead');thead.append(head);grid.append(thead);i++;
        const body=el('tbody');grid.append(body);while((lines[i+1]||'').includes('|')){const row=el('tr');for(const value of cells(lines[++i]))row.append(inline(el('td'),value));body.append(row);}continue;
      }
      const heading=line.match(/^(#{1,6})\s+(.+)$/),bullet=line.match(/^\s*(?:[-*]|(\d+)\.)\s+(.+)$/);
      if(heading){flush();list=null;doc.append(inline(el('h'+Math.min(6,heading[1].length+1)),heading[2]));}
      else if(bullet){flush();const tag=bullet[1]?'OL':'UL';if(!list||list.tagName!==tag){list=el(tag.toLowerCase());doc.append(list);}list.append(inline(el('li'),bullet[2]));}
      else if(!line.trim()){flush();list=null;}
      else if(/^\s*---+\s*$/.test(line)){flush();doc.append(el('hr'));}
      else{list=null;paragraph.push(line);}
    }
    flush();return doc;
  }
  function packageCards(packages) {
    const grid=el('div',undefined,'plan-packages');
    for(const item of packages){
      const card=el('article',undefined,'plan-package'),head=el('div',undefined,'package-heading');
      const labels={done:['Completed','ok'],implemented:['Implemented','blue'],working:['In progress','warn'],waiting:['Waiting','warn'],rejected:['Rejected','bad'],open:['Not started','dim']};
      const [label,tone]=labels[item.status]||[item.status,'dim'];
      head.append(el('code',item.id),el('span',t(label),'package-status '+tone));card.append(head,el('h4',item.title));
      if(item.step){const [n,m]=item.step.split('/').map(Number);const progress=el('div',undefined,'package-progress');progress.append(meter(n,m,item.step),el('span',t('Last reported steps')+' '+item.step));card.append(progress);}
      const breakdown=el('details',undefined,'package-substeps');
      breakdown.append(el('summary',t('Individual steps')+(item.substeps?.length?' · '+item.substeps.length:'')));
      const list=el('ol');
      for(const step of item.substeps||[]){
        const line=el('li',undefined,step.status),label={done:'Completed',current:'Current step',pending:'Not yet reported complete',unknown:'No matching progress report'}[step.status]||step.status;
        line.append(el('span',step.status==='done'?'✓':String(step.number),'substep-number'),el('span',step.text),el('small',t(label)));list.append(line);
      }
      breakdown.append(item.substeps?.length?list:el('p',t('No saved step descriptions available for this package.'),'dim'));card.append(breakdown);
      if(item.task)card.append(el('p',item.task,'package-task'));
      if(item.reason)card.append(el('p',item.reason,'warn'));
      const facts=el('dl',undefined,'package-facts');
      for(const [label,value] of [['Agent',item.agent],['Depends on',(item.depends_on||[]).join(', ')],['Requirements',(item.requirements||[]).join(', ')],['Verification',item.verify]])if(value)facts.append(el('dt',t(label)),el('dd',value));
      card.append(facts);grid.append(card);
    }
    if(!packages.length)grid.append(el('p',t('No work packages defined yet.'),'dim'));
    return grid;
  }
  function entityFrame(row,kind) {
    detail(row.name,row);$('detail').classList.add('entity-dialog');
    const body=$('detail-body');body.replaceChildren();body.className='';
    const header=el('div',undefined,'entity-header'),heading=el('h2',row.name);heading.id='detail-title';
    header.append(el('small',t(kind)+' · '+(row.apiary||row.project||'')),heading,el('span',t(row.missing?'Plan file missing':row.status||'–'),'entity-status'));
    const actions=el('div',undefined,'entity-actions');
    const layout=el('div',undefined,'entity-layout'),summary=el('div',undefined,'entity-summary'),content=el('div',undefined,'entity-content');
    const facts=el('dl',undefined,'entity-facts');
    for(const key of ['hive','task','model','queen','runtime','step','waiting_reasons'])if(row[key])facts.append(el('dt',pretty(key)),el('dd',row[key]));
    summary.append(facts);
    const all=el('details',undefined,'entity-all');all.append(el('summary',t('Details')),valueView(row));summary.append(all);
    layout.append(summary,content);body.append(header,actions,layout);return {body,actions,summary,content};
  }
  function entityStats(parent,values){
    const stats=el('div',undefined,'entity-stats');for(const [label,value] of values){const item=el('div');item.append(el('small',t(label)),el('strong',value??'–'));stats.append(item);}parent.prepend(stats);
  }
  async function attachConsole(parent,row){
    const panel=el('section',undefined,'entity-console');panel.append(el('h3',t('Live')+' · '+(row.name||'')));
    const output=el('pre',t('Loading…'),'live-output');output.setAttribute('aria-label',t('Live'));panel.append(output);parent.append(panel);
    if(!row.agent && row.kind!=='orch'){output.textContent=t('No live output yet.');return;}
    const run={project:row.project||row.apiary,agent:row.agent,kind:row.kind,hive:row.hive,output,loading:false,initial:true};liveRuns.set('detail',run);await pollLive(run);
  }
  async function openAgent(row){
    const kind={queen:'Queen',orch:'Orchestrator',fix:'Fix Agent'}[row.kind]||'Agent';
    const frame=entityFrame(row,kind);
    frame.actions.append(button('Filter log',()=>{selectedAgent=row.agent||row.name;selectedPlan='';$('detail').close();selectTab('Logs');}));
    frame.body.classList.add('agent-detail');
    frame.summary.querySelector('.entity-all')?.remove();
    const facts=frame.summary.querySelector('.entity-facts');facts.replaceChildren();
    for(const [label,value] of [['PLAN',row.hive],['MODEL',row.model],['RUNTIME',row.runtime]])if(value)facts.append(el('dt',t(label)),el('dd',value));
    const tabs=el('div',undefined,'entity-tabs');tabs.setAttribute('role','tablist');
    const layout=frame.summary.parentElement,technical=el('div',undefined,'agent-technical');
    technical.hidden=true;technical.id='agent-technical';technical.setAttribute('role','tabpanel');layout.id='agent-overview';layout.setAttribute('role','tabpanel');
    const technicalFacts=el('dl',undefined,'entity-facts');
    const labels={agent:'Agent ID',plan_path:'Plan file',kind:'Role',ctx:'Context used',ctx_limit:'Context limit',stop_agent:'Stop target'};
    const shown=new Set(['name','apiary','project','hive','task','model','runtime','status','step','step_frac','tin','tout']);
    for(const [key,value] of Object.entries(row))if(!shown.has(key)&&value!=null&&value!=='')technicalFacts.append(el('dt',t(labels[key]||pretty(key))),el('dd',undefined));
    let index=0;for(const [key,value] of Object.entries(row))if(!shown.has(key)&&value!=null&&value!=='')technicalFacts.querySelectorAll('dd')[index++].append(valueView(value));
    technical.append(el('h3',t('Technical information')),technicalFacts);
    const panels=[layout,technical],tabButtons=[];
    function choose(index){panels.forEach((panel,i)=>{panel.hidden=i!==index;tabButtons[i].setAttribute('aria-selected',String(i===index));tabButtons[i].tabIndex=i===index?0:-1;});}
    for(const [index,label] of ['Overview','Technical information'].entries()){
      const tab=el('button',t(label));tab.type='button';tab.id='agent-tab-'+index;tab.setAttribute('role','tab');tab.setAttribute('aria-controls',panels[index].id);panels[index].setAttribute('aria-labelledby',tab.id);tab.onclick=()=>choose(index);
      tab.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?1:1-index;choose(next);tabButtons[next].focus();}};
      tabs.append(tab);tabButtons.push(tab);
    }
    layout.before(tabs);layout.after(technical);choose(0);
    entityStats(frame.summary,[['CONTEXT',row.ctx_limit?Math.round(100*(row.ctx||0)/row.ctx_limit)+'%':'–'],['Tokens in',tokens(row.tin)],['Tokens out',tokens(row.tout)]]);
    const task=el('section',undefined,'current-task');task.append(el('h3',t('TASK')),el('p',row.task||'–'));
    const orchestration=orchestrationFor(row,client.state.hives);if(orchestration)task.append(orchestrationView(orchestration,row));
    if(row.step)task.append(meter((row.step_frac||0)*100,100,row.step),el('span',row.step));frame.content.append(task);
    const plan=client.state.hives?.find(plan=>plan.apiary===(row.apiary||row.project) && plan.plan_path===row.plan_path);
    if(plan)frame.actions.append(button('Plan',()=>openPlan(plan)));
    if(row.stop_agent){const stop=button('Force stop',()=>act('agent/force-stop',{project:row.project||row.apiary,agent:row.stop_agent},t('Stop agent %s (%s)? The package stays stopped until you resume it.').replace('%s',row.name).replace('%s',row.task)));stop.disabled=stop.disabled||!!row.stop_disabled;stop.title=row.stop_disabled||'';frame.actions.append(stop);}
    if(row.stopped)frame.actions.append(button('Resume package',()=>act('package/resume',{...key(row),package:row.package||row.task},t('Resume package')+'?')));
    let source=row;
    if(!row.agent && row.embedded){source=client.state.bees?.find(parent=>parent.kind==='queen' && parent.apiary===row.apiary && parent.plan_path===row.plan_path)||row;}
    await attachConsole(frame.content,source);
  }
  async function openQueuedPlan(item) {
    try {
      const available=await client.request('plans');
      const plan=available.find(row=>row.apiary===item.project && row.plan_path===item.plan_path);
      if(!plan)throw new Error(t('Plan details are no longer available.'));
      await openPlan(plan);
    } catch(error) { detail(t('Plan details'),error.message); }
  }
  async function openPlan(row) {
    const frame=entityFrame(row,'Plan'),target=frame.body;
    frame.actions.append(button('Filter log',()=>{selectedPlan=row.name;selectedAgent='';$('detail').close();selectTab('Logs');}));
    frame.body.classList.add('plan-detail');frame.summary.querySelector('.entity-all')?.remove();
    const actionMenu=el('details',undefined,'plan-action-menu');actionMenu.append(el('summary',t('Actions')));const bar=el('div',undefined,'plan-action-list');actionMenu.append(bar);frame.actions.append(actionMenu);
    const layout=frame.summary.parentElement,readerPanel=el('div'),technical=el('div',undefined,'plan-technical'),tabs=el('div',undefined,'entity-tabs');tabs.setAttribute('role','tablist');
    const panels=[layout,readerPanel,technical],buttons=[];
    panels.forEach((panel,index)=>{panel.id='plan-panel-'+index;panel.setAttribute('role','tabpanel');});
    function select(index){panels.forEach((panel,i)=>{panel.hidden=i!==index;buttons[i].setAttribute('aria-selected',String(i===index));buttons[i].tabIndex=i===index?0:-1;});}
    for(const [index,label] of ['Overview','Plan text','Technical information'].entries()){
      const tab=el('button',t(label));tab.type='button';tab.id='plan-tab-'+index;tab.setAttribute('role','tab');tab.setAttribute('aria-controls',panels[index].id);panels[index].setAttribute('aria-labelledby',tab.id);tab.onclick=()=>select(index);
      tab.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?2:(index+(e.key==='ArrowRight'?1:2))%3;select(next);buttons[next].focus();}};tabs.append(tab);buttons.push(tab);
    }
    layout.before(tabs);layout.after(readerPanel,technical);select(0);
    const extra=Object.fromEntries(Object.entries(row).filter(([key])=>!['name','apiary','project','queen','runtime','done','total','implemented','waiting','rejected','progress','status','hive','task','model','step','waiting_reasons'].includes(key)));
    const facts=el('dl',undefined,'plan-technical-facts');
    for(const [label,value] of [['Plan path',row.plan_path],['Queen',row.queen_agent],['Model',row.queen_model],['Tokens in',tokens(row.hive_tin)],['Tokens out',tokens(row.hive_tout)]])if(value)facts.append(el('dt',t(label)),el('dd',value));
    technical.append(el('h3',t('Technical information')),facts);
    const raw=el('details',undefined,'plan-raw');raw.append(el('summary',t('Raw metadata')),el('pre',JSON.stringify(extra,null,2)));technical.append(raw);
    entityStats(frame.summary,[['Completed packages',(row.done??0)+' / '+(row.total??0)],['Implemented',row.implemented??0],['Waiting',row.waiting??0],['Rejected',row.rejected??0]]);
    const progress=planProgress(row);progress.querySelectorAll('details').forEach(node=>node.remove());frame.summary.prepend(progress);
    const reader=el('section',undefined,'plan-reader');reader.append(el('h3',t('Plan text')));
    const text=el('div',t('Loading…'),'plan-text');reader.append(text);readerPanel.append(reader);frame.content.append(el('h3',t('Work packages')),el('p',t('Loading…'),'package-loading'));
    if(row.orch)frame.summary.append(el('h3',t('Integration')),orchestrationView(row.orch,row));
    void client.request('plan/detail?'+new URLSearchParams(key(row))).then(result=>{
      if(!target.contains(reader))return;
      text.replaceChildren(planDocument(result.body??result.text??t('No plan text available.')));if(result.text){const source=el('details',undefined,'plan-raw');source.append(el('summary',t('Plan source')),el('pre',result.text,'plan-source'));technical.append(source);}
      frame.content.querySelector('.package-loading')?.remove();frame.content.append(packageCards(result.packages||[]));
      const pending=el('section',undefined,'plan-questions');pending.append(el('h3',t('Open questions and acceptance')));
      for(const question of result.open_questions||[])pending.append(button('#'+question.id+' · '+t(questionKind(question)),()=>showQuestion(question)));
      if(!result.open_questions?.length)pending.append(el('p',t('No open questions for this plan.'),'dim'));
      frame.summary.append(pending);
      if(row.status==='DRAFT')frame.actions.append(button('Edit draft',()=>openPlanEditor(row,result)));

    }).catch(error=>{if(target.contains(reader)){text.textContent=error.message;frame.content.querySelector('.package-loading')?.remove();}});
    try {
      const available=await resolvePlanActions(row,route=>client.request(route));
      if(!target.contains(bar))return;
      for(const action of available)bar.append(button(actions[action],()=>act('plan/'+action,key(row),t(actions[action])+' '+row.name+'?')));
    } catch(error) {if(target.contains(bar))bar.append(el('span',error.message,'bad'));}
    if(row.status!=='DONE')bar.append(button('Release readiness gate',()=>act('owner/release',key(row),t('Allow this plan past the readiness check? This does not publish a release.'))));
    let providers;try{providers=await client.request('plan/providers?'+new URLSearchParams(key(row)));}catch(error){if(target.contains(bar))bar.append(el('span',error.message,'bad'));return;}
    if(target!==$('detail-body') || !target.contains(bar))return;
    for(const provider of providers){
      const node=button(t('Restart Queen on %s').replace('%s',provider.name)+(provider.current?' ✓':''),()=>act('plan/restart-queen',{...key(row),provider:provider.name},t('Restart Queen on %s').replace('%s',provider.name)+'?'));
      node.disabled=provider.selectable===false || node.disabled;
      bar.append(node);
    }
  }
  function showQuestion(question) {
    openConversation(question.project,question.plan_path,Number(question.id));
  }
  function openConversation(project,plan,id=null,keepFilter=false) {
    selectedThread=JSON.stringify([project,plan||'']);questionFocus=id;questionNavigation=true;if(!keepFilter)questionFilter='all';conversationMobile=true;
    $('detail').close();void selectTab('Questions');
  }
  function planMessageButton(row) {
    const thread=questionThreads(questions,[row]).find(item=>item.project===(row.project||row.apiary)&&item.plan===(row.plan_path||''));
    const pending=thread?.pending||[];
    const label=pending.length?pending.length+' '+t('pending')+(pending.some(q=>questionKind(q)==='Acceptance')?' · '+t('Acceptance'):''):t('Conversation');
    const node=button(label,()=>openConversation(row.project||row.apiary,row.plan_path));node.className='plan-message-button'+(pending.length?' needs-you':'');node.setAttribute('aria-label',t('Messages')+' · '+row.name+' · '+label);return node;
  }
  async function openPlanEditor(row=null,result=null,fresh=false) {
    const replacing=fresh || (row && planDraft && (planDraft.plan!==row.plan_path || planDraft.project!==(row.apiary||row.project)));
    if(replacing && planDraft?.dirty && !await requestConfirm(t('Discard unsaved changes?')))return;
    if(fresh)planDraft=null;
    if(row && row.status!=='DRAFT')return;
    planDraft=row && (!planDraft || planDraft.plan!==row.plan_path || planDraft.project!==(row.apiary||row.project))?{project:row.apiary||row.project,plan:row.plan_path,filename:row.plan_path,content:result.text||'',revision:result.revision,dirty:false}:planDraft||{project:client.state.projects?.[0]?.name||'',filename:'',content:t("# Plan title\n\nDescribe what should change and the result you expect.\n"),dirty:false};
    detail(t(row?'Edit draft':'New plan'),{});const body=$('detail-body');body.className='';const heading=el('h2',t(row?'Edit draft':'New plan'));heading.id='detail-title';body.replaceChildren(heading);
    const current=planDraft,form=el('div',undefined,'plan-editor');body.append(form);
    form.append(el('p',t('Save Markdown as a draft, then submit it explicitly to start preparation.')));
    const project=el('select');project.setAttribute('aria-label',t('Project'));
    for(const item of client.state.projects||[]){const option=el('option',item.name);option.value=item.name;project.append(option);}project.value=current.project;project.disabled=!!current.plan;
    const filename=el('input');filename.value=current.filename;filename.setAttribute('aria-label',t('Plan file'));filename.placeholder='example-PLAN.md';filename.disabled=!!current.plan;
    const hint=el('p',undefined,'dim');
    function projectHint(){const item=client.state.projects?.find(item=>item.name===current.project);hint.textContent=t('Plan files')+': '+(item?.plan_globs||[]).join(', ');if(!current.filename || current.autoFilename){current.filename=(item?.suggested_plan_folder?item.suggested_plan_folder+'/':'')+'new-PLAN.md';current.autoFilename=true;filename.value=current.filename;}}
    project.onchange=()=>{current.project=project.value;markDirty();projectHint();};projectHint();
    filename.oninput=()=>{current.autoFilename=false;current.filename=filename.value;markDirty();};
    const content=el('textarea');content.value=current.content;content.className='plan-markdown';content.setAttribute('aria-label',t('Plan Markdown'));content.oninput=()=>{current.content=content.value;markDirty();};
    const imported=el('input');imported.type='file';imported.accept='.md,text/markdown';imported.setAttribute('aria-label',t('Import Markdown'));
    imported.onchange=async()=>{const file=imported.files?.[0];if(!file)return;if(!/\.md$/i.test(file.name)){client.notice(t('Choose a Markdown (.md) file.'),true);return;}try{const value=await file.text();if(current!==planDraft)return;current.content=value;content.value=value;markDirty();submit.disabled=true;}catch(error){client.notice(error.message,true);}};
    const status=el('p',current.dirty?t('Unsaved changes'):'', 'dim'),controls=el('div',undefined,'actions');
    status.setAttribute('role','status');
    function markDirty(){current.dirty=true;submit.disabled=true;status.textContent=t('Unsaved changes');}
    const submit=button('Submit plan',async()=>{if(current.dirty||!current.plan){client.notice(t('Save the draft before submitting.'),true);return;}const result=await act('plan/start',{project:current.project,plan:current.plan},t('Submit this draft for preparation?'));if(result){planDraft=null;$('detail').close();}});
    const save=button('Save draft',async()=>{const sent={project:current.project,filename:current.filename,plan:current.plan,content:current.content,revision:current.revision};const result=await client.action(current.plan?'plans/save':'plans/create',sent);if(!result)return;current.plan=result.plan;current.revision=result.revision;current.dirty=current.content!==sent.content||current.filename!==sent.filename;project.disabled=true;filename.disabled=true;submit.disabled=current.dirty;status.textContent=t(current.dirty?'Saved. Newer edits are still unsaved.':'Draft saved. Submit when ready.');void refreshLists();});
    submit.disabled=submit.disabled||!current.plan||current.dirty;content.addEventListener('input',()=>{submit.disabled=true;});filename.addEventListener('input',()=>{submit.disabled=true;});
    controls.append(save,submit);form.append(el('label',t('Project')),project,hint,el('label',t('Plan file')),filename,imported,content,controls,status);
  }
  function renderPlans() {
    const box=section('Plans');box.append(button('New plan',()=>openPlanEditor(null,null,true)));if(planDraft)box.append(button('Continue draft',()=>openPlanEditor()));box.append(table(plans.map(row=>({...row,started:t(row.has_hive?'yes':'no')})),['apiary','name','status','messages','started','waiting_for','error'],(cell,row)=>cell.append(button('Plan',()=>openPlan(row)))));
  }
  function questionKind(question) {
    if(question.kind==='acceptance' || question.item==='owner-check' || String(question.item).startsWith('owner-check-'))return 'Acceptance';
    return question.kind==='decision'?'Decision':'Question';
  }
  function questionAnswered(question) {
    return question.status?question.status==='answered':!!question.answered_at;
  }
  function questionThreads(items,planRows) {
    const groups=new Map();
    function ensure(project,plan,name='',status=''){
      const id=JSON.stringify([project,plan||'']);
      if(!groups.has(id))groups.set(id,{id,project,plan:plan||'',name:name||String(plan||'').split(/[\\/]/).pop().replace(/\.md$/i,'')||project,status,questions:[],pending:[]});
      return groups.get(id);
    }
    for(const row of planRows)ensure(row.project||row.apiary,row.plan_path,row.name,row.status);
    for(const question of items){const thread=ensure(question.project,question.plan_path);thread.questions.push(question);if(!question.status||question.status==='open')thread.pending.push(question);}
    return [...groups.values()].sort((a,b)=>a.project.localeCompare(b.project)||a.name.localeCompare(b.name));
  }
  function questionTimeline(items) {
    const events=[];
    for(const question of items){
      events.push({question,type:'question',at:question.created_at||'',text:String(question.text||question.question||question.item||'')});
      if(question.answer)events.push({question,type:'answer',at:question.answered_at||question.created_at||'',text:question.answer});
    }
    return events.sort((a,b)=>a.at.localeCompare(b.at)||Number(a.question.id)-Number(b.question.id)||(a.type==='question'?-1:1));
  }
  function questionCard(question) {
      const answered=questionAnswered(question),kind=questionKind(question),check=kind==='Acceptance';
      const card=el('article',undefined,'card question');card.dataset.question=question.id;card.tabIndex=-1;card.setAttribute('aria-label',t(kind)+' #'+question.id+' '+question.project);
      card.append(el('small',t(kind)+' #'+question.id+' · '+t(answered?'Answered':'Needs you')));

      const message=String(question.text||question.question||question.item||'');
      const lines=message.split(/\r?\n/),brief=lines.find(line=>line.trim())||message;
      card.append(el('p',brief.slice(0,320)+(brief.length>320?'…':''),'question-summary'));
      if(check){
        const facts=el('dl',undefined,'package-facts');
        for(const [label,prefixes] of [['Integrated commit',['Integrated commit:','Integrierter Commit:']],['Steps that ran',['Steps that ran:','Ausgeführte Schritte:']]] ){
          const reportIndex=lines.findIndex(line=>/^(Report of the (Queen|fix agent)|Bericht (der Queen|des Fix-Agenten)):$/.test(line));
          const line=(reportIndex<0?lines:lines.slice(0,reportIndex)).find(line=>prefixes.some(prefix=>line.startsWith(prefix)));
          if(line)facts.append(el('dt',t(label)),el('dd',line.slice(line.indexOf(':')+1).trim()));
        }
        if(facts.children.length)card.append(facts);
        if(question.owner_check_result)card.append(el('strong',t(question.owner_check_result==='accepted'?'Accepted':'Rejected')));
      }
      if(message!==brief || brief.length>320){const full=el('details');full.append(el('summary',t('Full question and report')),el('pre',message));card.append(full);}
      card.append(el('p',t(answered?'This entry is preserved as history.':check?'Review the result. Save note records feedback; only Accept or Reject decides the outcome.':'Your answer is recorded for the plan so work can continue.'),'dim'));
      if(question.evidence && Object.values(question.evidence).some(value=>Array.isArray(value)?value.length:!!value)){const evidence=el('details');evidence.append(el('summary',t('Context and evidence')),valueView(question.evidence));card.append(evidence);}
      if(question.recommendation){const recommendation=el('details');recommendation.append(el('summary',t('Recommendation')),el('pre',question.recommendation));card.append(recommendation);}
      if(!answered && question.answer){const note=el('details');note.append(el('summary',t('Latest saved note')),el('pre',question.answer));card.append(note);}
      if(answered){card.append(el('h4',t('Answer')),el('pre',question.answer||'–'),el('small',[question.answered_at,question.answered_by].filter(Boolean).join(' · ')));return card;}
      const draft=questionDrafts.get(Number(question.id))||{text:'',checked:false,packages:''};questionDrafts.set(Number(question.id),draft);
      const answer=el('textarea');answer.value=draft.text;answer.setAttribute('aria-label',t(check?'Note or acceptance feedback':'Answer'));answer.oninput=()=>{draft.text=answer.value;};card.append(answer);
      if(question.recommendation)card.append(button('Use recommendation',()=>{answer.value=question.recommendation;draft.text=answer.value;}));
      const apiary=el('input');apiary.type='checkbox';apiary.checked=draft.checked;apiary.onchange=()=>{draft.checked=apiary.checked;};const label=el('label',t('Apply answer to the whole project'));label.prepend(apiary);if(!check){const options=el('details');options.append(el('summary',t('Answer options')),label,el('p',t('Unchecked: this plan only. Checked: store a project decision for future work.'),'dim'));card.append(options);}
      card.append(button(check?'Save note':'Answer',async()=>{const sent=answer.value;const result=await act('questions/answer',{id:question.id,text:sent,apiary:apiary.checked});if(result && draft.text===sent){draft.text='';answer.value='';}}));
      if(check){
        const packages=el('input');packages.value=draft.packages;packages.placeholder=t('Rejected packages (comma separated)');packages.setAttribute('aria-label',t('Rejected packages (comma separated)'));packages.oninput=()=>{draft.packages=packages.value;};const specific=el('details');specific.append(el('summary',t('Specific packages')),packages);card.append(specific);
        const packageList=el('div',undefined,'question-packages');for(const name of question.packages||[]){const label=el('label',name),toggle=el('input');toggle.type='checkbox';toggle.dataset.package=name;toggle.checked=draft.packages.split(',').map(x=>x.trim()).includes(name);toggle.onchange=()=>{const selected=new Set(packages.value.split(',').map(x=>x.trim()).filter(Boolean));if(toggle.checked)selected.add(name);else selected.delete(name);packages.value=[...selected].join(', ');draft.packages=packages.value;};label.prepend(toggle);packageList.append(label);}specific.append(packageList,el('p',t('Select the packages needing rework. Leave empty to reject the overall result.'),'dim'));
        const actions=el('div',undefined,'messenger-decision-actions'),accept=button('Accept',()=>act('owner/accept',{...key(question),text:answer.value},t('Accept the plan result?')));accept.className='messenger-primary';actions.append(accept,button('Request changes',()=>act('owner/reject',{...key(question),text:answer.value,packages:packages.value.split(',').map(x=>x.trim()).filter(Boolean)},t('Reject the result and request rework?'))));card.append(actions);
      }
      return card;
  }
  function renderQuestions() {
    const box=section('Questions');box.className='questions-messenger';box.dataset.mobileChat=String(conversationMobile);
    const threads=questionThreads(questions,plans),shown=threads.filter(thread=>questionFilter==='all'||(questionFilter==='open'?thread.pending.length:thread.questions.length&&!thread.pending.length&&thread.questions.every(questionAnswered)));
    if(!selectedThread && shown.length)selectedThread=shown[0].id;
    const selected=shown.find(thread=>thread.id===selectedThread)||shown[0];if(selected)selectedThread=selected.id;
    const inbox=el('aside',undefined,'messenger-inbox'),heading=el('div',undefined,'messenger-inbox-head');
    heading.append(el('h2',t('Questions')),el('span',questions.filter(q=>!q.status||q.status==='open').length+' '+t('pending'),'messenger-count'));
    const filters=el('div',undefined,'messenger-filters');
    for(const [value,label] of [['open','Needs you'],['all','All'],['answered','Completed']]){const control=button(label,()=>{questionFilter=value;selectedThread=null;conversationMobile=false;renderQuestionsPage();});control.setAttribute('aria-pressed',String(questionFilter===value));filters.append(control);}heading.append(filters);inbox.append(heading);
    for(const project of [...new Set(shown.map(thread=>thread.project))]){
      const group=el('section',undefined,'messenger-project'),projectCount=shown.filter(thread=>thread.project===project).reduce((sum,thread)=>sum+thread.pending.length,0);
      const projectHeading=el('h3',undefined,'messenger-project-heading');projectHeading.append(el('small',t('Project')),el('strong',project),el('span',projectCount+' '+t('pending')));group.append(projectHeading);
      for(const thread of shown.filter(thread=>thread.project===project)){
        const target=thread.pending[0]||thread.questions.at(-1),preview=String(target?.text||target?.question||target?.item||'').split(/\r?\n/).find(line=>line.trim())||t('No recorded questions.');
        const row=button('',()=>openConversation(thread.project,thread.plan,null,true));row.className='messenger-thread';row.setAttribute('aria-pressed',String(thread.id===selectedThread));row.setAttribute('aria-label',t('Project')+' '+thread.project+' · '+t('Plan')+' '+thread.name+' · '+thread.pending.length+' '+t('pending'));
        const title=el('span',undefined,'messenger-thread-title');title.append(el('strong',thread.name));if(thread.pending.length)title.append(el('span',thread.pending.length,'messenger-count'));
        row.append(el('small',t('Plan'),'messenger-field-label'),title,el('span',preview,'messenger-preview'),el('small',thread.pending.length?t(questionKind(thread.pending[0]))+' · '+t('Needs you'):t(thread.status||'Recorded history')));group.append(row);
      }
      inbox.append(group);
    }
    if(!shown.length)inbox.append(el('p',t(questionFilter==='open'?'No questions need your answer.':'No conversations match this filter.'),'messenger-empty'));
    const conversation=el('div',undefined,'messenger-conversation');box.append(inbox,conversation);
    if(!selected){conversation.append(el('p',t('Choose a plan conversation.'),'messenger-empty'));return;}
    const head=el('header',undefined,'messenger-chat-head'),title=el('h2',selected.name);title.tabIndex=-1;title.id='conversation-title';
    const filterLabel=t({open:'Needs you',all:'All',answered:'Completed'}[questionFilter]);
    const back=button(t('Back to %s').replace('%s',filterLabel),()=>{conversationMobile=false;renderQuestionsPage();});back.className='messenger-back';
    const identity=el('div',undefined,'messenger-identity'),projectIdentity=el('div',undefined,'messenger-project-identity'),planIdentity=el('div',undefined,'messenger-plan-identity');
    projectIdentity.append(el('small',t('Project')),el('strong',selected.project));planIdentity.append(el('small',t('Plan')),title);identity.append(projectIdentity,planIdentity,el('small',t('View')+': '+filterLabel,'messenger-filter-context'));head.append(back,identity);
    if(plans.some(row=>(row.project||row.apiary)===selected.project&&row.plan_path===selected.plan))head.append(button('Plan',()=>openQueuedPlan({project:selected.project,plan_path:selected.plan})));conversation.append(head);
    const active=selected.pending.find(question=>Number(question.id)===questionFocus)||selected.pending[0];
    if(active)questionFocus=Number(active.id);
    const body=el('div',undefined,'messenger-chat-body');conversation.append(body);
    const history=el('details',undefined,'messenger-history');history.append(el('summary',t('Recorded questions and answers')+' · '+selected.questions.length));
    const timeline=el('div',undefined,'messenger-timeline');
    for(const event of questionTimeline(selected.questions)){
      const bubble=el('article',undefined,'messenger-message '+(event.type==='answer'?'messenger-answer':''));
      const label=event.type==='answer'?(questionKind(event.question)==='Acceptance'&&!questionAnswered(event.question)?t('Latest saved note'):t('Answer')):t(questionKind(event.question))+' #'+event.question.id;
      bubble.append(el('small',[label,event.type==='answer'?event.question.answered_by:'',event.at].filter(Boolean).join(' · ')));
      const preview=event.text.split(/\r?\n/).find(line=>line.trim())||event.text;bubble.append(el('p',preview.slice(0,240)+(preview.length>240?'…':'')));
      if(event.text!==preview||preview.length>240){const full=el('details');full.append(el('summary',t('Full text')),el('pre',event.text));bubble.append(full);}
      if(event.type==='question' && event.question.evidence){const evidence=el('details');evidence.append(el('summary',t('Context and evidence')),valueView(event.question.evidence));bubble.append(evidence);}
      if(event.type==='question' && event.question.recommendation){const recommendation=el('details');recommendation.append(el('summary',t('Recommendation')),el('pre',event.question.recommendation));bubble.append(recommendation);}
      if(event.type==='answer' && event.question.owner_check_result)bubble.append(el('strong',t(event.question.owner_check_result==='accepted'?'Accepted':'Rejected')));
      timeline.append(bubble);
    }
    history.append(timeline);if(selected.questions.length)body.append(history);
    const latestAnswer=questionTimeline(selected.questions).filter(event=>event.type==='answer').at(-1);
    if(latestAnswer){const latest=el('div',undefined,'messenger-message messenger-answer'),preview=String(latestAnswer.text).split(/\r?\n/).find(line=>line.trim())||'';latest.append(el('small',t(questionKind(latestAnswer.question)==='Acceptance'&&!questionAnswered(latestAnswer.question)?'Latest saved note':'Latest recorded answer')+' · '+(latestAnswer.question.answered_by||'')+' · '+latestAnswer.at),el('p',preview.slice(0,240)+(preview.length>240?'…':'')));body.append(latest);}

    if(active){
      const pending=el('div',undefined,'messenger-pending');
      if(selected.pending.length>1){pending.append(el('label',selected.pending.length+' '+t('pending')+' · '+t('Choose the question to answer')));
      const choose=el('select');choose.setAttribute('aria-label',t('Choose the question to answer'));
      for(const question of selected.pending){const option=el('option','#'+question.id+' · '+t(questionKind(question))+' · '+String(question.text||question.question||question.item||'').split(/\r?\n/)[0].slice(0,100));option.value=question.id;choose.append(option);}choose.value=active.id;
      choose.onchange=()=>{questionFocus=Number(choose.value);questionNavigation=true;renderQuestionsPage();};pending.append(choose);}
      else pending.append(el('small','1 '+t('pending'),'dim'));
      body.append(pending);
      const card=questionCard(active);card.className='question messenger-request';body.append(card);
    }else body.append(el('p',t(selected.questions.length?'No open questions for this plan.':'No recorded questions.'),'messenger-empty'));
    if(questionNavigation){questionNavigation=false;title.focus({preventScroll:true});head.scrollIntoView({block:'start'});}
  }
  function renderQuestionsPage(){view.replaceChildren();renderQuestions();}
  function renderUpdates() {
    const box=$('update-status');box.replaceChildren();
    let label='',colour='dim';
    if(updateState.installing){label=t('installing version %s – waiting for running agents').replace('%s',updateState.installing.version || '');colour='warn';}
    else if(updateState.failed){label=t('update to %s failed, see STATUS').replace('%s',updateState.failed);colour='bad';}
    else if(updateState.error){label=t('update check failed');colour='bad';}
    else if(updateState.available?.version){label='';}
    else if(updateState.no_release){label=t('no release published yet');}
    else if(updateState.last_check){label=t('up to date');colour='ok';}
    if(label)box.append(el('span',t(label),colour));
    if(updateState.available?.version && !updateState.installing)box.append(button(t('Update to %s').replace('%s',displayVersion(updateState.available.version)),async()=>{await act('updates/install',{version:updateState.available.version},t(bootstrap.updateConfirm).replace('%s',updateState.available.version));}));
    const check=button('Check now',async()=>{updateState=await client.action('updates/check');renderUpdates();});check.disabled=check.disabled||!!updateState.installing;check.classList.add('update-check');box.append(check);
  }
  async function loadEditor(project='') {
    if(tab==='Projects' && !project){client.notice(t('Add Project'));renderEditor();return;}
    const token=++generation, pageTab=tab;
    client.notice('Loading…');
    try {
      const loaded=await client.request('settings/view'+(project?'?project='+encodeURIComponent(project):''));
      if(token!==generation || pageTab!==tab)return;
      editor={...loaded,project,dirty:false,storedKeys:Object.keys(loaded.data||{})};
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
      const bar=el('div',undefined,'toolbar');bar.append(projectSelect(async value=>{if(editor?.dirty&&!await requestConfirm(t('Discard unsaved changes?')))return;await loadEditor(value);},editor?.project,false),
        button('Add Project',addProject),button('Remove entry',async()=>{
          if(!editor?.project)return;
          const machine=await client.request('settings/view'),data=copy(machine.data);
          data.projects=(data.projects || []).filter(project=>project.name!==editor.project);
          await client.action('settings/save',{revision:machine.revision,data},t('Remove this Project from the settings?'));
        }));view.append(bar);
    }
    if(!editor){view.append(el('p',t(tab==='Projects' && !(client.state?.projects || []).length?'Add Project':'Loading…')));return;}
    const toolbar=el('div',undefined,'toolbar');view.append(toolbar);
    toolbar.append(button('Save',saveEditor),button('Reload',async()=>{if(!editor.dirty || await requestConfirm(t('Discard unsaved changes?')))return loadEditor(editor.project);}));
    const form=section(({General:'System',Providers:'AI providers'})[tab]||tab), data=editor.data;
    form.classList.add('settings-editor');
    toolbar.classList.add('settings-savebar');
    const impact=el('details',undefined,'settings-impact');impact.append(el('summary',t('When changes take effect')),el('p',t('Save applies to this page. Running agents keep their current configuration; new starts use saved settings.')));toolbar.append(impact);
    if(tab==='General')impact.append(el('p',t('Web settings can reconfigure the web listener when saved. Resource limits apply at the next scheduling decision.')));
    if(tab==='Git connections'){toolbar.hidden=true;form.append(el('p',t('Credentials are saved separately for each connection and shared across projects.')));}

    if(tab==='General'){
      const defaults=bootstrap.general;
      for(const [key,value] of Object.entries(defaults))if(!['providers','projects'].includes(key) && data[key]===undefined)data[key]=copy(value);
      if(editor.build){
        const build=el('fieldset');build.append(el('legend',t('Build')));
        const values={buildJobs:{...(typeof data.buildJobs==='object'?data.buildJobs:{}),...editor.build}};
        fields(build,values.buildJobs,['buildJobs'],values,new Set(),false);
        for(const input of build.querySelectorAll('input,select'))input.addEventListener('change',()=>{
          const next=values.buildJobs;
          if(!Number.isInteger(next.cores)||next.cores<1||next.cores>editor.cores||!Number.isInteger(next.parallel)||next.parallel<1||next.minFreeRamGB<=0){client.notice(t('Invalid build settings'),true);return;}
          delete next.coreShare;delete next.fixed;data.buildJobs=copy(next);changed();
        });form.append(build);
      }
      fields(form,data,[],data,new Set(['providers','projects','buildJobs',...(data.minFreeRamGB!==undefined?['ramReserveMb']:[])]));
      form.append(button('Install /bzz',async()=>{const job=await client.action('tools/install');if(job){job.title='Install /bzz';jobs.set(job.id,job);renderJob(job);}}));
    } else if(tab==='Git connections') {
      const git=el('details',undefined,'git-connections');git.append(el('summary',t('Git provider login')),
        el('p',t('One access token is shared by all projects for each Git provider.')));form.append(git);
      client.request('git-login/providers').then(providers=>{
        if(!git.isConnected)return;
        const grid=el('div',undefined,'git-connection-grid');git.append(grid);
        for(const provider of providers){
          const method={device:'Browser sign-in',bearer:'Browser sign-in',token:'Access token',pat:'Access token','existing access':'Existing access'}[provider.method] || provider.method || 'None';
          const card=el('details',undefined,'git-connection');const summary=el('summary');summary.append(el('strong',provider.name),statusBadge(credentialStatus(provider)),el('span',t('Current method')+': '+t(method),'dim'));card.append(summary);
          if(provider.error){card.append(el('p',provider.error,'bad'));grid.append(card);continue;}
          if(provider.status_error)card.append(el('p',provider.status_error,'bad'));
          const token=el('input');token.placeholder=t('Access token');token.type='password';token.autocomplete='off';token.setAttribute('aria-label',t('Access token'));
          const origin=el('input');origin.placeholder=t('HTTPS server origin');origin.setAttribute('aria-label',t('HTTPS server origin'));if(provider.origin_required)card.append(origin);
          card.append(token,button('Save access token',async()=>{await client.action('git-login/token',{provider:provider.name,token:token.value,...(provider.origin_required?{origin:origin.value}:{})});token.value='';render();}));
          grid.append(card);
        }
      }).catch(error=>client.notice(error.message,true));
    } else if(tab==='Providers') {
      data.providers ||= [];
      editor.providerIndex=Math.min(editor.providerIndex||0,Math.max(0,data.providers.length-1));
      const layout=el('div',undefined,'provider-layout'),list=el('div',undefined,'provider-list');
      const propose=provider=>{if(provider.nameEdited!==false)return;const key=providerCli(provider,bootstrap.presets),base=key+({api:'-api',llm:'-llm'}[provider.access]||'');let name=base,n=1;while(data.providers.some(p=>p!==provider&&p.name===name))name=base+'-'+(++n);provider.name=name;};
      list.append(button('Add provider',()=>{const provider=newProvider();provider.nameEdited=false;provider.capabilitiesVersion=1;provider.slots=1;provider.use='fallback';data.providers.push(provider);propose(provider);editor.providerIndex=data.providers.length-1;changed();render();}));
      data.providers.forEach((provider,index)=>{const item=button(provider.name||t('Providers'),()=>{editor.providerIndex=index;render();});item.className='provider-choice';item.setAttribute('aria-pressed',String(index===editor.providerIndex));item.append(el('small',t(bootstrap.choiceLabels?.access?.[provider.access||'subscription']||'Subscription')));list.append(item);});
      layout.append(list);form.append(layout);
      if(!data.providers.length){layout.append(el('p',t('Add provider')));}
      else {
        const index=editor.providerIndex,provider=data.providers[index],card=el('div',undefined,'provider-form');layout.append(card);
        const path=['providers',index],customFields=['protocol','command','outputFormat','loginCommand','loginFiles','profileEnv'];
        const defaults=newProvider(provider.protocol);
        provider.capabilities=effectiveCapabilities(provider);
        for(const key of ['enabled','name','slots','use','beeMode','args','contextLimit','claudeTools'])if(provider[key]===undefined)provider[key]=copy(defaults[key]);
        provider.access ||= 'subscription';provider.classes ||= {};
        const cliKey=providerCli(provider,bootstrap.presets),access=provider.access,status=providerStates.find(p=>p.name===provider.name);
        const ready=Array.isArray(status?.ready)?status.ready[0]:!!status?.ready,reason=Array.isArray(status?.ready)?status.ready[1]:status?.reason;
        const group=(title,parent=card)=>{const box=el('div',undefined,'provider-section');box.append(el('h3',t(title)));parent.append(box);return box;};
        const selected=(parent,keys,object=provider,base=path)=>{for(const key of keys)if(object[key]===undefined)object[key]=copy(defaults[key]??'');fields(parent,object,base,data,new Set(Object.keys(object).filter(key=>!keys.includes(key))),false);};
        const top=group('Providers');selected(top,['enabled','name','access']);
        const inputFor=(parent,key)=>[...parent.querySelectorAll('[data-path]')].find(node=>node.dataset.path===JSON.stringify([...path,key]));
        const nameInput=inputFor(top,'name');if(nameInput){const change=nameInput.oninput;nameInput.oninput=nameInput.onchange=()=>{change();provider.nameEdited=true;};}
        const accessInput=inputFor(top,'access');if(accessInput){accessInput.oninput=null;accessInput.onchange=()=>{editor.typeStash ||= {};const offered=bootstrap.accessClis?.[accessInput.value]||Object.keys(bootstrap.presets);switchProviderAccess(provider,accessInput.value,editor.typeStash[index]||=( {}),bootstrap.presets,offered);propose(provider);changed();render();};}
        if(status)top.append(el('p',t(ready?'Ready':'Not ready')+(reason?' · '+reason:''),ready?'provider-ready':'provider-not-ready'));
        const cli=group('CLI'),label=el('label',undefined,'field'),preset=el('select');label.append(el('span',t('CLI')),preset,el('small',t(bootstrap.help.cli||'The program HaiveQueen starts for the agents of this provider.')));cli.append(label);preset.setAttribute('aria-label',t('CLI'));
        const choices=bootstrap.accessClis?.[access]||[...Object.keys(bootstrap.presets),'custom'];
        for(const key of choices){const option=el('option',bootstrap.presets[key]?.label||t('Custom CLI'));option.value=key;preset.append(option);}preset.value=cliKey;
        preset.onchange=()=>{applyProviderCli(provider,preset.value,bootstrap.presets);propose(provider);changed();render();};
        if(cliKey==='custom')selected(cli,customFields);
        const source=group(access==='subscription'?'Login':access==='api'?'API key':'Local LLM');
        const saved=!editor.dirty;
        source.append(el('small',t('Save changes before using connection actions.')));
        const savedAction=(title,fn,parent=source,ready=true)=>{const node=button(title,fn);node.disabled=node.disabled||!saved||!ready;node.dataset.savedOnly='true';parent.append(node);return node;};
        const cliUpdate=status?.cli_update;
        if(cliUpdate){
          const updateLine=el('p',undefined,cliUpdate.status==='update'?'provider-not-ready':'muted');
          updateLine.textContent=cliUpdate.status==='update'?t('Update available')+' · '+cliUpdate.installed+' → '+cliUpdate.latest:cliUpdate.status==='current'?t('Up to date')+' · '+cliUpdate.installed:t(cliUpdate.message||'Version check unavailable');
          cli.append(updateLine);
        }
        savedAction('Check for CLI updates',async()=>{await client.action('provider/updates',{provider:provider.name});},cli);
        if(access==='subscription'){
          source.append(statusBadge(loginStatus(status)));
          savedAction('Login',async()=>{const run=await client.action('login/start',{provider:provider.name});if(run){loginRuns.set(run.id,run);renderLogin(run);}});
          source.append(el('small',t('This login is shared by all providers with this CLI.')));
        }else{
          selected(source,access==='api'?['apiKey']:['address','apiKey','contextWindow']);
          if(access==='llm'){
            savedAction('Test connection',async()=>{const current=editor;await act('provider',{provider:provider.name,provider_action:'test'});if(current===editor&&!editor.dirty)await loadEditor('');});
            const result=editor.provider_connections?.[provider.name];
            if(result&&!editor.dirty){if(result.time)source.append(el('small',String(result.time)));for(const line of result.lines||[])source.append(el('p',line));}
          }
        }
        const models=group('Models'),modelRows=el('div',undefined,'provider-models');models.append(modelRows);
        const cachedModels=editor.provider_models?.[provider.name];
        const localModels=cachedModels?.access==='llm'&&cachedModels.address===provider.address&&cachedModels.protocol===provider.protocol?cachedModels:{};
        const offered=cliKey==='custom'?{}:access==='llm'?{...localModels,efforts:bootstrap.cliModels?.[cliKey]?.efforts||[]}:(bootstrap.cliModels?.[cliKey]||cachedModels||{});
        for(const cls of ['light','medium','heavy']){
          const line=el('div',undefined,'provider-model-row');line.append(el('h4',t(bootstrap.labels[cls]||pretty(cls))),el('small',t(bootstrap.help[cls]||'')));
          const entry=provider.classes[cls] ||= {};entry.model ??= '';entry.effort ??= '';
          fields(line,entry,[...path,'classes',cls],data,new Set(Object.keys(entry).filter(key=>!['model','effort'].includes(key))),false,offered);modelRows.append(line);
        }
        const use=group('Use');selected(use,['slots','use']);
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
        capabilities.append(button('Add own',async()=>{const name=await requestInput(t('Capability name'));if(name?.trim()){provider.capabilities=effectiveCapabilities(provider);if(!provider.capabilities.includes(name.trim()))provider.capabilities.push(name.trim());provider.capabilitiesVersion=1;changed();render();}}));use.append(capabilities);
        const setup=group('Tool connections');
        const verified=new Set(status?.mcp?.available||[]),native=new Set(['Coding','Queen','Build','Deploy','Confidential data']);
        for(const capability of effectiveCapabilities(provider).filter(name=>!native.has(name))){
          setup.append(el('p',capability+' · '+t(verified.has(capability)?'Verified':'Missing setup'),verified.has(capability)?'provider-ready':'provider-not-ready'));
        }
        const mcpForm=el('details');mcpForm.append(el('summary',t('MCP configuration')));setup.append(mcpForm);
        const configuration=el('textarea');configuration.rows=8;configuration.setAttribute('aria-label',t('MCP configuration'));
        configuration.placeholder='{"mcpServers": {}, "capabilities": {"3D modeling": {"server": "server-name", "tools": ["tool-name"]}}}';
        configuration.value=editor.mcpDraft?.[provider.name]||'';
        configuration.oninput=()=>{editor.mcpDraft ||= {};editor.mcpDraft[provider.name]=configuration.value;};mcpForm.append(configuration);
        savedAction('Import and verify',async()=>{const value=JSON.parse(configuration.value);const job=await client.action('provider/mcp',{provider:provider.name,configuration:value},t('Import this configuration into the HaiveQueen CLI profile and start the supplied servers to verify their tools?'));if(job){jobs.set(job.id,job);renderJob(job);}},mcpForm);
        const advanced=el('details',undefined,'provider-advanced');advanced.append(el('summary',t('Advanced')));card.append(advanced);
        provider.allowedTools=toolsFromArgs(provider.args);
        selected(use,['contextLimit','beeMode']);
        selected(advanced,['args',...(provider.protocol==='claude'?['claudeTools']:[])]);
        const handled=new Set(['enabled','name','nameEdited','access','apiKey','address','contextWindow','slots','use','classes','limit','capabilities','capabilityThresholds','capabilitiesVersion','beeMode','contextLimit','allowedTools','args','claudeTools',...customFields]);
        fields(advanced,provider,path,data,handled);
        if(provider.limit&&typeof provider.limit==='object'){const limits=el('details');limits.append(el('summary',t('Usage check')));fields(limits,provider.limit,[...path,'limit'],data,new Set(['source']));advanced.append(limits);}
        for(const [cls,entry] of Object.entries(provider.classes)){const extra=el('details');extra.append(el('summary',t(pretty(cls))+' · '+t('Own settings')));fields(extra,entry,[...path,'classes',cls],data,new Set(['model','effort','customModel']));advanced.append(extra);}
        savedAction('Benchmark Start Context',async()=>{const job=await client.action('benchmark/start',{provider:provider.name},t('This starts the provider CLI twice per Project and uses login tokens. Confirm to continue.'));if(job){jobs.set(job.id,job);renderJob(job);}},models,ready);
        const measurements=(editor.benchmark||[]).filter(row=>row.provider===provider.name).map(row=>Object.fromEntries(Object.entries(row).map(([key,value])=>[key,typeof value==='number'&&!Number.isInteger(value)?Math.round(value*10)/10:value])));
        if(measurements.length)advanced.append(valueView(measurements));
        const footer=el('div',undefined,'provider-footer');footer.append(button('Remove entry',async()=>{if(await requestConfirm(t('Remove entry')+'?')){data.providers.splice(index,1);editor.typeStash={};changed();render();}}));card.append(footer);
      }
    } else {
      editor.data=mergeDefaults({...bootstrap.project,...editor.project_defaults},data);
      if(data.upstreamRules===undefined && editor.upstream)editor.data.upstreamRules=editor.upstream.on;
      editor.place ||= {repository:editor.repository || '',plansFolder:''};
      fields(form,editor.place,[],editor.place,new Set(),false);
      workflowFields(form,editor.data,editor.git_workflow);
      fields(form,editor.data,[],editor.data,new Set(['codeRepo','repo','rulesOff','upstreamRules','publishReleases','wayOfWorking','mergeMode','checkMode','buildMode','deployMode','releaseMode','checkEnabled','buildEnabled','deployEnabled','releaseEnabled','compileCheck','buildCommand','deployCommand','releaseFolder']));
      const knowledge=el('fieldset');knowledge.append(el('legend',t('Knowledge base')));
      knowledge.append(el('p',t('These files are supplied by HQ. A CLI may still load its own rules when HQ injection is disabled.')));
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
      knowledge.append(el('p',t("HaiveQueen's own role texts are always added.")));form.append(knowledge);
    }
    organizeSettings(form);
    // The unsaved marker survives live dashboard reads.
    const marker=el('span',editor.dirty?t('Unsaved changes'):'');marker.id='dirty-marker';toolbar.append(marker);
  }
  function organizeSettings(form) {
    if(tab==='Git connections')return;
    const host=tab==='Providers'?form.querySelector('.provider-form'):form;
    if(!host)return;
    const order=tab==='Projects'?['Basics','Workflow and checks','Agent rules','Git and cleanup','Advanced']:
      tab==='Providers'?['Connection','Models and effort','Use and limits','Tools and MCP','Advanced']:
      ['Basics','Resources','Agent control','Web access','Data and retention','Updates','Advanced'];
    const sections=new Map(order.map(name=>{const box=el('section',undefined,'settings-category');box.dataset.category=name;box.setAttribute('aria-label',t(name));box.append(el('h3',t(name)));return [name,box];}));
    const providerGroups={'Providers':'Connection','CLI':'Connection','Login':'Connection','API key':'Connection','Local LLM':'Connection','Models':'Models and effort','Use':'Use and limits','Tool connections':'Tools and MCP'};
    for(const node of [...host.children]){
      if(node.tagName==='H2')continue;
      const heading=node.querySelector('legend,h3,summary')?.textContent;
      const pathNode=node.matches?.('[data-path]')?node:node.querySelector('[data-path]');
      const key=pathNode?JSON.parse(pathNode.dataset.path)[0]:null;
      let category=tab==='Providers'?Object.keys(providerGroups).find(name=>t(name)===heading):null;
      category=tab==='Providers'?(providerGroups[category]||'Advanced'):settingsCategory(tab,key);
      if(tab==='Projects'){
        if(node.classList.contains('workflow-settings'))category='Workflow and checks';
        if(heading===t('Knowledge base'))category='Agent rules';
        if(['repository','plansFolder','plans','planGlobs','planExclude'].includes(key))category='Basics';
      }
      if(tab==='General'&&heading===t('Build'))category='Resources';
      if(node.classList.contains('provider-footer'))category='Advanced';
      if(node.tagName==='BUTTON'&&node.textContent===t('Add setting')){
        // Only the advanced category exposes raw configuration extensions.
        if(sections.get('Advanced').querySelector('.settings-add')){node.remove();continue;}
        node.classList.add('settings-add');
      }
      sections.get(category).append(node);
    }
    const navigation=el('nav',undefined,'settings-categories');navigation.setAttribute('aria-label',t('Settings sections'));
    const search=el('input');search.type='search';search.className='settings-search';search.placeholder=t('Search settings');search.setAttribute('aria-label',t('Search settings'));search.value=editor.search||'';
    const empty=el('p',t('No matching settings'),'settings-empty');empty.hidden=true;
    const active=order.includes(editor.settingsCategory)?editor.settingsCategory:order[0];editor.settingsCategory=active;
    const buttons=new Map();
    const apply=()=>{
      const query=search.value.trim().toLocaleLowerCase();let matches=0;
      for(const [name,box] of sections){
        const found=!query||box.textContent.toLocaleLowerCase().includes(query)||[...box.querySelectorAll('input,textarea')].some(input=>String(input.value).toLocaleLowerCase().includes(query));
        box.hidden=query?!found:name!==(editor.settingsCategory||active);
        if(!box.hidden){matches++;if(query)for(const details of box.querySelectorAll('details'))details.open=true;}
        buttons.get(name).setAttribute('aria-pressed',String(!query&&!box.hidden));
      }
      empty.hidden=matches>0;
    };
    for(const [name,box] of sections){
      const control=button(name,()=>{editor.settingsCategory=name;editor.search='';search.value='';apply();});buttons.set(name,control);navigation.append(control);
      host.append(box);
    }
    search.oninput=()=>{editor.search=search.value;apply();};
    host.prepend(navigation);host.prepend(search);host.append(empty);
    if(tab==='Projects'){
      const summary=el('p',workflowSummary(editor.data,t),'settings-workflow-summary');host.prepend(summary);
      const source=el('p',t('Scope: this project. Models, agent resources and access are configured in AI providers and System.'),'settings-scope');host.prepend(source);
    }else if(tab==='General')host.prepend(el('p',t('Scope: all projects on this installation.'),'settings-scope'));
    const heading=host.querySelector(':scope > h2');if(heading)host.prepend(heading);
    apply();
  }
  function workflowFields(parent,data,capabilities) {
    const steps=[['merge','Merge'],['check','Check','compileCheck','Compile check command'],['build','Build','buildCommand','Build command'],['deploy','Deploy','deployCommand','Deploy command'],['release','Release','releaseFolder','Release folder']];
    const group=el('fieldset',undefined,'workflow-settings');group.append(el('legend',t('Way of working')));
    const supported=!!capabilities?.provider;
    if(!supported)group.append(el('p',t('Git provider is not supported for this remote. Only Local is available.')));
    const select=el('select');select.setAttribute('aria-label',t('Way of working'));
    for(const [value,label] of [['local','Local'],['git','Git provider'],['custom','Custom']]){const option=el('option',t(label));option.value=value;if(value!=='local'&&!supported)option.disabled=true;select.append(option);}
    select.value='custom';
    const apply=el('button',t('Apply template'));apply.type='button';apply.onclick=()=>{if(select.value==='custom')return;data.wayOfWorking=select.value;for(const [step] of steps)data[step+'Mode']=select.value;changed();render();};
    const overall=el('details',undefined,'workflow-overall');overall.append(el('summary',t('Apply execution template')),el('p',t('Replaces the location of every step. Enabled steps and commands are preserved.')),select,apply);group.append(overall);
    for(const [step,label,field,fieldLabel] of steps){
      const enabled=!field||!!data[step+'Enabled'];
      const row=el('div',undefined,'workflow-step'+(enabled?'':' is-off'));row.dataset.workflowStep=step;row.setAttribute('role','group');row.setAttribute('aria-label',t(label));row.append(el('h3',t(label)));
      if(field){const switchLabel=el('label',undefined,'workflow-switch'),toggle=el('input');toggle.type='checkbox';toggle.setAttribute('role','switch');toggle.checked=!!data[step+'Enabled'];toggle.setAttribute('aria-label',t(label+' on'));toggle.onchange=()=>{data[step+'Enabled']=toggle.checked;changed();render();};switchLabel.append(toggle,el('span',t(toggle.checked?'On':'Off')));row.append(switchLabel);}
      else row.append(el('span',t('Always on'),'dim'));
      const unavailable=supported&&data[step+'Mode']==='git'&&!capabilities.supported[step];
      const mode=el('div',undefined,'workflow-location');mode.setAttribute('role','radiogroup');mode.setAttribute('aria-label',t(label+' location'));
      for(const [value,text] of [['local','Local'],['git','Git provider']]){
        const choice=el('button',t(text));choice.type='button';choice.dataset.mode=value;choice.setAttribute('role','radio');choice.setAttribute('aria-checked',String(data[step+'Mode']===value));choice.disabled=value==='git'&&(!supported||!capabilities.supported[step]);
        choice.tabIndex=data[step+'Mode']===value||(value==='local'&&(!supported||unavailable))?0:-1;
        const choose=(next,keyboard=false)=>{if(next==='git'&&(!supported||!capabilities.supported[step]))return;data[step+'Mode']=next;const modes=new Set(steps.map(([name])=>data[name+'Mode']));data.wayOfWorking=modes.size===1?[...modes][0]:'custom';changed();render();if(keyboard)view.querySelector('[data-workflow-step="'+step+'"] [data-mode="'+next+'"]')?.focus({preventScroll:true});};
        choice.onclick=()=>choose(value);
        choice.onkeydown=event=>{if(choice.disabled)return;let next;if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))next=value==='local'?'git':'local';else if(event.key==='Home')next='local';else if(event.key==='End')next='git';else return;event.preventDefault();choose(next,true);};
        mode.append(choice);
      }
      mode.hidden=!enabled;row.append(mode);
      const content=el('div',undefined,'workflow-command');row.append(content);
      if(field && enabled && data[step+'Mode']!=='git'){const inputLabel=el('label',undefined,'workflow-field'),input=el('input');input.value=data[field]||'';input.dataset.path=JSON.stringify([field]);input.disabled=!data[step+'Enabled']||data[step+'Mode']==='git';input.required=!input.disabled;input.oninput=()=>{data[field]=input.value;changed();};inputLabel.append(el('span',t(fieldLabel)),input);content.append(inputLabel);}
      const explanation=unavailable?'This provider does not support this step; it will be skipped.':field&&!data[step+'Enabled']?'Step is off; it will be skipped.':data[step+'Mode']==='git'?'Executed by the Git provider.':'Executed locally by HaiveQueen.';
      content.append(el('small',t(explanation),'dim'));
      group.append(row);
    }
    parent.append(group);
  }
  async function addProject() {
    if(editor?.dirty&&!await requestConfirm(t('Discard unsaved changes?')))return;
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
  function fields(parent, object, path, root, omit=new Set(), allowAdd=true, modelChoices=null) {
    for(const [name,value] of Object.entries(object)){
      if(['password','capabilitiesVersion'].includes(name)||omit.has(name))continue;
      const full=[...path,name];
      if(value && typeof value==='object' && !Array.isArray(value)){
        const group=el('details');group.open=['web','classes','buildJobs'].includes(name);group.append(el('summary',pretty(name)));fields(group,value,full,root,new Set(),false);parent.append(group);continue;
      }
      if(Array.isArray(value) && (value.some(item=>typeof item==='object') || ['providers','projects'].includes(name))){arrayEditor(parent,object,name,path,{},root);continue;}
      const label=el('label',undefined,'field');label.append(el('span',t((tab==='General'&&path.length===0&&name==='minFreeRamGB'?'Minimum free RAM for agent starts (GiB)':null) || bootstrap.fieldLabels?.[tab]?.[name] || bootstrap.labels[name] || pretty(name))));
      const choices=bootstrap.choices[name] || (name==='access'?['subscription','api','llm']:name==='language'&&tab==='General'?['English','German']:null);
      let input;
      if(choices){input=el('select');for(const choice of [...new Set([...choices,value])]){const option=el('option',t(bootstrap.choiceLabels?.[name]?.[choice] || choice));option.value=choice;input.append(option);}input.value=value;}
      else if(Array.isArray(value)||String(value).includes('\n')){input=el('textarea');input.value=Array.isArray(value)?value.join('\n'):value;}
      else {input=el('input');input.type=typeof value==='boolean'?'checkbox':typeof value==='number'?'number':/apiKey|providerKey/i.test(name)?'password':'text';if(input.type==='checkbox')input.checked=value;else input.value=value ?? '';if(input.type==='number')input.step='any';if(['cores','parallel','port','maxConcurrentCompileChecks'].includes(name)){input.min='1';input.step='1';input.required=true;}if(name==='port')input.max='65535';if(name==='slowTestSeconds'){input.required=true;}}
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
            const offered=modelChoices?.models || editor.provider_models?.[provider.name]?.models;
            if(Array.isArray(offered)){const entry=provider.classes[path[3]];if(offered.includes(next))delete entry.customModel;else entry.customModel=true;}
          }
        }
        label.querySelector('.settings-origin')?.remove();changed();};
      input.dataset.path=JSON.stringify(full);
      if(name==='model'||name==='effort'){
        const provider=path[0]==='providers'?root.providers[path[1]]:null;
        const offered=modelChoices || (provider?(editor.provider_models?.[provider.name] || {}):{});
        const items=name==='model'?offered.models:offered.efforts;
        if(items?.length){const list=el('datalist');list.id='choices-'+Math.random().toString(36).slice(2);for(const item of items){const option=el('option');option.value=typeof item==='string'?item:item.id||item.name;list.append(option);}input.setAttribute('list',list.id);label.append(list);}
      }
      label.append(input);
      if(path.length===0&&root===editor.data&&!editor.storedKeys?.includes(name)){
        const origin=editor.project_defaults&&Object.hasOwn(editor.project_defaults,name)?'Repository default':'Application default';
        label.append(el('small',t(origin),'settings-origin'));
      }
      const help=(tab==='General'&&path.length===0&&name==='minFreeRamGB'?'Overrides the legacy agent RAM reserve; independent of the build RAM threshold.':null) || bootstrap.fieldHelp?.[tab]?.[name] || bootstrap.help[name] || bootstrap.defaultHelp?.[tab];if(help)label.append(el('small',t(help)));parent.append(label);
    }
    if(!allowAdd)return;
    const add=button('Add setting',async()=>{
      const name=await requestInput(t('Setting name'));if(!name || ['__proto__','constructor','prototype','password'].includes(name) || Object.hasOwn(object,name))return;
      const value=await requestInput(t('Value (JSON, or plain text)'));if(value===null)return;
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
    for(const input of view.querySelectorAll('input,select,textarea'))if(!input.disabled && !input.checkValidity()){
      const category=input.closest('.settings-category');if(category){for(const box of view.querySelectorAll('.settings-category'))box.hidden=box!==category;editor.settingsCategory=category.dataset.category;}
      for(let parent=input.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
      input.reportValidity();return;
    }
    const sent=JSON.stringify([current.data,current.place]);
    const data=copy(current.data);
    if(!current.project)for(const provider of data.providers || []){
      delete provider.allowedTools;
      if((provider.access||'subscription')!=='llm'){delete provider.address;delete provider.contextWindow;delete provider.server;}
      if((provider.access||'subscription')==='subscription')delete provider.apiKey;
    }
    const loaded=await client.action('settings/save',{project:current.project || undefined,revision:current.revision,data,...(current.project?{place:current.place}:{})});
    if(!loaded)return;
    if(current!==editor)return;
    if(JSON.stringify([current.data,current.place])!==sent){current.revision=loaded.revision;current.dirty=true;client.notice(t('Saved. Newer edits are still unsaved.'));return;}
    editor={...loaded,project:current.project,settingsCategory:current.settingsCategory,search:current.search,storedKeys:Object.keys(loaded.data||{}),dirty:false,rules:current.rules,upstream:current.upstream,workspace:current.workspace,place:current.place,project_defaults:current.project_defaults,git_workflow:current.git_workflow,provider_models:current.provider_models,benchmark:current.benchmark,build:current.build,cores:current.cores};
    if(!current.project && current.data.language && current.data.language!==bootstrap.language){location.reload();return;}
    render();void client.refresh();
    client.notice(t(current.data.web?'Saved. Applying web settings.':'Saved.'));
  }
  async function openLive(row) {
    const project=row.project || row.apiary,agent=row.agent || row.name,id=JSON.stringify([project,agent]);
    if(liveRuns.has(id))return;
    const panel=el('section',undefined,'live-panel'),title=el('h2',project+' · '+agent),output=el('pre',undefined,'live-output');
    panel.append(title,button('Close',()=>{liveRuns.delete(id);panel.remove();}),output);$('live').append(panel);panel.scrollIntoView({block:'nearest',behavior:'smooth'});
    liveRuns.set(id,{project,agent,output,loading:false,initial:true});await pollLive(liveRuns.get(id));
  }
  async function pollLive(run) {
    if(run.loading || !run.output.isConnected)return;run.loading=true;
    try{const result=run.kind==='orch'?{lines:(client.state.log||[]).filter((line,index)=>{const meta=client.state.log_meta?.[index]||{};return meta.project===run.project && meta.hive===run.hive;}).map(line=>['text',line.join(' ')])}:await client.request('live?'+new URLSearchParams({project:run.project,agent:run.agent}));const follow=run.initial || run.output.scrollHeight-run.output.scrollTop-run.output.clientHeight<25;
      run.output.replaceChildren();for(const [colour,text] of result.lines){run.output.append(el('div',text,colour));}if(!result.lines.length)run.output.append(el('span',t('No live output yet.')));if(follow)run.output.scrollTop=run.output.scrollHeight;run.initial=false;
    }catch(error){if(run.output.isConnected)run.output.textContent=error.message;}finally{run.loading=false;}
  }
  function renderLogin(run) {
    let panel=$('login-'+run.id);
    if(!panel){panel=el('section',undefined,'live-panel');panel.id='login-'+run.id;panel.append(el('h2',run.provider+' · '+t('Login')),el('pre'),el('div',undefined,'login-links'));const form=el('form'),input=el('input');input.setAttribute('aria-label',t('Code or input'));input.autocomplete='off';const send=el('button',t('Send'));send.type='submit';form.append(input,send);form.onsubmit=async event=>{event.preventDefault();await runButton(send,async()=>{await client.action('login/input',{id:run.id,text:input.value});input.value='';});};panel.append(form);$('live').append(panel);}
    panel.querySelector('pre').textContent=run.output;
    const links=panel.querySelector('.login-links');links.replaceChildren();
    for(const url of safeLinks(run.links || [])){const link=el('a',url);link.href=url;link.target='_blank';link.rel='noopener noreferrer';links.append(link,el('br'));}
    panel.querySelector('input').disabled=!run.running;panel.querySelector('form button').disabled=!run.running;
    if(!run.running){links.append(el('p',t('Login finished')+' ('+run.exit+')'));panel.append(button('Close',()=>{panel.remove();loginRuns.delete(run.id);}));}
  }
  function renderJob(job) {
    let panel=$('job-'+job.id);if(!panel){panel=el('section');panel.id='job-'+job.id;$('live').append(panel);}
    panel.replaceChildren(el('h2',t(job.title || job.key || 'Owner action')),valueView(job));
    if(!job.running)panel.append(button('Close',()=>{jobs.delete(job.id);panel.remove();}));
  }
  async function refreshLists() {
    const tasks=[];
    if(['Plans','Questions'].includes(tab)&&!planLoading){planLoading=true;tasks.push(client.request('plans').then(value=>{if(JSON.stringify(plans)!==JSON.stringify(value)){plans=value;if(['Plans','Questions'].includes(tab))render();}}).finally(()=>planLoading=false));}
    if(['Questions','Plans'].includes(tab)&&!questionLoading){questionLoading=true;tasks.push(client.request('questions?history=1').then(value=>{
      if(JSON.stringify(questions)!==JSON.stringify(value)){questions=value;if(['Questions','Plans'].includes(tab))render();}
    }).finally(()=>questionLoading=false));}
    if(tab==='Providers'&&!providerLoading){providerLoading=true;tasks.push(client.request('providers').then(value=>{if(JSON.stringify(value)!==JSON.stringify(providerStates)){providerStates=value;if(tab==='Providers'&&!editor?.dirty)render();}}).finally(()=>providerLoading=false));}
    if(tab==='Dashboard'&&!updateLoading){updateLoading=true;tasks.push(client.request('updates').then(value=>{updateState=value;renderUpdates();}).finally(()=>updateLoading=false));}
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
  window.addEventListener('beforeunload',event=>{if(editor?.dirty || planDraft?.dirty || [...questionDrafts.values()].some(draft=>draft.text||draft.packages)){event.preventDefault();event.returnValue='';}});
  if(typeof MutationObserver!=='undefined')installCopyButtons(document,window.navigator,t,(...args)=>client.notice(...args));
  render();
  void client.request('jobs/status').then(history=>{
    const previous=el('details');previous.append(el('summary',t('Previous owner actions')));
    for(const job of history){if(!jobs.has(job.id)){jobs.set(job.id,job);renderJob(job);if(!job.running)previous.append($('job-'+job.id));}}
    if(previous.children.length>1)$('live').append(previous);
  }).catch(error=>client.notice(error.message,true));
  void tick();setInterval(()=>void tick(),2000);
  return client;
}
if(typeof module!=='undefined')module.exports={settingsCategory,workflowSummary,testTimingNotice,LogPager,dashboardLoading,hiveTree,pendingQueue,planProcessGroups,credentialStatus,loginStatus,copyOutput,installCopyButtons,dashboardPlanHeight,orchestrationFor,dashboardMetrics,dashboardRows,Client,RenderGate,planActions,resolvePlanActions,setValue,safeLinks,toolsFromArgs,withTools,orchestratorDisabled,capabilityRows,isPackagePlaceholder,hiveCounts,visibleHiveRows,providerCli,applyProviderCli,switchProviderAccess,mount};
if(typeof document!=='undefined')mount(document,JSON.parse(document.getElementById('bootstrap').textContent));
