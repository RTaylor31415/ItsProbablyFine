"""Local extraction and template export. Document text is data, never commands."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import fitz
import openpyxl
from openpyxl.workbook.properties import CalcProperties

ROOT = Path(__file__).resolve().parent.parent
NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
SYMMETRIC = re.compile(rf"(?P<nom>{NUMBER})\s*(?:±|\+/-)\s*(?P<tol>{NUMBER})")
ASYMMETRIC = re.compile(rf"(?P<nom>{NUMBER})\s*\+\s*(?P<plus>\d*\.?\d+)\s*/?\s*-\s*(?P<minus>\d*\.?\d+)")


def candidate(page, line, requirement, lower="", upper="", number="", quantity="", kind="Requirement"):
    return dict(page=page, source=line, requirement=requirement, lower=lower, upper=upper,
                characteristic=number, quantity=quantity, designator=kind, reviewed=False,
                include=True, resultMin="", resultMax="", comments="", reference=f"Page {page}")


def extract_candidates(text, page):
    """Conservative candidates: explicit tolerance text, CMM size rows, manual checklist.

    Never interpret GD&T symbols or copy OCR measurements into certified results.
    """
    output = []
    context = ""
    size_table = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.search(r"JEM(?:CO|[_ .])", line, re.I):
            context = line
            size_table = False
        if "NOMINAL" in line.upper():
            size_table = all(x in line.upper() for x in ("+TOL", "MEAS", "DEV")) and "AX" not in line.upper() and "BONUS" not in line.upper() and "MAX" not in line.upper()
            continue
        match = SYMMETRIC.search(line) or ASYMMETRIC.search(line)
        if match:
            n = float(match['nom'])
            plus = float(match['tol'] if 'tol' in match.groupdict() else match['plus'])
            minus = float(match['tol'] if 'tol' in match.groupdict() else match['minus'])
            if plus >= 0 and minus >= 0:
                output.append(candidate(page, line, line, round(n-minus, 8), round(n+plus, 8), kind="Explicit tolerance"))
                continue
        if size_table:
            # Only full ordinary CMM size rows with NOMINAL +TOL -TOL MEAS DEV OUTTOL.
            match = re.match(rf"^([A-Za-z][\w-]*)\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})(?:\s|$)", line)
            if match:
                label, nominal, plus, minus = match.group(1, 2, 3, 4)
                nominal, plus, minus = float(nominal), float(plus), abs(float(minus))
                if plus >= 0:
                    output.append(candidate(page, f"{context}\n{line}", f"{label}: {nominal:g} +{plus:g}/-{minus:g} (confirm units and callout)", round(nominal-minus, 8), round(nominal+plus, 8), kind="CMM size"))
                    continue
        if re.search(r"SURFACE TEXTURE|REMOVE ALL BURRS|BREAK SHARP EDGES|JEMCO\s+[\d, ]+:|\bRa\s*\d", line, re.I):
            output.append(candidate(page, line, line, kind="Manual checklist"))
    return output


def ocr_page(page, executable):
    with tempfile.TemporaryDirectory(prefix='local-fai-') as temp:
        image = Path(temp) / 'page.png'
        # Bound render memory for very large engineering sheets.
        scale = min(300 / 72, 5000 / max(page.rect.width, page.rect.height))
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pixmap.set_dpi(round(scale * 72), round(scale * 72))
        pixmap.save(image)
        result = subprocess.run([executable, str(image), 'stdout', '--psm', '3'],
                                capture_output=True, text=True, encoding='utf-8', timeout=120, check=True)
        return result.stdout


def scan(file):
    executable = os.environ.get('TESSERACT_CMD') or shutil.which('tesseract')
    warnings, pages, rows = [], [], []
    with fitz.open(file) as document:
        if document.needs_pass:
            raise ValueError('Password-protected PDFs must be unlocked locally before scanning.')
        if not 0 < len(document) <= 200:
            raise ValueError('Select a PDF containing 1–200 pages, or split it into smaller files.')
        for i, page in enumerate(document, 1):
            text = page.get_text(sort=True)
            # Even a substantial text overlay can conceal raster measurement tables.
            needs_ocr = bool(page.get_images()) or len(text.strip()) < 100
            method = 'PDF text'
            if needs_ocr:
                if executable:
                    try:
                        text = ocr_page(page, executable)
                        method = 'Local OCR'
                    except (subprocess.SubprocessError, OSError) as error:
                        warnings.append(f'Page {i}: OCR failed ({type(error).__name__}); only the PDF text layer was used. Review the entire page manually.')
                else:
                    warnings.append(f'Page {i}: local Tesseract is unavailable. Image content was not read.')
            extracted = extract_candidates(text, i)
            rows.extend(extracted)
            pages.append(dict(page=i, text=text, method=method, candidates=len(extracted)))
    return dict(pages=pages, rows=rows, warnings=warnings,
                limitations='Candidates are incomplete by design. GD&T, datums, basic dimensions, general tolerances, and units require manual review. Inspection results are left blank. Review every source page for omitted characteristics.')


def safe_text(cell, value):
    """Force document/user text to a string cell, including leading '='."""
    cell.value = str('' if value is None else value)[:32767]
    cell.data_type = 's'


def numeric(value):
    if value is None or str(value).strip() == '':
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Numeric fields must contain finite numbers.')
    return result


def export(payload, output):
    rows = [row for row in payload.get('rows', []) if row.get('include')]
    if not rows or len(rows) > 5000:
        raise ValueError('Select between 1 and 5,000 characteristics.')
    if not payload.get('coverageConfirmed'):
        raise ValueError('Review all source pages for missing characteristics before export.')
    for row in rows:
        if row.get('reviewed') is not True or not str(row.get('characteristic', '')).strip() or not str(row.get('requirement', '')).strip():
            raise ValueError('Every included row needs a characteristic number, requirement, and review confirmation.')
        low, high = numeric(row.get('lower')), numeric(row.get('upper'))
        if low is not None and high is not None and low > high:
            raise ValueError('A lower limit exceeds its upper limit.')
        a, b = numeric(row.get('resultMin')), numeric(row.get('resultMax'))
        if a is not None and b is not None and a > b:
            raise ValueError('A minimum result exceeds its maximum result.')
        if row.get('quantity') not in ('', None):
            q = numeric(row['quantity'])
            if q is None or q <= 0 or not q.is_integer():
                raise ValueError('Quantity must be a positive whole number or blank.')
    workbook = openpyxl.load_workbook(ROOT / 'assets/as9102-template.xltx')
    workbook.template = False
    for sheet in workbook:
        for line in sheet:
            for cell in line:
                if (isinstance(cell.value, str) and cell.value.startswith('iex:')) or isinstance(cell.value, openpyxl.worksheet.formula.ArrayFormula):
                    cell.value = None
        sheet.oddHeader.center.text = 'DRAFT - Review and authorize before use'
        sheet.oddFooter.center.text = 'Prepared with Local FAI | Not a signed certification'
    form1 = workbook['Form1']
    meta = payload.get('metadata', {})
    mapping = {'partNumber':'A3', 'partName':'B3', 'serialNumber':'D3', 'fairId':'E3',
               'partRevision':'A5', 'drawingNumber':'B5', 'drawingRevision':'D5', 'purchaseOrder':'E7'}
    for key, address in mapping.items():
        safe_text(form1[address], meta.get(key, ''))
    for address in ('B38', 'B39'):
        form1[address] = None
    form1['A1'] = 'DRAFT — Form 1: Part Number Accountability'
    workbook['CofC']['A1'] = 'DRAFT — NOT A SIGNED CERTIFICATE'
    # Never carry the template's blanket conformance assertion into an unsigned draft.
    workbook['CofC']['A3'] = 'DRAFT ONLY. Material, process, inspection, and conformance statements require independent completion, verification, and authorization.'
    base = workbook['Form3']
    sheets = [base]
    for index in range(1, math.ceil(len(rows)/99)):
        sheet = workbook.copy_worksheet(base)
        sheet.title = f'Form3_{index+1}'
        sheet.print_title_rows = base.print_title_rows
        sheet.oddHeader.center.text = 'DRAFT - Review and authorize before use'
        sheet.oddFooter.center.text = 'Prepared with Local FAI | Not a signed certification'
        sheets.append(sheet)
    for index, sheet in enumerate(sheets):
        sheet['B1'] = f'DRAFT — Characteristics {index*99+1}–{min((index+1)*99, len(rows))}'
        for target, row in enumerate(rows[index*99:(index+1)*99], 8):
            values = {'B':row['characteristic'], 'C':row.get('reference',''), 'D':row.get('designator',''),
                      'F':row['requirement'], 'M': f"Source: {row.get('fileName','')} | {row.get('comments','')} | Verify source coverage and units."}
            for column, value in values.items():
                safe_text(sheet[f'{column}{target}'], value)
            for column, field in [('E','quantity'), ('G','lower'), ('H','upper'), ('I','resultMin'), ('J','resultMax')]:
                sheet[f'{column}{target}'] = numeric(row.get(field))
    audit = workbook.create_sheet('Extraction review')
    audit.append(['DRAFT — source evidence; OCR text may be inaccurate.'])
    audit.append(['File', 'Page', 'Characteristic', 'Reference', 'Source text', 'Requirement', 'Review confirmed'])
    for row in rows:
        audit.append([None]*7)
        for cell, value in zip(audit[audit.max_row], [row.get('fileName'), row.get('page'), row.get('characteristic'), row.get('reference'), row.get('source'), row.get('requirement'), 'Yes']):
            safe_text(cell, value)
    audit.column_dimensions['A'].width = 35
    for column in ('D','E','F'):
        audit.column_dimensions[column].width = 60
    workbook.calculation = CalcProperties(calcId=0, fullCalcOnLoad=True)
    workbook.save(output)


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'scan':
            print(json.dumps(scan(sys.argv[2]), ensure_ascii=True))
        elif sys.argv[1] == 'export':
            export(json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')), sys.argv[3])
            print(json.dumps({'ok': True}))
        else:
            raise ValueError('Unknown operation')
    except Exception as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
