"""PDF-owned CMM tables: positioned words, column alignment and cell OCR."""
import csv
import io
import math
from pathlib import Path
import re
import subprocess
import statistics
import tempfile

from PIL import Image, ImageChops, ImageOps, ImageFilter

VERSION = 2
FIELDS = ('nominal','upperTol','lowerTol','value','deviation','outtol','pmax','pmin','bonus')


def words_ocr(image, executable, psm=3, whitelist=None):
    with tempfile.TemporaryDirectory(prefix='cmm-ocr-') as temp:
        file=Path(temp)/'ocr.png';image.save(file,dpi=(400,400))
        args=[executable,str(file),'stdout','--psm',str(psm)]
        if whitelist:args += ['-c','tessedit_char_whitelist='+whitelist]
        args += ['tsv']
        output=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',check=True,timeout=120).stdout
    words=[]
    for w in csv.DictReader(io.StringIO(output),delimiter='\t',quoting=csv.QUOTE_NONE):
        if w.get('level')=='5' and w.get('text','').strip():
            words.append(dict(text=w['text'],x=int(w['left']),y=int(w['top']),w=int(w['width']),h=int(w['height']),confidence=float(w['conf'])))
    return words


def clean_image(image):
    image=image.convert('RGB');r,g,b=image.split()
    difference=ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(r,g),b),ImageChops.darker(ImageChops.darker(r,g),b))
    gray=ImageOps.grayscale(image)
    gray.paste(255,mask=difference.point(lambda p:255 if p>35 else 0))
    return gray


def lines(words):
    rows=[]
    for w in sorted(words,key=lambda w:(w['y']+w['h']/2,w['x'])):
        center=w['y']+w['h']/2
        target=next((row for row in reversed(rows[-3:]) if abs(row['cy']-center)<max(8,min(w['h'],row['h'])*.65)),None)
        if target:target['words'].append(w)
        else:rows.append(dict(cy=center,h=w['h'],words=[w]))
    for row in rows:
        row['words'].sort(key=lambda w:w['x'])
        row['text']=' '.join(w['text'] for w in row['words'])
    return sorted(rows,key=lambda r:r['cy'])


def dimension(text):
    m=re.search(r'[JI1][E3]M(?:CO)?[_. ]*(\d+(?:\s*[_ ,]\s*\d+)*(?:\.\d+)?)',text,re.I)
    return 'JEM_'+re.sub(r'\s*[_ ,]\s*','_',m[1]) if m else None


def numeric(text):
    text=re.sub(r'\s+','',text.strip()).replace('−','-').replace('–','-').replace('—','-').replace(',','.')
    # A field is one token. Never steal digits from a feature label or fill a blank.
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',text):return None
    value=float(text)
    return value if math.isfinite(value) else None


def header_columns(row):
    anchors=[];tols=[]
    for w in row['words']:
        key=re.sub(r'[^A-Z]','',w['text'].upper())
        if key.startswith('NOM'):field='nominal'
        elif key in ('MEAS','MEASURED'):field='value'
        elif key in ('DEV','DEVIATION'):field='deviation'
        elif key in ('OUTTOL','CUTTOL'):field='outtol'
        elif key=='MAX':field='pmax'
        elif key=='MIN':field='pmin'
        elif key=='BONUS':field='bonus'
        elif key=='TOL':tols.append(w);continue
        elif key in ('AX','AXIS'):field='axis'
        elif key=='FEATURE':field='feature'
        else:continue
        anchors.append(dict(field=field,cx=w['x']+w['w']/2))
    # Tolerance signs often disappear in OCR. The printed table orders +TOL then -TOL.
    for index,w in enumerate(sorted(tols,key=lambda w:w['x'])):
        anchors.append(dict(field='upperTol' if index==0 else 'lowerTol',cx=w['x']+w['w']/2))
    anchors.sort(key=lambda a:a['cx'])
    return anchors


