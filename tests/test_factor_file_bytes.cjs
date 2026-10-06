// Actual-page VM + native Node File bytes + real synthetic HTTP importer.
// No Chromium, rendered UI, native browser download or provider acceptance claim.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{createHash}=require('node:crypto'),{File}=require('node:buffer');
const {fixture}=require('./test_factor_import_state.cjs');
const root=path.resolve(__dirname,'..');
const fields=['instrument_id','observed_at','source_published_at','provider_available_at','value','unit','factor_id','factor_version','missing_reason'];
const csv=Buffer.from(fields.join(',')+'\r\nSYNTH,2026-10-06T12:00:00Z,2026-10-06T12:00:01Z,2026-10-06T12:00:02Z,,ratio,external.bytes,1,café 🧪\r\n');
const document={definition:{ref:{factor_id:'external.bytes',version:'1'},name:'Synthetic café 🧪',purpose:'Synthetic byte transport',market:'us_equity',frequency:'daily',unit:'ratio',formula_text:'External fixture',calculator_key:null,input_fields:['value'],min_history:{bars:1},missing_policy:'Preserve missing',window:{bars:1},lag:{bars:0},direction:'descriptive'},manifest:{dataset_id:'synthetic-bytes',version:1,source_ref:'synthetic',universe_version:'browser-synthetic-v1',adjustment:'unadjusted',provider:'synthetic',permission_basis:'Synthetic fixture',source_timezone:'UTC',corporate_action_basis:'Synthetic fixture',history_version:'1',coverage_gaps:[]}};
const json=Buffer.from(JSON.stringify(document));
const file=(kind,bytes)=>new File([bytes],kind==='csv'?'values.csv':'definition.json',{type:kind==='csv'?'text/csv':'application/json'});
async function tests(){
 const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'qd-factor-bytes-'));
 const child=spawn(process.env.QD_FACTOR_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python'),[path.join(__dirname,'factor_browser_server.py'),path.join(temporary,'evidence.sqlite')],{cwd:root,stdio:['ignore','pipe','pipe']});
 const stopped=new Promise(resolve=>child.once('close',resolve));
 let diagnostics='';child.stderr.on('data',chunk=>diagnostics+=chunk.toString());
 try{
  const port=await new Promise((resolve,reject)=>{let data='';const timeout=setTimeout(()=>reject(new Error('Synthetic server startup timed out')),15000);child.once('error',error=>{clearTimeout(timeout);reject(error);});child.once('exit',code=>{clearTimeout(timeout);reject(new Error('Synthetic server exited '+code+': '+diagnostics));});child.stdout.on('data',chunk=>{data+=chunk.toString();if(/^[0-9]+\n/.test(data)){clearTimeout(timeout);resolve(Number(data.trim()));}});});
  const origin='http://127.0.0.1:'+port,failures=[];
  const check=async(name,action)=>{try{await action();console.log('PASS: '+name);}catch(error){failures.push(name+': '+error.message);console.error('FAIL: '+name+': '+error.message);}};
  for(const kind of ['csv','definition'])for(const encoding of ['invalid','bom'])await check(kind+' '+encoding+' rejects before preview/save',async()=>{
   const f=await fixture({origin});
   await f.select('csv',file('csv',csv));await f.select('definition',file('definition',json));
   await f.click('preview-import');assert.equal(f.get('save-import').disabled,false);
   const original=kind==='csv'?csv:json;
   const bytes=encoding==='bom'?Buffer.concat([Buffer.from([0xef,0xbb,0xbf]),original]):Buffer.from(original);
   if(encoding==='invalid')bytes[bytes.indexOf(Buffer.from('café'))]=0xff;
   await f.select(kind,file(kind,bytes));
   assert.equal(f.get('preview-import').disabled,true);
   assert.equal(f.get('save-import').disabled,true);
   assert.equal(f.get('download-evidence').disabled,true);
   assert.equal(f.get('import-status').textContent,encoding==='bom'?'UTF8_BOM_NOT_ALLOWED':'INVALID_UTF8');
   const before=f.requests.length;await f.click('preview-import');await f.click('save-import');
   assert.equal(f.requests.length,before,'Rejected bytes cannot reach preview/cache/store');
  });
  await check('token-bearing metadata cannot become downloadable evidence',async()=>{
   const f=await fixture({origin}),session=await (await fetch(origin+'/api/factors/session')).json();
   await f.select('csv',file('csv',csv));await f.select('definition',file('definition',json));
   await f.click('preview-import');assert.equal(f.get('download-evidence').disabled,false);
   const poison={...document,manifest:{...document.manifest,source_ref:'prefix-'+session.csrf_token+'-suffix'}};
   await f.select('definition',file('definition',Buffer.from(JSON.stringify(poison))));
   await f.click('preview-import');
   assert.equal(f.get('save-import').disabled,true);
   assert.equal(f.get('download-evidence').disabled,true);
   assert.equal(f.get('preview-status').textContent,'SESSION_TOKEN_IN_BODY');
   const leaked=['preview-manifest','preview-samples','commit-result'].some(id=>f.get(id).textContent.includes(session.csrf_token));
   assert.equal(leaked,false);
  });
  await check('accepted native File source bytes equal manifest SHA-256',async()=>{
   const f=await fixture({origin});
   await f.select('csv',file('csv',csv));await f.select('definition',file('definition',json));
   await f.click('preview-import');assert.equal(f.get('save-import').disabled,false);
   const manifest=JSON.parse(f.get('preview-manifest').textContent).proposed_manifest;
   assert.equal(manifest.file_sha256,createHash('sha256').update(csv).digest('hex'));
   assert.deepEqual(Buffer.from(f.requests.at(-1).payload.csv_text,'utf8'),csv);
   assert.equal(f.requests.at(-1).payload.definition_json.definition.name,document.definition.name);
   await f.click('save-import');
   assert.equal(JSON.parse(f.get('commit-result').textContent).manifest.file_sha256,manifest.file_sha256);
  });
  assert.deepEqual(failures,[]);
 }finally{child.kill('SIGTERM');await stopped;fs.rmSync(temporary,{recursive:true,force:true});}
}
tests().catch(error=>{console.error(error);process.exitCode=1;});
