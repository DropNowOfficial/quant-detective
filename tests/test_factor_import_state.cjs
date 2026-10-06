// Unit-level asynchronous state tests execute the actual page script in a minimal
// DOM/fetch fixture. These are not browser, rendering or visual acceptance.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const script=fs.readFileSync(path.join(__dirname,'../market_data/factors.html'),'utf8').split('<script>')[1].split('</script>')[0];
const fields=['instrument_id','observed_at','source_published_at','provider_available_at','value','unit','factor_id','factor_version'];
const csv=label=>fields.join(',')+'\n'+label+',2026-10-06T12:00:00Z,2026-10-06T12:00:00Z,2026-10-06T12:00:00Z,10,ratio,external.state,1\n';
const definition=label=>JSON.stringify({definition:{name:label,ref:{factor_id:'external.'+label,version:'1'}}});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const flush=()=>new Promise(resolve=>setImmediate(resolve));

async function fixture(){
 const elements=new Map(),requests=[],timers=new Map();let timerId=0,previewReply=null;
 class Element{
  constructor(tag='div',id=''){this.tag=tag;this.id=id;this.children=[];this.value='';this.textContent='';this.dataset={};this.listeners={};this.disabled=false;this.files=[];}
  append(...children){for(const child of children){this.children.push(child);register(child);}}
  replaceChildren(...children){for(const child of this.children)unregister(child);this.children=[];this.append(...children);}
  get firstChild(){return this.children[0];}
  querySelectorAll(tag){return this.children.flatMap(child=>[...(child.tag===tag?[child]:[]),...child.querySelectorAll(tag)]);}
  addEventListener(event,callback){this.listeners[event]=callback;}
  dispatch(event){return this.listeners[event]?.();}
 }
 const register=element=>{if(element.id)elements.set(element.id,element);for(const child of element.children)register(child);};
 const unregister=element=>{if(element.id)elements.delete(element.id);for(const child of element.children)unregister(child);};
 const get=id=>{if(!elements.has(id))elements.set(id,new Element('div',id));return elements.get(id);};
 const preview=()=>({ok:true,preview_id:'synthetic-preview',expires_at:'2099-01-01T00:00:00Z',errors:[],warnings:[],sample_rows:[],proposed_manifest:{},content_hash:'synthetic',expected_revision:0});
 const fetch=async(url,options)=>{
  if(url==='/api/factors/session')return {ok:true,json:async()=>({ok:true,csrf_token:'synthetic-memory-token',limits:{request_bytes:5242880,definition_bytes:65536},universe:{version:'synthetic'}})};
  if(url==='/api/factors')return {ok:true,json:async()=>({ok:true,factors:[],hypotheses:[]})};
  requests.push({url,payload:JSON.parse(options.body)});
  if(url.endsWith('/preview')){const pending=previewReply;previewReply=null;const body=pending?await pending.promise:preview();return {ok:true,json:async()=>body};}
  if(url.endsWith('/commit'))return {ok:true,json:async()=>({ok:true,result:{replayed:false},manifest:{}})};
  throw new Error('Unexpected fixture request '+url);
 };
 vm.runInNewContext(script,{document:{getElementById:get,createElement:tag=>new Element(tag)},fetch,
  Date,URLSearchParams,crypto:{randomUUID:()=> 'synthetic-request'},
  setTimeout:callback=>{const id=++timerId;timers.set(id,callback);return id;},clearTimeout:id=>timers.delete(id)});
 await flush();
 const file=(name,text)=>({name,size:20,text:()=>typeof text==='string'?Promise.resolve(text):text.promise});
 const select=(kind,text)=>{const input=get(kind==='csv'?'csv-file':'definition-file');input.files=text===null?[]:[file(kind+'.file',text)];return input.dispatch('change');};
 const loaded=async()=>{await select('csv',csv('OLD'));await select('definition',definition('old'));};
 return {get,requests,select,loaded,preview,delayPreview:pending=>{previewReply=pending;},
         click:id=>get(id).dispatch('click')};
}

