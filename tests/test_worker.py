import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from PIL import Image, ImageDraw

import fitz
import openpyxl

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import worker
from pdf_extract import extract_text_tables,numeric,remove_minus,clean_rules


def spec(id='JEM_901.ODA.SIZE',nominal=99.5,upper=.25,lower=-.15):
    return dict(id=id,dimension='JEM_901',feature='ODA',axis='SIZE',nominal=nominal,upperTol=upper,lowerTol=lower,units='mm',number=1)


def report(id='a',serial='001',value=99.6,definition=None):
    d=definition or spec()
    return dict(id=id,name=f'{serial}.pdf',serial=serial,status='ready',extractionVersion=2,definitions=[d],values={d['id']:dict(definition=d,value=value,pmin=99.55,pmax=99.65,bonus=None,flags=[],source='=1+1',page=1,method='PDF text')})


def table_pdf(path,nominal='99.5',measured='99.6',lower='0.15',upper='0.25',serial='001'):
    document=fitz.open();page=document.new_page(width=842,height=595)
    page.insert_text((40,30),f'PART NAME: TEST-PART SER NUMBER: {serial}',fontsize=10)
    page.insert_text((40,60),'JEM_901 Size MM',fontsize=12)
    xs=[40,180,280,370,460,560,650,730]
    for x,t in zip(xs,['Feature','NOMINAL','+TOL','-TOL','MEAS','DEV','MAX','MIN']):page.insert_text((x,85),t,fontsize=12)
    for x,t in zip(xs,['OD_A',nominal,upper,lower,measured,'0.1','99.65','99.55']):page.insert_text((x,115),t,fontsize=12)
    document.save(path);document.close()


def headers(sheet):return {c.value:c.column for c in next(sheet.iter_rows())}


class PDFExtractionTests(unittest.TestCase):
    def test_native_pdf_supplies_all_fields_without_any_template(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'test.pdf';table_pdf(file)
            result=worker.scan(file,'test.pdf')
            self.assertEqual(result['serial'],'001')
            self.assertEqual(result['extractionVersion'],2)
            self.assertEqual(len(result['definitions']),1)
            d=result['definitions'][0];v=result['values'][d['id']]
            self.assertEqual((d['nominal'],d['upperTol'],d['lowerTol']),(99.5,.25,-.15))
            self.assertEqual((v['value'],v['pmin'],v['pmax']),(99.6,99.55,99.65))
            self.assertEqual(worker.limits(d),(99.35,99.75))

    def test_blank_tolerance_stays_unspecified_not_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'test.pdf';table_pdf(file,upper='',lower='')
            result=worker.scan(file,'test.pdf');v=next(iter(result['values'].values()))
            self.assertIsNone(v['definition']['lowerTol'])
            self.assertIsNone(v['definition']['upperTol'])
            self.assertEqual(worker.limits(v['definition']),(None,None))
            self.assertEqual(worker.assessment(v['definition'],v),'Limits not specified')

    def test_new_unlisted_characteristic_not_rejected(self):
        rows=extract_text_tables('JEM_987 Size MM\nFeature NOMINAL +TOL -TOL MEAS DEV\nNEW_FEATURE 50.0 0.2 0.3 49.9 -0.1')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['id'],'JEM_987.NEWFEATURE.SIZE')
        self.assertEqual(rows[0]['value'],49.9)

    def test_negative_and_decimal_comma_parse(self):
        self.assertEqual(numeric('-27,6013'),-27.6013)
        self.assertEqual(numeric('0'),0)
        self.assertIsNone(numeric('CIR_13'))
        self.assertIsNone(numeric(''))

    def test_grid_line_and_attached_minus_are_preserved_as_a_negative_sign(self):
        image=Image.new('L',(160,60),255);draw=ImageDraw.Draw(image)
        draw.rectangle((0,0,159,2),fill=0)
        draw.ellipse((40,12,62,48),outline=0,width=4)
        draw.rectangle((25,29,43,33),fill=0)
        cleaned,negative=remove_minus(clean_rules(image))
        self.assertTrue(negative)
        self.assertEqual(cleaned.getpixel((28,31)),255)
        self.assertLess(cleaned.getpixel((50,13)),160)

    def test_single_sided_limits_only_check_printed_side(self):
        d=spec(nominal=0,upper=.2,lower=None)
        self.assertEqual(worker.assessment(d,dict(value=.3,definition=d)),'Outside base limits')
        self.assertEqual(worker.assessment(d,dict(value=-.1,definition=d)),'Within base limits')


