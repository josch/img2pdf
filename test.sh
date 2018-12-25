#!/bin/sh

set -eu

similar()
{
	psnr=$(compare -metric PSNR "$1" "$2" null: 2>&1 || true)
	if [ -z "$psnr" ]; then
		echo "compare failed"
		return 1
	fi

	# PSNR of zero means that they are identical
	if [ "$psnr" = 0 ]; then
		echo "images are equal -- don't use similar() but require exactness"
		exit 2
	fi

	# The lower PSNR value, the fewer the similarities
	# The lowest (and worst) value is 1.0
	min_psnr=50
	if [ "$min_psnr" != "$( printf "$psnr\n$min_psnr\n" | sort --general-numeric-sort | head --lines=1)" ]; then
		echo "pdf wrongly rendered"
		return 1
	fi
	return 0
}

compare_rendered()
{
	pdf="$1"
	img="$2"
	gsdevice=png16m
	if [ "$#" -eq 3 ]; then
		gsdevice="$3"
	fi

	compare_ghostscript "$pdf" "$img" "$gsdevice"

	compare_poppler "$pdf" "$img"

	compare_mupdf "$pdf" "$img"
}

compare_ghostscript()
{
	pdf="$1"
	img="$2"
	gsdevice="$3"
	gs -dQUIET -dNOPAUSE -dBATCH -sDEVICE="$gsdevice" -r96 -sOutputFile="$tempdir/gs-%00d.png" "$pdf"
	compare -metric AE "$img" "$tempdir/gs-1.png" null: 2>/dev/null
	rm "$tempdir/gs-1.png"
}

compare_poppler()
{
	pdf="$1"
	img="$2"
	pdftocairo -r 96 -png "$pdf" "$tempdir/poppler"
	compare -metric AE "$img" "$tempdir/poppler-1.png" null: 2>/dev/null
	rm "$tempdir/poppler-1.png"
}

compare_mupdf()
{
	pdf="$1"
	img="$2"
	mutool draw -o "$tempdir/mupdf.png" -r 96 "$pdf" 2>/dev/null
	compare -metric AE "$img" "$tempdir/mupdf.png" null: 2>/dev/null
	rm "$tempdir/mupdf.png"
}

compare_pdfimages()
{
	pdf="$1"
	img="$2"
	pdfimages -png "$pdf" "$tempdir/images"
	compare -metric AE "$img" "$tempdir/images-000.png" null: 2>/dev/null
	rm "$tempdir/images-000.png"
}

error()
{
	echo test $j failed
	echo intermediate data is left in $tempdir
	exit 1
}

tempdir=$(mktemp --directory --tmpdir img2pdf.XXXXXXXXXX)

trap error EXIT

# we use -strip to remove all timestamps (tIME chunk and exif data)
convert -size 60x60 \( xc:none -fill red -draw 'circle 30,21 30,3' -gaussian-blur 0x3 \) \
	\( \( xc:none -fill lime -draw 'circle 39,39 36,57' -gaussian-blur 0x3 \) \
	   \( xc:none -fill blue -draw 'circle 21,39 24,57' -gaussian-blur 0x3 \) \
	   -compose plus -composite \
	\) -compose plus -composite \
	-strip \
	"$tempdir/alpha.png"

convert "$tempdir/alpha.png" -background black -alpha remove -alpha off -strip "$tempdir/normal16.png"

convert "$tempdir/normal16.png" -depth 8 -strip "$tempdir/normal.png"

convert "$tempdir/normal.png" -negate -strip "$tempdir/inverse.png"

convert "$tempdir/normal16.png" -colorspace Gray -depth 16 -strip "$tempdir/gray16.png"
convert "$tempdir/normal16.png" -colorspace Gray -dither FloydSteinberg -colors 256 -depth 8 -strip "$tempdir/gray8.png"
convert "$tempdir/normal16.png" -colorspace Gray -dither FloydSteinberg -colors 16 -depth 4 -strip "$tempdir/gray4.png"
convert "$tempdir/normal16.png" -colorspace Gray -dither FloydSteinberg -colors 4 -depth 2 -strip "$tempdir/gray2.png"
convert "$tempdir/normal16.png" -colorspace Gray -dither FloydSteinberg -colors 2 -depth 1 -strip "$tempdir/gray1.png"

# use "-define png:exclude-chunk=bkgd" because otherwise, imagemagick will
# add the background color (white) as an additional entry to the palette
convert "$tempdir/normal.png" -dither FloydSteinberg -colors 2 -define png:exclude-chunk=bkgd -strip "$tempdir/palette1.png"
convert "$tempdir/normal.png" -dither FloydSteinberg -colors 4 -define png:exclude-chunk=bkgd -strip "$tempdir/palette2.png"
convert "$tempdir/normal.png" -dither FloydSteinberg -colors 16 -define png:exclude-chunk=bkgd -strip "$tempdir/palette4.png"
convert "$tempdir/normal.png" -dither FloydSteinberg -colors 256 -define png:exclude-chunk=bkgd -strip "$tempdir/palette8.png"

cat << END | ( cd "$tempdir"; md5sum --check --status - )
a99ef2a356c315090b6939fa4ce70516  alpha.png
0df21ebbce5292654119b17f6e52bc81  gray16.png
6faee81b8db446caa5004ad71bddcb5b  gray1.png
97e423da517ede069348484a1283aa6c  gray2.png
cbed1b6da5183aec0b86909e82b77c41  gray4.png
c0df42fdd69ae2a16ad0c23adb39895e  gray8.png
ac6bb850fb5aaee9fa7dcb67525cd0fc  inverse.png
3f3f8579f5054270e79a39e7cc4e89e0  normal16.png
cbe63b21443af8321b213bde6666951f  normal.png
2f00705cca05fd94406fc39ede4d7322  palette1.png
6cb250d1915c2af99c324c43ff8286eb  palette2.png
ab7b3d3907a851692ee36f5349ed0b2c  palette4.png
03829af4af8776adf56ba2e68f5b111e  palette8.png
END

# use img2pdfprog environment variable if it is set
if [ -z ${img2pdfprog+x} ]; then
	img2pdfprog=src/img2pdf.py
fi

img2pdf()
{
	# we use --without-pdfrw to better "grep" the result and because we
	# cannot write palette based images otherwise
	$img2pdfprog --without-pdfrw --producer="" --nodate "$1" > "$2" 2>/dev/null
}

tests=51 # number of tests
j=1      # current test

###############################################################################
echo "Test $j/$tests JPEG"

convert "$tempdir/normal.png" "$tempdir/normal.jpg"

identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Format: JPEG (Joint Photographic Experts Group JFIF format)$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Mime type: image/jpeg$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Type: TrueColor$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Compression: JPEG$'

img2pdf "$tempdir/normal.jpg" "$tempdir/out.pdf"

# We have to use jpegtopnm with the original JPG before being able to compare
# it with imagemagick because imagemagick will decode the JPG slightly
# differently than ghostscript, poppler and mupdf do it.
# We have to use jpegtopnm and cannot use djpeg because the latter produces
# slightly different results as well when called like this:
#    djpeg -dct int -pnm "$tempdir/normal.jpg" > "$tempdir/normal.pnm"
# An alternative way to compare the JPG would be to require a different DCT
# method when decoding by setting -define jpeg:dct-method=ifast in the
# compare command.
jpegtopnm -dct int "$tempdir/normal.jpg" > "$tempdir/normal.pnm" 2>/dev/null

