import http from 'node:http';
import { readFile, writeFile, mkdir, rename } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID, createHash } from 'node:crypto';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const data = path.join(root, '.part-study');
await mkdir(data, {recursive:true});
const python = process.env.FAI_PYTHON || (process.platform === 'win32' ? path.join(root,'.venv/Scripts/python.exe') : 'python');
const port = Number(process.env.PORT || 3210), origin = `http://127.0.0.1:${port}`;
const uuid = value => /^[a-f0-9-]{36}$/.test(value || '');
const readJSON = async file => JSON.parse(await readFile(file,'utf8'));
async function save(file,value) { await writeFile(`${file}.tmp`,JSON.stringify(value)); await rename(`${file}.tmp`,file); }
const defaults = await readJSON(path.join(root,'assets/characteristics.json'));
let study;
try { study = await readJSON(path.join(data,'active.json')); }
catch(error) { if(error.code !== 'ENOENT') throw error; study = {id:randomUUID(),metadata:{title:'Part study',part:'110780-001_MAJORS',units:'mm'},schema:defaults,reports:[],paused:false}; }
const folder = () => path.join(data,study.id);
await mkdir(folder(),{recursive:true});
const persist = () => save(path.join(data,'active.json'),study);
// Serialize manifest saves so simultaneous uploads cannot race a shared temporary file.
let saves = Promise.resolve();
function persistState() { const snapshot=JSON.stringify(study);saves=saves.catch(()=>{}).then(async()=>{await writeFile(path.join(data,'active.json.tmp'),snapshot);await rename(path.join(data,'active.json.tmp'),path.join(data,'active.json'));});return saves; }
const docPath = id => path.join(folder(),`${id}.json`);
for(const report of study.reports) if(['processing','uploading'].includes(report.status)) {report.status=report.status==='processing'?'queued':'failed';report.error='Previous process was interrupted; retry upload if needed.';}
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
    const schemaPath=path.join(folder(),'schema.json');await save(schemaPath,study.schema);
    const result=await worker(['scan',path.join(folder(),`${report.id}.pdf`),schemaPath,report.name]);
    await save(docPath(report.id),{...result,id:report.id});
    Object.assign(report,{status:'ready',serial:result.serial,part:result.part,revision:result.revision,matched:Object.keys(result.values).length,reviewed:false,warnings:result.warnings,error:''});
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
    if(req.method==='GET' && url.pathname==='/api/study')return json(res,200,study);
    if(req.method==='PATCH' && url.pathname==='/api/study'){
      const payload=await jsonBody(req);
      if(payload.metadata)for(const key of ['title','part','units'])if(typeof payload.metadata[key]==='string')study.metadata[key]=payload.metadata[key].slice(0,200);
      if(typeof payload.paused==='boolean')study.paused=payload.paused;
      await persistState();setImmediate(processQueue);return json(res,200,study);
    }
    if(req.method==='POST' && url.pathname==='/api/new'){
      if(running || study.reports.some(r=>['queued','uploading'].includes(r.status)))return json(res,409,{error:'Finish the queue before starting another study.'});
      study={id:randomUUID(),metadata:{title:'Part study',part:'',units:'mm'},schema:defaults,reports:[],paused:false};await mkdir(folder(),{recursive:true});await persistState();return json(res,200,study);
    }
    if(req.method==='POST' && url.pathname==='/api/reference'){
      if(study.reports.length)return json(res,409,{error:'Load the reference before importing PDFs, or start a new study.'});
      const file=path.join(folder(),'reference.xlsx');await writeFile(file,await body(req,20*1024*1024));
      study.schema=await worker(['reference',file]);study.referenceName=decodeURIComponent(req.headers['x-file-name']||'Custom reference workbook').slice(0,200);await persistState();return json(res,200,{count:study.schema.length});
    }
    if(req.method==='POST' && url.pathname==='/api/reports'){
      if(!req.headers['x-retry-id'] && study.reports.length>=5000)return json(res,400,{error:'The limit is 5,000 reports per study.'});
      const name=decodeURIComponent(req.headers['x-file-name'] || 'report.pdf').slice(0,500);
      const retry=req.headers['x-retry-id'];
      const report=retry?study.reports.find(r=>r.id===retry && r.status==='failed'):null;
      if(retry && !report)return json(res,409,{error:'Only failed reports can be retried.'});
      const record=report||{id:randomUUID(),name,status:'uploading',reviewed:false,excluded:false};
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
        if(typeof payload.serial==='string'){report.serial=payload.serial.slice(0,120);document.serial=report.serial;report.reviewed=false;}
        if(payload.values){
          for(const [id,reading] of Object.entries(payload.values)){
            if(!study.schema.some(d=>d.id===id))throw new Error('Unknown characteristic.');
            const raw=reading.value, value=raw===''||raw===null?null:Number(raw);
            if(value!==null && !Number.isFinite(value))throw new Error('Use a finite measurement or leave it blank.');
            const previous=document.values[id]||{};
            const changed=value!==previous.value;
            document.values[id]={...previous,value,source:changed?`Manual correction; original: ${previous.source||'not extracted'}`:previous.source,method:changed?'Manual correction':previous.method,flags:reading.clearFlags?[]:previous.flags||[]};
          }
          report.reviewed=false;
        }
        if(typeof payload.excluded==='boolean')report.excluded=payload.excluded;
        if(typeof payload.reviewed==='boolean')report.reviewed=payload.reviewed;
        document.reviewed=report.reviewed;report.matched=Object.values(document.values).filter(v=>v.value!==null).length;
        await save(docPath(report.id),document);await persistState();return json(res,200,{...document,...report});
      }
    }
    if(req.method==='POST' && url.pathname==='/api/export'){
      if(running || study.reports.some(r=>['queued','uploading','processing'].includes(r.status)))return json(res,409,{error:'Finish processing every queued report before exporting.'});
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
