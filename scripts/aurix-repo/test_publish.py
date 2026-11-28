"""Pruebas del publicador (sin red ni R2):  python -m unittest discover -s scripts/aurix-repo -p "test_*.py" """
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

from publish import DAY, LocalStore, clean_control, compare_versions, pool_key, publish, read_deb


def make_deb(folder: Path, name: str, version: str, arch: str = 'aarch64', extra: str = '', payload: bytes = b'') -> Path:
    control = f'Package: {name}\nVersion: {version}\nArchitecture: {arch}\n{extra}Description: prueba\n continuación\n'.encode()

    def tar(files):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:xz') as archive:
            for path, body in files:
                info = tarfile.TarInfo(path)
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
        return buffer.getvalue()

    out = b'!<arch>\n'
    for member, body in (('debian-binary', b'2.0\n'), ('control.tar.xz', tar([('./control', control)])),
                         ('data.tar.xz', tar([('./x', payload)]))):
        out += f'{member + "/":<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(body):<10}`\n'.encode() + body + (b'\n' if len(body) % 2 else b'')
    path = folder / f'{name}_{version.replace(":", "_")}_{arch}_{hashlib.sha1(payload).hexdigest()[:6]}.deb'
    path.write_bytes(out)
    return path


def fake_sign(release: bytes):
    return b'SIGNED\n' + release, b'SIGNATURE'


class VersionTests(unittest.TestCase):
    def test_debian_ordering(self):
        ordered = ['1.0~rc1', '1.0', '1.0-1', '1.0-2', '1.0-10', '1.0+dfsg', '1.0.1', '1.2', '1.10', '2.0', '1:0.1']
        for older, newer in zip(ordered, ordered[1:]):
            self.assertLess(compare_versions(older, newer), 0, f'{older} < {newer}')
            self.assertGreater(compare_versions(newer, older), 0, f'{newer} > {older}')
        self.assertEqual(compare_versions('2.4.1-3', '2.4.1-3'), 0)
        self.assertEqual(compare_versions('1.002', '1.2'), 0)

    def test_letters_before_symbols(self):
        self.assertLess(compare_versions('3.6a', '3.6+x'), 0)
        self.assertLess(compare_versions('5.3p1', '5.3p2'), 0)


