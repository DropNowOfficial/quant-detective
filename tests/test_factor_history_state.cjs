// Executes the actual page script in a DOM/fetch fixture, not browser acceptance.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const script=fs.readFileSync(require('node:path').join(__dirname,'../market_data/factors.html'),'utf8').split('<script>')[1].split('</script>')[0];
const deferred=()=>{let resolve;const promise=new Promise(yes=>resolve=yes);return {promise,resolve};};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function fixture(){
 const elements=new Map(),requests=[];let delayed=null;
 class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.value='';this.dataset={};this.listeners={};this.files=[];}
  append(...children){this.children.push(...children);}replaceChildren(...children){this.children=[...children];}
  addEventListener(event,callback){this.listeners[event]=callback;}querySelectorAll(){return [];}
  set innerHTML(value){throw new Error('HTML insertion is forbidden');}
 }
 const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const record=id=>({definition:{ref:{factor_id:id,version:'1'},name:id,input_fields:[]},binding_status:'EXTERNAL_VALUE',lifecycle_state:'candidate'});
 const fetch=async url=>{
  requests.push(url);const id=String(url).includes('external.other')?'external.other':'external.history';
  let body;
  if(url==='/api/factors')body={ok:true,factors:[record('external.history'),record('external.other')],hypotheses:[]};
  else if(url==='/api/factors/session')return {ok:false,json:async()=>({ok:false})};
  else if(url.startsWith('/api/factors/observations'))body={ok:true,cards:[],message:'No observations'};
  else if(url.startsWith('/api/factors/lifecycle'))body={ok:true,lifecycle_state:'retired',history_initialized:true,
   events:[{event_id:id,from_state:'candidate',to_state:'retired',actor:'local reviewer',reason:'<img onerror="unsafe">',evidence_ids:[]}],checks:[]};
  else if(url.startsWith('/api/factors/trials')){
   if(delayed){const pending=delayed;delayed=null;body=await pending.promise;}
   else body={ok:true,history_initialized:true,trials:[{record:{run_id:id,report_kind:'factor',status:'FAILED',error_reason:'<script>unsafe</script>',split:{},artifact_refs:[]},evidence:{promotable:false,limitations:['EVIDENCE_VALIDATOR_NOT_CONFIGURED']}}]};
  }else throw new Error('Unexpected read '+url);
  return {ok:true,json:async()=>body};
 };
 get('factor-select').value='external.history@1';
 vm.runInNewContext(script,{document:{getElementById:get,createElement:tag=>new Element(tag)},fetch,Date,URLSearchParams,setTimeout,clearTimeout,crypto:{randomUUID:()=> 'unused'}});
 await flush();await flush();
 const text=element=>element.textContent+' '+element.children.map(text).join(' ');
 return {get,requests,text,delay:pending=>delayed=pending,change:async id=>{get('factor-select').value=id+'@1';await get('factor-select').listeners.change();await flush();}};
}
(async()=>{
 const f=await fixture();
 assert.match(f.text(f.get('lifecycle-list')),/candidate.*retired/,'Actual local transitions are shown');
 assert.match(f.text(f.get('lifecycle-list')),/<img onerror="unsafe">/,'Reasons remain literal text');
 assert.match(f.text(f.get('trial-list')),/factor.*FAILED/,'Trial kind and failed status remain distinct');
 assert.match(f.text(f.get('trial-list')),/EVIDENCE_VALIDATOR_NOT_CONFIGURED/);
 assert.match(f.text(f.get('trial-list')),/<script>unsafe<\/script>/,'Trial errors remain literal text');
 assert.equal(f.requests.some(url=>url.includes('preview')||url.includes('commit')),false,'History cannot start work');
 const pending=deferred();f.delay(pending);const old=f.change('external.history');await flush();
 await f.change('external.other');
 pending.resolve({ok:true,trials:[{record:{run_id:'OLD-STALE',report_kind:'strategy',status:'SUCCEEDED'},evidence:{limitations:[]}}]});await old;await flush();
 assert.doesNotMatch(f.text(f.get('trial-list')),/OLD-STALE/,'Late history cannot cross selected definitions');
 assert.match(f.text(f.get('trial-list')),/external.other/);
 console.log('PASS: actual page history unit tests: lifecycle/trial kinds, failed status, safe literal text, no writes, stale selection isolation');
})().catch(error=>{console.error(error);process.exitCode=1;});
