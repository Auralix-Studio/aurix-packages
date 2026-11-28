#!/usr/bin/env python3
"""Publica los APK de Aurix en el bucket aurx-dl, con la misma disposición que las otras apps de Auralix:

  aurix/releases/vX.Y.Z/apk/aurix-vX.Y.Z-<variante>.apk    la versión publicada (solo se conserva la actual)
  aurix/apk/aurix-android[-<variante>].apk                 nombres fijos, para enlazar desde la web
  aurix/SHA256SUMS.txt y aurix/version.json

Antes de subir nada comprueba cada APK: que sea de Aurix, de la versión pedida, no depurable y firmado con la clave
de siempre (un APK con otra firma no se podría instalar encima, o no sería nuestro). Nunca baja de versión.
No toca nada fuera de aurix/apk/, aurix/releases/ y esos dos archivos (el repositorio apt está en aurix/apt/).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from publish import IMMUTABLE, NO_CACHE, LocalStore, R2Store, compare_versions

PACKAGE = 'pro.auralix.aurix'
CDN = 'https://dl.aurx.pro/aurix'
# Variante -> nombre fijo. La universal es la que se enlaza si no se sabe la arquitectura.
VARIANTS = {
    'arm64': 'aurix-android-arm64.apk',
    'armv7': 'aurix-android-armv7.apk',
    'x86_64': 'aurix-android-x86_64.apk',
    'universal': 'aurix-android.apk',
}
SHORT_CACHE = 'public, max-age=300'


def source_name(variant: str, version: str) -> str:
    return f'aurix-v{version}-{variant}.apk'


def release_key(variant: str, version: str) -> str:
    return f'releases/v{version}/apk/{source_name(variant, version)}'


def check_badging(text: str, version: str) -> int:
    """Valida la salida de «aapt2 dump badging» y devuelve el versionCode."""
    match = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", text, re.M)
    if not match:
        raise ValueError('no se pudo leer el paquete del APK')
    name, code, version_name = match.groups()
    if name != PACKAGE:
        raise ValueError(f'el paquete es {name}, no {PACKAGE}')
    if version_name != version:
        raise ValueError(f'el APK es la versión {version_name}, no la {version}')
    if re.search(r'^application-debuggable', text, re.M):
        raise ValueError('es una compilación de depuración')
    return int(code)


def check_signer(text: str, expected: str) -> None:
    """Valida la salida de «apksigner verify --print-certs»: un único firmante, el esperado."""
    found = re.findall(r'^Signer #\d+ certificate SHA-256 digest: ([0-9a-f]{64})\s*$', text, re.M)
    if found != [expected.lower().replace(':', '')]:
        raise ValueError(f'firma inesperada: {found or "sin firma"}')


def sdk_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for root in sorted(Path(os.environ.get('ANDROID_HOME', '/nada')).glob('build-tools/*'), reverse=True):
        if (root / name).exists():
            return str(root / name)
    raise RuntimeError(f'no se encontró {name}')


def inspect(path: Path, version: str, certificate: str) -> int:
    def run(*args):
        result = subprocess.run(list(args), capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f'{path.name}: {result.stderr.strip() or result.stdout.strip()}')
        return result.stdout

    check_signer(run(sdk_tool('apksigner'), 'verify', '--print-certs', str(path)), certificate)
    return check_badging(run(sdk_tool('aapt2'), 'dump', 'badging', str(path)), version)


def plan(folder: Path, version: str) -> dict[str, Path]:
    """Los APK de [folder] por variante; deben estar las cuatro."""
    files = {variant: folder / source_name(variant, version) for variant in VARIANTS}
    missing = [p.name for p in files.values() if not p.is_file()]
    if missing:
        raise ValueError(f'faltan APK: {", ".join(missing)}')
    return files


def manifest(version: str, build: int, files: dict[str, Path], now: float) -> dict:
    assets = {}
    for variant, path in files.items():
        data = path.read_bytes()
        assets[f'android-{variant}'] = {
            'name': path.name,
            'cdn_url': f'{CDN}/{release_key(variant, version)}',
            'latest_url': f'{CDN}/apk/{VARIANTS[variant]}',
            'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
        }
    return {'schema': 1, 'version': version, 'build': build,
            'date': time.strftime('%Y-%m-%d', time.gmtime(now - 5 * 3600)), 'assets': assets}


def old_releases(prefixes: list[str], version: str) -> list[str]:
    """De las carpetas bajo releases/, las que sobran (solo las que tienen forma de versión y no son la actual)."""
    return sorted(p for p in prefixes if re.fullmatch(r'releases/v[0-9][0-9A-Za-z.+-]*/', p) and p != f'releases/v{version}/')


def publish(store, files: dict[str, Path], version: str, build: int, now: float) -> dict:
    raw = store.get('version.json')
    if raw:
        published = json.loads(raw)
        if compare_versions(version, published['version']) < 0 or (version == published['version'] and build < published['build']):
            raise RuntimeError(f'Publicada la {published["version"]} (build {published["build"]}): no se baja a {version} ({build}).')
    data = manifest(version, build, files, now)
    sums = ''.join(f'{data["assets"][f"android-{v}"]["sha256"]}  {files[v].name}\n' for v in files)
    for variant, path in files.items():
        store.put(release_key(variant, version), path, SHORT_CACHE)
    # Los nombres fijos y version.json van después: nunca apuntan a algo que aún no está.
    for variant, path in files.items():
        store.put(f'apk/{VARIANTS[variant]}', path, NO_CACHE)
    store.put('SHA256SUMS.txt', sums.encode(), NO_CACHE)
    store.put('version.json', (json.dumps(data, indent=2) + '\n').encode(), NO_CACHE)
    return data


class ApkStore(R2Store):
    """R2 con el tipo de contenido de un APK y la lista de versiones publicadas."""

    def put(self, key: str, source, cache: str):
        if not key.endswith('.apk'):
            return super().put(key, source, cache)
        result = self._aws('cp', str(source), self._url(key), '--cache-control', cache,
                           '--content-type', 'application/vnd.android.package-archive')
        if result.returncode != 0:
            raise RuntimeError(f'No se pudo subir {key}: {result.stderr.strip()}')

    def release_folders(self) -> list[str]:
        result = subprocess.run(
            ['aws', 's3api', 'list-objects-v2', '--bucket', self.bucket, '--prefix', f'{self.prefix}/releases/',
             '--delimiter', '/', '--endpoint-url', self.endpoint, '--query', 'CommonPrefixes[].Prefix', '--output', 'json'],
            capture_output=True, text=True, check=True)
        return [p[len(self.prefix) + 1:] for p in json.loads(result.stdout or 'null') or []]

    def delete_folder(self, folder: str):
        result = self._aws('rm', self._url(folder), '--recursive')
        if result.returncode != 0:
            raise RuntimeError(f'No se pudo borrar {folder}: {result.stderr.strip()}')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('folder', type=Path, help='carpeta con aurix-vX.Y.Z-<variante>.apk')
    parser.add_argument('--version', required=True)
    parser.add_argument('--certificate', required=True, help='SHA-256 del certificado de firma')
    parser.add_argument('--bucket', default='aurx-dl')
    parser.add_argument('--prefix', default='aurix')
    parser.add_argument('--endpoint', default=os.environ.get('R2_ENDPOINT', ''))
    parser.add_argument('--local', type=Path, help='ensayo: publica en una carpeta en vez de en R2')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        parser.error('la versión tiene que ser X.Y.Z')
    if not (args.endpoint or args.local):
        parser.error('falta --endpoint (R2_ENDPOINT) o --local')
    files = plan(args.folder, args.version)
    builds = {inspect(path, args.version, args.certificate) for path in files.values()}
    if len(builds) != 1:
        raise SystemExit(f'los APK tienen versionCode distintos: {sorted(builds)}')
    store = LocalStore(args.local) if args.local else ApkStore(args.endpoint, args.bucket, args.prefix)
    data = publish(store, files, args.version, builds.pop(), time.time())
    if isinstance(store, ApkStore):
        # Se limpia al final y solo si lo nuevo está subido.
        folders = store.release_folders()
        if f'releases/v{args.version}/' in folders:
            for folder in old_releases(folders, args.version):
                store.delete_folder(folder)
                print(f'Borrada la versión anterior: {folder}')
    for name, asset in data['assets'].items():
        print(f'{name:18} {asset["size"] / 1e6:6.1f} MB  {asset["latest_url"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
