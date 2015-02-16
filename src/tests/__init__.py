import unittest

import datetime
import os
import unittest
import img2pdf
import zlib
from PIL import Image

HERE = os.path.dirname(__file__)

#convert +set date:create +set date:modify -define png:exclude-chunk=time

def test_suite():
    class TestImg2Pdf(unittest.TestCase):
        pass

    for test_name in os.listdir(os.path.join(HERE, "input")):
        inputf = os.path.join(HERE, "input", test_name)
        if not os.path.isfile(inputf):
            continue
        outputf = os.path.join(HERE, "output", test_name+".pdf")
        assert os.path.isfile(outputf)
        def handle(self, f=inputf, out=outputf):
            with open(f, "rb") as inf:
                orig_imgdata = inf.read()
            pdf = img2pdf.convert([f], nodate=True)
            imgdata = b""
            instream = False
            imgobj = False
            colorspace = None
            imgfilter = None
            width = None
            height = None
            length = None
            # ugly workaround to parse the created pdf
            for line in pdf.split(b'\n'):
                if instream:
                    if line == b"endstream":
                        break
                    else:
                        imgdata += line + b'\n'
                else:
                    if imgobj and line == b"stream":
                        instream = True
                    elif b"/Subtype /Image" in line:
                        imgobj = True
                    elif b"/Width" in line:
                        width = int(line.split()[-1])
                    elif b"/Height" in line:
                        height = int(line.split()[-1])
                    elif b"/Length" in line:
                        length = int(line.split()[-1])
                    elif b"/Filter" in line:
                        imgfilter = line.split()[-2]
                    elif b"/ColorSpace" in line:
                        colorspace = line.split()[-1]
            # remove trailing \n
            imgdata = imgdata[:-1]
            # test if the length field is correct
            self.assertEqual(len(imgdata), length)
            # test if the filter is valid:
            self.assertIn(imgfilter, [b"/DCTDecode", b"/JPXDecode", b"/FlateDecode"])
            # test if the colorspace is valid
            self.assertIn(colorspace, [b"/DeviceGray", b"/DeviceRGB", b"/DeviceCMYK"])
            # test if the image has correct size
            orig_img = Image.open(f)
            self.assertEqual(width, orig_img.size[0])
            self.assertEqual(height, orig_img.size[1])
            # if the input file is a jpeg then it should've been copied
            # verbatim into the PDF
            if imgfilter in [b"/DCTDecode", b"/JPXDecode"]:
                self.assertEqual(imgdata, orig_imgdata)
            elif imgfilter == b"/FlateDecode":
                # otherwise, the data is flate encoded and has to be equal to
                # the pixel data of the input image
                imgdata = zlib.decompress(imgdata)
                if colorspace == b"/DeviceGray":
                    colorspace = 'L'
                elif colorspace == b"/DeviceRGB":
                    colorspace = 'RGB'
                elif colorspace == b"/DeviceCMYK":
                    colorspace = 'CMYK'
                else:
                    raise Exception("invalid colorspace")
                im = Image.frombytes(colorspace, (width, height), imgdata)
                if orig_img.mode == '1':
                    orig_img = orig_img.convert("L")
                elif orig_img.mode not in ("RGB", "L", "CMYK", "CMYK;I"):
                    orig_img = orig_img.convert("RGB")
                self.assertEqual(im.tobytes(), orig_img.tobytes())
                im.close()
            # lastly, make sure that the generated pdf matches bit by bit the
            # expected pdf
            with open(out, "rb") as outf:
                out = outf.read()
            self.assertEqual(pdf, out)
            orig_img.close()
        setattr(TestImg2Pdf, "test_%s"%test_name, handle)

    return unittest.TestSuite((
            unittest.makeSuite(TestImg2Pdf),
            ))
