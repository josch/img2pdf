#!/usr/bin/env python3

import sys
import numpy
import scipy.signal
import zlib
import struct
import subprocess
import pytest
import re
import pikepdf
import hashlib
import img2pdf
import os
from io import BytesIO
from PIL import Image
import decimal
from packaging.version import parse as parse_version
import warnings
import json
import pathlib
import itertools
import xml.etree.ElementTree as ET

img2pdfprog = os.getenv("img2pdfprog", default="src/img2pdf.py")

ICC_PROFILE = None
ICC_PROFILE_PATHS = (
    # Debian
    "/usr/share/color/icc/ghostscript/srgb.icc",
    # Fedora
    "/usr/share/ghostscript/iccprofiles/srgb.icc",
    # Archlinux and Gentoo
    "/usr/share/ghostscript/*/iccprofiles/srgb.icc",
)
for glob in ICC_PROFILE_PATHS:
    for path in pathlib.Path("/").glob(glob.lstrip("/")):
        if path.is_file():
            ICC_PROFILE = path
            break

HAVE_FAKETIME = True
try:
    ver = subprocess.check_output(["faketime", "--version"])
    if b"faketime: Version " not in ver:
        HAVE_FAKETIME = False
except FileNotFoundError:
    HAVE_FAKETIME = False

HAVE_MUTOOL = True
try:
    ver = subprocess.check_output(["mutool", "-v"], stderr=subprocess.STDOUT)
    m = re.fullmatch(r"mutool version ([0-9.]+)\n", ver.decode("utf8"))
    if m is None:
        HAVE_MUTOOL = False
    else:
        if parse_version(m.group(1)) < parse_version("1.10.0"):
            HAVE_MUTOOL = False
except FileNotFoundError:
    HAVE_MUTOOL = False

if not HAVE_MUTOOL:
    warnings.warn("mutool >= 1.10.0 not available, skipping checks...")

HAVE_PDFIMAGES_CMYK = True
try:
    ver = subprocess.check_output(["pdfimages", "-v"], stderr=subprocess.STDOUT)
    m = re.fullmatch(r"pdfimages version ([0-9.]+)", ver.split(b"\n")[0].decode("utf8"))
    if m is None:
        HAVE_PDFIMAGES_CMYK = False
    else:
        if parse_version(m.group(1)) < parse_version("0.42.0"):
            HAVE_PDFIMAGES_CMYK = False
except FileNotFoundError:
    HAVE_PDFIMAGES_CMYK = False

if not HAVE_PDFIMAGES_CMYK:
    warnings.warn("pdfimages >= 0.42.0 not available, skipping CMYK checks...")

for prog in ["convert", "compare", "identify"]:
    try:
        subprocess.check_call([prog] + ["-version"], stderr=subprocess.STDOUT)
        globals()[prog.upper()] = [prog]
    except subprocess.CalledProcessError:
        globals()[prog.upper()] = ["magick", prog]

HAVE_IMAGEMAGICK_MODERN = True
HAVE_EXACT_CMYK8 = True
try:
    ver = subprocess.check_output(CONVERT + ["-version"], stderr=subprocess.STDOUT)
    m = re.fullmatch(
        r"Version: ImageMagick ([0-9.]+-[0-9]+) .*", ver.split(b"\n")[0].decode("utf8")
    )
    if m is None:
        HAVE_IMAGEMAGICK_MODERN = False
        HAVE_EXACT_CMYK8 = False
    else:
        if parse_version(m.group(1)) < parse_version("6.9.10-12"):
            HAVE_IMAGEMAGICK_MODERN = False
        if parse_version(m.group(1)) < parse_version("7.1.0-48"):
            HAVE_EXACT_CMYK8 = False
except FileNotFoundError:
    HAVE_IMAGEMAGICK_MODERN = False
    HAVE_EXACT_CMYK8 = False
except subprocess.CalledProcessError:
    HAVE_IMAGEMAGICK_MODERN = False
    HAVE_EXACT_CMYK8 = False

if not HAVE_IMAGEMAGICK_MODERN:
    warnings.warn("imagemagick >= 6.9.10-12 not available, skipping certain checks...")

HAVE_JP2 = True
try:
    ver = subprocess.check_output(
        IDENTIFY + ["-list", "format"], stderr=subprocess.STDOUT
    )
    found = False
    for line in ver.split(b"\n"):
        if re.match(rb"\s+JP2\* JP2\s+rw-\s+JPEG-2000 File Format Syntax", line):
            found = True
            break
    if not found:
        HAVE_JP2 = False
except FileNotFoundError:
    HAVE_JP2 = False
except subprocess.CalledProcessError:
    HAVE_JP2 = False

if not HAVE_JP2:
    warnings.warn("imagemagick has no jpeg 2000 support, skipping certain checks...")

# the result of compare -metric PSNR is either just a floating point value or a
# floating point value following by the same value multiplied by 0.01,
# surrounded in parenthesis since ImagemMagick 7.1.0-48:
# https://github.com/ImageMagick/ImageMagick/commit/751829cd4c911d7a42953a47c1f73068d9e7da2f
psnr_re = re.compile(rb"((?:inf|(?:0|[1-9][0-9]*)(?:\.[0-9]+)?))(?: \([0-9.]+\))?")

###############################################################################
#                               HELPER FUNCTIONS                              #
###############################################################################


# Interpret a datetime string in a given timezone and format it according to a
# given format string in in UTC.
# We avoid using the Python datetime module for this job because doing so would
# just replicate the code we want to test for correctness.
def tz2utcstrftime(string, fmt, timezone):
    return (
        subprocess.check_output(
            [
                "date",
                "--utc",
                f'--date=TZ="{timezone}" {string}',
                f"+{fmt}",
            ]
        )
        .decode("utf8")
        .removesuffix("\n")
    )


def find_closest_palette_color(color, palette):
    if color.ndim == 0:
        idx = (numpy.abs(palette - color)).argmin()
    else:
        # naive distance function by computing the euclidean distance in RGB space
        idx = ((palette - color) ** 2).sum(axis=-1).argmin()
    return palette[idx]


def floyd_steinberg(img, palette):
    result = numpy.array(img, copy=True)
    for y in range(result.shape[0]):
        for x in range(result.shape[1]):
            oldpixel = result[y, x]
            newpixel = find_closest_palette_color(oldpixel, palette)
            quant_error = oldpixel - newpixel
            result[y, x] = newpixel
            if x + 1 < result.shape[1]:
                result[y, x + 1] += quant_error * 7 / 16
            if y + 1 < result.shape[0]:
                result[y + 1, x - 1] += quant_error * 3 / 16
                result[y + 1, x] += quant_error * 5 / 16
            if x + 1 < result.shape[1] and y + 1 < result.shape[0]:
                result[y + 1, x + 1] += quant_error * 1 / 16
    return result


def convolve_rgba(img, kernel):
    return numpy.stack(
        (
            scipy.signal.convolve2d(img[:, :, 0], kernel, "same"),
            scipy.signal.convolve2d(img[:, :, 1], kernel, "same"),
            scipy.signal.convolve2d(img[:, :, 2], kernel, "same"),
            scipy.signal.convolve2d(img[:, :, 3], kernel, "same"),
        ),
        axis=-1,
    )


def rgb2gray(img):
    result = numpy.zeros((60, 60), dtype=numpy.dtype("int64"))
    count = 0
    for y in range(img.shape[0]):
        for x in range(img.shape[1]):
            clin = sum(img[y, x] * [0.2126, 0.7152, 0.0722]) / 0xFFFF
            if clin <= 0.0031308:
                csrgb = 12.92 * clin
            else:
                csrgb = 1.055 * clin ** (1 / 2.4) - 0.055
            result[y, x] = csrgb * 0xFFFF
            count += 1
            # if count == 24:
            #    raise Exception(result[y, x])
    return result


def palettize(img, pal):
    result = numpy.zeros((img.shape[0], img.shape[1]), dtype=numpy.dtype("int64"))
    for y in range(img.shape[0]):
        for x in range(img.shape[1]):
            for i, col in enumerate(pal):
                if numpy.array_equal(img[y, x], col):
                    result[y, x] = i
                    break
            else:
                raise Exception()
    return result


# we cannot use zlib.compress() because different compressors may compress the
# same data differently, for example by using different optimizations on
# different architectures:
# https://lists.fedoraproject.org/archives/list/devel@lists.fedoraproject.org/thread/R7GD4L5Z6HELCDAL2RDESWR2F3ZXHWVX/
#
# to make the compressed representation of the uncompressed data bit-by-bit
# identical on all platforms we make use of the compression method 0, that is,
# no compression at all :)
def compress(data):
    # two-byte zlib header (rfc1950)
    # common header for lowest compression level
    # bits 0-3: Compression info, base-2 logarithm of the LZ77 window size,
    #           minus eight -- 7 indicates a 32K window size
    # bits 4-7: Compression method -- 8 is deflate
    # bits 8-9: Compression level -- 0 is fastest
    # bit 10:   preset dictionary -- 0 is none
    # bits 11-15: check bits so that the 16-bit unsigned integer stored in MSB
    #             order is a multiple of 31
    result = b"\x78\x01"
    # content is stored in deflate format (rfc1951)
    # maximum chunk size is the largest 16 bit unsigned integer
    chunksize = 0xFFFF
    for i in range(0, len(data), chunksize):
        # bits 0-4 are unused
        # bits 5-6 indicate compression method -- 0 is no compression
        # bit 7 indicates the last chunk
        if i * chunksize < len(data) - chunksize:
            result += b"\x00"
        else:
            # last chunck
            result += b"\x01"
        chunk = data[i : i + chunksize]
        # the chunk length as little endian 16 bit unsigned integer
        result += struct.pack("<H", len(chunk))
        # the one's complement of the chunk length
        # one's complement is all bits inverted which is the result of
        # xor with 0xffff for a 16 bit unsigned integer
        result += struct.pack("<H", len(chunk) ^ 0xFFFF)
        result += chunk
    # adler32 checksum of the uncompressed data as big endian 32 bit unsigned
    # integer
    result += struct.pack(">I", zlib.adler32(data))
    return result


def write_png(data, path, bitdepth, colortype, palette=None, iccp=None):
    with open(str(path), "wb") as f:
        f.write(b"\x89PNG\r\n\x1A\n")
        # PNG image type        Colour type Allowed bit depths
        # Greyscale             0           1, 2, 4, 8, 16
        # Truecolour            2           8, 16
        # Indexed-colour        3           1, 2, 4, 8
        # Greyscale with alpha  4           8, 16
        # Truecolour with alpha 6           8, 16
        block = b"IHDR" + struct.pack(
            ">IIBBBBB",
            data.shape[1],  # width
            data.shape[0],  # height
            bitdepth,  # bitdepth
            colortype,  # colortype
            0,  # compression
            0,  # filtertype
            0,  # interlaced
        )
        f.write(
            struct.pack(">I", len(block) - 4)
            + block
            + struct.pack(">I", zlib.crc32(block))
        )
        if iccp is not None:
            with open(iccp, "rb") as infh:
                iccdata = infh.read()
            block = b"iCCP"
            block += b"icc\0"  # arbitrary profile name
            block += b"\0"  # compression method (deflate)
            block += zlib.compress(iccdata)
            f.write(
                struct.pack(">I", len(block) - 4)
                + block
                + struct.pack(">I", zlib.crc32(block))
            )
        if palette is not None:
            block = b"PLTE"
            for col in palette:
                block += struct.pack(">BBB", col[0], col[1], col[2])
            f.write(
                struct.pack(">I", len(block) - 4)
                + block
                + struct.pack(">I", zlib.crc32(block))
            )
        raw = b""
        for y in range(data.shape[0]):
            raw += b"\0"
            if bitdepth == 16:
                raw += data[y].astype(">u2").tobytes()
            elif bitdepth == 8:
                raw += data[y].astype(">u1").tobytes()
            elif bitdepth in [4, 2, 1]:
                valsperbyte = 8 // bitdepth
                for x in range(0, data.shape[1], valsperbyte):
                    val = 0
                    for j in range(valsperbyte):
                        if x + j >= data.shape[1]:
                            break
                        val |= (data[y, x + j].astype(">u2") & (2**bitdepth - 1)) << (
                            (valsperbyte - j - 1) * bitdepth
                        )
                    raw += struct.pack(">B", val)
            else:
                raise Exception()
        compressed = compress(raw)
        block = b"IDAT" + compressed
        f.write(
            struct.pack(">I", len(compressed))
            + block
            + struct.pack(">I", zlib.crc32(block))
        )
        block = b"IEND"
        f.write(struct.pack(">I", 0) + block + struct.pack(">I", zlib.crc32(block)))


def compare(im1, im2, exact, icc, cmyk):
    if exact:
        if cmyk and not HAVE_EXACT_CMYK8:
            raise Exception("cmyk cannot be exact before ImageMagick 7.1.0-48")
        elif icc:
            raise Exception("icc cannot be exact")
        else:
            subprocess.check_call(
                COMPARE
                + [
                    "-metric",
                    "AE",
                    "-alpha",
                    "off",
                    im1,
                    im2,
                    "null:",
                ]
            )
    else:
        iccargs = []
        if icc:
            if ICC_PROFILE is None:
                pytest.skip("Could not locate an ICC profile")
            iccargs = ["-profile", ICC_PROFILE]
        psnr = subprocess.run(
            COMPARE
            + iccargs
            + [
                "-metric",
                "PSNR",
                im1,
                im2,
                "null:",
            ],
            check=False,
            stderr=subprocess.PIPE,
        ).stderr
        assert psnr != b"0"
        assert psnr != b"0 (0)"
        assert psnr_re.fullmatch(psnr) is not None, psnr
        psnr = psnr_re.fullmatch(psnr).group(1)
        psnr = float(psnr)
        assert psnr != 0  # or otherwise we would use the exact variant
        assert psnr > 50


def compare_ghostscript(tmpdir, img, pdf, gsdevice="png16m", exact=True, icc=False):
    if gsdevice in ["png16m", "pnggray"]:
        ext = "png"
    elif gsdevice in ["tiff24nc", "tiff32nc", "tiff48nc"]:
        ext = "tiff"
    else:
        raise Exception("unknown gsdevice: " + gsdevice)
    subprocess.check_call(
        [
            "gs",
            "-dQUIET",
            "-dNOPAUSE",
            "-dBATCH",
            "-sDEVICE=" + gsdevice,
            "-r96",
            "-sOutputFile=" + str(tmpdir / "gs-") + "%00d." + ext,
            str(pdf),
        ]
    )
    compare(str(img), str(tmpdir / "gs-1.") + ext, exact, icc, False)
    (tmpdir / ("gs-1." + ext)).unlink()


def compare_poppler(tmpdir, img, pdf, exact=True, icc=False):
    subprocess.check_call(
        ["pdftocairo", "-r", "96", "-png", str(pdf), str(tmpdir / "poppler")]
    )
    compare(str(img), str(tmpdir / "poppler-1.png"), exact, icc, False)
    (tmpdir / "poppler-1.png").unlink()


def compare_mupdf(tmpdir, img, pdf, exact=True, cmyk=False):
    if not HAVE_MUTOOL:
        return
    if cmyk:
        out = tmpdir / "mupdf.pam"
        subprocess.check_call(
            ["mutool", "draw", "-r", "96", "-c", "cmyk", "-o", str(out), str(pdf)]
        )
    else:
        out = tmpdir / "mupdf.png"
        subprocess.check_call(
            ["mutool", "draw", "-r", "96", "-png", "-o", str(out), str(pdf)]
        )
    compare(str(img), str(out), exact, False, cmyk)
    out.unlink()


def compare_pdfimages_jpg(tmpdir, img, pdf):
    subprocess.check_call(["pdfimages", "-j", str(pdf), str(tmpdir / "images")])
    assert img.read_bytes() == (tmpdir / "images-000.jpg").read_bytes()
    (tmpdir / "images-000.jpg").unlink()


def compare_pdfimages_cmyk(tmpdir, img, pdf):
    if not HAVE_PDFIMAGES_CMYK:
        return
    subprocess.check_call(["pdfimages", "-j", str(pdf), str(tmpdir / "images")])
    assert img.read_bytes() == (tmpdir / "images-000.jpg").read_bytes()
    (tmpdir / "images-000.jpg").unlink()