class ControlTests(unittest.TestCase):
    def test_bad_lines_are_dropped_and_continuations_kept(self):
        fields = clean_control('Package: a\nCommandline junk without colon\nVersion: 1\nDescription: x\n y\n')
        self.assertEqual(fields, {'Package': 'a', 'Version': '1', 'Description': 'x\n y'})

    def test_rejects_unknown_arch_and_bad_names(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                read_deb(make_deb(Path(folder), 'a', '1', arch='mips'))
            with self.assertRaises(ValueError):
                read_deb(make_deb(Path(folder), 'Bad_Name', '1'))


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.debs = Path(self.tmp.name) / 'debs'
        self.debs.mkdir()
        self.store = LocalStore(Path(self.tmp.name) / 'remote')

    def run_publish(self, debs, now, **kwargs):
        options = {'grace_days': 7, 'max_bytes': 10 ** 9, **kwargs}
        return publish(self.store, debs, fake_sign, now=now, **options)

    def index(self, arch='aarch64') -> str:
        return self.store.get(f'dists/stable/main/binary-{arch}/Packages').decode()

    def state(self):
        return json.loads(self.store.get('state.json'))

    def test_first_publish_creates_signed_consistent_repo(self):
        deb = make_deb(self.debs, 'htop', '3.4.1')
        shared = make_deb(self.debs, 'ca-certificates', '2026.1', arch='all')
        self.run_publish([deb, shared], now=1000)
        for arch in ('aarch64', 'arm', 'x86_64'):
            self.assertIn('Package: ca-certificates', self.index(arch))
        self.assertIn('Package: htop', self.index('aarch64'))
        self.assertNotIn('Package: htop', self.index('arm'))
        release = self.store.get('dists/stable/Release').decode()
        self.assertIn('Acquire-By-Hash: yes', release)
        for line in release.split('SHA256:\n', 1)[1].splitlines():
            digest, size, name = line.split()
            body = self.store.get(f'dists/stable/{name}')
            self.assertEqual((hashlib.sha256(body).hexdigest(), len(body)), (digest, int(size)))
            self.assertEqual(self.store.get(f'dists/stable/{name.rsplit("/", 1)[0]}/by-hash/SHA256/{digest}'), body)
        self.assertTrue(self.store.get('dists/stable/InRelease').startswith(b'SIGNED'))
        filename = [l for l in self.index().splitlines() if l.startswith('Filename: ') and 'htop' in l][0].split(' ', 1)[1]
        self.assertEqual(self.store.get(filename), deb.read_bytes())

    def test_index_is_readable_by_the_build_system_script(self):
        deb = make_deb(self.debs, 'libx', '1:2.0', extra='Depends: liby\n')
        self.run_publish([deb, make_deb(self.debs, 'zeta', '1')], now=0)
        index = Path(self.tmp.name) / 'Packages'
        index.write_text(self.index(), encoding='utf-8')
        script = Path(__file__).resolve().parent.parent / 'get_hash_from_file.py'
        for version in ('1:2.0', '*'):
            out = subprocess.run([sys.executable, str(script), str(index), 'libx', version], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.split(), ['pool/main/libx/libx_e1_2.0_aarch64.deb', hashlib.sha256(deb.read_bytes()).hexdigest()])
        release = Path(self.tmp.name) / 'InRelease'
        release.write_bytes(self.store.get('dists/stable/Release'))
        out = subprocess.run([sys.executable, str(script), str(release), 'aarch64', 'main'], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), hashlib.sha256(self.store.get('dists/stable/main/binary-aarch64/Packages')).hexdigest())

    def test_new_version_leaves_index_at_once_and_is_deleted_after_grace(self):
        self.run_publish([make_deb(self.debs, 'htop', '3.4.1')], now=0)
        old_file = 'pool/main/htop/htop_3.4.1_aarch64.deb'
        self.run_publish([make_deb(self.debs, 'htop', '3.5.0')], now=DAY)
        self.assertNotIn('3.4.1', self.index())
        self.assertIsNotNone(self.store.get(old_file), 'dentro del margen sigue descargable')
        other = make_deb(self.debs, 'jq', '1.8')
        self.run_publish([other], now=DAY + 6 * DAY)
        self.assertIsNotNone(self.store.get(old_file))
        summary = self.run_publish([other], now=DAY + 7 * DAY)
        self.assertEqual(summary['deleted'], [old_file])
        self.assertIsNone(self.store.get(old_file))
        htop = next(s for s in self.index().split(chr(10) * 2) if s.startswith('Package: htop'))
        self.assertIn('Version: 3.5.0', htop)

    def test_never_downgrades_and_never_replaces_same_version(self):
        first = make_deb(self.debs, 'vim', '9.1', payload=b'uno')
        self.run_publish([first], now=0)
        rebuilt = make_deb(self.debs, 'vim', '9.1', payload=b'dos')
        older = make_deb(self.debs, 'vim', '9.0', payload=b'tres')
        summary = self.run_publish([rebuilt, older], now=10)
        self.assertEqual(summary['uploaded'], [])
        self.assertEqual(self.store.get('pool/main/vim/vim_9.1_aarch64.deb'), first.read_bytes())

    def test_unrelated_packages_stay_when_publishing_only_some(self):
        self.run_publish([make_deb(self.debs, 'htop', '1'), make_deb(self.debs, 'jq', '1')], now=0)
        self.run_publish([make_deb(self.debs, 'jq', '2')], now=100 * DAY)
        self.assertIn('Package: htop', self.index())

    def test_size_limit_uploads_nothing(self):
        big = make_deb(self.debs, 'rust', '1', payload=bytes(range(256)) * 400)
        with self.assertRaises(RuntimeError):
            self.run_publish([big], now=0, max_bytes=500)
        self.assertEqual(self.store.log, [])

    def test_signing_failure_changes_nothing(self):
        def broken(_):
            raise RuntimeError('sin clave')
        with self.assertRaises(RuntimeError):
            publish(self.store, [make_deb(self.debs, 'jq', '1')], broken, 7, 10 ** 9, now=0)
        self.assertEqual(self.store.log, [])

    def test_inrelease_goes_up_after_files_and_deletes_come_last(self):
        self.run_publish([make_deb(self.debs, 'jq', '1')], now=0)
        self.run_publish([make_deb(self.debs, 'jq', '2')], now=1)
        self.store.log.clear()
        self.run_publish([make_deb(self.debs, 'jq', '3')], now=30 * DAY)
        keys = [key for _, key in self.store.log]
        self.assertLess(keys.index('pool/main/jq/jq_3_aarch64.deb'), keys.index('dists/stable/InRelease'))
        first_delete = next(i for i, (action, _) in enumerate(self.store.log) if action == 'delete')
        self.assertGreater(first_delete, keys.index('state.json'))

    def test_file_names_survive_url_decoding(self):
        debs = [make_deb(self.debs, 'git', '1:2.0'), make_deb(self.debs, 'git', '2.0', arch='arm'),
                make_deb(self.debs, 'libc++', '2.0~rc1+x')]
        self.run_publish(debs, now=0)
        for arch in ('aarch64', 'arm'):
            for line in self.index(arch).splitlines():
                if line.startswith('Filename: '):
                    key = line.split(' ', 1)[1]
                    self.assertEqual(unquote(key), key)
                    self.assertNotIn(':', key)
        self.assertIsNotNone(self.store.get('pool/main/git/git_e1_2.0_aarch64.deb'))

    def test_epoch_never_collides_with_another_version(self):
        keys = {pool_key({'Package': 'a', 'Version': v, 'Architecture': 'arm'}) for v in ('2.0', '1:2.0', '2:2.0', 'e1-2.0')}
        self.assertEqual(len(keys), 4)

    def publish_with_old_names(self, debs, now):
        """Deja el repositorio como lo dejó el publicador anterior, con «%3a» en la clave."""
        state = {'files': {}, 'indexes': {}}
        for path in debs:
            info = read_deb(path)
            key = pool_key(info['fields']).replace('_e1_', '_1%3a')
            state['files'][key] = {**info, 'retired_at': None}
            self.store.put(key, path, '')
        self.store.put('state.json', json.dumps(state).encode(), '')

    def test_republishing_same_version_renames_undownloadable_files(self):
        deb = make_deb(self.debs, 'openssl', '1:3.6')
        self.publish_with_old_names([deb], now=0)
        old, new = 'pool/main/openssl/openssl_1%3a3.6_aarch64.deb', 'pool/main/openssl/openssl_e1_3.6_aarch64.deb'
        summary = self.run_publish([deb], now=100)
        self.assertEqual(summary['uploaded'], [new])
        self.assertIn(f'Filename: {new}', self.index())
        self.assertNotIn('%3a', self.index())
        self.assertEqual(self.store.get(new), deb.read_bytes())
        self.assertEqual(self.run_publish([deb], now=200)['uploaded'], [])
        self.assertEqual(self.run_publish([deb], now=100 + 7 * DAY)['deleted'], [old])

    def test_refuses_to_leave_undownloadable_files_in_the_index(self):
        self.publish_with_old_names([make_deb(self.debs, 'openssl', '1:3.6')], now=0)
        self.store.log.clear()
        with self.assertRaises(RuntimeError):
            self.run_publish([make_deb(self.debs, 'jq', '1')], now=100)
        self.assertEqual(self.store.log, [])


if __name__ == '__main__':
    unittest.main()