compare_rendered "$tempdir/out.pdf" "$tempdir/normal.pnm"

pdfimages -j "$tempdir/out.pdf" "$tempdir/images"
cmp "$tempdir/normal.jpg" "$tempdir/images-000.jpg"
rm "$tempdir/images-000.jpg"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 8$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceRGB$' "$tempdir/out.pdf"
grep --quiet '^    /Filter /DCTDecode$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/normal.jpg" "$tempdir/normal.pnm" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
echo "Test $j/$tests JPEG (90° rotated)"

convert "$tempdir/normal.png" "$tempdir/normal.jpg"
exiftool -overwrite_original -all= "$tempdir/normal.jpg" -n >/dev/null
exiftool -overwrite_original -Orientation=6 -XResolution=96 -YResolution=96 -n "$tempdir/normal.jpg" >/dev/null

identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Format: JPEG (Joint Photographic Experts Group JFIF format)$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Mime type: image/jpeg$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Type: TrueColor$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Compression: JPEG$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^    exif:Orientation: 6$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^    exif:ResolutionUnit: 2$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^    exif:XResolution: 96/1$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^    exif:YResolution: 96/1$'

img2pdf "$tempdir/normal.jpg" "$tempdir/out.pdf"

# We have to use jpegtopnm with the original JPG before being able to compare
# it with imagemagick because imagemagick will decode the JPG slightly
# differently than ghostscript, poppler and mupdf do it.
# We have to use jpegtopnm and cannot use djpeg because the latter produces
# slightly different results as well when called like this:
#    djpeg -dct int -pnm "$tempdir/normal.jpg" > "$tempdir/normal.pnm"
# An alternative way to compare the JPG would be to require a different DCT
# method when decoding by setting -define jpeg:dct-method=ifast in the
# compare command.
jpegtopnm -dct int "$tempdir/normal.jpg" > "$tempdir/normal.pnm" 2>/dev/null
convert -rotate "90" "$tempdir/normal.pnm" "$tempdir/normal_rotated.png"
#convert -rotate "0" "$tempdir/normal.pnm" "$tempdir/normal_rotated.png"

compare_rendered "$tempdir/out.pdf" "$tempdir/normal_rotated.png"

pdfimages -j "$tempdir/out.pdf" "$tempdir/images"
cmp "$tempdir/normal.jpg" "$tempdir/images-000.jpg"
rm "$tempdir/images-000.jpg"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 8$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceRGB$' "$tempdir/out.pdf"
grep --quiet '^    /Filter /DCTDecode$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"
grep --quiet '^    /Rotate 90$' "$tempdir/out.pdf"

rm "$tempdir/normal.jpg" "$tempdir/normal.pnm" "$tempdir/out.pdf" "$tempdir/normal_rotated.png"
j=$((j+1))

###############################################################################
echo "Test $j/$tests JPEG CMYK"

convert "$tempdir/normal.png" -colorspace cmyk "$tempdir/normal.jpg"

identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Format: JPEG (Joint Photographic Experts Group JFIF format)$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Mime type: image/jpeg$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Colorspace: CMYK$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Type: ColorSeparation$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.jpg" | grep --quiet '^  Compression: JPEG$'

img2pdf "$tempdir/normal.jpg" "$tempdir/out.pdf"

gs -dQUIET -dNOPAUSE -dBATCH -sDEVICE=tiff32nc -r96 -sOutputFile="$tempdir/gs-%00d.tiff" "$tempdir/out.pdf"
similar "$tempdir/normal.jpg" "$tempdir/gs-1.tiff"
rm "$tempdir/gs-1.tiff"

# not testing with poppler as it cannot write CMYK images

mutool draw -o "$tempdir/mupdf.pam" -r 96 -c cmyk "$pdf" 2>/dev/null
similar "$tempdir/normal.jpg" "$tempdir/mupdf.pam"
rm "$tempdir/mupdf.pam"

pdfimages -j "$tempdir/out.pdf" "$tempdir/images"
cmp "$tempdir/normal.jpg" "$tempdir/images-000.jpg"
rm "$tempdir/images-000.jpg"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 8$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceCMYK$' "$tempdir/out.pdf"
grep --quiet '^    /Decode \[ 1 0 1 0 1 0 1 0 \]$' "$tempdir/out.pdf"
grep --quiet '^    /Filter /DCTDecode$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/normal.jpg" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
echo "Test $j/$tests JPEG2000"

convert "$tempdir/normal.png" "$tempdir/normal.jp2"

identify -verbose "$tempdir/normal.jp2" | grep --quiet '^  Format: JP2 (JPEG-2000 File Format Syntax)$'
identify -verbose "$tempdir/normal.jp2" | grep --quiet '^  Mime type: image/jp2$'
identify -verbose "$tempdir/normal.jp2" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.jp2" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/normal.jp2" | grep --quiet '^  Type: TrueColor$'
identify -verbose "$tempdir/normal.jp2" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/normal.jp2" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.jp2" | grep --quiet '^  Compression: JPEG2000$'

img2pdf "$tempdir/normal.jp2" "$tempdir/out.pdf"

compare_rendered "$tempdir/out.pdf" "$tempdir/normal.jp2"

pdfimages -jp2 "$tempdir/out.pdf" "$tempdir/images"
cmp "$tempdir/normal.jp2" "$tempdir/images-000.jp2"
rm "$tempdir/images-000.jp2"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 8$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceRGB$' "$tempdir/out.pdf"
grep --quiet '^    /Filter /JPXDecode$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/normal.jp2" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
#echo Test JPEG2000 CMYK
#
# cannot test because imagemagick does not support JPEG2000 CMYK

###############################################################################
echo "Test $j/$tests PNG RGB8"

identify -verbose "$tempdir/normal.png" | grep --quiet '^  Format: PNG (Portable Network Graphics)$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^  Mime type: image/png$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^  Type: TrueColor$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^    png:IHDR.bit-depth-orig: 8$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^    png:IHDR.bit_depth: 8$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^    png:IHDR.color-type-orig: 2$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^    png:IHDR.color_type: 2 (Truecolor)$'
identify -verbose "$tempdir/normal.png" | grep --quiet '^    png:IHDR.interlace_method: 0 (Not interlaced)$'

img2pdf "$tempdir/normal.png" "$tempdir/out.pdf"

compare_rendered "$tempdir/out.pdf" "$tempdir/normal.png"

compare_pdfimages "$tempdir/out.pdf" "$tempdir/normal.png"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 8$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceRGB$' "$tempdir/out.pdf"
grep --quiet '^        /BitsPerComponent 8$' "$tempdir/out.pdf"
grep --quiet '^        /Colors 3$' "$tempdir/out.pdf"
grep --quiet '^        /Predictor 15$' "$tempdir/out.pdf"
grep --quiet '^    /Filter /FlateDecode$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
echo "Test $j/$tests PNG RGB16"

identify -verbose "$tempdir/normal16.png" | grep --quiet '^  Format: PNG (Portable Network Graphics)$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^  Mime type: image/png$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^  Type: TrueColor$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^  Depth: 16-bit$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^    png:IHDR.bit-depth-orig: 16$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^    png:IHDR.bit_depth: 16$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^    png:IHDR.color-type-orig: 2$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^    png:IHDR.color_type: 2 (Truecolor)$'
identify -verbose "$tempdir/normal16.png" | grep --quiet '^    png:IHDR.interlace_method: 0 (Not interlaced)$'

img2pdf "$tempdir/normal16.png" "$tempdir/out.pdf"

