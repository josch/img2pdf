img2pdf
=======

Lossless conversion of images to PDF without unnecessarily re-encoding JPEG and
JPEG2000 files. Thus, no loss of quality and no unnecessary large output file.

Background
----------

PDF is able to embed JPEG and JPEG2000 images as they are without re-encoding
them (and hence loosing quality) but I was missing a tool to do this
automatically, thus I wrote this piece of python code.

If you know how to embed JPEG and JPEG2000 images into a PDF container without
recompression, using existing tools, please contact me so that I can put this
code into the garbage bin :D

Functionality
-------------

The program will take image filenames from commandline arguments and output a
PDF file with them embedded into it. If the input image is a JPEG or JPEG2000
file, it will be included as-is without any processing. If it is in any other
format, the image will be included as zip-encoded RGB. As a result, this tool
will be able to lossless wrap any image into a PDF container while performing
better (in terms of quality/filesize ratio) than existing tools in case the
input image is a JPEG or JPEG2000 file.

For example, imagemagick will re-encode the input JPEG image and thus change
its content:

	$ convert img.jpg img.pdf
	$ pdfimages img.pdf img.extr # not using -j to be extra sure there is no recompression
	$ compare -metric AE img.jpg img.extr-000.ppm null:
	1.6301e+06

If one wants to do a lossless conversion from any format to PDF with
imagemagick then one has to use zip-encoding:

	$ convert input.jpg -compress Zip output.pdf
	$ pdfimages img.pdf img.extr # not using -j to be extra sure there is no recompression
	$ compare -metric AE img.jpg img.extr-000.ppm null:
	0

The downside is, that using imagemagick like this will make the resulting PDF
files a few times bigger than the input JPEG or JPEG2000 file and can also not
output a multipage PDF.

img2pdf is able to output a PDF with multiple pages if more than one input
image is given, losslessly embed JPEG and JPEG2000 files into a PDF container
without adding more overhead than the PDF structure itself and will save all
other graphics formats using lossless zip-compression.

Another nifty advantage: Since no re-encoding is done in case of JPEG images,
the conversion is many (ten to hundred) times faster with img2pdf compared to
imagemagick. While a run of above convert command with a 2.8MB JPEG takes 27
seconds (on average) on my machine, conversion using img2pdf takes just a
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

If you find a JPEG or JPEG2000 file that, when embedded can not be read by the
Adobe Acrobat Reader, please contact me.

For lossless conversion of other formats than JPEG or JPEG2000 files, zip/flate
encoding is used.  This choice is based on a number of tests I did on images.
I converted them into PDF using imagemagick and all compressions it has to
offer and then compared the output size of the lossless variants. In all my
tests, zip/flate encoding performed best. You can verify my findings using the
test_comp.sh script with any input image given as a commandline argument. If
you find an input file that is outperformed by another lossless compression,
contact me.

I have not yet figured out how to read the colorspace from jpeg2000 files.
Therefor jpeg2000 files use DeviceRGB per default. If your jpeg2000 files are
of any other colorspace you must force it using the --colorspace option.
Like -C L for DeviceGray.

Installation
------------

You can install the package using:

	$ pip install img2pdf

If you want to install from source code simply use:

	$ cd img2pdf/
	$ pip install .

To test the console script without installing the package on your system,
simply use virtualenv:

	$ cd img2pdf/
	$ virtualenv ve
	$ ve/bin/pip install .

You can then test the converter using:

	$ ve/bin/img2pdf -o test.pdf src/tests/test.jpg

Note that the package can also be used as a library as follows:

	import img2pdf
	pdf_bytes = img2pdf('test.jpg', dpi=150)
