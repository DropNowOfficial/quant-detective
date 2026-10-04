// Deterministic randomized interaction testing on actual generated HTML.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_MODULE||'jsdom');
const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(process.argv[2],'utf8'),{runScripts:'dangerously',virtualConsole:vc});
const d=dom.window.document,data=JSON.parse(d.getElementById('screen-data').textContent).result;
let seed=20261004;const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/2**32;};
const choose=a=>a[Math.floor(random()*a.length)];
const states=['ALL','CANDIDATE','WATCH','NOT_MATCHED','DATA_BLOCKED'];
const queries=['','A','m','nvda','QQQ','不存在','</script>',' ',...data.rows.map(r=>r.symbol)];
const actions={tab:0,card:0,search:0,cell:0,keyboard:0};
for(let i=0;i<3000;i++){
 const op=Math.floor(random()*5),buttons=[...d.querySelectorAll('#rows [data-symbol]')];
 if(op===0){d.querySelector(`[data-tab="${choose(states)}"]`).click();actions.tab++;}
 else if(op===1){d.querySelector(`[data-card="${choose(states.slice(1))}"]`).click();actions.card++;}
 else if(op===2){const s=d.getElementById('search');s.value=choose(queries);s.dispatchEvent(new dom.window.Event('input'));actions.search++;}
 else if(buttons.length&&op===3){const button=choose(buttons);button.closest('tr').children[Math.floor(random()*6)].click();assert.equal(d.querySelector('#detail h3')?.textContent,button.dataset.symbol);actions.cell++;}
 else if(buttons.length){const b=choose(buttons);b.focus();b.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:choose(['ArrowUp','ArrowDown','Home','End']),bubbles:true,cancelable:true}));assert.equal(d.querySelector('#detail h3')?.textContent,d.activeElement.dataset.symbol);actions.keyboard++;}
 const visible=[...d.querySelectorAll('#rows tr')],selected=d.querySelectorAll('#rows tr.selected');
 assert.equal(selected.length,visible.length?1:0,`selection invariant at ${i}`);
 if(visible.length)assert.equal(selected[0].querySelector('[data-symbol]').dataset.symbol,d.querySelector('#detail h3').textContent);
 assert.equal(d.getElementById('empty').classList.contains('hidden'),visible.length>0);
 assert.equal(errors.length,0,`script error at ${i}`);
}
// Keyboard tab activation must leave a usable focus target.
const tab=d.querySelector('[data-tab="NOT_MATCHED"]');tab.focus();tab.click();
assert.equal(d.activeElement,tab,'category activation must preserve keyboard focus');
console.log(JSON.stringify({seed:20261004,iterations:3000,actions_executed:Object.values(actions).reduce((a,b)=>a+b,0),actions,errors}));dom.window.close();
