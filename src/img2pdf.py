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
default_dpi = 96.0

import re
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
            [4 * indent * b" " + k + b" " + parse(v, indent+1)
             for k, v in sorted(cont.items())])+b"\n"+4*(indent-1)*b" "+b">>"
    elif type(cont) is int:
        return str(cont).encode()
    elif type(cont) is float:
        return ("%0.4f"%cont).encode()
    elif isinstance(cont, obj):
        return ("%d 0 R"%cont.identifier).encode()
    elif type(cont) is str or type(cont) is bytes:
        if type(cont) is str and type(cont) is not bytes:
            raise Exception("parse must be passed a bytes object in py3")
        return cont
    elif type(cont) is list:
        return b"[ "+b" ".join([parse(c, indent) for c in cont])+b" ]"
    else:
        raise Exception("cannot handle type %s"%type(cont))

class obj(object):
    def __init__(self, content, stream=None):
        self.content = content
        self.stream = stream

    def tostring(self):
        if self.stream:
            return (
                ("%d 0 obj " % self.identifier).encode() +
                parse(self.content) +
                b"\nstream\n" + self.stream + b"\nendstream\nendobj\n")
        else:
            return ("%d 0 obj "%self.identifier).encode()+parse(self.content)+b" endobj\n"

class pdfdoc(object):

    def __init__(self, version=3, title=None, author=None, creator=None,
                 producer=None, creationdate=None, moddate=None, subject=None,
                 keywords=None, nodate=False):
        self.version = version # default pdf version 1.3
        now = datetime.now()
        self.objects = []

        info = {}
        if title:
            info[b"/Title"] = b"("+title+b")"
        if author:
            info[b"/Author"] = b"("+author+b")"
        if creator:
            info[b"/Creator"] = b"("+creator+b")"
        if producer:
            info[b"/Producer"] = b"("+producer+b")"

        datetime_formatstring = "%Y%m%d%H%M%S"
        if creationdate:
            info[b"/CreationDate"] = b"(D:"+creationdate.strftime(datetime_formatstring).encode()+b")"
        elif not nodate:
            info[b"/CreationDate"] = b"(D:"+now.strftime(datetime_formatstring).encode()+b")"
        if moddate:
            info[b"/ModDate"] = b"(D:"+moddate.strftime(datetime_formatstring).encode()+b")"
        elif not nodate:
            info[b"/ModDate"] = b"(D:"+now.strftime(datetime_formatstring).encode()+b")"

        if subject:
            info[b"/Subject"] = b"("+subject+b")"
        if keywords:
            info[b"/Keywords"] = b"("+b",".join(keywords)+b")"

        self.info = obj(info)

        # create an incomplete pages object so that a /Parent entry can be
        # added to each page
        self.pages = obj({
            b"/Type": b"/Pages",
            b"/Kids": [],
            b"/Count": 0
        })

        self.catalog = obj({
            b"/Pages": self.pages,
            b"/Type": b"/Catalog"
        })
        self.addobj(self.catalog)
        self.addobj(self.pages)

    def addobj(self, obj):
        newid = len(self.objects)+1
        obj.identifier = newid
        self.objects.append(obj)

    def addimage(self, color, width, height, imgformat, imgdata, pdf_x, pdf_y):
        if color == 'L':
            colorspace = b"/DeviceGray"
        elif color == 'RGB':
            colorspace = b"/DeviceRGB"
        elif color == 'CMYK' or color == 'CMYK;I':
            colorspace = b"/DeviceCMYK"
        else:
            error_out("unsupported color space: %s"%color)
            exit(1)

        if pdf_x < 3.00 or pdf_y < 3.00:
            warning_out("pdf width or height is below 3.00 - decrease the dpi")

        # either embed the whole jpeg or deflate the bitmap representation
        if imgformat is "JPEG":
            ofilter = [ b"/DCTDecode" ]
        elif imgformat is "JPEG2000":
            ofilter = [ b"/JPXDecode" ]
            self.version = 5 # jpeg2000 needs pdf 1.5
        else:
            ofilter = [ b"/FlateDecode" ]
        image = obj({
            b"/Type": b"/XObject",
            b"/Subtype": b"/Image",
            b"/Filter": ofilter,
            b"/Width": width,
            b"/Height": height,
            b"/ColorSpace": colorspace,
            # hardcoded as PIL doesnt provide bits for non-jpeg formats
            b"/BitsPerComponent": 8,
            b"/Length": len(imgdata)
        }, imgdata)

        if color == 'CMYK;I':
            # Inverts all four channels
            image.content[b'/Decode'] = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0]

        text = ("q\n%0.4f 0 0 %0.4f 0 0 cm\n/Im0 Do\nQ"%(pdf_x, pdf_y)).encode()

        content = obj({
            b"/Length": len(text)
        }, text)

        page = obj({
            b"/Type": b"/Page",
            b"/Parent": self.pages,
            b"/Resources": {
                b"/XObject": {
                    b"/Im0": image
                }
            },
            b"/MediaBox": [0, 0, pdf_x, pdf_y],
            b"/Contents": content
        })
        self.pages.content[b"/Kids"].append(page)
        self.pages.content[b"/Count"] += 1
        self.addobj(page)
        self.addobj(content)
        self.addobj(image)

    def tostring(self):
        # add info as last object
        self.addobj(self.info)

        xreftable = list()

        result = ("%%PDF-1.%d\n"%self.version).encode()

        xreftable.append(b"0000000000 65535 f \n")
        for o in self.objects:
            xreftable.append(("%010d 00000 n \n"%len(result)).encode())
            result += o.tostring()

        xrefoffset = len(result)
        result += b"xref\n"
        result += ("0 %d\n"%len(xreftable)).encode()
        for x in xreftable:
            result += x
        result += b"trailer\n"
        result += parse({b"/Size": len(xreftable), b"/Info": self.info, b"/Root": self.catalog})+b"\n"
        result += b"startxref\n"
        result += ("%d\n"%xrefoffset).encode()
        result += b"%%EOF\n"
        return result

