import unittest
import test_img2pdf

def test_suite():
    return unittest.TestSuite((
            unittest.makeSuite(test_img2pdf.TestImg2Pdf),
            ))