compare_ghostscript "$tempdir/out.pdf" "$tempdir/normal16.png" tiff48nc

# poppler outputs 8-bit RGB so the comparison will not be exact
pdftocairo -r 96 -png "$tempdir/out.pdf" "$tempdir/poppler"
similar "$tempdir/normal16.png" "$tempdir/poppler-1.png"
rm "$tempdir/poppler-1.png"

# pdfimages is unable to write 16 bit output

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 16$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceRGB$' "$tempdir/out.pdf"
grep --quiet '^        /BitsPerComponent 16$' "$tempdir/out.pdf"
grep --quiet '^        /Colors 3$' "$tempdir/out.pdf"
grep --quiet '^        /Predictor 15$' "$tempdir/out.pdf"
grep --quiet '^    /Filter /FlateDecode$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
echo "Test $j/$tests PNG RGBA8"

convert "$tempdir/alpha.png" -depth 8 -strip "$tempdir/alpha8.png"

identify -verbose "$tempdir/alpha8.png" | grep --quiet '^  Format: PNG (Portable Network Graphics)$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^  Mime type: image/png$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^  Type: TrueColorAlpha$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^    png:IHDR.bit-depth-orig: 8$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^    png:IHDR.bit_depth: 8$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^    png:IHDR.color-type-orig: 6$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^    png:IHDR.color_type: 6 (RGBA)$'
identify -verbose "$tempdir/alpha8.png" | grep --quiet '^    png:IHDR.interlace_method: 0 (Not interlaced)$'

img2pdf "$tempdir/alpha8.png" /dev/null && rc=$? || rc=$?
if [ "$rc" -eq 0 ]; then
	echo needs to fail here
	exit 1
fi

rm "$tempdir/alpha8.png"
j=$((j+1))

###############################################################################
echo "Test $j/$tests PNG RGBA16"

identify -verbose "$tempdir/alpha.png" | grep --quiet '^  Format: PNG (Portable Network Graphics)$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^  Mime type: image/png$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^  Type: TrueColorAlpha$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^  Depth: 16-bit$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^    png:IHDR.bit-depth-orig: 16$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^    png:IHDR.bit_depth: 16$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^    png:IHDR.color-type-orig: 6$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^    png:IHDR.color_type: 6 (RGBA)$'
identify -verbose "$tempdir/alpha.png" | grep --quiet '^    png:IHDR.interlace_method: 0 (Not interlaced)$'

img2pdf "$tempdir/alpha.png" /dev/null && rc=$? || rc=$?
if [ "$rc" -eq 0 ]; then
	echo needs to fail here
	exit 1
fi

j=$((j+1))

###############################################################################
echo "Test $j/$tests PNG Gray8 Alpha"

convert "$tempdir/alpha.png" -colorspace Gray -dither FloydSteinberg -colors 256 -depth 8 -strip "$tempdir/alpha_gray8.png"

identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^  Format: PNG (Portable Network Graphics)$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^  Mime type: image/png$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^  Colorspace: Gray$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^  Type: GrayscaleAlpha$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^    png:IHDR.bit-depth-orig: 8$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^    png:IHDR.bit_depth: 8$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^    png:IHDR.color-type-orig: 4$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^    png:IHDR.color_type: 4 (GrayAlpha)$'
identify -verbose "$tempdir/alpha_gray8.png" | grep --quiet '^    png:IHDR.interlace_method: 0 (Not interlaced)$'

img2pdf "$tempdir/alpha_gray8.png" /dev/null && rc=$? || rc=$?
if [ "$rc" -eq 0 ]; then
	echo needs to fail here
	exit 1
fi

rm "$tempdir/alpha_gray8.png"
j=$((j+1))

###############################################################################
echo "Test $j/$tests PNG Gray16 Alpha"

convert "$tempdir/alpha.png" -colorspace Gray -depth 16 -strip "$tempdir/alpha_gray16.png"

identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^  Format: PNG (Portable Network Graphics)$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^  Mime type: image/png$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^  Colorspace: Gray$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^  Type: GrayscaleAlpha$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^  Depth: 16-bit$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^    png:IHDR.bit-depth-orig: 16$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^    png:IHDR.bit_depth: 16$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^    png:IHDR.color-type-orig: 4$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^    png:IHDR.color_type: 4 (GrayAlpha)$'
identify -verbose "$tempdir/alpha_gray16.png" | grep --quiet '^    png:IHDR.interlace_method: 0 (Not interlaced)$'

img2pdf "$tempdir/alpha_gray16.png" /dev/null && rc=$? || rc=$?
if [ "$rc" -eq 0 ]; then
	echo needs to fail here
	exit 1
fi

rm "$tempdir/alpha_gray16.png"
j=$((j+1))

###############################################################################
echo "Test $j/$tests PNG interlaced"

convert "$tempdir/normal.png" -interlace PNG -strip "$tempdir/interlace.png"

identify -verbose "$tempdir/interlace.png" | grep --quiet '^  Format: PNG (Portable Network Graphics)$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^  Mime type: image/png$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^  Type: TrueColor$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^    png:IHDR.bit-depth-orig: 8$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^    png:IHDR.bit_depth: 8$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^    png:IHDR.color-type-orig: 2$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^    png:IHDR.color_type: 2 (Truecolor)$'
identify -verbose "$tempdir/interlace.png" | grep --quiet '^    png:IHDR.interlace_method: 1 (Adam7 method)$'

img2pdf "$tempdir/interlace.png" "$tempdir/out.pdf"

compare_rendered "$tempdir/out.pdf" "$tempdir/normal.png"

compare_pdfimages "$tempdir/out.pdf" "$tempdir/normal.png"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 8$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceRGB$' "$tempdir/out.pdf"
grep --quiet '^        /BitsPerComponent 8$' "$tempdir/out.pdf"
grep --quiet '^        /Colors 3$' "$tempdir/out.pdf"
grep --quiet '^        /Predictor 15$' "$tempdir/out.pdf"
grep --quiet '^    /Filter /FlateDecode$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/interlace.png" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
for i in 1 2 4 8; do
	echo "Test $j/$tests PNG Gray$i"

	identify -verbose "$tempdir/gray$i.png" | grep --quiet '^  Format: PNG (Portable Network Graphics)$'
	identify -verbose "$tempdir/gray$i.png" | grep --quiet '^  Mime type: image/png$'
	identify -verbose "$tempdir/gray$i.png" | grep --quiet '^  Geometry: 60x60+0+0$'
	identify -verbose "$tempdir/gray$i.png" | grep --quiet '^  Colorspace: Gray$'
	if [ "$i" -eq 1 ]; then
		identify -verbose "$tempdir/gray$i.png" | grep --quiet '^  Type: Bilevel$'
	else
		identify -verbose "$tempdir/gray$i.png" | grep --quiet '^  Type: Grayscale$'
	fi
	if [ "$i" -eq 8 ]; then
		identify -verbose "$tempdir/gray$i.png" | grep --quiet "^  Depth: 8-bit$"
	else
		identify -verbose "$tempdir/gray$i.png" | grep --quiet "^  Depth: 8/$i-bit$"
	fi
	identify -verbose "$tempdir/gray$i.png" | grep --quiet '^  Page geometry: 60x60+0+0$'
	identify -verbose "$tempdir/gray$i.png" | grep --quiet '^  Compression: Zip$'
	identify -verbose "$tempdir/gray$i.png" | grep --quiet "^    png:IHDR.bit-depth-orig: $i$"
	identify -verbose "$tempdir/gray$i.png" | grep --quiet "^    png:IHDR.bit_depth: $i$"
	identify -verbose "$tempdir/gray$i.png" | grep --quiet '^    png:IHDR.color-type-orig: 0$'
	identify -verbose "$tempdir/gray$i.png" | grep --quiet '^    png:IHDR.color_type: 0 (Grayscale)$'
	identify -verbose "$tempdir/gray$i.png" | grep --quiet '^    png:IHDR.interlace_method: 0 (Not interlaced)$'

	img2pdf "$tempdir/gray$i.png" "$tempdir/out.pdf"

	compare_rendered "$tempdir/out.pdf" "$tempdir/gray$i.png" pnggray

	compare_pdfimages "$tempdir/out.pdf" "$tempdir/gray$i.png"

	grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
	grep --quiet '^    /BitsPerComponent '"$i"'$' "$tempdir/out.pdf"
	grep --quiet '^    /ColorSpace /DeviceGray$' "$tempdir/out.pdf"
	grep --quiet '^        /BitsPerComponent '"$i"'$' "$tempdir/out.pdf"
	grep --quiet '^        /Colors 1$' "$tempdir/out.pdf"
	grep --quiet '^        /Predictor 15$' "$tempdir/out.pdf"
	grep --quiet '^    /Filter /FlateDecode$' "$tempdir/out.pdf"
	grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
	grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

	rm "$tempdir/out.pdf"
	j=$((j+1))
