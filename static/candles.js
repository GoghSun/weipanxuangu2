const candleState={data:null,runId:null,symbol:null,index:null,start:0,count:60,request:null,allTrades:[]};
async function openCandle(runId,symbol,index){
  candleState.request?.abort();
  const controller=new AbortController();candleState.request=controller;
  if(state.result?.id===runId)candleState.allTrades=state.result.trades||[];
  else if(candleState.runId!==runId)candleState.allTrades=[];
  candleState.runId=runId;candleState.symbol=symbol;candleState.index=index;candleState.data=null;
  $('#candle-title').textContent=`${symbol} · 日 K`;
  $('#candle-source').textContent='按本次回测记录查看';
  $('#candle-content').classList.add('hidden');$('#candle-error').classList.add('hidden');$('#candle-loading').classList.remove('hidden');
  if(!$('#candle-dialog').open)$('#candle-dialog').showModal();
  try{
    const data=await api(`/api/runs/${runId}/candles/${symbol}`,{signal:controller.signal});
    if(controller!==candleState.request||!$('#candle-dialog').open)return;
    candleState.data=data;
    $('#candle-title').textContent=`${data.name} ${data.symbol} · 日 K`;
    $('#candle-source').textContent=`${data.source==='demo'?'合成演示行情':'AKShare 真实行情'} · 未复权 · ${data.bars.length} 个交易日`;
    $('#candle-trades').innerHTML=data.trades.map(t=>`<option value="${t.index}">${t.buy_date} → ${t.sell_date} · ${sign(t.return_pct)}${fmt(t.return_pct)}%</option>`).join('');
    if(!data.trades.some(t=>t.index===index))candleState.index=data.trades[0]?.index??null;
    $('#candle-trades').value=String(candleState.index);
    $('#candle-fast-label').textContent=`MA${data.ma_fast}`;$('#candle-slow-label').textContent=`MA${data.ma_slow}`;
    $('#candle-note').textContent=`${data.note} 买卖标记使用回测成交价，虚线为本笔买入价下方 ${data.stop_loss}% 的止损线。${data.mode==='daily'?'旧版次日开盘记录，仅供查阅，不代表当前当日尾盘策略。':'本记录为分钟回放，此处以日线展示成交日期。'}`;
    $('#candle-loading').classList.add('hidden');$('#candle-content').classList.remove('hidden');
    focusCandleTrade();
  }catch(error){
    if(error.name==='AbortError')return;
    $('#candle-loading').classList.add('hidden');$('#candle-error').textContent=error.message;$('#candle-error').classList.remove('hidden');
  }
}
function selectedCandleTrade(){return candleState.data?.trades.find(t=>t.index===candleState.index);}
function focusCandleTrade(){
  const data=candleState.data;if(!data)return;
  const t=selectedCandleTrade();
  const buy=t?data.bars.findIndex(b=>b.date>=t.buy_date):data.bars.length-60;
  const sell=t?data.bars.findIndex(b=>b.date>=t.sell_date):data.bars.length-1;
  candleState.count=Math.min(data.bars.length,Math.max(60,sell-buy+41));
  candleState.start=Math.max(0,Math.min(data.bars.length-candleState.count,buy-25));
  drawCandles();
}
function candleQuote(bar){
  if(!bar)return;
  const c=bar.close>=bar.open?'positive':'negative';
  $('#candle-quote').innerHTML=`<strong>${bar.date}</strong><span>开 <b class="${c}">${fmt(bar.open)}</b></span><span>高 <b>${fmt(bar.high)}</b></span><span>低 <b>${fmt(bar.low)}</b></span><span>收 <b class="${c}">${fmt(bar.close)}</b></span><span>成交量 <b>${fmt(bar.volume,0)} 手</b></span><span class="ma-fast">MA${candleState.data.ma_fast} ${fmt(bar.ma_fast)}</span><span class="ma-slow">MA${candleState.data.ma_slow} ${fmt(bar.ma_slow)}</span>`;
}
function drawCandles(){
  const data=candleState.data;if(!data||!$('#candle-dialog').open)return;
  const svg=$('#candle-chart'),total=data.bars.length;
  candleState.count=Math.max(Math.min(15,total),Math.min(total,Math.round(candleState.count)));
  candleState.start=Math.max(0,Math.min(total-candleState.count,Math.round(candleState.start)));
  const visible=data.bars.slice(candleState.start,candleState.start+candleState.count);
  const W=Math.max(700,svg.clientWidth||1080),H=470,L=62,R=18,T=22,P=320,VT=366,VB=432;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const step=(W-L-R)/visible.length,bw=Math.max(1,Math.min(12,step*.62));
  const x=i=>L+(i+.5)*step,t=selectedCandleTrade();
  const averages=visible.flatMap(b=>[b.ma_fast,b.ma_slow,b.ma20]).filter(v=>v!=null);
  const lows=[...visible.map(b=>b.low),...averages],highs=[...visible.map(b=>b.high),...averages];
  let low=Math.min(...lows),high=Math.max(...highs);
  const stop=t?t.buy_price*(1-data.stop_loss/100):null;
  const selectedVisible=t&&t.buy_date<=visible.at(-1).date&&t.sell_date>=visible[0].date;
  if(selectedVisible){low=Math.min(low,stop,t.buy_price,t.sell_price);high=Math.max(high,t.buy_price,t.sell_price);}
  const pad=Math.max((high-low)*.14,.05);low-=pad;high+=pad;
  const y=p=>T+(high-p)/(high-low)*(P-T);
  const maxVol=Math.max(1,...visible.map(b=>b.volume)),vy=v=>VB-v/maxVol*(VB-VT);
  let out='';
  for(let i=0;i<5;i++){
    const v=low+(high-low)*i/4,py=y(v);
    out+=`<line x1="${L}" y1="${py}" x2="${W-R}" y2="${py}" stroke="#edf1f2"/><text x="${L-10}" y="${py+4}" text-anchor="end">${fmt(v)}</text>`;
  }
  out+=`<line x1="${L}" y1="${VB}" x2="${W-R}" y2="${VB}" stroke="#e5ece9"/><text x="${L}" y="${VT-12}">成交量（手）</text><text x="${L-10}" y="${VT+5}" text-anchor="end">${fmt(maxVol/10000,1)}万</text>`;
  if(selectedVisible){
    const start=Math.max(0,visible.findIndex(b=>b.date>=t.buy_date));
    let end=visible.findIndex(b=>b.date>=t.sell_date);if(end<0)end=visible.length-1;
    const sx=x(start)-step/2,ex=x(end)+step/2;
    out+=`<rect class="holding-band" x="${sx}" y="${T}" width="${Math.max(step,ex-sx)}" height="${P-T}" fill="#168c7210"/><line class="stop-line" x1="${sx}" y1="${y(stop)}" x2="${ex}" y2="${y(stop)}" stroke="#c28c6d" stroke-width="1.2" stroke-dasharray="5 4"/><text x="${Math.min(W-140,ex+5)}" y="${y(stop)-5}" class="stop-label">止损 ${fmt(stop)}</text>`;
  }
  visible.forEach((b,i)=>{
    const cx=x(i),top=y(Math.max(b.open,b.close)),height=Math.max(1,Math.abs(y(b.open)-y(b.close))),c=b.close>=b.open?'#dc635d':'#269c7c';
    out+=`<g class="daily-candle" data-date="${b.date}"><line x1="${cx}" y1="${y(b.high)}" x2="${cx}" y2="${y(b.low)}" stroke="${c}" stroke-width="1"/><rect x="${cx-bw/2}" y="${top}" width="${bw}" height="${height}" fill="${c}"/><rect class="volume-bar" x="${cx-bw/2}" y="${vy(b.volume)}" width="${bw}" height="${VB-vy(b.volume)}" fill="${c}" opacity=".5"/></g>`;
  });
  for(const [key,c] of [['ma_fast','#ce9933'],['ma_slow','#5e8ec5'],['ma20','#a284bd']]){
    let path='',started=false;visible.forEach((b,i)=>{if(b[key]==null){started=false;return;}path+=`${started?'L':'M'}${x(i).toFixed(2)} ${y(b[key]).toFixed(2)} `;started=true;});
    if(path)out+=`<path class="moving-average ${key}" d="${path}" stroke="${c}" stroke-width="1.4" fill="none"/>`;
  }
  for(const trade of data.trades){
    for(const [kind,day,price,c] of [['buy',trade.buy_date,trade.buy_price,'#c9514e'],['sell',trade.sell_date,trade.sell_price,'#1d8a71']]){
      const i=visible.findIndex(b=>b.date===day);if(i<0)continue;
      const cx=x(i),py=y(price),active=trade.index===candleState.index;
      const labelY=kind==='buy'?Math.min(P-7,py+26):Math.max(T+10,py-24);
      out+=`<g class="trade-marker ${kind}-marker" data-date="${day}" opacity="${active?1:.42}"><line x1="${cx}" y1="${py}" x2="${cx}" y2="${labelY}" stroke="${c}" stroke-width="1.3"/><circle cx="${cx}" cy="${py}" r="${active?4:2.5}" fill="white" stroke="${c}" stroke-width="2"/><rect x="${cx-9}" y="${labelY-9}" width="18" height="18" rx="3" fill="${c}"/><text x="${cx}" y="${labelY+4}" text-anchor="middle" class="marker-label">${kind==='buy'?'B':'S'}</text><title>${kind==='buy'?'买入':'卖出'} ${day} · ${fmt(price)}</title></g>`;
    }
  }
  for(const i of [...new Set(Array.from({length:6},(_,j)=>Math.round((visible.length-1)*j/5)))]){
    out+=`<text x="${x(i)}" y="${H-9}" text-anchor="${i===0?'start':i===visible.length-1?'end':'middle'}">${visible[i].date}</text>`;
  }
  out+=`<line id="candle-crosshair" x1="0" y1="${T}" x2="0" y2="${VB}" stroke="#88a89f" stroke-dasharray="3 3" visibility="hidden"/>`;
  svg.innerHTML=out;
  $('#candle-range-label').textContent=`${visible[0].date} — ${visible.at(-1).date} · ${visible.length} 日`;
  $('#candle-pan').max=Math.max(0,total-candleState.count);$('#candle-pan').value=candleState.start;$('#candle-pan').disabled=candleState.count>=total;
  $('#candle-prev').disabled=candleState.index==null||candleState.index<=0;
  $('#candle-next').disabled=candleState.index==null||candleState.index>=candleState.allTrades.length-1;
  if(t)$('#candle-trade-summary').innerHTML=`买入 <b>${fmt(t.buy_price)}</b> → 卖出 <b>${fmt(t.sell_price)}</b><span class="${color(t.pnl)}">净盈亏 ${sign(t.pnl)}${fmt(t.pnl)} 元（${sign(t.return_pct)}${fmt(t.return_pct)}%）</span><span>${escapeHTML(t.reason)} · 持有 ${t.holding_days} 日</span>`;
  const entry=t?.entry_signal;
  $('#candle-entry-note').classList.toggle('hidden',!entry);
  $('#candle-entry-note').textContent=entry?`买入信号（${entry.date} ${entry.selection_time}）${t.buy_window_start?`；同日买入时段 ${t.buy_window_start.slice(11,16)}–${t.buy_time.slice(11,16)}`:''}：价格 ${fmt(entry.price)}，入场 MA ${fmt(entry.entry_ma,3)}；${entry.current_volume!=null?`当日累计 ${fmt(entry.current_volume,0)} 手 / 前期均量 ${fmt(entry.base_volume,0)} 手 = ${fmt(entry.volume_ratio,3)} 倍；`:''}14:30 涨幅 ${fmt(entry.gain_1430,3)}%；上影占比 ${fmt(entry.upper_shadow_pct,1)}%。此处日 K 为完整日线，可能与买入时的部分日线不同。`:'';
  candleQuote(visible.at(-1));
  svg.onpointermove=e=>{
    const rect=svg.getBoundingClientRect(),px=(e.clientX-rect.left)/rect.width*W;
    const i=Math.max(0,Math.min(visible.length-1,Math.floor((px-L)/step)));
    const line=$('#candle-crosshair');line.setAttribute('x1',x(i));line.setAttribute('x2',x(i));line.setAttribute('visibility','visible');candleQuote(visible[i]);
  };
  svg.onpointerleave=()=>{$('#candle-crosshair')?.setAttribute('visibility','hidden');};
}
function zoomCandles(factor,pivot=.5){
  if(!candleState.data)return;
  const total=candleState.data.bars.length,old=candleState.count,newCount=Math.max(Math.min(15,total),Math.min(total,Math.round(old*factor)));
  candleState.start+=Math.round((old-newCount)*pivot);candleState.count=newCount;drawCandles();
}
$('#candle-close').addEventListener('click',()=>$('#candle-dialog').close());
$('#candle-dialog').addEventListener('close',()=>candleState.request?.abort());
$('#candle-dialog').addEventListener('click',e=>{if(e.target===$('#candle-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
$('#candle-trades').addEventListener('change',e=>{candleState.index=Number(e.target.value);focusCandleTrade();});
$('#candle-focus').addEventListener('click',focusCandleTrade);
$('#candle-prev').addEventListener('click',()=>{const index=candleState.index-1,t=candleState.allTrades[index];if(t)openCandle(candleState.runId,t.symbol,index);});
$('#candle-next').addEventListener('click',()=>{const index=candleState.index+1,t=candleState.allTrades[index];if(t)openCandle(candleState.runId,t.symbol,index);});
$('#candle-zoom-in').addEventListener('click',()=>zoomCandles(.7));
$('#candle-zoom-out').addEventListener('click',()=>zoomCandles(1.4));
document.querySelectorAll('[data-candle-range]').forEach(b=>b.addEventListener('click',()=>{if(!candleState.data)return;candleState.count=b.dataset.candleRange==='all'?candleState.data.bars.length:Number(b.dataset.candleRange);drawCandles();}));
$('#candle-pan').addEventListener('input',e=>{candleState.start=Number(e.target.value);drawCandles();});
$('#candle-chart').addEventListener('wheel',e=>{if(!candleState.data)return;e.preventDefault();const r=e.currentTarget.getBoundingClientRect();zoomCandles(e.deltaY>0?1.15:.85,Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)));},{passive:false});
window.addEventListener('resize',()=>{if($('#candle-dialog').open)drawCandles();});
