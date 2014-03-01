from setuptools import setup

setup (
    name='img2pdf',
    version='1.0.0.dev0',
    author = "Johannes 'josch' Schauer",
    description = "Convert images to PDF via direct JPEG inclusion.",
    long_description = open('README.md').read(),
    license = "GPL",
    keywords = "jpeg pdf converter",
    classifiers = [
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: Implementation :: CPython',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Programming Language :: Python',
        'Natural Language :: English',
        'Operating System :: OS Independent'],
    url = 'http://pypi.python.org/pypi/img2pdf',
    package_dir={"": "src"},
    py_modules=['img2pdf', 'jp2'],
    include_package_data = True,
    test_suite = 'tests.test_suite',
    zip_safe = True,
    install_requires=(
        'Pillow',
    ),
    entry_points='''
    [console_scripts]
    img2pdf = img2pdf:main
    ''',
    )
