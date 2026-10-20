const assert=require('node:assert/strict');
let playwright;try{playwright=require('playwright');}catch{playwright=require('/tmp/futures-ui-check/node_modules/playwright');}
const base=process.env.WEIPAN_URL||'http://127.0.0.1:8766';
const entryOnly=process.argv.includes('--entry-only');
(async()=>{
  const browser=await playwright.chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
  const page=await browser.newPage({viewport:{width:1600,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);
  await page.waitForFunction(()=>state.defaults&&state.result&&!state.busy);
  const history=await (await page.request.get(base+'/api/runs')).json();
  const run=history.find(r=>r.kind==='backtest'&&r.config.source==='akshare'&&r.metrics.trade_count>0&&r.config.symbols.split(',').length>=300);
  assert.ok(run,'A completed expanded real backtest is available for review');
  if(entryOnly){
    assert.equal(await page.evaluate(()=>state.result.kind),'backtest','Home restores a curve even when the latest run is a screen');
    const fileURL=require('node:url').pathToFileURL(require('node:path').resolve(__dirname,'../static/index.html'));
    fileURL.search=`?run=${run.id}&tab=trades`;
    await page.goto(fileURL.href);
    await page.waitForURL(`${base}/?run=${run.id}&tab=trades`);
    await page.waitForFunction(id=>state.result?.id===id&&state.tab==='trades',run.id);
    assert.equal(await page.locator('#chart-empty').isVisible(),false);
    assert.ok(await page.locator('#equity-chart path').count()>=3);
    assert.equal(await page.locator('#error-panel').isVisible(),false);
    assert.equal(await page.locator('.sidebar').evaluate(el=>getComputedStyle(el).position),'fixed');
    await page.locator('#overview-section').screenshot({path:'/tmp/weipan-entry-equity-fixed.png'});
  }
  await page.evaluate(async id=>{const r=await api('/api/runs/'+id);fillConfig(r.config);renderResult(r);setTab('trades');},run.id);
  const count=await page.locator('[data-candle]').count();
  assert.equal(count,run.metrics.trade_count);
  await page.locator('[data-candle]').first().click();
  await page.waitForFunction(()=>candleState.data&&document.querySelector('#candle-content').classList.contains('hidden')===false);
  assert.ok(await page.locator('.daily-candle').count()>=15);
  assert.ok(await page.locator('.buy-marker').count()>=1);
  assert.ok(await page.locator('.sell-marker').count()>=1);
  assert.equal(await page.locator('.moving-average').count(),3);
  const initial=await page.evaluate(()=>({count:candleState.count,index:candleState.index,trade:selectedCandleTrade(),data:candleState.data}));
  assert.equal(initial.trade.index,run.metrics.trade_count-1);
  const buyIndex=initial.data.bars.findIndex(b=>b.date===initial.trade.buy_date);
  assert.ok(buyIndex>=0);
  await page.screenshot({path:'/tmp/weipan-trade-daily.png'});
  await page.locator('#candle-zoom-in').click();
  assert.ok(await page.locator('.daily-candle').count()<initial.count);
  await page.locator('[data-candle-range=all]').click();
  assert.equal(await page.locator('.daily-candle').count(),initial.data.bars.length);
  await page.locator('[data-candle-range="60"]').click();
  await page.locator('#candle-pan').evaluate(el=>{el.value='0';el.dispatchEvent(new Event('input'));});
  assert.equal(await page.evaluate(()=>candleState.start),0);
  await page.locator('#candle-focus').click();
  assert.ok(await page.locator('.buy-marker').count()>=1);
  if(initial.index>0){
    await page.locator('#candle-prev').click();
    await page.waitForFunction(i=>candleState.data&&candleState.index===i,initial.index-1);
    assert.equal(await page.locator('#candle-error').isVisible(),false);
    await page.locator('#candle-next').click();
    await page.waitForFunction(i=>candleState.data&&candleState.index===i,initial.index);
  }
  const chart=await page.locator('#candle-chart').boundingBox();
  await page.mouse.move(chart.x+chart.width/2,chart.y+chart.height/2);
  assert.equal(await page.locator('#candle-crosshair').getAttribute('visibility'),'visible');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#candle-dialog').isVisible(),false);
  if(entryOnly){
    const screen=history.find(r=>r.kind==='screen');
    if(screen){
      await page.goto(`${base}/?run=${screen.id}`);
      await page.waitForFunction(id=>state.result?.id===id,screen.id);
      assert.equal(await page.evaluate(()=>state.result.kind),'screen','Explicit screen links keep their original meaning');
    }
    await page.goto(base);
    await page.waitForFunction(()=>state.result?.kind==='backtest'&&!state.busy);
    assert.equal(await page.locator('#chart-empty').isVisible(),false);
    await page.setViewportSize({width:1440,height:1050});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'passed',runId:run.id,trades:count,checks:['file entry redirects with run/tab preserved','home restores backtest curve','scripts and styles loaded','candles, volume and MA','zoom and trade switching','explicit screen links','desktop layout'],pageErrors:errors}));
    await browser.close();
    return;
  }
  // Real catalog is cached: verify both UI and server return every selected security.
  for(const size of ['500','0','300']){
    await page.locator('[name=universe_limit]').selectOption(size);
    await page.locator('#universe-btn').click();
    await page.waitForFunction(()=>!state.busy,{timeout:300000});
    assert.equal(await page.locator('#error-panel').isVisible(),false);
    const imported=await page.evaluate(()=>form.elements.symbols.value.split(',').length);
    if(size==='0')assert.ok(imported>1000);else assert.equal(imported,Number(size));
    assert.match(await page.locator('#pool-count').textContent(),/只/);
  }
  await page.evaluate(async id=>{const r=await api('/api/runs/'+id);fillConfig(r.config);renderResult(r);setTab('trades');},run.id);
  await page.setViewportSize({width:1440,height:1050});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.locator('[data-candle]').first().click();
  await page.waitForFunction(()=>candleState.data&&!document.querySelector('#candle-content').classList.contains('hidden'));
  assert.equal(await page.evaluate(()=>document.querySelector('#candle-dialog').scrollWidth>document.querySelector('#candle-dialog').clientWidth),false);
  await page.screenshot({path:'/tmp/weipan-trade-daily-1440.png'});
  await page.goto(`${base}/?run=${run.id}&tab=trades`);
  await page.waitForFunction(id=>state.result?.id===id&&state.tab==='trades',run.id);
  assert.equal(await page.locator('[data-candle]').count(),run.metrics.trade_count);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:'passed',runId:run.id,trades:count,checks:['300-stock real result','candles, volume and 3 moving averages','actual buy/sell markers','zoom and pan','previous and next trade','500/all/300 imports','desktop modal layout'],pageErrors:errors}));
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
