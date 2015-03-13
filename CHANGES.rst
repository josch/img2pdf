=======
CHANGES
=======

0.1.6
-----

 - replace -x and -y option by combined option -s (or --pagesize) and use -S
   for --subject
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
