import sys
from setuptools import setup

VERSION = "0.3.6"

INSTALL_REQUIRES = (
    'Pillow',
)

TESTS_REQUIRE = (
    'pdfrw',
)


setup(
    name='img2pdf',
    version=VERSION,
    author="Johannes 'josch' Schauer",
    author_email='josch@mister-muffin.de',
    description="Convert images to PDF via direct JPEG inclusion.",
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    license="LGPL",
    keywords="jpeg pdf converter",
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Intended Audience :: Other Audience',
        'Environment :: Console',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: Implementation :: CPython',
        "Programming Language :: Python :: Implementation :: PyPy",
        'License :: OSI Approved :: GNU Lesser General Public License v3 '
        '(LGPLv3)',
        'Natural Language :: English',
        'Operating System :: OS Independent'],
    url='https://gitlab.mister-muffin.de/josch/img2pdf',
    download_url='https://gitlab.mister-muffin.de/josch/img2pdf/repository/'
        'archive.tar.gz?ref=' + VERSION,
    package_dir={"": "src"},
    py_modules=['img2pdf', 'jp2'],
    include_package_data=True,
    test_suite='tests.test_suite',
    zip_safe=True,
    install_requires=INSTALL_REQUIRES,
    tests_requires=TESTS_REQUIRE,
    extras_require={
        'test': TESTS_REQUIRE,
        'gui': ('tkinter'),
    },
    entry_points={
        "setuptools.installation": ["eggsecutable = img2pdf:main"],
        "console_scripts": ["img2pdf = img2pdf:main"],
        "gui_scripts": ["img2pdf-gui = img2pdf:gui"],
    },
    )
