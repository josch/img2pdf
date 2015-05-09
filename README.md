img2pdf
=======

Losslessly convert images to PDF without unnecessarily re-encoding JPEG and
JPEG2000 files.  Image quality is retained without unnecessarily increasing
file size.

Background
----------

Quality loss can be avoided when converting JPEG and JPEG2000 images to
PDF by embedding them without re-encoding.  I wrote this piece of python code.
because I was missing a tool to do this automatically.

If you know how to embed JPEG and JPEG2000 images into a PDF container without
recompression, using existing tools, please contact me so that I can put this
code into the garbage bin :D

Functionality
-------------

This program will take a list of images and produce a PDF file with the images
embedded in it.  JPEG and JPEG2000 images will be included without
recompression.  Images in other formats will be included with zip/flate
encoding which usually leads to an increase in the resulting size because
formats like png compress better than PDF which just zip/flate compresses the
RGB data.  As a result, this tool is able to losslessly wrap images into a PDF
container with a quality-filesize ratio that is typically better (in case of
JPEG and JPEG2000 images) or equal (in case of other formats) than that of
existing tools.

For example, imagemagick will re-encode the input JPEG image (thus changing
its content):

	$ convert img.jpg img.pdf
	$ pdfimages img.pdf img.extr # not using -j to be extra sure there is no recompression
	$ compare -metric AE img.jpg img.extr-000.ppm null:
	1.6301e+06

If one wants to losslessly convert from any format to PDF with
imagemagick, one has to use zip compression:

	$ convert input.jpg -compress Zip output.pdf
	$ pdfimages img.pdf img.extr # not using -j to be extra sure there is no recompression
	$ compare -metric AE img.jpg img.extr-000.ppm null:
	0

However, this approach will result in PDF files that are a few times larger
than the input JPEG or JPEG2000 file.

img2pdf is able to losslessly embed JPEG and JPEG2000 files into a PDF
container without additional overhead (aside from the PDF structure itself),
save other graphics formats using lossless zip compression,
and produce multi-page PDF files when more than one input image is given.

Also, since JPEG and JPEG2000 images are not reencoded, conversion  with
img2pdf is several times faster than with other tools.


Usage
-----

#### General Notes

The images must be provided as files because img2pdf needs to seek
in the file descriptor.  Input cannot be piped through stdin.

If no output file is specified with the `-o`/`--output` option,
output will be to stdout.

Descriptions of the options should be self explanatory.
They are available by running:

	img2pdf --help


#### Controlling Page Size

The PDF page size can be manipulated.  By default, the image will be sized "into" the given dimensions with the aspect ratio retained.  For instance, to size an image into a page that is at most 500pt x 500pt, use:

	img2pdf -s 500x500 -o output.pdf input.jpg

To "fill" out a page that is at least 500pt x 500pt, follow the dimensions with a `^`:

	img2pdf -s 500x500^ -o output.pdf input.jpg

To output pages that are exactly 500pt x 500pt, follow the dimensions with an `!`:

	img2pdf -s 500x500\! -o output.pdf input.jpg

Notice that the default unit is points.  Units may be also be specified and mixed:

	img2pdf -s 8.5inx27.94cm -o output.pdf input.jpg

If either width or height is omitted, the other will be calculated
to preserve aspect ratio.

	img2pdf -s x280mm -o output1.pdf input.jpg
	img2pdf -s 280mmx -o output2.pdf input.jpg

Some standard page sizes are recognized:

	img2pdf -s letter -o output1.pdf input.jpg
	img2pdf -s a4 -o output2.pdf input.jpg

#### Colorspace

Currently, the colorspace must be forced for JPEG 2000 images that are
not in the RGB colorspace.  Available colorspace options are based on
Python Imaging Library (PIL) short handles.

 * `RGB` = RGB color
 * `L` = Grayscale
 * `1` = Black and white (internally converted to grayscale)
 * `CMYK` = CMYK color
 * `CMYK;I` = CMYK color with inversion

For example, to encode a grayscale JPEG2000 image, use:

	img2pdf -C L -o output.pdf input.jp2

Bugs
----

If you find a JPEG or JPEG2000 file that, when embedded cannot be read
by the Adobe Acrobat Reader, please contact me.

For lossless conversion of formats other than JPEG or JPEG2000, zip/flate
encoding is used.  This choice is based on tests I did with a number of images.
I converted them into PDF using the lossless variants of the compression
formats offered by imagemagick.  In all my tests, zip/flate encoding performed
best.  You can verify my findings using the test_comp.sh script with any input
image given as a commandline argument.  If you find an input file that is
outperformed by another lossless compression method, contact me.

I have not yet figured out how to determine the colorspace of JPEG2000 files.
Therefore JPEG2000 files use DeviceRGB by default. For JPEG2000 files with
other colorspaces, you must force it using the `--colorspace` option.

It might be possible to store transparency using masks but it is not clear
what the utility of such a functionality would be.

Most vector graphic formats can be losslessly turned into PDF (minus some of
the features unsupported by PDF) but img2pdf will currently turn vector
graphics into their lossy raster representations.

Acrobat is able to store a hint for the PDF reader of how to present the PDF
when opening it. Things like automatic fullscreen or the zoom level can be
configured.

It would be nice if a single input image could be read from standard input.

Installation
------------

On a Debian- and Ubuntu-based systems, dependencies may be installed
with the following command:

	apt-get install python python-pil python-setuptools

Or for Python 3:

	apt-get install python3 python3-pil python3-setuptools

You can then install the package using:

	$ pip install img2pdf

If you prefer to install from source code use:

	$ cd img2pdf/
	$ pip install .

To test the console script without installing the package on your system,
use virtualenv:

	$ cd img2pdf/
	$ virtualenv ve
	$ ve/bin/pip install .

You can then test the converter using:

	$ ve/bin/img2pdf -o test.pdf src/tests/test.jpg

The package can also be used as a library:

	import img2pdf
	pdf_bytes = img2pdf.convert(['test.jpg'])

	file = open("name.pdf","wb")
	file.write(pdf_bytes)