def convert(images, dpi=None, pagesize=(None, None, None), title=None,
            author=None, creator=None, producer=None, creationdate=None,
            moddate=None, subject=None, keywords=None, colorspace=None,
            nodate=False, verbose=False):

    pagesize_options = pagesize[2]

    pdf = pdfdoc(3, title, author, creator, producer, creationdate,
                 moddate, subject, keywords, nodate)

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

            # TODO: read real dpi from input jpeg2000 image
            ndpi = (default_dpi, default_dpi)
            debug_out("input dpi = %d x %d" % ndpi, verbose)

            if colorspace:
                color = colorspace
                debug_out("input colorspace (forced) = %s"%(ics))
            else:
                color = ics
                debug_out("input colorspace = %s"%(ics), verbose)
        else:
            width, height = imgdata.size
            imgformat = imgdata.format

            ndpi = imgdata.info.get("dpi", (default_dpi, default_dpi))
            # in python3, the returned dpi value for some tiff images will
            # not be an integer but a float. To make the behaviour of
            # img2pdf the same between python2 and python3, we convert that
            # float into an integer by rounding
            # search online for the 72.009 dpi problem for more info
            ndpi = (int(round(ndpi[0])),int(round(ndpi[1])))
            debug_out("input dpi = %d x %d" % ndpi, verbose)

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

        if dpi:
            ndpi = dpi, dpi
            debug_out("input dpi (forced) = %d x %d" % ndpi, verbose)
        elif pagesize_options:
            ndpi = get_ndpi(width, height, pagesize)
            debug_out("calculated dpi (based on pagesize) = %d x %d" % ndpi, verbose)

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

        if pagesize_options and pagesize_options['exact'][1]:
            # output size exactly to specified dimensions
            # pagesize[0], pagesize[1] already checked in valid_size()
            pdf_x, pdf_y = pagesize[0], pagesize[1]
        else:
            # output size based on dpi; point = 1/72 inch
            pdf_x, pdf_y = 72.0*width/float(ndpi[0]), 72.0*height/float(ndpi[1])

        pdf.addimage(color, width, height, imgformat, imgdata, pdf_x, pdf_y)

    return pdf.tostring()

def get_ndpi(width, height, pagesize):
    pagesize_options = pagesize[2]

    if pagesize_options and pagesize_options['fill'][1]:
        if width/height < pagesize[0]/pagesize[1]:
            tmp_dpi = 72.0*width/pagesize[0]
        else:
            tmp_dpi = 72.0*height/pagesize[1]
    elif pagesize[0] and pagesize[1]:
        # if both height and width given with no specific pagesize_option,
        # resize to fit "into" page
        if width/height < pagesize[0]/pagesize[1]:
            tmp_dpi = 72.0*height/pagesize[1]
        else:
            tmp_dpi = 72.0*width/pagesize[0]
    elif pagesize[0]:
        # if width given, calculate dpi based on width
        tmp_dpi = 72.0*width/pagesize[0]
    elif pagesize[1]:
        # if height given, calculate dpi based on height
        tmp_dpi = 72.0*height/pagesize[1]
    else:
        tmp_dpi = default_dpi

    return tmp_dpi, tmp_dpi