def row_bands(image, columns, top, bottom, font_height):
    columns=[a for a in columns if a['field'] in ('nominal','value')]
    if not columns:return []
    strips=[]
    for a in columns:
        half=max(80,int(font_height*4.0))
        strips.append(image.crop((max(0,int(a['cx']-half)),top,min(image.width,int(a['cx']+half)),bottom)).point(lambda p:0 if p<150 else 255))
    active=[]
    for y in range(max(0,bottom-top)):
        ink=sum(strip.crop((0,y,strip.width,y+1)).histogram()[0] for strip in strips)
        active.append(ink>=max(3,int(font_height*.12)))
    runs=[];start=None
    for y,on in enumerate(active+[False]):
        if on and start is None:start=y
        if not on and start is not None:
            if y-start>=max(2,font_height*.13):runs.append([start+top,y+top])
            start=None
    merged=[]
    for run in runs:
        if merged and run[0]-merged[-1][1]<max(2,font_height*.12):merged[-1][1]=run[1]
        else:merged.append(run)
    # Discard rules and oversized graphics; rows are text-height bands.
    return [(int(a-font_height*.25),int(b+font_height*.25)) for a,b in merged if font_height*.3<=b-a<=font_height*1.9]


def clean_rules(crop):
    crop=crop.copy()
    for y in range(crop.height):
        if crop.crop((0,y,crop.width,y+1)).point(lambda p:0 if p<180 else 255).histogram()[0]>crop.width*.7:
            crop.paste(255,(0,y,crop.width,y+1))
    for x in range(crop.width):
        if crop.crop((x,0,x+1,crop.height)).point(lambda p:0 if p<180 else 255).histogram()[0]>crop.height*.9:
            crop.paste(255,(x,0,x+1,crop.height))
    return crop


def remove_minus(crop):
    # Identify the actual horizontal sign before the first full-height digit.
    binary=crop.point(lambda p:0 if p<160 else 255)
    columns=[binary.crop((x,0,x+1,binary.height)).histogram()[0]>0 for x in range(binary.width)]
    runs=[];start=None
    for x,on in enumerate(columns+[False]):
        if on and start is None:start=x
        if not on and start is not None:runs.append((start,x));start=None
    start=next((i for i,on in enumerate(columns) if on),None)
    if start is not None:
        tall=next((x for x in range(start,crop.width) if (lambda b:b is not None and b[3]-b[1]>=crop.height*.25)(ImageOps.invert(binary.crop((x,0,x+1,crop.height))).getbbox())),None)
        if tall is not None and tall-start>=6:
            prefix=ImageOps.invert(binary.crop((start,0,tall,crop.height))).getbbox()
            if prefix and prefix[3]-prefix[1]<crop.height*.3 and crop.height*.35<(prefix[1]+prefix[3])/2<crop.height*.75:
                result=crop.copy();result.paste(255,(start,0,tall,crop.height));return result,True
    for start,end in runs:
        box=ImageOps.invert(binary.crop((start,0,end,binary.height))).getbbox()
        if not box:continue
        width=end-start;height=box[3]-box[1];cy=(box[1]+box[3])/2
        if height>=crop.height*.35:return crop,False
        if width>=6 and width>height*2 and crop.height*.2<cy<crop.height*.8:
            result=crop.copy();result.paste(255,(start,0,end,crop.height));return result,True
    return crop,False