def compare_pdfimages_jp2(tmpdir, img, pdf):
    subprocess.check_call(["pdfimages", "-jp2", str(pdf), str(tmpdir / "images")])
    assert img.read_bytes() == (tmpdir / "images-000.jp2").read_bytes()
    (tmpdir / "images-000.jp2").unlink()


def compare_pdfimages_tiff(tmpdir, img, pdf):
    subprocess.check_call(["pdfimages", "-tiff", str(pdf), str(tmpdir / "images")])
    subprocess.check_call(
        COMPARE
        + [
            "-metric",
            "AE",
            str(img),
            str(tmpdir / "images-000.tif"),
            "null:",
        ]
    )
    (tmpdir / "images-000.tif").unlink()


def compare_pdfimages_png(tmpdir, img, pdf, exact=True, icc=False):
    subprocess.check_call(["pdfimages", "-png", str(pdf), str(tmpdir / "images")])
    # images-001.png is the grayscale SMask image (the original alpha channel)
    if os.path.isfile(tmpdir / "images-001.png"):
        subprocess.check_call(
            CONVERT
            + [
                str(tmpdir / "images-000.png"),
                str(tmpdir / "images-001.png"),
                "-compose",
                "copy-opacity",
                "-composite",
                str(tmpdir / "composite.png"),
            ]
        )
        (tmpdir / "images-000.png").unlink()
        (tmpdir / "images-001.png").unlink()
        os.rename(tmpdir / "composite.png", tmpdir / "images-000.png")

    if exact:
        if icc:
            raise Exception("not exact with icc")
        subprocess.check_call(
            COMPARE
            + [
                "-metric",
                "AE",
                str(img),
                str(tmpdir / "images-000.png"),
                "null:",
            ]
        )
    else:
        if icc:
            if ICC_PROFILE is None:
                pytest.skip("Could not locate an ICC profile")
            psnr = subprocess.run(
                COMPARE
                + [
                    "-metric",
                    "PSNR",
                    "(",
                    "-profile",
                    ICC_PROFILE,
                    "-depth",
                    "8",
                    str(img),
                    ")",
                    str(tmpdir / "images-000.png"),
                    "null:",
                ],
                check=False,
                stderr=subprocess.PIPE,
            ).stderr
        else:
            psnr = subprocess.run(
                COMPARE
                + [
                    "-metric",
                    "PSNR",
                    str(img),
                    str(tmpdir / "images-000.png"),
                    "null:",
                ],
                check=False,
                stderr=subprocess.PIPE,
            ).stderr
        assert psnr != b"0"
        assert psnr != b"0 (0)"
        psnr = psnr_re.fullmatch(psnr).group(1)
        psnr = float(psnr)
        assert psnr != 0  # or otherwise we would use the exact variant
        assert psnr > 50
    (tmpdir / "images-000.png").unlink()


def tiff_header_for_ccitt(width, height, img_size, ccitt_group=4):
    # Quick and dirty TIFF header builder from
    # https://stackoverflow.com/questions/2641770
    tiff_header_struct = "<" + "2s" + "h" + "l" + "h" + "hhll" * 8 + "h"
    return struct.pack(
        # fmt: off
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
        0
        # last IFD
        # fmt: on
    )


pixel_R = [
    [1, 1, 1, 0],
    [1, 0, 0, 1],
    [1, 0, 0, 1],
    [1, 1, 1, 0],
    [1, 0, 0, 1],
    [1, 0, 0, 1],
    [1, 0, 0, 1],
]
pixel_G = [
    [0, 1, 1, 0],
    [1, 0, 0, 1],
    [1, 0, 0, 0],
    [1, 0, 1, 1],
    [1, 0, 0, 1],
    [1, 0, 0, 1],
    [0, 1, 1, 0],
]
pixel_B = [
    [1, 1, 1, 0],
    [1, 0, 0, 1],
    [1, 0, 0, 1],
    [1, 1, 1, 0],
    [1, 0, 0, 1],
    [1, 0, 0, 1],
    [1, 1, 1, 0],
]


def alpha_value():
    # gaussian kernel with sigma=3
    kernel = numpy.array(
        [
            [0.011362, 0.014962, 0.017649, 0.018648, 0.017649, 0.014962, 0.011362],
            [0.014962, 0.019703, 0.02324, 0.024556, 0.02324, 0.019703, 0.014962],
            [0.017649, 0.02324, 0.027413, 0.028964, 0.027413, 0.02324, 0.017649],
            [0.018648, 0.024556, 0.028964, 0.030603, 0.028964, 0.024556, 0.018648],
            [0.017649, 0.02324, 0.027413, 0.028964, 0.027413, 0.02324, 0.017649],
            [0.014962, 0.019703, 0.02324, 0.024556, 0.02324, 0.019703, 0.014962],
            [0.011362, 0.014962, 0.017649, 0.018648, 0.017649, 0.014962, 0.011362],
        ],
        float,
    )

    # constructs a 2D array of a circle with a width of 36
    circle = list()
    offsets_36 = [14, 11, 9, 7, 6, 5, 4, 3, 3, 2, 2, 1, 1, 1, 0, 0, 0, 0]
    for offs in offsets_36 + offsets_36[::-1]:
        circle.append([0] * offs + [1] * (len(offsets_36) - offs) * 2 + [0] * offs)

    alpha = numpy.zeros((60, 60, 4), dtype=numpy.dtype("int64"))

    # draw three circles
    for xpos, ypos, color in [
        (12, 3, [0xFFFF, 0, 0, 0xFFFF]),
        (21, 21, [0, 0xFFFF, 0, 0xFFFF]),
        (3, 21, [0, 0, 0xFFFF, 0xFFFF]),
    ]:
        for x, row in enumerate(circle):
            for y, pos in enumerate(row):
                if pos:
                    alpha[y + ypos, x + xpos] += color
    alpha = numpy.clip(alpha, 0, 0xFFFF)
    alpha = convolve_rgba(alpha, kernel)

    # draw letters
    for y, row in enumerate(pixel_R):
        for x, pos in enumerate(row):
            if pos:
                alpha[13 + y, 28 + x] = [0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF]
    for y, row in enumerate(pixel_G):
        for x, pos in enumerate(row):
            if pos:
                alpha[39 + y, 40 + x] = [0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF]
    for y, row in enumerate(pixel_B):
        for x, pos in enumerate(row):
            if pos:
                alpha[39 + y, 15 + x] = [0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF]
    return alpha


def icc_profile():
    PCS = (0.96420288, 1.0, 0.82490540)  # D50 illuminant constants
    # approximate X,Y,Z values for white, red, green and blue
    white = (0.95, 1.0, 1.09)
    red = (0.44, 0.22, 0.014)
    green = (0.39, 0.72, 0.1)
    blue = (0.14, 0.06, 0.71)

    getxyz = lambda v: (round(65536 * v[0]), round(65536 * v[1]), round(65536 * v[2]))

    header = (
        # header
        +4 * b"\0"  # cmmsignatures
        + 4 * b"\0"  # version
        + b"mntr"  # device class
        + b"RGB "  # color space
        + b"XYZ "  # PCS
        + 12 * b"\0"  # datetime
        + b"\x61\x63\x73\x70"  # static signature
        + 4 * b"\0"  # platform
        + 4 * b"\0"  # flags
        + 4 * b"\0"  # device manufacturer
        + 4 * b"\0"  # device model
        + 8 * b"\0"  # device attributes
        + 4 * b"\0"  # rendering intents
        + struct.pack(">III", *getxyz(PCS))
        + 4 * b"\0"  # creator
        + 16 * b"\0"  # identifier
        + 28 * b"\0"  # reserved
    )

    def pad4(s):
        if len(s) % 4 == 0:
            return s
        else:
            return s + b"\x00" * (4 - len(s) % 4)

    tagdata = [
        b"desc\x00\x00\x00\x00" + struct.pack(">I", 5) + b"fake" + 79 * b"\x00",
        b"XYZ \x00\x00\x00\x00" + struct.pack(">III", *getxyz(white)),
        # by mixing up red, green and blue, we create a test profile
        b"XYZ \x00\x00\x00\x00" + struct.pack(">III", *getxyz(blue)),  # red
        b"XYZ \x00\x00\x00\x00" + struct.pack(">III", *getxyz(red)),  # green
        b"XYZ \x00\x00\x00\x00" + struct.pack(">III", *getxyz(green)),  # blue
        # by only supplying two values, we create the most trivial "curve",
        # where the remaining values will be linearly interpolated between them
        b"curv\x00\x00\x00\x00" + struct.pack(">IHH", 2, 0, 65535),
        b"text\x00\x00\x00\x00" + b"no copyright, use freely" + 1 * b"\x00",
    ]

    table = [
        (b"desc", 0),
        (b"wtpt", 1),
        (b"rXYZ", 2),
        (b"gXYZ", 3),
        (b"bXYZ", 4),
        # we use the same curve for all three channels, so the same offset is referenced
        (b"rTRC", 5),
        (b"gTRC", 5),
        (b"bTRC", 5),
        (b"cprt", 6),
    ]

    offset = (
        lambda n: 4  # total size
        + len(header)  # header length
        + 4  # number table entries
        + len(table) * 12  # table length
        + sum([len(pad4(s)) for s in tagdata[:n]])
    )

    table = struct.pack(">I", len(table)) + b"".join(
        [t + struct.pack(">II", offset(o), len(tagdata[o])) for t, o in table]
    )

    data = b"".join([pad4(s) for s in tagdata])

    data = (
        struct.pack(">I", 4 + len(header) + len(table) + len(data))
        + header
        + table
        + data
    )

    return data


###############################################################################
#                                 INPUT FIXTURES                              #
###############################################################################


@pytest.fixture(scope="session")
def alpha():
    return alpha_value()


@pytest.fixture(scope="session")
def tmp_alpha_png(tmp_path_factory, alpha):
    tmp_alpha_png = tmp_path_factory.mktemp("alpha_png") / "alpha.png"
    write_png(alpha, str(tmp_alpha_png), 16, 6)
    assert (
        hashlib.md5(tmp_alpha_png.read_bytes()).hexdigest()
        == "600bb4cffb039a022cec6ed55537deba"
    )
    yield tmp_alpha_png
    tmp_alpha_png.unlink()


@pytest.fixture(scope="session")
def tmp_gray1_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    gray16 = rgb2gray(normal16)
    tmp_gray1_png = tmp_path_factory.mktemp("gray1_png") / "gray1.png"
    write_png(
        floyd_steinberg(gray16, numpy.arange(2) / 0x1 * 0xFFFF) / 0xFFFF * 0x1,
        str(tmp_gray1_png),
        1,
        0,
    )
    assert (
        hashlib.md5(tmp_gray1_png.read_bytes()).hexdigest()
        == "dd2c528152d34324747355b73495a115"
    )
    yield tmp_gray1_png
    tmp_gray1_png.unlink()


@pytest.fixture(scope="session")
def tmp_gray2_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    gray16 = rgb2gray(normal16)
    tmp_gray2_png = tmp_path_factory.mktemp("gray2_png") / "gray2.png"
    write_png(
        floyd_steinberg(gray16, numpy.arange(4) / 0x3 * 0xFFFF) / 0xFFFF * 0x3,
        str(tmp_gray2_png),
        2,
        0,
    )
    assert (
        hashlib.md5(tmp_gray2_png.read_bytes()).hexdigest()
        == "68e614f4e6a85053d47098dad0ca3976"
    )
    yield tmp_gray2_png
    tmp_gray2_png.unlink()


@pytest.fixture(scope="session")
def tmp_gray4_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    gray16 = rgb2gray(normal16)
    tmp_gray4_png = tmp_path_factory.mktemp("gray4_png") / "gray4.png"
    write_png(
        floyd_steinberg(gray16, numpy.arange(16) / 0xF * 0xFFFF) / 0xFFFF * 0xF,
        str(tmp_gray4_png),
        4,
        0,
    )
    assert (
        hashlib.md5(tmp_gray4_png.read_bytes()).hexdigest()
        == "ff04a6fea88133eb77bbb748692ae0fd"
    )
    yield tmp_gray4_png
    tmp_gray4_png.unlink()


@pytest.fixture(scope="session")
def tmp_gray8_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    gray16 = rgb2gray(normal16)
    tmp_gray8_png = tmp_path_factory.mktemp("gray8_png") / "gray8.png"
    write_png(gray16 / 0xFFFF * 0xFF, tmp_gray8_png, 8, 0)
    assert (
        hashlib.md5(tmp_gray8_png.read_bytes()).hexdigest()
        == "90b4ed9123f295dda7fde499744dede7"
    )
    yield tmp_gray8_png
    tmp_gray8_png.unlink()


@pytest.fixture(scope="session")
def tmp_gray16_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    gray16 = rgb2gray(normal16)
    tmp_gray16_png = tmp_path_factory.mktemp("gray16_png") / "gray16.png"
    write_png(gray16, str(tmp_gray16_png), 16, 0)
    assert (
        hashlib.md5(tmp_gray16_png.read_bytes()).hexdigest()
        == "f76153d2e72fada11d934c32c8168a57"
    )
    yield tmp_gray16_png
    tmp_gray16_png.unlink()


@pytest.fixture(scope="session")
def tmp_inverse_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    tmp_inverse_png = tmp_path_factory.mktemp("inverse_png") / "inverse.png"
    write_png(0xFF - normal16 / 0xFFFF * 0xFF, str(tmp_inverse_png), 8, 2)
    assert (
        hashlib.md5(tmp_inverse_png.read_bytes()).hexdigest()
        == "0a7d57dc09c4d8fd1ad3511b116c7dfa"
    )
    yield tmp_inverse_png
    tmp_inverse_png.unlink()


@pytest.fixture(scope="session")
def tmp_icc_profile(tmp_path_factory):
    tmp_icc_profile = tmp_path_factory.mktemp("icc_profile") / "fake.icc"
    tmp_icc_profile.write_bytes(icc_profile())
    yield tmp_icc_profile
    tmp_icc_profile.unlink()


@pytest.fixture(scope="session")
def tmp_icc_png(tmp_path_factory, alpha, tmp_icc_profile):
    normal16 = alpha[:, :, 0:3]
    tmp_icc_png = tmp_path_factory.mktemp("icc_png") / "icc.png"
    write_png(
        normal16 / 0xFFFF * 0xFF,
        str(tmp_icc_png),
        8,
        2,
        iccp=str(tmp_icc_profile),
    )
    assert (
        hashlib.md5(tmp_icc_png.read_bytes()).hexdigest()
        == "bf25f673c1617f5f9353b2a043747655"
    )
    yield tmp_icc_png
    tmp_icc_png.unlink()


@pytest.fixture(scope="session")
def tmp_normal16_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    tmp_normal16_png = tmp_path_factory.mktemp("normal16_png") / "normal16.png"
    write_png(normal16, str(tmp_normal16_png), 16, 2)
    assert (
        hashlib.md5(tmp_normal16_png.read_bytes()).hexdigest()
        == "820dd30a2566775fc64c110e8ac65c7e"
    )
    yield tmp_normal16_png
    tmp_normal16_png.unlink()


@pytest.fixture(scope="session")
def tmp_normal_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    tmp_normal_png = tmp_path_factory.mktemp("normal_png") / "normal.png"
    write_png(normal16 / 0xFFFF * 0xFF, str(tmp_normal_png), 8, 2)
    assert (
        hashlib.md5(tmp_normal_png.read_bytes()).hexdigest()
        == "bc30c705f455991cd04be1c298063002"
    )
    yield tmp_normal_png
    tmp_normal_png.unlink()