done

###############################################################################
echo "Test $j/$tests PNG Gray16"

identify -verbose "$tempdir/gray16.png" | grep --quiet '^  Format: PNG (Portable Network Graphics)$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^  Mime type: image/png$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^  Colorspace: Gray$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^  Type: Grayscale$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^  Depth: 16-bit$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^    png:IHDR.bit-depth-orig: 16$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^    png:IHDR.bit_depth: 16$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^    png:IHDR.color-type-orig: 0$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^    png:IHDR.color_type: 0 (Grayscale)$'
identify -verbose "$tempdir/gray16.png" | grep --quiet '^    png:IHDR.interlace_method: 0 (Not interlaced)$'

img2pdf "$tempdir/gray16.png" "$tempdir/out.pdf"

# ghostscript outputs 8-bit grayscale, so the comparison will not be exact
gs -dQUIET -dNOPAUSE -dBATCH -sDEVICE=pnggray -r96 -sOutputFile="$tempdir/gs-%00d.png" "$tempdir/out.pdf"
similar "$tempdir/gray16.png" "$tempdir/gs-1.png"
rm "$tempdir/gs-1.png"

# poppler outputs 8-bit grayscale so the comparison will not be exact
pdftocairo -r 96 -png "$tempdir/out.pdf" "$tempdir/poppler"
similar "$tempdir/gray16.png" "$tempdir/poppler-1.png"
rm "$tempdir/poppler-1.png"

# pdfimages outputs 8-bit grayscale so the comparison will not be exact
pdfimages -png "$tempdir/out.pdf" "$tempdir/images"
similar "$tempdir/gray16.png" "$tempdir/images-000.png"
rm "$tempdir/images-000.png"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 16$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceGray$' "$tempdir/out.pdf"
grep --quiet '^        /BitsPerComponent 16$' "$tempdir/out.pdf"
grep --quiet '^        /Colors 1$' "$tempdir/out.pdf"
grep --quiet '^        /Predictor 15$' "$tempdir/out.pdf"
grep --quiet '^    /Filter /FlateDecode$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
for i in 1 2 4 8; do
	echo "Test $j/$tests PNG Palette$i"

	identify -verbose "$tempdir/palette$i.png" | grep --quiet '^  Format: PNG (Portable Network Graphics)$'
	identify -verbose "$tempdir/palette$i.png" | grep --quiet '^  Mime type: image/png$'
	identify -verbose "$tempdir/palette$i.png" | grep --quiet '^  Geometry: 60x60+0+0$'
	identify -verbose "$tempdir/palette$i.png" | grep --quiet '^  Colorspace: sRGB$'
	identify -verbose "$tempdir/palette$i.png" | grep --quiet '^  Type: Palette$'
	identify -verbose "$tempdir/palette$i.png" | grep --quiet '^  Depth: 8-bit$'
	identify -verbose "$tempdir/palette$i.png" | grep --quiet '^  Page geometry: 60x60+0+0$'
	identify -verbose "$tempdir/palette$i.png" | grep --quiet '^  Compression: Zip$'
	identify -verbose "$tempdir/palette$i.png" | grep --quiet "^    png:IHDR.bit-depth-orig: $i$"
	identify -verbose "$tempdir/palette$i.png" | grep --quiet "^    png:IHDR.bit_depth: $i$"
	identify -verbose "$tempdir/palette$i.png" | grep --quiet '^    png:IHDR.color-type-orig: 3$'
	identify -verbose "$tempdir/palette$i.png" | grep --quiet '^    png:IHDR.color_type: 3 (Indexed)$'
	identify -verbose "$tempdir/palette$i.png" | grep --quiet '^    png:IHDR.interlace_method: 0 (Not interlaced)$'

	img2pdf "$tempdir/palette$i.png" "$tempdir/out.pdf"

	compare_rendered "$tempdir/out.pdf" "$tempdir/palette$i.png"

	# pdfimages cannot export palette based images

	grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
	grep --quiet '^    /BitsPerComponent '"$i"'$' "$tempdir/out.pdf"
	grep --quiet '^    /ColorSpace \[ /Indexed /DeviceRGB ' "$tempdir/out.pdf"
	grep --quiet '^        /BitsPerComponent '"$i"'$' "$tempdir/out.pdf"
	grep --quiet '^        /Colors 1$' "$tempdir/out.pdf"
	grep --quiet '^        /Predictor 15$' "$tempdir/out.pdf"
	grep --quiet '^    /Filter /FlateDecode$' "$tempdir/out.pdf"
	grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
	grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

	rm "$tempdir/out.pdf"
	j=$((j+1))
done

###############################################################################
echo "Test $j/$tests GIF transparent"

convert "$tempdir/alpha.png" "$tempdir/alpha.gif"

identify -verbose "$tempdir/alpha.gif" | grep --quiet '^  Format: GIF (CompuServe graphics interchange format)$'
identify -verbose "$tempdir/alpha.gif" | grep --quiet '^  Mime type: image/gif$'
identify -verbose "$tempdir/alpha.gif" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha.gif" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/alpha.gif" | grep --quiet '^  Type: PaletteAlpha$'
identify -verbose "$tempdir/alpha.gif" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/alpha.gif" | grep --quiet '^  Colormap entries: 256$'
identify -verbose "$tempdir/alpha.gif" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha.gif" | grep --quiet '^  Compression: LZW$'

img2pdf "$tempdir/alpha.gif" /dev/null && rc=$? || rc=$?
if [ "$rc" -eq 0 ]; then
	echo needs to fail here
	exit 1
fi

rm "$tempdir/alpha.gif"
j=$((j+1))

