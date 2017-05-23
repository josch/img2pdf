=======
CHANGES
=======

0.2.4
-----

 - Restore support for Python 2.7
 - Add support for PyPy
 - Add support for testing using tox

0.2.3
-----

 - version number bump for botched pypi upload...

0.2.2
-----

 - automatic monochrome CCITT Group4 encoding via Pillow/libtiff

0.2.1
-----

 - set img2pdf as /producer value
 - support multi-frame images like multipage TIFF and animated GIF
 - support for palette images like GIF
 - support all colorspaces and imageformats knows by PIL
 - read horizontal and vertical dpi from JPEG2000 files

0.2.0
-----

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

0.1.5
-----

- Enable support for CMYK images
- Rework test suite
- support file objects as input

0.1.4
-----

- add Python 3 support
- make output reproducible by sorting and --nodate option

0.1.3
-----

- Avoid leaking file descriptors
- Convert unrecognized colorspaces to RGB

0.1.1
-----

- allow running src/img2pdf.py standalone
- license change from GPL to LGPL
- Add pillow 2.4.0 support
- add options to specify pdf dimensions in points

0.1.0 (unreleased)
------------------

- Initial PyPI release.
- Modified code to create proper package.
- Added tests.
- Added console script entry point.
