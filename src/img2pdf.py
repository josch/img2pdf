#!/usr/bin/env python3

# Copyright (C) 2012-2014 Johannes 'josch' Schauer <j.schauer at email.de>
#
# This program is free software: you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with this program.  If not, see
# <http://www.gnu.org/licenses/>.

import sys
import zlib
import argparse
from PIL import Image
from datetime import datetime
from jp2 import parsejp2
from enum import Enum
from io import BytesIO
import logging

__version__ = "0.2"
default_dpi = 96.0
papersizes = {
    "letter": "8.5inx11in",
    "a0":     "841mmx1189mm",
    "a1":     "594mmx841mm",
    "a2":     "420mmx594mm",
    "a3":     "297mmx420mm",
    "a4":     "210mmx297mm",
    "a5":     "148mmx210mm",
    "a6":     "105mmx148mm",
}
papernames = {
    "letter": "Letter",
    "a0":     "A0",
    "a1":     "A1",
    "a2":     "A2",
    "a3":     "A3",
    "a4":     "A4",
    "a5":     "A5",
    "a6":     "A6",
}


FitMode = Enum('FitMode', 'into fill exact shrink enlarge')

PageOrientation = Enum('PageOrientation', 'portrait landscape')

Colorspace = Enum('Colorspace', 'RGB L 1 CMYK CMYK;I RGBA')

ImageFormat = Enum('ImageFormat', 'JPEG JPEG2000 TIFF PNG GIF')

PageMode = Enum('PageMode', 'none outlines thumbs')

PageLayout = Enum('PageLayout',
                  'single onecolumn twocolumnright twocolumnleft')

Magnification = Enum('Magnification', 'fit fith fitbh')

ImgSize = Enum('ImgSize', 'abs perc dpi')

Unit = Enum('Unit', 'pt cm mm inch')

ImgUnit = Enum('ImgUnit', 'pt cm mm inch perc dpi')


class NegativeDimensionException(Exception):
    pass


# without pdfrw this function is a no-op
def my_convert_load(string):
    return string


def parse(cont, indent=1):
    if type(cont) is dict:
        return b"<<\n"+b"\n".join(
            [4 * indent * b" " + k + b" " + parse(v, indent+1)
             for k, v in sorted(cont.items())])+b"\n"+4*(indent-1)*b" "+b">>"
    elif type(cont) is int:
        return str(cont).encode()
    elif type(cont) is float:
        if int(cont) == cont:
            return parse(int(cont))
        else:
            return ("%0.4f" % cont).rstrip("0").encode()
    elif isinstance(cont, MyPdfDict):
        # if cont got an identifier, then adobj() has been called with it
        # and a link to it will be added, otherwise add it inline
        if hasattr(cont, "identifier"):
            return ("%d 0 R" % cont.identifier).encode()
        else:
            return parse(cont.content, indent)
    elif type(cont) is str or isinstance(cont, bytes):
        if type(cont) is str and type(cont) is not bytes:
            raise Exception(
                "parse must be passed a bytes object in py3. Got: %s" % cont)
        return cont
    elif isinstance(cont, list):
        return b"[ "+b" ".join([parse(c, indent) for c in cont])+b" ]"
    else:
        raise Exception("cannot handle type %s with content %s" % (type(cont),
                                                                   cont))


class MyPdfDict(object):
    def __init__(self, *args, **kw):
        self.content = dict()
        if args:
            if len(args) == 1:
                args = args[0]
            self.content.update(args)
        self.stream = None
        for key, value in kw.items():
            if key == "stream":
                self.stream = value
                self.content[MyPdfName.Length] = len(value)
            elif key == "indirect":
                pass
            else:
                self.content[getattr(MyPdfName, key)] = value

    def tostring(self):
        if self.stream is not None:
            return (
                ("%d 0 obj\n" % self.identifier).encode() +
                parse(self.content) +
                b"\nstream\n" + self.stream + b"\nendstream\nendobj\n")
        else:
            return ("%d 0 obj\n" % self.identifier).encode() + \
                   parse(self.content) + b"\nendobj\n"

    def __setitem__(self, key, value):
        self.content[key] = value

    def __getitem__(self, key):
        return self.content[key]


class MyPdfName():
    def __getattr__(self, name):
        return b'/' + name.encode('ascii')
MyPdfName = MyPdfName()


class MyPdfObject(bytes):
    def __new__(cls, string):
        return bytes.__new__(cls, string.encode('ascii'))


class MyPdfArray(list):
    pass


class MyPdfWriter():
    def __init__(self, version="1.3"):
        self.objects = []
        # create an incomplete pages object so that a /Parent entry can be
        # added to each page
        self.pages = MyPdfDict(Type=MyPdfName.Pages, Kids=[], Count=0)
        self.catalog = MyPdfDict(Pages=self.pages, Type=MyPdfName.Catalog)
        self.version = version  # default pdf version 1.3
        self.pagearray = []

    def addobj(self, obj):
        newid = len(self.objects)+1
        obj.identifier = newid
        self.objects.append(obj)

    def getstring(self, info):
        xreftable = list()

        # justification of the random binary garbage in the header from
        # adobe:
        #
        #  > Note: If a PDF file contains binary data, as most do (see Section
        #  > 3.1, “Lexical Conventions”), it is recommended that the header
        #  > line be immediately followed by a comment line containing at
        #  > least four binary characters—that is, characters whose codes are
        #  > 128 or greater. This ensures proper behavior of file transfer
        #  > applications that inspect data near the beginning of a file to
        #  > determine whether to treat the file’s contents as text or as
        #  > binary.
        #
        # the choice of binary characters is arbitrary but those four seem to
        # be used elsewhere.
        result = ('%%PDF-%s\n' % self.version).encode('ascii')
        result += b'%\xe2\xe3\xcf\xd3\n'

        # From section 3.4.3 of the PDF Reference (version 1.7):
        #
        #  > Each entry is exactly 20 bytes long, including the end-of-line
        #  > marker.
        #  >
        #  > [...]
        #  >
        #  > The format of an in-use entry is
        #  > nnnnnnnnnn ggggg n eol
        #  > where
        #  > nnnnnnnnnn is a 10-digit byte offset
        #  > ggggg is a 5-digit generation number
        #  > n is a literal keyword identifying this as an in-use entry
        #  > eol is a 2-character end-of-line sequence
        #  >
        #  > [...]
        #  >
        #  > If the file’s end-of-line marker is a single character (either a
        #  > carriage return or a line feed), it is preceded by a single space;
        #
        # Since we chose to use a single character eol marker, we preceed it by
        # a space
        xreftable.append(b"0000000000 65535 f \n")
        for o in self.objects:
            xreftable.append(("%010d 00000 n \n" % len(result)).encode())
            result += o.tostring()

        xrefoffset = len(result)
        result += b"xref\n"
        result += ("0 %d\n" % len(xreftable)).encode()
        for x in xreftable:
            result += x
        result += b"trailer\n"
        result += parse({b"/Size": len(xreftable), b"/Info": info,
                         b"/Root": self.catalog})+b"\n"
        result += b"startxref\n"
        result += ("%d\n" % xrefoffset).encode()
        result += b"%%EOF\n"
        return result

    def addpage(self, page):
        page[b"/Parent"] = self.pages
        self.pagearray.append(page)
        self.pages.content[b"/Kids"].append(page)
        self.pages.content[b"/Count"] += 1
        self.addobj(page)


