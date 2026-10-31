import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
import openpyxl

spec=importlib.util.spec_from_file_location('worker',Path(__file__).resolve().parents[1]/'app/worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


def definition(identifier='JEM_13_14.CYL_B1.D.SIZE',nominal=11.5,axis='D'):
    return dict(id=identifier,number=1,dimension='JEM_13_14',axis=axis,nominal=nominal,upperTol=.1,lowerTol=-.1,units='mm')


def parse(text,schema):
    return worker.parse_pages([dict(text=text,page=1,method='OCR')],schema)[0]


class ParserTests(unittest.TestCase):
    def test_size_measured_column_not_tolerance_or_deviation(self):
        d=definition()
        values=parse('JEM_13_14 Size | MM\nFeature NOMINAL +TOL -TOL MEAS DEV OUTTOL\nCYL_B1 11.5000 0.1000 0.1000 11.4978 -0.0022 0.0000',[d])
        self.assertEqual(values[d['id']]['value'],11.4978)

    def test_position_coordinate_and_bonus_are_kept_distinct(self):
        schema=[definition('JEM_13_14.CYL_B1.TP',0,'DRF'),definition('JEM_13_14.CYL_B1.X',0,'X'),definition('JEM_13_14.CYL_B1.Y',0,'Y')]
        text='''JEM_13_14 MM
Feature AX NOMINAL +TOL -TOL MEAS DEV OUTTOL BONUS
X 0.0000 -0.0045 -0.0045
CYL_B1 Y 0.0000 0.0000 0.0000
(START PT) TP 0.0000 0.1000 0.0000 0.0090 0.0090 0.0000 0.0978'''
        v=parse(text,schema)
        self.assertEqual(v[schema[0]['id']]['value'],.009)
        self.assertEqual(v[schema[0]['id']]['bonus'],.0978)
        self.assertEqual(v[schema[1]['id']]['value'],-.0045)
        self.assertEqual(v[schema[2]['id']]['value'],0)
        self.assertEqual(worker.assessment(schema[0],v[schema[0]['id']],True),'Geometric review')

    def test_unknown_feature_does_not_match_by_substring_or_order(self):
        d=definition('JEM_13_14.CYL_1.D.SIZE')
        v=parse('JEM_13_14 Size\nFeature NOMINAL +TOL -TOL MEAS DEV OUTTOL\nCYL_11 11.5000 0.1000 0.1000 11.4000 0.1000 0.0000',[d])
        self.assertEqual(v,{})

    def test_profile_result_and_pmin_pmax_are_separate(self):
        d=definition('JEM_13_14.SCN4.M',0,'M')
        v=parse('JEM_13_14 MM\nFeature NOMINAL +TOL MEAS MAX MIN OUTTOL\nSCN4 0.0000 0.5000 0.2755 0.1377 -0.0468 0.0000',[d])[d['id']]
        self.assertEqual((v['value'],v['pmin'],v['pmax']),(.2755,-.0468,.1377))

    def test_sign_mismatch_and_duplicate_values_are_flagged(self):
        d=definition(nominal=-11.5)
        line='CYL_B1 11.5000 0.1000 0.1000 11.4978 -0.0022 0.0000'
        v=parse('JEM_13_14 Size\nFeature NOMINAL +TOL -TOL MEAS DEV OUTTOL\n'+line+'\n'+line,[d])[d['id']]
        self.assertEqual(len(v['flags']),2)
        self.assertEqual(worker.assessment(d,v,True),'Check mapping')


class ExportTests(unittest.TestCase):
    def report(self,id='a',serial='001_FINAL',value=11.5):
        return dict(id=id,serial=serial,name='=HYPERLINK("bad")',status='ready',reviewed=True,values={'JEM_13_14.CYL_B1.D.SIZE':dict(value=value,pmin=-99,pmax=99,source='=1+1',flags=[],page=1,method='OCR')})

    def test_statistics_missing_exclusions_duplicate_and_literal_strings(self):
        reports=[self.report(value=11.4),self.report('b',value=11.6),self.report('c','002',value=0),self.report('d','003',value=None),dict(id='e',name='failed.pdf',status='failed',error='Unreadable'),dict(self.report('f',value=999),excluded=True)]
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'out.xlsx';worker.export(dict(schema=[definition()],reports=reports,metadata={}),file)
            book=openpyxl.load_workbook(file,data_only=False)
            self.assertEqual(book.sheetnames,['Study overview','Serial matrix','Characteristic summary','Measurements','Exceptions','Source reports'])
            matrix=book['Serial matrix']
            self.assertEqual(matrix.max_row,8)
            self.assertEqual(matrix['A5'].value,'001_FINAL')
            self.assertEqual(matrix['B5'].data_type,'s')
            self.assertEqual(matrix['E7'].value,0)
            self.assertIsNone(matrix['E8'].value)
            self.assertEqual(matrix.freeze_panes,'E5')
            stats=book['Characteristic summary']
            self.assertEqual(stats['H2'].value,3)
            self.assertEqual(stats['I2'].value,1)
            self.assertAlmostEqual(stats['K2'].value,(11.4+11.6)/3)
            self.assertEqual(book['Measurements']['I2'].value,-99)
            self.assertEqual(book['Measurements']['P2'].data_type,'s')
            self.assertIn('Duplicate serial',book['Source reports']['K2'].value)
            self.assertEqual(book['Source reports'].max_row,7)
            self.assertEqual(book['Measurements'].max_row,5)

    def test_large_batch_has_no_fixed_statistics_range(self):
        reports=[self.report(str(i),str(i).zfill(4),11.4+i/10000) for i in range(1000)]
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'out.xlsx';worker.export(dict(schema=[definition()],reports=reports),file)
            book=openpyxl.load_workbook(file,read_only=True,data_only=True)
            self.assertEqual(book['Serial matrix'].max_row,1004)
            self.assertEqual(book['Characteristic summary']['H2'].value,1000)
            self.assertAlmostEqual(book['Characteristic summary']['K2'].value,11.44995)

    def test_reference_reads_definitions_without_serial_results(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'ref.xlsx';book=openpyxl.Workbook();sheet=book.active
            for row in [['PCD-ID','JEM_13_14.CYL_B1.D.SIZE'],['Dimension ID','JEM_13_14'],['Axis','D'],['Nominal',11.5],['U Tol',.1],['L Tol',-.1],['001_FINAL',999]]:sheet.append(row)
            book.save(file);schema=worker.reference(file)
            self.assertEqual(len(schema),1)
            self.assertEqual(schema[0]['nominal'],11.5)
            self.assertNotIn('value',schema[0])

    def test_invalid_limits_rejected(self):
        d=definition();d['lowerTol']=.1
        with self.assertRaises(ValueError):worker.validate_schema([d])
        with self.assertRaises(ValueError):worker.validate_schema([definition(),definition()])


if __name__=='__main__':unittest.main()