def positive_float(string):
    value = float(string)
    if value <= 0:
        msg = "%r is not positive"%string
        raise argparse.ArgumentTypeError(msg)
    return value

def valid_date(string):
    return datetime.strptime(string, "%Y-%m-%dT%H:%M:%S")

def get_standard_papersize(string):
    papersizes = {
        "11x17"       : "792x792^",     # "792x1224",
        "ledger"      : "792x792^",     # "1224x792",
        "legal"       : "612x612^",     # "612x1008",
        "letter"      : "612x612^",     # "612x792",
        "arche"       : "2592x2592^",   # "2592x3456",
        "archd"       : "1728x1728^",   # "1728x2592",
        "archc"       : "1296x1296^",   # "1296x1728",
        "archb"       : "864x864^",     # "864x1296",
        "archa"       : "648x648^",     # "648x864",
        "a0"          : "2380x2380^",   # "2380x3368",
        "a1"          : "1684x1684^",   # "1684x2380",
        "a2"          : "1190x1190^",   # "1190x1684",
        "a3"          : "842x842^",     # "842x1190",
        "a4"          : "595x595^",     # "595x842",
        "a5"          : "421x421^",     # "421x595",
        "a6"          : "297x297^",     # "297x421",
        "a7"          : "210x210^",     # "210x297",
        "a8"          : "148x148^",     # "148x210",
        "a9"          : "105x105^",     # "105x148",
        "a10"         : "74x74^",       # "74x105",
        "b0"          : "2836x2836^",   # "2836x4008",
        "b1"          : "2004x2004^",   # "2004x2836",
        "b2"          : "1418x1418^",   # "1418x2004",
        "b3"          : "1002x1002^",   # "1002x1418",
        "b4"          : "709x709^",     # "709x1002",
        "b5"          : "501x501^",     # "501x709",
        "c0"          : "2600x2600^",   # "2600x3677",
        "c1"          : "1837x1837^",   # "1837x2600",
        "c2"          : "1298x1298^",   # "1298x1837",
        "c3"          : "918x918^",     # "918x1298",
        "c4"          : "649x649^",     # "649x918",
        "c5"          : "459x459^",     # "459x649",
        "c6"          : "323x323^",     # "323x459",
        "flsa"        : "612x612^",     # "612x936",
        "flse"        : "612x612^",     # "612x936",
        "halfletter"  : "396x396^",     # "396x612",
        "tabloid"     : "792x792^",     # "792x1224",
        "statement"   : "396x396^",     # "396x612",
        "executive"   : "540x540^",     # "540x720",
        "folio"       : "612x612^",     # "612x936",
        "quarto"      : "610x610^",     # "610x780"
    }

    string = string.lower()
    return papersizes.get(string, string)

def valid_size(string):
    # conversion factors from units to points
    units = {
        'in'  : 72.0,
        'cm'  : 72.0/2.54,
        'mm'  : 72.0/25.4,
        'pt' : 1.0
    }

    pagesize_options = {
        'exact'  : ['\!', False],
        'shrink'  : ['\>', False],
        'enlarge' : ['\<', False],
        'fill'    : ['\^', False],
        'percent' : ['\%', False],
        'count'   : ['\@', False],
    }

    string = get_standard_papersize(string)

    pattern = re.compile(r"""
            ([0-9]*\.?[0-9]*)   # tokens.group(1) == width; may be empty
            ([a-z]*)            # tokens.group(2) == units; may be empty
            x
            ([0-9]*\.?[0-9]*)   # tokens.group(3) == height; may be empty
            ([a-zA-Z]*)         # tokens.group(4) == units; may be empty
            ([^0-9a-zA-Z]*)     # tokens.group(5) == extra options
        """, re.VERBOSE)

    tokens = pattern.match(string)

    # tokens.group(0) should match entire input string
    if tokens.group(0) != string:
        msg = ('Input size needs to be of the format AuxBv#, '
            'where A is width, B is height, u and v are units, '
            '# are options.  '
            'You may omit either width or height, but not both.  '
            'Units may be specified as (in, cm, mm, pt).  '
            'You may omit units, which will default to pt.  '
            'Available options include (! = exact ; ^ = fill ; default = into).')
        raise argparse.ArgumentTypeError(msg)

    # temporary list to loop through to process width and height
    pagesize_size = {
        'x' : [0, tokens.group(1), tokens.group(2)],
        'y' : [0, tokens.group(3), tokens.group(4)]
    }

    for key, value in pagesize_size.items():
        try:
            value[0] = float(value[1])
            value[0] *= units[value[2]]     # convert to points
        except ValueError, e:
            # assign None if width or height not provided
            value[0] = None
        except KeyError, e:
            # if units unrecognized, raise error
            # otherwise default to pt because units not provided 
            if value[2]:
                msg = "unrecognized unit '%s'." % value[2]
                raise argparse.ArgumentTypeError(msg)

    x = pagesize_size['x'][0]
    y = pagesize_size['y'][0]

    # parse options for resize methods
    if tokens.group(5):
        for key, value in pagesize_options.items():
            if re.search(value[0], tokens.group(5)):
                value[1] = True

    if pagesize_options['fill'][1]:
        # if either width or height is not given, try to fill in missing value
        if not x:
            x = y
        elif not y:
            y = x

    if pagesize_options['exact'][1]:
        if not x or not y:
            msg = ('exact size requires both width and height.')
            raise argparse.ArgumentTypeError(msg)

    if not x and not y:
        msg = ('width and height cannot both be omitted.')
        raise argparse.ArgumentTypeError(msg)

    return (x, y, pagesize_options)