class MyPdfString():
    @classmethod
    def encode(cls, string):
        try:
            string = string.encode('ascii')
        except UnicodeEncodeError:
            string = b"\xfe\xff"+string.encode("utf-16-be")
        string = string.replace(b'\\', b'\\\\')
        string = string.replace(b'(', b'\\(')
        string = string.replace(b')', b'\\)')
        return b'(' + string + b')'


class pdfdoc(object):
    def __init__(self, version="1.3", title=None, author=None, creator=None,
                 producer=None, creationdate=None, moddate=None, subject=None,
                 keywords=None, nodate=False, panes=None, initial_page=None,
                 magnification=None, page_layout=None, fit_window=False,
                 center_window=False, fullscreen=False, with_pdfrw=True):
        if with_pdfrw:
            try:
                from pdfrw import PdfWriter, PdfDict, PdfName, PdfString
                self.with_pdfrw = True
            except ImportError:
                PdfWriter = MyPdfWriter
                PdfDict = MyPdfDict
                PdfName = MyPdfName
                PdfString = MyPdfString
                self.with_pdfrw = False
        else:
            PdfWriter = MyPdfWriter
            PdfDict = MyPdfDict
            PdfName = MyPdfName
            PdfString = MyPdfString
            self.with_pdfrw = False

        now = datetime.now()
        self.info = PdfDict(indirect=True)

        def datetime_to_pdfdate(dt):
            return dt.strftime("%Y%m%d%H%M%SZ")

        if title is not None:
            self.info[PdfName.Title] = PdfString.encode(title)
        if author is not None:
            self.info[PdfName.Author] = PdfString.encode(author)
        if creator is not None:
            self.info[PdfName.Creator] = PdfString.encode(creator)
        if producer is not None:
            self.info[PdfName.Producer] = PdfString.encode(producer)
        if creationdate is not None:
            self.info[PdfName.CreationDate] = \
                PdfString.encode("D:"+datetime_to_pdfdate(creationdate))
        elif not nodate:
            self.info[PdfName.CreationDate] = \
                PdfString.encode("D:"+datetime_to_pdfdate(now))
        if moddate is not None:
            self.info[PdfName.ModDate] = \
                PdfString.encode("D:"+datetime_to_pdfdate(moddate))
        elif not nodate:
            self.info[PdfName.ModDate] = PdfString.encode(
                    "D:"+datetime_to_pdfdate(now))
        if subject is not None:
            self.info[PdfName.Subject] = PdfString.encode(subject)
        if keywords is not None:
            self.info[PdfName.Keywords] = PdfString.encode(",".join(keywords))

        self.writer = PdfWriter()
        self.writer.version = version
        # this is done because pdfrw adds info, catalog and pages as the first
        # three objects in this order
        if not self.with_pdfrw:
            self.writer.addobj(self.info)
            self.writer.addobj(self.writer.catalog)
            self.writer.addobj(self.writer.pages)

        self.panes = panes
        self.initial_page = initial_page
        self.magnification = magnification
        self.page_layout = page_layout
        self.fit_window = fit_window
        self.center_window = center_window
        self.fullscreen = fullscreen

    def add_imagepage(self, color, imgwidthpx, imgheightpx, imgformat, imgdata,
                      imgwidthpdf, imgheightpdf, imgxpdf, imgypdf, pagewidth,
                      pageheight):
        if self.with_pdfrw:
            from pdfrw import PdfDict, PdfName
            from pdfrw.py23_diffs import convert_load
        else:
            PdfDict = MyPdfDict
            PdfName = MyPdfName
            convert_load = my_convert_load

        if color == Colorspace.L:
            colorspace = PdfName.DeviceGray
        elif color == Colorspace.RGB:
            colorspace = PdfName.DeviceRGB
        elif color == Colorspace.CMYK or color == Colorspace['CMYK;I']:
            colorspace = PdfName.DeviceCMYK
        else:
            logging.error("unsupported color space: %s", color.name)
            exit(1)

        # either embed the whole jpeg or deflate the bitmap representation
        if imgformat is ImageFormat.JPEG:
            ofilter = [PdfName.DCTDecode]
        elif imgformat is ImageFormat.JPEG2000:
            ofilter = [PdfName.JPXDecode]
            self.writer.version = "1.5"  # jpeg2000 needs pdf 1.5
        else:
            ofilter = [PdfName.FlateDecode]

        image = PdfDict(stream=convert_load(imgdata))

        image[PdfName.Type] = PdfName.XObject
        image[PdfName.Subtype] = PdfName.Image
        image[PdfName.Filter] = ofilter
        image[PdfName.Width] = imgwidthpx
        image[PdfName.Height] = imgheightpx
        image[PdfName.ColorSpace] = colorspace
        # hardcoded as PIL doesnt provide bits for non-jpeg formats
        image[PdfName.BitsPerComponent] = 8

        if color == Colorspace['CMYK;I']:
            # Inverts all four channels
            image[PdfName.Decode] = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0]

        text = ("q\n%0.4f 0 0 %0.4f %0.4f %0.4f cm\n/Im0 Do\nQ" %
                (imgwidthpdf, imgheightpdf, imgxpdf, imgypdf)).encode("ascii")

        content = PdfDict(stream=convert_load(text))
        resources = PdfDict(XObject=PdfDict(Im0=image))

        page = PdfDict(indirect=True)
        page[PdfName.Type] = PdfName.Page
        page[PdfName.MediaBox] = [0, 0, pagewidth, pageheight]
        page[PdfName.Resources] = resources
        page[PdfName.Contents] = content

        self.writer.addpage(page)

        if not self.with_pdfrw:
            self.writer.addobj(content)
            self.writer.addobj(image)

    def tostring(self):
        if self.with_pdfrw:
            from pdfrw import PdfDict, PdfName, PdfArray, PdfObject
        else:
            PdfDict = MyPdfDict
            PdfName = MyPdfName
            PdfObject = MyPdfObject
            PdfArray = MyPdfArray
        NullObject = PdfObject('null')
        TrueObject = PdfObject('true')

        # We fill the catalog with more information like /ViewerPreferences,
        # /PageMode, /PageLayout or /OpenAction because the latter refers to a
        # page object which has to be present so that we can get its id.
        #
        # Furthermore, if using pdfrw, the trailer is cleared every time a page
        # is added, so we can only start using it after all pages have been
        # written.

        if self.with_pdfrw:
            catalog = self.writer.trailer.Root
        else:
            catalog = self.writer.catalog

        if self.fullscreen or self.fit_window or self.center_window or \
                self.panes is not None:
            catalog[PdfName.ViewerPreferences] = PdfDict()

        if self.fullscreen:
            # this setting might be overwritten later by the page mode
            catalog[PdfName.ViewerPreferences][PdfName.NonFullScreenPageMode] \
                    = PdfName.UseNone

        if self.panes == PageMode.thumbs:
            catalog[PdfName.ViewerPreferences][PdfName.NonFullScreenPageMode] \
                    = PdfName.UseThumbs
            # this setting might be overwritten later if fullscreen
            catalog[PdfName.PageMode] = PdfName.UseThumbs
        elif self.panes == PageMode.outlines:
            catalog[PdfName.ViewerPreferences][PdfName.NonFullScreenPageMode] \
                    = PdfName.UseOutlines
            # this setting might be overwritten later if fullscreen
            catalog[PdfName.PageMode] = PdfName.UseOutlines
        elif self.panes in [PageMode.none, None]:
            pass
        else:
            raise Exception("unknown page mode: %s" % self.panes)

        if self.fit_window:
            catalog[PdfName.ViewerPreferences][PdfName.FitWindow] = TrueObject

        if self.center_window:
            catalog[PdfName.ViewerPreferences][PdfName.CenterWindow] = \
                    TrueObject

        if self.fullscreen:
            catalog[PdfName.PageMode] = PdfName.FullScreen

        # see table 8.2 in section 8.2.1 in
        # http://partners.adobe.com/public/developer/en/pdf/PDFReference16.pdf
        # Fit - Fits the page to the window.
        # FitH - Fits the width of the page to the window.
        # FitV - Fits the height of the page to the window.
        # FitR - Fits the rectangle specified by the four coordinates to the
        #        window.
        # FitB - Fits the page bounding box to the window. This basically
        #        reduces the amount of whitespace (margins) that is displayed
        #        and thus focussing more on the text content.
        # FitBH - Fits the width of the page bounding box to the window.
        # FitBV - Fits the height of the page bounding box to the window.

        # by default the initial page is the first one
        initial_page = self.writer.pagearray[0]
        # we set the open action here to make sure we open on the requested
        # initial page but this value might be overwritten by a custom open
        # action later while still taking the requested inital page into
        # account
        if self.initial_page is not None:
            initial_page = self.writer.pagearray[self.initial_page - 1]
            catalog[PdfName.OpenAction] = PdfArray([initial_page, PdfName.XYZ,
                                                    NullObject, NullObject, 0])

        if self.magnification == Magnification.fit:
            catalog[PdfName.OpenAction] = PdfArray([initial_page, PdfName.Fit])
        elif self.magnification == Magnification.fith:
            pagewidth = initial_page[PdfName.MediaBox][2]
            catalog[PdfName.OpenAction] = PdfArray(
                [initial_page, PdfName.FitH, pagewidth])
        elif self.magnification == Magnification.fitbh:
            # quick hack to determine the image width on the page
            imgwidth = float(initial_page[PdfName.Contents].stream.split()[4])
            catalog[PdfName.OpenAction] = PdfArray(
                [initial_page, PdfName.FitBH, imgwidth])
        elif isinstance(self.magnification, float):
            catalog[PdfName.OpenAction] = PdfArray(
                [initial_page, PdfName.XYZ, NullObject, NullObject,
                 self.magnification])
        elif self.magnification is None:
            pass
        else:
            raise Exception("unknown magnification: %s" % self.magnification)

        if self.page_layout == PageLayout.single:
            catalog[PdfName.PageLayout] = PdfName.SinglePage
        elif self.page_layout == PageLayout.onecolumn:
            catalog[PdfName.PageLayout] = PdfName.OneColumn
        elif self.page_layout == PageLayout.twocolumnright:
            catalog[PdfName.PageLayout] = PdfName.TwoColumnRight
        elif self.page_layout == PageLayout.twocolumnleft:
            catalog[PdfName.PageLayout] = PdfName.TwoColumnLeft
        elif self.page_layout is None:
            pass
        else:
            raise Exception("unknown page layout: %s" % self.page_layout)

        # now write out the PDF
        if self.with_pdfrw:
            outf = BytesIO()
            self.writer.trailer.Info = self.info
            self.writer.write(outf)
            return outf.getvalue()
        else:
            return self.writer.getstring(self.info)