def read_cells(cells, executable, numeric_only=False):
    results=[]
    for offset in range(0,len(cells),90):
        batch=cells[offset:offset+90]
        slot=100;width=650
        sheet=Image.new('L',(width,slot*len(batch)),255)
        for i,cell in enumerate(batch):
            crop=clean_rules(cell['image'])
            if numeric_only:
                crop,cell['negative']=remove_minus(crop)
            bbox=ImageOps.invert(crop.point(lambda p:0 if p<200 else 255)).getbbox()
            if bbox:crop=crop.crop((max(0,bbox[0]-4),max(0,bbox[1]-4),min(crop.width,bbox[2]+4),min(crop.height,bbox[3]+4)))
            scale=min(60/max(crop.height,1),620/max(crop.width,1))
            crop=crop.resize((max(1,round(crop.width*scale)),max(1,round(crop.height*scale))),Image.Resampling.LANCZOS)
            crop=crop.filter(ImageFilter.MinFilter(3))
            sheet.paste(crop,(15,i*slot+(slot-crop.height)//2))
        words=words_ocr(sheet,executable,6,'0123456789.,+-' if numeric_only else 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ .')
        texts=['']*len(batch)
        for word in sorted(words,key=lambda w:(int((w['y']+w['h']/2)//slot),w['x'])):
            index=int((word['y']+word['h']/2)//slot)
            if 0<=index<len(batch):texts[index]+=(' ' if texts[index] else '')+word['text']
        results.extend(texts)
    return results


def page_tables(page, executable):
    """Extract by table geometry; numeric/label crops are OCRed at readable scale."""
    raster=bool(page.get_images())
    dpi=min(400,max(72,int(6000*72/max(page.rect.width,page.rect.height))))
    pix=page.get_pixmap(dpi=dpi,alpha=False)
    original=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    image=clean_image(original)
    cell_image=ImageOps.grayscale(original)
    if raster:
        words=words_ocr(image,executable)
        sources=[words]
        # Independent strip OCR is a fallback for headings suppressed by page layout.
        for info in page.get_image_info():
            box=tuple(round(v*dpi/72) for v in info['bbox'])
            if box[2]-box[0]<image.width*.4 or box[3]-box[1]<25:continue
            strip=words_ocr(cell_image.crop(box),executable,6)
            for word in strip:word['x']+=box[0];word['y']+=box[1]
            sources.append(strip)
        method='Positioned cell OCR'
    else:
        scale=dpi/72
        words=[dict(text=w[4],x=w[0]*scale,y=w[1]*scale,w=(w[2]-w[0])*scale,h=(w[3]-w[1])*scale,confidence=100) for w in page.get_text('words')]
        sources=[words];method='Positioned PDF text'
    text_lines=lines(words)
    headers=[]
    for source in sources:
        source_lines=lines(source)
        for nominal in source:
            if not re.sub(r'[^A-Z]','',nominal['text'].upper()).startswith('NOM'):continue
            cy=nominal['y']+nominal['h']/2
            nearby=[w for w in source if abs(w['y']+w['h']/2-cy)<max(w['h'],nominal['h'])*.75]
            if not any(re.sub(r'[^A-Z]','',w['text'].upper())=='MEAS' for w in nearby):continue
            candidate=dict(cy=cy,h=nominal['h'],words=nearby,text=' '.join(w['text'] for w in nearby),source_lines=source_lines)
            existing=next((h for h in headers if abs(h['cy']-cy)<nominal['h']),None)
            if existing:
                if len(header_columns(candidate))>len(header_columns(existing)):headers[headers.index(existing)]=candidate
            else:headers.append(candidate)
    headers.sort(key=lambda h:h['cy'])
    tables=[];cells=[];labels=[]
    annotations=[]
    for block in page.get_text('blocks'):
        group=dimension(block[4])
        if group:annotations.append((block[1]*dpi/72,group))
    explicit_titles={dimension(r['text']) for source in sources for r in lines(source) if re.search(r'\b(?:MM|INCH|IN)\b',r['text'],re.I)}
    previous_group=None;previous_units=''
    for i,head in enumerate(headers):
        columns=header_columns(head)
        if not {'nominal','value'}.issubset(a['field'] for a in columns):continue
        prior=[r for r in head['source_lines'] if r['cy']<head['cy']-head['h']*.5 and (i==0 or r['cy']>headers[i-1]['cy'])]
        context=''
        group=None;units=''
        for row in reversed(prior):
            found=dimension(row['text'])
            if found:
                group=found;context=row['text'];break
        nearest=next((g for y,g in sorted(annotations,reverse=True) if y<head['cy']-head['h']),None)
        separate_members=nearest and all('JEM_'+n in explicit_titles for n in nearest.split('_')[1:])
        if nearest and not (group and group.startswith(nearest+'.')) and not separate_members:group=nearest
        if group:previous_group=group
        else:group=previous_group
        size_title=next((row['text'] for row in reversed(prior) if re.search(r'\bSize\b',row['text'],re.I)),None)
        if size_title:context=size_title
        for row in reversed(prior[-8:]):
            match=re.search(r'\b(MM|INCH|INCHES|IN)\b',row['text'],re.I)
            if match:units=match[1].lower();break
        local_units=[w for source in sources for w in source if re.sub(r'[^A-Z]','',w['text'].upper()) in ('MM','IN','INCH','INCHES') and 0<head['cy']-(w['y']+w['h']/2)<head['h']*5]
        if local_units:units=re.sub(r'[^A-Z]','',max(local_units,key=lambda w:w['y'])['text'].upper()).lower()
        units=units
        if units:previous_units=units
        if not group:continue
        height=statistics.median(w['h'] for w in head['words'] if re.sub(r'[^A-Z]','',w['text'].upper()) in ('NOMINAL','MEAS'));top=round(head['cy']+height*.7)
        bottom=round(headers[i+1]['cy']-height*1.8) if i+1<len(headers) else image.height
        if i+1<len(headers):
            titles=[r['cy'] for r in headers[i+1]['source_lines'] if head['cy']<r['cy']<headers[i+1]['cy']-height*.5 and dimension(r['text'])]
            if titles:bottom=min(bottom,round(max(titles)-height*.7))
        bands=row_bands(image,columns,top,bottom,height)
        fields=[a for a in columns if a['field'] in FIELDS]
        first=fields[0]['cx'];second=fields[1]['cx'] if len(fields)>1 else first+height*9
        numeric_left=round(first-(second-first)/2)
        axis=next((a for a in columns if a['field']=='axis'),None)
        feature_anchor=next((a for a in columns if a['field']=='feature'),None)
        label_left=round(min((info['bbox'][0]*dpi/72 for info in page.get_image_info()),default=min(w['x'] for w in head['words']))+height*.12)
        for band in bands:
            entry=dict(group=group,context=context,units=units,method=method,fields={},raw={},feature='',axis='',flags=[],cy=sum(band)/2)
            tables.append(entry)
            for n,a in enumerate(fields):
                left=(fields[n-1]['cx']+a['cx'])/2 if n else numeric_left
                right=(a['cx']+fields[n+1]['cx'])/2 if n+1<len(fields) else a['cx']+(a['cx']-fields[n-1]['cx'])/2
                cells.append(dict(entry=entry,field=a['field'],image=cell_image.crop(box:=(round(left),max(0,band[0]),min(image.width,round(right)),min(image.height,band[1]))),box=box))
            if axis:
                boundary=round((axis['cx']+first)/2)
                labels.append(dict(entry=entry,field='axis',image=cell_image.crop(box:=(round(axis['cx']-height*1.3),max(0,band[0]),boundary,min(image.height,band[1]))),box=box))
                feature_right=round(axis['cx']-height*1.3)
            else:feature_right=numeric_left
            if feature_right>label_left:
                labels.append(dict(entry=entry,field='feature',image=cell_image.crop(box:=(max(0,label_left),max(0,band[0]),feature_right,min(image.height,band[1]))),box=box))
    if raster:
        for cell,text in zip(cells,read_cells(cells,executable,True)):
            value=numeric(text)
            if value is None and any(ch.isdigit() for ch in text):
                crop=clean_rules(cell['image']);crop=crop.resize((crop.width*2,crop.height*2))
                alternate=words_ocr(crop,executable,7,'0123456789.,+-')
                recovered=numeric(''.join(w['text'] for w in sorted(alternate,key=lambda w:w['x'])))
                if recovered is not None:value=recovered;text=str(recovered)
            if value is not None and cell.get('negative'):value=-abs(value)
            cell['entry']['fields'][cell['field']]=value
            cell['entry']['raw'][cell['field']]=('-' if cell.get('negative') else '')+text
        for cell,text in zip(labels,read_cells(labels,executable)):
            text=text.strip()
            if cell['field']=='axis' and re.sub(r'[^A-Z]','',text.upper()) not in ('X','Y','Z','R','M','D','TP'):
                crop=cell['image'];crop=crop.resize((crop.width*2,crop.height*2))
                alt=words_ocr(crop,executable,10,'XYZRMDTP')
                result=''.join(w['text'] for w in alt).strip()
                if result:text=result
            cell['entry'][cell['field']]=text
    else:
        for cell in cells+labels:
            x0,y0,x1,y1=cell['box']
            inside=sorted((w for w in words if x0<=w['x']+w['w']/2<x1 and y0<=w['y']+w['h']/2<y1),key=lambda w:w['x'])
            text=' '.join(w['text'] for w in inside)
            if cell['field'] in FIELDS:
                cell['entry']['fields'][cell['field']]=numeric(text)
                cell['entry']['raw'][cell['field']]=text
            else:cell['entry'][cell['field']]=text
        if not tables:return extract_text_tables(page.get_text(sort=False)),text_lines
    return assemble(tables),text_lines


def canonical_feature(text):
    text=re.sub(r'\([^)]*\)','',text.upper())
    return re.sub(r'[^A-Z0-9]','',text)


def assemble(rows):
    records=[];pending=[];feature=None;group=None
    for row in rows:
        f=row['fields'];axis=re.sub(r'[^A-Z]','',row['axis'].upper())
        if axis and len(set(axis))==1:axis=axis[0]
        # Ignore titles/callout graphics without both printed nominal and measured value.
        if f.get('nominal') is None:continue
        if f.get('value') is None and all(f.get(k) is None for k in ('upperTol','lowerTol')):continue
        if row['group']!=group:
            pending=[];feature=None;group=row['group']
        label=canonical_feature(row['feature'])
        label=label if re.search(r'[A-Z]',label) and not label.startswith(('START','END','FEATURE','JEM')) and 'ENDPT' not in label and 'STARTP' not in label else ''
        if axis in ('X','Y','Z'):
            if any(p['axis']==axis for p in pending):pending=[];feature=None
            if label:feature=label
            row['axis']=axis;pending.append(row)
            continue
        is_position=('bonus' in f and (f.get('upperTol') is not None or f.get('lowerTol') is not None)) or axis in ('TP','P','IP')
        if label:feature=label
        if is_position:
            if not feature:
                pending=[];continue
            for coordinate in pending:
                records.append(make_record(coordinate,feature,coordinate['axis']))
            pending=[]
            records.append(make_record(row,feature,'TP'))
            feature=None
        else:
            if axis in ('R','M','D'):
                records.append(make_record(row,label,axis))
            elif label:
                records.append(make_record(row,label,'SIZE' if 'SIZE' in row['context'].upper() or f.get('lowerTol') is not None else 'M'))
    return records


def make_record(row,feature,axis):
    f=dict(row['fields']);dimension_id=row['group']
    if axis in ('X','Y','Z'):f['bonus']=None
    identifier='.'.join(x for x in (dimension_id,feature,axis) if x)
    definition=dict(id=identifier,dimension=dimension_id,feature=feature,axis=axis,units=row['units'],
                    nominal=f.get('nominal'),upperTol=f.get('upperTol'),lowerTol=-abs(f['lowerTol']) if f.get('lowerTol') is not None else None)
    if definition['upperTol'] is not None:definition['upperTol']=abs(definition['upperTol'])
    flags=list(row.get('flags',[]))
    if f.get('value') is not None and f.get('nominal') is not None and f.get('deviation') is not None:
        if abs(abs(f['value']-f['nominal'])-abs(f['deviation']))>.00025:
            flags.append('Printed nominal / measured / deviation are inconsistent; possible OCR error.')
    if f.get('pmin') is not None and f.get('pmax') is not None and f['pmin']>f['pmax']:flags.append('PDF MIN exceeds PDF MAX; possible OCR error.')
    return dict(id=identifier,definition=definition,value=f.get('value'),nominalRead=f.get('nominal'),
                pmin=f.get('pmin'),pmax=f.get('pmax'),bonus=f.get('bonus'),deviation=f.get('deviation'),outtol=f.get('outtol'),
                source=row.get('context','')+'\nFeature='+row.get('feature','')+' Axis='+row.get('axis','')+'\n'+' | '.join(f'{k}={v}' for k,v in row.get('raw',f).items()),method=row['method'],flags=flags)


def extract_text_tables(text):
    """Native text fallback for simple line-oriented CMM exports; no external definitions."""
    rows=[];group=None;context='';columns=[];units=''
    for line in text.splitlines():
        found=dimension(line)
        if found:
            group=found;context=line;columns=[]
            m=re.search(r'\b(MM|INCH|IN)\b',line,re.I)
            if m:units=m[1].lower()
            continue
        if 'NOMINAL' in line.upper() and 'MEAS' in line.upper():
            # Text exports without coordinates require complete fixed-column numeric rows.
            cols=re.findall(r'NOMINAL|\+TOL|-TOL|MEAS|DEV|OUTTOL|MAX|MIN|BONUS',line.upper())
            mapping={'NOMINAL':'nominal','+TOL':'upperTol','-TOL':'lowerTol','MEAS':'value','DEV':'deviation','OUTTOL':'outtol','MAX':'pmax','MIN':'pmin','BONUS':'bonus'}
            columns=[mapping[x] for x in cols];continue
        if not group or not columns:continue
        match=re.search(r'(?<!\S)[+-]?(?:\d+\.\d+|\d+)(?=\s|$)',line)
        if not match:continue
        prefix=line[:match.start()].strip()
        nums=[numeric(t) for t in line[match.start():].split()]
        if len(nums)!=len(columns) or any(n is None for n in nums):continue
        axis_match=re.search(r'(?:^|\s)(TP|X|Y|Z|R|M|D)(?:\s|$)',prefix,re.I)
        axis=axis_match[1].upper() if axis_match else ''
        label=(prefix[:axis_match.start()]+prefix[axis_match.end():]).strip() if axis_match else prefix
        rows.append(dict(group=group,context=context,units=units,method='PDF text',axis=axis,feature=label,fields=dict(zip(columns,nums)),flags=[]))
    return assemble(rows)