@pytest.fixture(scope="session")
def tmp_palette1_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    tmp_palette1_png = tmp_path_factory.mktemp("palette1_png") / "palette1.png"
    # don't choose black and white or otherwise imagemagick will classify the
    # image as bilevel with 8/1-bit depth instead of palette with 8-bit color
    # don't choose gray colors or otherwise imagemagick will classify the
    # image as grayscale
    pal1 = numpy.array(
        [[0x01, 0x02, 0x03], [0xFE, 0xFD, 0xFC]], dtype=numpy.dtype("int64")
    )
    write_png(
        palettize(
            floyd_steinberg(normal16, pal1 * 0xFFFF / 0xFF) / 0xFFFF * 0xFF, pal1
        ),
        str(tmp_palette1_png),
        1,
        3,
        pal1,
    )
    assert (
        hashlib.md5(tmp_palette1_png.read_bytes()).hexdigest()
        == "3d065f731540e928fb730b3233e4e8a7"
    )
    yield tmp_palette1_png
    tmp_palette1_png.unlink()


@pytest.fixture(scope="session")
def tmp_palette2_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    tmp_palette2_png = tmp_path_factory.mktemp("palette2_png") / "palette2.png"
    # choose values slightly off red, lime and blue because otherwise
    # imagemagick will classify the image as Depth: 8/1-bit
    pal2 = numpy.array(
        [[0, 0, 0], [0xFE, 0, 0], [0, 0xFE, 0], [0, 0, 0xFE]],
        dtype=numpy.dtype("int64"),
    )
    write_png(
        palettize(
            floyd_steinberg(normal16, pal2 * 0xFFFF / 0xFF) / 0xFFFF * 0xFF, pal2
        ),
        str(tmp_palette2_png),
        2,
        3,
        pal2,
    )
    assert (
        hashlib.md5(tmp_palette2_png.read_bytes()).hexdigest()
        == "0b0d4412c28da26163a622d218ee02ca"
    )
    yield tmp_palette2_png
    tmp_palette2_png.unlink()


@pytest.fixture(scope="session")
def tmp_palette4_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    tmp_palette4_png = tmp_path_factory.mktemp("palette4_png") / "palette4.png"
    # windows 16 color palette
    pal4 = numpy.array(
        [
            [0x00, 0x00, 0x00],
            [0x80, 0x00, 0x00],
            [0x00, 0x80, 0x00],
            [0x80, 0x80, 0x00],
            [0x00, 0x00, 0x80],
            [0x80, 0x00, 0x80],
            [0x00, 0x80, 0x80],
            [0xC0, 0xC0, 0xC0],
            [0x80, 0x80, 0x80],
            [0xFF, 0x00, 0x00],
            [0x00, 0xFF, 0x00],
            [0xFF, 0x00, 0x00],
            [0x00, 0xFF, 0x00],
            [0xFF, 0x00, 0xFF],
            [0x00, 0xFF, 0x00],
            [0xFF, 0xFF, 0xFF],
        ],
        dtype=numpy.dtype("int64"),
    )
    write_png(
        palettize(
            floyd_steinberg(normal16, pal4 * 0xFFFF / 0xFF) / 0xFFFF * 0xFF, pal4
        ),
        str(tmp_palette4_png),
        4,
        3,
        pal4,
    )
    assert (
        hashlib.md5(tmp_palette4_png.read_bytes()).hexdigest()
        == "163f6d7964b80eefa0dc6a48cb7315dd"
    )
    yield tmp_palette4_png
    tmp_palette4_png.unlink()


@pytest.fixture(scope="session")
def tmp_palette8_png(tmp_path_factory, alpha):
    normal16 = alpha[:, :, 0:3]
    tmp_palette8_png = tmp_path_factory.mktemp("palette8_png") / "palette8.png"
    # create a 256 color palette by first writing 16 shades of gray
    # and then writing an array of RGB colors with 6, 8 and 5 levels
    # for red, green and blue, respectively
    pal8 = numpy.zeros((256, 3), dtype=numpy.dtype("int64"))
    i = 0
    for gray in range(15, 255, 15):
        pal8[i] = [gray, gray, gray]
        i += 1
    for red in 0, 0x33, 0x66, 0x99, 0xCC, 0xFF:
        for green in 0, 0x24, 0x49, 0x6D, 0x92, 0xB6, 0xDB, 0xFF:
            for blue in 0, 0x40, 0x80, 0xBF, 0xFF:
                pal8[i] = [red, green, blue]
                i += 1
    assert i == 256
    write_png(
        palettize(
            floyd_steinberg(normal16, pal8 * 0xFFFF / 0xFF) / 0xFFFF * 0xFF, pal8
        ),
        str(tmp_palette8_png),
        8,
        3,
        pal8,
    )
    assert (
        hashlib.md5(tmp_palette8_png.read_bytes()).hexdigest()
        == "8847bb734eba0e2d85e3f97fc2849dd4"
    )
    yield tmp_palette8_png
    tmp_palette8_png.unlink()


@pytest.fixture(scope="session")
def jpg_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("jpg") / "in.jpg"
    subprocess.check_call(CONVERT + [str(tmp_normal_png), str(in_img)])
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "JPEG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/jpeg", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert "resolution" not in identify[0]["image"]
    assert identify[0]["image"].get("units") == "Undefined", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    endian = "endianess" if identify[0].get("version", "0") < "1.0" else "endianness"
    assert identify[0]["image"].get(endian) == "Undefined", str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "JPEG", str(identify)
    assert identify[0]["image"].get("orientation") == "Undefined", str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("jpeg:colorspace") == "2"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def jpg_rot_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("jpg_rot") / "in.jpg"
    subprocess.check_call(CONVERT + [str(tmp_normal_png), str(in_img)])
    subprocess.check_call(
        ["exiftool", "-overwrite_original", "-all=", str(in_img), "-n"]
    )
    subprocess.check_call(
        [
            "exiftool",
            "-overwrite_original",
            "-Orientation=6",
            "-XResolution=96",
            "-YResolution=96",
            "-n",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "JPEG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/jpeg", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("resolution") == {"x": 96, "y": 96}
    assert identify[0]["image"].get("units") == "PixelsPerInch", str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "JPEG", str(identify)
    assert identify[0]["image"].get("orientation") == "RightTop", str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def jpg_cmyk_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("jpg_cmyk") / "in.jpg"
    subprocess.check_call(
        CONVERT + [str(tmp_normal_png), "-colorspace", "cmyk", str(in_img)]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "JPEG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/jpeg", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "CMYK", str(identify)
    assert identify[0]["image"].get("type") == "ColorSeparation", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "JPEG", str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def jpg_2000_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("jpg_2000") / "in.jp2"
    subprocess.check_call(CONVERT + [str(tmp_normal_png), str(in_img)])
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "JP2", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/jp2", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "JPEG2000", str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def jpg_2000_rgba8_img(tmp_path_factory, tmp_alpha_png):
    in_img = tmp_path_factory.mktemp("jpg_2000_rgba8") / "in.jp2"
    subprocess.check_call(CONVERT + [str(tmp_alpha_png), "-depth", "8", str(in_img)])
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "JP2", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/jp2", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColorAlpha", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "JPEG2000", str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def jpg_2000_rgba16_img(tmp_path_factory, tmp_alpha_png):
    in_img = tmp_path_factory.mktemp("jpg_2000_rgba16") / "in.jp2"
    subprocess.check_call(CONVERT + [str(tmp_alpha_png), str(in_img)])
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "JP2", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/jp2", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColorAlpha", str(identify)
    assert identify[0]["image"].get("depth") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "JPEG2000", str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def png_rgb8_img(tmp_normal_png):
    in_img = tmp_normal_png
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "2"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "2 (Truecolor)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return in_img


@pytest.fixture(scope="session")
def png_rgb16_img(tmp_normal16_png):
    in_img = tmp_normal16_png
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig")
        == "16"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "16"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "2"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "2 (Truecolor)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return in_img


@pytest.fixture(scope="session")
def png_rgba8_img(tmp_path_factory, tmp_alpha_png):
    in_img = tmp_path_factory.mktemp("png_rgba8") / "in.png"
    subprocess.check_call(
        CONVERT + [str(tmp_alpha_png), "-depth", "8", "-strip", str(in_img)]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColorAlpha", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "6"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "6 (RGBA)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def png_rgba16_img(tmp_alpha_png):
    in_img = tmp_alpha_png
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColorAlpha", str(identify)
    assert identify[0]["image"].get("depth") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig")
        == "16"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "16"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "6"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "6 (RGBA)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return in_img


@pytest.fixture(scope="session")
def png_gray8a_img(tmp_path_factory, tmp_alpha_png):
    in_img = tmp_path_factory.mktemp("png_gray8a") / "in.png"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_alpha_png),
            "-colorspace",
            "Gray",
            "-dither",
            "FloydSteinberg",
            "-colors",
            "256",
            "-depth",
            "8",
            "-strip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "GrayscaleAlpha", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "4"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "4 (GrayAlpha)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def png_gray16a_img(tmp_path_factory, tmp_alpha_png):
    in_img = tmp_path_factory.mktemp("png_gray16a") / "in.png"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_alpha_png),
            "-colorspace",
            "Gray",
            "-depth",
            "16",
            "-strip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "GrayscaleAlpha", str(identify)
    assert identify[0]["image"].get("depth") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig")
        == "16"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "16"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "4"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "4 (GrayAlpha)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def png_interlaced_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("png_interlaced") / "in.png"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_normal_png),
            "-interlace",
            "PNG",
            "-strip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "2"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "2 (Truecolor)"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.interlace_method")
        == "1 (Adam7 method)"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def png_gray1_img(tmp_path_factory, tmp_gray1_png):
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(tmp_gray1_png), "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") in ["Bilevel", "Grayscale"], str(identify)
    assert identify[0]["image"].get("depth") == 1, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "1"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "1"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "0"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "0 (Grayscale)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return tmp_gray1_png


@pytest.fixture(scope="session")
def png_gray2_img(tmp_path_factory, tmp_gray2_png):
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(tmp_gray2_png), "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Grayscale", str(identify)
    assert identify[0]["image"].get("depth") == 2, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "2"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "2"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "0"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "0 (Grayscale)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return tmp_gray2_png


@pytest.fixture(scope="session")
def png_gray4_img(tmp_path_factory, tmp_gray4_png):
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(tmp_gray4_png), "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Grayscale", str(identify)
    assert identify[0]["image"].get("depth") == 4, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "4"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "4"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "0"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "0 (Grayscale)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return tmp_gray4_png


@pytest.fixture(scope="session")
def png_gray8_img(tmp_path_factory, tmp_gray8_png):
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(tmp_gray8_png), "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Grayscale", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "0"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "0 (Grayscale)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return tmp_gray8_png


@pytest.fixture(scope="session")
def png_gray16_img(tmp_path_factory, tmp_gray16_png):
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(tmp_gray16_png), "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Grayscale", str(identify)
    assert identify[0]["image"].get("depth") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig")
        == "16"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "16"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "0"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "0 (Grayscale)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return tmp_gray16_png


@pytest.fixture(scope="session")
def png_palette1_img(tmp_path_factory, tmp_palette1_png):
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(tmp_palette1_png), "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "1"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "1"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "3"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "3 (Indexed)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return tmp_palette1_png


@pytest.fixture(scope="session")
def png_palette2_img(tmp_path_factory, tmp_palette2_png):
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(tmp_palette2_png), "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "2"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "2"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "3"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "3 (Indexed)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return tmp_palette2_png


@pytest.fixture(scope="session")
def png_palette4_img(tmp_path_factory, tmp_palette4_png):
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(tmp_palette4_png), "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "4"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "4"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "3"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "3 (Indexed)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return tmp_palette4_png


@pytest.fixture(scope="session")
def png_palette8_img(tmp_path_factory, tmp_palette8_png):
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(tmp_palette8_png), "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "3"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "3 (Indexed)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return tmp_palette8_png