def read_image(rawdata, colorspace):
    im = BytesIO(rawdata)
    im.seek(0)
    try:
        imgdata = Image.open(im)
    except IOError as e:
        # test if it is a jpeg2000 image
        if rawdata[:12] != "\x00\x00\x00\x0C\x6A\x50\x20\x20\x0D\x0A\x87\x0A":
            logging.error("cannot read input image (not jpeg2000)")
            logging.error("PIL: error reading image: %s", e)
            exit(1)
        # image is jpeg2000
        imgwidthpx, imgheightpx, ics = parsejp2(rawdata)
        imgformat = ImageFormat.JPEG2000

        # TODO: read real dpi from input jpeg2000 image
        ndpi = (default_dpi, default_dpi)
        logging.debug("input dpi = %d x %d", *ndpi)

        if colorspace:
            color = colorspace
            logging.debug("input colorspace (forced) = %s", ics)
        else:
            color = ics
            logging.debug("input colorspace = %s", ics)
    else:
        imgwidthpx, imgheightpx = imgdata.size
        imgformat = None
        for f in ImageFormat:
            if f.name == imgdata.format:
                imgformat = f
        if imgformat is None:
            raise Exception("unknown PIL image format: %s" % imgdata.format)

        ndpi = imgdata.info.get("dpi", (default_dpi, default_dpi))
        # In python3, the returned dpi value for some tiff images will
        # not be an integer but a float. To make the behaviour of
        # img2pdf the same between python2 and python3, we convert that
        # float into an integer by rounding.
        # Search online for the 72.009 dpi problem for more info.
        ndpi = (int(round(ndpi[0])), int(round(ndpi[1])))
        logging.debug("input dpi = %d x %d", *ndpi)

        if colorspace:
            color = colorspace
            logging.debug("input colorspace (forced) = %s", color)
        else:
            color = None
            for c in Colorspace:
                if c.name == imgdata.mode:
                    color = c
            if color is None:
                raise Exception("unknown PIL colorspace: %s" % imgdata.mode)
            if color == Colorspace.CMYK and imgformat == ImageFormat.JPEG:
                # Adobe inverts CMYK JPEGs for some reason, and others
                # have followed suit as well. Some software assumes the
                # JPEG is inverted if the Adobe tag (APP14), while other
                # software assumes all CMYK JPEGs are inverted. I don't
                # have enough experience with these to know which is
                # better for images currently in the wild, so I'm going
                # with the first approach for now.
                if "adobe" in imgdata.info:
                    color = Colorspace['CMYK;I']
            logging.debug("input colorspace = %s", color.name)

    logging.debug("width x height = %dpx x %dpx", imgwidthpx, imgheightpx)
    logging.debug("imgformat = %s", imgformat.name)

    # depending on the input format, determine whether to pass the raw
    # image or the zlib compressed color information
    if imgformat == ImageFormat.JPEG or imgformat == ImageFormat.JPEG2000:
        if color == Colorspace['1']:
            logging.error("jpeg can't be monochrome")
            exit(1)
        imgdata = rawdata
    else:
        # because we do not support /CCITTFaxDecode
        if color == Colorspace['1']:
            logging.debug("Converting colorspace 1 to L")
            imgdata = imgdata.convert('L')
            color = Colorspace.L
        elif color in [Colorspace.RGB, Colorspace.L, Colorspace.CMYK,
                       Colorspace["CMYK;I"]]:
            logging.debug("Colorspace is OK: %s", color)
        elif color in [Colorspace.RGBA]:
            logging.debug("Converting colorspace %s to RGB", color)
            imgdata = imgdata.convert('RGB')
            color = Colorspace.RGB
        else:
            raise Exception("unknown colorspace: %s" % color.name)
        img = imgdata.tobytes()
        # the python-pil version 2.3.0-1ubuntu3 in Ubuntu does not have the
        # close() method
        try:
            imgdata.close()
        except AttributeError:
            pass
        imgdata = zlib.compress(img)
    im.close()

    return color, ndpi, imgformat, imgdata, imgwidthpx, imgheightpx