###############################################################################
for i in 1 2 4 8; do
	echo "Test $j/$tests GIF Palette$i"

	convert "$tempdir/palette$i.png" "$tempdir/palette$i.gif"

	identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Format: GIF (CompuServe graphics interchange format)$'
	identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Mime type: image/gif$'
	identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Geometry: 60x60+0+0$'
	identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Colorspace: sRGB$'
	identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Type: Palette$'
	identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Depth: 8-bit$'
	case $i in
		1) identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Colormap entries: 2$';;
		2) identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Colormap entries: 4$';;
		4) identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Colormap entries: 16$';;
		8) identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Colormap entries: 256$';;
	esac
	identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Page geometry: 60x60+0+0$'
	identify -verbose "$tempdir/palette$i.gif" | grep --quiet '^  Compression: LZW$'

	img2pdf "$tempdir/palette$i.gif" "$tempdir/out.pdf"

	compare_rendered "$tempdir/out.pdf" "$tempdir/palette$i.png"

	# pdfimages cannot export palette based images

	grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
	grep --quiet '^    /BitsPerComponent '"$i"'$' "$tempdir/out.pdf"
	grep --quiet '^    /ColorSpace \[ /Indexed /DeviceRGB ' "$tempdir/out.pdf"
	grep --quiet '^        /BitsPerComponent '"$i"'$' "$tempdir/out.pdf"
	grep --quiet '^        /Colors 1$' "$tempdir/out.pdf"
	grep --quiet '^        /Predictor 15$' "$tempdir/out.pdf"
	grep --quiet '^    /Filter /FlateDecode$' "$tempdir/out.pdf"
	grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
	grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

	rm "$tempdir/out.pdf" "$tempdir/palette$i.gif"
	j=$((j+1))
done

###############################################################################
echo "Test $j/$tests GIF animation"

convert "$tempdir/normal.png" "$tempdir/inverse.png" -strip "$tempdir/animation.gif"

identify -verbose "$tempdir/animation.gif[0]" | grep --quiet '^  Format: GIF (CompuServe graphics interchange format)$'
identify -verbose "$tempdir/animation.gif[0]" | grep --quiet '^  Mime type: image/gif$'
identify -verbose "$tempdir/animation.gif[0]" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/animation.gif[0]" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/animation.gif[0]" | grep --quiet '^  Type: Palette$'
identify -verbose "$tempdir/animation.gif[0]" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/animation.gif[0]" | grep --quiet '^  Colormap entries: 256$'
identify -verbose "$tempdir/animation.gif[0]" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/animation.gif[0]" | grep --quiet '^  Compression: LZW$'

identify -verbose "$tempdir/animation.gif[1]" | grep --quiet '^  Format: GIF (CompuServe graphics interchange format)$'
identify -verbose "$tempdir/animation.gif[1]" | grep --quiet '^  Mime type: image/gif$'
identify -verbose "$tempdir/animation.gif[1]" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/animation.gif[1]" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/animation.gif[1]" | grep --quiet '^  Type: Palette$'
identify -verbose "$tempdir/animation.gif[1]" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/animation.gif[1]" | grep --quiet '^  Colormap entries: 256$'
identify -verbose "$tempdir/animation.gif[1]" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/animation.gif[1]" | grep --quiet '^  Compression: LZW$'
identify -verbose "$tempdir/animation.gif[1]" | grep --quiet '^  Scene: 1$'

img2pdf "$tempdir/animation.gif" "$tempdir/out.pdf"

if [ "$(pdfinfo "$tempdir/out.pdf" | awk '/Pages:/ {print $2}')" != 2 ]; then
	echo "pdf does not have 2 pages"
	exit 1
fi

pdfseparate "$tempdir/out.pdf" "$tempdir/page-%d.pdf"
rm "$tempdir/out.pdf"

for page in 1 2; do
	compare_rendered "$tempdir/page-$page.pdf" "$tempdir/animation.gif[$((page-1))]"

	# pdfimages cannot export palette based images

	# We cannot grep the PDF metadata here, because the page was
	# rewritten into a non-greppable format by pdfseparate. but that's
	# okay, because we already grepped single pages before and multipage
	# PDF should not be different.

	rm "$tempdir/page-$page.pdf"
done

rm "$tempdir/animation.gif"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF float"

convert "$tempdir/normal.png" -depth 32 -define quantum:format=floating-point "$tempdir/float.tiff"

identify -verbose "$tempdir/float.tiff" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^  Mime type: image/tiff$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^  Type: TrueColor$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^  Endianess: LSB$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^  Depth: 32/8-bit$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^    quantum:format: floating-point$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^    tiff:alpha: unspecified$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^    tiff:endian: lsb$'
identify -verbose "$tempdir/float.tiff" | grep --quiet '^    tiff:photometric: RGB$'

img2pdf "$tempdir/float.tiff" /dev/null && rc=$? || rc=$?
if [ "$rc" -eq 0 ]; then
	echo needs to fail here
	exit 1
fi

rm "$tempdir/float.tiff"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF CMYK8"

convert "$tempdir/normal.png" -colorspace cmyk "$tempdir/cmyk8.tiff"

identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^  Mime type: image/tiff$'
identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^  Colorspace: CMYK$'
identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^  Type: ColorSeparation$'
identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^  Endianess: LSB$'
identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^    tiff:alpha: unspecified$'
identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^    tiff:endian: lsb$'
identify -verbose "$tempdir/cmyk8.tiff" | grep --quiet '^    tiff:photometric: separated$'

img2pdf "$tempdir/cmyk8.tiff" "$tempdir/out.pdf"

compare_ghostscript "$tempdir/out.pdf" "$tempdir/cmyk8.tiff" tiff32nc

# not testing with poppler as it cannot write CMYK images

mutool draw -o "$tempdir/mupdf.pam" -r 96 -c cmyk "$pdf" 2>/dev/null
compare -metric AE "$tempdir/cmyk8.tiff" "$tempdir/mupdf.pam" null: 2>/dev/null
rm "$tempdir/mupdf.pam"

pdfimages -tiff "$tempdir/out.pdf" "$tempdir/images"
compare -metric AE "$tempdir/cmyk8.tiff" "$tempdir/images-000.tif" null: 2>/dev/null
rm "$tempdir/images-000.tif"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 8$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceCMYK$' "$tempdir/out.pdf"
grep --quiet '^    /Filter /FlateDecode$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/cmyk8.tiff" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF CMYK16"

convert "$tempdir/normal.png" -depth 16 -colorspace cmyk "$tempdir/cmyk16.tiff"

identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^  Mime type: image/tiff$'
identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^  Colorspace: CMYK$'
identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^  Type: ColorSeparation$'
identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^  Endianess: LSB$'
identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^  Depth: 16-bit$'
identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^    tiff:alpha: unspecified$'
identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^    tiff:endian: lsb$'
identify -verbose "$tempdir/cmyk16.tiff" | grep --quiet '^    tiff:photometric: separated$'

# PIL is unable to read 16 bit CMYK images
img2pdf "$tempdir/cmyk16.gif" /dev/null && rc=$? || rc=$?
if [ "$rc" -eq 0 ]; then
	echo needs to fail here
	exit 1
fi

rm "$tempdir/cmyk16.tiff"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF RGB8"

convert "$tempdir/normal.png" "$tempdir/normal.tiff"

identify -verbose "$tempdir/normal.tiff" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
identify -verbose "$tempdir/normal.tiff" | grep --quiet '^  Mime type: image/tiff$'
identify -verbose "$tempdir/normal.tiff" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.tiff" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/normal.tiff" | grep --quiet '^  Type: TrueColor$'
identify -verbose "$tempdir/normal.tiff" | grep --quiet '^  Endianess: LSB$'
identify -verbose "$tempdir/normal.tiff" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/normal.tiff" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/normal.tiff" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/normal.tiff" | grep --quiet '^    tiff:alpha: unspecified$'
identify -verbose "$tempdir/normal.tiff" | grep --quiet '^    tiff:endian: lsb$'
identify -verbose "$tempdir/normal.tiff" | grep --quiet '^    tiff:photometric: RGB$'