async function tests(){
 for(const kind of ['csv','definition']){
  const f=await fixture();await f.loaded();await f.click('preview-import');
  assert.equal(f.get('save-import').disabled,false,'Initial valid preview is explicitly saveable');
  const pending=deferred(),read=f.select(kind,pending);
  assert.equal(f.get('preview-import').disabled,true,kind+' replacement must disable preview immediately');
  assert.equal(f.get('save-import').disabled,true,kind+' replacement must invalidate old preview immediately');
  const before=f.requests.length;await f.click('preview-import');await f.click('save-import');
  assert.equal(f.requests.length,before,'Pending '+kind+' read must not submit previous parsed content');
  pending.resolve(kind==='csv'?csv('NEW'):definition('new'));await read;
  assert.equal(f.get('save-import').disabled,true,'Read completion never restores an old saveable preview');
  await f.click('preview-import');
  const latest=f.requests.at(-1).payload;
  assert.equal(kind==='csv'?latest.csv_text:latest.definition_json.definition.name,kind==='csv'?csv('NEW'):'new');
  await f.click('save-import');assert.equal(f.requests.at(-1).url,'/api/factors/import/commit');
 }
 for(const kind of ['csv','definition']){
  const f=await fixture();await f.loaded();
  const old=deferred(),latest=deferred(),oldRead=f.select(kind,old),newRead=f.select(kind,latest);
  latest.resolve(kind==='csv'?csv('LATEST'):definition('latest'));await newRead;
  old.reject(new Error('late superseded file error'));await oldRead;
  assert.equal(f.get('preview-import').disabled,false,'Superseded read error cannot clear latest '+kind+' state');
  await f.click('preview-import');const payload=f.requests.at(-1).payload;
  assert.equal(kind==='csv'?payload.csv_text:payload.definition_json.definition.name,kind==='csv'?csv('LATEST'):'latest');
  const clearing=deferred(),reading=f.select(kind,clearing);await f.select(kind,null);
  clearing.resolve(kind==='csv'?csv('CLEARED'):definition('cleared'));await reading;
  assert.equal(f.get('preview-import').disabled,true,'Cleared file selection cannot be resurrected by late read');
 }
 for(const kind of ['csv','definition']){
  const f=await fixture();await f.loaded();const pendingPreview=deferred();f.delayPreview(pendingPreview);
  const previewRequest=f.click('preview-import');await flush();
  const replacement=deferred(),read=f.select(kind,replacement);
  replacement.resolve(kind==='csv'?csv('REPLACEMENT'):definition('replacement'));await read;
  pendingPreview.resolve(f.preview());await previewRequest;
  assert.equal(f.get('save-import').disabled,true,'Late old-file preview cannot become saveable after '+kind+' replacement');
  assert.notEqual(f.get('preview-status').dataset.state,'ready');
  await f.click('save-import');assert.equal(f.requests.filter(item=>item.url.endsWith('/commit')).length,0);
 }
 for(const kind of ['csv','definition']){
  const f=await fixture();await f.loaded();
  const older=deferred(),newer=deferred(),oldRead=f.select(kind,older),newRead=f.select(kind,newer);
  newer.resolve(kind==='csv'?csv('NEWEST'):definition('newest'));await newRead;
  older.resolve(kind==='csv'?csv('OBSOLETE'):definition('obsolete'));await oldRead;
  await f.click('preview-import');const current=f.requests.at(-1).payload;
  assert.equal(kind==='csv'?current.csv_text:current.definition_json.definition.name,kind==='csv'?csv('NEWEST'):'newest');
  const failure=deferred();f.delayPreview(failure);const request=f.click('preview-import');await flush();
  await f.select(kind,kind==='csv'?csv('AFTER'):definition('after'));
  failure.reject(new Error('late old preview failure'));await request;
  assert.notEqual(f.get('preview-status').dataset.state,'error','Superseded preview failure must not overwrite latest file state');
  assert.equal(f.get('save-import').disabled,true);
 }
 const f=await fixture();await f.loaded();const c=deferred(),j=deferred(),cr=f.select('csv',c),jr=f.select('definition',j);
 c.resolve(csv('BOTH'));await cr;assert.equal(f.get('preview-import').disabled,true,'Both files must finish reading before preview');
 j.resolve(definition('both'));await jr;assert.equal(f.get('preview-import').disabled,false);
 console.log('PASS: actual page async-state unit tests: CSV/JSON replacements, pending-read guards, late read errors/clears, late previews, concurrent files, exact explicit save');
}
tests().catch(error=>{console.error(error);process.exitCode=1;});
