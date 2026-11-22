# aurix-packages

Paquetes de la **terminal nativa de [Aurix](https://github.com/Auralix-Studio)** (`pro.auralix.aurix`), el editor de
código para Android de Auralix Studio.

Es una bifurcación de [termux-play-store/termux-packages](https://github.com/termux-play-store/termux-packages)
(los paquetes de Termux para Google Play, a su vez derivados de [termux/termux-packages](https://github.com/termux/termux-packages)).
Los paquetes se compilan **desde las fuentes** con el nombre de app `pro.auralix.aurix`: los de Termux no sirven en
otra app porque llevan su ruta (`/data/data/com.termux/...`) grabada dentro.

## Qué cambia respecto a Termux

- `scripts/properties.sh`: `TERMUX_APP__PACKAGE_NAME` y `TERMUX_APP__NAMESPACE` = `pro.auralix.aurix`, nombre «Aurix».
- `repo.json`: el repositorio de paquetes de Aurix (aún no publicado).
- `scripts/build-bootstraps.sh`: copiado sin cambios de termux/termux-packages (Apache-2.0).
- `.github/workflows/aurix_bootstrap.yml`: compila el sistema base para aarch64, arm y x86_64 y lo publica como
  release con sus SHA-256. La app lo incluye dentro del APK.

## Licencias

- Infraestructura de compilación (fuera de `packages/` y similares): Apache-2.0, ver [LICENSE.md](LICENSE.md).
- Recetas y parches de cada paquete: la misma licencia que el paquete (GPL, MIT, BSD…). El código fuente de cada
  binario publicado está en este repositorio (recetas y parches) y en la URL de origen de cada `build.sh`.

---

# Termux packages for the Google Play build of Termux

This repository contains the packages used in the [Termux build on Google Play](https://play.google.com/store/apps/details?id=com.termux&hl=en).

It's currently mostly interesting if you are a developer looking into the changes necessary to make Termux compatible with the Google Play requirements.

Otherwise, please work on [https://github.com/termux/termux-packages](https://github.com/termux/termux-packages) instead - this repository regularly merges in changes from there.

See https://github.com/termux-play-store for more information, status and updates regarding Termux on Google Play.

## Quick guide to how to build a package
Most developers should use a prebuilt docker image to get a correctly configured and isolated build environment. Start with:

```sh
./scripts/run-docker.sh
```

Now build a package with:

```sh
./build-package.sh -i <package-name>
```

where `<package-name>` is a package name, corresponding to a directory `packages/<package-name/` (so `bash` and `vim` are examples of package names).

## Quick guide to how to develop and patch a package
There are mainly two parts to iterating on a package:

1. Edit the `packages/<package-name/build.sh` build script and run builds iteratively as above.
2. Update package patches and run builds iteratively as above.

Patches are applied from `packages/<package-name/*.patch` files.

Once there is an existing build, a built `.deb` file will be created in the `output/` directory, as in `output/bash_5.2.26-2_aarch64.deb`. Transfer that to your device and install with `dpkg -i output/bash_5.2.26-2_aarch64.deb`.

Feel free to reach out with an issue or [#termux-google-play on Matrix](https://matrix.to/#/#termux-google-play:matrix.org) to discuss or get help!
