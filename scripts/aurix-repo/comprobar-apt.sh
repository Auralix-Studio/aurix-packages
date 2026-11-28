#!/usr/bin/env bash
# Comprueba el repositorio como lo usará un teléfono:
#  1. Cada archivo que nombra el índice firmado se puede pedir y pesa lo que dice.
#  2. Un apt DE VERDAD (en un contenedor Debian) acepta la firma, resuelve las dependencias partiendo de un sistema
#     vacío y DESCARGA los paquetes obligatorios con todas sus dependencias, además de los de nombre delicado
#     (época, «+», «~»): apt los codifica en la URL a su manera.
#   uso: comprobar-apt.sh <url-del-repo> <clave-publica.gpg> <arquitectura> [paquetes obligatorios...]
set -euo pipefail
url="$1"; key="$(realpath "$2")"; arch="$3"; shift 3
required="$*"

python3 - "$url" "$arch" <<'PY'
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

url, arch = sys.argv[1], sys.argv[2]


def fetch(path, method='GET'):
    request = urllib.request.Request(f'{url}/{path}', method=method, headers={'User-Agent': 'aurix-repo-check/1'})
    return urllib.request.urlopen(request, timeout=120)


# El índice se pide por su hash, como hace apt: la copia sin hash puede venir de una caché anterior.
release = fetch('dists/stable/InRelease').read().decode()
digest = re.search(rf'^ ([0-9a-f]{{64}}) \d+ main/binary-{arch}/Packages$', release, re.M).group(1)
index = fetch(f'dists/stable/main/binary-{arch}/by-hash/SHA256/{digest}').read().decode()


def check(stanza):
    fields = dict(line.split(': ', 1) for line in stanza.splitlines() if not line.startswith(' ') and ': ' in line)
    try:
        length = fetch(fields['Filename'], 'HEAD').headers.get('Content-Length')
    except (urllib.error.URLError, TimeoutError) as error:
        return f'{fields["Filename"]}: {error}'
    return None if length == fields['Size'] else f'{fields["Filename"]}: pesa {length} y el índice dice {fields["Size"]}'


stanzas = [s for s in index.split('\n\n') if s.strip()]
with ThreadPoolExecutor(8) as pool:
    problems = [p for p in pool.map(check, stanzas) if p]
print(f'archivos del índice ({arch}): {len(stanzas)} · con problemas: {len(problems)}')
for problem in problems[:40]:
    print(f'::error::{problem}')
sys.exit(1 if problems else 0)
PY

docker run --rm --network host -v "$key:/aurix.gpg:ro" -e URL="$url" -e ARCH="$arch" -e REQUIRED="$required" \
  debian:stable-slim bash -euc '
    mkdir -p /tmp/apt/lists/partial /tmp/apt/archives/partial /tmp/sueltos
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
    [ "$fail" = 0 ] || exit 1
    if [ -n "$REQUIRED" ]; then
      apt-get "${opts[@]}" -y --no-install-recommends --download-only install $REQUIRED
      echo "descargados con sus dependencias: $(ls /tmp/apt/archives/*.deb | wc -l)"
    fi
    delicate=$(apt-cache "${opts[@]}" dumpavail | awk "/^Package:/{p=\$2} /^Filename:/ && \$2 ~ /[+~]|_e[0-9]+_/{print p}" | sort -u)
    if [ -n "$delicate" ]; then
      (cd /tmp/sueltos && apt-get "${opts[@]}" download $delicate)
      echo "descargados con nombre delicado: $(ls /tmp/sueltos/*.deb | wc -l)"
    fi
  '
