img2pdf
=======

Losslessly convert raster images to PDF. The file size will not unnecessarily
increase. It can for example be used to create a PDF document from a number of
scans that are only available in JPEG format. Existing solutions would either
re-encode the input JPEG files (leading to quality loss) or store them in the
zip/flate format which results into the PDF becoming unnecessarily large in
terms of its file size.

Background
----------

Quality loss can be avoided when converting JPEG and JPEG2000 images to PDF by
embedding them into the PDF without re-encoding them. This is what img2pdf
does. It thus treats the PDF format merely as a container format for storing
one or more JPEGs without re-encoding the JPEG images themselves.

If you know an existing tool which allows one to embed JPEG and JPEG2000 images
into a PDF container without recompression, please contact me so that I can put
this code into the garbage bin.

Functionality
-------------

This program will take a list of raster images and produce a PDF file with the
images embedded in it.  JPEG and JPEG2000 images will be included without
recompression and the resulting PDF will only be slightly larger than the input
images due to the overhead of the PDF container.  Raster images in other
formats (like png, gif or tif) will be included using the lossless zip/flate
encoding which usually leads to a significant increase in the PDF size if the
input was for example a png image. This is unfortunately unavoidable because
there is no other way to store arbitrary RGB bitmaps in PDF in a lossless way
other than zip/flate encoding. And zip/flate compresses bitmaps worse than png
is able to compress them.

As a result, this tool is able to losslessly wrap raster images into a PDF
container with a quality to filesize ratio that is typically better (in case of
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
save other graphics formats using lossless zip compression, and produce
multi-page PDF files when more than one input image is given.

Also, since JPEG and JPEG2000 images are not reencoded, conversion with img2pdf
is several times faster than with other tools.

Usage
-----

The images must be provided as files because img2pdf needs to seek in the file
descriptor.

If no output file is specified with the `-o`/`--output` option, output will be
done to stdout. A typical invocation is:

	img2pdf img1.png img2.jpg -o out.pdf

The detailed documentation can be accessed by running:

	img2pdf --help


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
other colorspaces, you must explicitly specify it using the `--colorspace`
option.

It might be possible to store transparency using masks but it is not clear
what the utility of such a functionality would be.

Most vector graphic formats can be losslessly turned into PDF (minus some of
the features unsupported by PDF) but img2pdf will currently turn vector
graphics into their lossy raster representations. For converting raster
graphics to PDF, use another tool like inkscape and then join the resulting
pages with a tool like pdftk.

A configuration file could be used for default options.

Installation
------------

On a Debian- and Ubuntu-based systems, dependencies may be installed
with the following command:

	apt-get install python3 python3-pil python3-setuptools

You can then install the package using:

	$ pip3 install img2pdf

If you prefer to install from source code use:

	$ cd img2pdf/
	$ pip3 install .

To test the console script without installing the package on your system,
use virtualenv:

	$ cd img2pdf/
	$ virtualenv ve
	$ ve/bin/pip3 install .

You can then test the converter using:

	$ ve/bin/img2pdf -o test.pdf src/tests/test.jpg

The package can also be used as a library:

	import img2pdf

	# opening from filename
	with open("name.pdf","wb") as f:
		f.write(img2pdf.convert('test.jpg'))

	# opening from file handle
	with open("name.pdf","wb") as f1, open("test.jpg") as f2:
		f1.write(img2pdf.convert(f2))

	# using in-memory image data
	with open("name.pdf","wb") as f:
		f.write(img2pdf.convert("\x89PNG...")

	# multiple inputs (variant 1)
	with open("name.pdf","wb") as f:
		f.write(img2pdf.convert("test1.jpg", "test2.png"))

	# multiple inputs (variant 2)
	with open("name.pdf","wb") as f:
		f.write(img2pdf.convert(["test1.jpg", "test2.png"]))

	# writing to file descriptor
	with open("name.pdf","wb") as f1, open("test.jpg") as f2:
		img2pdf.convert(f2, outputstream=f1)

	# specify paper size (A4)
	a4inpt = (img2pdf.mm_to_pt(210),img2pdf.mm_to_pt(297))
	layout_fun = img2pdf.get_layout_fun(a4inpt)
	with open("name.pdf","wb") as f:
		f.write(img2pdf.convert('test.jpg', layout_fun=layout_fun))
