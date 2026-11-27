#!/usr/bin/env python3
"""Publica el repositorio apt de Aurix en Cloudflare R2 (bucket aurx-dl, bajo aurix/apt/) sin acumular versiones viejas.

Reglas:
- En el índice solo está la versión más nueva de cada paquete y arquitectura. Nunca se baja de versión.
- Un .deb ya publicado no se reemplaza por otra compilación de la MISMA versión (apt vería un hash distinto).
- Autoborrado: lo que sale del índice se «retira» y se borra pasados GRACE días. Ese margen evita que quien tenga un
  índice viejo (sin haber hecho «pkg update») reciba un error al descargar.
- Tope de espacio: si el repositorio fuera a superar --max-bytes no se sube nada (el bucket lo comparten otras apps).
- Orden de subida: primero los .deb y los índices por hash, al final InRelease; así el repositorio nunca queda a medias.

Estado: aurix/apt/state.json (qué hay en el bucket y desde cuándo está retirado). No hace falta listar el bucket.
Solo usa la biblioteca estándar; para R2 llama a «aws s3» (compatible) y para firmar a «gpg».
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from email.utils import formatdate
from pathlib import Path

ARCHITECTURES = ('aarch64', 'arm', 'x86_64')
DAY = 86400
IMMUTABLE = 'public, max-age=31536000, immutable'
NO_CACHE = 'no-cache, max-age=0, must-revalidate'


# ---------- versiones de Debian ----------

def _order(char: str) -> int:
    if char == '~':
        return -1
    if char.isalpha():
        return ord(char)
    return ord(char) + 256


def _compare_part(a: str, b: str) -> int:
    """Algoritmo de dpkg (verrevcmp): tramos alternos de no-dígitos y dígitos; «~» va antes que todo, incluso el final."""
    i = j = 0
    while i < len(a) or j < len(b):
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            left = _order(a[i]) if i < len(a) and not a[i].isdigit() else 0
            right = _order(b[j]) if j < len(b) and not b[j].isdigit() else 0
            if left != right:
                return -1 if left < right else 1
            i += 1
            j += 1
        x = i
        while x < len(a) and a[x].isdigit():
            x += 1
        y = j
        while y < len(b) and b[y].isdigit():
            y += 1
        n, m = int(a[i:x] or 0), int(b[j:y] or 0)
        if n != m:
            return -1 if n < m else 1
        i, j = x, y
    return 0


def compare_versions(a: str, b: str) -> int:
    def split(version: str):
        epoch, sep, rest = version.partition(':')
        if not sep:
            epoch, rest = '0', version
        upstream, sep, revision = rest.rpartition('-')
        if not sep:
            upstream, revision = rest, ''
        return int(epoch), upstream, revision

    ea, ua, ra = split(a)
    eb, ub, rb = split(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _compare_part(ua, ub) or _compare_part(ra, rb)


# ---------- lectura de .deb ----------

def _ar_members(data: bytes):
    if not data.startswith(b'!<arch>\n'):
        raise ValueError('no es un archivo ar')
    position = 8
    while position + 60 <= len(data):
        name = data[position:position + 16].decode().strip().rstrip('/')
        size = int(data[position + 48:position + 58].decode().strip())
        yield name, data[position + 60:position + 60 + size]
        position += 60 + size + (size % 2)


def clean_control(text: str) -> dict[str, str]:
    """Campos del control en orden. Las líneas que no son «Campo: valor» ni continuación se descartan (algún paquete
    de Termux trae líneas así y apt rechazaría el índice entero)."""
    fields: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        match = re.match(r'^([A-Za-z0-9][A-Za-z0-9-]*):[ \t]*(.*)$', line)
        if match:
            current = match.group(1)
            fields[current] = match.group(2)
        elif line[:1] in (' ', '\t') and current:
            fields[current] += '\n' + line
        else:
            current = None
    return fields


def read_deb(path: Path) -> dict:
    data = path.read_bytes()
    for name, body in _ar_members(data):
        if name.startswith('control.tar'):
            with tarfile.open(fileobj=io.BytesIO(body), mode='r:*') as archive:
                member = next(m for m in archive.getmembers() if m.name.lstrip('./') == 'control')
                fields = clean_control(archive.extractfile(member).read().decode('utf-8'))
            break
    else:
        raise ValueError(f'{path.name}: sin control.tar')
    for required in ('Package', 'Version', 'Architecture'):
        if not fields.get(required):
            raise ValueError(f'{path.name}: falta el campo {required}')
    if fields['Architecture'] not in ARCHITECTURES + ('all',):
        raise ValueError(f'{path.name}: arquitectura no admitida {fields["Architecture"]}')
    if not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', fields['Package']):
        raise ValueError(f'{path.name}: nombre de paquete no válido')
    if not re.fullmatch(r'[A-Za-z0-9.+:~-]+', fields['Version']):
        raise ValueError(f'{path.name}: versión no válida')
    return {'fields': fields, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def pool_key(fields: dict) -> str:
    # El «:» de la época no va en el nombre del archivo (convención de Debian).
    version = fields['Version'].replace(':', '%3a')
    name = fields['Package']
    return f'pool/main/{name}/{name}_{version}_{fields["Architecture"]}.deb'


# ---------- plan ----------

def merge(state: dict, incoming: list[tuple[Path, dict]], now: float) -> tuple[dict, list[tuple[Path, str]]]:
    """Devuelve el estado nuevo y los (archivo local, clave) que hay que subir. No modifica [state]."""
    files = {key: dict(value) for key, value in state.get('files', {}).items()}

    def active():
        return {(v['fields']['Package'], v['fields']['Architecture']): k for k, v in files.items() if v['retired_at'] is None}

    uploads: dict[str, Path] = {}
    for path, info in incoming:
        fields = info['fields']
        identity = (fields['Package'], fields['Architecture'])
        current_key = active().get(identity)
        if current_key:
            order = compare_versions(fields['Version'], files[current_key]['fields']['Version'])
            if order <= 0:
                continue  # igual (aunque se haya recompilado) o más vieja: se queda lo publicado
            files[current_key]['retired_at'] = now
            uploads.pop(current_key, None)
        key = pool_key(fields)
        if key in files:
            files[key]['retired_at'] = None  # volvió una versión que aún no se había borrado: se reutiliza el archivo
            continue
        files[key] = {'fields': fields, 'size': info['size'], 'sha256': info['sha256'], 'retired_at': None}
        uploads[key] = path
    return {'files': files, 'indexes': dict(state.get('indexes', {}))}, [(path, key) for key, path in uploads.items()]


def expired(entries: dict, now: float, grace_days: float) -> list[str]:
    return sorted(k for k, v in entries.items() if v['retired_at'] is not None and now - v['retired_at'] >= grace_days * DAY)


def packages_index(state: dict, arch: str) -> bytes:
    stanzas = []
    for key, entry in sorted(state['files'].items()):
        fields = entry['fields']
        if entry['retired_at'] is not None or fields['Architecture'] not in (arch, 'all'):
            continue
        lines = [f'{name}: {value}' for name, value in fields.items() if name not in ('Filename', 'Size', 'SHA256')]
        lines += [f'Filename: {key}', f'Size: {entry["size"]}', f'SHA256: {entry["sha256"]}']
        stanzas.append('\n'.join(lines) + '\n')
    return '\n'.join(stanzas).encode('utf-8')


def build_indexes(state: dict, now: float) -> tuple[dict[str, bytes], bytes]:
    indexes: dict[str, bytes] = {}
    for arch in ARCHITECTURES:
        plain = packages_index(state, arch)
        base = f'main/binary-{arch}/Packages'
        indexes[base] = plain
        indexes[base + '.gz'] = gzip.compress(plain, mtime=0)
        indexes[base + '.xz'] = lzma.compress(plain)
    lines = [
        'Origin: Aurix', 'Label: Aurix', 'Suite: stable', 'Codename: stable',
        f'Date: {formatdate(now, usegmt=True)}', f'Architectures: {" ".join(ARCHITECTURES)}', 'Components: main',
        'Description: Paquetes de la terminal de Aurix', 'Acquire-By-Hash: yes', 'SHA256:',
    ]
    lines += [f' {hashlib.sha256(body).hexdigest()} {len(body)} {name}' for name, body in sorted(indexes.items())]
    return indexes, ('\n'.join(lines) + '\n').encode('utf-8')


def by_hash_key(name: str, body: bytes) -> str:
    return f'dists/stable/{name.rsplit("/", 1)[0]}/by-hash/SHA256/{hashlib.sha256(body).hexdigest()}'


def total_bytes(state: dict) -> int:
    return sum(v['size'] for v in state['files'].values()) + sum(v['size'] for v in state['indexes'].values())


# ---------- almacenes ----------

class LocalStore:
    """Carpeta local con la misma interfaz que R2: para las pruebas."""

    def __init__(self, root: Path):
        self.root = root
        self.log: list[tuple[str, str]] = []

    def get(self, key: str):
        path = self.root / key
        return path.read_bytes() if path.exists() else None

    def put(self, key: str, source: Path | bytes, cache: str):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, bytes):
            path.write_bytes(source)
        else:
            shutil.copyfile(source, path)
        self.log.append(('put', key))

    def delete(self, key: str):
        (self.root / key).unlink(missing_ok=True)
        self.log.append(('delete', key))


class R2Store:
    def __init__(self, endpoint: str, bucket: str, prefix: str):
        self.endpoint, self.bucket, self.prefix = endpoint, bucket, prefix.strip('/')

    def _url(self, key: str) -> str:
        return f's3://{self.bucket}/{self.prefix}/{key}'

    def _aws(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(['aws', 's3', *args, '--endpoint-url', self.endpoint], capture_output=True, text=True)

    def get(self, key: str):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'object'
            result = self._aws('cp', self._url(key), str(target))
            if result.returncode == 0:
                return target.read_bytes()
        # Solo «no existe» significa vacío. Un error de red o de permisos NO: publicar «desde cero» borraría el índice.
        if '404' in result.stderr or 'Not Found' in result.stderr or 'NoSuchKey' in result.stderr:
            return None
        raise RuntimeError(f'No se pudo leer {key}: {result.stderr.strip()}')

    def put(self, key: str, source, cache: str):
        with tempfile.TemporaryDirectory() as folder:
            if isinstance(source, bytes):
                path = Path(folder) / 'object'
                path.write_bytes(source)
            else:
                path = source
            content_type = 'application/vnd.debian.binary-package' if key.endswith('.deb') else 'application/octet-stream'
            result = self._aws('cp', str(path), self._url(key), '--cache-control', cache, '--content-type', content_type)
        if result.returncode != 0:
            raise RuntimeError(f'No se pudo subir {key}: {result.stderr.strip()}')

    def delete(self, key: str):
        result = self._aws('rm', self._url(key))
        if result.returncode != 0:
            raise RuntimeError(f'No se pudo borrar {key}: {result.stderr.strip()}')


# ---------- publicación ----------

def gpg_sign(release: bytes, key_id: str) -> tuple[bytes, bytes]:
    def run(*args):
        return subprocess.run(['gpg', '--batch', '--yes', '--local-user', key_id, *args], input=release,
                              capture_output=True, check=True).stdout
    return run('--digest-algo', 'SHA256', '--clearsign'), run('--digest-algo', 'SHA256', '--armor', '--detach-sign')


def publish(store, debs: list[Path], sign, grace_days: float, max_bytes: int, now: float | None = None,
            dry_run: bool = False) -> dict:
    now = time.time() if now is None else now
    raw = store.get('state.json')
    old = json.loads(raw) if raw else {'files': {}, 'indexes': {}}
    incoming = [(path, read_deb(path)) for path in sorted(debs)]
    state, uploads = merge(old, incoming, now)

    indexes, release = build_indexes(state, now)
    current = {by_hash_key(name, body): len(body) for name, body in indexes.items()}
    for key, entry in state['indexes'].items():
        if key not in current and entry['retired_at'] is None:
            state['indexes'][key] = {**entry, 'retired_at': now}
    new_hashes = {}
    for name, body in indexes.items():
        key = by_hash_key(name, body)
        if key not in state['indexes']:
            new_hashes[key] = body
        state['indexes'][key] = {'size': len(body), 'retired_at': None}

    dead_files = expired(state['files'], now, grace_days)
    dead_indexes = expired(state['indexes'], now, grace_days)
    for key in dead_files:
        del state['files'][key]
    for key in dead_indexes:
        del state['indexes'][key]

    size = total_bytes(state)
    summary = {
        'uploaded': [key for _, key in uploads], 'deleted': dead_files,
        'active': sum(1 for v in state['files'].values() if v['retired_at'] is None),
        'retired': sum(1 for v in state['files'].values() if v['retired_at'] is not None), 'bytes': size,
    }
    if size > max_bytes:
        raise RuntimeError(f'El repositorio ocuparía {size / 1e9:.2f} GB, por encima del tope de {max_bytes / 1e9:.2f} GB. '
                           'No se subió nada.')
    if dry_run:
        return summary

    inrelease, detached = sign(release)  # firmar antes de subir nada: si falla, el repositorio no cambia
    for path, key in uploads:
        store.put(key, path, IMMUTABLE)
    for key, body in new_hashes.items():
        store.put(key, body, IMMUTABLE)
    for name, body in indexes.items():
        store.put(f'dists/stable/{name}', body, NO_CACHE)
    store.put('dists/stable/Release', release, NO_CACHE)
    store.put('dists/stable/Release.gpg', detached, NO_CACHE)
    store.put('dists/stable/InRelease', inrelease, NO_CACHE)
    store.put('state.json', json.dumps(state, sort_keys=True).encode('utf-8'), NO_CACHE)
    # Borrar al final: si algo falla antes, sobran archivos pero no falta ninguno.
    for key in dead_files + dead_indexes:
        store.delete(key)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('debs', type=Path, help='carpeta con los .deb nuevos (se busca en subcarpetas)')
    parser.add_argument('--bucket', default='aurx-dl')
    parser.add_argument('--prefix', default='aurix/apt')
    parser.add_argument('--endpoint', default=os.environ.get('R2_ENDPOINT', ''))
    parser.add_argument('--sign-key', default=os.environ.get('APT_KEY_ID', ''))
    parser.add_argument('--grace-days', type=float, default=7)
    parser.add_argument('--max-gb', type=float, default=5.0)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--local', type=Path, help='ensayo: publica en una carpeta en vez de en R2')
    args = parser.parse_args(argv)
    if not (args.endpoint or args.local) or (not args.sign_key and not args.dry_run):
        parser.error('faltan --endpoint (R2_ENDPOINT) o --local, o --sign-key (APT_KEY_ID)')
    debs = [p for p in args.debs.rglob('*.deb') if '-static_' not in p.name]
    if not debs:
        parser.error(f'no hay .deb en {args.debs}')
    store = LocalStore(args.local) if args.local else R2Store(args.endpoint, args.bucket, args.prefix)
    summary = publish(store, debs,
                      lambda release: gpg_sign(release, args.sign_key), args.grace_days, int(args.max_gb * 1e9),
                      dry_run=args.dry_run)
    print(f'Subidos: {len(summary["uploaded"])} · borrados: {len(summary["deleted"])} · activos: {summary["active"]} · '
          f'en margen antes de borrarse: {summary["retired"]} · tamaño: {summary["bytes"] / 1e9:.2f} GB'
          + (' (simulacro: no se cambió nada)' if args.dry_run else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
