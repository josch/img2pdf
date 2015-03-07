#!/usr/bin/env python2

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

__version__ = "0.1.6~git"

import sys
import zlib
import argparse

from PIL import Image
from datetime import datetime
from jp2 import parsejp2
try:
    from cStringIO import cStringIO
except ImportError:
    from io import BytesIO as cStringIO

# XXX: Switch to use logging module.
def debug_out(message, verbose=True):
    if verbose:
        sys.stderr.write("D: "+message+"\n")

def error_out(message):
    sys.stderr.write("E: "+message+"\n")

def warning_out(message):
    sys.stderr.write("W: "+message+"\n")

def parse(cont, indent=1):
    if type(cont) is dict:
        return b"<<\n"+b"\n".join(
            [4 * indent * b" " + k.encode("utf8") + b" " + parse(v, indent+1)
             for k, v in sorted(cont.items())])+b"\n"+4*(indent-1)*b" "+b">>"
    elif type(cont) is int:
        return str(cont).encode("utf8")
    elif type(cont) is float:
        return ("%0.4f"%cont).encode("utf8")
    elif isinstance(cont, obj):
        return ("%d 0 R"%cont.identifier).encode("utf8")
    elif type(cont) is str:
        return cont.encode("utf8")
    elif type(cont) is bytes:
        return cont
    elif type(cont) is list:
        return b"[ "+b" ".join([parse(c, indent) for c in cont])+b" ]"

class obj(object):
    def __init__(self, content, stream=None):
        self.content = content
        self.stream = stream

    def tostring(self):
        if self.stream:
            return (
                ("%d 0 obj " % self.identifier).encode("utf8") +
                parse(self.content) +
                b"\nstream\n" + self.stream + b"\nendstream\nendobj\n")
        else:
            return ("%d 0 obj "%self.identifier).encode("utf8")+parse(self.content)+b" endobj\n"

class pdfdoc(object):

    def __init__(self, version=3, title=None, author=None, creator=None,
                 producer=None, creationdate=None, moddate=None, subject=None,
                 keywords=None, nodate=False):
        self.version = version # default pdf version 1.3
        now = datetime.now()
        self.objects = []

        info = {}
        if title:
            info["/Title"] = "("+title+")"
        if author:
            info["/Author"] = "("+author+")"
        if creator:
            info["/Creator"] = "("+creator+")"
        if producer:
            info["/Producer"] = "("+producer+")"
        if creationdate:
            info["/CreationDate"] = "(D:"+creationdate.strftime("%Y%m%d%H%M%S")+")"
        elif not nodate:
            info["/CreationDate"] = "(D:"+now.strftime("%Y%m%d%H%M%S")+")"
        if moddate:
            info["/ModDate"] = "(D:"+moddate.strftime("%Y%m%d%H%M%S")+")"
        elif not nodate:
            info["/ModDate"] = "(D:"+now.strftime("%Y%m%d%H%M%S")+")"
        if subject:
            info["/Subject"] = "("+subject+")"
        if keywords:
            info["/Keywords"] = "("+",".join(keywords)+")"

        self.info = obj(info)

        # create an incomplete pages object so that a /Parent entry can be
        # added to each page
        self.pages = obj({
            "/Type": "/Pages",
            "/Kids": [],
            "/Count": 0
        })

        self.catalog = obj({
            "/Pages": self.pages,
            "/Type": "/Catalog"
        })
        self.addobj(self.catalog)
        self.addobj(self.pages)

    def addobj(self, obj):
        newid = len(self.objects)+1
        obj.identifier = newid
        self.objects.append(obj)

    def addimage(self, color, width, height, imgformat, imgdata, pdf_x, pdf_y):
        if color == 'L':
            colorspace = "/DeviceGray"
        elif color == 'RGB':
            colorspace = "/DeviceRGB"
        elif color == 'CMYK' or color == 'CMYK;I':
            colorspace = "/DeviceCMYK"
        else:
            error_out("unsupported color space: %s"%color)
            exit(1)

        if pdf_x < 3.00 or pdf_y < 3.00:
            warning_out("pdf width or height is below 3.00 - decrease the dpi")

        # either embed the whole jpeg or deflate the bitmap representation
        if imgformat is "JPEG":
            ofilter = [ "/DCTDecode" ]
        elif imgformat is "JPEG2000":
            ofilter = [ "/JPXDecode" ]
            self.version = 5 # jpeg2000 needs pdf 1.5
        else:
            ofilter = [ "/FlateDecode" ]
        image = obj({
            "/Type": "/XObject",
            "/Subtype": "/Image",
            "/Filter": ofilter,
            "/Width": width,
            "/Height": height,
            "/ColorSpace": colorspace,
            # hardcoded as PIL doesnt provide bits for non-jpeg formats
            "/BitsPerComponent": 8,
            "/Length": len(imgdata)
        }, imgdata)

        if color == 'CMYK;I':
            # Inverts all four channels
            image.content['/Decode'] = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0]

        text = ("q\n%0.4f 0 0 %0.4f 0 0 cm\n/Im0 Do\nQ"%(pdf_x, pdf_y)).encode('utf8')

        content = obj({
            "/Length": len(text)
        }, text)

        page = obj({
            "/Type": "/Page",
            "/Parent": self.pages,
            "/Resources": {
                "/XObject": {
                    "/Im0": image
                }
            },
            "/MediaBox": [0, 0, pdf_x, pdf_y],
            "/Contents": content
        })
        self.pages.content["/Kids"].append(page)
        self.pages.content["/Count"] += 1
        self.addobj(page)
        self.addobj(content)
        self.addobj(image)

    def tostring(self):
        # add info as last object
        self.addobj(self.info)

        xreftable = list()

        result = ("%%PDF-1.%d\n"%self.version).encode("utf8")

        xreftable.append(b"0000000000 65535 f \n")
        for o in self.objects:
            xreftable.append(("%010d 00000 n \n"%len(result)).encode("utf8"))
            result += o.tostring()

        xrefoffset = len(result)
        result += b"xref\n"
        result += ("0 %d\n"%len(xreftable)).encode("utf8")
        for x in xreftable:
            result += x
        result += b"trailer\n"
        result += parse({"/Size": len(xreftable), "/Info": self.info, "/Root": self.catalog})+b"\n"
        result += b"startxref\n"
        result += ("%d\n"%xrefoffset).encode("utf8")
        result += b"%%EOF\n"
        return result

