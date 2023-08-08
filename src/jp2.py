#!/usr/bin/env python
#
# Copyright (C) 2013 Johannes Schauer Marin Rodrigues <j.schauer at email.de>
#
# this module is heavily based upon jpylyzer which is
# KB / National Library of the Netherlands, Open Planets Foundation
# and released under the same license conditions
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import struct


def getBox(data, byteStart, noBytes):
    boxLengthValue = struct.unpack(">I", data[byteStart : byteStart + 4])[0]
    boxType = data[byteStart + 4 : byteStart + 8]
    contentsStartOffset = 8
    if boxLengthValue == 1:
        boxLengthValue = struct.unpack(">Q", data[byteStart + 8 : byteStart + 16])[0]
        contentsStartOffset = 16
    if boxLengthValue == 0:
        boxLengthValue = noBytes - byteStart
    byteEnd = byteStart + boxLengthValue
    boxContents = data[byteStart + contentsStartOffset : byteEnd]
    return (boxLengthValue, boxType, byteEnd, boxContents)


def parse_ihdr(data):
    height, width, channels, bpp = struct.unpack(">IIHB", data[:11])
    return width, height, channels, bpp + 1


def parse_colr(data):
    meth = struct.unpack(">B", data[0:1])[0]
    if meth != 1:
        raise Exception("only enumerated color method supported")
    enumCS = struct.unpack(">I", data[3:])[0]
    if enumCS == 16:
        return "RGB"
    elif enumCS == 17:
        return "L"
    else:
        raise Exception(
            "only sRGB and greyscale color space is supported, " "got %d" % enumCS
        )


def parse_resc(data):
    hnum, hden, vnum, vden, hexp, vexp = struct.unpack(">HHHHBB", data)
    hdpi = ((hnum / hden) * (10**hexp) * 100) / 2.54
    vdpi = ((vnum / vden) * (10**vexp) * 100) / 2.54
    return hdpi, vdpi


def parse_res(data):
    hdpi, vdpi = None, None
    noBytes = len(data)
    byteStart = 0
    boxLengthValue = 1  # dummy value for while loop condition
    while byteStart < noBytes and boxLengthValue != 0:
        boxLengthValue, boxType, byteEnd, boxContents = getBox(data, byteStart, noBytes)
        if boxType == b"resc":
            hdpi, vdpi = parse_resc(boxContents)
            break
    return hdpi, vdpi


def parse_jp2h(data):
    width, height, colorspace, hdpi, vdpi = None, None, None, None, None
    noBytes = len(data)
    byteStart = 0
    boxLengthValue = 1  # dummy value for while loop condition
    while byteStart < noBytes and boxLengthValue != 0:
        boxLengthValue, boxType, byteEnd, boxContents = getBox(data, byteStart, noBytes)
        if boxType == b"ihdr":
            width, height, channels, bpp = parse_ihdr(boxContents)
        elif boxType == b"colr":
            colorspace = parse_colr(boxContents)
        elif boxType == b"res ":
            hdpi, vdpi = parse_res(boxContents)
        byteStart = byteEnd
    return (width, height, colorspace, hdpi, vdpi, channels, bpp)


def parsejp2(data):
    noBytes = len(data)
    byteStart = 0
    boxLengthValue = 1  # dummy value for while loop condition
    width, height, colorspace, hdpi, vdpi = None, None, None, None, None
    while byteStart < noBytes and boxLengthValue != 0:
        boxLengthValue, boxType, byteEnd, boxContents = getBox(data, byteStart, noBytes)
        if boxType == b"jp2h":
            width, height, colorspace, hdpi, vdpi, channels, bpp = parse_jp2h(
                boxContents
            )
            break
        byteStart = byteEnd
    if not width:
        raise Exception("no width in jp2 header")
    if not height:
        raise Exception("no height in jp2 header")
    if not colorspace:
        raise Exception("no colorspace in jp2 header")
    # retrieving the dpi is optional so we do not error out if not present
    return (width, height, colorspace, hdpi, vdpi, channels, bpp)


def parsej2k(data):
    lsiz, rsiz, xsiz, ysiz, xosiz, yosiz, _, _, _, _, csiz = struct.unpack(
        ">HHIIIIIIIIH", data[4:42]
    )
    ssiz = [None] * csiz
    xrsiz = [None] * csiz
    yrsiz = [None] * csiz
    for i in range(csiz):
        ssiz[i], xrsiz[i], yrsiz[i] = struct.unpack(
            "BBB", data[42 + 3 * i : 42 + 3 * (i + 1)]
        )
    assert ssiz == [7, 7, 7]
    return xsiz - xosiz, ysiz - yosiz, None, None, None, csiz, 8


def parse(data):
    if data[:4] == b"\xff\x4f\xff\x51":
        return parsej2k(data)
    else:
        return parsejp2(data)


if __name__ == "__main__":
    import sys

    width, height, colorspace, hdpi, vdpi, channels, bpp = parse(
        open(sys.argv[1], "rb").read()
    )
    print("width = %d" % width)
    print("height = %d" % height)
    print("colorspace = %s" % colorspace)
    print("hdpi = %s" % hdpi)
    print("vdpi = %s" % vdpi)
    print("channels = %s" % channels)
    print("bpp = %s" % bpp)
