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

This program will take a list of images and produce a PDF file with the
images embedded in it.  JPEG and JPEG2000 images will be included without
recompression.  Images in other formats will be included with zip/flate
encoding.  As a result, this tool is able to losslessly wrap any image
into a PDF container with a quality-filesize ratio that is typically better
than that of existing tools.

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
img2pdf is several (ten to hundred) times faster than with imagemagick.
While the above convert command with a 2.8MB JPEG took 27 seconds
(on average) on my machine, conversion using img2pdf took just a
fraction of a second.

Commandline Arguments
---------------------

At least one input file argument must be given as img2pdf needs to seek in the
file descriptor which would not be possible for stdin.

Specify the dpi with the -d or --dpi options instead of reading it from the
image or falling back to 96.0.

Specify the output file with -o or --output. By default output will be done to
stdout.

Specify metadata using the --title, --author, --creator, --producer,
--creationdate, --moddate, --subject and --keywords options (or their short
forms).

Specify -C or --colorspace to force a colorspace using PIL short handles like
'RGB', 'L' or '1'.

More help is available with the -h or --help option.

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
