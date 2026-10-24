"""Downloaded bundle integrity and safe extraction checks, without host install."""
import hashlib
import io
import os
import shutil
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def test_missing_python_and_curl_are_installed_before_use(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bindir = root/'bin'
            bindir.mkdir()
            for name in ('env','chmod','mktemp','sha256sum','ip'):
                (bindir/name).symlink_to(shutil.which(name))
            sudo = bindir/'sudo'
            sudo.write_text('#!/bin/bash\nexec "$@"\n')
            sudo.chmod(0o700)
            apt = bindir/'apt-get'
            apt.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$APT_RECORD"\n'
                           'if [[ $1 == install ]]; then\n'
                           ' /usr/bin/ln -sf "$PYTHON_BINARY" "$FIXTURE_BIN/python3"\n'
                           ' /usr/bin/ln -sf "$CURL_BINARY" "$FIXTURE_BIN/curl"\nfi\n')
            apt.chmod(0o700)
            env = dict(os.environ, PATH=str(bindir), APT_RECORD=str(root/'apt.log'),
                       FIXTURE_BIN=str(bindir), PYTHON_BINARY=shutil.which('python3'), CURL_BINARY=shutil.which('curl'))
            result = subprocess.run(['/bin/bash','-c','source "$1"; bootstrap_dependencies; command -v python3; command -v curl',
                                     '--',str(ROOT/'deploy/bootstrap.sh')],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            record=(root/'apt.log').read_text()
            self.assertIn('python3',record)
            self.assertIn('curl',record)
            self.assertTrue((bindir/'python3').exists())
            self.assertTrue((bindir/'curl').exists())

    def exercise(self, extra=None, corrupt=False, sudo_mode='allowed', as_root=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root/'source.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                content = b'#!/bin/bash\nprintf success > "$MARKER"\n'
                entry = tarfile.TarInfo('wechat-web/deploy/install.sh')
                entry.size = len(content)
                tar.addfile(entry, io.BytesIO(content))
                if extra:
                    entry = tarfile.TarInfo(extra[0])
                    if extra[1] == 'link':
                        entry.type, entry.linkname = tarfile.SYMTYPE, '/etc'
                        tar.addfile(entry)
                    else:
                        entry.size = 4
                        tar.addfile(entry, io.BytesIO(b'evil'))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            template = ((ROOT/'install.sh').read_text().replace('@@RELEASE@@', 'a'*40)
                        .replace('@@BOOTSTRAP@@', (ROOT/'deploy/bootstrap.sh').read_text()))
            script = root/'bootstrap.sh'
            script.write_text(template.replace('@@BUNDLE_SHA256@@', '0'*64 if corrupt else digest))
            bindir = root/'bin'
            bindir.mkdir()
            for name in ('bash', 'chmod', 'mktemp', 'sha256sum', 'ip', 'env', 'rm', 'cp', 'python3'):
                (bindir/name).symlink_to(shutil.which(name))
            curl = bindir/'curl'
            curl.write_text('#!/bin/bash\nprintf downloaded > "$DOWNLOAD_RECORD"\nwhile (($#)); do if [[ $1 == -o ]]; then cp "$SOURCE_ARCHIVE" "$2"; exit; fi; shift; done\nexit 1\n')
            curl.chmod(0o700)
            sudo = bindir/'sudo'
            sudo.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$SUDO_RECORD"\n'
                            'if [[ $SUDO_MODE == denied ]]; then exit 1; fi\n'
                            'if [[ $1 == -v ]]; then exit 0; fi\nexec "$@"\n')
            sudo.chmod(0o700)
            if sudo_mode == 'missing':
                sudo.unlink()
            env = dict(os.environ, PATH=str(bindir), SOURCE_ARCHIVE=str(archive), MARKER=str(root/'installed'),
                       SUDO_MODE=sudo_mode, SUDO_RECORD=str(root/'sudo.log'), DOWNLOAD_RECORD=str(root/'downloaded'))
            options = {}
            # Also exercise the ordinary-user path when this suite runs as
            # root inside the isolated container. No real sudo policy is used.
            if os.geteuid() == 0 and not as_root:
                os.chown(root, 1000, 1000)
                for path in root.rglob('*'):
                    os.chown(path, 1000, 1000, follow_symlinks=False)
                options = dict(user=1000, group=1000)
            result = subprocess.run(['/bin/bash', str(script)], env=env, capture_output=True, text=True, **options)
            return dict(code=result.returncode, installed=(root/'installed').exists(), error=result.stderr,
                        downloaded=(root/'downloaded').exists(),
                        sudo=(root/'sudo.log').read_text() if (root/'sudo.log').exists() else '')

    def test_valid_bundle_runs(self):
        result = self.exercise()
        self.assertEqual(result['code'], 0, result['error'])
        self.assertTrue(result['installed'])
        self.assertTrue(result['sudo'].startswith('-v\n'))

    def test_missing_sudo_stops_before_any_download(self):
        result = self.exercise(sudo_mode='missing')
        self.assertNotEqual(result['code'], 0)
        self.assertFalse(result['downloaded'])
        self.assertFalse(result['installed'])
        self.assertIn('管理员权限', result['error'])

    def test_denied_or_cancelled_sudo_stops_before_any_download(self):
        result = self.exercise(sudo_mode='denied')
        self.assertNotEqual(result['code'], 0)
        self.assertFalse(result['downloaded'])
        self.assertFalse(result['installed'])
        self.assertIn('sudo 授权失败', result['error'])

    @unittest.skipUnless(os.geteuid() == 0, 'Root branch is exercised in the isolated container')
    def test_root_entry_does_not_need_sudo(self):
        result = self.exercise(sudo_mode='missing', as_root=True)
        self.assertEqual(result['code'], 0, result['error'])
        self.assertTrue(result['installed'])
        self.assertEqual(result['sudo'], '')

    def test_digest_mismatch_stops_before_install(self):
        result = self.exercise(corrupt=True)
        self.assertNotEqual(result['code'], 0)
        self.assertFalse(result['installed'])

    def test_traversal_stops_before_install(self):
        for name in ('../escape', '/tmp/escape', 'wechat-web/../../escape'):
            result = self.exercise((name, 'file'))
            self.assertNotEqual(result['code'], 0)
            self.assertFalse(result['installed'])

    def test_symlink_stops_before_install(self):
        result = self.exercise(('wechat-web/link', 'link'))
        self.assertNotEqual(result['code'], 0)
        self.assertFalse(result['installed'])


if __name__ == '__main__':
    unittest.main()
