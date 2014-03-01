import datetime
import os
import unittest
import img2pdf

HERE = os.path.dirname(__file__)
moddate = datetime.datetime(2014, 1, 1)

class TestImg2Pdf(unittest.TestCase):
    def test_jpg2pdf(self):
        with open(os.path.join(HERE, 'test.jpg'), 'r') as img_fp:
            with open(os.path.join(HERE, 'test.pdf'), 'r') as pdf_fp:
                self.assertEqual(
                    img2pdf.convert([img_fp], 150,
                                    creationdate=moddate, moddate=moddate),
                    pdf_fp.read())

    def test_png2pdf(self):
        with open(os.path.join(HERE, 'test.png'), 'r') as img_fp:
            self.assertRaises(SystemExit, img2pdf.convert, [img_fp], 150)
