"""Pages packages only allowlisted source and produces stable release bytes."""
import importlib.util
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publisher', ROOT/'scripts/build-pages.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PackageTests(unittest.TestCase):
    def test_allowlist_and_reproducible_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'repo'
            root.mkdir()
            for name, data in {'deploy/setup.py': '# fixture', 'deploy/compose.yaml': '# fixture',
                               'docs/index.html': '<title>fixture</title>',
                               'README.md': '# fixture',
                               'install.sh': 'release=@@RELEASE@@\nhash=@@BUNDLE_SHA256@@\n',
                               '.env': 'fixture-untracked-private-material',
                               'audit/report.txt': 'fixture-private-report'}.items():
                path = root/name
                path.parent.mkdir(exist_ok=True, parents=True)
                path.write_text(data)
            subprocess.run(['git', 'init', '-q', root], check=True)
            subprocess.run(['git', '-C', root, 'add', 'deploy', 'README.md', 'docs/index.html', 'install.sh'], check=True)
            first, second = Path(directory)/'first', Path(directory)/'second'
            with patch.object(publisher, 'ROOT', root):
                publisher.build(first, 'a'*40)
                publisher.build(second, 'a'*40)
            relative = Path('releases')/('a'*40)/'wechat-web.tar.gz'
            a, b = (first/relative).read_bytes(), (second/relative).read_bytes()
            self.assertEqual(a, b)
            self.assertEqual(a[4:8], b'\0'*4)
            with tarfile.open(first/relative) as archive:
                self.assertEqual(set(archive.getnames()),
                                 {'wechat-web/deploy/setup.py', 'wechat-web/deploy/compose.yaml', 'wechat-web/README.md'})
            self.assertNotIn('@@RELEASE@@', (first/'install.sh').read_text())
            self.assertNotIn('@@BUNDLE_SHA256@@', (first/'install.sh').read_text())


if __name__ == '__main__':
    unittest.main()