@pytest.fixture(scope="session")
def gif_transparent_img(tmp_path_factory, tmp_alpha_png):
    in_img = tmp_path_factory.mktemp("gif_transparent_img") / "in.gif"
    subprocess.check_call(CONVERT + [str(tmp_alpha_png), str(in_img)])
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "GIF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/gif", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "PaletteAlpha", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("colormapEntries") == 256, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "LZW", str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def gif_palette1_img(tmp_path_factory, tmp_palette1_png):
    in_img = tmp_path_factory.mktemp("gif_palette1_img") / "in.gif"
    subprocess.check_call(CONVERT + [str(tmp_palette1_png), str(in_img)])
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "GIF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/gif", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("colormapEntries") == 2, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "LZW", str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def gif_palette2_img(tmp_path_factory, tmp_palette2_png):
    in_img = tmp_path_factory.mktemp("gif_palette2_img") / "in.gif"
    subprocess.check_call(CONVERT + [str(tmp_palette2_png), str(in_img)])
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "GIF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/gif", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("colormapEntries") == 4, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "LZW", str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def gif_palette4_img(tmp_path_factory, tmp_palette4_png):
    in_img = tmp_path_factory.mktemp("gif_palette4_img") / "in.gif"
    subprocess.check_call(CONVERT + [str(tmp_palette4_png), str(in_img)])
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "GIF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/gif", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("colormapEntries") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "LZW", str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def gif_palette8_img(tmp_path_factory, tmp_palette8_png):
    in_img = tmp_path_factory.mktemp("gif_palette8_img") / "in.gif"
    subprocess.check_call(CONVERT + [str(tmp_palette8_png), str(in_img)])
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "GIF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/gif", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("colormapEntries") == 256, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "LZW", str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def gif_animation_img(tmp_path_factory, tmp_normal_png, tmp_inverse_png):
    in_img = tmp_path_factory.mktemp("gif_animation_img") / "in.gif"
    pal_img = tmp_path_factory.mktemp("gif_animation_img") / "pal.gif"
    tmp_img = tmp_path_factory.mktemp("gif_animation_img") / "tmp.gif"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_normal_png),
            str(tmp_inverse_png),
            str(tmp_img),
        ]
    )
    # create palette image with all unique colors
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_img),
            "-unique-colors",
            str(pal_img),
        ]
    )
    # make sure all frames have the same palette by using -remap
    subprocess.check_call(
        CONVERT + [str(tmp_img), "-strip", "-remap", str(pal_img), str(in_img)]
    )
    pal_img.unlink()
    tmp_img.unlink()
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(in_img) + "[0]", "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "GIF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/gif", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("colormapEntries") == 256, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "LZW", str(identify)
    colormap_frame0 = identify[0]["image"].get("colormap")
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(in_img) + "[1]", "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "GIF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/gif", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("colormapEntries") == 256, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "LZW", str(identify)
    assert identify[0]["image"].get("scene") == 1, str(identify)
    colormap_frame1 = identify[0]["image"].get("colormap")
    assert colormap_frame0 == colormap_frame1
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_float_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("tiff_float_img") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_normal_png),
            "-depth",
            "32",
            "-define",
            "quantum:format=floating-point",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("baseDepth") == 32, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("quantum:format")
        == "floating-point"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "RGB"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_cmyk8_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("tiff_cmyk8") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_normal_png),
            "-colorspace",
            "cmyk",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "CMYK", str(identify)
    assert identify[0]["image"].get("type") == "ColorSeparation", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "separated"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_cmyk16_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("tiff_cmyk16") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_normal_png),
            "-depth",
            "16",
            "-colorspace",
            "cmyk",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "CMYK", str(identify)
    assert identify[0]["image"].get("type") == "ColorSeparation", str(identify)
    assert identify[0]["image"].get("depth") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "separated"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_rgb8_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("tiff_rgb8") / "in.tiff"
    subprocess.check_call(
        CONVERT + [str(tmp_normal_png), "-compress", "Zip", str(in_img)]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "RGB"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_rgb12_img(tmp_path_factory, tmp_normal16_png):
    in_img = tmp_path_factory.mktemp("tiff_rgb8") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_normal16_png),
            "-depth",
            "12",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("baseDepth") == 12, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "RGB"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_rgb14_img(tmp_path_factory, tmp_normal16_png):
    in_img = tmp_path_factory.mktemp("tiff_rgb8") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_normal16_png),
            "-depth",
            "14",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("baseDepth") == 14, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "RGB"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_rgb16_img(tmp_path_factory, tmp_normal16_png):
    in_img = tmp_path_factory.mktemp("tiff_rgb8") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_normal16_png),
            "-depth",
            "16",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "RGB"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_rgba8_img(tmp_path_factory, tmp_alpha_png):
    in_img = tmp_path_factory.mktemp("tiff_rgba8") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_alpha_png),
            "-depth",
            "8",
            "-strip",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColorAlpha", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unassociated"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "RGB"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_rgba16_img(tmp_path_factory, tmp_alpha_png):
    in_img = tmp_path_factory.mktemp("tiff_rgba16") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_alpha_png),
            "-depth",
            "16",
            "-strip",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColorAlpha", str(identify)
    assert identify[0]["image"].get("depth") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unassociated"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "RGB"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_gray1_img(tmp_path_factory, tmp_gray1_png):
    in_img = tmp_path_factory.mktemp("tiff_gray1") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_gray1_png),
            "-depth",
            "1",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Bilevel", str(identify)
    assert identify[0]["image"].get("depth") == 1, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "min-is-black"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_gray2_img(tmp_path_factory, tmp_gray2_png):
    in_img = tmp_path_factory.mktemp("tiff_gray2") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_gray2_png),
            "-depth",
            "2",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Grayscale", str(identify)
    assert identify[0]["image"].get("depth") == 2, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "min-is-black"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_gray4_img(tmp_path_factory, tmp_gray4_png):
    in_img = tmp_path_factory.mktemp("tiff_gray4") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_gray4_png),
            "-depth",
            "4",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Grayscale", str(identify)
    assert identify[0]["image"].get("depth") == 4, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "min-is-black"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_gray8_img(tmp_path_factory, tmp_gray8_png):
    in_img = tmp_path_factory.mktemp("tiff_gray8") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_gray8_png),
            "-depth",
            "8",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Grayscale", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "min-is-black"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_gray16_img(tmp_path_factory, tmp_gray16_png):
    in_img = tmp_path_factory.mktemp("tiff_gray16") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_gray16_png),
            "-depth",
            "16",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Grayscale", str(identify)
    assert identify[0]["image"].get("depth") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "min-is-black"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_multipage_img(tmp_path_factory, tmp_normal_png, tmp_inverse_png):
    in_img = tmp_path_factory.mktemp("tiff_multipage_img") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_normal_png),
            str(tmp_inverse_png),
            "-strip",
            "-compress",
            "Zip",
            str(in_img),
        ]
    )
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(in_img) + "[0]", "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "RGB"
    ), str(identify)
    identify = json.loads(
        subprocess.check_output(CONVERT + [str(in_img) + "[1]", "json:"])
    )
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "RGB"
    ), str(identify)
    assert identify[0]["image"].get("scene") == 1, str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_palette1_img(tmp_path_factory, tmp_palette1_png):
    in_img = tmp_path_factory.mktemp("tiff_palette1_img") / "in.tiff"
    subprocess.check_call(
        CONVERT + [str(tmp_palette1_png), "-compress", "Zip", str(in_img)]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("baseDepth") == 1, str(identify)
    assert identify[0]["image"].get("colormapEntries") == 2, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "palette"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_palette2_img(tmp_path_factory, tmp_palette2_png):
    in_img = tmp_path_factory.mktemp("tiff_palette2_img") / "in.tiff"
    subprocess.check_call(
        CONVERT + [str(tmp_palette2_png), "-compress", "Zip", str(in_img)]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("baseDepth") == 2, str(identify)
    assert identify[0]["image"].get("colormapEntries") == 4, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "palette"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_palette4_img(tmp_path_factory, tmp_palette4_png):
    in_img = tmp_path_factory.mktemp("tiff_palette4_img") / "in.tiff"
    subprocess.check_call(
        CONVERT + [str(tmp_palette4_png), "-compress", "Zip", str(in_img)]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("baseDepth") == 4, str(identify)
    assert identify[0]["image"].get("colormapEntries") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "palette"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_palette8_img(tmp_path_factory, tmp_palette8_png):
    in_img = tmp_path_factory.mktemp("tiff_palette8_img") / "in.tiff"
    subprocess.check_call(
        CONVERT + [str(tmp_palette8_png), "-compress", "Zip", str(in_img)]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "Palette", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("colormapEntries") == 256, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric") == "palette"
    ), str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_ccitt_lsb_m2l_white_img(tmp_path_factory, tmp_gray1_png):
    in_img = tmp_path_factory.mktemp("tiff_ccitt_lsb_m2l_white_img") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_gray1_png),
            "-compress",
            "group4",
            "-define",
            "tiff:endian=lsb",
            "-define",
            "tiff:fill-order=msb",
            "-define",
            "quantum:polarity=min-is-white",
            "-compress",
            "Group4",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Bilevel", str(identify)
    endian = "endianess" if identify[0].get("version", "0") < "1.0" else "endianness"
    assert identify[0]["image"].get(endian) in [
        "Undefined",
        "LSB",
    ], str(identify)
    assert identify[0]["image"].get("depth") == 1, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "Group4", str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert identify[0]["image"].get("properties", {}).get("tiff:endian") == "lsb", str(
        identify
    )
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "min-is-white"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:rows-per-strip") == "60"
    ), str(identify)
    tiffinfo = subprocess.check_output(["tiffinfo", str(in_img)])
    expected = [
        r"^  Image Width: 60 Image Length: 60",
        r"^  Bits/Sample: 1",
        r"^  Compression Scheme: CCITT Group 4",
        r"^  Photometric Interpretation: min-is-white",
        r"^  FillOrder: msb-to-lsb",
        r"^  Samples/Pixel: 1",
        r"^  Rows/Strip: 60",
    ]
    for e in expected:
        assert re.search(e, tiffinfo.decode("utf8"), re.MULTILINE), identify.decode(
            "utf8"
        )
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_ccitt_msb_m2l_white_img(tmp_path_factory, tmp_gray1_png):
    in_img = tmp_path_factory.mktemp("tiff_ccitt_msb_m2l_white_img") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_gray1_png),
            "-compress",
            "group4",
            "-define",
            "tiff:endian=msb",
            "-define",
            "tiff:fill-order=msb",
            "-define",
            "quantum:polarity=min-is-white",
            "-compress",
            "Group4",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Bilevel", str(identify)
    endian = "endianess" if identify[0].get("version", "0") < "1.0" else "endianness"
    assert identify[0]["image"].get(endian) in [
        "Undefined",
        "MSB",
    ]  # FIXME: should be MSB
    assert identify[0]["image"].get("depth") == 1, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "Group4", str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert identify[0]["image"].get("properties", {}).get("tiff:endian") == "msb", str(
        identify
    )
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "min-is-white"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:rows-per-strip") == "60"
    ), str(identify)
    tiffinfo = subprocess.check_output(["tiffinfo", str(in_img)])
    expected = [
        r"^  Image Width: 60 Image Length: 60",
        r"^  Bits/Sample: 1",
        r"^  Compression Scheme: CCITT Group 4",
        r"^  Photometric Interpretation: min-is-white",
        r"^  FillOrder: msb-to-lsb",
        r"^  Samples/Pixel: 1",
        r"^  Rows/Strip: 60",
    ]
    for e in expected:
        assert re.search(e, tiffinfo.decode("utf8"), re.MULTILINE), identify.decode(
            "utf8"
        )
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_ccitt_msb_l2m_white_img(tmp_path_factory, tmp_gray1_png):
    in_img = tmp_path_factory.mktemp("tiff_ccitt_msb_l2m_white_img") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_gray1_png),
            "-compress",
            "group4",
            "-define",
            "tiff:endian=msb",
            "-define",
            "tiff:fill-order=lsb",
            "-define",
            "quantum:polarity=min-is-white",
            "-compress",
            "Group4",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Bilevel", str(identify)
    endian = "endianess" if identify[0].get("version", "0") < "1.0" else "endianness"
    assert identify[0]["image"].get(endian) in [
        "Undefined",
        "MSB",
    ]  # FIXME: should be MSB
    assert identify[0]["image"].get("depth") == 1, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "Group4", str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert identify[0]["image"].get("properties", {}).get("tiff:endian") == "msb", str(
        identify
    )
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "min-is-white"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:rows-per-strip") == "60"
    ), str(identify)
    tiffinfo = subprocess.check_output(["tiffinfo", str(in_img)])
    expected = [
        r"^  Image Width: 60 Image Length: 60",
        r"^  Bits/Sample: 1",
        r"^  Compression Scheme: CCITT Group 4",
        r"^  Photometric Interpretation: min-is-white",
        r"^  FillOrder: lsb-to-msb",
        r"^  Samples/Pixel: 1",
        r"^  Rows/Strip: 60",
    ]
    for e in expected:
        assert re.search(e, tiffinfo.decode("utf8"), re.MULTILINE), identify.decode(
            "utf8"
        )
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_ccitt_lsb_m2l_black_img(tmp_path_factory, tmp_gray1_png):
    in_img = tmp_path_factory.mktemp("tiff_ccitt_lsb_m2l_black_img") / "in.tiff"
    # "-define quantum:polarity=min-is-black" requires ImageMagick with:
    # https://github.com/ImageMagick/ImageMagick/commit/00730551f0a34328685c59d0dde87dd9e366103a
    # or at least 7.0.8-11 from Aug 29, 2018
    # or at least 6.9.10-12 from Sep 7, 2018 (for the ImageMagick6 branch)
    # also see: https://www.imagemagick.org/discourse-server/viewtopic.php?f=1&t=34605
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_gray1_png),
            "-compress",
            "group4",
            "-define",
            "tiff:endian=lsb",
            "-define",
            "tiff:fill-order=msb",
            "-define",
            "quantum:polarity=min-is-black",
            "-compress",
            "Group4",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Bilevel", str(identify)
    endian = "endianess" if identify[0].get("version", "0") < "1.0" else "endianness"
    assert identify[0]["image"].get(endian) in [
        "Undefined",
        "LSB",
    ], str(identify)
    assert identify[0]["image"].get("depth") == 1, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "Group4", str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert identify[0]["image"].get("properties", {}).get("tiff:endian") == "lsb", str(
        identify
    )
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "min-is-black"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:rows-per-strip") == "60"
    ), str(identify)
    tiffinfo = subprocess.check_output(["tiffinfo", str(in_img)])
    expected = [
        r"^  Image Width: 60 Image Length: 60",
        r"^  Bits/Sample: 1",
        r"^  Compression Scheme: CCITT Group 4",
        r"^  Photometric Interpretation: min-is-black",
        r"^  FillOrder: msb-to-lsb",
        r"^  Samples/Pixel: 1",
        r"^  Rows/Strip: 60",
    ]
    for e in expected:
        assert re.search(e, tiffinfo.decode("utf8"), re.MULTILINE), identify.decode(
            "utf8"
        )
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_ccitt_nometa1_img(tmp_path_factory, tmp_gray1_png):
    in_img = tmp_path_factory.mktemp("tiff_ccitt_nometa1_img") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_gray1_png),
            "-compress",
            "group4",
            "-define",
            "tiff:endian=lsb",
            "-define",
            "tiff:fill-order=msb",
            "-define",
            "quantum:polarity=min-is-white",
            "-compress",
            "Group4",
            str(in_img),
        ]
    )
    subprocess.check_call(
        ["tiffset", "-u", "258", str(in_img)]
    )  # remove BitsPerSample (258)
    subprocess.check_call(
        ["tiffset", "-u", "266", str(in_img)]
    )  # remove FillOrder (266)
    subprocess.check_call(
        ["tiffset", "-u", "277", str(in_img)]
    )  # remove SamplesPerPixel (277)
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("type") == "Bilevel", str(identify)
    endian = "endianess" if identify[0].get("version", "0") < "1.0" else "endianness"
    assert identify[0]["image"].get(endian) in [
        "Undefined",
        "LSB",
    ], str(identify)
    assert identify[0]["image"].get("depth") == 1, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("compression") == "Group4", str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert identify[0]["image"].get("properties", {}).get("tiff:endian") == "lsb", str(
        identify
    )
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "min-is-white"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:rows-per-strip") == "60"
    ), str(identify)
    tiffinfo = subprocess.check_output(["tiffinfo", str(in_img)])
    expected = [
        r"^  Image Width: 60 Image Length: 60",
        r"^  Compression Scheme: CCITT Group 4",
        r"^  Photometric Interpretation: min-is-white",
        r"^  Rows/Strip: 60",
    ]
    for e in expected:
        assert re.search(e, tiffinfo.decode("utf8"), re.MULTILINE), identify.decode(
            "utf8"
        )
    unexpected = [" Bits/Sample: ", " FillOrder: ", " Samples/Pixel: "]
    for e in unexpected:
        assert e not in tiffinfo.decode("utf8")
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def tiff_ccitt_nometa2_img(tmp_path_factory, tmp_gray1_png):
    in_img = tmp_path_factory.mktemp("tiff_ccitt_nometa2_img") / "in.tiff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_gray1_png),
            "-compress",
            "group4",
            "-define",
            "tiff:endian=lsb",
            "-define",
            "tiff:fill-order=msb",
            "-define",
            "quantum:polarity=min-is-white",
            "-compress",
            "Group4",
            str(in_img),
        ]
    )
    subprocess.check_call(
        ["tiffset", "-u", "278", str(in_img)]
    )  # remove RowsPerStrip (278)
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "TIFF", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/tiff", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("units") == "PixelsPerInch", str(identify)
    assert identify[0]["image"].get("type") == "Bilevel", str(identify)
    endian = "endianess" if identify[0].get("version", "0") < "1.0" else "endianness"
    assert identify[0]["image"].get(endian) in [
        "Undefined",
        "LSB",
    ], str(identify)
    assert identify[0]["image"].get("colorspace") == "Gray", str(identify)
    assert identify[0]["image"].get("depth") == 1, str(identify)
    assert identify[0]["image"].get("compression") == "Group4", str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:alpha") == "unspecified"
    ), str(identify)
    assert identify[0]["image"].get("properties", {}).get("tiff:endian") == "lsb", str(
        identify
    )
    assert (
        identify[0]["image"].get("properties", {}).get("tiff:photometric")
        == "min-is-white"
    ), str(identify)
    assert "tiff:rows-per-strip" not in identify[0]["image"]["properties"]
    tiffinfo = subprocess.check_output(["tiffinfo", str(in_img)])
    expected = [
        r"^  Image Width: 60 Image Length: 60",
        r"^  Bits/Sample: 1",
        r"^  Compression Scheme: CCITT Group 4",
        r"^  Photometric Interpretation: min-is-white",
        r"^  FillOrder: msb-to-lsb",
        r"^  Samples/Pixel: 1",
    ]
    for e in expected:
        assert re.search(e, tiffinfo.decode("utf8"), re.MULTILINE), identify.decode(
            "utf8"
        )
    unexpected = [" Rows/Strip: "]
    for e in unexpected:
        assert e not in tiffinfo.decode("utf8")
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def miff_cmyk8_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("miff_cmyk8") / "in.miff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_normal_png),
            "-colorspace",
            "cmyk",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "MIFF", str(identify)
    assert identify[0]["image"].get("class") == "DirectClass"
    assert identify[0]["image"].get("type") == "ColorSeparation"
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "CMYK", str(identify)
    assert identify[0]["image"].get("type") == "ColorSeparation", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def miff_cmyk16_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("miff_cmyk16") / "in.miff"
    subprocess.check_call(
        CONVERT
        + [
            str(tmp_normal_png),
            "-depth",
            "16",
            "-colorspace",
            "cmyk",
            str(in_img),
        ]
    )
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "MIFF", str(identify)
    assert identify[0]["image"].get("class") == "DirectClass"
    assert identify[0]["image"].get("type") == "ColorSeparation"
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "CMYK", str(identify)
    assert identify[0]["image"].get("type") == "ColorSeparation", str(identify)
    assert identify[0]["image"].get("depth") == 16, str(identify)
    assert identify[0]["image"].get("baseDepth") == 16, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def miff_rgb8_img(tmp_path_factory, tmp_normal_png):
    in_img = tmp_path_factory.mktemp("miff_rgb8") / "in.miff"
    subprocess.check_call(CONVERT + [str(tmp_normal_png), str(in_img)])
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "MIFF", str(identify)
    assert identify[0]["image"].get("class") == "DirectClass"
    assert identify[0]["image"].get("type") == "TrueColor"
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    yield in_img
    in_img.unlink()


