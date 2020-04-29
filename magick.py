#!/usr/bin/env python3

import sys
import numpy
import scipy.signal
import zlib
import struct


def find_closest_palette_color(color, palette):
    if color.ndim == 0:
        idx = (numpy.abs(palette - color)).argmin()
    else:
        # naive distance function by computing the euclidean distance in RGB space
        idx = ((palette - color) ** 2).sum(axis=-1).argmin()
    return palette[idx]


def floyd_steinberg(img, palette):
    for y in range(img.shape[0]):
        for x in range(img.shape[1]):
            oldpixel = img[y, x]
            newpixel = find_closest_palette_color(oldpixel, palette)
            quant_error = oldpixel - newpixel
            img[y, x] = newpixel
            if x + 1 < img.shape[1]:
                img[y, x + 1] += quant_error * 7 / 16
            if y + 1 < img.shape[0]:
                img[y + 1, x - 1] += quant_error * 3 / 16
                img[y + 1, x] += quant_error * 5 / 16
            if x + 1 < img.shape[1] and y + 1 < img.shape[0]:
                img[y + 1, x + 1] += quant_error * 1 / 16
    return img


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
    for y in range(img.shape[0]):
        for x in range(img.shape[1]):
            clin = sum(img[y, x] * [0.2126, 0.7152, 0.0722]) / 0xFFFF
            if clin <= 0.0031308:
                csrgb = 12.92 * clin
            else:
                csrgb = 1.055 * clin ** (1 / 2.4) - 0.055
            result[y, x] = csrgb * 0xFFFF
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


def write_png(data, path, bitdepth, colortype, palette=None):
    with open(path, "wb") as f:
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
                        val |= (data[y, x + j].astype(">u2") & (2 ** bitdepth - 1)) << (
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


def main():
    outdir = sys.argv[1]

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

    # choose values slightly off red, lime and blue because otherwise
    # imagemagick will classify the image as Depth: 8/1-bit
    pal2 = numpy.array(
        [[0, 0, 0], [0xFE, 0, 0], [0, 0xFE, 0], [0, 0, 0xFE]],
        dtype=numpy.dtype("int64"),
    )

    # don't choose black and white or otherwise imagemagick will classify the
    # image as bilevel with 8/1-bit depth instead of palette with 8-bit color
    # don't choose gray colors or otherwise imagemagick will classify the
    # image as grayscale
    pal1 = numpy.array(
        [[0x01, 0x02, 0x03], [0xFE, 0xFD, 0xFC]], dtype=numpy.dtype("int64")
    )

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
        numpy.float,
    )

    # constructs a 2D array of a circle with a width of 36
    circle = list()
    offsets_36 = [14, 11, 9, 7, 6, 5, 4, 3, 3, 2, 2, 1, 1, 1, 0, 0, 0, 0]
    for offs in offsets_36 + offsets_36[::-1]:
        circle.append([0] * offs + [1] * (len(offsets_36) - offs) * 2 + [0] * offs)

    alpha = numpy.zeros((60, 60, 4), dtype=numpy.dtype("int64"))

    # draw three circles
    for (xpos, ypos, color) in [
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

    write_png(alpha, outdir + "/alpha.png", 16, 6)

    normal16 = alpha[:, :, 0:3]
    write_png(normal16, outdir + "/normal16.png", 16, 2)

    write_png(normal16 / 0xFFFF * 0xFF, outdir + "/normal.png", 8, 2)

    write_png(0xFF - normal16 / 0xFFFF * 0xFF, outdir + "/inverse.png", 8, 2)

    gray16 = rgb2gray(normal16)

    write_png(gray16, outdir + "/gray16.png", 16, 0)

    write_png(gray16 / 0xFFFF * 0xFF, outdir + "/gray8.png", 8, 0)

    write_png(
        floyd_steinberg(gray16, numpy.arange(16) / 0xF * 0xFFFF) / 0xFFFF * 0xF,
        outdir + "/gray4.png",
        4,
        0,
    )

    write_png(
        floyd_steinberg(gray16, numpy.arange(4) / 0x3 * 0xFFFF) / 0xFFFF * 0x3,
        outdir + "/gray2.png",
        2,
        0,
    )

    write_png(
        floyd_steinberg(gray16, numpy.arange(2) / 0x1 * 0xFFFF) / 0xFFFF * 0x1,
        outdir + "/gray1.png",
        1,
        0,
    )

    write_png(
        palettize(
            floyd_steinberg(normal16, pal8 * 0xFFFF / 0xFF) / 0xFFFF * 0xFF, pal8
        ),
        outdir + "/palette8.png",
        8,
        3,
        pal8,
    )

    write_png(
        palettize(
            floyd_steinberg(normal16, pal4 * 0xFFFF / 0xFF) / 0xFFFF * 0xFF, pal4
        ),
        outdir + "/palette4.png",
        4,
        3,
        pal4,
    )

    write_png(
        palettize(
            floyd_steinberg(normal16, pal2 * 0xFFFF / 0xFF) / 0xFFFF * 0xFF, pal2
        ),
        outdir + "/palette2.png",
        2,
        3,
        pal2,
    )

    write_png(
        palettize(
            floyd_steinberg(normal16, pal1 * 0xFFFF / 0xFF) / 0xFFFF * 0xFF, pal1
        ),
        outdir + "/palette1.png",
        1,
        3,
        pal1,
    )


if __name__ == "__main__":
    main()
