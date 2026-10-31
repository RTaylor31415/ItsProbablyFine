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


def reference(file):
    book = openpyxl.load_workbook(file, read_only=True, data_only=True)
    for sheet in book:
        # Recognize the supplied Part Study layout by labels, not fixed sheet name.
        grid = list(sheet.iter_rows(min_row=1, max_row=30, max_col=min(sheet.max_column, 1000), values_only=True))
        labels = {str(row[0]).strip().lower(): i for i, row in enumerate(grid) if row and row[0] is not None}
        if not all(k in labels for k in ('pcd-id', 'nominal', 'u tol', 'l tol')):
            continue
        result = []
        for column in range(1, len(grid[labels['pcd-id']])):
            identifier = grid[labels['pcd-id']][column]
            if not identifier:
                continue
            identifier = str(identifier)
            result.append(dict(id=identifier, number=len(result)+1,
                dimension=str(grid[labels['dimension id']][column]) if 'dimension id' in labels else identifier.split('.')[0],
                axis=str(grid[labels['axis']][column]) if 'axis' in labels else '',
                nominal=number(grid[labels['nominal']][column]),
                upperTol=number(grid[labels['u tol']][column]),
                lowerTol=number(grid[labels['l tol']][column]), units='mm'))
        validate_schema(result)
        book.close()
        return result
    book.close()
    raise ValueError('Reference needs rows labelled PCD-ID, Nominal, U Tol and L Tol, with a characteristic in each column.')


def validate_schema(schema):
    if not isinstance(schema, list) or not 1 <= len(schema) <= 1000:
        raise ValueError('Use between 1 and 1,000 characteristic definitions.')
    seen = set()
    for item in schema:
        if not item.get('id') or item['id'] in seen:
            raise ValueError('Characteristic IDs must be nonempty and unique.')
        seen.add(item['id'])
        n, up, low = (number(item.get(k)) for k in ('nominal', 'upperTol', 'lowerTol'))
        if n is None or up is None or low is None or up < 0 or low > 0:
            raise ValueError('Each characteristic needs a nominal, nonnegative upper tolerance, and nonpositive lower tolerance.')


