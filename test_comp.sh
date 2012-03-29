#!/bin/sh

if [ $# -ne 1 ]; then
	echo "usage: $0 image"
	exit
fi

echo "converting image to pdf, trying all compressions imagemagick has to offer"
echo "if, as a result, Zip/FlateDecode should NOT be the lossless compression with the lowest size ratio, contact me j [dot] schauer [at] email [dot] de"
echo "also, send me the image in question"
echo

imsize=`stat -c "%s" "$1"`

for a in `convert -list compress`; do
	echo "encode:\t$a"
	convert "$1" -compress $a "`basename $1 .jpg`.pdf"
	pdfimages "`basename $1 .jpg`.pdf" "`basename $1 .jpg`"
	/bin/echo -ne "diff:\t"
	diff=`compare -metric AE "$1" "\`basename $1 .jpg\`-000.ppm" null: 2>&1`
	if [ "$diff" != "0" ]; then
		echo "lossy"
	else
		echo "lossless"
	fi
	/bin/echo -ne "size:\t"
	pdfsize=`stat -c "%s" "\`basename $1 .jpg\`.pdf"`
	echo "scale=1;$pdfsize/$imsize" | bc
	/bin/echo -ne "pdf:\t"
	grep --max-count=1 --text /Filter "`basename $1 .jpg`.pdf"
	echo
done
