TERMUX_PKG_HOMEPAGE=https://mpg123.org/
TERMUX_PKG_DESCRIPTION="Fast console MPEG Audio Player and decoder library"
TERMUX_PKG_LICENSE="LGPL-2.1"
TERMUX_PKG_MAINTAINER="@termux"
TERMUX_PKG_VERSION="1.33.7"
# Flaky https://mpg123.org/download/mpg123-${TERMUX_PKG_VERSION}.tar.bz2
TERMUX_PKG_SRCURL=https://downloads.sourceforge.net/sourceforge/mpg123/mpg123-${TERMUX_PKG_VERSION}.tar.bz2
TERMUX_PKG_SHA256=31d0e35a4ca567ec9b5ebda6c3062bb4435d6d3eacd6ef0d95cadd7854dc03ee
TERMUX_PKG_AUTO_UPDATE=true
# Sin pulseaudio: cerraba un círculo (libmpg123 -> pulseaudio -> libsndfile -> libmp3lame -> libmpg123) que solo
# se puede compilar si ya hay paquetes publicados de la cadena. Solo el reproductor «mpg123» pierde esa salida.
TERMUX_PKG_BREAKS="mpg123 (<< 1.33.1-1)"
TERMUX_PKG_REPLACES="mpg123 (<< 1.33.1-1)"