def convert(images, dpi=None, pagesize=(None, None, None), title=None, author=None,
            creator=None, producer=None, creationdate=None, moddate=None,
            subject=None, keywords=None, colorspace=None, nodate=False,
            verbose=False):

    pdf = pdfdoc(3, title, author, creator, producer, creationdate,
                 moddate, subject, keywords, nodate)

    pdf_orientation = None

    for imfilename in images:
        debug_out("Reading %s"%imfilename, verbose)
        try:
            rawdata = imfilename.read()
            im = cStringIO(rawdata)
        except:
            with open(imfilename, "rb") as im:
                rawdata = im.read()
                im = cStringIO(rawdata)
        try:
            imgdata = Image.open(im)
        except IOError as e:
            # test if it is a jpeg2000 image
            if rawdata[:12] != "\x00\x00\x00\x0C\x6A\x50\x20\x20\x0D\x0A\x87\x0A":
                error_out("cannot read input image (not jpeg2000)")
                error_out("PIL: %s"%e)
                exit(1)
            # image is jpeg2000
            width, height, ics = parsejp2(rawdata)
            imgformat = "JPEG2000"

            if dpi:
                ndpi = dpi, dpi
                debug_out("input dpi (forced) = %d x %d"%ndpi, verbose)
            else:
                # TODO: read real dpi from input jpeg2000 image
                ndpi = (96, 96)
                debug_out("input dpi = %d x %d"%ndpi, verbose)

            if colorspace:
                color = colorspace
                debug_out("input colorspace (forced) = %s"%(ics))
            else:
                color = ics
                debug_out("input colorspace = %s"%(ics), verbose)
        else:
            width, height = imgdata.size
            imgformat = imgdata.format

            if dpi:
                ndpi = dpi, dpi
                debug_out("input dpi (forced) = %d x %d"%ndpi, verbose)
            else:
                ndpi = imgdata.info.get("dpi", (96, 96))
                # in python3, the returned dpi value for some tiff images will
                # not be an integer but a float. To make the behaviour of
                # img2pdf the same between python2 and python3, we convert that
                # float into an integer by rounding
                # search online for the 72.009 dpi problem for more info
                ndpi = (int(round(ndpi[0])),int(round(ndpi[1])))
                debug_out("input dpi = %d x %d"%ndpi, verbose)

            if colorspace:
                color = colorspace
                debug_out("input colorspace (forced) = %s"%(color), verbose)
            else:
                color = imgdata.mode
                if color == "CMYK" and imgformat == "JPEG":
                    # Adobe inverts CMYK JPEGs for some reason, and others
                    # have followed suit as well. Some software assumes the
                    # JPEG is inverted if the Adobe tag (APP14), while other
                    # software assumes all CMYK JPEGs are inverted. I don't
                    # have enough experience with these to know which is
                    # better for images currently in the wild, so I'm going
                    # with the first approach for now.
                    if "adobe" in imgdata.info:
                        color = "CMYK;I"
                debug_out("input colorspace = %s"%(color), verbose)

        debug_out("width x height = %d x %d"%(width,height), verbose)
        debug_out("imgformat = %s"%imgformat, verbose)

        # depending on the input format, determine whether to pass the raw
        # image or the zlib compressed color information
        if imgformat is "JPEG" or imgformat is "JPEG2000":
            if color == '1':
                error_out("jpeg can't be monochrome")
                exit(1)
            imgdata = rawdata
        else:
            # because we do not support /CCITTFaxDecode
            if color == '1':
                debug_out("Converting colorspace 1 to L", verbose)
                imgdata = imgdata.convert('L')
                color = 'L'
            elif color in ("RGB", "L", "CMYK", "CMYK;I"):
                debug_out("Colorspace is OK: %s"%color, verbose)
            else:
                debug_out("Converting colorspace %s to RGB"%color, verbose)
                imgdata = imgdata.convert('RGB')
                color = imgdata.mode
            img = imgdata.tobytes()
            # the python-pil version 2.3.0-1ubuntu3 in Ubuntu does not have the close() method
            try:
                imgdata.close()
            except AttributeError:
                pass
            imgdata = zlib.compress(img)
        im.close()

        # determine current page orientation
        if width < height:
            page_orientation = 'portrait'
        else:
            page_orientation = 'landscape'

        if pdf_orientation == None:
            if pagesize[2] == 'orient':
                if pagesize[0] < pagesize[1]:
                    pdf_orientation = 'portrait'
                else:
                    pdf_orientation = 'landscape'

        if pagesize[2] == 'fit':
            if width/height < pagesize[0]/pagesize[1]:
                pdf_x, pdf_y = None, pagesize[1]
            else:
                pdf_x, pdf_y = pagesize[0], None
        else:
            pdf_x, pdf_y = pagesize[0], pagesize[1]

        # pdf units = 1/72 inch
        if not pdf_x and not pdf_y:
            # pdf output based on dpi
            pdf_x, pdf_y = 72.0*width/float(ndpi[0]), 72.0*height/float(ndpi[1])
        elif not pdf_y:
            pdf_x, pdf_y = pdf_x, pdf_x*height/float(width)
        elif not pdf_x:
            pdf_x, pdf_y = pdf_y*width/float(height), pdf_y

        if pagesize[2] == 'orient' and pdf_orientation != page_orientation:
            pdf_x, pdf_y = pdf_y, pdf_x

        pdf.addimage(color, width, height, imgformat, imgdata, pdf_x, pdf_y)

    return pdf.tostring()