# converts a length in pixels to a length in PDF units (1/72 of an inch)
def px_to_pt(length, dpi):
    return 72*length/dpi


def cm_to_pt(length):
    return (72*length)/2.54


def mm_to_pt(length):
    return (72*length)/25.4


def in_to_pt(length):
    return 72*length


def get_layout_fun(pagesize, imgsize, border, fit, auto_orient):
    def fitfun(fit, imgwidth, imgheight, fitwidth, fitheight):
        if fitwidth is None and fitheight is None:
            raise Exception("fitwidth and fitheight cannot both be None")
        # if fit is fill or enlarge then it is okay if one of the dimensions
        # are negative but one of them must still be positive
        # if fit is not fill or enlarge then both dimensions must be positive
        if fit in [FitMode.fill, FitMode.enlarge] and \
                (fitwidth < 0 and fitheight < 0):
            raise Exception("cannot fit into a rectangle where both "
                            "dimensions are negative")
        elif fit not in [FitMode.fill, FitMode.enlarge] and \
                (fitwidth < 0 or fitheight < 0):
            raise Exception("cannot fit into a rectangle where either "
                            "dimensions are negative")

        def default():
            if fitwidth is not None and fitheight is not None:
                newimgwidth = fitwidth
                newimgheight = (newimgwidth * imgheight)/imgwidth
                if newimgheight > fitheight:
                    newimgheight = fitheight
                    newimgwidth = (newimgheight * imgwidth)/imgheight
            elif fitwidth is None and fitheight is not None:
                newimgheight = fitheight
                newimgwidth = (newimgheight * imgwidth)/imgheight
            elif fitheight is None and fitwidth is not None:
                newimgwidth = fitwidth
                newimgheight = (newimgwidth * imgheight)/imgwidth
            else:
                raise Exception("fitwidth and fitheight cannot both be None")
            return newimgwidth, newimgheight
        if fit is None or fit == FitMode.into:
            return default()
        elif fit == FitMode.fill:
            if fitwidth is not None and fitheight is not None:
                newimgwidth = fitwidth
                newimgheight = (newimgwidth * imgheight)/imgwidth
                if newimgheight < fitheight:
                    newimgheight = fitheight
                    newimgwidth = (newimgheight * imgwidth)/imgheight
            elif fitwidth is None and fitheight is not None:
                newimgheight = fitheight
                newimgwidth = (newimgheight * imgwidth)/imgheight
            elif fitheight is None and fitwidth is not None:
                newimgwidth = fitwidth
                newimgheight = (newimgwidth * imgheight)/imgwidth
            else:
                raise Exception("fitwidth and fitheight cannot both be None")
            return newimgwidth, newimgheight
        elif fit == FitMode.exact:
            if fitwidth is not None and fitheight is not None:
                return fitwidth, fitheight
            elif fitwidth is None and fitheight is not None:
                newimgheight = fitheight
                newimgwidth = (newimgheight * imgwidth)/imgheight
            elif fitheight is None and fitwidth is not None:
                newimgwidth = fitwidth
                newimgheight = (newimgwidth * imgheight)/imgwidth
            else:
                raise Exception("fitwidth and fitheight cannot both be None")
        elif fit == FitMode.shrink:
            if fitwidth is not None and fitheight is not None:
                if imgwidth <= fitwidth and imgheight <= fitheight:
                    return imgwidth, imgheight
            elif fitwidth is None and fitheight is not None:
                if imgheight <= fitheight:
                    return imgwidth, imgheight
            elif fitheight is None and fitwidth is not None:
                if imgwidth <= fitwidth:
                    return imgwidth, imgheight
            else:
                raise Exception("fitwidth and fitheight cannot both be None")
            return default()
        elif fit == FitMode.enlarge:
            if fitwidth is not None and fitheight is not None:
                if imgwidth > fitwidth or imgheight > fitheight:
                    return imgwidth, imgheight
            elif fitwidth is None and fitheight is not None:
                if imgheight > fitheight:
                    return imgwidth, imgheight
            elif fitheight is None and fitwidth is not None:
                if imgwidth > fitwidth:
                    return imgwidth, imgheight
            else:
                raise Exception("fitwidth and fitheight cannot both be None")
            return default()
        else:
            raise NotImplementedError
    # if no layout arguments are given, then the image size is equal to the
    # page size and will be drawn with the default dpi
    if pagesize is None and imgsize is None and border is None:
        return default_layout_fun
    if pagesize is None and imgsize is None and border is not None:
        def layout_fun(imgwidthpx, imgheightpx, ndpi):
            imgwidthpdf = px_to_pt(imgwidthpx, ndpi[0])
            imgheightpdf = px_to_pt(imgheightpx, ndpi[1])
            pagewidth = imgwidthpdf+2*border[1]
            pageheight = imgheightpdf+2*border[0]
            return pagewidth, pageheight, imgwidthpdf, imgheightpdf
        return layout_fun
    if border is None:
        border = (0, 0)
    # if the pagesize is given but the imagesize is not, then the imagesize
    # will be calculated from the pagesize, taking into account the border
    # and the fitting
    if pagesize is not None and imgsize is None:
        def layout_fun(imgwidthpx, imgheightpx, ndpi):
            if pagesize[0] is not None and pagesize[1] is not None and \
                    auto_orient and \
                    ((imgwidthpx > imgheightpx and
                     pagesize[0] < pagesize[1]) or
                     (imgwidthpx < imgheightpx and pagesize[0] > pagesize[1])):
                pagewidth, pageheight = pagesize[1], pagesize[0]
                newborder = border[1], border[0]
            else:
                pagewidth, pageheight = pagesize[0], pagesize[1]
                newborder = border
            if pagewidth is not None:
                fitwidth = pagewidth-2*newborder[0]
            else:
                fitwidth = None
            if pageheight is not None:
                fitheight = pageheight-2*newborder[1]
            else:
                fitheight = None
            if fit in [FitMode.fill, FitMode.enlarge] and \
                    fitwidth is not None and fitwidth < 0 and \
                    fitheight is not None and fitheight < 0:
                raise NegativeDimensionException(
                    "at least one border dimension musts be smaller than half "
                    "the respective page dimension")
            elif fit not in [FitMode.fill, FitMode.enlarge] \
                    and ((fitwidth is not None and fitwidth < 0) or
                         (fitheight is not None and fitheight < 0)):
                raise NegativeDimensionException(
                    "one border dimension is larger than half of the "
                    "respective page dimension")
            imgwidthpdf, imgheightpdf = \
                fitfun(fit, px_to_pt(imgwidthpx, ndpi[0]),
                       px_to_pt(imgheightpx, ndpi[1]),
                       fitwidth, fitheight)
            if pagewidth is None:
                pagewidth = imgwidthpdf+border[1]*2
            if pageheight is None:
                pageheight = imgheightpdf+border[0]*2
            return pagewidth, pageheight, imgwidthpdf, imgheightpdf
        return layout_fun

    def scale_imgsize(s, px, dpi):
        if s is None:
            return None
        mode, value = s
        if mode == ImgSize.abs:
            return value
        if mode == ImgSize.perc:
            return (px_to_pt(px, dpi)*value)/100
        if mode == ImgSize.dpi:
            return px_to_pt(px, value)
        raise NotImplementedError
    if pagesize is None and imgsize is not None:
        def layout_fun(imgwidthpx, imgheightpx, ndpi):
            imgwidthpdf, imgheightpdf = \
                    fitfun(fit, px_to_pt(imgwidthpx, ndpi[0]),
                           px_to_pt(imgheightpx, ndpi[1]),
                           scale_imgsize(imgsize[0], imgwidthpx, ndpi[0]),
                           scale_imgsize(imgsize[1], imgheightpx, ndpi[1]))
            pagewidth = imgwidthpdf+2*border[1]
            pageheight = imgheightpdf+2*border[0]
            return pagewidth, pageheight, imgwidthpdf, imgheightpdf
        return layout_fun
    if pagesize is not None and imgsize is not None:
        def layout_fun(imgwidthpx, imgheightpx, ndpi):
            if pagesize[0] is not None and pagesize[1] is not None and \
                    auto_orient and \
                    ((imgwidthpx > imgheightpx and
                      pagesize[0] < pagesize[1]) or
                     (imgwidthpx < imgheightpx and pagesize[0] > pagesize[1])):
                pagewidth, pageheight = pagesize[1], pagesize[0]
            else:
                pagewidth, pageheight = pagesize[0], pagesize[1]
            imgwidthpdf, imgheightpdf = \
                fitfun(fit, px_to_pt(imgwidthpx, ndpi[0]),
                       px_to_pt(imgheightpx, ndpi[1]),
                       scale_imgsize(imgsize[0], imgwidthpx, ndpi[0]),
                       scale_imgsize(imgsize[1], imgheightpx, ndpi[1]))
            return pagewidth, pageheight, imgwidthpdf, imgheightpdf
        return layout_fun
    raise NotImplementedError


