const $ = id => document.getElementById(id);
let state = { metadata: {}, rows: [], documents: [], coverageConfirmed: false };
let selected = null;
let scanning = false;
const metadata = [['partNumber','Part number'],['partName','Part name'],['partRevision','Part revision'],['drawingNumber','Drawing number'],['drawingRevision','Drawing revision'],['serialNumber','Serial number'],['fairId','FAIR identifier'],['purchaseOrder','Purchase order']];
function status(text) { $('status').textContent = text; }
function element(tag, text, className) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(className) el.className = className; return el; }
function field(name, label, value, onChange, wide=false) {
  const wrap = element('label', label, wide ? 'full' : '');
  const input = document.createElement(name === 'requirement' || name === 'comments' ? 'textarea' : 'input');
  input.value = value ?? ''; input.name = name;
  if(['lower','upper','resultMin','resultMax','quantity'].includes(name)) { input.type = 'number'; input.step = 'any'; }
  input.addEventListener('input', () => onChange(input.value)); wrap.append(input); return wrap;
}
function resetCoverage() { state.coverageConfirmed = false; $('coverage').checked = false; }
function renderMetadata() { $('metadata').replaceChildren(...metadata.map(([key,label]) => field(key,label,state.metadata[key], value => {state.metadata[key]=value;}))); }
function updateProgress() {
  const included = state.rows.filter(r=>r.include);
  $('count').textContent = state.rows.length;
  $('progress').textContent = `${included.filter(r=>r.reviewed).length} of ${included.length} included characteristics reviewed · ${state.documents.length} PDF(s)`;
  $('empty').hidden = state.rows.length > 0;
  $('documents').replaceChildren();
  for (const doc of state.documents) {
    const button = element('button', `${doc.name} · ${doc.pages.length} pages${doc.warnings.length ? ' · Check warnings' : ''}`, 'secondary');
    button.addEventListener('click', () => showSource({id:doc.id,documentId:doc.id,fileName:doc.name,page:1,reference:'Page 1',source:'Review all extracted pages below and use “Add manually” for missing characteristics.'}));
    $('documents').append(button);
  }
}
function showSource(row) {
  selected = row.id;
  document.querySelectorAll('.row').forEach(el=>el.classList.toggle('selected',el.dataset.id===row.id));
  $('sourceLabel').textContent = `${row.fileName || 'Manual entry'} · ${row.reference || 'No reference'}`;
  $('evidence').textContent = row.source || 'Manual characteristic. Enter a source reference and verify it against the original document.';
  const doc = state.documents.find(d=>d.id===row.documentId);
  $('pdf').hidden = !doc?.sourceUrl;
  if(doc?.sourceUrl) $('pdf').src = `${doc.sourceUrl}#page=${row.page || 1}&view=FitH`;
  else $('pdf').removeAttribute('src');
  $('sourceHelp').textContent = doc && !doc.sourceUrl ? 'To view the original PDF after opening a saved review, select “Reconnect PDF” below. A restart clears server source links.' : 'Compare the original page with the extracted text. OCR can lose signs and GD&T symbols.';
  $('pages').replaceChildren();
  for(const d of (doc ? [doc] : state.documents)) {
    $('pages').append(element('h3', d.name));
    if(!d.sourceUrl) {
      const reconnect = element('label','Reconnect PDF','button secondary'); const input = document.createElement('input'); input.type='file'; input.accept='.pdf'; input.hidden=true;
      input.addEventListener('change',()=>{const file=input.files[0];if(file){d.sourceUrl=URL.createObjectURL(file);showSource(row);}}); reconnect.append(input);$('pages').append(reconnect);
    }
    for(const warning of d.warnings || []) $('pages').append(element('p',warning));
    for(const p of d.pages || []) { const details=document.createElement('details'); details.append(element('summary',`Page ${p.page} · ${p.method} · ${p.candidates} candidates`),element('pre',p.text));$('pages').append(details); }
  }
}
function renderRows() {
  $('rows').replaceChildren();
  for(const row of state.rows) {
    const card = element('article', undefined, `row${row.include ? '' : ' excluded'}${selected===row.id?' selected':''}`); card.dataset.id=row.id;
    const top = element('div',undefined,'row-top');
    const include = element('label',''); const checkbox = document.createElement('input'); checkbox.type='checkbox';checkbox.checked=row.include;
    checkbox.addEventListener('change',()=>{row.include=checkbox.checked;resetCoverage();renderRows();}); include.append(checkbox,document.createTextNode('Include'));top.append(include,element('span',`${row.fileName || 'Manual'} · p.${row.page || '—'}`,'source'));
    const sourceButton=element('button','View source','secondary');sourceButton.addEventListener('click',()=>showSource(row));top.append(sourceButton);card.append(top);
    const fields=element('div',undefined,'row-fields');
    const invalidate = (key,value)=> {row[key]=value;row.reviewed=false;card.querySelector('.review-check input').checked=false;resetCoverage();updateProgress();};
    for(const [key,label,wide] of [['characteristic','Characteristic #'],['reference','Reference / units'],['quantity','Quantity'],['requirement','Requirement',true],['lower','Lower limit'],['upper','Upper limit'],['designator','Designator (verify)'],['resultMin','Measured min (optional)'],['resultMax','Measured max (optional)'],['comments','Comments',true]]) {
      fields.append(field(key,label,row[key],value=>invalidate(key,value),wide));
    }
    card.append(fields);
    const reviewed=element('label',undefined,'review-check');const check=document.createElement('input');check.type='checkbox';check.checked=row.reviewed;
    check.addEventListener('change',()=>{row.reviewed=check.checked;updateProgress();});reviewed.append(check,document.createTextNode('I verified this row against the source, including units and limits.'));card.append(reviewed);$('rows').append(card);
  }
  updateProgress();
}
$('coverage').addEventListener('change',()=>state.coverageConfirmed=$('coverage').checked);
$('files').addEventListener('change',async event=>{
  if(scanning)return;
  const files=[...event.target.files];scanning=true;$('export').disabled=true;$('add').disabled=true;$('restore').disabled=true;
  let failed=0;
  try {for(const [index,file] of files.entries()) {
    status(`Scanning ${index+1}/${files.length}: ${file.name}. Local OCR may take a few minutes…`);
    try {
      const response=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/pdf'},body:file});
      const result=await response.json();if(!response.ok)throw new Error(result.error);
      state.documents.push({id:result.id,name:file.name,sourceUrl:`/pdf/${result.id}`,pages:result.pages,warnings:result.warnings});
      for(const row of result.rows) state.rows.push({...row,id:crypto.randomUUID(),documentId:result.id,fileName:file.name});
      resetCoverage();renderRows();
      if(state.rows.length && !selected)showSource(state.rows[0]);
    } catch(error){failed++;state.documents.push({id:crypto.randomUUID(),name:file.name,pages:[],warnings:[`SCAN FAILED: ${error.message}. No content was extracted. Retry this file before confirming coverage.`]});renderRows();alert(`${file.name}: ${error.message}`);}
  }
  status(`Batch complete: ${files.length-failed}/${files.length} PDFs scanned. ${failed?'Some files failed; retry them. ':''}Review all pages and add missing characteristics manually.`);
  } finally {scanning=false;event.target.value='';$('export').disabled=false;$('add').disabled=false;$('restore').disabled=false;}
});
$('add').addEventListener('click',()=>{
  const row={id:crypto.randomUUID(),include:true,reviewed:false,characteristic:'',requirement:'',reference:'',source:'Manual entry'};state.rows.push(row);resetCoverage();renderRows();showSource(row);$('rows').lastElementChild?.scrollIntoView({behavior:'smooth',block:'center'});
});
function download(blob,name) { const url=URL.createObjectURL(blob);const link=element('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000); }
$('save').addEventListener('click',()=>{
  const saved={...state,version:1,documents:state.documents.map(({sourceUrl,...document})=>document)};
  download(new Blob([JSON.stringify(saved,null,2)],{type:'application/json'}),'local-fai-review.json');status('Review saved. Keep the original PDFs with this file; PDFs are not embedded in review JSON.');
});
$('restore').addEventListener('change',async event=>{
  try {
    const file=event.target.files[0];if(!file)return;if(file.size>8*1024*1024)throw new Error('Review file exceeds 8 MB.');
    const saved=JSON.parse(await file.text());if(saved.version!==1 || !Array.isArray(saved.rows) || !Array.isArray(saved.documents) || typeof saved.metadata!=='object' || saved.metadata===null)throw new Error('Not a supported Local FAI review file.');
    if(state.rows.length && !confirm('Replace the current review? Save it first if needed.'))return;
    state={metadata:saved.metadata,rows:saved.rows,documents:saved.documents.map(({sourceUrl,...d})=>d),coverageConfirmed:false};selected=null;renderMetadata();renderRows();resetCoverage();if(state.rows.length)showSource(state.rows[0]);status('Review opened. Reconnect source PDFs and verify coverage before export.');
  }catch(error){status(error.message);}finally{event.target.value='';}
});
$('export').addEventListener('click',async()=>{
  const rows=state.rows.filter(row=>row.include);
  if(!rows.length || rows.some(row=>!row.reviewed || !String(row.characteristic||'').trim() || !String(row.requirement||'').trim()))return status('Each included row needs a characteristic number, a requirement, and review confirmation.');
  if(!state.coverageConfirmed)return status('Confirm you reviewed every source page for omissions before exporting.');
  $('export').disabled=true;status('Building your draft workbook…');
  try {const response=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(state)});if(!response.ok)throw new Error((await response.json()).error);download(await response.blob(),'AS9102-reviewed-draft.xlsx');status('Draft workbook exported. Complete materials, processes, and authorization in Excel.');}
  catch(error){status(error.message);}finally{$('export').disabled=false;}
});
renderMetadata();renderRows();
