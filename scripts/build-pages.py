#!/usr/bin/env python3
"""Publish only a reviewed file allowlist, never a working-tree tar archive."""
import hashlib
import gzip
import io
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def build(output, revision):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Expected a full Git commit SHA')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    release = output / 'releases' / revision
    release.mkdir(parents=True, exist_ok=True)
    bundle = release / 'wechat-web.tar.gz'
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    names = [name for name in tracked if name and (name.startswith('deploy/') or
             name in ('docs/install-guide.txt', 'docs/upstream-LICENSE', 'README.md', 'LICENSE', 'NOTICE.md', 'SECURITY.md'))]
    if 'deploy/setup.py' not in names or 'deploy/compose.yaml' not in names:
        raise ValueError('Required source files missing from Git index')
    # A rerun for the same commit must keep the same bytes and digest, including
    # the gzip header. Cached bootstrap scripts then remain usable.
    with bundle.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(names):
            path = ROOT / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Only regular source files may be published')
            data = path.read_bytes()
            entry = tarfile.TarInfo('wechat-web/' + name)
            entry.mode, entry.mtime, entry.uid, entry.gid = 0o644, 0, 0, 0
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    installer = (ROOT / 'install.sh').read_text().replace('@@RELEASE@@', revision).replace('@@BUNDLE_SHA256@@', digest)
    (output / 'install.sh').write_text(installer)
    (output / 'SHA256SUMS').write_text(f'{digest}  releases/{revision}/wechat-web.tar.gz\n')
    (output / 'version.txt').write_text(revision + '\n')
    (output / '.nojekyll').touch()
    (output / 'index.html').write_text((ROOT / 'docs/index.html').read_text())
    print(f'Built Pages installer for {revision}; bundle sha256 {digest}')


if __name__ == '__main__':
    import sys
    build(sys.argv[1] if len(sys.argv) > 1 else ROOT / '_site',
          subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip())
