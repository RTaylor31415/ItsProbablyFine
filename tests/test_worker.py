import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
import openpyxl

spec = importlib.util.spec_from_file_location('worker', Path(__file__).resolve().parents[1] / 'app/worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class ExtractionTests(unittest.TestCase):
    def test_explicit_tolerances(self):
        rows = worker.extract_candidates('JEMCO 26: R0.89±0.1\nDiameter 11.5 +0.1/-0.2\n25.4 REF', 6)
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]['lower'], rows[0]['upper']), (.79, .99))
        self.assertEqual((rows[1]['lower'], rows[1]['upper']), (11.3, 11.6))
        self.assertFalse(rows[0]['reviewed'])
        self.assertEqual(rows[0]['resultMin'], '')

    def test_size_and_position_separation(self):
        text = '''JEM_13_14 Size | MM
Feature NOMINAL +TOL -TOL MEAS DEV OUTTOL
CYL_B1 11.5000 0.1000 0.1000 11.4978 -0.0022 0.0000
JEM_13_14 | MM
Feature AX NOMINAL +TOL -TOL MEAS DEV OUTTOL BONUS
CYL_B2 0.0000 0.1000 0.0000 0.0090 0.0090 0.0000'''
        rows = worker.extract_candidates(text, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['lower'], rows[0]['upper']), (11.4, 11.6))
        self.assertEqual(rows[0]['resultMin'], '')


class ExportTests(unittest.TestCase):
    def payload(self):
        row = worker.candidate(1, '=HYPERLINK("http://example.com")', 'Diameter 11.5 ±0.1', 11.4, 11.6, '13')
        row.update(reviewed=True, fileName='report.pdf')
        return dict(rows=[row], metadata={'partNumber':'=1+1'}, coverageConfirmed=True)

    def test_review_gates_and_limits(self):
        payload = self.payload()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output.xlsx'
            payload['rows'][0]['reviewed'] = False
            with self.assertRaises(ValueError): worker.export(payload, output)
            payload['rows'][0]['reviewed'] = True
            payload['rows'][0]['lower'] = 99
            with self.assertRaises(ValueError): worker.export(payload, output)
            payload['rows'][0]['lower'] = 'NaN'
            with self.assertRaises(ValueError): worker.export(payload, output)
            payload['coverageConfirmed'] = False
            with self.assertRaises(ValueError): worker.export(payload, output)

    def test_template_overflow_and_literal_text(self):
        payload = self.payload()
        payload['rows'] = [copy.deepcopy(payload['rows'][0]) for _ in range(100)]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output.xlsx'
            worker.export(payload, output)
            book = openpyxl.load_workbook(output)
            self.assertFalse(book.template)
            self.assertEqual(book.sheetnames, ['Form1','Form2','Form3','CofC','Form3_2','Extraction review'])
            self.assertEqual(book['Form1']['A3'].data_type, 's')
            self.assertEqual(book['Form1']['A3'].value, '=1+1')
            self.assertEqual(book['Extraction review']['E3'].data_type, 's')
            self.assertEqual(book['Form3']['G8'].value, 11.4)
            self.assertIsNone(book['Form3']['I8'].value)
            self.assertEqual(book['Form3_2']['F8'].value, 'Diameter 11.5 ±0.1')
            self.assertIn('B3:E3', str(book['Form3'].merged_cells))
            self.assertIsNone(book['Form1']['B38'].value)
            self.assertIsNone(book['Form2']['F40'].value)
            self.assertIn('DRAFT', book['CofC']['A1'].value)
            for sheet in book:
                for row in sheet:
                    for cell in row:
                        self.assertFalse(isinstance(cell.value,str) and cell.value.startswith('iex:'))


if __name__ == '__main__': unittest.main()
