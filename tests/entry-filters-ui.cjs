const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const path=require('node:path');
let playwright;try{playwright=require('playwright');}catch{playwright=require('/tmp/futures-ui-check/node_modules/playwright');}
const base=process.env.WEIPAN_URL||'http://127.0.0.1:8766';
(async()=>{
  const browser=await playwright.chromium.launch({headless:true,args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:1600,height:1100}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    // Leave the user's real download running; isolate the form review from active-job polling.
    await page.route('**/api/active-job',route=>route.fulfill({json:null}));
    const oldId='1c8201cf410e';
    await page.goto(`${base}/?run=${oldId}&tab=trades`);
    await page.waitForFunction(id=>state.result?.id===id&&!state.busy,oldId);
    assert.equal(await page.locator('[name=mode]').inputValue(),'minute');
    assert.equal(await page.locator('[name=mode] option[value=daily]').count(),0);
    assert.equal(await page.locator('[name=volume_recent]').getAttribute('type'),'hidden');
    assert.equal(await page.locator('[name=volume_recent]').inputValue(),'1');
    assert.equal(await page.locator('[name=volume_ratio]').inputValue(),'1.5');
    assert.equal(await page.locator('[name=volume_base]').inputValue(),'5');
    assert.equal(await page.locator('[name=strategy_version]').inputValue(),'2');
    assert.match(await page.locator('#result-mode').textContent(),/旧版次日成交/);
    assert.match(await page.locator('#entry-result-note').textContent(),/不代表当日尾盘/);
    assert.equal(await page.locator('#dirty-note').isVisible(),true);
    assert.equal(await page.locator('[data-candle]').count(),28);
    assert.match(await page.locator('tbody').textContent(),/09:30/);
    await page.locator('[data-candle]').first().click();
    await page.waitForFunction(()=>candleState.data&&!document.querySelector('#candle-content').classList.contains('hidden'));
    assert.equal(await page.locator('.moving-average').count(),3);
    assert.match(await page.locator('#candle-note').textContent(),/旧版次日开盘/);
    await page.keyboard.press('Escape');
    await page.locator('[name=entry_filters]').selectOption('off');
    assert.equal(await page.locator('[name=mode]').inputValue(),'minute');
    await page.locator('#apply-entry-btn').click();
    assert.equal(await page.locator('[name=entry_filters]').inputValue(),'on');
    await page.locator('[name=volume_base]').fill('7');
    await page.locator('[name=volume_ratio]').fill('1.8');
    await page.locator('[name=entry_ma_days]').fill('6');
    await page.locator('[name=gain_1430_min]').fill('3.2');
    await page.locator('[name=upper_shadow_pct]').fill('45');
    const edited=await page.evaluate(()=>readConfig());
    assert.equal(edited.volume_base,'7');assert.equal(edited.volume_ratio,'1.8');
    assert.equal(edited.volume_recent,'1');assert.equal(edited.entry_ma_days,'6');
    assert.equal(edited.gain_1430_min,'3.2');assert.equal(edited.upper_shadow_pct,'45');
    assert.match(await page.locator('#rule-summary').textContent(),/当日累计量 \/ 前 7 日均量 > 1.8 倍/);
    await page.locator('#recent-range-btn').click();
    assert.equal(await page.locator('[name=start_date]').inputValue(),await page.evaluate(()=>state.defaults.start_date));
    for(const payload of [{mode:'daily',entry_filters:'off'},{volume_recent:2}]){
      const response=await page.request.post(base+'/api/backtest',{data:payload});
      assert.equal(response.status(),400);
    }
    // This fixture is the actual engine output for a controlled synthetic same-day entry.
    const generated=spawnSync('python3',[path.join(__dirname,'same_day_fixture.py')],{encoding:'utf8'});
    assert.equal(generated.status,0,generated.stderr);
    const fixture=JSON.parse(generated.stdout);
    await page.route(`**/api/runs/${fixture.result.id}/candles/600000`,route=>route.fulfill({json:fixture.chart}));
    await page.evaluate(result=>{fillConfig(result.config);renderResult(result);setTab('trades');},fixture.result);
    assert.equal(await page.locator('#entry-result-note').isVisible(),false);
    assert.match(await page.locator('#result-mode').textContent(),/当日尾盘/);
    assert.match(await page.locator('thead').textContent(),/信号时间.*买入时段.*卖出时段/);
    assert.match(await page.locator('tbody').textContent(),/14:50–14:55/);
    assert.equal(await page.locator('[data-candle]').count(),1);
    await page.locator('[data-candle]').click();
    await page.waitForFunction(()=>candleState.data&&!document.querySelector('#candle-content').classList.contains('hidden'));
    assert.match(await page.locator('#candle-entry-note').textContent(),/同日买入时段 14:50–14:55/);
    assert.match(await page.locator('#candle-entry-note').textContent(),/当日累计 184 手 \/ 前期均量 110 手/);
    assert.equal(await page.locator('.buy-marker').count(),1);
    assert.equal(await page.locator('.sell-marker').count(),1);
    await page.screenshot({path:'/tmp/weipan-same-day-candle.png'});
    await page.keyboard.press('Escape');
    await page.locator('#detail-section').screenshot({path:'/tmp/weipan-same-day-trades.png'});
    await page.locator('.volume-box').screenshot({path:'/tmp/weipan-current-volume.png'});
    await page.locator('[data-tab=signals]').click();
    assert.equal(await page.locator('.condition-dot').count(),8);
    for(const width of [1600,1440]){
      await page.setViewportSize({width,height:1100});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'passed',checks:['legacy warnings and migration','same-day only execution','current-day volume controls','recent date shortcut','API rejects daily and two-day modes','signal and fill timestamps','entry snapshot in daily K','desktop 1440/1600'],errors}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