def ocr_page(page, executable):
    with tempfile.TemporaryDirectory(prefix='part-study-') as temp:
        image = Path(temp) / 'page.png'
        scale = min(300/72, 5000/max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pix.set_dpi(round(scale*72), round(scale*72))
        pix.save(image)
        return subprocess.run([executable, str(image), 'stdout', '--psm', '3'],
            capture_output=True, text=True, encoding='utf-8', check=True, timeout=120).stdout


def parse_pages(pages, schema):
    """Match explicit dimension + feature + axis. Never match by column/row order."""
    values, issues, unmatched = {}, [], []
    groups = {}
    for definition in schema:
        groups.setdefault(normalized(definition['dimension']), []).append(definition)
    for page in pages:
        group, header, feature = None, '', None
        pending = []
        for raw in page['text'].splitlines():
            line = raw.strip()
            if not line:
                continue
            group_match = re.search(r'JEM(?:CO)?[_. ]*(\d+(?:\s*[_ ,]\s*\d+)*(?:\.\d+)?)', line, re.I)
            if group_match:
                key = normalized('JEM_' + group_match[1])
                group = key if key in groups else None
                header, feature, pending = '', None, []
                continue
            if 'NOMINAL' in line.upper() and 'MEAS' in line.upper():
                header, feature, pending = line.upper(), None, []
                continue
            if not group or not header:
                continue
            tokens = list(re.finditer(r'(?<![\w.])[-+]?(?:\d+\.\d+|\d+,\d{4})(?![\w.])', line))
            if not tokens:
                continue
            nums = [float(t[0].replace(',', '.')) for t in tokens]
            prefix = line[:tokens[0].start()].strip(' |')
            if len(nums) < 3:
                unmatched.append(dict(page=page['page'], source=raw, reason='Incomplete numeric row'))
                continue
            definitions = groups[group]
            axis_match = re.search(r'(?:^|\s)(TP|X|Y|Z|R|M)(?:\s|$)', prefix.upper())
            axis = axis_match[1] if axis_match else None
            possible_features = set()
            for d in definitions:
                tail = d['id'][len(d['dimension'])+1:]
                if '.' in tail:
                    possible_features.add(tail.rsplit('.', 2)[0] if tail.endswith('.D.SIZE') else tail.rsplit('.', 1)[0])
            clean_prefix = re.sub(r'\([^)]*\)', '', prefix)
            clean_prefix = re.sub(r'(?:^|\s)(TP|X|Y|Z|R|M)(?=\s|$)', ' ', clean_prefix, flags=re.I)
            explicit = [f for f in possible_features if normalized(f) == normalized(clean_prefix)]
            # Never map an unknown feature by a substring or report row order.
            explicit = sorted(explicit, key=lambda f:len(normalized(f)), reverse=True)
            chosen = explicit[0] if explicit else None
            if chosen:
                feature = chosen
            if axis in ('X','Y','Z') and len(nums) == 3:
                if axis == 'X':
                    pending = []
                    if not chosen:
                        feature = None
                pending.append((axis, nums, raw))
                continue
            is_position = 'BONUS' in header
            if is_position and len(nums) >= 6:
                axis = 'TP'
            matches = []
            for d in definitions:
                tail = d['id'][len(d['dimension'])+1:]
                if is_position:
                    match = tail.endswith('.TP') and feature and normalized(tail.rsplit('.',1)[0]) == normalized(feature)
                elif 'SIZE' in d['id']:
                    match = chosen and normalized(tail[:-7]) == normalized(chosen)
                else:
                    match = axis == d['axis'] or (d['axis'] == 'M' and chosen)
                    if '.' in tail and chosen:
                        match = match and normalized(tail.rsplit('.',1)[0]) == normalized(chosen)
                if match:
                    matches.append(d)
            if len(matches) != 1:
                unmatched.append(dict(page=page['page'], source=raw, reason='Feature/axis not uniquely identified'))
                pending = []
                continue
            definition = matches[0]
            if is_position:
                index = 3
            elif 'MAX' in header and 'DEV' not in header:
                index = 2
            elif len(nums) == 7 and 'MAX' in header and 'DEV' in header:
                # Unilateral form: nominal, +tol, meas, dev, outtol, max, min.
                index = 2
            else:
                index = 3
            if len(nums) <= index:
                continue
            row = dict(value=nums[index], source=raw, page=page['page'], method=page['method'],
                       nominalRead=nums[0], pmin=None, pmax=None, bonus=None, flags=[])
            if 'MAX' in header and 'DEV' not in header and len(nums) >= 5:
                row['pmax'], row['pmin'] = nums[3], nums[4]
            elif 'MAX' in header and len(nums) >= 7:
                row['pmax'], row['pmin'] = nums[-2], nums[-1]
            if is_position and len(nums) >= 7:
                row['bonus'] = nums[6]
            put_value(values, definition, row)
            if is_position and feature:
                for ax, coordinates, source in pending:
                    targets = [d for d in definitions if d['axis'] == ax and normalized(d['id'].rsplit('.',1)[0]) == normalized(definition['id'].rsplit('.',1)[0])]
                    if len(targets) == 1:
                        put_value(values, targets[0], dict(value=coordinates[1], nominalRead=coordinates[0], source=source,
                            page=page['page'], method=page['method'], pmin=None, pmax=None, bonus=None, flags=[]))
                pending = []
    return values, issues, unmatched


def put_value(values, definition, row):
    if abs(row['nominalRead'] - definition['nominal']) > 0.00015:
        row['flags'].append('Nominal differs from reference; check OCR signs and mapping')
    if row.get('pmin') is not None and row.get('pmax') is not None and row['pmin'] > row['pmax']:
        row['flags'].append('PMin exceeds PMax; check OCR')
    if definition['id'] in values:
        values[definition['id']]['flags'].append('Repeated characteristic in report; first occurrence retained')
        return
    values[definition['id']] = row


def scan(file, schema, filename):
    validate_schema(schema)
    executable = os.environ.get('TESSERACT_CMD') or shutil.which('tesseract')
    warnings, pages = [], []
    with fitz.open(file) as document:
        if document.needs_pass or not 0 < len(document) <= 200:
            raise ValueError('Use an unlocked PDF with 1–200 pages.')
        for i, page in enumerate(document, 1):
            text, method = page.get_text(sort=True), 'PDF text'
            if page.get_images() or len(text.strip()) < 100:
                if executable:
                    try:
                        text, method = ocr_page(page, executable), 'OCR'
                    except (OSError, subprocess.SubprocessError) as error:
                        warnings.append(f'Page {i}: OCR failed ({type(error).__name__}).')
                else:
                    warnings.append(f'Page {i}: Tesseract unavailable; raster measurements were not read.')
            pages.append(dict(page=i, text=text, method=method))
    text = '\n'.join(p['text'] for p in pages)
    serial = re.search(r'SER\s*(?:IAL\s*)?NUMBER\s*[:：]\s*([A-Z0-9_-]+)', text, re.I)
    serial = serial[1] if serial else ''
    if not serial:
        match = re.search(r'(?:^|[#_ -])(\d+(?:_RR\d*)?(?:_FINAL)?)(?:[_. -]|$)', Path(filename).name, re.I)
        serial = match[1] if match else Path(filename).stem
        warnings.append('Serial inferred from filename; verify before using.')
    part = re.search(r'PART\s*NAME\s*[:：]\s*[—–-]*\s*([A-Z0-9_-]+)', text, re.I)
    revision = re.search(r'REV\s*NUMBER\s*:\s*([A-Z0-9]+)', text, re.I)
    values, issues, unmatched = parse_pages(pages, schema)
    if not values:
        warnings.append('No measurements matched this reference. Check the PDF format and characteristic definitions.')
    return dict(serial=serial, part=part[1] if part else '', revision=revision[1] if revision else '',
                values=values, warnings=warnings+issues, unmatched=unmatched, pages=pages, reviewed=False,
                serialSource='report' if 'Serial inferred' not in '\n'.join(warnings) else 'filename')


def limits(d):
    return round(d['nominal']+d['lowerTol'], 9), round(d['nominal']+d['upperTol'], 9)


def assessment(d, reading, reviewed):
    if not reading or number(reading.get('value')) is None:
        return 'Missing'
    if reading.get('flags'):
        return 'Check mapping'
    if not reviewed:
        return 'Unreviewed'
    if d['axis'] == 'DRF' or d['id'].endswith('.TP'):
        return 'Geometric review'
    low, high = limits(d)
    return 'Outside base limits' if reading['value'] < low-1e-9 or reading['value'] > high+1e-9 else 'Within base limits'


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
    schema,reports,meta=payload['schema'],payload['reports'],payload.get('metadata',{})
    validate_schema(schema)
    if not reports or len(reports)>5000:
        raise ValueError('Export between 1 and 5,000 reports.')
    if len(reports)*len(schema)>950000:
        raise ValueError('This study exceeds 950,000 measurements; split it into smaller studies.')
    writer=Writer(output)
    overview=writer.sheet('Study overview',['PART STUDY / MULTI-SERIAL RESULTS',''],{1:34,2:110})
    matrix=writer.sheet('Serial matrix',['Serial number','Source report','Review','Missing count']+[d['id'] for d in schema],{1:22,2:48,3:18,4:16})
    stats=writer.sheet('Characteristic summary',['Characteristic','Dimension','Axis','Units','Nominal','Lower limit','Upper limit','Results','Missing','Reviewed','Average','Min','Max','Range','Std dev (sample)','Outside base limits','Assessment rule'],{1:44,2:22,17:60})
    detail=writer.sheet('Measurements',['Serial number','Source report','Characteristic','Axis','Nominal','Lower limit','Upper limit','Measured','PMin','PMax','Bonus','Assessment','Review','Page','Extraction','Source text','Flags'],{1:22,2:42,3:44,12:25,16:85,17:70})
    exceptions=writer.sheet('Exceptions',['Serial number','Source report','Characteristic','Issue','Measured','Lower limit','Upper limit','Page'],{1:22,2:42,3:44,4:90})
    sources=writer.sheet('Source reports',['Report ID','Serial number','Source path','Status','Included','Reviewed','Part read','Revision read','Matched','Missing','Warnings'],{1:38,2:22,3:60,11:95})
    append=writer.append
    names={}
    for report in reports:
        serial=report.get('serial','')
        if serial:names[serial]=names.get(serial,0)+1
    successful=[r for r in reports if r.get('status')=='ready' and not r.get('excluded')]
    revisions=sorted({r['revision'] for r in successful if r.get('revision')})
    info=[('Study',meta.get('title','Part study')),('Part / drawing',meta.get('part','')),('Units',meta.get('units','mm')),
        ('Created UTC',datetime.now(timezone.utc).isoformat(timespec='seconds')),('Reports (all)',len(reports)),
        ('Included parsed reports',len(successful)),('Characteristics',len(schema)),
        ('Reviewed reports',sum(bool(r.get('reviewed')) for r in successful)),
        ('Notes','Reference base-limit screening only. Geometric tolerances, bonus, datums, and certification require separate review.'),
        ('Missing data','Blank cells mean missing/unreadable, never zero. Failed and excluded reports are listed in Source reports.'),
        ('PMin / PMax','Retained separately in Measurements. Never mixed into population statistics.'),
        ('Statistics','Calculated from included measured results at export, including unreviewed readings. Review counts are shown. No fixed B17:B200 ranges.'),
        ('Scope','Part-study compilation, not an AS9102 form. Included reports should describe the same part and revision.')]
    for item in info:append(overview,item)
    for label,key in [('Nominal','nominal'),('Lower limit',None),('Upper limit',None)]:
        append(matrix,[label,None,None,None]+[d[key] if key else limits(d)[0 if label=='Lower limit' else 1] for d in schema])
    for report in reports:
        values=report.get('values',{})
        warning=list(report.get('warnings',[]))
        if names.get(report.get('serial'),0)>1:warning.append('Duplicate serial / rerun: retained as a separate report')
        if report.get('duplicateOf'):warning.append('Identical PDF content: '+report['duplicateOf'])
        if len(revisions)>1:warning.append('Mixed drawing revisions in study: '+', '.join(revisions))
        if meta.get('part') and report.get('part') and normalized(meta['part'])!=normalized(report['part']):warning.append('Part differs from study; verify before combining')
        append(sources,[report['id'],report.get('serial'),report.get('name'),report.get('status'),'No' if report.get('excluded') else 'Yes','Yes' if report.get('reviewed') else 'No',report.get('part'),report.get('revision'),sum(v.get('value') is not None for v in values.values()),sum(values.get(d['id'],{}).get('value') is None for d in schema),' | '.join(warning)])
        if report.get('status')!='ready' or report.get('excluded'):
            append(exceptions,[report.get('serial'),report.get('name'),'','Excluded' if report.get('excluded') else report.get('error',report.get('status')),None,None,None,None]);continue
        if warning:append(exceptions,[report.get('serial'),report.get('name'),'',' | '.join(warning),None,None,None,None])
        append(matrix,[report.get('serial'),report.get('name'),'Reviewed' if report.get('reviewed') else 'Unreviewed',sum(values.get(d['id'],{}).get('value') is None for d in schema)]+[values.get(d['id'],{}).get('value') for d in schema])
        for d in schema:
            reading=values.get(d['id'],{})
            value=number(reading.get('value'));low,high=limits(d);status=assessment(d,reading,report.get('reviewed'))
            append(detail,[report.get('serial'),report.get('name'),d['id'],d['axis'],d['nominal'],low,high,value,reading.get('pmin'),reading.get('pmax'),reading.get('bonus'),status,'Yes' if report.get('reviewed') else 'No',reading.get('page'),reading.get('method'),reading.get('source'),' | '.join(reading.get('flags',[]))])
            if status!='Within base limits':append(exceptions,[report.get('serial'),report.get('name'),d['id'],status,value,low,high,reading.get('page')])
    for d in schema:
        readings=[(r,r.get('values',{}).get(d['id'],{})) for r in successful]
        data=[number(v['value']) for r,v in readings if v.get('value') is not None]
        low,high=limits(d)
        append(stats,[d['id'],d['dimension'],d['axis'],d.get('units',meta.get('units','mm')),d['nominal'],low,high,len(data),len(successful)-len(data),sum(bool(r.get('reviewed')) and v.get('value') is not None for r,v in readings),statistics.mean(data) if data else None,min(data) if data else None,max(data) if data else None,max(data)-min(data) if data else None,statistics.stdev(data) if len(data)>1 else None,sum(v<low-1e-9 or v>high+1e-9 for v in data) if not d['id'].endswith('.TP') else None,'Geometric review (no automatic acceptance)' if d['id'].endswith('.TP') else 'Reference base limits; not certification'])
    matrix['sheet'].freeze_panes(4,4);matrix['sheet'].repeat_rows(0,3);matrix['sheet'].set_row(0,90)
    for i,d in enumerate(schema,4):
        matrix['sheet'].set_column(i,i,23)
        letter=get_column_letter(i+1)
        if matrix['row']>4:
            matrix['sheet'].conditional_format(4,i,matrix['row']-1,i,{'type':'formula','criteria':f'AND(ISNUMBER({letter}5),OR({letter}5<{letter}$3,{letter}5>{letter}$4))','format':writer.warn})
    for sheet in (stats,detail,exceptions,sources):writer.filter(sheet)
    writer.book.close()


if __name__ == '__main__':
    try:
        op = sys.argv[1]
        if op == 'scan':
            result=scan(sys.argv[2],json.loads(Path(sys.argv[3]).read_text(encoding='utf-8')),sys.argv[4])
        elif op == 'reference': result=reference(sys.argv[2])
        elif op == 'export':
            export(json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')),sys.argv[3]);result={'ok':True}
        else: raise ValueError('Unknown operation')
        print(json.dumps(result,ensure_ascii=True))
    except Exception as error:
        print(f'{type(error).__name__}: {error}',file=sys.stderr)
        sys.exit(1)
