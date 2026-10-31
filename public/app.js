const $=id=>document.getElementById(id);
let study=null, page=1, importing=false, exporting=false, detail=null;
const fileCache=new Map(), edited=new Map();
const pageSize=50;
const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
function message(text){$('status').textContent=text;}
async function api(url,options={}){const response=await fetch(url,options);if(!response.ok){let error;try{error=(await response.json()).error;}catch{error='The local server did not respond. Restart the app and reload.';}throw new Error(error);}return response.json();}
const patch=(url,data)=>api(url,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
const limit=(d,upper=false)=>{const tol=upper?d?.upperTol:d?.lowerTol;return d?.nominal===null||d?.nominal===undefined||tol===null||tol===undefined?null:Number((d.nominal+tol).toFixed(8));};
const display=value=>value===null||value===undefined?'—':value;
function attention(report){return report.status==='outdated'||report.status==='ready'&&!report.excluded&&((report.missing??study.schema.length-report.matched)>0||(report.outside||0)>0);}
function assessment(d,reading){if(reading?.value===null||reading?.value===undefined||reading.value==='')return 'Missing';d=reading.definition||{};const lo=limit(d),hi=limit(d,true),value=Number(reading.value);if(lo===null&&hi===null)return 'Limits not specified';return lo!==null&&value<lo-1e-9||hi!==null&&value>hi+1e-9?'Outside base limits':'Within base limits';}
function showView(name){document.querySelectorAll('.view').forEach(node=>node.hidden=node.id!==name);document.querySelectorAll('.nav').forEach(node=>node.classList.toggle('active',node.dataset.view===name));}
for(const nav of document.querySelectorAll('.nav'))nav.addEventListener('click',()=>showView(nav.dataset.view));
function render(){
  if(!study)return;
  const reports=study.reports, ready=reports.filter(r=>r.status==='ready'), pending=reports.filter(r=>['queued','processing','uploading'].includes(r.status));
  $('total').textContent=reports.length;$('ready').textContent=ready.length;$('pending').textContent=`${pending.length} waiting / processing`;$('characteristicCount').textContent=study.schema.length;$('attention').textContent=reports.filter(attention).length;
  $('queueStatus').textContent=study.paused?`Queue paused · ${pending.length} pending`:pending.length?`● Processing queue · ${pending.length} remaining`:'● Queue up to date';
  $('pause').textContent=study.paused?'Resume queue':'Pause queue';
  $('referenceCount').textContent=`${study.schema.length} PDF characteristics`;
  $('reextract').disabled=importing||exporting||pending.length>0||!reports.length;
  $('export').disabled=importing||exporting||pending.length>0||!reports.length||reports.some(r=>r.status==='outdated');
  $('newStudy').disabled=importing||exporting||pending.length>0;
  renderReports();renderDefinitions();
}
function renderReports(){
  const term=$('search').value.toLowerCase(), filter=$('filter').value;
  const reports=study.reports.filter(r=>(!term||`${r.serial||''} ${r.name}`.toLowerCase().includes(term))&&(filter==='all'||filter==='attention'&&attention(r)||filter==='ready'&&r.status==='ready'||filter==='failed'&&r.status==='failed'||filter==='queued'&&['queued','processing','uploading'].includes(r.status)));
  const totalPages=Math.max(1,Math.ceil(reports.length/pageSize));page=Math.min(page,totalPages);
  $('reportRows').replaceChildren();
  for(const report of reports.slice((page-1)*pageSize,page*pageSize)){
    const row=el('tr');row.append(el('td',report.serial||'—'),el('td',report.name));
    const status=el('td');status.append(el('span',report.excluded?'Excluded':report.status,'badge '+report.status));if(report.duplicateOf)status.append(el('p','Identical content','muted'));if(['failed','outdated'].includes(report.status)){const error=el('p',report.error,'muted');status.append(error);}row.append(status);
    row.append(el('td',report.status==='ready'?`${report.matched} results`:'—'),el('td',report.status==='ready'&&!report.excluded?`${report.missing??study.schema.length-report.matched} missing / ${report.outside||0} outside`:'—'));
    const action=el('td');
    if(report.status==='ready'){const open=el('button','Open report','secondary tiny');open.addEventListener('click',()=>openReport(report.id));action.append(open);}
    if(report.status==='failed'){
      const retry=el('button','Retry PDF','secondary tiny'),input=document.createElement('input');input.type='file';input.accept='.pdf';input.hidden=true;
      retry.addEventListener('click',()=>{if(fileCache.has(report.id))upload([fileCache.get(report.id)],report.id);else input.click();});
      input.addEventListener('change',()=>{if(input.files[0])upload([input.files[0]],report.id);});action.append(retry,input);
    }
    row.append(action);$('reportRows').append(row);
  }
  $('reportCount').textContent=`${reports.length} reports`;$('pageNumber').textContent=`${page} / ${totalPages}`;$('prev').disabled=page<=1;$('next').disabled=page>=totalPages;$('noReports').hidden=reports.length>0;
}
function renderDefinitions(){
  $('definitionRows').replaceChildren();
  for(const d of study.schema){const row=el('tr');for(const text of [d.number,d.id,d.axis,d.variants>1?'Varies by PDF':display(d.nominal),d.variants>1?'Varies by PDF':display(limit(d)),d.variants>1?'Varies by PDF':display(limit(d,true)),d.units||'—',d.variants||1])row.append(el('td',text));$('definitionRows').append(row);}
}
async function refresh(){
  try{study=await api('/api/study');render();}
  catch(error){message(error.message);}
}
function loadSettings(){for(const key of ['title','part'])$(key).value=study.metadata[key]||'';}
async function upload(selected,retryId=null){
  if(importing||exporting)return;
  const files=[...selected].filter(f=>f.name.toLowerCase().endsWith('.pdf')).sort((a,b)=>(a.webkitRelativePath||a.name).localeCompare(b.webkitRelativePath||b.name,undefined,{numeric:true}));
  if(!files.length)return message('No PDFs found in the selected folder.');
  if(!retryId && files.length+study.reports.length>5000)return message('Split this batch: each study supports up to 5,000 PDF reports.');
  importing=true;$('folder').disabled=true;$('files').disabled=true;$('uploadProgress').hidden=false;render();
  let accepted=0, failed=0;
  for(const [index,file] of files.entries()){
    const name=file.webkitRelativePath||file.name;
    $('uploadBar').value=100*index/files.length;$('uploadLabel').textContent=`Uploading ${index+1} of ${files.length} · ${name}`;
    try{
      const headers={'Content-Type':'application/pdf','X-File-Name':encodeURIComponent(name)};if(retryId)headers['X-Retry-Id']=retryId;
      const report=await api('/api/reports',{method:'POST',headers,body:file});fileCache.set(report.id,file);accepted++;
    }catch(error){failed++;message(`Upload failed: ${name} — ${error.message}. Failed entries remain in Serial results.`);}
    await refresh();
  }
  importing=false;$('folder').disabled=false;$('files').disabled=false;$('uploadBar').value=100;$('uploadLabel').textContent=`${accepted} uploaded · ${failed} upload errors · extraction continues in the queue.`;
  message(`Folder import finished: ${accepted} PDF(s) queued. Open Serial results to follow processing and retry failures.`);render();showView('reports');
}
$('folder').addEventListener('change',event=>{upload(event.target.files);event.target.value='';});
$('files').addEventListener('change',event=>{upload(event.target.files);event.target.value='';});
$('search').addEventListener('input',()=>{page=1;renderReports();});$('filter').addEventListener('change',()=>{page=1;renderReports();});
$('prev').addEventListener('click',()=>{page--;renderReports();});$('next').addEventListener('click',()=>{page++;renderReports();});
$('saveSettings').addEventListener('click',async()=>{try{study=await patch('/api/study',{metadata:Object.fromEntries(['title','part'].map(k=>[k,$(k).value]))});message('Study details saved locally.');}catch(error){message(error.message);}});
$('pause').addEventListener('click',async()=>{try{study=await patch('/api/study',{paused:!study.paused});render();}catch(error){message(error.message);}});
$('newStudy').addEventListener('click',async()=>{
  if(!confirm('Start a new study? Export the current workbook first. Previous source files remain on disk, but the app will open the new study.'))return;
  try{study=await api('/api/new',{method:'POST'});fileCache.clear();page=1;loadSettings();render();renderDefinitions();showView('overview');message('New study created. Import PDFs to discover their measurements and specifications.');}catch(error){message(error.message);}
});
$('reextract').addEventListener('click',async()=>{
  try{study=await api('/api/reextract',{method:'POST'});render();showView('reports');message('Saved PDFs are queued for PDF-sourced extraction. Previous extraction JSON was backed up.');}catch(error){message(error.message);}
});
function showEvidence(d){
  const reading=detail.values[d.id];$('evidence').textContent=reading?`${d.id}\nPage ${reading.page||'manual'} · ${reading.method||'Manual entry'}\n${reading.source||'No extracted source text.'}\n${(reading.flags||[]).join('\n')}`:`${d.id}\nMissing: no unambiguous measurement was extracted. Enter a value only after checking the source PDF.`;
  $('pdf').src=`/pdf/${detail.id}#page=${reading?.page||1}&view=FitH`;
}
async function openReport(id){
  try{
    detail=await api(`/api/reports/${id}`);edited.clear();$('detailTitle').textContent=detail.name;$('serial').value=detail.serial;$('excluded').checked=!!detail.excluded;$('detailMessage').textContent='Changes are saved only when you click Save.';
    const duplicate=study.reports.filter(r=>r.serial&&r.serial===detail.serial).length>1;
    $('warnings').textContent=[...detail.warnings,`${study.schema.length-detail.matched} missing measurements.`,...duplicate?['Duplicate serial / rerun: kept as a separate report.']:[],...detail.duplicateOf?[`Identical PDF content: ${detail.duplicateOf}`]:[]].join(' ');
    $('measurementRows').replaceChildren();
    for(const d of study.schema){
      const reading=detail.values[d.id],row=el('tr'),label=el('td',d.id);label.tabIndex=0;label.addEventListener('click',()=>showEvidence(d));label.addEventListener('keydown',event=>{if(event.key==='Enter')showEvidence(d);});row.append(label);
      const valueCell=el('td'),input=document.createElement('input');input.type='number';input.step='any';input.value=reading?.value??'';input.setAttribute('aria-label',`Measured ${d.id}`);
      input.addEventListener('input',()=>{const change=edited.get(d.id)||{};edited.set(d.id,{...change,value:input.value});$('detailMessage').textContent='Unsaved edits. Click Save changes to apply corrections.';});valueCell.append(input);const spec=reading?.definition||{};row.append(valueCell,el('td',display(spec.nominal)),el('td',`${display(spec.upperTol)} / ${display(spec.lowerTol)}`),el('td',`${display(reading?.pmin)} / ${display(reading?.pmax)}`),el('td',`${display(limit(spec))} … ${display(limit(spec,true))}`));
      row.append(el('td',assessment(d,reading)));
      row.append(el('td',(reading?.flags||[]).join('; ')));$('measurementRows').append(row);
    }
    $('rawPages').replaceChildren();
    for(const unmatched of detail.unmatched||[]) $('rawPages').append(el('pre',`Page ${unmatched.page}: ${unmatched.reason}\n${unmatched.source}`));
    for(const p of detail.pages||[]){const section=document.createElement('details');section.append(el('summary',`Page ${p.page} · ${p.method}`),el('pre',p.text));$('rawPages').append(section);}
    if(study.schema[0])showEvidence(study.schema[0]);if(!$('detail').open)$('detail').showModal();
  }catch(error){message(error.message);}
}
async function saveReport(){
  $('saveReport').disabled=true;
  try{
    const serial=$('serial').value.trim();if(!serial)throw new Error('Enter a serial number.');
    const payload={excluded:$('excluded').checked};if(serial!==detail.serial)payload.serial=serial;if(edited.size)payload.values=Object.fromEntries(edited);
    await patch(`/api/reports/${detail.id}`,payload);await refresh();await openReport(detail.id);$('detailMessage').textContent='Changes saved. Base-limit results updated automatically.';
  }catch(error){$('detailMessage').textContent=error.message;}finally{$('saveReport').disabled=false;}
}
$('saveReport').addEventListener('click',saveReport);
function closeDetail(event){const dirty=edited.size||(detail&&($('serial').value!==detail.serial||$('excluded').checked!==!!detail.excluded));if(dirty&&!confirm('Discard unsaved report edits?')){event?.preventDefault();return;}$('detail').close();detail=null;edited.clear();$('pdf').removeAttribute('src');}
$('closeDetail').addEventListener('click',closeDetail);$('detail').addEventListener('cancel',event=>{event.preventDefault();closeDetail();});
$('export').addEventListener('click',async()=>{
  if(!study)return;exporting=true;render();message('Building the workbook from every included report…');
  try{
    const response=await fetch('/api/export',{method:'POST'});if(!response.ok)throw new Error((await response.json()).error);
    const url=URL.createObjectURL(await response.blob()),link=el('a');link.href=url;link.download='Part-study-results.xlsx';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message('Excel workbook exported. Exceptions include only missing and outside-base-limit results. Other file information is in Source reports.');
  }catch(error){message(error.message);}finally{exporting=false;render();}
});
await refresh();if(study){loadSettings();renderDefinitions();message(study.reports.some(r=>r.status==='outdated')?'Existing reports use the old definitions. Click Re-extract PDFs to read all fields from the original PDFs.':'Study restored. Import PDFs to discover their own measurements and specifications.');}
setInterval(refresh,2000);
