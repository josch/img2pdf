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

import sys
import zlib
import argparse
import struct
from PIL import Image
from datetime import datetime
from jp2 import parsejp2

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
        return "<<\n"+"\n".join(
            [4 * indent * " " + "%s %s" % (k, parse(v, indent+1))
             for k, v in cont.items()])+"\n"+4*(indent-1)*" "+">>"
    elif type(cont) is int or type(cont) is float:
        return str(cont)
    elif isinstance(cont, obj):
        return "%d 0 R"%cont.identifier
    elif type(cont) is str:
        return cont
    elif type(cont) is list:
        return "[ "+" ".join([parse(c, indent) for c in cont])+" ]"

class obj(object):
    def __init__(self, content, stream=None):
        self.content = content
        self.stream = stream

    def tostring(self):
        if self.stream:
            return (
                "%d 0 obj " % self.identifier +
                parse(self.content) +
                "\nstream\n" + self.stream + "\nendstream\nendobj\n")
        else:
            return "%d 0 obj "%self.identifier+parse(self.content)+" endobj\n"

class pdfdoc(object):

    def __init__(self, version=3, title=None, author=None, creator=None,
                 producer=None, creationdate=None, moddate=None, subject=None,
                 keywords=None):
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
        else:
            info["/CreationDate"] = "(D:"+now.strftime("%Y%m%d%H%M%S")+")"
        if moddate:
            info["/ModDate"] = "(D:"+moddate.strftime("%Y%m%d%H%M%S")+")"
        else:
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
            color = "/DeviceGray"
        elif color == 'RGB':
            color = "/DeviceRGB"
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
            "/ColorSpace": color,
            # hardcoded as PIL doesnt provide bits for non-jpeg formats
            "/BitsPerComponent": 8,
            "/Length": len(imgdata)
        }, imgdata)

        text = "q\n%f 0 0 %f 0 0 cm\n/Im0 Do\nQ"%(pdf_x, pdf_y)

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

        result = "%%PDF-1.%d\n"%self.version

        xreftable.append("0000000000 65535 f \n")
        for o in self.objects:
            xreftable.append("%010d 00000 n \n"%len(result))
            result += o.tostring()

        xrefoffset = len(result)
        result += "xref\n"
        result += "0 %d\n"%len(xreftable)
        for x in xreftable:
            result += x
        result += "trailer\n"
        result += parse({"/Size": len(xreftable), "/Info": self.info, "/Root": self.catalog})+"\n"
        result += "startxref\n"
        result += "%d\n"%xrefoffset
        result += "%%EOF\n"
        return result

def convert(images, dpi, x, y, title=None, author=None, creator=None, producer=None,
            creationdate=None, moddate=None, subject=None, keywords=None,
            colorspace=None, verbose=False):

    pdf = pdfdoc(3, title, author, creator, producer, creationdate,
                 moddate, subject, keywords)

    for imfilename in images:
        debug_out("Reading %s"%imfilename, verbose)
        with open(imfilename, "rb") as im:
            rawdata = im.read()
            im.seek(0)
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
                    ndpi = (96, 96) # TODO: read real dpi
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
                    debug_out("input dpi = %d x %d"%ndpi, verbose)

                if colorspace:
                    color = colorspace
                    debug_out("input colorspace (forced) = %s"%(color), verbose)
                else:
                    color = imgdata.mode
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
                elif color in ("RGB", "L"):
                    debug_out("Colorspace is OK: %s"%color, verbose)
                else:
                    debug_out("Converting colorspace %s to RGB"%color, verbose)
                    imgdata = imgdata.convert('RGB')
                    color = imgdata.mode
                imgdata = zlib.compress(imgdata.tostring())

        # pdf units = 1/72 inch
        if not x and not y:
            pdf_x, pdf_y = 72.0*width/ndpi[0], 72.0*height/ndpi[1]
        elif not y:
            pdf_x, pdf_y = x, x*height/width
        elif not x:
            pdf_x, pdf_y = y*width/height, y

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

parser = argparse.ArgumentParser(
    description='Lossless conversion/embedding of images (in)to pdf')
parser.add_argument(
    'images', metavar='infile', type=str,
    nargs='+', help='input file(s)')
parser.add_argument(
    '-o', '--output', metavar='out', type=argparse.FileType('wb'),
    default=sys.stdout, help='output file (default: stdout)')
parser.add_argument(
    '-d', '--dpi', metavar='dpi', type=positive_float,
    help='dpi for pdf output (default: 96.0)')
parser.add_argument(
    '-x', metavar='pdf_x', type=positive_float,
    help='output width in points')
parser.add_argument(
    '-y', metavar='pdf_y', type=positive_float,
    help='output height in points')
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
    '-s', '--subject', metavar='subject', type=str,
    help='subject for metadata')
parser.add_argument(
    '-k', '--keywords', metavar='kw', type=str, nargs='+',
    help='keywords for metadata')
parser.add_argument(
    '-C', '--colorspace', metavar='colorspace', type=str,
    help='force PIL colorspace (one of: RGB, L, 1)')
parser.add_argument(
    '-v', '--verbose', help='verbose mode', action="store_true")

def main(args=None):
    if args is None:
        args = sys.argv[1:]
    args = parser.parse_args(args)

    args.output.write(
        convert(
            args.images, args.dpi, args.x, args.y, args.title, args.author,
            args.creator, args.producer, args.creationdate, args.moddate,
            args.subject, args.keywords, args.colorspace, args.verbose))

if __name__ == '__main__':
    main()
