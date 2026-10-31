"""Offline CMM extraction and multi-serial part-study workbooks."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import fitz
import openpyxl
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
GREEN = '245C4E'


def number(value):
    if value is None or str(value).strip() == '':
        return None
    n = float(value)
    if not math.isfinite(n):
        raise ValueError('Measurements must be finite numbers.')
    return n


def normalized(value):
    return re.sub(r'[^A-Z0-9]', '', str(value).upper())


def validate_schema(schema):
    if not isinstance(schema,list) or len(schema)>1000:
        raise ValueError('A study supports up to 1,000 PDF-derived characteristics.')
    seen=set()
    for d in schema:
        if not d.get('id') or d['id'] in seen:raise ValueError('Characteristic IDs must be unique.')
        seen.add(d['id'])
        for field in ('nominal','upperTol','lowerTol'):number(d.get(field))


def scan(file, filename):
    from pdf_extract import page_tables, VERSION
    executable=os.environ.get('TESSERACT_CMD') or shutil.which('tesseract')
    warnings,pages,records=[],[],[]
    with fitz.open(file) as document:
        if document.needs_pass or not 0<len(document)<=200:raise ValueError('Use an unlocked PDF with 1–200 pages.')
        for i,page in enumerate(document,1):
            if page.get_images() and not executable:
                warnings.append(f'Page {i}: local Tesseract is unavailable; image tables were not read.')
                rows=[];text=page.get_text()
            else:
                rows,line_data=page_tables(page,executable)
                text='\n'.join(line['text'] for line in line_data)
            for row in rows:
                row['page']=i
                row['definition']['sourcePage']=i
                records.append(row)
            pages.append(dict(page=i,text=text,method='Cell OCR' if page.get_images() else 'PDF text'))
    text='\n'.join(p['text'] for p in pages)
    serial=re.search(r'SER\s*(?:IAL\s*)?NUMBER\s*[:：]\s*([A-Z0-9_-]+)',text,re.I)
    serial=serial[1] if serial else ''
    serial_source='report'
    if not serial:
        m=re.search(r'(?:^|[#_ -])(\d+(?:_RR\d*)?(?:_FINAL)?)(?:[_. -]|$)',Path(filename).name,re.I)
        serial=m[1] if m else Path(filename).stem;serial_source='filename'
        warnings.append('Serial inferred from filename.')
    part=re.search(r'PART\s*NAME\s*[:：]\s*[—–-]*\s*([A-Z0-9_-]+)',text,re.I)
    revision=re.search(r'REV\s*NUMBER\s*:\s*([A-Z0-9]+)',text,re.I)
    # Resolve a visibly truncated feature against a unique full label printed elsewhere
    # in the same PDF, never against an Excel vocabulary.
    for row in records:
        d=row['definition'];feature=d.get('feature','')
        if d['axis'] in ('X','Y','Z','TP') and len(feature)>=7:
            choices={r['definition']['feature'] for r in records if r['definition']['dimension']==d['dimension'] and r['definition']['axis']=='SIZE' and len(r['definition']['feature'])>len(feature) and r['definition']['feature'].startswith(feature[:-1])}
            if len(choices)==1:
                d['feature']=next(iter(choices));d['id']='.'.join((d['dimension'],d['feature'],d['axis']));row['id']=d['id']
                row['flags'].append('Truncated feature label resolved from the unique full label in this PDF.')
    measured_groups={r['definition']['dimension'] for r in records}
    for page in pages:
        for match in re.finditer(r'JEMCO\s+(\d+)(?![\d,])',page['text'],re.I):
            group='JEM_'+match[1]
            if group not in measured_groups and not any(g.startswith(group+'.') or g.startswith(group+'_') for g in measured_groups):
                warning=f'{group}: manual checklist item; no measured numeric result is reported.' if 'MANUAL CHECKLIST' in page['text'] else f'{group}: annotation found but no readable measurement table was present.'
                if warning not in warnings:warnings.append(warning)
    values={};definitions=[]
    for row in records:
        key=row['id']
        if key in values:
            # Retain repeated measurements rather than silently overwriting a run.
            n=2
            while f'{key}.repeat{n}' in values:n+=1
            key=f'{key}.repeat{n}';row['id']=key;row['definition']['id']=key
            row['flags'].append('Repeated identifier in this PDF; retained separately.')
        row['definition']['number']=len(definitions)+1
        definitions.append(row['definition']);values[key]=row
    if not values:warnings.append('No readable CMM measurement tables found in this PDF.')
    return dict(serial=serial,serialSource=serial_source,part=part[1] if part else '',revision=revision[1] if revision else '',
        values=values,definitions=definitions,warnings=warnings,pages=pages,unmatched=[],extractionVersion=VERSION)


def limits(d):
    n=number(d.get('nominal'))
    lo=number(d.get('lowerTol'));hi=number(d.get('upperTol'))
    return (round(n+lo,9) if n is not None and lo is not None else None,
            round(n+hi,9) if n is not None and hi is not None else None)


def assessment(d,reading):
    if not reading or number(reading.get('value')) is None:return 'Missing'
    low,high=limits(reading.get('definition') or d)
    if low is None and high is None:return 'Limits not specified'
    value=number(reading['value'])
    return 'Outside base limits' if (low is not None and value<low-1e-9) or (high is not None and value>high+1e-9) else 'Within base limits'


def pdf_schema(reports):
    result={}
    for report in reports:
        if report.get('status')!='ready' or report.get('excluded'):continue
        if report.get('extractionVersion')!=2:raise ValueError('Re-extract saved PDFs to replace the old Excel-derived definitions.')
        for d in report.get('definitions',[]):result.setdefault(d['id'],d)
        for key,row in report.get('values',{}).items():
            if row.get('definition'):result.setdefault(key,row['definition'])
    schema=list(result.values());validate_schema(schema)
    return schema


def shared_field(d,reports,field):
    values=[]
    for report in reports:
        reading=report.get('values',{}).get(d['id'])
        if reading:
            spec=reading.get('definition',{})
            value=limits(spec)[0 if field=='lowerLimit' else 1] if field in ('lowerLimit','upperLimit') else spec.get(field)
            if value not in values:values.append(value)
    return values[0] if len(values)==1 else 'Varies by PDF' if values else None


class Writer:
    """Row-streamed XLSX: bounded worksheet memory for large serial batches."""
    def __init__(self, output):
        import xlsxwriter
        self.book = xlsxwriter.Workbook(output, {'constant_memory': True,
            'strings_to_formulas': False, 'strings_to_urls': False})
        self.header = self.book.add_format({'font_name':'Calibri','font_size':10,'bold':True,
            'bg_color':GREEN,'font_color':'white','text_wrap':True,'valign':'vcenter'})
        self.formats = [self.book.add_format({'font_name':'Calibri','font_size':10,
            'bg_color':bg,'font_color':'#24342D','num_format':'0.0000','valign':'top'})
            for bg in ('#FFFFFF','#F0F5F1')]
        self.warn = self.book.add_format({'bg_color':'#FBE1DD','font_color':'#893D2C'})

    def sheet(self, name, headers, widths=None):
        sheet=self.book.add_worksheet(name)
        sheet.hide_gridlines(2)
        sheet.freeze_panes(1,1)
        for i in range(len(headers)):
            sheet.set_column(i,i,(widths or {}).get(i+1,18))
        sheet.set_row(0,36)
        sheet.set_landscape();sheet.set_paper(8);sheet.fit_to_pages(1,0)
        sheet.repeat_rows(0)
        sheet.set_footer('&CPart Study | &P of &N | Verify source reports')
        result={'sheet':sheet,'row':0,'columns':len(headers)}
        self.append(result,headers,header=True)
        return result

    def append(self, target, values, header=False):
        sheet,row=target['sheet'],target['row']
        if row>=1048576:
            raise ValueError('Excel row limit exceeded; split the study.')
        for col,value in enumerate(values):
            fmt=self.header if header else self.formats[row%2]
            if isinstance(value,dict) and '_result' in value:
                if value.get('outside'):fmt=self.warn
                value=value['_result']
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                sheet.write_number(row,col,value,fmt)
            elif value is None or value=='':
                sheet.write_blank(row,col,None,fmt)
            else:
                sheet.write_string(row,col,str(value)[:32767],fmt)
        target['row']+=1

    def filter(self,target):
        if target['row']>1:
            target['sheet'].autofilter(0,0,target['row']-1,target['columns']-1)


def export(payload, output):
    reports,meta=payload['reports'],payload.get('metadata',{})
    schema=pdf_schema(reports)
    if not reports or len(reports)>5000:
        raise ValueError('Export between 1 and 5,000 reports.')
    if len(reports)*len(schema)>950000:
        raise ValueError('This study exceeds 950,000 measurements; split it into smaller studies.')
    writer=Writer(output)
    overview=writer.sheet('Study overview',['PART STUDY / MULTI-SERIAL RESULTS',''],{1:34,2:110})
    matrix=writer.sheet('Serial matrix',['Serial number','Source report','Missing count']+[d['id'] for d in schema],{1:22,2:48,3:16})
    stats=writer.sheet('Characteristic summary',['Characteristic','Dimension','Axis','Units','Nominal','Lower limit','Upper limit','Results','Missing','Average','Observed min','Observed max','Range','Std dev (sample)','Outside base limits','Assessment rule'],{1:44,2:22,16:60})
    detail=writer.sheet('Measurements',['Serial number','Source report','Characteristic','Axis','PDF nominal','PDF +TOL','PDF -TOL','Lower limit','Upper limit','Measured','PDF MIN','PDF MAX','PDF DEV','PDF OUTTOL','PDF BONUS','Assessment','Units','Page','Extraction','Source text','Notes'],{1:22,2:42,3:44,16:25,20:85,21:70})
    exceptions=writer.sheet('Exceptions',['Serial number','Source report','Characteristic','Issue','Measured','Lower limit','Upper limit','Page'],{1:22,2:42,3:44,4:90})
    sources=writer.sheet('Source reports',['Report ID','Serial number','Source path','Status','Included','Part read','Revision read','Matched','Missing','Warnings'],{1:38,2:22,3:60,10:95})
    append=writer.append
    names={}
    for report in reports:
        serial=report.get('serial','')
        if serial:names[serial]=names.get(serial,0)+1
    successful=[r for r in reports if r.get('status')=='ready' and not r.get('excluded')]
    revisions=sorted({r['revision'] for r in successful if r.get('revision')})
    info=[('Study',meta.get('title','Part study')),('Part / drawing',meta.get('part','')),('Units','Read from each PDF; see Measurements'),
        ('Created UTC',datetime.now(timezone.utc).isoformat(timespec='seconds')),('Reports (all)',len(reports)),
        ('Included parsed reports',len(successful)),('Characteristics',len(schema)),
        ('Notes','Limits come from each PDF’s printed nominal and tolerances. Blank tolerances remain unspecified. Bonus is not applied automatically.'),
        ('Missing data','Blank cells mean missing/unreadable, never zero. Failed and excluded reports are listed in Source reports.'),
        ('PDF MIN / MAX','Read from the PDF into Measurements; distinct from tolerance limits and observed min/max across serials.'),
        ('Statistics','Calculated automatically from included measured results at export. No review confirmation is required. No fixed B17:B200 ranges.'),
        ('Scope','Part-study compilation, not an AS9102 form. Included reports should describe the same part and revision.')]
    for item in info:append(overview,item)
    for label,key in [('PDF nominal','nominal'),('PDF lower limit','lowerLimit'),('PDF upper limit','upperLimit')]:
        append(matrix,[label,None,None]+[shared_field(d,successful,key) for d in schema])
    for report in reports:
        values=report.get('values',{})
        warning=list(report.get('warnings',[]))
        if report.get('error'):warning.append(report['error'])
        if names.get(report.get('serial'),0)>1:warning.append('Duplicate serial / rerun: retained as a separate report')
        if report.get('duplicateOf'):warning.append('Identical PDF content: '+report['duplicateOf'])
        if len(revisions)>1:warning.append('Mixed drawing revisions in study: '+', '.join(revisions))
        if meta.get('part') and report.get('part') and normalized(meta['part'])!=normalized(report['part']):warning.append('Part differs from study; verify before combining')
        append(sources,[report['id'],report.get('serial'),report.get('name'),report.get('status'),'No' if report.get('excluded') else 'Yes',report.get('part'),report.get('revision'),sum(v.get('value') is not None for v in values.values()),sum(values.get(d['id'],{}).get('value') is None for d in schema),' | '.join(warning)])
        if report.get('status')!='ready' or report.get('excluded'):
            continue
        append(matrix,[report.get('serial'),report.get('name'),sum(values.get(d['id'],{}).get('value') is None for d in schema)]+[{'_result':values.get(d['id'],{}).get('value'),'outside':assessment(d,values.get(d['id'],{}))=='Outside base limits'} for d in schema])
        for d in schema:
            reading=values.get(d['id'],{})
            value=number(reading.get('value'));spec=reading.get('definition') or {};low,high=limits(spec);status=assessment(spec,reading)
            append(detail,[report.get('serial'),report.get('name'),d['id'],d['axis'],spec.get('nominal'),spec.get('upperTol'),spec.get('lowerTol'),low,high,value,reading.get('pmin'),reading.get('pmax'),reading.get('deviation'),reading.get('outtol'),reading.get('bonus'),status,spec.get('units'),reading.get('page'),reading.get('method'),reading.get('source'),' | '.join(reading.get('flags',[]))])
            if status in ('Missing','Outside base limits'):append(exceptions,[report.get('serial'),report.get('name'),d['id'],status,value,low,high,reading.get('page')])
    for d in schema:
        readings=[(r,r.get('values',{}).get(d['id'],{})) for r in successful]
        data=[number(v['value']) for r,v in readings if v.get('value') is not None]
        outside=sum(assessment(v.get('definition',{}),v)=='Outside base limits' for r,v in readings if v)
        append(stats,[d['id'],d['dimension'],d['axis'],shared_field(d,successful,'units'),shared_field(d,successful,'nominal'),shared_field(d,successful,'lowerLimit'),shared_field(d,successful,'upperLimit'),len(data),len(successful)-len(data),statistics.mean(data) if data else None,min(data) if data else None,max(data) if data else None,max(data)-min(data) if data else None,statistics.stdev(data) if len(data)>1 else None,outside,'Each PDF’s own tolerances; blank limits are unspecified; bonus not applied'])
    matrix['sheet'].freeze_panes(4,3);matrix['sheet'].repeat_rows(0,3);matrix['sheet'].set_row(0,90)
    for i,d in enumerate(schema,3):
        matrix['sheet'].set_column(i,i,23)
    for sheet in (stats,detail,exceptions,sources):writer.filter(sheet)
    writer.book.close()


if __name__ == '__main__':
    try:
        op = sys.argv[1]
        if op == 'scan':
            result=scan(sys.argv[2],sys.argv[3])
        elif op == 'export':
            export(json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')),sys.argv[3]);result={'ok':True}
        else: raise ValueError('Unknown operation')
        print(json.dumps(result,ensure_ascii=True))
    except Exception as error:
        print(f'{type(error).__name__}: {error}',file=sys.stderr)
        sys.exit(1)