def default_layout_fun(imgwidthpx, imgheightpx, ndpi):
    imgwidthpdf = pagewidth = px_to_pt(imgwidthpx, ndpi[0])
    imgheightpdf = pageheight = px_to_pt(imgheightpx, ndpi[1])
    return pagewidth, pageheight, imgwidthpdf, imgheightpdf


def convert(*images, title=None,
            author=None, creator=None, producer=None, creationdate=None,
            moddate=None, subject=None, keywords=None, colorspace=None,
            nodate=False, layout_fun=default_layout_fun, viewer_panes=None,
            viewer_initial_page=None, viewer_magnification=None,
            viewer_page_layout=None, viewer_fit_window=False,
            viewer_center_window=False, viewer_fullscreen=False,
            with_pdfrw=True):

    pdf = pdfdoc("1.3", title, author, creator, producer, creationdate,
                 moddate, subject, keywords, nodate, viewer_panes,
                 viewer_initial_page, viewer_magnification, viewer_page_layout,
                 viewer_fit_window, viewer_center_window, viewer_fullscreen,
                 with_pdfrw)

    for rawdata in images:
        color, ndpi, imgformat, imgdata, imgwidthpx, imgheightpx = \
            read_image(rawdata, colorspace)
        pagewidth, pageheight, imgwidthpdf, imgheightpdf = \
            layout_fun(imgwidthpx, imgheightpx, ndpi)
        if pagewidth < 3.00 or pageheight < 3.00:
            logging.warning("pdf width or height is below 3.00 - too small "
                            "for some viewers!")
        # the image is always centered on the page
        imgxpdf = (pagewidth - imgwidthpdf)/2.0
        imgypdf = (pageheight - imgheightpdf)/2.0
        pdf.add_imagepage(color, imgwidthpx, imgheightpx, imgformat, imgdata,
                          imgwidthpdf, imgheightpdf, imgxpdf, imgypdf,
                          pagewidth, pageheight)

    return pdf.tostring()


def parse_num(num, name):
    if num == '':
        return None
    unit = None
    if num.endswith("pt"):
        unit = Unit.pt
    elif num.endswith("cm"):
        unit = Unit.cm
    elif num.endswith("mm"):
        unit = Unit.mm
    elif num.endswith("in"):
        unit = Unit.inch
    else:
        try:
            num = float(num)
        except ValueError:
            msg = "%s is not a floating point number and doesn't have a " \
                  "valid unit: %s" % (name, num)
            raise argparse.ArgumentTypeError(msg)
    if unit is None:
        unit = Unit.pt
    else:
        num = num[:-2]
        try:
            num = float(num)
        except ValueError:
            msg = "%s is not a floating point number: %s" % (name, num)
            raise argparse.ArgumentTypeError(msg)
    if unit == Unit.cm:
        num = cm_to_pt(num)
    elif unit == Unit.mm:
        num = mm_to_pt(num)
    elif unit == Unit.inch:
        num = in_to_pt(num)
    return num


def parse_imgsize_num(num, name):
    if num == '':
        return None
    unit = None
    if num.endswith("pt"):
        unit = ImgUnit.pt
    elif num.endswith("cm"):
        unit = ImgUnit.cm
    elif num.endswith("mm"):
        unit = ImgUnit.mm
    elif num.endswith("in"):
        unit = ImgUnit.inch
    elif num.endswith("dpi"):
        unit = ImgUnit.dpi
    elif num.endswith("%"):
        unit = ImgUnit.perc
    else:
        try:
            num = float(num)
        except ValueError:
            msg = "%s is not a floating point number and doesn't have a " \
                  "valid unit: %s" % (name, num)
            raise argparse.ArgumentTypeError(msg)
    if unit is None:
        unit = ImgUnit.pt
    else:
        num = num[:-2]
        try:
            num = float(num)
        except ValueError:
            msg = "%s is not a floating point number: %s" % (name, num)
            raise argparse.ArgumentTypeError(msg)
    if unit == ImgUnit.cm:
        num = (ImgSize.abs, cm_to_pt(num))
    elif unit == ImgUnit.mm:
        num = (ImgSize.abs, mm_to_pt(num))
    elif unit == ImgUnit.inch:
        num = (ImgSize.abs, in_to_pt(num))
    elif unit == ImgUnit.pt:
        num = (ImgSize.abs, num)
    elif unit == ImgUnit.dpi:
        num = (ImgSize.dpi, num)
    elif unit == ImgUnit.perc:
        num = (ImgSize.perc, num)
    return num