img2pdf "$tempdir/normal.tiff" "$tempdir/out.pdf"

compare_rendered "$tempdir/out.pdf" "$tempdir/normal.tiff" tiff24nc

compare_pdfimages "$tempdir/out.pdf" "$tempdir/normal.tiff"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 8$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceRGB$' "$tempdir/out.pdf"
grep --quiet '^        /BitsPerComponent 8$' "$tempdir/out.pdf"
grep --quiet '^        /Colors 3$' "$tempdir/out.pdf"
grep --quiet '^        /Predictor 15$' "$tempdir/out.pdf"
grep --quiet '^    /Filter /FlateDecode$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/normal.tiff" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF RGBA8"

convert "$tempdir/alpha.png" -depth 8 -strip "$tempdir/alpha8.tiff"

identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^  Mime type: image/tiff$'
identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^  Type: TrueColorAlpha$'
identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^  Endianess: LSB$'
identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^    tiff:alpha: unassociated$'
identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^    tiff:endian: lsb$'
identify -verbose "$tempdir/alpha8.tiff" | grep --quiet '^    tiff:photometric: RGB$'

img2pdf "$tempdir/alpha8.tiff" /dev/null && rc=$? || rc=$?
if [ "$rc" -eq 0 ]; then
	echo needs to fail here
	exit 1
fi

rm "$tempdir/alpha8.tiff"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF RGBA16"

convert "$tempdir/alpha.png" -strip "$tempdir/alpha16.tiff"

identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^  Mime type: image/tiff$'
identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^  Type: TrueColorAlpha$'
identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^  Endianess: LSB$'
identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^  Depth: 16-bit$'
identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^    tiff:alpha: unassociated$'
identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^    tiff:endian: lsb$'
identify -verbose "$tempdir/alpha16.tiff" | grep --quiet '^    tiff:photometric: RGB$'

img2pdf "$tempdir/alpha16.tiff" /dev/null && rc=$? || rc=$?
if [ "$rc" -eq 0 ]; then
	echo needs to fail here
	exit 1
fi

rm "$tempdir/alpha16.tiff"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF Gray1"

convert "$tempdir/gray1.png" -depth 1 "$tempdir/gray1.tiff"

identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^  Mime type: image/tiff$'
identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^  Colorspace: Gray$'
identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^  Type: Bilevel$'
identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^  Endianess: LSB$'
identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^  Depth: 1-bit$'
identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^    tiff:alpha: unspecified$'
identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^    tiff:endian: lsb$'
identify -verbose "$tempdir/gray1.tiff" | grep --quiet '^    tiff:photometric: min-is-black$'

img2pdf "$tempdir/gray1.tiff" "$tempdir/out.pdf"

compare_rendered "$tempdir/out.pdf" "$tempdir/gray1.png" pnggray

compare_pdfimages "$tempdir/out.pdf" "$tempdir/gray1.png"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 1$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceGray$' "$tempdir/out.pdf"
grep --quiet '^        /BlackIs1 true$' "$tempdir/out.pdf"
grep --quiet '^        /Columns 60$' "$tempdir/out.pdf"
grep --quiet '^        /K -1$' "$tempdir/out.pdf"
grep --quiet '^        /Rows 60$' "$tempdir/out.pdf"
grep --quiet '^    /Filter \[ /CCITTFaxDecode \]$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/gray1.tiff" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
for i in 2 4 8; do
	echo "Test $j/$tests TIFF Gray$i"

	convert "$tempdir/gray$i.png" -depth $i "$tempdir/gray$i.tiff"

	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet '^  Mime type: image/tiff$'
	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet '^  Geometry: 60x60+0+0$'
	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet '^  Colorspace: Gray$'
	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet '^  Type: Grayscale$'
	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet '^  Endianess: LSB$'
	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet "^  Depth: $i-bit$"
	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet '^  Page geometry: 60x60+0+0$'
	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet '^  Compression: Zip$'
	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet '^    tiff:alpha: unspecified$'
	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet '^    tiff:endian: lsb$'
	identify -verbose "$tempdir/gray$i.tiff" | grep --quiet '^    tiff:photometric: min-is-black$'

	img2pdf "$tempdir/gray$i.tiff" "$tempdir/out.pdf"

	compare_rendered "$tempdir/out.pdf" "$tempdir/gray$i.png" pnggray

	compare_pdfimages "$tempdir/out.pdf" "$tempdir/gray$i.png"

	# When saving a PNG, PIL will store it as 8-bit data
	grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
	grep --quiet '^    /BitsPerComponent 8$' "$tempdir/out.pdf"
	grep --quiet '^    /ColorSpace /DeviceGray$' "$tempdir/out.pdf"
	grep --quiet '^        /BitsPerComponent 8$' "$tempdir/out.pdf"
	grep --quiet '^        /Colors 1$' "$tempdir/out.pdf"
	grep --quiet '^        /Predictor 15$' "$tempdir/out.pdf"
	grep --quiet '^    /Filter /FlateDecode$' "$tempdir/out.pdf"
	grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
	grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

	rm "$tempdir/gray$i.tiff" "$tempdir/out.pdf"
	j=$((j+1))
done

################################################################################
echo "Test $j/$tests TIFF Gray16"

convert "$tempdir/gray16.png" -depth 16 "$tempdir/gray16.tiff"

identify -verbose "$tempdir/gray16.tiff" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
identify -verbose "$tempdir/gray16.tiff" | grep --quiet '^  Mime type: image/tiff$'
identify -verbose "$tempdir/gray16.tiff" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/gray16.tiff" | grep --quiet '^  Colorspace: Gray$'
identify -verbose "$tempdir/gray16.tiff" | grep --quiet '^  Type: Grayscale$'
identify -verbose "$tempdir/gray16.tiff" | grep --quiet '^  Endianess: LSB$'
identify -verbose "$tempdir/gray16.tiff" | grep --quiet "^  Depth: 16-bit$"
identify -verbose "$tempdir/gray16.tiff" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/gray16.tiff" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/gray16.tiff" | grep --quiet '^    tiff:alpha: unspecified$'
identify -verbose "$tempdir/gray16.tiff" | grep --quiet '^    tiff:endian: lsb$'
identify -verbose "$tempdir/gray16.tiff" | grep --quiet '^    tiff:photometric: min-is-black$'

img2pdf "$tempdir/gray16.tiff" /dev/null && rc=$? || rc=$?
if [ "$rc" -eq 0 ]; then
	echo needs to fail here
	exit 1
fi

rm "$tempdir/gray16.tiff"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF multipage"

convert "$tempdir/normal.png" "$tempdir/inverse.png" -strip "$tempdir/multipage.tiff"

identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^  Mime type: image/tiff$'
identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^  Type: TrueColor$'
identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^  Endianess: LSB$'
identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^    tiff:alpha: unspecified$'
identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^    tiff:endian: lsb$'
identify -verbose "$tempdir/multipage.tiff[0]" | grep --quiet '^    tiff:photometric: RGB$'

identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^  Mime type: image/tiff$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^  Geometry: 60x60+0+0$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^  Colorspace: sRGB$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^  Type: TrueColor$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^  Endianess: LSB$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^  Depth: 8-bit$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^  Page geometry: 60x60+0+0$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^  Compression: Zip$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^    tiff:alpha: unspecified$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^    tiff:endian: lsb$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^    tiff:photometric: RGB$'
identify -verbose "$tempdir/multipage.tiff[1]" | grep --quiet '^  Scene: 1$'

img2pdf "$tempdir/multipage.tiff" "$tempdir/out.pdf"

if [ "$(pdfinfo "$tempdir/out.pdf" | awk '/Pages:/ {print $2}')" != 2 ]; then
	echo "pdf does not have 2 pages"
	exit 1
fi

pdfseparate "$tempdir/out.pdf" "$tempdir/page-%d.pdf"
rm "$tempdir/out.pdf"

for page in 1 2; do
	compare_rendered "$tempdir/page-$page.pdf" "$tempdir/multipage.tiff[$((page-1))]"

	compare_pdfimages "$tempdir/page-$page.pdf" "$tempdir/multipage.tiff[$((page-1))]"

	# We cannot grep the PDF metadata here, because the page was
	# rewritten into a non-greppable format by pdfseparate. but that's
	# okay, because we already grepped single pages before and multipage
	# PDF should not be different.

	rm "$tempdir/page-$page.pdf"
done

rm "$tempdir/multipage.tiff"
j=$((j+1))

###############################################################################
for i in 1 2 4 8; do
	echo "Test $j/$tests TIFF Palette$i"

	convert "$tempdir/palette$i.png" "$tempdir/palette$i.tiff"

	identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
	identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Mime type: image/tiff$'
	identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Geometry: 60x60+0+0$'
	identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Colorspace: sRGB$'
	identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Type: Palette$'
	identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Endianess: LSB$'
	if [ "$i" -eq 8 ]; then
		identify -verbose "$tempdir/palette$i.tiff" | grep --quiet "^  Depth: 8-bit$"
	else
		identify -verbose "$tempdir/palette$i.tiff" | grep --quiet "^  Depth: $i/8-bit$"
	fi
	case $i in
		1) identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Colormap entries: 2$';;
		2) identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Colormap entries: 4$';;
		4) identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Colormap entries: 16$';;
		8) identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Colormap entries: 256$';;
	esac
	identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Page geometry: 60x60+0+0$'
	identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^  Compression: Zip$'
	identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^    tiff:alpha: unspecified$'
	identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^    tiff:endian: lsb$'
	identify -verbose "$tempdir/palette$i.tiff" | grep --quiet '^    tiff:photometric: palette$'

	img2pdf "$tempdir/palette$i.tiff" "$tempdir/out.pdf"

	compare_rendered "$tempdir/out.pdf" "$tempdir/palette$i.png"

	# pdfimages cannot export palette based images

	grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
	grep --quiet '^    /BitsPerComponent '"$i"'$' "$tempdir/out.pdf"
	grep --quiet '^    /ColorSpace \[ /Indexed /DeviceRGB ' "$tempdir/out.pdf"
	grep --quiet '^        /BitsPerComponent '"$i"'$' "$tempdir/out.pdf"
	grep --quiet '^        /Colors 1$' "$tempdir/out.pdf"
	grep --quiet '^        /Predictor 15$' "$tempdir/out.pdf"
	grep --quiet '^    /Filter /FlateDecode$' "$tempdir/out.pdf"
	grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
	grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

	rm "$tempdir/out.pdf"

	rm "$tempdir/palette$i.tiff"
	j=$((j+1))
done

###############################################################################
for i in 12 14 16; do
	echo "Test $j/$tests TIFF RGB$i"

	convert "$tempdir/normal16.png" -depth "$i" "$tempdir/normal$i.tiff"

	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet '^  Format: TIFF (Tagged Image File Format)$'
	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet '^  Mime type: image/tiff$'
	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet '^  Geometry: 60x60+0+0$'
	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet '^  Colorspace: sRGB$'
	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet '^  Type: TrueColor$'
	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet '^  Endianess: LSB$'
	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet "^  Depth: $i-bit$"
	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet '^  Page geometry: 60x60+0+0$'
	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet '^  Compression: Zip$'
	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet '^    tiff:alpha: unspecified$'
	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet '^    tiff:endian: lsb$'
	identify -verbose "$tempdir/normal$i.tiff" | grep --quiet '^    tiff:photometric: RGB$'

	img2pdf "$tempdir/normal$i.tiff" /dev/null && rc=$? || rc=$?
	if [ "$rc" -eq 0 ]; then
		echo needs to fail here
		exit 1
	fi

	rm "$tempdir/normal$i.tiff"
	j=$((j+1))
done

###############################################################################
echo "Test $j/$tests TIFF CCITT Group4, little endian, msb-to-lsb, min-is-white"

convert "$tempdir/gray1.png" -compress group4 -define tiff:endian=lsb -define tiff:fill-order=msb -define quantum:polarity=min-is-white "$tempdir/group4.tiff"
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Bits/Sample: 1'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Compression Scheme: CCITT Group 4'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Photometric Interpretation: min-is-white'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'FillOrder: msb-to-lsb'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Samples/Pixel: 1'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Type: Bilevel'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Endianess: LSB'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Depth: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'gray: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Compression: Group4'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:endian: lsb'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:photometric: min-is-white'

img2pdf "$tempdir/group4.tiff" "$tempdir/out.pdf"

compare_rendered "$tempdir/out.pdf" "$tempdir/group4.tiff" pnggray

compare_pdfimages "$tempdir/out.pdf" "$tempdir/group4.tiff"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 1$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceGray$' "$tempdir/out.pdf"
grep --quiet '^        /BlackIs1 false$' "$tempdir/out.pdf"
grep --quiet '^        /Columns 60$' "$tempdir/out.pdf"
grep --quiet '^        /K -1$' "$tempdir/out.pdf"
grep --quiet '^        /Rows 60$' "$tempdir/out.pdf"
grep --quiet '^    /Filter \[ /CCITTFaxDecode \]$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/group4.tiff" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF CCITT Group4, big endian, msb-to-lsb, min-is-white"

convert "$tempdir/gray1.png" -compress group4 -define tiff:endian=msb -define tiff:fill-order=msb -define quantum:polarity=min-is-white "$tempdir/group4.tiff"
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Bits/Sample: 1'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Compression Scheme: CCITT Group 4'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Photometric Interpretation: min-is-white'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'FillOrder: msb-to-lsb'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Samples/Pixel: 1'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Type: Bilevel'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Endianess: MSB'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Depth: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'gray: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Compression: Group4'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:endian: msb'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:photometric: min-is-white'

img2pdf "$tempdir/group4.tiff" "$tempdir/out.pdf"

compare_rendered "$tempdir/out.pdf" "$tempdir/group4.tiff" pnggray

compare_pdfimages "$tempdir/out.pdf" "$tempdir/group4.tiff"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 1$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceGray$' "$tempdir/out.pdf"
grep --quiet '^        /BlackIs1 false$' "$tempdir/out.pdf"
grep --quiet '^        /Columns 60$' "$tempdir/out.pdf"
grep --quiet '^        /K -1$' "$tempdir/out.pdf"
grep --quiet '^        /Rows 60$' "$tempdir/out.pdf"
grep --quiet '^    /Filter \[ /CCITTFaxDecode \]$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/group4.tiff" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF CCITT Group4, big endian, lsb-to-msb, min-is-white"