# in python3, the received argument will be a unicode str() object which needs
# to be encoded into a bytes() object
# in python2, the received argument will be a binary str() object which needs
# no encoding
# we check whether we use python2 or python3 by checking whether the argument
# is both, type str and type bytes (only the case in python2)
def pdf_embedded_string(string):
    if type(string) is str and type(string) is not bytes:
        # py3
        pass
    else:
        # py2
        string = string.decode("utf8")
    string = b"\xfe\xff"+string.encode("utf-16-be")
    string = string.replace(b'\\', b'\\\\')
    string = string.replace(b'(', b'\\(')
    string = string.replace(b')', b'\\)')
    return string

parser = argparse.ArgumentParser(
    description='Lossless conversion/embedding of images (in)to pdf')
parser.add_argument(
    'images', metavar='infile', type=str,
    nargs='+', help='input file(s)')
parser.add_argument(
    '-o', '--output', metavar='out', type=argparse.FileType('wb'),
    default=getattr(sys.stdout, "buffer", sys.stdout),
    help='output file (default: stdout)')

sizeopts = parser.add_mutually_exclusive_group()
sizeopts.add_argument(
    '-d', '--dpi', metavar='dpi', type=positive_float,
    help=('dpi for pdf output. '
        'If input image does not specify dpi the default is %.2f.  '
        'Must not be used with -s/--pagesize.') % default_dpi
)

sizeopts.add_argument(
    '-s', '--pagesize', metavar='size', type=valid_size,
    default=(None, None, None),
    help=('size of the pdf pages in format AuxBv#, '
        'where A is width, B is height, u and v are units, # are options. '
        'You may omit either width or height, but not both.  '
        'Some common page sizes, such as letter and a4, are also recognized.  '
        'Units may be specified as (in, cm, mm, pt).  '
        'Units default to pt when absent.  '
        'Available options include (! = exact ; ^ = fill ; default = into).  '
        'Must not be used with -d/--dpi.')
)

parser.add_argument(
    '-t', '--title', metavar='title', type=pdf_embedded_string,
    help='title for metadata')
parser.add_argument(
    '-a', '--author', metavar='author', type=pdf_embedded_string,
    help='author for metadata')
parser.add_argument(
    '-c', '--creator', metavar='creator', type=pdf_embedded_string,
    help='creator for metadata')
parser.add_argument(
    '-p', '--producer', metavar='producer', type=pdf_embedded_string,
    help='producer for metadata')
parser.add_argument(
    '-r', '--creationdate', metavar='creationdate', type=valid_date,
    help='creation date for metadata in YYYY-MM-DDTHH:MM:SS format')
parser.add_argument(
    '-m', '--moddate', metavar='moddate', type=valid_date,
    help='modification date for metadata in YYYY-MM-DDTHH:MM:SS format')
parser.add_argument(
    '-S', '--subject', metavar='subject', type=pdf_embedded_string,
    help='subject for metadata')
parser.add_argument(
    '-k', '--keywords', metavar='kw', type=pdf_embedded_string, nargs='+',
    help='keywords for metadata')
parser.add_argument(
    '-C', '--colorspace', metavar='colorspace', type=pdf_embedded_string,
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