@pytest.fixture(scope="session")
def png_icc_img(tmp_icc_png):
    in_img = tmp_icc_png
    identify = json.loads(subprocess.check_output(CONVERT + [str(in_img), "json:"]))
    assert len(identify) == 1
    # somewhere between imagemagick 6.9.7.4 and 6.9.9.34, the json output was
    # put into an array, here we cater for the older version containing just
    # the bare dictionary
    if "image" in identify:
        identify = [identify]
    assert "image" in identify[0]
    assert identify[0]["image"].get("format") == "PNG", str(identify)
    assert identify[0]["image"].get("mimeType") == "image/png", str(identify)
    assert identify[0]["image"].get("geometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert identify[0]["image"].get("colorspace") == "sRGB", str(identify)
    assert identify[0]["image"].get("type") == "TrueColor", str(identify)
    assert identify[0]["image"].get("depth") == 8, str(identify)
    assert identify[0]["image"].get("pageGeometry") == {
        "width": 60,
        "height": 60,
        "x": 0,
        "y": 0,
    }, str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit-depth-orig") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.bit_depth") == "8"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color-type-orig")
        == "2"
    ), str(identify)
    assert (
        identify[0]["image"].get("properties", {}).get("png:IHDR.color_type")
        == "2 (Truecolor)"
    ), str(identify)
    assert (
        identify[0]["image"]["properties"]["png:IHDR.interlace_method"]
        == "0 (Not interlaced)"
    ), str(identify)
    return in_img


