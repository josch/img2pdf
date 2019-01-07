=======
CHANGES
=======

0.3.3 (2019-01-07)
------------------

 - restore basic support for Python 2
 - also ship test.sh
 - add legal and tabloid paper formats
 - respect exif rotation tag

0.3.2 (2018-11-20)
------------------

 - support big endian TIFF with lsb-to-msb FillOrder
 - support multipage CCITT Group 4 TIFF
 - also reject palette images with transparency
 - support PNG images with 1, 2, 4 or 16 bits per sample
 - support multipage TIFF with differently encoded images
 - support CCITT Group4 TIFF without rows-per-strip
 - add extensive test suite

0.3.1 (2018-08-04)
------------------

 - Directly copy data from CCITT Group 4 encoded TIFF images into the PDF
   container without re-encoding

0.3.0 (2018-06-18)
------------------

 - Store non-jpeg images using PNG compression
 - Support arbitrarily large pages via PDF /UserUnit field
 - Disallow input with alpha channel as it cannot be preserved
 - Add option --pillow-limit-break to support very large input

0.2.4 (2017-05-23)
------------------

 - Restore support for Python 2.7
 - Add support for PyPy
 - Add support for testing using tox

0.2.3 (2017-01-20)
------------------

 - version number bump for botched pypi upload...

0.2.2 (2017-01-20)
------------------

 - automatic monochrome CCITT Group4 encoding via Pillow/libtiff

0.2.1 (2016-05-04)
------------------

 - set img2pdf as /producer value
 - support multi-frame images like multipage TIFF and animated GIF
 - support for palette images like GIF
 - support all colorspaces and imageformats known by PIL
 - read horizontal and vertical dpi from JPEG2000 files

0.2.0 (2015-05-10)
------------------

 - now Python3 only
 - pep8 compliant code
 - update my email to josch@mister-muffin.de
 - move from github to gitlab.mister-muffin.de/josch/img2pdf
 - use logging module
 - add extensive test suite
 - ability to read from standard input
 - pdf writer:
      - make more compatible with the interface of pdfrw module
      - print floats which equal to their integer conversion as integer
      - do not print trailing zeroes for floating point numbers
      - print more linebreaks
      - add binary string at beginning of PDF to indicate that the PDF
        contains binary data
      - handle datetime and unicode strings by using utf-16-be encoding
 - new options (see --help for more details):
      - --without-pdfrw
      - --imgsize
      - --border
      - --fit
      - --auto-orient
      - --viewer-panes
      - --viewer-initial-page
      - --viewer-magnification
      - --viewer-page-layout
      - --viewer-fit-window
      - --viewer-center-window
      - --viewer-fullscreen
 - remove short options for metadata command line arguments
 - correctly encode and escape non-ascii metadata
 - explicitly store date in UTC and allow parsing all date formats understood
   by dateutil and `date --date`

0.1.5 (2015-02-16)
------------------

- Enable support for CMYK images
- Rework test suite
- support file objects as input

0.1.4 (2015-01-21)
------------------

- add Python 3 support
- make output reproducible by sorting and --nodate option

0.1.3 (2014-11-10)
------------------

- Avoid leaking file descriptors
- Convert unrecognized colorspaces to RGB

0.1.1 (2014-09-07)
------------------

- allow running src/img2pdf.py standalone
- license change from GPL to LGPL
- Add pillow 2.4.0 support
- add options to specify pdf dimensions in points

0.1.0 (2014-03-14, unreleased)
------------------

- Initial PyPI release.
- Modified code to create proper package.
- Added tests.
- Added console script entry point.
