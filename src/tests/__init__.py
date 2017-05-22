import unittest

import img2pdf
import os
import struct
import sys
import zlib
from PIL import Image
from io import StringIO, BytesIO

HERE = os.path.dirname(__file__)

PY3 = sys.version_info[0] >= 3

if PY3:
    PdfReaderIO = StringIO
else:
    PdfReaderIO = BytesIO


# convert +set date:create +set date:modify -define png:exclude-chunk=time

# we define some variables so that the table below can be narrower
psl = (972, 504)     # --pagesize landscape
psp = (504, 972)     # --pagesize portrait
isl = (756, 324)     # --imgsize landscape
isp = (324, 756)     # --imgsize portrait
border = (162, 270)  # --border
# there is no need to have test cases with the same images with inverted
# orientation (landscape/portrait) because --pagesize and --imgsize are
# already inverted
im1 = (864, 288)     # imgpx #1 => 648x216
im2 = (1152, 576)    # imgpx #2 => 864x432
# shortcuts for fit modes
f_into = img2pdf.FitMode.into
f_fill = img2pdf.FitMode.fill
f_exact = img2pdf.FitMode.exact
f_shrink = img2pdf.FitMode.shrink
f_enlarge = img2pdf.FitMode.enlarge
layout_test_cases = [
    # psp=972x504, psl=504x972, isl=756x324, isp=324x756, border=162:270
    # --pagesize   --border           -a pagepdf      imgpdf
    #        --imgsize     --fit
    (None, None, None,   f_into,    0, (648, 216),  (648, 216),    # 000
                                       (864, 432),  (864, 432)),
    (None, None, None,   f_into,    1, (648, 216),  (648, 216),    # 001
                                       (864, 432),  (864, 432)),
    (None, None, None,   f_fill,    0, (648, 216),  (648, 216),    # 002
                                       (864, 432),  (864, 432)),
    (None, None, None,   f_fill,    1, (648, 216),  (648, 216),    # 003
                                       (864, 432),  (864, 432)),
    (None, None, None,   f_exact,   0, (648, 216),  (648, 216),    # 004
                                       (864, 432),  (864, 432)),
    (None, None, None,   f_exact,   1, (648, 216),  (648, 216),    # 005
                                       (864, 432),  (864, 432)),
    (None, None, None,   f_shrink,  0, (648, 216),  (648, 216),    # 006
                                       (864, 432),  (864, 432)),
    (None, None, None,   f_shrink,  1, (648, 216),  (648, 216),    # 007
                                       (864, 432),  (864, 432)),
    (None, None, None,   f_enlarge, 0, (648, 216),  (648, 216),    # 008
                                       (864, 432),  (864, 432)),
    (None, None, None,   f_enlarge, 1, (648, 216),  (648, 216),    # 009
                                       (864, 432),  (864, 432)),
    (None, None, border, f_into,    0, (1188, 540), (648, 216),    # 010
                                       (1404, 756), (864, 432)),
    (None, None, border, f_into,    1, (1188, 540), (648, 216),    # 011
                                       (1404, 756), (864, 432)),
    (None, None, border, f_fill,    0, (1188, 540), (648, 216),    # 012
                                       (1404, 756), (864, 432)),
    (None, None, border, f_fill,    1, (1188, 540), (648, 216),    # 013
                                       (1404, 756), (864, 432)),
    (None, None, border, f_exact,   0, (1188, 540), (648, 216),    # 014
                                       (1404, 756), (864, 432)),
    (None, None, border, f_exact,   1, (1188, 540), (648, 216),    # 015
                                       (1404, 756), (864, 432)),
    (None, None, border, f_shrink,  0, (1188, 540), (648, 216),    # 016
                                       (1404, 756), (864, 432)),
    (None, None, border, f_shrink,  1, (1188, 540), (648, 216),    # 017
                                       (1404, 756), (864, 432)),
    (None, None, border, f_enlarge, 0, (1188, 540), (648, 216),    # 018
                                       (1404, 756), (864, 432)),
    (None, None, border, f_enlarge, 1, (1188, 540), (648, 216),    # 019
                                       (1404, 756), (864, 432)),
    (None, isp,  None,   f_into,    0, (324, 108),  (324, 108),    # 020
                                       (324, 162),  (324, 162)),
    (None, isp,  None,   f_into,    1, (324, 108),  (324, 108),    # 021
                                       (324, 162),  (324, 162)),
    (None, isp,  None,   f_fill,    0, (2268, 756), (2268, 756),   # 022
                                       (1512, 756), (1512, 756)),
    (None, isp,  None,   f_fill,    1, (2268, 756), (2268, 756),   # 023
                                       (1512, 756), (1512, 756)),
    (None, isp,  None,   f_exact,   0, (324, 756),  (324, 756),    # 024
                                       (324, 756),  (324, 756)),
    (None, isp,  None,   f_exact,   1, (324, 756),  (324, 756),    # 025
                                       (324, 756),  (324, 756)),
    (None, isp,  None,   f_shrink,  0, (324, 108),  (324, 108),    # 026
                                       (324, 162),  (324, 162)),
    (None, isp,  None,   f_shrink,  1, (324, 108),  (324, 108),    # 027
                                       (324, 162),  (324, 162)),
    (None, isp,  None,   f_enlarge, 0, (648, 216),  (648, 216),    # 028
                                       (864, 432),  (864, 432)),
    (None, isp,  None,   f_enlarge, 1, (648, 216),  (648, 216),    # 029
                                       (864, 432),  (864, 432)),
    (None, isp,  border, f_into,    0, (864, 432),  (324, 108),    # 030
                                       (864, 486),  (324, 162)),
    (None, isp,  border, f_into,    1, (864, 432),  (324, 108),    # 031
                                       (864, 486),  (324, 162)),
    (None, isp,  border, f_fill,    0, (2808, 1080), (2268, 756),  # 032
                                       (2052, 1080), (1512, 756)),
    (None, isp,  border, f_fill,    1, (2808, 1080), (2268, 756),  # 033
                                       (2052, 1080), (1512, 756)),
    (None, isp,  border, f_exact,   0, (864, 1080), (324, 756),    # 034
                                       (864, 1080), (324, 756)),
    (None, isp,  border, f_exact,   1, (864, 1080), (324, 756),    # 035
                                       (864, 1080), (324, 756)),
    (None, isp,  border, f_shrink,  0, (864, 432),  (324, 108),    # 036
                                       (864, 486),  (324, 162)),
    (None, isp,  border, f_shrink,  1, (864, 432),  (324, 108),    # 037
                                       (864, 486),  (324, 162)),
    (None, isp,  border, f_enlarge, 0, (1188, 540), (648, 216),    # 038
                                       (1404, 756), (864, 432)),
    (None, isp,  border, f_enlarge, 1, (1188, 540), (648, 216),    # 039
                                       (1404, 756), (864, 432)),
    (None, isl,  None,   f_into,    0, (756, 252),  (756, 252),    # 040
                                       (648, 324),  (648, 324)),
    (None, isl,  None,   f_into,    1, (756, 252),  (756, 252),    # 041
                                       (648, 324),  (648, 324)),
    (None, isl,  None,   f_fill,    0, (972, 324),  (972, 324),    # 042
                                       (756, 378),  (756, 378)),
    (None, isl,  None,   f_fill,    1, (972, 324),  (972, 324),    # 043
                                       (756, 378),  (756, 378)),
    (None, isl,  None,   f_exact,   0, (756, 324),  (756, 324),    # 044
                                       (756, 324),  (756, 324)),
    (None, isl,  None,   f_exact,   1, (756, 324),  (756, 324),    # 045
                                       (756, 324),  (756, 324)),
    (None, isl,  None,   f_shrink,  0, (648, 216),  (648, 216),    # 046
                                       (648, 324),  (648, 324)),
    (None, isl,  None,   f_shrink,  1, (648, 216),  (648, 216),    # 047
                                       (648, 324),  (648, 324)),
    (None, isl,  None,   f_enlarge, 0, (756, 252),  (756, 252),    # 048
                                       (864, 432),  (864, 432)),
    (None, isl,  None,   f_enlarge, 1, (756, 252),  (756, 252),    # 049
                                       (864, 432),  (864, 432)),
    # psp=972x504, psp=504x972, isl=756x324, isp=324x756, border=162:270
    # --pagesize   --border           -a      pagepdf     imgpdf
    #        --imgsize     --fit         imgpx
    (None, isl,  border, f_into,    0, (1296, 576), (756, 252),    # 050
                                       (1188, 648), (648, 324)),
    (None, isl,  border, f_into,    1, (1296, 576), (756, 252),    # 051
                                       (1188, 648), (648, 324)),
    (None, isl,  border, f_fill,    0, (1512, 648), (972, 324),    # 052
                                       (1296, 702), (756, 378)),
    (None, isl,  border, f_fill,    1, (1512, 648), (972, 324),    # 053
                                       (1296, 702), (756, 378)),
    (None, isl,  border, f_exact,   0, (1296, 648), (756, 324),    # 054
                                       (1296, 648), (756, 324)),
    (None, isl,  border, f_exact,   1, (1296, 648), (756, 324),    # 055
                                       (1296, 648), (756, 324)),
    (None, isl,  border, f_shrink,  0, (1188, 540), (648, 216),    # 056
                                       (1188, 648), (648, 324)),
    (None, isl,  border, f_shrink,  1, (1188, 540), (648, 216),    # 057
                                       (1188, 648), (648, 324)),
    (None, isl,  border, f_enlarge, 0, (1296, 576), (756, 252),    # 058
                                       (1404, 756), (864, 432)),
    (None, isl,  border, f_enlarge, 1, (1296, 576), (756, 252),    # 059
                                       (1404, 756), (864, 432)),
    (psp,  None, None,   f_into,    0, (504, 972),  (504, 168),    # 060
                                       (504, 972),  (504, 252)),
    (psp,  None, None,   f_into,    1, (972, 504),  (972, 324),    # 061
                                       (972, 504),  (972, 486)),
    (psp,  None, None,   f_fill,    0, (504, 972),  (2916, 972),   # 062
                                       (504, 972),  (1944, 972)),
    (psp,  None, None,   f_fill,    1, (972, 504),  (1512, 504),   # 063
                                       (972, 504),  (1008, 504)),
    (psp,  None, None,   f_exact,   0, (504, 972),  (504, 972),    # 064
                                       (504, 972),  (504, 972)),
    (psp,  None, None,   f_exact,   1, (972, 504),  (972, 504),    # 065
                                       (972, 504),  (972, 504)),
    (psp,  None, None,   f_shrink,  0, (504, 972),  (504, 168),    # 066
                                       (504, 972),  (504, 252)),
    (psp,  None, None,   f_shrink,  1, (972, 504),  (648, 216),    # 067
                                       (972, 504),  (864, 432)),
    (psp,  None, None,   f_enlarge, 0, (504, 972),  (648, 216),    # 068
                                       (504, 972),  (864, 432)),
    (psp,  None, None,   f_enlarge, 1, (972, 504),  (972, 324),    # 069
                                       (972, 504),  (972, 486)),
    (psp,  None, border, f_into,    0, None,  None, None,  None),  # 070
    (psp,  None, border, f_into,    1, None,  None, None,  None),  # 071
    (psp,  None, border, f_fill,    0, (504, 972),  (1944, 648),   # 072
                                       (504, 972),  (1296, 648)),
    (psp,  None, border, f_fill,    1, (972, 504),  (648, 216),    # 073
                                       (972, 504),  (648, 324)),
    (psp,  None, border, f_exact,   0, None,  None, None,  None),  # 074
    (psp,  None, border, f_exact,   1, None,  None, None,  None),  # 075
    (psp,  None, border, f_shrink,  0, None,  None, None,  None),  # 076
    (psp,  None, border, f_shrink,  1, None,  None, None,  None),  # 077
    (psp,  None, border, f_enlarge, 0, (504, 972),  (648, 216),    # 078
                                       (504, 972),  (864, 432)),
    (psp,  None, border, f_enlarge, 1, (972, 504),  (648, 216),    # 079
                                       (972, 504),  (864, 432)),
    (psp,  isp,  None,   f_into,    0, (504, 972),  (324, 108),    # 080
                                       (504, 972),  (324, 162)),
    (psp,  isp,  None,   f_into,    1, (972, 504),  (324, 108),    # 081
                                       (972, 504),  (324, 162)),
    (psp,  isp,  None,   f_fill,    0, (504, 972),  (2268, 756),   # 082
                                       (504, 972),  (1512, 756)),
    (psp,  isp,  None,   f_fill,    1, (972, 504),  (2268, 756),   # 083
                                       (972, 504),  (1512, 756)),
    (psp,  isp,  None,   f_exact,   0, (504, 972),  (324, 756),    # 084
                                       (504, 972),  (324, 756)),
    (psp,  isp,  None,   f_exact,   1, (972, 504),  (324, 756),    # 085
                                       (972, 504),  (324, 756)),
    (psp,  isp,  None,   f_shrink,  0, (504, 972),  (324, 108),    # 086
                                       (504, 972),  (324, 162)),
    (psp,  isp,  None,   f_shrink,  1, (972, 504),  (324, 108),    # 087
                                       (972, 504),  (324, 162)),
    (psp,  isp,  None,   f_enlarge, 0, (504, 972),  (648, 216),    # 088
                                       (504, 972),  (864, 432)),
    (psp,  isp,  None,   f_enlarge, 1, (972, 504),  (648, 216),    # 089
                                       (972, 504),  (864, 432)),
    (psp,  isp,  border, f_into,    0, (504, 972),  (324, 108),    # 090
                                       (504, 972),  (324, 162)),
    (psp,  isp,  border, f_into,    1, (972, 504),  (324, 108),    # 091
                                       (972, 504),  (324, 162)),
    (psp,  isp,  border, f_fill,    0, (504, 972),  (2268, 756),   # 092
                                       (504, 972),  (1512, 756)),
    (psp,  isp,  border, f_fill,    1, (972, 504),  (2268, 756),   # 093
                                       (972, 504),  (1512, 756)),
    (psp,  isp,  border, f_exact,   0, (504, 972),  (324, 756),    # 094
                                       (504, 972),  (324, 756)),
    (psp,  isp,  border, f_exact,   1, (972, 504),  (324, 756),    # 095
                                       (972, 504),  (324, 756)),
    (psp,  isp,  border, f_shrink,  0, (504, 972),  (324, 108),    # 096
                                       (504, 972),  (324, 162)),
    (psp,  isp,  border, f_shrink,  1, (972, 504),  (324, 108),    # 097
                                       (972, 504),  (324, 162)),
    (psp,  isp,  border, f_enlarge, 0, (504, 972),  (648, 216),    # 098
                                       (504, 972),  (864, 432)),
    (psp,  isp,  border, f_enlarge, 1, (972, 504),  (648, 216),    # 099
                                       (972, 504),  (864, 432)),
    # psp=972x504, psp=504x972, isl=756x324, isp=324x756, border=162:270
    # --pagesize   --border           -a      pagepdf    imgpdf
    #        --imgsize     --fit         imgpx
    (psp,  isl,  None,   f_into,    0, (504, 972),  (756, 252),    # 100
                                       (504, 972),  (648, 324)),
    (psp,  isl,  None,   f_into,    1, (972, 504),  (756, 252),    # 101
                                       (972, 504),  (648, 324)),
    (psp,  isl,  None,   f_fill,    0, (504, 972),  (972, 324),    # 102
                                       (504, 972),  (756, 378)),
    (psp,  isl,  None,   f_fill,    1, (972, 504),  (972, 324),    # 103
                                       (972, 504),  (756, 378)),
    (psp,  isl,  None,   f_exact,   0, (504, 972),  (756, 324),    # 104
                                       (504, 972),  (756, 324)),
    (psp,  isl,  None,   f_exact,   1, (972, 504),  (756, 324),    # 105
                                       (972, 504),  (756, 324)),
    (psp,  isl,  None,   f_shrink,  0, (504, 972),  (648, 216),    # 106
                                       (504, 972),  (648, 324)),
    (psp,  isl,  None,   f_shrink,  1, (972, 504),  (648, 216),    # 107
                                       (972, 504),  (648, 324)),
    (psp,  isl,  None,   f_enlarge, 0, (504, 972),  (756, 252),    # 108
                                       (504, 972),  (864, 432)),
    (psp,  isl,  None,   f_enlarge, 1, (972, 504),  (756, 252),    # 109
                                       (972, 504),  (864, 432)),
    (psp,  isl,  border, f_into,    0, (504, 972),  (756, 252),    # 110
                                       (504, 972),  (648, 324)),
    (psp,  isl,  border, f_into,    1, (972, 504),  (756, 252),    # 111
                                       (972, 504),  (648, 324)),
    (psp,  isl,  border, f_fill,    0, (504, 972),  (972, 324),    # 112
                                       (504, 972),  (756, 378)),
    (psp,  isl,  border, f_fill,    1, (972, 504),  (972, 324),    # 113
                                       (972, 504),  (756, 378)),
    (psp,  isl,  border, f_exact,   0, (504, 972),  (756, 324),    # 114
                                       (504, 972),  (756, 324)),
    (psp,  isl,  border, f_exact,   1, (972, 504),  (756, 324),    # 115
                                       (972, 504),  (756, 324)),
    (psp,  isl,  border, f_shrink,  0, (504, 972),  (648, 216),    # 116
                                       (504, 972),  (648, 324)),
    (psp,  isl,  border, f_shrink,  1, (972, 504),  (648, 216),    # 117
                                       (972, 504),  (648, 324)),
    (psp,  isl,  border, f_enlarge, 0, (504, 972),  (756, 252),    # 118
                                       (504, 972),  (864, 432)),
    (psp,  isl,  border, f_enlarge, 1, (972, 504),  (756, 252),    # 119
                                       (972, 504),  (864, 432)),
    (psl,  None, None,   f_into,    0, (972, 504),  (972, 324),    # 120
                                       (972, 504),  (972, 486)),
    (psl,  None, None,   f_into,    1, (972, 504),  (972, 324),    # 121
                                       (972, 504),  (972, 486)),
    (psl,  None, None,   f_fill,    0, (972, 504),  (1512, 504),   # 122
                                       (972, 504),  (1008, 504)),
    (psl,  None, None,   f_fill,    1, (972, 504),  (1512, 504),   # 123
                                       (972, 504),  (1008, 504)),
    (psl,  None, None,   f_exact,   0, (972, 504),  (972, 504),    # 124
                                       (972, 504),  (972, 504)),
    (psl,  None, None,   f_exact,   1, (972, 504),  (972, 504),    # 125
                                       (972, 504),  (972, 504)),
    (psl,  None, None,   f_shrink,  0, (972, 504),  (648, 216),    # 126
                                       (972, 504),  (864, 432)),
    (psl,  None, None,   f_shrink,  1, (972, 504),  (648, 216),    # 127
                                       (972, 504),  (864, 432)),
    (psl,  None, None,   f_enlarge, 0, (972, 504),  (972, 324),    # 128
                                       (972, 504),  (972, 486)),
    (psl,  None, None,   f_enlarge, 1, (972, 504),  (972, 324),    # 129
                                       (972, 504),  (972, 486)),
    (psl,  None, border, f_into,    0, (972, 504),  (432, 144),    # 130
                                       (972, 504),  (360, 180)),
    (psl,  None, border, f_into,    1, (972, 504),  (432, 144),    # 131
                                       (972, 504),  (360, 180)),
    (psl,  None, border, f_fill,    0, (972, 504),  (540, 180),    # 132
                                       (972, 504),  (432, 216)),
    (psl,  None, border, f_fill,    1, (972, 504),  (540, 180),    # 133
                                       (972, 504),  (432, 216)),
    (psl,  None, border, f_exact,   0, (972, 504),  (432, 180),    # 134
                                       (972, 504),  (432, 180)),
    (psl,  None, border, f_exact,   1, (972, 504),  (432, 180),    # 135
                                       (972, 504),  (432, 180)),
    (psl,  None, border, f_shrink,  0, (972, 504),  (432, 144),    # 136
                                       (972, 504),  (360, 180)),
    (psl,  None, border, f_shrink,  1, (972, 504),  (432, 144),    # 137
                                       (972, 504),  (360, 180)),
    (psl,  None, border, f_enlarge, 0, (972, 504),  (648, 216),    # 138
                                       (972, 504),  (864, 432)),
    (psl,  None, border, f_enlarge, 1, (972, 504),  (648, 216),    # 139
                                       (972, 504),  (864, 432)),
    (psl,  isp,  None,   f_into,    0, (972, 504),  (324, 108),    # 140
                                       (972, 504),  (324, 162)),
    (psl,  isp,  None,   f_into,    1, (972, 504),  (324, 108),    # 141
                                       (972, 504),  (324, 162)),
    (psl,  isp,  None,   f_fill,    0, (972, 504),  (2268, 756),   # 142
                                       (972, 504),  (1512, 756)),
    (psl,  isp,  None,   f_fill,    1, (972, 504),  (2268, 756),   # 143
                                       (972, 504),  (1512, 756)),
    (psl,  isp,  None,   f_exact,   0, (972, 504),  (324, 756),    # 144
                                       (972, 504),  (324, 756)),
    (psl,  isp,  None,   f_exact,   1, (972, 504),  (324, 756),    # 145
                                       (972, 504),  (324, 756)),
    (psl,  isp,  None,   f_shrink,  0, (972, 504),  (324, 108),    # 146
                                       (972, 504),  (324, 162)),
    (psl,  isp,  None,   f_shrink,  1, (972, 504),  (324, 108),    # 147
                                       (972, 504),  (324, 162)),
    (psl,  isp,  None,   f_enlarge, 0, (972, 504),  (648, 216),    # 148
                                       (972, 504),  (864, 432)),
    (psl,  isp,  None,   f_enlarge, 1, (972, 504),  (648, 216),    # 149
                                       (972, 504),  (864, 432)),
    # psp=972x504, psl=504x972, isl=756x324, isp=324x756, border=162:270
    # --pagesize   --border           -a      pagepdf     imgpdf
    #        --imgsize     --fit         imgpx
    (psl,  isp,  border, f_into,    0, (972, 504),  (324, 108),    # 150
                                       (972, 504),  (324, 162)),
    (psl,  isp,  border, f_into,    1, (972, 504),  (324, 108),    # 151
                                       (972, 504),  (324, 162)),
    (psl,  isp,  border, f_fill,    0, (972, 504),  (2268, 756),   # 152
                                       (972, 504),  (1512, 756)),
    (psl,  isp,  border, f_fill,    1, (972, 504),  (2268, 756),   # 153
                                       (972, 504),  (1512, 756)),
    (psl,  isp,  border, f_exact,   0, (972, 504),  (324, 756),    # 154
                                       (972, 504),  (324, 756)),
    (psl,  isp,  border, f_exact,   1, (972, 504),  (324, 756),    # 155
                                       (972, 504),  (324, 756)),
    (psl,  isp,  border, f_shrink,  0, (972, 504),  (324, 108),    # 156
                                       (972, 504),  (324, 162)),
    (psl,  isp,  border, f_shrink,  1, (972, 504),  (324, 108),    # 157
                                       (972, 504),  (324, 162)),
    (psl,  isp,  border, f_enlarge, 0, (972, 504),  (648, 216),    # 158
                                       (972, 504),  (864, 432)),
    (psl,  isp,  border, f_enlarge, 1, (972, 504),  (648, 216),    # 159
                                       (972, 504),  (864, 432)),
    (psl,  isl,  None,   f_into,    0, (972, 504),  (756, 252),    # 160
                                       (972, 504),  (648, 324)),
    (psl,  isl,  None,   f_into,    1, (972, 504),  (756, 252),    # 161
                                       (972, 504),  (648, 324)),
    (psl,  isl,  None,   f_fill,    0, (972, 504),  (972, 324),    # 162
                                       (972, 504),  (756, 378)),
    (psl,  isl,  None,   f_fill,    1, (972, 504),  (972, 324),    # 163
                                       (972, 504),  (756, 378)),
    (psl,  isl,  None,   f_exact,   0, (972, 504),  (756, 324),    # 164
                                       (972, 504),  (756, 324)),
    (psl,  isl,  None,   f_exact,   1, (972, 504),  (756, 324),    # 165
                                       (972, 504),  (756, 324)),
    (psl,  isl,  None,   f_shrink,  0, (972, 504),  (648, 216),    # 166
                                       (972, 504),  (648, 324)),
    (psl,  isl,  None,   f_shrink,  1, (972, 504),  (648, 216),    # 167
                                       (972, 504),  (648, 324)),
    (psl,  isl,  None,   f_enlarge, 0, (972, 504),  (756, 252),    # 168
                                       (972, 504),  (864, 432)),
    (psl,  isl,  None,   f_enlarge, 1, (972, 504),  (756, 252),    # 169
                                       (972, 504),  (864, 432)),
    (psl,  isl,  border, f_into,    0, (972, 504),  (756, 252),    # 170
                                       (972, 504),  (648, 324)),
    (psl,  isl,  border, f_into,    1, (972, 504),  (756, 252),    # 171
                                       (972, 504),  (648, 324)),
    (psl,  isl,  border, f_fill,    0, (972, 504),  (972, 324),    # 172
                                       (972, 504),  (756, 378)),
    (psl,  isl,  border, f_fill,    1, (972, 504),  (972, 324),    # 173
                                       (972, 504),  (756, 378)),
    (psl,  isl,  border, f_exact,   0, (972, 504),  (756, 324),    # 174
                                       (972, 504),  (756, 324)),
    (psl,  isl,  border, f_exact,   1, (972, 504),  (756, 324),    # 175
                                       (972, 504),  (756, 324)),
    (psl,  isl,  border, f_shrink,  0, (972, 504),  (648, 216),    # 176
                                       (972, 504),  (648, 324)),
    (psl,  isl,  border, f_shrink,  1, (972, 504),  (648, 216),    # 177
                                       (972, 504),  (648, 324)),
    (psl,  isl,  border, f_enlarge, 0, (972, 504),  (756, 252),    # 178
                                       (972, 504),  (864, 432)),
    (psl,  isl,  border, f_enlarge, 1, (972, 504),  (756, 252),    # 179
                                       (972, 504),  (864, 432)),
]


