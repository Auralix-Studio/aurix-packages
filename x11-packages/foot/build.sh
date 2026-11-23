TERMUX_PKG_HOMEPAGE=https://codeberg.org/dnkl/foot
TERMUX_PKG_DESCRIPTION="Fast, lightweight and minimalistic Wayland terminal emulator"
TERMUX_PKG_LICENSE="MIT"
TERMUX_PKG_MAINTAINER="@termux"
TERMUX_PKG_VERSION="1.28.0"
# Espejo propio: Codeberg no entregaba el .tar.gz desde GitHub Actions (tag oficial 1.28.0, commit ab33c9a).
TERMUX_PKG_SRCURL="https://github.com/Auralix-Studio/aurix-packages/releases/download/mirror-foot-${TERMUX_PKG_VERSION}/foot-${TERMUX_PKG_VERSION}.tar.gz"
TERMUX_PKG_SHA256=567b0b07629fdafd39b51f0c0acb08346ac81093fddf559b5aa064a1b8be23b7
TERMUX_PKG_AUTO_UPDATE=true
TERMUX_PKG_DEPENDS="fontconfig, freetype, libfcft, libpixman, libwayland, libxkbcommon, utf8proc"
TERMUX_PKG_BUILD_DEPENDS="libtllist, libwayland-protocols, libwayland-cross-scanner, scdoc, xdg-utils"
TERMUX_PKG_EXTRA_CONFIGURE_ARGS="
-Ddocs=enabled
-Dterminfo-base-name=foot-extra
-Dtests=false
"

termux_step_pre_configure() {
	termux_setup_wayland_cross_pkg_config_wrapper

	# NDK r30 deprecates strtok; foot enables -Werror by default.
	CFLAGS+=" -Wno-error=deprecated-declarations"

	# bionic provides this
	export CPPFLAGS+=" -D__STDC_ISO_10646__=201103L"
}