class WorkbookTests(unittest.TestCase):
    def export(self,payload,temp):
        file=Path(temp)/'out.xlsx';worker.export(payload,file)
        return openpyxl.load_workbook(file,data_only=True)

    def test_each_pdf_uses_own_limits_even_if_an_excel_schema_is_passed(self):
        a=report(value=99.8)
        b=report('b','002',value=100.8,definition=spec(nominal=100.5,upper=.4,lower=-.4))
        with tempfile.TemporaryDirectory() as temp:
            book=self.export(dict(reports=[a,b],schema=[spec(nominal=0,upper=999,lower=-999)]),temp)
            matrix=book['Serial matrix'];self.assertEqual(matrix['D2'].value,'Varies by PDF')
            detail=book['Measurements'];h=headers(detail)
            self.assertEqual(detail.cell(2,h['PDF nominal']).value,99.5)
            self.assertEqual(detail.cell(3,h['PDF nominal']).value,100.5)
            self.assertEqual(detail.cell(2,h['Assessment']).value,'Outside base limits')
            self.assertEqual(detail.cell(3,h['Assessment']).value,'Within base limits')
            self.assertEqual(book['Exceptions'].max_row,2)
            self.assertEqual(book['Characteristic summary']['O2'].value,1)

    def test_reported_min_max_distinct_from_population_min_max_and_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            book=self.export(dict(reports=[report(value=99.6),report('b','002',value=99.7)]),temp)
            detail=book['Measurements'];h=headers(detail)
            self.assertEqual(detail.cell(2,h['PDF MIN']).value,99.55)
            self.assertEqual(detail.cell(2,h['PDF MAX']).value,99.65)
            self.assertEqual(detail.cell(2,h['Lower limit']).value,99.35)
            stats=book['Characteristic summary'];sh=headers(stats)
            self.assertEqual(stats.cell(2,sh['Observed min']).value,99.6)
            self.assertEqual(stats.cell(2,sh['Observed max']).value,99.7)
            self.assertAlmostEqual(stats.cell(2,sh['Average']).value,99.65)

    def test_missing_different_characteristic_not_missing_tolerances(self):
        a=report();b=report('b','002',definition=spec('JEM_902.NEW.SIZE',nominal=0,upper=None,lower=None),value=0)
        with tempfile.TemporaryDirectory() as temp:
            book=self.export(dict(reports=[a,b]),temp)
            issues=[r[3] for r in book['Exceptions'].iter_rows(min_row=2,values_only=True)]
            self.assertEqual(issues,['Missing','Missing'])
            self.assertEqual(book['Serial matrix']['E6'].value,0)
            self.assertIsNone(book['Serial matrix']['D6'].value)

    def test_no_review_gate_no_nonmeasurement_exceptions(self):
        a=report(value=100);a['reviewed']=False;a['values'][spec()['id']]['flags']=['OCR warning']
        failed=dict(id='failed',name='bad.pdf',status='failed',error='Unreadable')
        with tempfile.TemporaryDirectory() as temp:
            book=self.export(dict(reports=[a,failed]),temp)
            self.assertEqual(book['Exceptions']['D2'].value,'Outside base limits')
            self.assertEqual(book['Exceptions'].max_row,2)
            self.assertIn('Unreadable',book['Source reports']['J3'].value)

    def test_old_template_extraction_cannot_be_exported_as_pdf_sourced(self):
        old=report();old.pop('extractionVersion')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'Re-extract'):worker.export(dict(reports=[old]),Path(temp)/'out.xlsx')

    def test_large_batch_stats_and_literal_excel_strings(self):
        records=[report(str(i),str(i).zfill(4),99.5+i/10000) for i in range(1000)]
        records[0]['name']='=HYPERLINK("bad")'
        with tempfile.TemporaryDirectory() as temp:
            book=self.export(dict(reports=records),temp)
            self.assertEqual(book['Serial matrix'].max_row,1004)
            self.assertEqual(book['Characteristic summary']['H2'].value,1000)
            self.assertAlmostEqual(book['Characteristic summary']['J2'].value,99.54995)
            self.assertEqual(book['Serial matrix']['B5'].data_type,'s')
            self.assertEqual(book['Measurements'].cell(2,headers(book['Measurements'])['Source text']).data_type,'s')


if __name__=='__main__':unittest.main()