def parse_pagesize_rectarg(string):
    transposed = string.endswith("^T")
    if transposed:
        string = string[:-2]
    if papersizes.get(string.lower()):
        string = papersizes[string.lower()]
    if 'x' not in string:
        raise argparse.ArgumentTypeError("size must contain 'x' character")
    w, h = string.split('x', 1)
    if transposed:
        w, h = h, w
    w, h = parse_num(w, "width"), parse_num(h, "height")
    if w is None and h is None:
        raise argparse.ArgumentTypeError("at least one dimension must be "
                                         "specified")
    return w, h


def parse_imgsize_rectarg(string):
    transposed = string.endswith("^T")
    if transposed:
        string = string[:-2]
    if papersizes.get(string.lower()):
        string = papersizes[string.lower()]
    if 'x' not in string:
        raise argparse.ArgumentTypeError("size must contain 'x' character")
    w, h = string.split('x', 1)
    if transposed:
        w, h = h, w
    w, h = parse_imgsize_num(w, "width"), parse_imgsize_num(h, "height")
    if w is None and h is None:
        raise argparse.ArgumentTypeError("at least one dimension must be "
                                         "specified")
    return w, h


def parse_colorspacearg(string):
    for c in Colorspace:
        if c.name == string:
            return c
    allowed = ", ".join([c.name for c in Colorspace])
    raise argparse.ArgumentTypeError("Unsupported colorspace: %s. Must be one "
                                     "of: %s." % (string, allowed))


def parse_borderarg(string):
    if ':' in string:
        h, v = string.split(':', 1)
        if h == '':
            raise argparse.ArgumentTypeError("missing value before colon")
        if v == '':
            raise argparse.ArgumentTypeError("missing value after colon")
    else:
        if string == '':
            raise argparse.ArgumentTypeError("border option cannot be empty")
        h, v = string, string
    h, v = parse_num(h, "left/right border"), parse_num(v, "top/bottom border")
    if h is None and v is None:
        raise argparse.ArgumentTypeError("missing value")
    return h, v


def input_images(path):
    if path == '-':
        rawdata = sys.stdin.buffer.read()
    else:
        try:
            with open(path, "rb") as im:
                rawdata = im.read()
        except IsADirectoryError:
            raise argparse.ArgumentTypeError(
                "\"%s\" is a directory" % path)
        except PermissionError:
            raise argparse.ArgumentTypeError(
                "\"%s\" permission denied" % path)
        except FileNotFoundError:
            raise argparse.ArgumentTypeError(
                "\"%s\" does not exist" % path)
    if len(rawdata) == 0:
        raise argparse.ArgumentTypeError("\"%s\" is empty" % path)
    return rawdata


def parse_fitarg(string):
    for m in FitMode:
        if m.name == string.lower():
            return m
    raise argparse.ArgumentTypeError("unknown fit mode: %s" % string)


def parse_panes(string):
    for m in PageMode:
        if m.name == string.lower():
            return m
    allowed = ", ".join([m.name for m in PageMode])
    raise argparse.ArgumentTypeError("Unsupported page mode: %s. Must be one "
                                     "of: %s." % (string, allowed))


def parse_magnification(string):
    for m in Magnification:
        if m.name == string.lower():
            return m
    try:
        return float(string)
    except ValueError:
        pass
    allowed = ", ".join([m.name for m in Magnification])
    raise argparse.ArgumentTypeError("Unsupported magnification: %s. Must be "
                                     "a floating point number or one of: %s." %
                                     (string, allowed))


def parse_layout(string):
    for l in PageLayout:
        if l.name == string.lower():
            return l
    allowed = ", ".join([l.name for l in PageLayout])
    raise argparse.ArgumentTypeError("Unsupported page layout: %s. Must be "
                                     "one of: %s." % (string, allowed))


