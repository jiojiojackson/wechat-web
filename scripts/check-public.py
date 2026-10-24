#!/usr/bin/env python3
"""Check staged/tracked public source for accidentally added runtime material."""
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
patterns = [r'-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----',
            r'gh[pousr]_[A-Za-z0-9]{30,}', r'github_pat_[A-Za-z0-9_]{30,}',
            r'AKIA[0-9A-Z]{16}', r'eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}']
violations = []
for name in filter(None, names):
    path = root/name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024**2:
        violations.append(name+': unexpected file type/size')
        continue
    if (Path(name).name in ('.env', 'credentials.txt', 'install-state.json') or
            any(x in Path(name).parts for x in ('secrets', 'audit', 'backups', '__pycache__')) or
            name.endswith(('.pem', '.key', '.ext4', '.pyc', '.tar.gz'))):
        violations.append(name+': private/runtime file')
        continue
    text = path.read_text()
    if any(re.search(pattern, text) for pattern in patterns):
        violations.append(name+': potential credential (redacted)')
if violations:
    print('\n'.join(violations), file=sys.stderr)
    raise SystemExit(1)
print(f'Public-source check passed: {len(list(filter(None, names)))} tracked files, no credential patterns or runtime material.')
