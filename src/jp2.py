#!/usr/bin/env python
#
# Copyright (C) 2013 Johannes 'josch' Schauer <j.schauer at email.de>
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
    boxLengthValue = struct.unpack(">I", data[byteStart:byteStart+4])[0]
    boxType = data[byteStart+4:byteStart+8]
    contentsStartOffset = 8
    if boxLengthValue == 1:
        boxLengthValue = struct.unpack(">Q", data[byteStart+8:byteStart+16])[0]
        contentsStartOffset = 16
    if boxLengthValue == 0:
        boxLengthValue = noBytes-byteStart
    byteEnd = byteStart + boxLengthValue
    boxContents = data[byteStart+contentsStartOffset:byteEnd]
    return (boxLengthValue, boxType, byteEnd, boxContents)

def parse_ihdr(data):
    height = struct.unpack(">I", data[0:4])[0]
    width = struct.unpack(">I", data[4:8])[0]
    return width, height

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
        raise Exception("only sRGB and greyscale color space is supported, got %d"%enumCS)

def parse_jp2h(data):
    width, height, colorspace = None, None, None
    noBytes=len(data)
    byteStart=0
    boxLengthValue=1 # dummy value for while loop condition
    while byteStart < noBytes and boxLengthValue != 0:
        boxLengthValue, boxType, byteEnd, boxContents = getBox(data, byteStart, noBytes)
        if boxType == 'ihdr':
            width, height = parse_ihdr(boxContents)
        elif boxType == 'colr':
            colorspace = parse_colr(boxContents)
        byteStart = byteEnd
    return (width, height, colorspace)

def parsejp2(data):
    noBytes=len(data)
    byteStart=0
    boxLengthValue=1 # dummy value for while loop condition
    while byteStart < noBytes and boxLengthValue != 0:
        boxLengthValue, boxType, byteEnd, boxContents = getBox(data, byteStart, noBytes)
        if boxType == 'jp2h':
            width, height, colorspace = parse_jp2h(boxContents)
        byteStart = byteEnd
    if not width:
        raise Exception("no width in jp2 header")
    if not height:
        raise Exception("no height in jp2 header")
    if not colorspace:
        raise Exception("no colorspace in jp2 header")
    return (width, height, colorspace)

if __name__ == "__main__":
    import sys
    width, height, colorspace = parsejp2(open(sys.argv[1]).read())
    print "width = %d"%width
    print "height = %d"%height
    print "colorspace = %s"%colorspace
