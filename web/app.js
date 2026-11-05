'use strict';
const $ = (s, root = document) => root.querySelector(s);
const h = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statusNames = {new:'新建',in_progress:'处理中',waiting_customer:'待客户补充',waiting_engineering:'待技术处理',pending_confirmation:'待客户确认',resolved:'已解决',running:'执行中',completed:'已完成',failed:'失败',draft:'待审核',published:'已发布',investigating:'调查中',fixed:'已修复'};
const moduleNames = {import:'订单导入',export:'报表导出',permission:'角色权限',general:'通用'};
const decisionNames = {answer:'已有处理建议',clarify:'需要补充信息',escalate:'建议升级处理'};
const badge = status => `<span class="status ${h(status)}">${h(statusNames[status] || status)}</span>`;
const when = value => value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '—';
const empty = text => `<div class="empty">${h(text)}</div>`;
const presets = {'customer-a':'demo-customer-a','customer-b':'demo-customer-b',support:'demo-support',engineer:'demo-engineer'};
const state = {token:sessionStorage.getItem('rd-token') || '',role:sessionStorage.getItem('rd-role') || 'customer-a',customer:'acme',page:'reception',me:null,config:null,result:null,selectedTicket:null,knowledgeTab:'published',epoch:0};
let toastTimer;
function toast(message){clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').hidden=false;toastTimer=setTimeout(()=>$('#toast').hidden=true,6500)}
async function api(path, options={}){
  const response = await fetch(path,{...options,headers:{'Content-Type':'application/json',Authorization:`Bearer ${state.token}`,...options.headers}});
  let data;try{data=await response.json()}catch{throw new Error(`服务返回异常 (${response.status})`)}
  if(!response.ok){let detail=data.detail;throw new Error(typeof detail==='string'?detail:detail?.message || JSON.stringify(detail))}
  return data;
}
const send = (path,body,method='POST') => api(path,{method,body:JSON.stringify(body)});
const scoped = path => `${path}${path.includes('?')?'&':'?'}customer_id=${encodeURIComponent(state.customer)}`;
const staff = () => state.me && state.me.role !== 'customer';
const engineer = () => state.me?.role === 'engineer';
const currentCustomer = () => state.me?.customers.find(c=>c.id===state.customer);
const iconPaths = ['M3 4h14v10H8l-5 4V4Z','M4 3h12v14H4z M7 7h6 M7 10h6 M7 13h4','M10 3v7m0 4v1 M3 17h14L10 3Z','M3 4h6l1 2h7v11H3z','M3 16l4-4 3 2 6-9 M13 5h3v3','M3 5h14 M3 10h14 M3 15h9'];
const navItems = [['reception','服务接待'],['tickets','工单工作台'],['problems','已知问题'],['knowledge','知识中心'],['search','检索实验'],['runs','运行记录']];
function navigation(){
  $('#nav').innerHTML=navItems.map(([id,name],i)=>id==='problems'&&!staff()?'':`<a href="#${id}" class="${state.page===id?'active':''}" ${state.page===id?'aria-current="page"':''}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="${iconPaths[i]}"/></svg>${name}</a>`).join('');
  $('#crumb').textContent=navItems.find(n=>n[0]===state.page)?.[1] || '服务接待';
}
function head(title,subtitle,meta=''){return `<div class="page-head"><div><h1>${h(title)}</h1><p>${h(subtitle)}</p></div><span class="meta">${h(meta)}</span></div>`}
function traceHTML(trace=[]){return `<details class="trace"><summary>查看执行轨迹 · ${trace.length} 个事件</summary>${trace.map(t=>`<div class="trace-entry"><div class="row spread"><span><span class="mono">${h(t.agent)}</span> / ${h(t.event==='tool'?t.detail.name:t.event)}</span><span>${h(t.duration_ms)} ms</span></div><pre>${h(JSON.stringify(t.detail,null,2))}</pre></div>`).join('')}</details>`}
function citationsHTML(result){
  const ids=result.resolution?.citation_ids || [];
  return (result.evidence?.knowledge||[]).filter(c=>ids.includes(c.id)).map(c=>`<details class="citation"><summary>${h(c.title)} <span class="muted small">· ${h(c.version)} · ${h(c.id)}</span></summary><div class="content">${h(c.content)}</div></details>`).join('');
}
function resultHTML(result,allowEscalate=true){
  const r=result.resolution;
  if(!r)return empty('此运行没有生成答复');
  return `<section class="panel result" aria-label="调查结果"><div class="answer-heading"><h2>处理建议</h2><span class="chip">${h(decisionNames[r.decision])}</span></div><div class="answer">${h(r.answer)}</div>${citationsHTML(result)}${r.suggested_problem_id?`<p class="note">已知问题候选：<span class="mono">${h(r.suggested_problem_id)}</span>，提交工单后由客服确认关联。</p>`:''}${r.decision==='escalate'&&allowEscalate?`<details class="citation" open><summary>待提交的升级草稿</summary><div class="content"><strong>${h(r.summary)}</strong>\n${h(r.reproduction)}</div></details><div class="actions"><button class="primary" data-action="escalate" data-id="${h(result.id)}">确认提交技术工单</button></div>`:''}<div class="note">${h(result.id)} · ${h(result.duration_ms)} ms · ${result.mode==='demo'?'离线规则演示':'真实模型调用'}</div>${traceHTML(result.trace)}</section>`;
}
async function connect(){
  state.config=await fetch('/api/config').then(r=>r.json());
  const demo=state.config.mode==='demo';
  $('#role').hidden=!demo;
  if(!state.token&&demo)state.token=presets[state.role];
  if(!state.token){$('#token-dialog').showModal();throw new Error('请填写访问凭证后连接')}
  state.me=await api('/api/me');
  sessionStorage.setItem('rd-token',state.token);sessionStorage.setItem('rd-role',state.role);
  state.customer=state.me.customers.some(c=>c.id===state.customer)?state.customer:state.me.customers[0].id;
  $('#customer').innerHTML=state.me.customers.map(c=>`<option value="${h(c.id)}">${h(c.name)} · v${h(c.version)}</option>`).join('');
  $('#customer').value=state.customer;$('#role').value=state.role;
  $('#mode-label').textContent=demo?'离线演示 · 数据为虚构样例':'真实模型模式';
  $('#footer-mode').textContent=demo?'规则 Agent + 词法哈希向量 / 不代表真实模型效果':`LLM: ${state.config.llm} · Embedding: ${state.config.embedding}`;
  await render();
}
async function render(){
  const epoch=++state.epoch;
  state.page=location.hash.slice(1).split('/')[0]||'reception';
  if(!navItems.some(n=>n[0]===state.page)||(!staff()&&state.page==='problems'))state.page='reception';
  navigation();document.body.classList.remove('menu-open');$('#menu').setAttribute('aria-expanded','false');
  $('#main').innerHTML='<div class="loading">正在读取业务数据…</div>';
  try{const html=await ({reception:receptionPage,tickets:ticketsPage,problems:problemsPage,knowledge:knowledgePage,search:searchPage,runs:runsPage}[state.page])();if(epoch===state.epoch)$('#main').innerHTML=html}
  catch(e){if(epoch===state.epoch)$('#main').innerHTML=`${head('连接未完成','请检查服务状态和访问凭证')}<p class="error-inline">${h(e.message)}</p><button class="secondary" data-action="refresh">重新读取</button>`}
}
async function receptionPage(){
  const tasks=await api(scoped('/api/tasks'));const customer=currentCustomer();
  return `${head('服务接待','先了解问题，再用产品知识和运行记录核实。',`${customer.name} / v${customer.version}`)}<div class="grid"><div><section class="panel"><div class="panel-head"><h2>遇到了什么问题？</h2><span class="small muted">商舟订单云</span></div><div class="examples">${(state.customer==='acme'?[['导入失败','升级后订单导入失败，任务 IMP-1001，提示 E2013，怎么处理？'],['导出超时','报表导出一直超时，任务 EXP-1002，帮我调查并提交工单。'],['权限问题','AUTH-1003 提示 E4030，为什么我不能导出报表？']]:[['导入失败','IMP-2001 提示 E2013，订单导入缺少什么字段？'],['导出进度','查询 EXP-2002 导出状态。']]).map(([label,q])=>`<button class="example" data-action="example" data-question="${h(q)}">${label}</button>`).join('')}</div><form id="ask-form"><label for="question">问题描述<textarea id="question" class="question-input" maxlength="2000" required placeholder="例如：升级后批量导入订单失败，任务编号 IMP-1001，错误码 E2013。">${h(state.question||'')}</textarea></label><div class="row spread wrap"><label class="row" style="margin:0">检索策略<select id="strategy"><option value="hybrid">混合检索 · BM25 + 向量</option><option value="bm25">BM25 关键词</option><option value="vector">向量检索</option></select></label><button class="primary" id="ask-button">开始调查</button></div>${state.continueTicket?`<p class="note">继续工单 ${h(state.continueTicket)} 的调查 <button type="button" class="text-button" data-action="clear-context">取消关联</button></p>`:''}</form><p class="mode-note">${state.config.mode==='demo'?'当前使用规则式 Agent 与词法哈希向量，可演示完整业务流程；真实模型与语义检索需在 .env 中配置。':'当前使用真实模型。调查阶段仅查询证据，创建工单需要你确认。'}</p></section><div id="answer-slot" aria-live="polite">${state.result?resultHTML(state.result):''}</div></div><aside class="context-column"><section class="panel"><h2>当前客户环境</h2><div class="kv"><span>客户</span><span>${h(customer.name)}</span></div><div class="kv"><span>产品版本</span><span class="mono">v${h(customer.version)}</span></div><div class="kv"><span>套餐</span><span>${h(customer.plan)}</span></div><div class="kv"><span>知识范围</span><span>共享文档 + 当前客户案例</span></div><p class="note">环境信息由服务端绑定，模型不能更改客户身份。</p></section><section class="panel"><h2>可调查的任务</h2>${tasks.map(t=>`<div class="task"><div class="row spread"><button data-action="task-example" data-id="${h(t.id)}">${h(t.id)}</button>${badge(t.status)}</div><p>${h(moduleNames[t.module])} · ${h(t.error_code||'无错误码')}</p></div>`).join('')||empty('暂无运行任务')}</section></aside></div>`;
}
let ticketCache=[];
async function ticketsPage(){
  const rows=await api(scoped('/api/tickets'));ticketCache=rows;
  if(!rows.some(t=>t.id===state.selectedTicket))state.selectedTicket=rows[0]?.id||null;
  const detail=state.selectedTicket?await ticketDetailHTML(state.selectedTicket):empty('暂无工单。可在服务接待中调查问题，并确认提交升级草稿。');
  const active=rows.filter(t=>t.status!=='resolved').length;
  return `${head('工单工作台','让调查证据随工单流转，处理完成后由客户确认。')}<div class="toolbar"><span class="counter">${rows.length} 个工单 · ${active} 个待跟进（最多展示最近 100 个）</span><button class="secondary" data-action="refresh">刷新工单</button></div><div class="ticket-layout"><div class="ticket-list">${rows.map(t=>`<button class="ticket-row ${t.id===state.selectedTicket?'selected':''}" data-action="select-ticket" data-id="${h(t.id)}"><div class="row spread"><span class="mono">${h(t.id)}</span>${badge(t.status)}</div><strong>${h(t.title)}</strong><div class="row spread"><span>${h(moduleNames[t.module])} · v${h(t.version)}</span><span>rev ${t.revision}</span></div></button>`).join('')||empty('尚未提交工单')}</div><div id="ticket-detail">${detail}</div></div>`;
}
let selectedTicketData;
async function ticketDetailHTML(id){
  const t=await api(`/api/tickets/${encodeURIComponent(id)}`);selectedTicketData=t;
  const transitions={new:['in_progress','waiting_customer','waiting_engineering'],in_progress:['waiting_customer','waiting_engineering','pending_confirmation'],waiting_customer:['in_progress','waiting_engineering','pending_confirmation'],waiting_engineering:['in_progress','waiting_customer','pending_confirmation'],pending_confirmation:['in_progress','waiting_customer','waiting_engineering'],resolved:['in_progress']};
  const allowed=staff()?transitions[t.status]:({pending_confirmation:['resolved','in_progress'],waiting_customer:['in_progress'],resolved:['in_progress']}[t.status]||[]);
  const problems=staff()?await api('/api/problems'):[];
  const matching=problems.filter(p=>p.module===t.module&&p.version===t.version);
  return `<section class="panel"><div class="row spread wrap"><span class="mono">${h(t.id)}</span>${badge(t.status)}</div><h2 class="detail-title">${h(t.title)}</h2><div class="badge-line"><span class="chip">${h(moduleNames[t.module])}</span><span class="chip">v${h(t.version)}</span>${t.problem_id?`<span class="chip">关联 ${h(t.problem_id)}</span>`:''}</div><p class="note">更新于 ${when(t.updated_at)} · 修订号 ${t.revision}</p><details class="citation"><summary>查看原始问题与调查证据</summary><div class="content">${h(t.evidence.question)}\n\n${h(t.evidence.resolution.reproduction)}</div>${citationsHTML(t.evidence)}${traceHTML(t.evidence.trace)}</details><div class="timeline">${t.events.map(e=>`<div class="timeline-item"><small>${h(e.actor)} · ${when(e.created_at)} · ${h(statusNames[e.status])}</small><p>${h(e.note)}</p></div>`).join('')}</div>${allowed.length?`<hr class="divider"><form id="ticket-form"><div class="form-grid"><label>下一步状态<select id="ticket-status">${allowed.map(s=>`<option value="${s}">${h(statusNames[s])}</option>`).join('')}</select></label>${staff()?`<label>关联已知问题（需人工核实）<select id="ticket-problem"><option value="">保持当前关联</option>${matching.map(p=>`<option value="${h(p.id)}">${h(p.id)} · ${h(p.title)}</option>`).join('')}</select></label>`:''}</div><label>处理说明<textarea id="ticket-note" minlength="2" maxlength="3000" required placeholder="填写已完成的检查、处理方案或客户反馈。"></textarea></label><button class="primary">记录处理并更新状态</button></form>`:''}<div class="actions"><button class="secondary" data-action="continue-ticket" data-id="${h(t.id)}">带上下文继续调查</button>${staff()&&t.status==='resolved'?`<button class="secondary" data-action="draft" data-id="${h(t.id)}">生成案例知识草稿</button>`:''}</div></section>`;
}
async function problemsPage(){
  const rows=await api('/api/problems');
  return `${head('已知问题','把相同问题的客户工单关联起来，统一记录技术处理结果。')}<p class="screen-notice">修复后，关联且处于处理中的工单转为“待客户确认”；系统不会自动替客户关闭工单。</p>${rows.map(p=>`<section class="problem"><div class="row spread"><span class="mono">${h(p.id)}</span>${badge(p.status)}</div><h2>${h(p.title)}</h2><p class="small muted">${h(moduleNames[p.module])} · v${h(p.version)} · 修订号 ${p.revision}</p><p class="description">${h(p.description)}</p>${p.resolution?`<p class="description">处理结果：${h(p.resolution)}</p>`:''}${engineer()?`<form class="problem-update-form" data-id="${h(p.id)}" data-revision="${p.revision}"><label>处理结果<textarea name="resolution" minlength="3" required placeholder="描述已验证的修复或临时处理方案。">${h(p.resolution)}</textarea></label><div class="row spread"><select name="status" aria-label="问题状态"><option value="fixed">已修复，等待客户确认</option value="investigating">继续调查</option></select><button class="primary">保存处理结果</button></div></form>`:''}</section>`).join('')}${engineer()?`<section class="panel"><h2>登记新的产品问题</h2><form id="problem-create-form"><label>问题标题<input name="title" minlength="3" maxlength="200" required></label><div class="form-grid"><label>模块<select name="module">${Object.entries(moduleNames).map(([v,n])=>`<option value="${v}">${n}</option>`).join('')}</select></label><label>适用版本<select name="version"><option>2.0</option><option>1.0</option></select></label></div><label>问题描述<textarea name="description" minlength="3" required></textarea></label><button class="primary">登记产品问题</button></form></section>`:''}`;
}
async function knowledgePage(){
  const docs=await api(scoped('/api/knowledge'));const drafts=staff()?await api('/api/knowledge-drafts'):[];
  if(!staff())state.knowledgeTab='published';
  const content=state.knowledgeTab==='drafts'?drafts.map(d=>`<section class="panel"><div class="row spread"><span class="mono">${h(d.id)}</span>${badge(d.status)}</div><p class="note">来源工单 ${h(d.ticket_id)} · v${h(d.version)} · 客户范围 ${h(d.customer_id)}</p><form class="draft-form" data-id="${h(d.id)}"><label>标题<input name="title" value="${h(d.title)}" required ${d.status==='published'?'readonly':''}></label><label>案例内容<textarea name="content" minlength="20" required rows="8" ${d.status==='published'?'readonly':''}>${h(d.content)}</textarea></label>${d.status==='draft'?`<div class="actions"><button class="secondary">保存草稿</button>${engineer()?`<button type="button" class="primary" data-action="publish" data-id="${h(d.id)}">审核并发布当前内容</button>`:'<span class="small muted">待技术人员审核发布</span>'}</div>`:''}</form></section>`).join('')||empty('暂无知识草稿。已解决工单可以生成案例草稿。'):`<div class="grid"><section class="panel"><div class="panel-head"><h2>产品文档与案例</h2><small>${docs.length} 篇文档</small></div>${docs.map(d=>`<article class="doc-row"><h3>${h(d.title)}</h3><p>${h(moduleNames[d.module])} · ${h(d.version==='all'?'跨版本':'v'+d.version)} · ${d.chunks} 个片段 · ${d.customer_id?'当前客户案例':'共享产品文档'}</p><details><summary>阅读文档</summary><div class="doc-content">${h(d.content)}</div></details></article>`).join('')}</section><aside><section class="panel"><h2>发布规则</h2><p class="small muted">共享产品文档由技术人员维护。工单案例先生成草稿、人工核对后发布，始终保留客户隔离。</p><p class="small muted">工单重新打开时，对应案例撤下，避免失效方案继续被检索。</p></section>${engineer()?`<section class="panel"><h2>添加共享文档</h2><form id="document-form"><label>文档标题<input name="title" minlength="2" required></label><div class="form-grid"><label>模块<select name="module">${Object.entries(moduleNames).map(([v,n])=>`<option value="${v}">${n}</option>`).join('')}</select></label><label>版本<select name="version"><option>2.0</option><option>1.0</option><option value="all">跨版本</option></select></label></div><label>导入文本文件<input type="file" id="document-file" accept=".md,.txt"></label><label>Markdown / 纯文本<textarea name="content" id="document-content" minlength="20" maxlength="50000" required rows="7"></textarea></label><button class="primary">发布并建立索引</button></form></section>`:''}</aside></div>`;
  return `${head('知识中心','让解决方案经过审核，再成为下一次调查的依据。')}<div class="tabs"><button data-action="knowledge-tab" data-tab="published" class="${state.knowledgeTab==='published'?'active':''}">已发布知识 · ${docs.length}</button>${staff()?`<button data-action="knowledge-tab" data-tab="drafts" class="${state.knowledgeTab==='drafts'?'active':''}">案例审核 · ${drafts.filter(d=>d.status==='draft').length} 待处理</button>`:''}</div>${content}`;
}
async function searchPage(){
  return `${head('检索实验','对同一个问题比较三种策略，检查版本、来源和排名。')}<section class="panel"><form id="search-form"><label>检索问题<input name="query" value="升级后 E2013 导入失败" maxlength="2000" required></label><div class="row spread wrap"><div class="row wrap"><select name="version" aria-label="检索版本" ${staff()?'':'disabled'}>${['2.0','1.0'].map(v=>`<option value="${v}" ${v===currentCustomer().version?'selected':''}>产品 v${v}</option>`).join('')}</select><select name="module" aria-label="检索模块"><option value="general">全部模块</option><option value="import">订单导入</option><option value="export">报表导出</option><option value="permission">角色权限</option></select></div><button class="primary">比较三种检索策略</button></div></form><p class="note">RRF 分数用于融合排名，不是回答置信度。${state.config.mode==='demo'?'演示向量为词法特征哈希，不能用于证明语义检索能力。':'向量阈值为初始工程参数，需要用独立评测集校准。'}</p></section><div id="search-results" aria-live="polite"></div>`;
}
async function runsPage(){
  const [rows,stats]=await Promise.all([api(scoped('/api/runs')),api(scoped('/api/stats'))]);
  return `${head('运行记录','查看每次调查的路由、工具证据、处理建议与失败原因。')}<div class="stats-strip"><div><strong>${stats.documents}</strong><span>可见知识文档</span></div><div><strong>${stats.chunks}</strong><span>可检索片段</span></div><div><strong>${stats.active_runs}</strong><span>正在调查</span></div></div><section class="panel"><div class="panel-head"><h2>最近调查</h2><small>当前客户 · 最近 ${rows.length} 条</small></div><div class="table-wrap"><table><thead><tr><th>客户问题</th><th>状态</th><th>模式</th><th class="number">耗时 / ms</th><th>创建时间</th></tr></thead><tbody>${rows.map(r=>`<tr><td><button class="text-button" data-action="view-run" data-id="${h(r.id)}">${h(r.question)}</button></td><td>${badge(r.status)}</td><td>${r.mode==='demo'?'演示':'真实模型'}</td><td class="number mono">${r.duration_ms??'—'}</td><td class="small muted">${when(r.created_at)}</td></tr>`).join('')}</tbody></table>${rows.length?'':empty('尚未发起调查')}</div></section><div id="run-detail"></div>`;
}
async function busy(button,fn){if(button)button.disabled=true;try{return await fn()}finally{if(button?.isConnected)button.disabled=false}}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-action]');if(!button)return;
  const action=button.dataset.action;
  try{await busy(button,async()=>{
    if(action==='refresh')return render();
    if(action==='example'||action==='task-example'){const q=button.dataset.question||`请调查任务 ${button.dataset.id} 的运行情况，并说明如何处理。`;$('#question').value=q;$('#question').focus();return}
    if(action==='clear-context'){state.continueTicket=null;return render()}
    if(action==='escalate'){const r=await send(`/api/runs/${button.dataset.id}/escalate`,{idempotency_key:`ui-${button.dataset.id}`});state.selectedTicket=r.id;toast(r.replayed?'已打开已有工单':'技术工单已提交');location.hash='tickets';if(state.page==='tickets')await render();return}
    if(action==='select-ticket'){state.selectedTicket=button.dataset.id;return render()}
    if(action==='continue-ticket'){state.continueTicket=button.dataset.id;state.question=`工单 ${button.dataset.id} 的问题仍未解决，请继续调查。`;state.result=null;location.hash='reception';return}
    if(action==='draft'){await send(`/api/tickets/${button.dataset.id}/knowledge-draft`,{});state.knowledgeTab='drafts';toast('案例草稿已生成，等待审核');location.hash='knowledge';return}
    if(action==='knowledge-tab'){state.knowledgeTab=button.dataset.tab;return render()}
    if(action==='publish'){const form=button.closest('form');await send(`/api/knowledge-drafts/${button.dataset.id}`,Object.fromEntries(new FormData(form)),'PATCH');await send(`/api/knowledge-drafts/${button.dataset.id}/publish`,{});toast('审核发布完成，已建立客户范围索引');return render()}
    if(action==='view-run'){const r=await api(`/api/runs/${button.dataset.id}`);$('#run-detail').innerHTML=r.status==='completed'?resultHTML({...r.result,id:r.id,mode:r.mode,duration_ms:r.duration_ms}):`<section class="panel result"><h2>运行未完成</h2><p class="error-message">${h(r.error||r.status)}</p>${traceHTML(r.result?.trace)}</section>`;$('#run-detail').scrollIntoView({behavior:'smooth',block:'start'});return}
  })}catch(e){toast(e.message)}
});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.matches('#ask-form,#ticket-form,#problem-create-form,.problem-update-form,#document-form,.draft-form,#search-form'))return;
  event.preventDefault();const button=form.querySelector('button[type="submit"],button:not([type])');const epoch=state.epoch;
  try{await busy(button,async()=>{
    if(form.id==='ask-form'){
      state.question=$('#question').value;$('#answer-slot').innerHTML='<div class="inline-loading">正在分诊、检索知识并核对运行记录…</div>';
      try{const result=await send('/api/ask',{question:state.question,customer_id:state.customer,strategy:$('#strategy').value,ticket_id:state.continueTicket||null});if(epoch!==state.epoch)return;state.result=result;$('#answer-slot').innerHTML=resultHTML(result)}catch(e){if(epoch===state.epoch)$('#answer-slot').innerHTML=`<p class="error-inline">${h(e.message)}</p>`;throw e}return;
    }
    if(form.id==='ticket-form'){const t=selectedTicketData;await send(`/api/tickets/${t.id}`,{status:$('#ticket-status').value,note:$('#ticket-note').value,expected_revision:t.revision,problem_id:$('#ticket-problem')?.value||null},'PATCH');toast('工单状态已更新');return render()}
    if(form.id==='problem-create-form'){await send('/api/problems',Object.fromEntries(new FormData(form)));toast('产品问题已登记');return render()}
    if(form.matches('.problem-update-form')){await send(`/api/problems/${form.dataset.id}`,{...Object.fromEntries(new FormData(form)),expected_revision:Number(form.dataset.revision)},'PATCH');toast('问题处理结果已保存');return render()}
    if(form.id==='document-form'){await send('/api/knowledge',Object.fromEntries([...new FormData(form)].filter(([k])=>k!=='file')));toast('共享文档已发布并完成索引');return render()}
    if(form.matches('.draft-form')){await send(`/api/knowledge-drafts/${form.dataset.id}`,Object.fromEntries(new FormData(form)),'PATCH');toast('草稿已保存');return}
    if(form.id==='search-form'){
      const body={...Object.fromEntries(new FormData(form)),version:form.elements.version.value,customer_id:state.customer,top_k:3};$('#search-results').innerHTML='<div class="inline-loading">正在比较检索策略…</div>';
      const results=await Promise.all(['bm25','vector','hybrid'].map(async strategy=>{const start=performance.now();const r=await send('/api/search',{...body,strategy});return {...r,ms:(performance.now()-start).toFixed(1)}}));
      if(epoch!==state.epoch)return;
      $('#search-results').innerHTML=`<div class="search-grid">${results.map(r=>`<section class="panel"><div class="panel-head"><h2>${h({bm25:'BM25 关键词',vector:'向量检索',hybrid:'混合检索'}[r.strategy])}</h2><small>${r.ms} ms</small></div>${r.hits.map((c,i)=>`<article class="hit"><div class="section-label">第 ${i+1} 位 · v${h(c.version)} · ${h(c.scope)}</div><h3>${h(c.title)}</h3><p>${h(c.content)}</p><div class="scores mono">BM25 ${c.bm25_score}<br>cosine ${c.cosine}<br>RRF ${c.rrf_score}</div></article>`).join('')||empty('没有达到召回条件的片段')}</section>`).join('')}</div>`;return;
    }
  })}catch(e){toast(e.message)}
});
document.addEventListener('change',async event=>{
  if(event.target.id==='document-file'){const file=event.target.files[0];if(!file)return;if(file.size>200000){toast('请使用小于 200 KB 的文本文件');return}$('#document-content').value=await file.text()}
});
$('#customer').addEventListener('change',()=>{state.customer=$('#customer').value;state.result=null;state.selectedTicket=null;state.continueTicket=null;state.question='';render()});
$('#role').addEventListener('change',async()=>{state.epoch++;state.role=$('#role').value;state.token=presets[state.role];state.result=null;state.selectedTicket=null;state.continueTicket=null;state.question='';try{await connect()}catch(e){toast(e.message)}});
$('#credentials').addEventListener('click',()=>{$('#token-input').value='';$('#token-dialog').showModal()});
$('#token-cancel').addEventListener('click',()=>$('#token-dialog').close());
$('#token-form').addEventListener('submit',async e=>{e.preventDefault();state.token=$('#token-input').value;try{await connect();$('#token-dialog').close();$('#token-input').value=''}catch(err){toast(err.message)}});
$('#menu').addEventListener('click',()=>{const open=document.body.classList.toggle('menu-open');$('#menu').setAttribute('aria-expanded',String(open))});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.body.classList.remove('menu-open');$('#menu').setAttribute('aria-expanded','false')}});
window.addEventListener('hashchange',()=>{if(state.me)render()});
connect().catch(e=>{$('#main').innerHTML=`${head('连接工作区','填写访问凭证，或检查后端是否已经启动。')}<p class="error-inline">${h(e.message)}</p>`;toast(e.message)});