def tiff_header_for_ccitt(width, height, img_size, ccitt_group=4):
    # Quick and dirty TIFF header builder from
    # https://stackoverflow.com/questions/2641770
    tiff_header_struct = '<' + '2s' + 'h' + 'l' + 'h' + 'hhll' * 8 + 'h'
    return struct.pack(
        tiff_header_struct,
        b'II',  # Byte order indication: Little indian
        42,  # Version number (always 42)
        8,  # Offset to first IFD
        8,  # Number of tags in IFD
        256, 4, 1, width,  # ImageWidth, LONG, 1, width
        257, 4, 1, height,  # ImageLength, LONG, 1, lenght
        258, 3, 1, 1,  # BitsPerSample, SHORT, 1, 1
        259, 3, 1, ccitt_group,  # Compression, SHORT, 1, 4 = CCITT Group 4
        262, 3, 1, 1,  # Threshholding, SHORT, 1, 0 = WhiteIsZero
        273, 4, 1, struct.calcsize(
            tiff_header_struct),  # StripOffsets, LONG, 1, len of header
        278, 4, 1, height,  # RowsPerStrip, LONG, 1, lenght
        279, 4, 1, img_size,  # StripByteCounts, LONG, 1, size of image
        0  # last IFD
        )


def test_suite():
    class TestImg2Pdf(unittest.TestCase):
        pass

    for i, (psopt, isopt, border, fit, ao, pspdf1, ispdf1,
            pspdf2, ispdf2) in enumerate(layout_test_cases):
        if isopt is not None:
            isopt = ((img2pdf.ImgSize.abs, isopt[0]),
                     (img2pdf.ImgSize.abs, isopt[1]))

        def layout_handler(
                self, psopt, isopt, border, fit, ao, pspdf, ispdf, im):
            layout_fun = img2pdf.get_layout_fun(psopt, isopt, border, fit, ao)
            try:
                pwpdf, phpdf, iwpdf, ihpdf = \
                    layout_fun(im[0], im[1], (img2pdf.default_dpi,
                                              img2pdf.default_dpi))
                self.assertEqual((pwpdf, phpdf), pspdf)
                self.assertEqual((iwpdf, ihpdf), ispdf)
            except img2pdf.NegativeDimensionError:
                self.assertEqual(None, pspdf)
                self.assertEqual(None, ispdf)

        def layout_handler_im1(self, psopt=psopt, isopt=isopt, border=border,
                               fit=fit, ao=ao, pspdf=pspdf1, ispdf=ispdf1):
            layout_handler(self, psopt, isopt, border, fit, ao, pspdf, ispdf,
                           im1)
        setattr(TestImg2Pdf, "test_layout_%03d_im1" % i, layout_handler_im1)

        def layout_handler_im2(self, psopt=psopt, isopt=isopt, border=border,
                               fit=fit, ao=ao, pspdf=pspdf2, ispdf=ispdf2):
            layout_handler(self, psopt, isopt, border, fit, ao, pspdf, ispdf,
                           im2)
        setattr(TestImg2Pdf, "test_layout_%03d_im2" % i, layout_handler_im2)

    files = os.listdir(os.path.join(HERE, "input"))
    for with_pdfrw, test_name in [(a, b) for a in [True, False]
                                  for b in files]:
        inputf = os.path.join(HERE, "input", test_name)
        if not os.path.isfile(inputf):
            continue
        outputf = os.path.join(HERE, "output", test_name+".pdf")
        assert os.path.isfile(outputf)

        def handle(self, f=inputf, out=outputf, with_pdfrw=with_pdfrw):
            with open(f, "rb") as inf:
                orig_imgdata = inf.read()
            output = img2pdf.convert(orig_imgdata, nodate=True,
                                     with_pdfrw=with_pdfrw)
            from pdfrw import PdfReader, PdfName, PdfWriter
            from pdfrw.py23_diffs import convert_load, convert_store
            x = PdfReader(PdfReaderIO(convert_load(output)))
            self.assertEqual(sorted(x.keys()), [PdfName.Info, PdfName.Root,
                             PdfName.Size])
            self.assertIn(x.Root.Pages.Count, ('1', '2'))
            if len(x.Root.Pages.Kids) == '1':
                self.assertEqual(x.Size, '7')
                self.assertEqual(len(x.Root.Pages.Kids), 1)
            elif len(x.Root.Pages.Kids) == '2':
                self.assertEqual(x.Size, '10')
                self.assertEqual(len(x.Root.Pages.Kids), 2)
            self.assertEqual(x.Info, {})
            self.assertEqual(sorted(x.Root.keys()), [PdfName.Pages,
                                                     PdfName.Type])
            self.assertEqual(x.Root.Type, PdfName.Catalog)
            self.assertEqual(sorted(x.Root.Pages.keys()),
                             [PdfName.Count, PdfName.Kids, PdfName.Type])
            self.assertEqual(x.Root.Pages.Type, PdfName.Pages)
            orig_img = Image.open(f)
            for pagenum in range(len(x.Root.Pages.Kids)):
                # retrieve the original image frame that this page was
                # generated from
                orig_img.seek(pagenum)
                cur_page = x.Root.Pages.Kids[pagenum]

                ndpi = orig_img.info.get("dpi", (96.0, 96.0))
                # In python3, the returned dpi value for some tiff images will
                # not be an integer but a float. To make the behaviour of
                # img2pdf the same between python2 and python3, we convert that
                # float into an integer by rounding.
                # Search online for the 72.009 dpi problem for more info.
                ndpi = (int(round(ndpi[0])), int(round(ndpi[1])))
                imgwidthpx, imgheightpx = orig_img.size
                pagewidth = 72.0*imgwidthpx/ndpi[0]
                pageheight = 72.0*imgheightpx/ndpi[1]

                def format_float(f):
                    if int(f) == f:
                        return str(int(f))
                    else:
                        return ("%.4f" % f).rstrip("0")

                self.assertEqual(sorted(cur_page.keys()),
                                 [PdfName.Contents, PdfName.MediaBox,
                                  PdfName.Parent, PdfName.Resources,
                                  PdfName.Type])
                self.assertEqual(cur_page.MediaBox,
                                 ['0', '0', format_float(pagewidth),
                                  format_float(pageheight)])
                self.assertEqual(cur_page.Parent, x.Root.Pages)
                self.assertEqual(cur_page.Type, PdfName.Page)
                self.assertEqual(cur_page.Resources.keys(),
                                 [PdfName.XObject])
                self.assertEqual(cur_page.Resources.XObject.keys(),
                                 [PdfName.Im0])
                self.assertEqual(cur_page.Contents.keys(),
                                 [PdfName.Length])
                self.assertEqual(cur_page.Contents.Length,
                                 str(len(cur_page.Contents.stream)))
                self.assertEqual(cur_page.Contents.stream,
                                 "q\n%.4f 0 0 %.4f 0.0000 0.0000 cm\n"
                                 "/Im0 Do\nQ" % (pagewidth, pageheight))

                imgprops = cur_page.Resources.XObject.Im0

                # test if the filter is valid:
                self.assertIn(
                    imgprops.Filter, [[PdfName.DCTDecode], [PdfName.JPXDecode],
                                      [PdfName.FlateDecode],
                                      [PdfName.CCITTFaxDecode]])
                # test if the colorspace is valid
                self.assertIn(
                    imgprops.ColorSpace, [PdfName.DeviceGray,
                                          PdfName.DeviceRGB,
                                          PdfName.DeviceCMYK])

                # test if the image has correct size
                self.assertEqual(imgprops.Width, str(orig_img.size[0]))
                self.assertEqual(imgprops.Height, str(orig_img.size[1]))
                # if the input file is a jpeg then it should've been copied
                # verbatim into the PDF
                if imgprops.Filter in [[PdfName.DCTDecode],
                                       [PdfName.JPXDecode]]:
                    self.assertEqual(
                        cur_page.Resources.XObject.Im0.stream,
                        convert_load(orig_imgdata))
                elif imgprops.Filter == [PdfName.CCITTFaxDecode]:
                    tiff_header = tiff_header_for_ccitt(
                        int(imgprops.Width), int(imgprops.Height),
                        int(imgprops.Length), 4)
                    imgio = BytesIO()
                    imgio.write(tiff_header)
                    imgio.write(convert_store(
                        cur_page.Resources.XObject.Im0.stream))
                    imgio.seek(0)
                    im = Image.open(imgio)
                    self.assertEqual(im.tobytes(), orig_img.tobytes())
                    try:
                        im.close()
                    except AttributeError:
                        pass

                elif imgprops.Filter == [PdfName.FlateDecode]:
                    # otherwise, the data is flate encoded and has to be equal
                    # to the pixel data of the input image
                    imgdata = zlib.decompress(
                        convert_store(cur_page.Resources.XObject.Im0.stream))
                    colorspace = imgprops.ColorSpace
                    if colorspace == PdfName.DeviceGray:
                        colorspace = 'L'
                    elif colorspace == PdfName.DeviceRGB:
                        colorspace = 'RGB'
                    elif colorspace == PdfName.DeviceCMYK:
                        colorspace = 'CMYK'
                    else:
                        raise Exception("invalid colorspace")
                    im = Image.frombytes(colorspace, (int(imgprops.Width),
                                                      int(imgprops.Height)),
                                         imgdata)
                    if orig_img.mode == '1':
                        self.assertEqual(im.tobytes(),
                                         orig_img.convert("L").tobytes())
                    elif orig_img.mode not in ("RGB", "L", "CMYK", "CMYK;I"):
                        self.assertEqual(im.tobytes(),
                                         orig_img.convert("RGB").tobytes())
                    # the python-pil version 2.3.0-1ubuntu3 in Ubuntu does not
                    # have the close() method
                    try:
                        im.close()
                    except AttributeError:
                        pass
            # now use pdfrw to parse and then write out both pdfs and check the
            # result for equality
            y = PdfReader(out)
            outx = BytesIO()
            outy = BytesIO()
            xwriter = PdfWriter()
            ywriter = PdfWriter()
            xwriter.trailer = x
            ywriter.trailer = y
            xwriter.write(outx)
            ywriter.write(outy)
            self.assertEqual(outx.getvalue(), outy.getvalue())
            # the python-pil version 2.3.0-1ubuntu3 in Ubuntu does not have the
            # close() method
            try:
                orig_img.close()
            except AttributeError:
                pass
        if with_pdfrw:
            setattr(TestImg2Pdf, "test_%s_with_pdfrw" % test_name, handle)
        else:
            setattr(TestImg2Pdf, "test_%s_without_pdfrw" % test_name, handle)

    return unittest.TestSuite((
            unittest.makeSuite(TestImg2Pdf),
            ))