convert "$tempdir/gray1.png" -compress group4 -define tiff:endian=msb -define tiff:fill-order=lsb -define quantum:polarity=min-is-white "$tempdir/group4.tiff"
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Bits/Sample: 1'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Compression Scheme: CCITT Group 4'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Photometric Interpretation: min-is-white'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'FillOrder: lsb-to-msb'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Samples/Pixel: 1'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Type: Bilevel'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Endianess: MSB'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Depth: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'gray: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Compression: Group4'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:endian: msb'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:photometric: min-is-white'

img2pdf "$tempdir/group4.tiff" "$tempdir/out.pdf"

compare_rendered "$tempdir/out.pdf" "$tempdir/group4.tiff" pnggray

compare_pdfimages "$tempdir/out.pdf" "$tempdir/group4.tiff"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 1$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceGray$' "$tempdir/out.pdf"
grep --quiet '^        /BlackIs1 false$' "$tempdir/out.pdf"
grep --quiet '^        /Columns 60$' "$tempdir/out.pdf"
grep --quiet '^        /K -1$' "$tempdir/out.pdf"
grep --quiet '^        /Rows 60$' "$tempdir/out.pdf"
grep --quiet '^    /Filter \[ /CCITTFaxDecode \]$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/group4.tiff" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF CCITT Group4, little endian, msb-to-lsb, min-is-black"

# We create a min-is-black group4 tiff with PIL because it creates these by
# default (and without the option to do otherwise) whereas imagemagick only
# became able to do it through commit 00730551f0a34328685c59d0dde87dd9e366103a
# See https://www.imagemagick.org/discourse-server/viewtopic.php?f=1&t=34605
python3 -c 'from PIL import Image;Image.open("'"$tempdir/gray1.png"'").save("'"$tempdir/group4.tiff"'",format="TIFF",compression="group4")'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Bits/Sample: 1'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Compression Scheme: CCITT Group 4'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Photometric Interpretation: min-is-black'
# PIL doesn't set those
#tiffinfo "$tempdir/group4.tiff" | grep --quiet 'FillOrder: msb-to-lsb'
#tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Samples/Pixel: 1'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Type: Bilevel'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Endianess: LSB'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Depth: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'gray: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Compression: Group4'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:endian: lsb'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:photometric: min-is-black'

img2pdf "$tempdir/group4.tiff" "$tempdir/out.pdf"

compare_rendered "$tempdir/out.pdf" "$tempdir/group4.tiff" pnggray

compare_pdfimages "$tempdir/out.pdf" "$tempdir/group4.tiff"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 1$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceGray$' "$tempdir/out.pdf"
grep --quiet '^        /BlackIs1 true$' "$tempdir/out.pdf"
grep --quiet '^        /Columns 60$' "$tempdir/out.pdf"
grep --quiet '^        /K -1$' "$tempdir/out.pdf"
grep --quiet '^        /Rows 60$' "$tempdir/out.pdf"
grep --quiet '^    /Filter \[ /CCITTFaxDecode \]$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/group4.tiff" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF CCITT Group4, without fillorder, samples/pixel, bits/sample"

convert "$tempdir/gray1.png" -compress group4 -define tiff:endian=lsb -define tiff:fill-order=msb -define quantum:polarity=min-is-white "$tempdir/group4.tiff"
# remove BitsPerSample (258)
tiffset -u 258 "$tempdir/group4.tiff"
# remove FillOrder (266)
tiffset -u 266 "$tempdir/group4.tiff"
# remove SamplesPerPixel (277)
tiffset -u 277 "$tempdir/group4.tiff"
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Bits/Sample: 1' && exit 1
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Compression Scheme: CCITT Group 4'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Photometric Interpretation: min-is-white'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'FillOrder: msb-to-lsb' && exit 1
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Samples/Pixel: 1' && exit 1
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Type: Bilevel'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Endianess: LSB'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Depth: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'gray: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Compression: Group4'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:endian: lsb'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:photometric: min-is-white'

img2pdf "$tempdir/group4.tiff" "$tempdir/out.pdf"

compare_rendered "$tempdir/out.pdf" "$tempdir/group4.tiff" pnggray

compare_pdfimages "$tempdir/out.pdf" "$tempdir/group4.tiff"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 1$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceGray$' "$tempdir/out.pdf"
grep --quiet '^        /BlackIs1 false$' "$tempdir/out.pdf"
grep --quiet '^        /Columns 60$' "$tempdir/out.pdf"
grep --quiet '^        /K -1$' "$tempdir/out.pdf"
grep --quiet '^        /Rows 60$' "$tempdir/out.pdf"
grep --quiet '^    /Filter \[ /CCITTFaxDecode \]$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/group4.tiff" "$tempdir/out.pdf"
j=$((j+1))

###############################################################################
echo "Test $j/$tests TIFF CCITT Group4, without rows-per-strip"

convert "$tempdir/gray1.png" -compress group4 -define tiff:endian=lsb -define tiff:fill-order=msb -define quantum:polarity=min-is-white -define tiff:rows-per-strip=4294967295 "$tempdir/group4.tiff"
# remove RowsPerStrip (278)
tiffset -u 278 "$tempdir/group4.tiff"
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Bits/Sample: 1'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Compression Scheme: CCITT Group 4'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Photometric Interpretation: min-is-white'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'FillOrder: msb-to-lsb'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Samples/Pixel: 1'
tiffinfo "$tempdir/group4.tiff" | grep --quiet 'Rows/Strip:' && exit 1
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Type: Bilevel'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Endianess: LSB'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Depth: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'gray: 1-bit'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'Compression: Group4'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:endian: lsb'
identify -verbose "$tempdir/group4.tiff" | grep --quiet 'tiff:photometric: min-is-white'

img2pdf "$tempdir/group4.tiff" "$tempdir/out.pdf"

compare_rendered "$tempdir/out.pdf" "$tempdir/group4.tiff" pnggray

compare_pdfimages "$tempdir/out.pdf" "$tempdir/group4.tiff"

grep --quiet '^45.0000 0 0 45.0000 0.0000 0.0000 cm$' "$tempdir/out.pdf"
grep --quiet '^    /BitsPerComponent 1$' "$tempdir/out.pdf"
grep --quiet '^    /ColorSpace /DeviceGray$' "$tempdir/out.pdf"
grep --quiet '^        /BlackIs1 false$' "$tempdir/out.pdf"
grep --quiet '^        /Columns 60$' "$tempdir/out.pdf"
grep --quiet '^        /K -1$' "$tempdir/out.pdf"
grep --quiet '^        /Rows 60$' "$tempdir/out.pdf"
grep --quiet '^    /Filter \[ /CCITTFaxDecode \]$' "$tempdir/out.pdf"
grep --quiet '^    /Height 60$' "$tempdir/out.pdf"
grep --quiet '^    /Width 60$' "$tempdir/out.pdf"

rm "$tempdir/group4.tiff" "$tempdir/out.pdf"
j=$((j+1))

rm "$tempdir/alpha.png" "$tempdir/normal.png" "$tempdir/inverse.png" "$tempdir/palette1.png" "$tempdir/palette2.png" "$tempdir/palette4.png" "$tempdir/palette8.png" "$tempdir/gray8.png" "$tempdir/normal16.png" "$tempdir/gray16.png" "$tempdir/gray4.png" "$tempdir/gray2.png" "$tempdir/gray1.png"
rmdir "$tempdir"

trap - EXIT
