import http from 'node:http';
import { readFile, writeFile, mkdir, rename, copyFile, stat } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID, createHash } from 'node:crypto';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const data = process.env.PART_STUDY_DATA_DIR || path.join(root, '.part-study');
await mkdir(data, {recursive:true});
const python = process.env.FAI_PYTHON || (process.platform === 'win32' ? path.join(root,'.venv/Scripts/python.exe') : 'python');
const port = Number(process.env.PORT || 3210), origin = `http://127.0.0.1:${port}`;
const uuid = value => /^[a-f0-9-]{36}$/.test(value || '');
const readJSON = async file => JSON.parse(await readFile(file,'utf8'));
async function save(file,value) { await writeFile(`${file}.tmp`,JSON.stringify(value)); await rename(`${file}.tmp`,file); }
const defaults = [];
let study;
try { study = await readJSON(path.join(data,'active.json')); }
catch(error) { if(error.code !== 'ENOENT') throw error; study = {id:randomUUID(),metadata:{title:'Part study',part:'',units:''},schema:defaults,reports:[],paused:false}; }
const folder = () => path.join(data,study.id);
await mkdir(folder(),{recursive:true});
const persist = () => save(path.join(data,'active.json'),study);
// Serialize manifest saves so simultaneous uploads cannot race a shared temporary file.
let saves = Promise.resolve();
function persistState() { const snapshot=JSON.stringify(study);saves=saves.catch(()=>{}).then(async()=>{await writeFile(path.join(data,'active.json.tmp'),snapshot);await rename(path.join(data,'active.json.tmp'),path.join(data,'active.json'));});return saves; }
const docPath = id => path.join(folder(),`${id}.json`);
for(const report of study.reports) if(['processing','uploading'].includes(report.status)) {report.status=report.status==='processing'?'queued':'failed';report.error='Previous process was interrupted; retry upload if needed.';}
const definitions = new Map();
const signatures = new Map();
function addDefinitions(items) {
  if(new Set([...definitions.keys(),...items.map(d=>d.id)]).size>1000)throw new Error('This study exceeds 1,000 discovered characteristics; split it into smaller studies.');
  let added=false;
  for(const d of items){
    const signature=JSON.stringify([d.nominal,d.upperTol,d.lowerTol,d.units]);
    if(!definitions.has(d.id)){definitions.set(d.id,{...d,number:definitions.size+1});signatures.set(d.id,new Set());added=true;}
    signatures.get(d.id).add(signature);
    definitions.get(d.id).variants=signatures.get(d.id).size;
  }
  study.schema=[...definitions.values()];
  if(added)for(const report of study.reports){const present=new Set(report.presentIds||[]);report.missing=study.schema.filter(d=>!present.has(d.id)).length;}
}
function summarize(report,document) {
  report.presentIds=Object.keys(document.values||{}).filter(id=>document.values[id]?.value!==null&&document.values[id]?.value!==undefined);
  report.matched=report.presentIds.length;report.outside=0;report.unspecified=0;
  const present=new Set(report.presentIds);report.missing=study.schema.filter(d=>!present.has(d.id)).length;
  for(const reading of Object.values(document.values||{})){
    if(reading.value===null||reading.value===undefined)continue;
    const d=reading.definition||{},n=d.nominal;
    const lo=n!==null&&n!==undefined&&d.lowerTol!==null&&d.lowerTol!==undefined?n+d.lowerTol:null;
    const hi=n!==null&&n!==undefined&&d.upperTol!==null&&d.upperTol!==undefined?n+d.upperTol:null;
    if(lo===null&&hi===null)report.unspecified++;
    if((lo!==null&&reading.value<lo-1e-9)||(hi!==null&&reading.value>hi+1e-9))report.outside++;
  }
}
for(const report of study.reports)if(report.status==='ready'){
  const document=await readJSON(docPath(report.id));
  if(document.extractionVersion!==2){report.status='outdated';report.error='Re-extract this PDF to obtain its own nominal, tolerance and measurement fields.';}
  else {if(!report.excluded)addDefinitions(document.definitions||[]);summarize(report,document);}
}
study.extractionVersion=2;study.schema=[...definitions.values()];
const view=()=>({...study,reports:study.reports.map(({presentIds,...report})=>report)});
await persist();
let running = false;
function worker(args) {
  return new Promise((resolve,reject)=>{
    const child=spawn(python,[path.join(root,'app/worker.py'),...args],{cwd:root,windowsHide:true});
    let out='',err='';const timer=setTimeout(()=>child.kill(),15*60*1000);
    child.stdout.on('data',chunk=>{out+=chunk;if(out.length>30_000_000)child.kill();});
    child.stderr.on('data',chunk=>{err=(err+chunk).slice(-5000);});
    child.on('error',error=>{clearTimeout(timer);reject(new Error(`Python could not start: ${error.message}`));});
    child.on('close',code=>{clearTimeout(timer);if(code===0){try{resolve(JSON.parse(out));}catch{reject(new Error('Invalid extractor response.'));}}else reject(new Error(err||'Processing stopped or exceeded 15 minutes.'));});
  });
}
async function processQueue() {
  if(running || study.paused)return;
  const report=study.reports.find(r=>r.status==='queued');if(!report)return;
  running=true;report.status='processing';
  try {
    await persistState();
    const result=await worker(['scan',path.join(folder(),`${report.id}.pdf`),report.name]);
    await save(docPath(report.id),{...result,id:report.id});
    if(!report.excluded)addDefinitions(result.definitions||[]);
    summarize(report,result);
    if(!study.metadata.part&&result.part)study.metadata.part=result.part;
    Object.assign(report,{status:'ready',serial:result.serial,part:result.part,revision:result.revision,matched:Object.keys(result.values).length,warnings:result.warnings,error:''});
  } catch(error) {report.status='failed';report.error=error.message;}
  finally {running=false;await persistState();setImmediate(processQueue);}
}
async function body(req,limit) {let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;if(size>limit)throw new Error('File exceeds upload limit.');chunks.push(chunk);}return Buffer.concat(chunks);}
const jsonBody=async req=>JSON.parse((await body(req,8*1024*1024)).toString());
function json(res,status,value){res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));}
const server=http.createServer(async(req,res)=>{
  res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','no-referrer');res.setHeader('Cache-Control','no-store');
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'");
  try {
    if(req.headers.host!==`127.0.0.1:${port}` || (req.headers.origin && req.headers.origin!==origin))return json(res,403,{error:'Open the local app address.'});
    const url=new URL(req.url,origin);
    if(req.method==='GET' && ['/', '/app.js','/style.css'].includes(url.pathname)){
      const file=url.pathname==='/'?'index.html':url.pathname.slice(1);res.setHeader('Content-Type',file.endsWith('.html')?'text/html; charset=utf-8':file.endsWith('.js')?'text/javascript; charset=utf-8':'text/css; charset=utf-8');return res.end(await readFile(path.join(root,'public',file)));
    }
    if(req.method==='GET' && url.pathname==='/api/study')return json(res,200,view());
    if(req.method==='PATCH' && url.pathname==='/api/study'){
      const payload=await jsonBody(req);
      if(payload.metadata)for(const key of ['title','part','units'])if(typeof payload.metadata[key]==='string')study.metadata[key]=payload.metadata[key].slice(0,200);
      if(typeof payload.paused==='boolean')study.paused=payload.paused;
      await persistState();setImmediate(processQueue);return json(res,200,view());
    }
    if(req.method==='POST' && url.pathname==='/api/new'){
      if(running || study.reports.some(r=>['queued','uploading'].includes(r.status)))return json(res,409,{error:'Finish the queue before starting another study.'});
      study={id:randomUUID(),metadata:{title:'Part study',part:'',units:''},schema:defaults,reports:[],paused:false};definitions.clear();signatures.clear();await mkdir(folder(),{recursive:true});await persistState();return json(res,200,view());
    }
    if(req.method==='POST' && url.pathname==='/api/reextract'){
      if(running||study.reports.some(r=>['queued','uploading'].includes(r.status)))return json(res,409,{error:'Wait for the current queue to finish first.'});
      for(const report of study.reports){
        try{
          await stat(path.join(folder(),`${report.id}.pdf`));
          try{await copyFile(docPath(report.id),path.join(folder(),`${report.id}.before-reextract-${randomUUID()}.json`));}catch(error){if(error.code!=='ENOENT')throw error;}
          report.status='queued';report.error='';report.presentIds=[];report.matched=0;
        }catch(error){report.status='failed';report.error='Source PDF unavailable: '+error.message;}
      }
      definitions.clear();signatures.clear();study.schema=[];study.paused=false;
      await persistState();setImmediate(processQueue);return json(res,200,view());
    }
    if(req.method==='POST' && url.pathname==='/api/reports'){
      if(!req.headers['x-retry-id'] && study.reports.length>=5000)return json(res,400,{error:'The limit is 5,000 reports per study.'});
      const name=decodeURIComponent(req.headers['x-file-name'] || 'report.pdf').slice(0,500);
      const retry=req.headers['x-retry-id'];
      const report=retry?study.reports.find(r=>r.id===retry && r.status==='failed'):null;
      if(retry && !report)return json(res,409,{error:'Only failed reports can be retried.'});
      const record=report||{id:randomUUID(),name,status:'uploading',excluded:false};
      if(!report)study.reports.push(record);
      else {record.status='uploading';record.error='';record.name=name;}
      await persistState();
      try{
        if(req.headers['content-type']!=='application/pdf')throw new Error('Select a PDF report.');
        const bytes=await body(req,50*1024*1024);
        if(!bytes.subarray(0,1024).includes(Buffer.from('%PDF-')))throw new Error('File does not contain a PDF header.');
        await writeFile(path.join(folder(),`${record.id}.pdf`),bytes);
        record.hash=createHash('sha256').update(bytes).digest('hex');
        record.duplicateOf=study.reports.find(r=>r.id!==record.id && r.hash===record.hash)?.name || '';
        record.status='queued';await persistState();setImmediate(processQueue);return json(res,202,record);
      }catch(error){record.status='failed';record.error=error.message;await persistState();throw error;}
    }
    const match=url.pathname.match(/^\/(api\/reports|pdf)\/([a-f0-9-]+)$/);
    if(match && uuid(match[2])){
      const report=study.reports.find(r=>r.id===match[2]);if(!report)return json(res,404,{error:'Report not found.'});
      if(req.method==='GET' && match[1]==='pdf'){res.setHeader('Content-Type','application/pdf');return res.end(await readFile(path.join(folder(),`${report.id}.pdf`)));}
      if(req.method==='GET')return json(res,200,{...await readJSON(docPath(report.id)),...report});
      if(req.method==='PATCH'){
        const payload=await jsonBody(req);
        if(report.status!=='ready')return json(res,409,{error:'Wait until the report finishes processing.'});
        const document=await readJSON(docPath(report.id));
        if(typeof payload.serial==='string'){report.serial=payload.serial.slice(0,120);document.serial=report.serial;}
        if(payload.values){
          for(const [id,reading] of Object.entries(payload.values)){
            if(!study.schema.some(d=>d.id===id))throw new Error('Unknown characteristic.');
            const raw=reading.value, value=raw===''||raw===null?null:Number(raw);
            if(value!==null && !Number.isFinite(value))throw new Error('Use a finite measurement or leave it blank.');
            const previous=document.values[id]||{};
            const changed=value!==previous.value;
            document.values[id]={...previous,value,source:changed?`Manual correction; original: ${previous.source||'not extracted'}`:previous.source,method:changed?'Manual correction':previous.method,flags:reading.clearFlags?[]:previous.flags||[]};
          }
        }
        if(typeof payload.excluded==='boolean')report.excluded=payload.excluded;
        summarize(report,document);
        await save(docPath(report.id),document);
        if(typeof payload.excluded==='boolean'){
          definitions.clear();signatures.clear();study.schema=[];
          for(const other of study.reports)if(other.status==='ready'&&!other.excluded){const d=await readJSON(docPath(other.id));addDefinitions(d.definitions||[]);}
          for(const other of study.reports){const present=new Set(other.presentIds||[]);other.missing=study.schema.filter(d=>!present.has(d.id)).length;}
        }
        await persistState();return json(res,200,{...document,...report});
      }
    }
    if(req.method==='POST' && url.pathname==='/api/export'){
      if(running || study.reports.some(r=>['queued','uploading','processing'].includes(r.status)))return json(res,409,{error:'Finish processing every queued report before exporting.'});
      if(study.reports.some(r=>r.status==='outdated'))return json(res,409,{error:'Click Re-extract PDFs to replace the old Excel-derived fields before exporting.'});
      const reports=[];
      for(const report of study.reports){const doc=report.status==='ready'?await readJSON(docPath(report.id)):{};reports.push({...doc,...report});}
      const id=randomUUID(),input=path.join(folder(),`${id}.json`),output=path.join(folder(),`${id}.xlsx`);
      await save(input,{schema:study.schema,reports,metadata:study.metadata});await worker(['export',input,output]);
      res.setHeader('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');res.setHeader('Content-Disposition','attachment; filename="Part-study-results.xlsx"');return res.end(await readFile(output));
    }
    json(res,404,{error:'Not found.'});
  }catch(error){json(res,400,{error:error.message});}
});
server.requestTimeout=16*60*1000;
server.listen(port,'127.0.0.1',()=>{console.log(`Part Study: ${origin}\nOffline processing. Your queue and results are saved locally.`);setImmediate(processQueue);});