def positive_float(string):
    value = float(string)
    if value <= 0:
        msg = "%r is not positive"%string
        raise argparse.ArgumentTypeError(msg)
    return value

def valid_date(string):
    return datetime.strptime(string, "%Y-%m-%dT%H:%M:%S")

def valid_size(string):
    papersizes = {
        "letter"    : [612,792],
        "tabloid"   : [792,1224],
        "ledger"    : [1224,792],
        "legal"     : [612,1008],
        "statement" : [396,612],
        "executive" : [540,720],
        "a0"        : [2384,3371],
        "a1"        : [1685,2384],
        "a2"        : [1190,1684],
        "a3"        : [842,1190],
        "a4"        : [595,842],
        "a5"        : [420,595],
        "a4"        : [729,1032],
        "a5"        : [516,729],
        "folio"     : [612,936],
        "quarto"    : [610,780]
    }

    extra_option = None

    try:
        return tuple(papersizes[string.lower()] + ["orient"])

    except KeyError, e:
        tokens = string.split('x')

        if len(tokens) != 2:
            msg = "input size needs to be of the format Ax, xB or AxB with A and B being integers"
            raise argparse.ArgumentTypeError(msg)

        try:
            x = float(tokens[0])
        except ValueError, e:
            x = None

        try:
            try:
                if tokens[1][-1] == '^':
                    extra_option = 'fit'
                    y = float(tokens[1][0:-1])
                elif tokens[1][-1] == '+':
                    extra_option = 'orient'
                    y = float(tokens[1][0:-1])
                else:
                    y = float(tokens[1])
            except IndexError, e:
                y = float(tokens[1])
        except ValueError, e:
            y = None

        if extra_option:
            if not x and not y:
                msg = "cannot auto size or orient pages without specifying width or height"
                raise argparse.ArgumentTypeError(msg)
            elif not x:
                x = y
            elif not y:
                y = x

        return (x,y, extra_option)

