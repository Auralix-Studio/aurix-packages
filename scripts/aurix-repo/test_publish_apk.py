"""Pruebas del publicador de APK (sin red, sin R2 y sin el SDK de Android)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from publish import LocalStore
from publish_apk import VARIANTS, check_badging, check_signer, old_releases, plan, publish, source_name

CERT = 'a' * 64
BADGING = "package: name='pro.auralix.aurix' versionCode='3' versionName='0.2.0' platformBuildVersionName=''\n"


class CheckTests(unittest.TestCase):
    def test_only_the_release_build_of_this_app_and_version_passes(self):
        self.assertEqual(check_badging(BADGING, '0.2.0'), 3)
        for bad in (
            BADGING.replace('pro.auralix.aurix', 'pro.otra.app'),
            BADGING.replace("versionName='0.2.0'", "versionName='0.1.0'"),
            BADGING + 'application-debuggable\n',
            'basura',
        ):
            with self.assertRaises(ValueError):
                check_badging(bad, '0.2.0')

    def test_only_our_single_signer_passes(self):
        line = f'Signer #1 certificate SHA-256 digest: {CERT}\n'
        check_signer(line, CERT.upper())
        # Versiones nuevas de apksigner: una línea por esquema de firma, todas con el mismo certificado.
        check_signer(f'V2 Signer: certificate SHA-256 digest: {CERT}\nV3 Signer: certificate SHA-256 digest: {CERT}\n', CERT)
        check_signer(f'Signer (minSdkVersion=24, maxSdkVersion=2147483647) certificate SHA-256 digest: {CERT}\n', CERT)
        for bad in('', line.replace('a' * 64, 'b' * 64), line + f'Signer #2 certificate SHA-256 digest: {"c" * 64}\n'):
            with self.assertRaises(ValueError):
                check_signer(bad, CERT)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LocalStore(Path(self.tmp.name) / 'remote')

    def apks(self, version, content=b'apk'):
        folder = Path(self.tmp.name) / f'in-{version}-{content.decode()}'
        folder.mkdir()
        for variant in VARIANTS:
            (folder / source_name(variant, version)).write_bytes(content + variant.encode())
        return plan(folder, version)

    def test_layout_matches_the_other_apps_and_the_manifest_is_true(self):
        publish(self.store, self.apks('0.2.0'), '0.2.0', 1, now=1_000_000)
        self.assertEqual(self.store.get('apk/aurix-android.apk'), b'apkuniversal')
        self.assertEqual(self.store.get('releases/v0.2.0/apk/aurix-v0.2.0-arm64.apk'), b'apkarm64')
        data = json.loads(self.store.get('version.json'))
        asset = data['assets']['android-arm64']
        self.assertEqual(asset['sha256'], hashlib.sha256(b'apkarm64').hexdigest())
        self.assertEqual(asset['latest_url'], 'https://dl.aurx.pro/aurix/apk/aurix-android-arm64.apk')
        self.assertIn(f'{asset["sha256"]}  aurix-v0.2.0-arm64.apk', self.store.get('SHA256SUMS.txt').decode())
        keys = [key for _, key in self.store.log]
        self.assertLess(keys.index('releases/v0.2.0/apk/aurix-v0.2.0-universal.apk'), keys.index('apk/aurix-android.apk'))
        self.assertEqual(keys[-1], 'version.json')
        self.assertTrue(all(k.startswith(('apk/', 'releases/')) or k in ('SHA256SUMS.txt', 'version.json') for k in keys))

    def test_never_goes_back_but_allows_rebuilding_the_same_version(self):
        publish(self.store, self.apks('0.3.0'), '0.3.0', 5, now=0)
        publish(self.store, self.apks('0.3.0', b'otro'), '0.3.0', 5, now=0)
        self.store.log.clear()
        for version, build in (('0.2.9', 9), ('0.3.0', 4)):
            with self.assertRaises(RuntimeError):
                publish(self.store, self.apks(version, f'x{build}'.encode()), version, build, now=0)
        self.assertEqual(self.store.log, [])

    def test_all_four_variants_are_required(self):
        files = self.apks('0.2.0')
        files['armv7'].unlink()
        with self.assertRaises(ValueError):
            plan(files['arm64'].parent, '0.2.0')

    def test_cleanup_only_touches_older_version_folders(self):
        folders = ['releases/v0.1.0/', 'releases/v0.2.0/', 'releases/v0.3.0/', 'releases/notas/', 'apt/']
        self.assertEqual(old_releases(folders, '0.3.0'), ['releases/v0.1.0/', 'releases/v0.2.0/'])


if __name__ == '__main__':
    unittest.main()
