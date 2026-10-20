const $ = (selector) => document.querySelector(selector);
const form = $('#strategy-form');
const state = { defaults: null, result: null, backtest: null, tab: 'signals', busy: false, runs: [], jobKind: null };
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (n, digits = 2) => n == null || !Number.isFinite(Number(n)) ? '—' : Number(n).toLocaleString('zh-CN', {minimumFractionDigits: digits, maximumFractionDigits: digits});
const sign = n => Number(n) > 0 ? '+' : '';
const color = n => n > 0 ? 'positive' : n < 0 ? 'negative' : '';
const currentStrategy = c => Number(c?.strategy_version)===2&&c?.mode==='minute';
function tradeTimeCell(day,start,end){return `${escapeHTML(day)}<br><span class="trade-time">${escapeHTML(start?start.slice(11,16)+'–'+end?.slice(11,16):end?.slice(11,16)||'时间未记录')}</span>`;}
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').classList.remove('hidden'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), 4500); }
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}
function readConfig() { return Object.fromEntries(new FormData(form).entries()); }
function fillConfig(config) {
  state.formLegacy = !currentStrategy(config);
  const values={...state.defaults,universe_limit:300,universe_scope:'market_cap',...config,
    strategy_version:2,mode:'minute',volume_recent:1};
  if(state.formLegacy)values.volume_ratio=1.5;
  Object.entries(values).forEach(([name, value]) => { if (form.elements[name]) form.elements[name].value = value; });
  describeForm();
}
function applyEntryFilters() {
  form.elements.entry_filters.value='on';
  form.elements.mode.value='minute';
  form.elements.strategy_version.value='2';form.elements.volume_recent.value='1';form.elements.volume_ratio.value='1.5';
  state.formLegacy=false;
  describeForm();
  $('#dirty-note').classList.toggle('hidden',!state.result);
  localStorage.setItem('weipan-config',JSON.stringify(readConfig()));
  localStorage.setItem('weipan-entry-filters-v1','1');
}
function describeForm() {
  const c = readConfig();
  const count=new Set((c.symbols||'').split(/[,，\s]+/).filter(Boolean)).size;
  $('#pool-count').textContent=`${count.toLocaleString()} 只`;
  const real = c.source === 'akshare';
  const notice = $('#source-notice');
  notice.classList.toggle('real', real);
  notice.querySelector('strong').textContent = real ? 'AKShare 数据源' : '演示模式';
  notice.querySelector(':scope > div > span').textContent = real ? '下次运行将下载真实 A 股行情与历史总市值。当前显示结果的来源以右侧标记为准。' : '当前选择合成行情验证策略流程，不代表真实市场收益。切换 AKShare 后可下载真实数据。';
  notice.querySelector('.notice-tag').textContent = real ? 'AKSHARE DATA' : 'SAMPLE DATA';
  $('#mode-note').textContent = `当日 ${c.selection_time} 形成信号，在同日下一根 5 分钟 K 线开盘买入；缺少分钟行情时停止，不顺延到次日。`;
  const entryOn=c.entry_filters==='on';
  $('#entry-filter-fields').classList.toggle('hidden',!entryOn);
  $('#entry-mode-note').textContent=entryOn?'免费分钟行情通常只覆盖近期，建议先选近期区间。':'已关闭 MA、上影线和 14:30 涨幅过滤，仍使用当日尾盘成交与当日放量条件。';
  $('#rule-summary').textContent = `总市值 ${c.market_cap_min}–${c.market_cap_max} 亿 → 近 ${c.limit_lookback} 日有涨停 → 至少 ${c.sideways_days} 日横盘 → 当日累计量 / 前 ${c.volume_base} 日均量 > ${c.volume_ratio} 倍 → 当日 ${c.selection_time} 后买入`+(entryOn?` → 价格 > MA${c.entry_ma_days} → 排除 ≥${c.burst_volume_ratio} 倍量且上影 ≥${c.upper_shadow_pct}% → 14:30 涨幅 > ${c.gain_1430_min}%`:'');
}
function setBusy(busy) {
  state.busy = busy;
  Array.from(form.elements).forEach(el => el.disabled = busy);
  $('#reset-btn').disabled = busy;
  $('#apply-entry-btn').disabled = busy;
  $('#progress-panel').classList.toggle('hidden', !busy);
  $('#run-btn').innerHTML = busy ? '<span class="spinner"></span> 策略运行中…' : '<span>▷</span> 运行策略回测';
}
function setError(message) {
  $('#error-panel').textContent = message + (state.result ? ' 下方保留上一次运行结果。' : '');
  $('#error-panel').classList.remove('hidden');
}
async function startJob(kind) {
  if (state.busy) return;
  if (!form.reportValidity()) return;
  const config = readConfig();
  if (kind === 'universe' && config.source !== 'akshare') {
    form.elements.source.value = 'akshare';
    config.source = 'akshare';
    describeForm();
  }
  localStorage.setItem('weipan-config', JSON.stringify(config));
  $('#error-panel').classList.add('hidden');
  $('#progress-message').textContent = kind === 'universe' ? '正在读取当前 A 股股票池…' : '准备数据与策略参数…';
  $('#progress-bar').style.width = '1%';
  $('#progress-number').textContent = '1%';
  state.jobKind = kind;
  setBusy(true);
  try {
    const job = await api(`/api/${kind}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(config)});
    localStorage.setItem('weipan-job', JSON.stringify({id:job.id, kind}));
    await pollJob(job.id, kind);
  } catch (error) {
    setError(error.message);
    localStorage.removeItem('weipan-job');
    setBusy(false);
  }
}
async function pollJob(id, kind) {
  try {
    const job = await api(`/api/jobs/${id}`);
    $('#progress-message').textContent = job.message;
    $('#progress-number').textContent = job.progress + '%';
    $('#progress-bar').style.width = job.progress + '%';
    if (job.status === 'running') { setTimeout(() => pollJob(id, kind), 900); return; }
    localStorage.removeItem('weipan-job');
    setBusy(false);
    if (job.status === 'error') throw new Error(job.message);
    if (kind === 'universe') {
      const stocks = job.result.stocks;
      if (!stocks.length) throw new Error('当前市值区间没有返回可用股票，请调整区间。');
      form.elements.symbols.value = stocks.map(s => s.symbol).join(',');
      describeForm();
      $('#pool-note').textContent=`${job.result.as_of?.slice(0,10)||''} · 匹配 ${job.result.total_count??stocks.length} 只，已导入 ${stocks.length} 只。固定代码抽样，不按收益选股。`;
      $('#dirty-note').classList.toggle('hidden', !state.result);
      localStorage.setItem('weipan-config', JSON.stringify(readConfig()));
      toast(`已导入 ${stocks.length} 只股票，可以按当前策略重新回测。`);
      return;
    }
    renderResult(job.result);
    refreshHistory();
    toast(kind === 'screen' ? '尾盘选股完成，已逐项核验条件' : '回测完成，结果已保存到本地');
  } catch (error) {
    localStorage.removeItem('weipan-job');
    setBusy(false);
    setError(error.message);
  }
}
function renderResult(result) {
  state.result = result;
  if (result.kind === 'backtest') state.backtest = result;
  const legacy=!currentStrategy(result.config);
  $('#dirty-note').classList.toggle('hidden',!legacy);
  $('#entry-result-note').classList.toggle('hidden',!legacy);
  $('#entry-result-note > span').textContent=result.config.mode==='daily'
    ?'旧版次日开盘成交记录，仅供查阅，不代表当日尾盘策略。左侧已改为同日尾盘与当日放量口径，重新回测后更新。'
    :'旧版均量规则记录，仅供查阅。左侧已改为当日累计量 / 前 N 日均量，重新回测后更新。';
  $('#result-title').textContent = result.kind === 'screen' ? '尾盘选股结果' : '策略回测概览';
  $('#result-mode').textContent = `${result.data.source === 'demo' ? '合成演示' : 'AKShare'} · ${legacy?(result.config.mode==='daily'?'旧版次日成交':'旧版均量规则'):(result.kind==='screen'?result.config.selection_time:'当日尾盘')}`;
  $('#result-date').textContent = result.kind === 'screen' ? `${result.screen_date} · ${result.universe_count} 只股票` : `${result.curve[0].date} — ${result.screen_date} · ${result.universe_count} 只`;
  $('#signal-count').textContent = result.signals.filter(s => s.eligible).length;
  const b = result.kind === 'backtest' ? result : null;
  $('#trade-count').textContent = b?.trades.length ?? 0;
  $('#holding-count').textContent = b?.holdings.length ?? 0;
  const m = b?.metrics;
  const metric = (selector, value, suffix = '', signed = false) => {
    const el = $(selector);
    el.innerHTML = `${signed && value != null ? sign(value) : ''}${fmt(value)}${suffix ? `<small>${suffix}</small>` : ''}`;
    el.className = signed && value != null ? color(value) : '';
  };
  metric('#metric-return', m?.total_return, '%', true);
  metric('#metric-drawdown', m?.max_drawdown, '%');
  metric('#metric-win', m?.win_rate, '%');
  metric('#metric-sharpe', m?.sharpe);
  $('#metric-annual').textContent = `年化收益 ${m ? sign(m.annual_return)+fmt(m.annual_return)+'%' : '—'}`;
  $('#metric-trades').textContent = `已平仓 ${m?.trade_count ?? 0} 笔交易`;
  $('#chart-subtitle').textContent = b ? `${result.data.label} · 初始资金 ¥${fmt(result.config.initial_cash,0)}` : '单次选股不产生收益曲线，运行回测后显示';
  $('#chart-summary').textContent = m ? `${m.trading_days} 个交易日 · 总费用 ¥${fmt(m.fees,0)}` : '— 个交易日';
  $('#warnings-list').innerHTML = result.warnings.map(w => `<li>${escapeHTML(w)}</li>`).join('');
  drawChart(b?.curve ?? []);
  setTab(result.kind === 'screen' ? 'signals' : state.tab);
}
function drawChart(curve) {
  const svg = $('#equity-chart');
  const W=Math.max(350,svg.clientWidth||900),H=W<500?245:285,L=46,R=16,T=15,B=35, cw=W-L-R,ch=H-T-B;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  $('#chart-empty').classList.toggle('hidden', !!curve.length);
  const all = curve.flatMap(p => [p.strategy,p.benchmark]);
  let min = all.length ? Math.min(1,...all) : .9;
  let max = all.length ? Math.max(1,...all) : 1.3;
  const pad = Math.max((max-min)*.17,.015); min-=pad; max+=pad;
  const x = i => L + i / Math.max(curve.length-1,1) * cw;
  const y = v => T+(max-v)/(max-min)*ch;
  let out = '<defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#4caa91" stop-opacity=".16"/><stop offset="100%" stop-color="#4caa91" stop-opacity="0"/></linearGradient></defs>';
  for(let i=0;i<5;i++) {
    const v=min+(max-min)*i/4, py=y(v);
    out+=`<line x1="${L}" y1="${py}" x2="${W-R}" y2="${py}" stroke="#eaf0ee" stroke-dasharray="3 5"/><text x="${L-12}" y="${py+4}" text-anchor="end">${v.toFixed(2)}</text>`;
  }
  if(curve.length) {
    const strategy=curve.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(2)} ${y(p.strategy).toFixed(2)}`).join(' ');
    const benchmark=curve.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(2)} ${y(p.benchmark).toFixed(2)}`).join(' ');
    out+=`<path d="${strategy} L${x(curve.length-1)} ${H-B} L${L} ${H-B} Z" fill="url(#area)"/><path d="${benchmark}" fill="none" stroke="#b2bfce" stroke-width="1.7" stroke-dasharray="5 4"/><path d="${strategy}" fill="none" stroke="#278d7b" stroke-width="2.3" stroke-linejoin="round"/><circle cx="${x(curve.length-1)}" cy="${y(curve.at(-1).strategy)}" r="3.5" fill="#278d7b" stroke="white" stroke-width="2"/>`;
    const tickCount=W<500?3:5;
    const ticks=[...new Set(Array.from({length:tickCount},(_,i)=>Math.round((curve.length-1)*i/(tickCount-1))))];
    ticks.forEach(i=>{out+=`<text x="${x(i)}" y="${H-8}" text-anchor="${i===0?'start':i===curve.length-1?'end':'middle'}">${curve[i].date}</text>`;});
    out+=`<line id="crosshair" x1="0" y1="15" x2="0" y2="${H-B}" stroke="#76a699" stroke-dasharray="3 3" visibility="hidden"/>`;
  }
  svg.innerHTML=out;
  svg.onpointermove = event => {
    if(!curve.length) return;
    const rect=svg.getBoundingClientRect(),px=(event.clientX-rect.left)/rect.width*W;
    const i=Math.max(0,Math.min(curve.length-1,Math.round((px-L)/cw*(curve.length-1)))),p=curve[i];
    const line=$('#crosshair');line.setAttribute('x1',x(i));line.setAttribute('x2',x(i));line.setAttribute('visibility','visible');
    const tip=$('#chart-tooltip');tip.innerHTML=`<strong>${p.date}</strong>策略净值 ${fmt(p.strategy,4)}<br>股票池等权 ${fmt(p.benchmark,4)}<br>当日回撤 ${fmt(p.drawdown)}%<br>持仓 ${p.positions} 只`;
    tip.classList.remove('hidden');tip.style.left=`${Math.max(0,Math.min(event.clientX-rect.left+12,rect.width-165))}px`;tip.style.top='25px';
  };
  svg.onpointerleave=()=>{$('#chart-tooltip').classList.add('hidden');$('#crosshair')?.setAttribute('visibility','hidden');};
}
function setTab(tab) {
  state.tab=tab;
  document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-selected',String(b.dataset.tab===tab));});
  $('#only-match-label').classList.toggle('hidden',tab!=='signals');
  $('#export-btn').classList.toggle('hidden',tab!=='trades'||state.result?.kind!=='backtest');
  renderTable();
}
function table(headers,rows) {
  $('#table-container').innerHTML=rows.length ? `<table><thead><tr>${headers.map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>` : '<div class="empty-table">当前没有符合条件的记录。调整参数或区间后重新运行。</div>';
}
function stockCell(r) {return `<td><span class="stock-name">${escapeHTML(r.name)}</span><span class="stock-code">${escapeHTML(r.symbol)}</span></td>`;}
function renderTable() {
  const r=state.result;
  if(state.tab==='history') {
    $('#table-description').textContent='最近 20 次研究记录 · 保存在本机';
    table(['运行时间','数据 / 模式','累计收益','交易数','操作'],state.runs.map(h=>`<tr><td>${escapeHTML(h.created_at.replace('T',' '))}</td><td>${h.config.source==='demo'?'合成演示':'AKShare'} · ${!currentStrategy(h.config)?(h.config.mode==='daily'?'旧版次日成交':'旧版均量规则'):h.kind==='screen'?'尾盘选股':'当日尾盘'}</td><td class="${color(h.metrics?.total_return)}">${h.metrics?fmt(h.metrics.total_return)+'%':'—'}</td><td>${h.metrics?.trade_count??'—'}</td><td><button class="history-load" data-id="${escapeHTML(h.id)}">载入</button></td></tr>`));
    return;
  }
  if(!r) {$('#table-container').innerHTML='<div class="empty-table">暂无结果，运行回测或尾盘选股后显示。</div>';return;}
  if(state.tab==='signals') {
    let rows=r.signals;
    if($('#only-match').checked) rows=rows.filter(s=>s.eligible);
    $('#table-description').textContent=`${r.screen_date} · ${r.kind==='screen'||r.config.mode==='minute'?r.config.selection_time:'收盘'} · ${r.universe_count} 只股票中 ${r.signals.filter(s=>s.eligible).length} 只符合`;
    const entryOn=r.config.entry_filters==='on';
    const titles={market_cap:'总市值',limit_up:'涨停记录',sideways:'横盘振幅',volume:'放量倍数',tradable:'基本交易状态',...(entryOn?{above_entry_ma:`价格 > MA${r.config.entry_ma_days}`,no_burst_shadow:'排除爆量上影线',gain_1430:`14:30 涨幅 > ${r.config.gain_1430_min}%`}:{})};
    table(['股票','价格','市值 / 亿','区间振幅','放量倍数',...(entryOn?[`MA${r.config.entry_ma_days}`,'14:30 涨幅','当日量 / 上影']:[]),'条件核验','信号'],rows.map(s=>`<tr>${stockCell(s)}<td>${fmt(s.price)}</td><td>${fmt(s.market_cap,1)}</td><td class="${s.checks.sideways?'check-pass':''}">${fmt(s.amplitude)}%</td><td class="${s.checks.volume?'check-pass':''}">${fmt(s.volume_ratio)}×</td>${entryOn?`<td class="${s.checks.above_entry_ma?'check-pass':''}">${fmt(s.entry_ma,3)}</td><td class="${s.checks.gain_1430?'check-pass':''}" title="14:30 价格 ${fmt(s.price_1430)}；前收 ${fmt(s.prev_close)}">${fmt(s.gain_1430,3)}%</td><td class="${s.checks.no_burst_shadow?'check-pass':''}">${fmt(s.day_volume_ratio)}× / ${fmt(s.upper_shadow_pct,1)}%</td>`:''}<td><div class="condition-dots">${Object.entries(titles).map(([k,v])=>`<i class="condition-dot ${s.checks[k]?'pass':''}" title="${escapeHTML(v)}：${s.checks[k]?'通过':'未通过'}" aria-label="${escapeHTML(v)}：${s.checks[k]?'通过':'未通过'}"></i>`).join('')}</div></td><td><span title="${escapeHTML(s.reason)}${s.last_limit?'；最近涨停 '+escapeHTML(s.last_limit):''}" class="status-pill ${s.eligible?'match':''}">${s.eligible?'符合条件':escapeHTML(s.reason==='历史数据不足'?'历史不足':'未入选')}</span></td></tr>`));
  }else if(state.tab==='trades') {
    const rows=r.trades??[];
    $('#table-description').textContent=`${rows.length} 笔已平仓交易 · ${currentStrategy(r.config)?'信号当日尾盘买入，时段为成交所在 5 分钟 K 线':'旧版记录，仅供查阅'} · 盈亏含费用`;
    table(['股票','信号时间','买入时段','卖出时段','买入 / 卖出价','股数','净盈亏','收益率','持有 / 日','卖出原因','复盘'],rows.map((t,index)=>({t,index})).reverse().map(({t,index})=>`<tr>${stockCell(t)}<td>${t.signal_time?tradeTimeCell(t.signal_time.slice(0,10),null,t.signal_time):'旧记录未保存'}</td><td>${tradeTimeCell(t.buy_date,t.buy_window_start,t.buy_time)}</td><td>${tradeTimeCell(t.sell_date,t.sell_window_start,t.sell_time)}</td><td>${fmt(t.buy_price)}<br>${fmt(t.sell_price)}</td><td>${fmt(t.quantity,0)}</td><td class="${color(t.pnl)}">${sign(t.pnl)}${fmt(t.pnl)}</td><td class="${color(t.return_pct)}">${sign(t.return_pct)}${fmt(t.return_pct)}%</td><td>${t.holding_days}</td><td><span class="status-pill">${escapeHTML(t.reason)}</span></td><td><button class="candle-open" data-candle="${escapeHTML(t.symbol)}" data-trade-index="${index}" title="查看 ${escapeHTML(t.name)} 日K与本笔买卖点">日 K ↗</button></td></tr>`));
  }else {
    $('#table-description').textContent=`期末持仓 ${r.holdings?.length??0} 只 · 可用现金 ¥${fmt(r.metrics?.cash)}`;
    table(['股票','买入时段','买入价','估值价格','股数','浮动盈亏'],(r.holdings??[]).map(p=>`<tr>${stockCell(p)}<td>${tradeTimeCell(p.buy_date,p.buy_window_start,p.buy_time)}</td><td>${fmt(p.buy_price)}</td><td>${fmt(p.price)}</td><td>${fmt(p.quantity,0)}</td><td class="${color(p.unrealized_pnl)}">${sign(p.unrealized_pnl)}${fmt(p.unrealized_pnl)}</td></tr>`));
  }
}
async function refreshHistory(){try{state.runs=await api('/api/runs');if(state.tab==='history')renderTable();}catch(error){toast(error.message);}}
form.addEventListener('submit',e=>{e.preventDefault();startJob('backtest');});
form.addEventListener('input',()=>{if(form.elements.entry_filters.value==='on')form.elements.mode.value='minute';describeForm();$('#dirty-note').classList.toggle('hidden',!state.result);localStorage.setItem('weipan-config',JSON.stringify(readConfig()));});
$('#apply-entry-btn').addEventListener('click',()=>{applyEntryFilters();toast('已应用当日尾盘与当日放量 1.5 倍规则，请重新回测');});
$('#recent-range-btn').addEventListener('click',()=>{form.elements.start_date.value=state.defaults.start_date;form.elements.end_date.value=state.defaults.end_date;form.dispatchEvent(new Event('input'));toast('已选择最近一周，运行后更新结果');});
$('#screen-btn').addEventListener('click',()=>startJob('screen'));
$('#universe-btn').addEventListener('click',()=>startJob('universe'));
$('#reset-btn').addEventListener('click',()=>{fillConfig(state.defaults);localStorage.removeItem('weipan-config');$('#dirty-note').classList.toggle('hidden',!state.result);toast('已恢复默认参数，运行后更新结果');});
$('#only-match').addEventListener('change',renderTable);
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>setTab(b.dataset.tab)));
document.querySelectorAll('[data-nav]').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('[data-nav]').forEach(n=>n.classList.toggle('active',n===b));
  $('#page-crumb').textContent=b.textContent.replace(/[◫⌕⇄◷]/g,'').trim();
  const target=b.dataset.nav;
  if(target==='overview')$('#overview-section').scrollIntoView({behavior:'smooth',block:'start'});
  else{setTab(target);$('#detail-section').scrollIntoView({behavior:'smooth',block:'start'});}
}));
$('#table-container').addEventListener('click',async e=>{
  const candle=e.target.closest('[data-candle]');
  if(candle){openCandle(state.result.id,candle.dataset.candle,Number(candle.dataset.tradeIndex));return;}
  const b=e.target.closest('[data-id]');if(!b)return;
  if(state.busy){toast('请等待当前任务完成后再载入历史结果');return;}
  try{const r=await api(`/api/runs/${b.dataset.id}`);fillConfig(r.config);state.tab='signals';renderResult(r);$('#error-panel').classList.add('hidden');toast(currentStrategy(r.config)?'已载入回测结果与对应参数':'已载入旧版结果；左侧已更新为当前尾盘规则，需重新回测');}catch(error){setError(error.message);}
});
$('#export-btn').addEventListener('click',()=>{if(state.result?.kind==='backtest')window.location.href=`/api/runs/${state.result.id}/trades.csv`;});
async function init(){
  const pageParams=new URLSearchParams(location.search),requestedRun=pageParams.get('run'),requestedTab=pageParams.get('tab');
  $('#today-label').textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date()).replaceAll('/','.');
  drawChart([]);
  try{
    const [defaults,health]=await Promise.all([api('/api/config'),api('/api/health')]);
    state.defaults=defaults;fillConfig(defaults);
    $('#health-label').textContent=health.akshare_installed?'AKShare 引擎就绪':'本地引擎就绪';
    let saved=null;
    try{saved=JSON.parse(localStorage.getItem('weipan-config'));if(saved)fillConfig(saved);}catch{}
    await refreshHistory();
    // A single-date screen has no equity curve; reopen a backtest on the workbench.
    const preferredSource=saved?.source||'akshare';
    const homeRun=state.runs.find(r=>r.kind==='backtest'&&currentStrategy(r.config)&&r.config.source===preferredSource)
      ||state.runs.find(r=>r.kind==='backtest'&&currentStrategy(r.config))
      ||state.runs.find(r=>r.kind==='backtest'&&r.config.source===preferredSource)
      ||state.runs.find(r=>r.kind==='backtest')||state.runs[0];
    const active=await api('/api/active-job');
    const pending=active||JSON.parse(localStorage.getItem('weipan-job')||'null');
    if(pending){if(active?.config)fillConfig(active.config);if(homeRun){renderResult(await api(`/api/runs/${homeRun.id}`));$('#dirty-note').classList.remove('hidden');}setBusy(true);pollJob(pending.id,pending.kind);}
    else if(requestedRun&&/^[a-f0-9]{12}$/.test(requestedRun)){const r=await api(`/api/runs/${requestedRun}`);fillConfig(r.config);renderResult(r);if(['signals','trades','holdings','history'].includes(requestedTab))setTab(requestedTab);}
    else if(homeRun){const r=await api(`/api/runs/${homeRun.id}`);if(!saved)fillConfig(r.config);renderResult(r);const current=readConfig();$('#dirty-note').classList.toggle('hidden',Object.keys(r.config).every(k=>String(r.config[k])===current[k]));}
    else if(form.elements.source.value==='demo'){startJob('backtest');}
    const oldPool='600000,600008,600017,600033,600039,600075,600081,600110,600160,600171,600201,600210,600256,600273,600282,600307,600350,600354,600366,600397,600500,600510,600517,600528,600580,600597,600601,600611,600660,600741';
    if(!requestedRun&&!state.busy&&defaults.symbols.split(',').length>30&&form.elements.symbols.value===oldPool&&!localStorage.getItem('weipan-expanded-pool')){
      form.elements.symbols.value=defaults.symbols;form.elements.universe_limit.value='300';describeForm();
      localStorage.setItem('weipan-expanded-pool','1');localStorage.setItem('weipan-config',JSON.stringify(readConfig()));
      $('#dirty-note').classList.toggle('hidden',!state.result);
    }
  }catch(error){setError(error.message);}
}
let resizeFrame;
window.addEventListener('resize',()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>drawChart(state.result?.kind==='backtest'?state.result.curve:[]));});
init();
