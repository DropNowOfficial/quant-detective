// Optional UI behavior check: JSDOM_MODULE=/path/to/jsdom node this-file.cjs result.html [local-url]
// Tests the actual generated page; does not claim layout or browser rendering coverage.
const fs=require('node:fs');
const assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_MODULE||'jsdom');
const errors=[];
function makeDom(html,url){
 const console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e.message));
 return new JSDOM(html,{url:url||'file:///screen.html',runScripts:'dangerously',virtualConsole:console,
  beforeParse(w){w.fetch=(p,o)=>fetch(new URL(p,url),o);w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};w.HTMLAnchorElement.prototype.click=function(){};}});
}
function clickTab(doc,state){doc.querySelector(`[data-tab="${state}"]`).click();}
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function idle(doc){for(let i=0;i<400;i++){if(!doc.getElementById('calculate').disabled)return;await pause(20);}throw Error('UI did not finish recalculating');}
(async()=>{
 const dom=makeDom(fs.readFileSync(process.argv[2],'utf8'));
 const d=dom.window.document;
 assert.equal(d.querySelectorAll('#rows tr').length,2);
 assert.match(d.getElementById('rows').textContent,/MU/);
 const search=d.getElementById('search');search.value='VST';search.dispatchEvent(new dom.window.Event('input'));
 assert.equal(d.querySelectorAll('#rows tr').length,1);
 assert.match(d.getElementById('detail').textContent,/VST/);
 search.value='';search.dispatchEvent(new dom.window.Event('input'));
 clickTab(d,'DATA_BLOCKED');assert.equal(d.querySelectorAll('#rows tr').length,1);
 assert.match(d.getElementById('detail').textContent,/DELL/);
 assert.match(d.getElementById('detail').textContent,/2026-09-18/);
 clickTab(d,'WATCH');assert.equal(d.getElementById('empty').classList.contains('hidden'),false);
 clickTab(d,'ALL');assert.equal(d.querySelectorAll('#rows tr').length,43);
 assert.equal(d.getElementById('calculate').disabled,true);
 d.getElementById('export').click();
 dom.window.close();
 if(process.argv[3]){
  const base=process.argv[3],response=await fetch(base), page=await response.text();
  const live=makeDom(page,base),doc=live.window.document;
  for(const id of ['minPrice','minAdv','maxAtr']) assert.equal(doc.getElementById(id).checkValidity(),true,`default ${id} must be valid`);
  doc.getElementById('minPrice').value='1000000';doc.getElementById('calculate').click();await idle(doc);
  assert.equal(doc.querySelectorAll('#rows tr').length,0,doc.getElementById('message').textContent);
  assert.match(doc.getElementById('message').textContent,/已保存本次结果/);
  doc.getElementById('reset').click();doc.getElementById('calculate').click();await idle(doc);
  assert.equal(doc.querySelectorAll('#rows tr').length,2);
  doc.getElementById('latest').checked=false;doc.getElementById('latest').dispatchEvent(new live.window.Event('change'));
  doc.getElementById('date').value='2026-10-03';doc.getElementById('calculate').click();await idle(doc);
  assert.match(doc.getElementById('message').textContent,/不是美股交易日/);
  assert.equal(doc.querySelectorAll('#rows tr').length,2); // A failed recalculation preserves prior results.
  live.window.close();
 }
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({static:'PASS',live:process.argv[3]?'PASS':'NOT_RUN',js_errors:errors,visual_render:'NOT_TESTED'}));
})().catch(e=>{console.error(e);process.exitCode=1;});