parser = argparse.ArgumentParser(
    description='Lossless conversion/embedding of images (in)to pdf')
parser.add_argument(
    'images', metavar='infile', type=str,
    nargs='+', help='input file(s)')
parser.add_argument(
    '-o', '--output', metavar='out', type=argparse.FileType('wb'),
    default=getattr(sys.stdout, "buffer", sys.stdout), help='output file (default: stdout)')

sizeopts = parser.add_mutually_exclusive_group()
sizeopts.add_argument(
    '-d', '--dpi', metavar='dpi', type=positive_float,
    help='dpi for pdf output. If input image does not specify dpi the default is 96.0. Must not be specified together with -s/--pagesize.')
sizeopts.add_argument(
    '-s', '--pagesize', metavar='size', type=valid_size,
    default=(None, None),
    help="size of the pages in the pdf output in format AxB with A and B being width and height of the page in points. You can omit either one of them.  Auto orientation with AxB+.  Auto-fit with AxB^.  Some common page sizes, such as letter and a4, are recognized.  Must not be used with -d/--dpi.")

parser.add_argument(
    '-t', '--title', metavar='title', type=str,
    help='title for metadata')
parser.add_argument(
    '-a', '--author', metavar='author', type=str,
    help='author for metadata')
parser.add_argument(
    '-c', '--creator', metavar='creator', type=str,
    help='creator for metadata')
parser.add_argument(
    '-p', '--producer', metavar='producer', type=str,
    help='producer for metadata')
parser.add_argument(
    '-r', '--creationdate', metavar='creationdate', type=valid_date,
    help='creation date for metadata in YYYY-MM-DDTHH:MM:SS format')
parser.add_argument(
    '-m', '--moddate', metavar='moddate', type=valid_date,
    help='modification date for metadata in YYYY-MM-DDTHH:MM:SS format')
parser.add_argument(
    '-S', '--subject', metavar='subject', type=str,
    help='subject for metadata')
parser.add_argument(
    '-k', '--keywords', metavar='kw', type=str, nargs='+',
    help='keywords for metadata')
parser.add_argument(
    '-C', '--colorspace', metavar='colorspace', type=str,
    help='force PIL colorspace (one of: RGB, L, 1, CMYK, CMYK;I)')
parser.add_argument(
    '-D', '--nodate', help='do not add timestamps', action="store_true")
parser.add_argument(
    '-v', '--verbose', help='verbose mode', action="store_true")
parser.add_argument(
    '-V', '--version', action='version', version='%(prog)s '+__version__,
    help="Print version information and exit")

def main(args=None):
    if args is None:
        args = sys.argv[1:]
    args = parser.parse_args(args)

    args.output.write(
        convert(
            args.images, args.dpi, args.pagesize, args.title, args.author,
            args.creator, args.producer, args.creationdate, args.moddate,
            args.subject, args.keywords, args.colorspace, args.nodate,
            args.verbose))

if __name__ == '__main__':
    main()
