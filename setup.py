from setuptools import setup

VERSION = "0.2.1"

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
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: Implementation :: CPython',
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
    install_requires=(
        'Pillow',
    ),
    entry_points='''
    [console_scripts]
    img2pdf = img2pdf:main
    ''',
    )
