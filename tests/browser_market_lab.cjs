/* Optional private-snapshot UI verification; pass generated lab.html. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const runtime=process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES;
const {chromium}=require(runtime?path.join(runtime,'playwright'):'playwright');
const html=path.resolve(process.argv[2]);
const data=JSON.parse(fs.readFileSync(path.join(path.dirname(html),'result.json'),'utf8'));
const output=process.env.QA_OUTPUT||'/tmp/quant-lab-market-qa';fs.mkdirSync(output,{recursive:true});
let browser;
(async()=>{
  browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE||undefined,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
  const page=await browser.newPage({viewport:{width:1440,height:960},acceptDownloads:true});
  const errors=[],network=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(/^https?:/.test(r.url()))network.push(r.url());});
  await page.goto(pathToFileURL(html).href);
  await page.locator('#dataset-select').waitFor();
  const results=await page.evaluate(datasets=>{
    const change=(id,value)=>{const el=document.getElementById(id);el.value=String(value);el.dispatchEvent(new Event('change',{bubbles:true}));};
    const checks=[];
    for(const d of datasets){
      change('dataset-select',d.id);
      for(const s of d.scenarios){
        change('variant-select',s.variant);change('hold-select',s.hold);change('cost-select',s.cost_bps);
        const text=document.querySelector('[data-metric="cagr"]').textContent.trim();
        const shown=Number(text.replace(/[^0-9.+-]/g,''));
        if(s.metrics.cagr===null ? text!=='—' : !Number.isFinite(shown)||Math.abs(shown-s.metrics.cagr*100)>.051)throw Error(`Scenario mismatch ${d.id}/${s.id}: ${text}`);
        checks.push(`${d.id}/${s.id}`);
      }
    }
    return checks;
  },data.datasets);
  assert.equal(results.length,192);
  await page.locator('#dataset-select').selectOption('yahoo_5y');
  await page.locator('#variant-select').selectOption('primary');
  await page.locator('#hold-select').selectOption('3');
  await page.locator('#cost-select').selectOption('10');
  const downloadEvent=page.waitForEvent('download');await page.locator('#export-csv').click();
  const download=await downloadEvent;const csvPath=path.join(output,'selected-nav.csv');await download.saveAs(csvPath);
  const csv=fs.readFileSync(csvPath,'utf8').trim().split(/\r?\n/);
  assert.equal(csv.length,data.datasets.find(d=>d.id==='yahoo_5y').dates.length+1);
  const font=process.env.QA_FONT;
  if(font){await page.addStyleTag({content:`@font-face{font-family:QAChinese;src:url(data:font/ttf;base64,${fs.readFileSync(font).toString('base64')})}body{font-family:QAChinese,sans-serif}`});await page.evaluate(()=>document.fonts.ready);}
  const layouts=[];
  for(const width of [1440,768,390,320]){
    await page.setViewportSize({width,height:960});
    for(const tab of ['bench','review','principles','sources','gates']){
      await page.locator(`[data-tab="${tab}"]`).click();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,`Overflow: ${width}/${tab}`);
      layouts.push({width,tab,overflow:false});
      if((width===1440&&['bench','review','principles'].includes(tab))||(width===390&&tab==='bench'))await page.screenshot({path:path.join(output,`${tab}-${width}.png`),fullPage:true});
    }
  }
  assert.deepEqual(errors,[]);
  assert.deepEqual(network,[],'Offline lab must not fetch external resources.');
  const receipt={run_id:data.run_id,scenario_checks:results.length,export_rows:csv.length-1,layouts,browser:browser.version(),errors,external_requests:network};
  fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
  console.log(JSON.stringify(receipt));await browser.close();
})().catch(async e=>{console.error(e);if(browser)await browser.close();process.exitCode=1;});
