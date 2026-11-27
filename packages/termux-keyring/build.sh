TERMUX_PKG_HOMEPAGE=https://github.com/termux
TERMUX_PKG_DESCRIPTION="GPG public key of the Aurix package repository"
TERMUX_PKG_LICENSE="Apache-2.0"
TERMUX_PKG_MAINTAINER="@termux"
TERMUX_PKG_VERSION=3.15
TERMUX_PKG_AUTO_UPDATE=false
TERMUX_PKG_SKIP_SRC_EXTRACT=true
TERMUX_PKG_PLATFORM_INDEPENDENT=true
TERMUX_PKG_ESSENTIAL=true

termux_step_make_install() {
	local GPG_SHARE_DIR="$TERMUX_PREFIX/share/termux-keyring"

	# Delete all existing termux-keyring keys
	rm -rf "$GPG_SHARE_DIR"
	mkdir -p "$GPG_SHARE_DIR"

	# Aurix: solo se confía en la clave del repositorio de Aurix (la crea scripts/aurix-repo/crear-clave.ps1).
	test -f $TERMUX_PKG_BUILDER_DIR/aurix-packages.gpg || termux_error_exit "Falta aurix-packages.gpg: ejecuta scripts/aurix-repo/crear-clave.ps1"
	install -Dm600 $TERMUX_PKG_BUILDER_DIR/aurix-packages.gpg $GPG_SHARE_DIR

	GPG_DIR="$TERMUX_PREFIX/etc/apt/trusted.gpg.d"
	mkdir -p "$GPG_DIR"
	for GPG_FILE in "$GPG_SHARE_DIR"/*.gpg; do
		ln -s "$GPG_FILE" "$GPG_DIR/$(basename $GPG_FILE)"
	done
}
