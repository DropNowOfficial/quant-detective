// Actual shipped page: row cells must select the stock and retain the list DOM/focus.
const fs=require('node:fs');
const assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_MODULE||'jsdom');
const errors=[];const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(process.argv[2],'utf8'),{runScripts:'dangerously',virtualConsole:vc});
const d=dom.window.document;
d.querySelector('[data-tab="NOT_MATCHED"]').click();
let count=0;
const symbols=[...d.querySelectorAll('#rows [data-symbol]')].map(x=>x.dataset.symbol);
for(const symbol of symbols){
 for(let cell=0;cell<6;cell++){
  const other=symbols.find(s=>s!==symbol);
  d.querySelector(`[data-symbol="${other}"]`).click();
  const button=d.querySelector(`[data-symbol="${symbol}"]`);const row=button.closest('tr');
  row.children[cell].click();
  assert.equal(d.querySelector('#detail h3')?.textContent,symbol,`${symbol} cell ${cell} must select`);
  assert.equal(row.classList.contains('selected'),true,`${symbol} selected row retained`);
  assert.equal(button.isConnected,true,'selection must not replace the focused list');
  count++;
 }
}
const button=d.querySelector(`[data-symbol="${symbols[1]}"]`);
button.focus();button.click();
assert.equal(d.activeElement,button,'mouse selection keeps keyboard focus');
button.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true,cancelable:true}));
assert.equal(d.querySelector('#detail h3').textContent,symbols[2]);
assert.equal(d.activeElement.dataset.symbol,symbols[2]);
assert.equal(dom.window.csvCell(-0.02),'"-0.02"','negative numeric indicators must remain numeric CSV values');
assert.equal(dom.window.csvCell('=SUM(1,2)'), '"\'=SUM(1,2)"','untrusted text still needs spreadsheet formula protection');
assert.deepEqual(errors,[]);
console.log(JSON.stringify({row_cell_selections:count,focus_and_keyboard:'PASS',errors}));
dom.window.close();
