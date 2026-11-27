#!/usr/bin/env bash
# Comprueba con un apt DE VERDAD (dentro de un contenedor Debian) que el repositorio es válido: acepta la firma,
# descarga los índices y puede resolver las dependencias de cada paquete partiendo de un sistema vacío.
#   uso: comprobar-apt.sh <url-del-repo> <clave-publica.gpg> <arquitectura> [paquetes obligatorios...]
set -euo pipefail
url="$1"; key="$(realpath "$2")"; arch="$3"; shift 3
required="$*"

docker run --rm --network host -v "$key:/aurix.gpg:ro" -e URL="$url" -e ARCH="$arch" -e REQUIRED="$required" \
  debian:stable-slim bash -euc '
    mkdir -p /tmp/apt/lists/partial /tmp/apt/archives/partial
    : > /tmp/apt/status
    printf "Types: deb\nURIs: %s\nSuites: stable\nComponents: main\nSigned-By: /aurix.gpg\n" "$URL" > /tmp/apt/aurix.sources
    opts=(-o APT::Architecture="$ARCH" -o APT::Architectures="$ARCH"
          -o Dir::Etc::sourcelist=/tmp/apt/aurix.sources -o Dir::Etc::sourceparts=-
          -o Dir::State::lists=/tmp/apt/lists -o Dir::State::status=/tmp/apt/status
          -o Dir::Cache::archives=/tmp/apt/archives -o APT::Update::Error-Mode=any)
    apt-get "${opts[@]}" update
    bad=()
    for p in $(apt-cache "${opts[@]}" pkgnames | sort); do
      apt-get "${opts[@]}" -s install "$p" > /dev/null 2>&1 || bad+=("$p")
    done
    echo "paquetes en el índice: $(apt-cache "${opts[@]}" pkgnames | wc -l)"
    if [ ${#bad[@]} -gt 0 ]; then echo "::warning title=Sin dependencias completas ($ARCH)::${bad[*]}"; fi
    fail=0
    for p in $REQUIRED; do
      if ! apt-get "${opts[@]}" -s install "$p" > /dev/null 2>&1; then echo "::error::$p no se puede instalar en $ARCH"; fail=1; fi
    done
    first="${REQUIRED%% *}"
    if [ -n "$first" ]; then (cd /tmp && apt-get "${opts[@]}" download "$first"); fi
    exit $fail
  '