def valid_date(string):
    # first try parsing in ISO8601 format
    try:
        return datetime.strptime(string, "%Y-%m-%d")
    except ValueError:
        pass
    try:
        return datetime.strptime(string, "%Y-%m-%dT%H:%M")
    except ValueError:
        pass
    try:
        return datetime.strptime(string, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        pass
    # then try dateutil
    try:
        from dateutil import parser
    except ImportError:
        pass
    else:
        try:
            return parser.parse(string)
        except TypeError:
            pass
    # as a last resort, try the local date utility
    try:
        import subprocess
    except ImportError:
        pass
    else:
        try:
            utime = subprocess.check_output(["date", "--date", string, "+%s"])
        except subprocess.CalledProcessError:
            pass
        else:
            return datetime.utcfromtimestamp(int(utime))
    raise argparse.ArgumentTypeError("cannot parse date: %s" % string)


def main():
    rendered_papersizes = ""
    for k, v in sorted(papersizes.items()):
        rendered_papersizes += "    %-8s %s\n" % (papernames[k], v)

    parser = argparse.ArgumentParser(
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description='''\
Losslessly convert raster images to PDF without re-encoding JPEG and JPEG2000
images. This leads to a lossless conversion of JPEG and JPEG2000 images with
the only added file size coming from the PDF container itself.

Other raster graphics formats are losslessly stored in a zip/flate encoding of
their RGB representation. This might increase file size and does not store
transparency. There is nothing that can be done about that until the PDF format
allows embedding other image formats like PNG. Thus, img2pdf is primarily
useful to convert JPEG and JPEG2000 images to PDF.

The output is sent to standard output so that it can be redirected into a file
or to another program as part of a shell pipe. To directly write the output
into a file, use the -o or --output option.
''',
            epilog='''\
Colorspace

  Currently, the colorspace must be forced for JPEG 2000 images that are not in
  the RGB colorspace.  Available colorspace options are based on Python Imaging
  Library (PIL) short handles.

    RGB      RGB color
    L        Grayscale
    1        Black and white (internally converted to grayscale)
    CMYK     CMYK color
    CMYK;I   CMYK color with inversion (for CMYK JPEG files from Adobe)

Paper sizes

  You can specify the short hand paper size names shown in the first column in
  the table below as arguments to the --pagesize and --imgsize options.  The
  width and height they are mapping to is shown in the second column.  Giving
  the value in the second column has the same effect as giving the short hand
  in the first column. Appending ^T (a caret/circumflex followed by the letter
  T) turns the paper size from portrait into landscape. The postfix thus
  symbolizes the transpose. The values are case insensitive.

%s

Fit options

  The img2pdf options for the --fit argument are shown in the first column in
  the table below. The function of these options can be mapped to the geometry
  operators of imagemagick. For users who are familiar with imagemagick, the
  corresponding operator is shown in the second column.  The third column shows
  whether or not the aspect ratio is preserved for that option (same as in
  imagemagick). Just like imagemagick, img2pdf tries hard to preserve the
  aspect ratio, so if the --fit argument is not given, then the default is
  "into" which corresponds to the absence of any operator in imagemagick.
  The value of the --fit option is case insensitive.

    into    |   | Y | The default. Width and height values specify maximum
            |   |   | values.
   ---------+---+---+----------------------------------------------------------
    fill    | ^ | Y | Width and height values specify the minimum values.
   ---------+---+---+----------------------------------------------------------
    exact   | ! | N | Width and height emphatically given.
   ---------+---+---+----------------------------------------------------------
    shrink  | > | Y | Shrinks an image with dimensions larger than the given
            |   |   | ones (and otherwise behaves like "into").
   ---------+---+---+----------------------------------------------------------
    enlarge | < | Y | Enlarges an image with dimensions smaller than the given
            |   |   | ones (and otherwise behaves like "into").

Examples

  Lines starting with a dollar sign denote commands you can enter into your
  terminal. The dollar sign signifies your command prompt. It is not part of
  the command you type.

  Convert two scans in JPEG format to a PDF document.

    $ img2pdf --output out.pdf page1.jpg page2.jpg

  Convert a directory of JPEG images into a PDF with printable A4 pages in
  landscape mode. On each page, the photo takes the maximum amount of space
  while preserving its aspect ratio and a print border of 2 cm on the top and
  bottom and 2.5 cm on the left and right hand side.

    $ img2pdf --output out.pdf --pagesize A4^T --border 2cm:2.5cm *.jpg

  On each A4 page, fit images into a 10 cm times 15 cm rectangle but keep the
  original image size if the image is smaller than that.

    $ img2pdf --output out.pdf -S A4 --imgsize 10cmx15cm --fit shrink *.jpg

  Prepare a directory of photos to be printed borderless on photo paper with a
  3:2 aspect ratio and rotate each page so that its orientation is the same as
  the input image.

    $ img2pdf --output out.pdf --pagesize 15cmx10cm --auto-orient *.jpg

  Encode a grayscale JPEG2000 image. The colorspace has to be forced as img2pdf
  cannot read it from the JPEG2000 file automatically.

    $ img2pdf --output out.pdf --colorspace L input.jp2

Argument parsing

  Argument long options can be abbreviated to a prefix if the abbreviation is
  anambiguous. That is, the prefix must match a unique option.

  Beware of your shell interpreting argument values as special characters (like
  the semicolon in the CMYK;I colorspace option). If in doubt, put the argument
  values in single quotes.

  If you want an argument value to start with one or more minus characters, you
  must use the long option name and join them with an equal sign like so:

    $ img2pdf --author=--test--

  If your input file name starts with one or more minus characters, either
  separate the input files from the other arguments by two minus signs:

    $ img2pdf -- --my-file-starts-with-two-minuses.jpg

  Or be more explicit about its relative path by prepending a ./:

    $ img2pdf ./--my-file-starts-with-two-minuses.jpg

  The order of non-positional arguments (all arguments other than the input
  images) does not matter.
''' % rendered_papersizes)

    parser.add_argument(
        'images', metavar='infile', type=input_images, nargs='*',
        help='Specifies the input file(s) in any format that can be read by '
        'the Python Imaging Library (PIL). If no input images are given, then '
        'a single image is read from standard input. The special filename "-" '
        'can be used once to read an image from standard input. To read a '
        'file in the current directory with the filename "-", pass it to '
        'img2pdf by explicitly stating its relative path like "./-".')
    parser.add_argument(
        '-v', '--verbose', action="store_true",
        help='Makes the program operate in verbose mode, printing messages on '
             'standard error.')
    parser.add_argument(
        '-V', '--version', action='version', version='%(prog)s '+__version__,
        help="Prints version information and exits.")

    outargs = parser.add_argument_group(
            title='General output arguments',
            description='')

    outargs.add_argument(
        '-o', '--output', metavar='out', type=argparse.FileType('wb'),
        default=sys.stdout.buffer,
        help='Makes the program output to a file instead of standard output.')
    outargs.add_argument(
        '-C', '--colorspace', metavar='colorspace', type=parse_colorspacearg,
        help='''
Forces the PIL colorspace. See the epilogue for a list of possible values.
Usually the PDF colorspace would be derived from the color space of the input
image. This option overwrites the automatically detected colorspace from the
input image and thus forces a certain colorspace in the output PDF /ColorSpace
property. This is useful for JPEG 2000 images with a different colorspace than
RGB.''')

    outargs.add_argument(
        '-D', '--nodate', action="store_true",
        help='Suppresses timestamps in the output and thus makes the output '
              'deterministic between individual runs. You can also manually '
              'set a date using the --moddate and --creationdate options.')

    outargs.add_argument(
        "--without-pdfrw", action="store_true",
        help="By default, img2pdf uses the pdfrw library to create the output "
             "PDF if pdfrw is available. If you want to use the internal PDF "
             "generator of img2pdf even if pdfrw is present, then pass this "
             "option. This can be useful if you want to have unicode metadata "
             "values which pdfrw does not yet support (See "
             "https://github.com/pmaupin/pdfrw/issues/39) or if you want the "
             "PDF code to be more human readable.")

    sizeargs = parser.add_argument_group(
        title='Image and page size and layout arguments',
        description='''\

Every input image will be placed on its own page.  The image size is controlled
by the dpi value of the input image or, if unset or missing, the default dpi of
%.2f. By default, each page will have the same size as the image it shows.
Thus, there will be no visible border between the image and the page border by
default. If image size and page size are made different from each other by the
options in this section, the image will always be centered in both dimensions.

The image size and page size can be explicitly set using the --imgsize and
--pagesize options, respectively.  If either dimension of the image size is
specified but the same dimension of the page size is not, then the latter will
be derived from the former using an optional minimal distance between the image
and the page border (given by the --border option) and/or a certain fitting
strategy (given by the --fit option). The converse happens if a dimension of
the page size is set but the same dimension of the image size is not.

Any length value in below options is represented by the meta variable L which
is a floating point value with an optional unit appended (without a space
between them). The default unit is pt (1/72 inch, the PDF unit) and other
allowed units are cm (centimeter), mm (millimeter), and in (inch).

Any size argument of the format LxL in the options below specifies the width
and height of a rectangle where the first L represents the width and the second
L represents the height with an optional unit following each value as described
above.  Either width or height may be omitted but in that case the separating x
must still be present. Instead of giving the width and height explicitly, you
may also specify some (case-insensitive) common page sizes such as letter and
A4.  See the epilogue at the bottom for a complete list of the valid sizes.

The --fit option scales to fit the image into a rectangle that is either
derived from the the --imgsize option or otherwise from the --pagesize option.
If the --border option is given in addition to the --imgsize option while the
--pagesize option is not given, then the page size will be calculated from the
image size, respecting the border setting. If the --border option is given in
addition to the --pagesize option while the --imgsize option is not given, then
the image size will be calculated from the page size, respecting the border
setting. If the --border option is given while both the --pagesize and
--imgsize options are passed, then the --border option will be ignored.

''' % default_dpi)

    sizeargs.add_argument(
            '-S', '--pagesize', metavar='LxL', type=parse_pagesize_rectarg,
            help='''
Sets the size of the PDF pages. The short-option is the upper case S because
it is an mnemonic for being bigger than the image size.''')

    sizeargs.add_argument(
            '-s', '--imgsize', metavar='LxL', type=parse_imgsize_rectarg,
            help='''
Sets the size of the images on the PDF pages.  In addition, the unit dpi is
allowed which will set the image size as a value of dots per inch.  Instead of
a unit, width and height values may also have a percentage sign appended,
indicating a resize of the image by that percentage. The short-option is the
lower case s because it is an mnemonic for being smaller than the page size.
''')
    sizeargs.add_argument(
            '-b', '--border', metavar='L[:L]', type=parse_borderarg,
            help='''
Specifies the minimal distance between the image border and the PDF page
border.  This value Is overwritten by explicit values set by --pagesize or
--imgsize.  The value will be used when calculating page dimensions from the
image dimensions or the other way round. One, or two length values can be given
as an argument, separated by a colon. One value specifies the minimal border on
all four sides. Two values specify the minimal border on the top/bottom and
left/right, respectively. It is not possible to specify asymmetric borders
because images will always be centered on the page.
''')
    sizeargs.add_argument(
            '-f', '--fit', metavar='FIT', type=parse_fitarg,
            default=FitMode.into, help='''

If --imgsize is given, fits the image using these dimensions. Otherwise, fit
the image into the dimensions given by --pagesize.  FIT is one of into, fill,
exact, shrink and enlarge. The default value is "into". See the epilogue at the
bottom for a description of the FIT options.

''')
    sizeargs.add_argument(
            '-a', '--auto-orient', action="store_true",
            help='''
If both dimensions of the page are given via --pagesize, conditionally swaps
these dimensions such that the page orientation is the same as the orientation
of the input image. If the orientation of a page gets flipped, then so do the
values set via the --border option.
''')

    metaargs = parser.add_argument_group(title='Arguments setting metadata',
                                         description='')
    metaargs.add_argument(
        '--title', metavar='title', type=str,
        help='Sets the title metadata value')
    metaargs.add_argument(
        '--author', metavar='author', type=str,
        help='Sets the author metadata value')
    metaargs.add_argument(
        '--creator', metavar='creator', type=str,
        help='Sets the creator metadata value')
    metaargs.add_argument(
        '--producer', metavar='producer', type=str,
        help='Sets the producer metadata value')
    metaargs.add_argument(
        '--creationdate', metavar='creationdate', type=valid_date,
        help='Sets the UTC creation date metadata value in YYYY-MM-DD or '
             'YYYY-MM-DDTHH:MM or YYYY-MM-DDTHH:MM:SS format or any format '
             'understood by python dateutil module or any format understood '
             'by `date --date`')
    metaargs.add_argument(
        '--moddate', metavar='moddate', type=valid_date,
        help='Sets the UTC modification date metadata value in YYYY-MM-DD '
             'or YYYY-MM-DDTHH:MM or YYYY-MM-DDTHH:MM:SS format or any format '
             'understood by python dateutil module or any format understood '
             'by `date --date`')
    metaargs.add_argument(
        '--subject', metavar='subject', type=str,
        help='Sets the subject metadata value')
    metaargs.add_argument(
        '--keywords', metavar='kw', type=str, nargs='+',
        help='Sets the keywords metadata value (can be given multiple times)')

    viewerargs = parser.add_argument_group(
        title='PDF viewer arguments',
        description='PDF files can specify how they are meant to be '
                    'presented to the user by a PDF viewer')

    viewerargs.add_argument(
        '--viewer-panes', metavar="PANES", type=parse_panes,
        help='Instruct the PDF viewer which side panes to show. Valid values '
             'are "outlines" and "thumbs". It is not possible to specify both '
             'at the same time.')
    viewerargs.add_argument(
        '--viewer-initial-page', metavar="NUM", type=int,
        help='Instead of showing the first page, instruct the PDF viewer to '
             'show the given page instead. Page numbers start with 1.')
    viewerargs.add_argument(
        '--viewer-magnification', metavar="MAG", type=parse_magnification,
        help='Instruct the PDF viewer to open the PDF with a certain zoom '
             'level. Valid values are either a floating point number giving '
             'the exact zoom level, "fit" (zoom to fit whole page), "fith" '
             '(zoom to fit page width) and "fitbh" (zoom to fit visible page '
             'width).')
    viewerargs.add_argument(
        '--viewer-page-layout', metavar="LAYOUT", type=parse_layout,
        help='Instruct the PDF viewer how to arrange the pages on the screen. '
             'Valid values are "single" (display single pages), "onecolumn" '
             '(one continuous column), "twocolumnright" (two continuous '
             'columns with odd number pages on the right) and "twocolumnleft" '
             '(two continuous columns with odd numbered pages on the left)')
    viewerargs.add_argument(
        '--viewer-fit-window', action="store_true",
        help='Instruct the PDF viewer to resize the window to fit the page '
             'size')
    viewerargs.add_argument(
        '--viewer-center-window', action="store_true",
        help='Instruct the PDF viewer to center the PDF viewer window')
    viewerargs.add_argument(
        '--viewer-fullscreen', action="store_true",
        help='Instruct the PDF viewer to open the PDF in fullscreen mode')

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    layout_fun = get_layout_fun(args.pagesize, args.imgsize, args.border,
                                args.fit, args.auto_orient)

    # if no positional arguments were supplied, read a single image from
    # standard input
    if len(args.images) == 0:
        logging.info("reading image from standard input")
        try:
            args.images = [sys.stdin.buffer.read()]
        except KeyboardInterrupt:
            exit(0)

    # with the number of pages being equal to the number of images, the
    # value passed to --viewer-initial-page must be between 1 and that number
    if args.viewer_initial_page is not None:
        if args.viewer_initial_page < 1:
            parser.print_usage(file=sys.stderr)
            logging.error("%s: error: argument --viewer-initial-page: must be "
                          "greater than zero" % parser.prog)
            exit(2)
        if args.viewer_initial_page > len(args.images):
            parser.print_usage(file=sys.stderr)
            logging.error("%s: error: argument --viewer-initial-page: must be "
                          "less than or equal to the total number of pages" %
                          parser.prog)
            exit(2)

    args.output.write(
        convert(
            *args.images, title=args.title, author=args.author,
            creator=args.creator, producer=args.producer,
            creationdate=args.creationdate, moddate=args.moddate,
            subject=args.subject, keywords=args.keywords,
            colorspace=args.colorspace, nodate=args.nodate,
            layout_fun=layout_fun, viewer_panes=args.viewer_panes,
            viewer_initial_page=args.viewer_initial_page,
            viewer_magnification=args.viewer_magnification,
            viewer_page_layout=args.viewer_page_layout,
            viewer_fit_window=args.viewer_fit_window,
            viewer_center_window=args.viewer_center_window,
            viewer_fullscreen=args.viewer_fullscreen,
            with_pdfrw=not args.without_pdfrw))

if __name__ == '__main__':
    main()
