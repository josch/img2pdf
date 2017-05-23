import sys
from setuptools import setup

PY3 = sys.version_info[0] >= 3

VERSION = "0.2.4"

INSTALL_REQUIRES = (
    'Pillow',
)

TESTS_REQUIRE = (
    'pdfrw',
)

if not PY3:
    INSTALL_REQUIRES += ('enum34',)


setup(
    name='img2pdf',
    version=VERSION,
    author="Johannes 'josch' Schauer",
    author_email='josch@mister-muffin.de',
    description="Convert images to PDF via direct JPEG inclusion.",
    long_description=open('README.md').read(),
    license="LGPL",
    keywords="jpeg pdf converter",
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Intended Audience :: Other Audience',
        'Environment :: Console',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
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
    },
    entry_points='''
    [console_scripts]
    img2pdf = img2pdf:main
    ''',
    )
