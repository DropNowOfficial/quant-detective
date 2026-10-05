// Real Chromium hit testing, scrolling, keyboard, responsive layout, download and async races.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const output=process.env.QA_OUTPUT||'/tmp/quant-browser-results';fs.mkdirSync(output,{recursive:true});
const errors=[];let clicks=0;const checks=[];
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const config=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH,headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-software-rasterizer']});
 try{
  for(const width of config.skipViewports?[]:[1440,768,390,320]){
   const context=await browser.newContext({viewport:{width,height:900},hasTouch:width<500});
   const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
   await page.goto(pathToFileURL(config.html).href);
   if(process.env.QA_FONT){await page.addStyleTag({content:`@font-face{font-family:QAChinese;src:url(data:font/ttf;base64,${fs.readFileSync(process.env.QA_FONT).toString('base64')})}body{font-family:QAChinese,sans-serif}`});await page.evaluate(()=>document.fonts.ready);}
   await page.locator('[data-tab="NOT_MATCHED"]').click();
   const symbols=await page.locator('#rows [data-symbol]').evaluateAll(a=>a.map(b=>b.dataset.symbol));
   for(const symbol of symbols){
    const row=page.locator(`[data-row-symbol="${symbol}"]`);
    const cell=row.locator('td').nth(width===1440?2:1);
    if(width<500)await cell.tap();else await cell.click();clicks++;
    assert.equal(await page.locator('#detail h3').textContent(),symbol);
    assert.equal(await row.getAttribute('class'),'selected');
   }
   await page.locator('[data-tab="ALL"]').click();
   const all=await page.locator('#rows [data-symbol]').evaluateAll(a=>a.map(b=>b.dataset.symbol));
   // First/middle/last: all cells, forcing another selection before each attempt.
   for(const symbol of [all[0],all[21],all[42]])for(let cell=0;cell<6;cell++){
    await page.locator(`[data-symbol="${all.find(s=>s!==symbol)}"]`).click();clicks++;
    await page.locator(`[data-row-symbol="${symbol}"] td`).nth(cell).click();clicks++;
    assert.equal(await page.locator('#detail h3').textContent(),symbol);
   }
   const container=page.locator('.table-wrap');
   await container.evaluate(e=>{e.scrollTop=700;e.scrollLeft=0;});
   const target=page.locator('#rows tr').nth(12).locator('td').nth(1);
   await target.scrollIntoViewIfNeeded();
   const before=await container.evaluate(e=>[e.scrollTop,e.scrollLeft]);await target.click();clicks++;
   const after=await container.evaluate(e=>[e.scrollTop,e.scrollLeft]);assert.deepEqual(after,before,'selection must retain table scroll');
   await page.locator('#rows tr.selected button').press('End');
   assert.equal(await page.locator('#detail h3').textContent(),all.at(-1));
   await page.locator('#rows tr.selected button').press('Home');assert.equal(await page.locator('#detail h3').textContent(),all[0]);
   await page.locator('[data-tab="WATCH"]').focus();await page.keyboard.press('Enter');
   assert.equal(await page.locator('[data-tab="WATCH"]').evaluate(e=>e===document.activeElement),true);
   await page.locator('[data-tab="ALL"]').click();await page.locator('#search').fill('NVDA');
   assert.equal(await page.locator('#rows tr').count(),1);assert.equal(await page.locator('#detail h3').textContent(),'NVDA');
   const downloadPromise=page.waitForEvent('download');await page.locator('#export').click();
   const download=await downloadPromise;const filename=path.join(output,`export-${width}.csv`);await download.saveAs(filename);
   const csv=fs.readFileSync(filename,'utf8');assert.match(csv,/NVDA/);assert.equal(csv.trim().split(/\r?\n/).length,2);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`viewport ${width} overflow`);
   await page.locator('#search').fill('');await page.locator('[data-tab="NOT_MATCHED"]').click();
   await page.screenshot({path:path.join(output,`workbench-${width}.png`),fullPage:true});
   checks.push({width,unmatched_selections:40,representative_cell_checks:18,scroll:'PASS',keyboard:'PASS',download:'PASS',overflow:false});
   console.log(JSON.stringify({viewport:width,status:'PASS',clicks}));await context.close();
  }
  if(config.base){
   const context=await browser.newContext(),page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
   await page.goto(config.base);await page.locator('#historyBox summary').click();
   await page.waitForFunction(()=>document.querySelectorAll('#history option').length>=3);
   const [a,b]=config.records;
   let started;
   const startedPromise=new Promise(r=>started=r);
   await page.route(`**/api/runs/${a.run_id}`,async route=>{started();await delay(350);await route.fulfill({json:a});});
   await page.route(`**/api/runs/${b.run_id}`,route=>route.fulfill({json:b}));
   await page.selectOption('#history',a.run_id);await startedPromise;await page.selectOption('#history',b.run_id);
   await delay(650);
   assert.match(await page.locator('#runInfo').textContent(),new RegExp(b.run_id),'last selected history must win when responses arrive out of order');
   assert.equal(await page.locator('#history').inputValue(),b.run_id);
   await page.unrouteAll({behavior:'wait'});
   checks.push({history_race:'PASS'});
   // Failure keeps the displayed evidence and restores its selector.
   await page.route(`**/api/runs/${a.run_id}`,route=>route.fulfill({status:500,json:{error:'controlled failure'}}));
   await page.selectOption('#history',a.run_id);await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('无法读取'));
   assert.match(await page.locator('#runInfo').textContent(),new RegExp(b.run_id));assert.equal(await page.locator('#history').inputValue(),b.run_id);
   checks.push({history_failure:'PASS'});await page.unrouteAll({behavior:'wait'});
   // A syntactically valid but incomplete record must not corrupt current UI state.
   const invalidRecords=[{run_id:a.run_id},{...a,rows:null},{...a,policy:null},{...a,counts:{}},{...a,rows:a.rows.map((r,i)=>i? r:{...r,source:null})}];
   for(const invalid of invalidRecords){
    await page.route(`**/api/runs/${a.run_id}`,route=>route.fulfill({json:invalid}));
    await page.selectOption('#history',a.run_id);await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('已保留'));
    assert.match(await page.locator('#message').textContent(),/格式/);
    assert.match(await page.locator('#runInfo').textContent(),new RegExp(b.run_id));
    await page.locator('[data-tab="ALL"]').click();assert.equal(await page.locator('#rows tr').count(),43);
    await page.unrouteAll({behavior:'wait'});
   }
   checks.push({malformed_history_cases:invalidRecords.length,status:'PASS'});
   await page.selectOption('#history',a.run_id);await page.waitForFunction(id=>document.querySelector('#runInfo').textContent.includes(id),a.run_id);
   let calculating;const calculatingPromise=new Promise(r=>calculating=r);
   await page.route('**/api/screen',async route=>{calculating();await delay(350);await route.fulfill({json:a});});
   await page.locator('#calculate').click();await calculatingPromise;await page.selectOption('#history',b.run_id);await delay(650);
   assert.match(await page.locator('#runInfo').textContent(),new RegExp(b.run_id),'new history selection supersedes an older calculation');
   assert.equal(await page.locator('#minPrice').inputValue(),String(b.policy.min_price));
   checks.push({calculation_history_race:'PASS'});await context.close();
  }
  assert.deepEqual(errors,[]);
  const report={browser:await browser.version(),clicks,checks,errors};fs.writeFileSync(path.join(output,'browser-stress.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