###############################################################################
#                                OUTPUT FIXTURES                              #
###############################################################################


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def jpg_pdf(tmp_path_factory, jpg_img, request):
    out_pdf = tmp_path_factory.mktemp("jpg_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(jpg_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.Filter == "/DCTDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def jpg_rot_pdf(tmp_path_factory, jpg_rot_img, request):
    out_pdf = tmp_path_factory.mktemp("jpg_rot_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(jpg_rot_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.Filter == "/DCTDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
        assert p.pages[0].Rotate == 90
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def jpg_cmyk_pdf(tmp_path_factory, jpg_cmyk_img, request):
    out_pdf = tmp_path_factory.mktemp("jpg_cmyk_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(jpg_cmyk_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceCMYK"
        assert p.pages[0].Resources.XObject.Im0.Decode == pikepdf.Array(
            [1, 0, 1, 0, 1, 0, 1, 0]
        )
        assert p.pages[0].Resources.XObject.Im0.Filter == "/DCTDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def jpg_2000_pdf(tmp_path_factory, jpg_2000_img, request):
    out_pdf = tmp_path_factory.mktemp("jpg_2000_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            jpg_2000_img,
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.Filter == "/JPXDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def jpg_2000_rgba8_pdf(tmp_path_factory, jpg_2000_rgba8_img, request):
    out_pdf = tmp_path_factory.mktemp("jpg_2000_rgba8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            jpg_2000_rgba8_img,
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert not hasattr(p.pages[0].Resources.XObject.Im0, "ColorSpace")
        assert p.pages[0].Resources.XObject.Im0.Filter == "/JPXDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def jpg_2000_rgba16_pdf(tmp_path_factory, jpg_2000_rgba16_img, request):
    out_pdf = tmp_path_factory.mktemp("jpg_2000_rgba16_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            jpg_2000_rgba16_img,
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 16
        assert not hasattr(p.pages[0].Resources.XObject.Im0, "ColorSpace")
        assert p.pages[0].Resources.XObject.Im0.Filter == "/JPXDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_rgb8_pdf(tmp_path_factory, png_rgb8_img, request):
    out_pdf = tmp_path_factory.mktemp("png_rgb8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(png_rgb8_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 3
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_rgba8_pdf(tmp_path_factory, png_rgba8_img, request):
    out_pdf = tmp_path_factory.mktemp("png_rgba8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(png_rgba8_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 3
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
        assert p.pages[0].Resources.XObject.Im0.SMask is not None

        assert p.pages[0].Resources.XObject.Im0.SMask.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.SMask.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.SMask.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.SMask.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.SMask.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.SMask.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.SMask.Height == 60
        assert p.pages[0].Resources.XObject.Im0.SMask.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def gif_transparent_pdf(tmp_path_factory, gif_transparent_img, request):
    out_pdf = tmp_path_factory.mktemp("gif_transparent_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(gif_transparent_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
        assert p.pages[0].Resources.XObject.Im0.SMask is not None

        assert p.pages[0].Resources.XObject.Im0.SMask.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.SMask.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.SMask.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.SMask.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.SMask.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.SMask.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.SMask.Height == 60
        assert p.pages[0].Resources.XObject.Im0.SMask.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_rgb16_pdf(tmp_path_factory, png_rgb16_img, request):
    out_pdf = tmp_path_factory.mktemp("png_rgb16_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(png_rgb16_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 16
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 16
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 3
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_interlaced_pdf(tmp_path_factory, png_interlaced_img, request):
    out_pdf = tmp_path_factory.mktemp("png_interlaced_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(png_interlaced_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 3
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_gray1_pdf(tmp_path_factory, tmp_gray1_png, request):
    out_pdf = tmp_path_factory.mktemp("png_gray1_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tmp_gray1_png),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_gray2_pdf(tmp_path_factory, tmp_gray2_png, request):
    out_pdf = tmp_path_factory.mktemp("png_gray2_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tmp_gray2_png),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 2
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 2
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_gray4_pdf(tmp_path_factory, tmp_gray4_png, request):
    out_pdf = tmp_path_factory.mktemp("png_gray4_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tmp_gray4_png),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 4
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 4
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_gray8_pdf(tmp_path_factory, tmp_gray8_png, request):
    out_pdf = tmp_path_factory.mktemp("png_gray8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tmp_gray8_png),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_gray8a_pdf(tmp_path_factory, png_gray8a_img, request):
    out_pdf = tmp_path_factory.mktemp("png_gray8a_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(png_gray8a_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
        assert p.pages[0].Resources.XObject.Im0.SMask is not None

        assert p.pages[0].Resources.XObject.Im0.SMask.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.SMask.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.SMask.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.SMask.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.SMask.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.SMask.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.SMask.Height == 60
        assert p.pages[0].Resources.XObject.Im0.SMask.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_gray16_pdf(tmp_path_factory, tmp_gray16_png, request):
    out_pdf = tmp_path_factory.mktemp("png_gray16_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tmp_gray16_png),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 16
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 16
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_palette1_pdf(tmp_path_factory, tmp_palette1_png, request):
    out_pdf = tmp_path_factory.mktemp("png_palette1_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tmp_palette1_png),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_palette2_pdf(tmp_path_factory, tmp_palette2_png, request):
    out_pdf = tmp_path_factory.mktemp("png_palette2_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tmp_palette2_png),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 2
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 2
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_palette4_pdf(tmp_path_factory, tmp_palette4_png, request):
    out_pdf = tmp_path_factory.mktemp("png_palette4_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tmp_palette4_png),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 4
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 4
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_palette8_pdf(tmp_path_factory, tmp_palette8_png, request):
    out_pdf = tmp_path_factory.mktemp("png_palette8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tmp_palette8_png),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def png_icc_pdf(tmp_path_factory, tmp_icc_png, tmp_icc_profile, request):
    out_pdf = tmp_path_factory.mktemp("png_icc_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tmp_icc_png),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/ICCBased"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1].N == 3
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1].Alternate == "/DeviceRGB"
        assert (
            p.pages[0].Resources.XObject.Im0.ColorSpace[1].read_bytes()
            == tmp_icc_profile.read_bytes()
        )
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 3
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def gif_palette1_pdf(tmp_path_factory, gif_palette1_img, request):
    out_pdf = tmp_path_factory.mktemp("gif_palette1_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(gif_palette1_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def gif_palette2_pdf(tmp_path_factory, gif_palette2_img, request):
    out_pdf = tmp_path_factory.mktemp("gif_palette2_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(gif_palette2_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 2
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 2
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def gif_palette4_pdf(tmp_path_factory, gif_palette4_img, request):
    out_pdf = tmp_path_factory.mktemp("gif_palette4_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(gif_palette4_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 4
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 4
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def gif_palette8_pdf(tmp_path_factory, gif_palette8_img, request):
    out_pdf = tmp_path_factory.mktemp("gif_palette8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(gif_palette8_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def gif_animation_pdf(tmp_path_factory, gif_animation_img, request):
    tmpdir = tmp_path_factory.mktemp("gif_animation_pdf")
    out_pdf = tmpdir / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(gif_animation_img),
        ]
    )
    pdfinfo = subprocess.check_output(["pdfinfo", str(out_pdf)])
    assert re.search(
        "^Pages: +2$", pdfinfo.decode("utf8"), re.MULTILINE
    ), identify.decode("utf8")
    subprocess.check_call(["pdfseparate", str(out_pdf), str(tmpdir / "page-%d.pdf")])
    for page in [1, 2]:
        gif_animation_pdf_nr = tmpdir / ("page-%d.pdf" % page)
        with pikepdf.open(gif_animation_pdf_nr) as p:
            assert (
                p.pages[0].Contents.read_bytes()
                == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
            )
            assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
            assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
            assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
            assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
            assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
            assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
            assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
            assert p.pages[0].Resources.XObject.Im0.Height == 60
            assert p.pages[0].Resources.XObject.Im0.Width == 60
        gif_animation_pdf_nr.unlink()
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_cmyk8_pdf(tmp_path_factory, tiff_cmyk8_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_cmyk8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_cmyk8_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceCMYK"
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_rgb8_pdf(tmp_path_factory, tiff_rgb8_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_rgb8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_rgb8_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 3
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_gray1_pdf(tmp_path_factory, tiff_gray1_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_gray1_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_gray1_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].BlackIs1 == True
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Columns == 60
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].K == -1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Rows == 60
        assert p.pages[0].Resources.XObject.Im0.Filter[0] == "/CCITTFaxDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_gray2_pdf(tmp_path_factory, tiff_gray2_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_gray2_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_gray2_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_gray4_pdf(tmp_path_factory, tiff_gray4_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_gray4_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_gray4_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_gray8_pdf(tmp_path_factory, tiff_gray8_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_gray8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_gray8_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_multipage_pdf(tmp_path_factory, tiff_multipage_img, request):
    tmpdir = tmp_path_factory.mktemp("tiff_multipage_pdf")
    out_pdf = tmpdir / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_multipage_img),
        ]
    )
    pdfinfo = subprocess.check_output(["pdfinfo", str(out_pdf)])
    assert re.search(
        "^Pages: +2$", pdfinfo.decode("utf8"), re.MULTILINE
    ), identify.decode("utf8")
    subprocess.check_call(["pdfseparate", str(out_pdf), str(tmpdir / "page-%d.pdf")])
    for page in [1, 2]:
        tiff_multipage_pdf_nr = tmpdir / ("page-%d.pdf" % page)
        with pikepdf.open(tiff_multipage_pdf_nr) as p:
            assert (
                p.pages[0].Contents.read_bytes()
                == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
            )
            assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
            assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceRGB"
            assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
            assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 3
            assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
            assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
            assert p.pages[0].Resources.XObject.Im0.Height == 60
            assert p.pages[0].Resources.XObject.Im0.Width == 60
        tiff_multipage_pdf_nr.unlink()
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_palette1_pdf(tmp_path_factory, tiff_palette1_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_palette1_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_palette1_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_palette2_pdf(tmp_path_factory, tiff_palette2_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_palette2_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_palette2_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 2
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 2
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_palette4_pdf(tmp_path_factory, tiff_palette4_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_palette4_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_palette4_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 4
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 4
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_palette8_pdf(tmp_path_factory, tiff_palette8_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_palette8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_palette8_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[0] == "/Indexed"
        assert p.pages[0].Resources.XObject.Im0.ColorSpace[1] == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_ccitt_lsb_m2l_white_pdf(
    tmp_path_factory, tiff_ccitt_lsb_m2l_white_img, request
):
    out_pdf = tmp_path_factory.mktemp("tiff_ccitt_lsb_m2l_white_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_ccitt_lsb_m2l_white_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].BlackIs1 == False
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Columns == 60
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].K == -1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Rows == 60
        assert p.pages[0].Resources.XObject.Im0.Filter[0] == "/CCITTFaxDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_ccitt_msb_m2l_white_pdf(
    tmp_path_factory, tiff_ccitt_msb_m2l_white_img, request
):
    out_pdf = tmp_path_factory.mktemp("tiff_ccitt_msb_m2l_white_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_ccitt_msb_m2l_white_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].BlackIs1 == False
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Columns == 60
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].K == -1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Rows == 60
        assert p.pages[0].Resources.XObject.Im0.Filter[0] == "/CCITTFaxDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_ccitt_msb_l2m_white_pdf(
    tmp_path_factory, tiff_ccitt_msb_l2m_white_img, request
):
    out_pdf = tmp_path_factory.mktemp("tiff_ccitt_msb_l2m_white_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_ccitt_msb_l2m_white_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].BlackIs1 == False
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Columns == 60
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].K == -1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Rows == 60
        assert p.pages[0].Resources.XObject.Im0.Filter[0] == "/CCITTFaxDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_ccitt_lsb_m2l_black_pdf(
    tmp_path_factory, tiff_ccitt_lsb_m2l_black_img, request
):
    out_pdf = tmp_path_factory.mktemp("tiff_ccitt_lsb_m2l_black_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_ccitt_lsb_m2l_black_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].BlackIs1 == True
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Columns == 60
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].K == -1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Rows == 60
        assert p.pages[0].Resources.XObject.Im0.Filter[0] == "/CCITTFaxDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_ccitt_nometa1_pdf(tmp_path_factory, tiff_ccitt_nometa1_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_ccitt_nometa1_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_ccitt_nometa1_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].BlackIs1 == False
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Columns == 60
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].K == -1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Rows == 60
        assert p.pages[0].Resources.XObject.Im0.Filter[0] == "/CCITTFaxDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def tiff_ccitt_nometa2_pdf(tmp_path_factory, tiff_ccitt_nometa2_img, request):
    out_pdf = tmp_path_factory.mktemp("tiff_ccitt_nometa2_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(tiff_ccitt_nometa2_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceGray"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].BlackIs1 == False
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Columns == 60
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].K == -1
        assert p.pages[0].Resources.XObject.Im0.DecodeParms[0].Rows == 60
        assert p.pages[0].Resources.XObject.Im0.Filter[0] == "/CCITTFaxDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def miff_cmyk8_pdf(tmp_path_factory, miff_cmyk8_img, request):
    out_pdf = tmp_path_factory.mktemp("miff_cmyk8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(miff_cmyk8_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceCMYK"
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def miff_cmyk16_pdf(tmp_path_factory, miff_cmyk16_img, request):
    out_pdf = tmp_path_factory.mktemp("miff_cmyk16_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(miff_cmyk16_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 16
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceCMYK"
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


@pytest.fixture(scope="session", params=["internal", "pikepdf"])
def miff_rgb8_pdf(tmp_path_factory, miff_rgb8_img, request):
    out_pdf = tmp_path_factory.mktemp("miff_rgb8_pdf") / "out.pdf"
    subprocess.check_call(
        [
            img2pdfprog,
            "--producer=",
            "--nodate",
            "--engine=" + request.param,
            "--output=" + str(out_pdf),
            str(miff_rgb8_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert (
            p.pages[0].Contents.read_bytes()
            == b"q\n45.0000 0 0 45.0000 0.0000 0.0000 cm\n/Im0 Do\nQ"
        )
        assert p.pages[0].Resources.XObject.Im0.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.ColorSpace == "/DeviceRGB"
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.BitsPerComponent == 8
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Colors == 3
        assert p.pages[0].Resources.XObject.Im0.DecodeParms.Predictor == 15
        assert p.pages[0].Resources.XObject.Im0.Filter == "/FlateDecode"
        assert p.pages[0].Resources.XObject.Im0.Height == 60
        assert p.pages[0].Resources.XObject.Im0.Width == 60
    yield out_pdf
    out_pdf.unlink()


###############################################################################
#                                  TEST CASES                                 #
###############################################################################


@pytest.mark.skipif(
    sys.platform in ["darwin", "win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_jpg(tmp_path_factory, jpg_img, jpg_pdf):
    tmpdir = tmp_path_factory.mktemp("jpg")
    pnm = tmpdir / "jpg.pnm"
    # We have to use jpegtopnm with the original JPG before being able to compare
    # it with imagemagick because imagemagick will decode the JPG slightly
    # differently than ghostscript, poppler and mupdf do it.
    # We have to use jpegtopnm and cannot use djpeg because the latter produces
    # slightly different results as well when called like this:
    #    djpeg -dct int -pnm "$tempdir/normal.jpg" > "$tempdir/normal.pnm"
    # An alternative way to compare the JPG would be to require a different DCT
    # method when decoding by setting -define jpeg:dct-method=ifast in the
    # compare command.
    pnm.write_bytes(subprocess.check_output(["jpegtopnm", "-dct", "int", str(jpg_img)]))
    compare_ghostscript(tmpdir, pnm, jpg_pdf)
    compare_poppler(tmpdir, pnm, jpg_pdf)
    compare_mupdf(tmpdir, pnm, jpg_pdf)
    pnm.unlink()
    compare_pdfimages_jpg(tmpdir, jpg_img, jpg_pdf)


@pytest.mark.skipif(
    sys.platform in ["darwin", "win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_jpg_rot(tmp_path_factory, jpg_rot_img, jpg_rot_pdf):
    tmpdir = tmp_path_factory.mktemp("jpg_rot")
    # We have to use jpegtopnm with the original JPG before being able to compare
    # it with imagemagick because imagemagick will decode the JPG slightly
    # differently than ghostscript, poppler and mupdf do it.
    # We have to use jpegtopnm and cannot use djpeg because the latter produces
    # slightly different results as well when called like this:
    #    djpeg -dct int -pnm "$tempdir/normal.jpg" > "$tempdir/normal.pnm"
    # An alternative way to compare the JPG would be to require a different DCT
    # method when decoding by setting -define jpeg:dct-method=ifast in the
    # compare command.
    jpg_rot_pnm = tmpdir / "jpg_rot.pnm"
    jpg_rot_pnm.write_bytes(
        subprocess.check_output(["jpegtopnm", "-dct", "int", str(jpg_rot_img)])
    )
    jpg_rot_png = tmpdir / "jpg_rot.png"
    subprocess.check_call(
        CONVERT + ["-rotate", "90", str(jpg_rot_pnm), str(jpg_rot_png)]
    )
    jpg_rot_pnm.unlink()
    compare_ghostscript(tmpdir, jpg_rot_png, jpg_rot_pdf)
    compare_poppler(tmpdir, jpg_rot_png, jpg_rot_pdf)
    compare_mupdf(tmpdir, jpg_rot_png, jpg_rot_pdf)
    jpg_rot_png.unlink()
    compare_pdfimages_jpg(tmpdir, jpg_rot_img, jpg_rot_pdf)


@pytest.mark.skipif(
    sys.platform in ["darwin", "win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_jpg_cmyk(tmp_path_factory, jpg_cmyk_img, jpg_cmyk_pdf):
    tmpdir = tmp_path_factory.mktemp("jpg_cmyk")
    compare_ghostscript(
        tmpdir, jpg_cmyk_img, jpg_cmyk_pdf, gsdevice="tiff32nc", exact=HAVE_EXACT_CMYK8
    )
    # not testing with poppler as it cannot write CMYK images
    compare_mupdf(tmpdir, jpg_cmyk_img, jpg_cmyk_pdf, exact=HAVE_EXACT_CMYK8, cmyk=True)
    compare_pdfimages_cmyk(tmpdir, jpg_cmyk_img, jpg_cmyk_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.skipif(
    not HAVE_JP2, reason="requires imagemagick with support for jpeg2000"
)
def test_jpg_2000(tmp_path_factory, jpg_2000_img, jpg_2000_pdf):
    tmpdir = tmp_path_factory.mktemp("jpg_2000")
    compare_ghostscript(tmpdir, jpg_2000_img, jpg_2000_pdf)
    compare_poppler(tmpdir, jpg_2000_img, jpg_2000_pdf)
    compare_mupdf(tmpdir, jpg_2000_img, jpg_2000_pdf)
    compare_pdfimages_jp2(tmpdir, jpg_2000_img, jpg_2000_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.skipif(
    not HAVE_JP2, reason="requires imagemagick with support for jpeg2000"
)
def test_jpg_2000_rgba8(tmp_path_factory, jpg_2000_rgba8_img, jpg_2000_rgba8_pdf):
    tmpdir = tmp_path_factory.mktemp("jpg_2000_rgba8")
    compare_ghostscript(tmpdir, jpg_2000_rgba8_img, jpg_2000_rgba8_pdf)
    compare_poppler(tmpdir, jpg_2000_rgba8_img, jpg_2000_rgba8_pdf)
    # compare_mupdf(tmpdir, jpg_2000_rgba8_img, jpg_2000_rgba8_pdf)
    compare_pdfimages_jp2(tmpdir, jpg_2000_rgba8_img, jpg_2000_rgba8_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.skipif(
    not HAVE_JP2, reason="requires imagemagick with support for jpeg2000"
)
def test_jpg_2000_rgba16(tmp_path_factory, jpg_2000_rgba16_img, jpg_2000_rgba16_pdf):
    tmpdir = tmp_path_factory.mktemp("jpg_2000_rgba16")
    compare_ghostscript(
        tmpdir, jpg_2000_rgba16_img, jpg_2000_rgba16_pdf, gsdevice="tiff48nc"
    )
    # poppler outputs 8-bit RGB so the comparison will not be exact
    # compare_poppler(tmpdir, jpg_2000_rgba16_img, jpg_2000_rgba16_pdf, exact=False)
    # compare_mupdf(tmpdir, jpg_2000_rgba16_img, jpg_2000_rgba16_pdf)
    compare_pdfimages_jp2(tmpdir, jpg_2000_rgba16_img, jpg_2000_rgba16_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_rgb8(tmp_path_factory, png_rgb8_img, png_rgb8_pdf):
    tmpdir = tmp_path_factory.mktemp("png_rgb8")
    compare_ghostscript(tmpdir, png_rgb8_img, png_rgb8_pdf)
    compare_poppler(tmpdir, png_rgb8_img, png_rgb8_pdf)
    compare_mupdf(tmpdir, png_rgb8_img, png_rgb8_pdf)
    compare_pdfimages_png(tmpdir, png_rgb8_img, png_rgb8_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_rgb16(tmp_path_factory, png_rgb16_img, png_rgb16_pdf):
    tmpdir = tmp_path_factory.mktemp("png_rgb16")
    compare_ghostscript(tmpdir, png_rgb16_img, png_rgb16_pdf, gsdevice="tiff48nc")
    # poppler outputs 8-bit RGB so the comparison will not be exact
    compare_poppler(tmpdir, png_rgb16_img, png_rgb16_pdf, exact=False)
    # pdfimages is unable to write 16 bit output


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_rgba8(tmp_path_factory, png_rgba8_img, png_rgba8_pdf):
    tmpdir = tmp_path_factory.mktemp("png_rgba8")
    compare_pdfimages_png(tmpdir, png_rgba8_img, png_rgba8_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.parametrize("engine", ["internal", "pikepdf"])
def test_png_rgba16(tmp_path_factory, png_rgba16_img, engine):
    out_pdf = tmp_path_factory.mktemp("png_rgba16") / "out.pdf"
    assert (
        0
        != subprocess.run(
            [
                img2pdfprog,
                "--producer=",
                "--nodate",
                "--engine=" + engine,
                "--output=" + str(out_pdf),
                str(png_rgba16_img),
            ]
        ).returncode
    )
    out_pdf.unlink()


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_gray8a(tmp_path_factory, png_gray8a_img, png_gray8a_pdf):
    tmpdir = tmp_path_factory.mktemp("png_gray8a")
    compare_pdfimages_png(tmpdir, png_gray8a_img, png_gray8a_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.parametrize("engine", ["internal", "pikepdf"])
def test_png_gray16a(tmp_path_factory, png_gray16a_img, engine):
    out_pdf = tmp_path_factory.mktemp("png_gray16a") / "out.pdf"
    assert (
        0
        != subprocess.run(
            [
                img2pdfprog,
                "--producer=",
                "--nodate",
                "--engine=" + engine,
                "--output=" + str(out_pdf),
                str(png_gray16a_img),
            ]
        ).returncode
    )
    out_pdf.unlink()


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_interlaced(tmp_path_factory, png_interlaced_img, png_interlaced_pdf):
    tmpdir = tmp_path_factory.mktemp("png_interlaced")
    compare_ghostscript(tmpdir, png_interlaced_img, png_interlaced_pdf)
    compare_poppler(tmpdir, png_interlaced_img, png_interlaced_pdf)
    compare_mupdf(tmpdir, png_interlaced_img, png_interlaced_pdf)
    compare_pdfimages_png(tmpdir, png_interlaced_img, png_interlaced_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_gray1(tmp_path_factory, png_gray1_img, png_gray1_pdf):
    tmpdir = tmp_path_factory.mktemp("png_gray1")
    compare_ghostscript(tmpdir, png_gray1_img, png_gray1_pdf, gsdevice="pnggray")
    compare_poppler(tmpdir, png_gray1_img, png_gray1_pdf)
    compare_mupdf(tmpdir, png_gray1_img, png_gray1_pdf)
    compare_pdfimages_png(tmpdir, png_gray1_img, png_gray1_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_gray2(tmp_path_factory, png_gray2_img, png_gray2_pdf):
    tmpdir = tmp_path_factory.mktemp("png_gray2")
    compare_ghostscript(tmpdir, png_gray2_img, png_gray2_pdf, gsdevice="pnggray")
    compare_poppler(tmpdir, png_gray2_img, png_gray2_pdf)
    compare_mupdf(tmpdir, png_gray2_img, png_gray2_pdf)
    compare_pdfimages_png(tmpdir, png_gray2_img, png_gray2_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_gray4(tmp_path_factory, png_gray4_img, png_gray4_pdf):
    tmpdir = tmp_path_factory.mktemp("png_gray4")
    compare_ghostscript(tmpdir, png_gray4_img, png_gray4_pdf, gsdevice="pnggray")
    compare_poppler(tmpdir, png_gray4_img, png_gray4_pdf)
    compare_mupdf(tmpdir, png_gray4_img, png_gray4_pdf)
    compare_pdfimages_png(tmpdir, png_gray4_img, png_gray4_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_gray8(tmp_path_factory, png_gray8_img, png_gray8_pdf):
    tmpdir = tmp_path_factory.mktemp("png_gray8")
    compare_ghostscript(tmpdir, png_gray8_img, png_gray8_pdf, gsdevice="pnggray")
    compare_poppler(tmpdir, png_gray8_img, png_gray8_pdf)
    compare_mupdf(tmpdir, png_gray8_img, png_gray8_pdf)
    compare_pdfimages_png(tmpdir, png_gray8_img, png_gray8_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_gray16(tmp_path_factory, png_gray16_img, png_gray16_pdf):
    tmpdir = tmp_path_factory.mktemp("png_gray16")
    # ghostscript outputs 8-bit grayscale, so the comparison will not be exact
    compare_ghostscript(
        tmpdir, png_gray16_img, png_gray16_pdf, gsdevice="pnggray", exact=False
    )
    # poppler outputs 8-bit grayscale so the comparison will not be exact
    compare_poppler(tmpdir, png_gray16_img, png_gray16_pdf, exact=False)
    # pdfimages outputs 8-bit grayscale so the comparison will not be exact
    compare_pdfimages_png(tmpdir, png_gray16_img, png_gray16_pdf, exact=False)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_palette1(tmp_path_factory, png_palette1_img, png_palette1_pdf):
    tmpdir = tmp_path_factory.mktemp("png_palette1")
    compare_ghostscript(tmpdir, png_palette1_img, png_palette1_pdf)
    compare_poppler(tmpdir, png_palette1_img, png_palette1_pdf)
    compare_mupdf(tmpdir, png_palette1_img, png_palette1_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_palette2(tmp_path_factory, png_palette2_img, png_palette2_pdf):
    tmpdir = tmp_path_factory.mktemp("png_palette2")
    compare_ghostscript(tmpdir, png_palette2_img, png_palette2_pdf)
    compare_poppler(tmpdir, png_palette2_img, png_palette2_pdf)
    compare_mupdf(tmpdir, png_palette2_img, png_palette2_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_palette4(tmp_path_factory, png_palette4_img, png_palette4_pdf):
    tmpdir = tmp_path_factory.mktemp("png_palette4")
    compare_ghostscript(tmpdir, png_palette4_img, png_palette4_pdf)
    compare_poppler(tmpdir, png_palette4_img, png_palette4_pdf)
    compare_mupdf(tmpdir, png_palette4_img, png_palette4_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_palette8(tmp_path_factory, png_palette8_img, png_palette8_pdf):
    tmpdir = tmp_path_factory.mktemp("png_palette8")
    compare_ghostscript(tmpdir, png_palette8_img, png_palette8_pdf)
    compare_poppler(tmpdir, png_palette8_img, png_palette8_pdf)
    compare_mupdf(tmpdir, png_palette8_img, png_palette8_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    sys.platform in ["darwin", "win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_png_icc(tmp_path_factory, png_icc_img, png_icc_pdf):
    tmpdir = tmp_path_factory.mktemp("png_icc")
    compare_ghostscript(tmpdir, png_icc_img, png_icc_pdf, exact=False, icc=True)
    compare_poppler(tmpdir, png_icc_img, png_icc_pdf, exact=False, icc=True)
    # mupdf ignores the ICC profile in Debian (needs patched thirdparty liblcms2-art)
    compare_pdfimages_png(tmpdir, png_icc_img, png_icc_pdf, exact=False, icc=True)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_gif_transparent(tmp_path_factory, gif_transparent_img, gif_transparent_pdf):
    tmpdir = tmp_path_factory.mktemp("gif_transparent")
    compare_pdfimages_png(tmpdir, gif_transparent_img, gif_transparent_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_gif_palette1(tmp_path_factory, gif_palette1_img, gif_palette1_pdf):
    tmpdir = tmp_path_factory.mktemp("gif_palette1")
    compare_ghostscript(tmpdir, gif_palette1_img, gif_palette1_pdf)
    compare_poppler(tmpdir, gif_palette1_img, gif_palette1_pdf)
    compare_mupdf(tmpdir, gif_palette1_img, gif_palette1_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_gif_palette2(tmp_path_factory, gif_palette2_img, gif_palette2_pdf):
    tmpdir = tmp_path_factory.mktemp("gif_palette2")
    compare_ghostscript(tmpdir, gif_palette2_img, gif_palette2_pdf)
    compare_poppler(tmpdir, gif_palette2_img, gif_palette2_pdf)
    compare_mupdf(tmpdir, gif_palette2_img, gif_palette2_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_gif_palette4(tmp_path_factory, gif_palette4_img, gif_palette4_pdf):
    tmpdir = tmp_path_factory.mktemp("gif_palette4")
    compare_ghostscript(tmpdir, gif_palette4_img, gif_palette4_pdf)
    compare_poppler(tmpdir, gif_palette4_img, gif_palette4_pdf)
    compare_mupdf(tmpdir, gif_palette4_img, gif_palette4_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_gif_palette8(tmp_path_factory, gif_palette8_img, gif_palette8_pdf):
    tmpdir = tmp_path_factory.mktemp("gif_palette8")
    compare_ghostscript(tmpdir, gif_palette8_img, gif_palette8_pdf)
    compare_poppler(tmpdir, gif_palette8_img, gif_palette8_pdf)
    compare_mupdf(tmpdir, gif_palette8_img, gif_palette8_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_gif_animation(tmp_path_factory, gif_animation_img, gif_animation_pdf):
    tmpdir = tmp_path_factory.mktemp("gif_animation")
    subprocess.check_call(
        ["pdfseparate", str(gif_animation_pdf), str(tmpdir / "page-%d.pdf")]
    )
    for page in [1, 2]:
        gif_animation_pdf_nr = tmpdir / ("page-%d.pdf" % page)
        compare_ghostscript(
            tmpdir, str(gif_animation_img) + "[%d]" % (page - 1), gif_animation_pdf_nr
        )
        compare_poppler(
            tmpdir, str(gif_animation_img) + "[%d]" % (page - 1), gif_animation_pdf_nr
        )
        compare_mupdf(
            tmpdir, str(gif_animation_img) + "[%d]" % (page - 1), gif_animation_pdf_nr
        )
        # pdfimages cannot export palette based images
        gif_animation_pdf_nr.unlink()


@pytest.mark.skipif(
    sys.platform in ["darwin", "win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.parametrize("engine", ["internal", "pikepdf"])
def test_tiff_float(tmp_path_factory, tiff_float_img, engine):
    out_pdf = tmp_path_factory.mktemp("tiff_float") / "out.pdf"
    assert (
        0
        != subprocess.run(
            [
                img2pdfprog,
                "--producer=",
                "--nodate",
                "--engine=" + engine,
                "--output=" + str(out_pdf),
                str(tiff_float_img),
            ]
        ).returncode
    )
    out_pdf.unlink()


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_cmyk8(tmp_path_factory, tiff_cmyk8_img, tiff_cmyk8_pdf):
    tmpdir = tmp_path_factory.mktemp("tiff_cmyk8")
    compare_ghostscript(
        tmpdir,
        tiff_cmyk8_img,
        tiff_cmyk8_pdf,
        gsdevice="tiff32nc",
        exact=HAVE_EXACT_CMYK8,
    )
    # not testing with poppler as it cannot write CMYK images
    compare_mupdf(
        tmpdir, tiff_cmyk8_img, tiff_cmyk8_pdf, exact=HAVE_EXACT_CMYK8, cmyk=True
    )
    compare_pdfimages_tiff(tmpdir, tiff_cmyk8_img, tiff_cmyk8_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.parametrize("engine", ["internal", "pikepdf"])
def test_tiff_cmyk16(tmp_path_factory, tiff_cmyk16_img, engine):
    out_pdf = tmp_path_factory.mktemp("tiff_cmyk16") / "out.pdf"
    # PIL is unable to read 16 bit CMYK images
    assert (
        0
        != subprocess.run(
            [
                img2pdfprog,
                "--producer=",
                "--nodate",
                "--engine=" + engine,
                "--output=" + str(out_pdf),
                str(tiff_cmyk16_img),
            ]
        ).returncode
    )
    out_pdf.unlink()


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_rgb8(tmp_path_factory, tiff_rgb8_img, tiff_rgb8_pdf):
    tmpdir = tmp_path_factory.mktemp("tiff_rgb8")
    compare_ghostscript(tmpdir, tiff_rgb8_img, tiff_rgb8_pdf, gsdevice="tiff24nc")
    compare_poppler(tmpdir, tiff_rgb8_img, tiff_rgb8_pdf)
    compare_mupdf(tmpdir, tiff_rgb8_img, tiff_rgb8_pdf)
    compare_pdfimages_tiff(tmpdir, tiff_rgb8_img, tiff_rgb8_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.parametrize("engine", ["internal", "pikepdf"])
def test_tiff_rgb12(tmp_path_factory, tiff_rgb12_img, engine):
    out_pdf = tmp_path_factory.mktemp("tiff_rgb12") / "out.pdf"
    # PIL is unable to preserve more than 8 bits per sample
    assert (
        0
        != subprocess.run(
            [
                img2pdfprog,
                "--producer=",
                "--nodate",
                "--engine=" + engine,
                "--output=" + str(out_pdf),
                str(tiff_rgb12_img),
            ]
        ).returncode
    )
    out_pdf.unlink()


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.parametrize("engine", ["internal", "pikepdf"])
def test_tiff_rgb14(tmp_path_factory, tiff_rgb14_img, engine):
    out_pdf = tmp_path_factory.mktemp("tiff_rgb14") / "out.pdf"
    # PIL is unable to preserve more than 8 bits per sample
    assert (
        0
        != subprocess.run(
            [
                img2pdfprog,
                "--producer=",
                "--nodate",
                "--engine=" + engine,
                "--output=" + str(out_pdf),
                str(tiff_rgb14_img),
            ]
        ).returncode
    )
    out_pdf.unlink()


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.parametrize("engine", ["internal", "pikepdf"])
def test_tiff_rgb16(tmp_path_factory, tiff_rgb16_img, engine):
    out_pdf = tmp_path_factory.mktemp("tiff_rgb16") / "out.pdf"
    # PIL is unable to preserve more than 8 bits per sample
    assert (
        0
        != subprocess.run(
            [
                img2pdfprog,
                "--producer=",
                "--nodate",
                "--engine=" + engine,
                "--output=" + str(out_pdf),
                str(tiff_rgb16_img),
            ]
        ).returncode
    )
    out_pdf.unlink()


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.parametrize("engine", ["internal", "pikepdf"])
def test_tiff_rgba8(tmp_path_factory, tiff_rgba8_img, engine):
    out_pdf = tmp_path_factory.mktemp("tiff_rgba8") / "out.pdf"
    assert (
        0
        != subprocess.run(
            [
                img2pdfprog,
                "--producer=",
                "--nodate",
                "--engine=" + engine,
                "--output=" + str(out_pdf),
                str(tiff_rgba8_img),
            ]
        ).returncode
    )
    out_pdf.unlink()


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.parametrize("engine", ["internal", "pikepdf"])
def test_tiff_rgba16(tmp_path_factory, tiff_rgba16_img, engine):
    out_pdf = tmp_path_factory.mktemp("tiff_rgba16") / "out.pdf"
    assert (
        0
        != subprocess.run(
            [
                img2pdfprog,
                "--producer=",
                "--nodate",
                "--engine=" + engine,
                "--output=" + str(out_pdf),
                str(tiff_rgba16_img),
            ]
        ).returncode
    )
    out_pdf.unlink()


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_gray1(tmp_path_factory, tiff_gray1_img, tiff_gray1_pdf):
    tmpdir = tmp_path_factory.mktemp("tiff_gray1")
    compare_ghostscript(tmpdir, tiff_gray1_img, tiff_gray1_pdf, gsdevice="pnggray")
    compare_poppler(tmpdir, tiff_gray1_img, tiff_gray1_pdf)
    compare_mupdf(tmpdir, tiff_gray1_img, tiff_gray1_pdf)
    compare_pdfimages_tiff(tmpdir, tiff_gray1_img, tiff_gray1_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_gray2(tmp_path_factory, tiff_gray2_img, tiff_gray2_pdf):
    tmpdir = tmp_path_factory.mktemp("tiff_gray2")
    compare_ghostscript(tmpdir, tiff_gray2_img, tiff_gray2_pdf, gsdevice="pnggray")
    compare_poppler(tmpdir, tiff_gray2_img, tiff_gray2_pdf)
    compare_mupdf(tmpdir, tiff_gray2_img, tiff_gray2_pdf)
    compare_pdfimages_tiff(tmpdir, tiff_gray2_img, tiff_gray2_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_gray4(tmp_path_factory, tiff_gray4_img, tiff_gray4_pdf):
    tmpdir = tmp_path_factory.mktemp("tiff_gray4")
    compare_ghostscript(tmpdir, tiff_gray4_img, tiff_gray4_pdf, gsdevice="pnggray")
    compare_poppler(tmpdir, tiff_gray4_img, tiff_gray4_pdf)
    compare_mupdf(tmpdir, tiff_gray4_img, tiff_gray4_pdf)
    compare_pdfimages_tiff(tmpdir, tiff_gray4_img, tiff_gray4_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_gray8(tmp_path_factory, tiff_gray8_img, tiff_gray8_pdf):
    tmpdir = tmp_path_factory.mktemp("tiff_gray8")
    compare_ghostscript(tmpdir, tiff_gray8_img, tiff_gray8_pdf, gsdevice="pnggray")
    compare_poppler(tmpdir, tiff_gray8_img, tiff_gray8_pdf)
    compare_mupdf(tmpdir, tiff_gray8_img, tiff_gray8_pdf)
    compare_pdfimages_tiff(tmpdir, tiff_gray8_img, tiff_gray8_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
@pytest.mark.parametrize("engine", ["internal", "pikepdf"])
def test_tiff_gray16(tmp_path_factory, tiff_gray16_img, engine):
    out_pdf = tmp_path_factory.mktemp("tiff_gray16") / "out.pdf"
    assert (
        0
        != subprocess.run(
            [
                img2pdfprog,
                "--producer=",
                "--nodate",
                "--engine=" + engine,
                "--output=" + str(out_pdf),
                str(tiff_gray16_img),
            ]
        ).returncode
    )
    out_pdf.unlink()


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_multipage(tmp_path_factory, tiff_multipage_img, tiff_multipage_pdf):
    tmpdir = tmp_path_factory.mktemp("tiff_multipage")
    subprocess.check_call(
        ["pdfseparate", str(tiff_multipage_pdf), str(tmpdir / "page-%d.pdf")]
    )
    for page in [1, 2]:
        tiff_multipage_pdf_nr = tmpdir / ("page-%d.pdf" % page)
        compare_ghostscript(
            tmpdir, str(tiff_multipage_img) + "[%d]" % (page - 1), tiff_multipage_pdf_nr
        )
        compare_poppler(
            tmpdir, str(tiff_multipage_img) + "[%d]" % (page - 1), tiff_multipage_pdf_nr
        )
        compare_mupdf(
            tmpdir, str(tiff_multipage_img) + "[%d]" % (page - 1), tiff_multipage_pdf_nr
        )
        compare_pdfimages_tiff(
            tmpdir, str(tiff_multipage_img) + "[%d]" % (page - 1), tiff_multipage_pdf_nr
        )
        tiff_multipage_pdf_nr.unlink()


@pytest.mark.skipif(
    not HAVE_IMAGEMAGICK_MODERN,
    reason="requires imagemagick with support for keeping the palette depth",
)
@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_palette1(tmp_path_factory, tiff_palette1_img, tiff_palette1_pdf):
    tmpdir = tmp_path_factory.mktemp("tiff_palette1")
    compare_ghostscript(tmpdir, tiff_palette1_img, tiff_palette1_pdf)
    compare_poppler(tmpdir, tiff_palette1_img, tiff_palette1_pdf)
    compare_mupdf(tmpdir, tiff_palette1_img, tiff_palette1_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    not HAVE_IMAGEMAGICK_MODERN,
    reason="requires imagemagick with support for keeping the palette depth",
)
@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_palette2(tmp_path_factory, tiff_palette2_img, tiff_palette2_pdf):
    tmpdir = tmp_path_factory.mktemp("tiff_palette2")
    compare_ghostscript(tmpdir, tiff_palette2_img, tiff_palette2_pdf)
    compare_poppler(tmpdir, tiff_palette2_img, tiff_palette2_pdf)
    compare_mupdf(tmpdir, tiff_palette2_img, tiff_palette2_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    not HAVE_IMAGEMAGICK_MODERN,
    reason="requires imagemagick with support for keeping the palette depth",
)
@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_palette4(tmp_path_factory, tiff_palette4_img, tiff_palette4_pdf):
    tmpdir = tmp_path_factory.mktemp("tiff_palette4")
    compare_ghostscript(tmpdir, tiff_palette4_img, tiff_palette4_pdf)
    compare_poppler(tmpdir, tiff_palette4_img, tiff_palette4_pdf)
    compare_mupdf(tmpdir, tiff_palette4_img, tiff_palette4_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_palette8(tmp_path_factory, tiff_palette8_img, tiff_palette8_pdf):
    tmpdir = tmp_path_factory.mktemp("tiff_palette8")
    compare_ghostscript(tmpdir, tiff_palette8_img, tiff_palette8_pdf)
    compare_poppler(tmpdir, tiff_palette8_img, tiff_palette8_pdf)
    compare_mupdf(tmpdir, tiff_palette8_img, tiff_palette8_pdf)
    # pdfimages cannot export palette based images


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_ccitt_lsb_m2l_white(
    tmp_path_factory, tiff_ccitt_lsb_m2l_white_img, tiff_ccitt_lsb_m2l_white_pdf
):
    tmpdir = tmp_path_factory.mktemp("tiff_ccitt_lsb_m2l_white")
    compare_ghostscript(
        tmpdir,
        tiff_ccitt_lsb_m2l_white_img,
        tiff_ccitt_lsb_m2l_white_pdf,
        gsdevice="pnggray",
    )
    compare_poppler(tmpdir, tiff_ccitt_lsb_m2l_white_img, tiff_ccitt_lsb_m2l_white_pdf)
    compare_mupdf(tmpdir, tiff_ccitt_lsb_m2l_white_img, tiff_ccitt_lsb_m2l_white_pdf)
    compare_pdfimages_tiff(
        tmpdir, tiff_ccitt_lsb_m2l_white_img, tiff_ccitt_lsb_m2l_white_pdf
    )


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_ccitt_msb_m2l_white(
    tmp_path_factory, tiff_ccitt_msb_m2l_white_img, tiff_ccitt_msb_m2l_white_pdf
):
    tmpdir = tmp_path_factory.mktemp("tiff_ccitt_msb_m2l_white")
    compare_ghostscript(
        tmpdir,
        tiff_ccitt_msb_m2l_white_img,
        tiff_ccitt_msb_m2l_white_pdf,
        gsdevice="pnggray",
    )
    compare_poppler(tmpdir, tiff_ccitt_msb_m2l_white_img, tiff_ccitt_msb_m2l_white_pdf)
    compare_mupdf(tmpdir, tiff_ccitt_msb_m2l_white_img, tiff_ccitt_msb_m2l_white_pdf)
    compare_pdfimages_tiff(
        tmpdir, tiff_ccitt_msb_m2l_white_img, tiff_ccitt_msb_m2l_white_pdf
    )


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_ccitt_msb_l2m_white(
    tmp_path_factory, tiff_ccitt_msb_l2m_white_img, tiff_ccitt_msb_l2m_white_pdf
):
    tmpdir = tmp_path_factory.mktemp("tiff_ccitt_msb_l2m_white")
    compare_ghostscript(
        tmpdir,
        tiff_ccitt_msb_l2m_white_img,
        tiff_ccitt_msb_l2m_white_pdf,
        gsdevice="pnggray",
    )
    compare_poppler(tmpdir, tiff_ccitt_msb_l2m_white_img, tiff_ccitt_msb_l2m_white_pdf)
    compare_mupdf(tmpdir, tiff_ccitt_msb_l2m_white_img, tiff_ccitt_msb_l2m_white_pdf)
    compare_pdfimages_tiff(
        tmpdir, tiff_ccitt_msb_l2m_white_img, tiff_ccitt_msb_l2m_white_pdf
    )


@pytest.mark.skipif(
    not HAVE_IMAGEMAGICK_MODERN,
    reason="requires imagemagick with support for min-is-black",
)
@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_ccitt_lsb_m2l_black(
    tmp_path_factory, tiff_ccitt_lsb_m2l_black_img, tiff_ccitt_lsb_m2l_black_pdf
):
    tmpdir = tmp_path_factory.mktemp("tiff_ccitt_lsb_m2l_black")
    compare_ghostscript(
        tmpdir,
        tiff_ccitt_lsb_m2l_black_img,
        tiff_ccitt_lsb_m2l_black_pdf,
        gsdevice="pnggray",
    )
    compare_poppler(tmpdir, tiff_ccitt_lsb_m2l_black_img, tiff_ccitt_lsb_m2l_black_pdf)
    compare_mupdf(tmpdir, tiff_ccitt_lsb_m2l_black_img, tiff_ccitt_lsb_m2l_black_pdf)
    compare_pdfimages_tiff(
        tmpdir, tiff_ccitt_lsb_m2l_black_img, tiff_ccitt_lsb_m2l_black_pdf
    )


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_ccitt_nometa1(
    tmp_path_factory, tiff_ccitt_nometa1_img, tiff_ccitt_nometa1_pdf
):
    tmpdir = tmp_path_factory.mktemp("tiff_ccitt_nometa1")
    compare_ghostscript(
        tmpdir, tiff_ccitt_nometa1_img, tiff_ccitt_nometa1_pdf, gsdevice="pnggray"
    )
    compare_poppler(tmpdir, tiff_ccitt_nometa1_img, tiff_ccitt_nometa1_pdf)
    compare_mupdf(tmpdir, tiff_ccitt_nometa1_img, tiff_ccitt_nometa1_pdf)
    compare_pdfimages_tiff(tmpdir, tiff_ccitt_nometa1_img, tiff_ccitt_nometa1_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_tiff_ccitt_nometa2(
    tmp_path_factory, tiff_ccitt_nometa2_img, tiff_ccitt_nometa2_pdf
):
    tmpdir = tmp_path_factory.mktemp("tiff_ccitt_nometa2")
    compare_ghostscript(
        tmpdir, tiff_ccitt_nometa2_img, tiff_ccitt_nometa2_pdf, gsdevice="pnggray"
    )
    compare_poppler(tmpdir, tiff_ccitt_nometa2_img, tiff_ccitt_nometa2_pdf)
    compare_mupdf(tmpdir, tiff_ccitt_nometa2_img, tiff_ccitt_nometa2_pdf)
    compare_pdfimages_tiff(tmpdir, tiff_ccitt_nometa2_img, tiff_ccitt_nometa2_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_miff_cmyk8(tmp_path_factory, miff_cmyk8_img, tiff_cmyk8_img, miff_cmyk8_pdf):
    tmpdir = tmp_path_factory.mktemp("miff_cmyk8")
    compare_ghostscript(
        tmpdir, tiff_cmyk8_img, miff_cmyk8_pdf, gsdevice="tiff32nc", exact=False
    )
    # not testing with poppler as it cannot write CMYK images
    compare_mupdf(tmpdir, tiff_cmyk8_img, miff_cmyk8_pdf, exact=False, cmyk=True)
    compare_pdfimages_tiff(tmpdir, tiff_cmyk8_img, miff_cmyk8_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_miff_cmyk16(
    tmp_path_factory, miff_cmyk16_img, tiff_cmyk16_img, miff_cmyk16_pdf
):
    tmpdir = tmp_path_factory.mktemp("miff_cmyk16")
    compare_ghostscript(
        tmpdir, tiff_cmyk16_img, miff_cmyk16_pdf, gsdevice="tiff32nc", exact=False
    )
    # not testing with poppler as it cannot write CMYK images
    compare_mupdf(tmpdir, tiff_cmyk16_img, miff_cmyk16_pdf, exact=False, cmyk=True)
    # compare_pdfimages_tiff(tmpdir, tiff_cmyk16_img, miff_cmyk16_pdf)


@pytest.mark.skipif(
    sys.platform in ["win32"],
    reason="test utilities not available on Windows and MacOS",
)
def test_miff_rgb8(tmp_path_factory, miff_rgb8_img, tiff_rgb8_img, miff_rgb8_pdf):
    tmpdir = tmp_path_factory.mktemp("miff_rgb8")
    compare_ghostscript(tmpdir, tiff_rgb8_img, miff_rgb8_pdf, gsdevice="tiff24nc")
    compare_poppler(tmpdir, tiff_rgb8_img, miff_rgb8_pdf)
    compare_mupdf(tmpdir, tiff_rgb8_img, miff_rgb8_pdf)
    compare_pdfimages_tiff(tmpdir, tiff_rgb8_img, miff_rgb8_pdf)


# we define some variables so that the table below can be narrower
psl = (972, 504)  # --pagesize landscape
psp = (504, 972)  # --pagesize portrait
isl = (756, 324)  # --imgsize landscape
isp = (324, 756)  # --imgsize portrait
border = (162, 270)  # --border
poster = (97200, 50400)
# shortcuts for fit modes
f_into = img2pdf.FitMode.into
f_fill = img2pdf.FitMode.fill
f_exact = img2pdf.FitMode.exact
f_shrink = img2pdf.FitMode.shrink
f_enlarge = img2pdf.FitMode.enlarge


@pytest.mark.parametrize(
    "layout_test_cases",
    [
        # fmt: off
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
    (poster, None, None, f_fill,    0, (97200, 50400), (151200, 50400),
                                       (97200, 50400), (100800, 50400)),
    ]
    # fmt: on
)
def test_layout(layout_test_cases):
    # there is no need to have test cases with the same images with inverted
    # orientation (landscape/portrait) because --pagesize and --imgsize are
    # already inverted
    im1 = (864, 288)  # imgpx #1 => 648x216
    im2 = (1152, 576)  # imgpx #2 => 864x432
    psopt, isopt, border, fit, ao, pspdf1, ispdf1, pspdf2, ispdf2 = layout_test_cases
    if isopt is not None:
        isopt = ((img2pdf.ImgSize.abs, isopt[0]), (img2pdf.ImgSize.abs, isopt[1]))
    layout_fun = img2pdf.get_layout_fun(psopt, isopt, border, fit, ao)
    try:
        pwpdf, phpdf, iwpdf, ihpdf = layout_fun(
            im1[0], im1[1], (img2pdf.default_dpi, img2pdf.default_dpi)
        )
        assert (pwpdf, phpdf) == pspdf1
        assert (iwpdf, ihpdf) == ispdf1
    except img2pdf.NegativeDimensionError:
        assert pspdf1 is None
        assert ispdf1 is None
    try:
        pwpdf, phpdf, iwpdf, ihpdf = layout_fun(
            im2[0], im2[1], (img2pdf.default_dpi, img2pdf.default_dpi)
        )
        assert (pwpdf, phpdf) == pspdf2
        assert (iwpdf, ihpdf) == ispdf2
    except img2pdf.NegativeDimensionError:
        assert pspdf2 is None
        assert ispdf2 is None


@pytest.fixture(
    scope="session",
    params=os.listdir(os.path.join(os.path.dirname(__file__), "tests", "input")),
)
def general_input(request):
    assert os.path.isfile(
        os.path.join(os.path.dirname(__file__), "tests", "input", request.param)
    )
    return request.param


@pytest.mark.skipif(not HAVE_FAKETIME, reason="requires faketime")
@pytest.mark.parametrize(
    "engine,testdata,timezone,pdfa",
    itertools.product(
        ["internal", "pikepdf"],
        ["2021-02-05 17:49:00"],
        ["Europe/Berlin", "GMT+12"],
        [True, False],
    ),
)
def test_faketime(tmp_path_factory, jpg_img, engine, testdata, timezone, pdfa):
    expected = tz2utcstrftime(testdata, "D:%Y%m%d%H%M%SZ", timezone)
    out_pdf = tmp_path_factory.mktemp("faketime") / "out.pdf"
    subprocess.check_call(
        ["env", f"TZ={timezone}", "faketime", "-f", testdata, img2pdfprog]
        + (["--pdfa"] if pdfa else [])
        + [
            "--producer=",
            "--engine=" + engine,
            "--output=" + str(out_pdf),
            str(jpg_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert p.docinfo.CreationDate == expected
        assert p.docinfo.ModDate == expected
        if pdfa:
            assert p.Root.Metadata.Subtype == "/XML"
            assert p.Root.Metadata.Type == "/Metadata"
            expected = tz2utcstrftime(testdata, "%Y-%m-%dT%H:%M:%SZ", timezone)
            root = ET.fromstring(p.Root.Metadata.read_bytes())
            for k in ["ModifyDate", "CreateDate"]:
                assert (
                    root.find(
                        f".//xmp:{k}", {"xmp": "http://ns.adobe.com/xap/1.0/"}
                    ).text
                    == expected
                )
    out_pdf.unlink()


@pytest.mark.parametrize(
    "engine,testdata,timezone,pdfa",
    itertools.product(
        ["internal", "pikepdf"],
        [
            "2021-02-05 17:49:00",
            "2021-02-05T17:49:00",
            "Fri, 05 Feb 2021 17:49:00 +0100",
            "last year 12:00",
        ],
        ["Europe/Berlin", "GMT+12"],
        [True, False],
    ),
)
def test_date(tmp_path_factory, jpg_img, engine, testdata, timezone, pdfa):
    # we use the date utility to convert the timestamp from the local
    # timezone into UTC with the format used by PDF
    expected = tz2utcstrftime(testdata, "D:%Y%m%d%H%M%SZ", timezone)
    out_pdf = tmp_path_factory.mktemp("faketime") / "out.pdf"
    subprocess.check_call(
        ["env", f"TZ={timezone}", img2pdfprog]
        + (["--pdfa"] if pdfa else [])
        + [
            f"--moddate={testdata}",
            f"--creationdate={testdata}",
            "--producer=",
            "--engine=" + engine,
            "--output=" + str(out_pdf),
            str(jpg_img),
        ]
    )
    with pikepdf.open(str(out_pdf)) as p:
        assert p.docinfo.CreationDate == expected
        assert p.docinfo.ModDate == expected
        if pdfa:
            assert p.Root.Metadata.Subtype == "/XML"
            assert p.Root.Metadata.Type == "/Metadata"
            expected = tz2utcstrftime(testdata, "%Y-%m-%dT%H:%M:%SZ", timezone)
            root = ET.fromstring(p.Root.Metadata.read_bytes())
            for k in ["ModifyDate", "CreateDate"]:
                assert (
                    root.find(
                        f".//xmp:{k}", {"xmp": "http://ns.adobe.com/xap/1.0/"}
                    ).text
                    == expected
                )
    out_pdf.unlink()


@pytest.mark.parametrize("engine", ["internal", "pikepdf"])
def test_general(general_input, engine):
    inputf = os.path.join(os.path.dirname(__file__), "tests", "input", general_input)
    outputf = os.path.join(
        os.path.dirname(__file__), "tests", "output", general_input + ".pdf"
    )
    assert os.path.isfile(outputf)
    f = inputf
    out = outputf

    engine = getattr(img2pdf.Engine, engine)

    with open(f, "rb") as inf:
        orig_imgdata = inf.read()
    output = img2pdf.convert(orig_imgdata, nodate=True, engine=engine)
    x = pikepdf.open(BytesIO(output))
    assert x.Root.Pages.Count in (1, 2)
    if len(x.Root.Pages.Kids) == "1":
        assert x.Size == "7"
        assert len(x.Root.Pages.Kids) == 1
    elif len(x.Root.Pages.Kids) == "2":
        assert x.Size == "10"
        assert len(x.Root.Pages.Kids) == 2
    assert sorted(x.Root.keys()) == ["/Pages", "/Type"]
    assert x.Root.Type == "/Catalog"
    assert sorted(x.Root.Pages.keys()) == ["/Count", "/Kids", "/Type"]
    assert x.Root.Pages.Type == "/Pages"
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
        pagewidth = 72.0 * imgwidthpx / ndpi[0]
        pageheight = 72.0 * imgheightpx / ndpi[1]

        def format_float(f):
            if int(f) == f:
                return int(f)
            else:
                return decimal.Decimal("%.4f" % f)

        assert sorted(cur_page.keys()) == [
            "/Contents",
            "/MediaBox",
            "/Parent",
            "/Resources",
            "/Type",
        ]
        assert cur_page.MediaBox == pikepdf.Array(
            [0, 0, format_float(pagewidth), format_float(pageheight)]
        )
        assert cur_page.Parent == x.Root.Pages
        assert cur_page.Type == "/Page"
        assert cur_page.Resources.keys() == {"/XObject"}
        assert cur_page.Resources.XObject.keys() == {"/Im0"}
        if engine != img2pdf.Engine.pikepdf:
            assert cur_page.Contents.Length == len(cur_page.Contents.read_bytes())
        assert (
            cur_page.Contents.read_bytes()
            == b"q\n%.4f 0 0 %.4f 0.0000 0.0000 cm\n/Im0 Do\nQ"
            % (
                pagewidth,
                pageheight,
            )
        )

        imgprops = cur_page.Resources.XObject.Im0

        # test if the filter is valid:
        assert imgprops.Filter in [
            "/DCTDecode",
            "/JPXDecode",
            "/FlateDecode",
            pikepdf.Array([pikepdf.Name.CCITTFaxDecode]),
        ]

        # test if the image has correct size
        assert imgprops.Width == orig_img.size[0]
        assert imgprops.Height == orig_img.size[1]
        # if the input file is a jpeg then it should've been copied
        # verbatim into the PDF
        if imgprops.Filter in ["/DCTDecode", "/JPXDecode"]:
            assert cur_page.Resources.XObject.Im0.read_raw_bytes() == orig_imgdata
        elif imgprops.Filter == pikepdf.Array([pikepdf.Name.CCITTFaxDecode]):
            tiff_header = tiff_header_for_ccitt(
                int(imgprops.Width), int(imgprops.Height), int(imgprops.Length), 4
            )
            imgio = BytesIO()
            imgio.write(tiff_header)
            imgio.write(cur_page.Resources.XObject.Im0.read_raw_bytes())
            imgio.seek(0)
            im = Image.open(imgio)
            assert im.tobytes() == orig_img.tobytes()
            try:
                im.close()
            except AttributeError:
                pass
        elif imgprops.Filter == "/FlateDecode":
            # otherwise, the data is flate encoded and has to be equal
            # to the pixel data of the input image
            imgdata = zlib.decompress(cur_page.Resources.XObject.Im0.read_raw_bytes())
            if hasattr(imgprops, "DecodeParms"):
                if orig_img.format == "PNG":
                    pngidat, palette = img2pdf.parse_png(orig_imgdata)
                elif (
                    orig_img.format == "TIFF"
                    and orig_img.info["compression"] == "group4"
                ):
                    offset, length = img2pdf.ccitt_payload_location_from_pil(orig_img)
                    pngidat = orig_imgdata[offset : offset + length]
                else:
                    pngbuffer = BytesIO()
                    orig_img.save(pngbuffer, format="png")
                    pngidat, palette = img2pdf.parse_png(pngbuffer.getvalue())
                assert zlib.decompress(pngidat) == imgdata
            else:
                colorspace = imgprops.ColorSpace
                if colorspace == "/DeviceGray":
                    colorspace = "L"
                elif colorspace == "/DeviceRGB":
                    colorspace = "RGB"
                elif colorspace == "/DeviceCMYK":
                    colorspace = "CMYK"
                else:
                    raise Exception("invalid colorspace")
                im = Image.frombytes(
                    colorspace, (int(imgprops.Width), int(imgprops.Height)), imgdata
                )
                if orig_img.mode == "1":
                    assert im.tobytes() == orig_img.convert("L").tobytes()
                elif orig_img.mode not in ("RGB", "L", "CMYK", "CMYK;I"):
                    assert im.tobytes() == orig_img.convert("RGB").tobytes()
                # the python-pil version 2.3.0-1ubuntu3 in Ubuntu does
                # not have the close() method
                try:
                    im.close()
                except AttributeError:
                    pass
        else:
            raise Exception("unknown filter")

    def rec(obj):
        if isinstance(obj, pikepdf.Dictionary):
            return {k: rec(v) for k, v in obj.items() if k != "/Parent"}
        elif isinstance(obj, pikepdf.Array):
            return [rec(v) for v in obj]
        elif isinstance(obj, pikepdf.Stream):
            ret = rec(obj.stream_dict)
            stream = obj.read_raw_bytes()
            assert len(stream) == ret["/Length"]
            del ret["/Length"]
            if ret.get("/Filter") == "/FlateDecode":
                stream = obj.read_bytes()
                del ret["/Filter"]
            ret["stream"] = stream
            return ret
        elif isinstance(obj, pikepdf.Name) or isinstance(obj, pikepdf.String):
            return str(obj)
        elif isinstance(obj, decimal.Decimal) or isinstance(obj, str):
            return obj
        elif isinstance(obj, int):
            return decimal.Decimal(obj)
        raise Exception("unhandled: %s" % (type(obj)))

    y = pikepdf.open(out)
    pydictx = rec(x.Root)
    pydicty = rec(y.Root)
    assert pydictx == pydicty
    # the python-pil version 2.3.0-1ubuntu3 in Ubuntu does not have the
    # close() method
    try:
        orig_img.close()
    except AttributeError:
        pass


def main():
    normal16 = alpha_value()[:, :, 0:3]
    pathlib.Path("test.icc").write_bytes(icc_profile())
    write_png(
        normal16 / 0xFFFF * 0xFF,
        "icc.png",
        8,
        2,
        iccp="test.icc",
    )
    write_png(
        normal16 / 0xFFFF * 0xFF,
        "normal.png",
        8,
        2,
    )


if __name__ == "__main__":
    main()
