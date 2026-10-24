#!/usr/bin/env python3
"""Disposable, networkless regression runner. No production stream connection."""
import argparse
import base64
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run_python(image, source, *, webkit=False):
    command = ['docker', 'run', '--rm', '--network', 'none', '--shm-size', '512m',
               '--memory', '3g', '--cpus', '2', '-i',
               '--entrypoint', '/lsiopy/bin/python3']
    if webkit:
        command += ['-e', 'WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1']
    command += [image, '-']
    subprocess.run(command, input=source, text=True, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='wechat-web:local')
    parser.add_argument('--webkit-image', help='Optional SDK built with tests/Dockerfile.webkit')
    parser.add_argument('--frontend-only', action='store_true')
    args = parser.parse_args()
    if not args.frontend_only:
        run_python(args.image, (ROOT/'tests/test_access_guard.py').read_text().replace(
            'from pathlib import Path\nguard_path =',
            'from pathlib import Path\nos.environ["WECHAT_GUARD_PATH"] = "/usr/local/lib/wechat-access-guard.py"\nguard_path ='))
        run_python(args.image, (ROOT/'tests/test_gateway.py').read_text())
    if args.webkit_image:
        assets = {name: (ROOT/'deploy'/name).read_text() for name in
                  ('mobile-tools.js', 'mobile-tools.css', 'mobile-viewport.js', 'safari-codecs.js')}
        for test, patch in (('test_mobile_ime.py', 'mobile-ime.js'),
                            ('test_mobile_tools.py', None),
                            ('test_mobile_viewport.py', 'mobile-viewport.js'),
                            ('test_safari_codecs.py', None)):
            source = (ROOT/'tests'/test).read_text()
            if patch:
                source = source.replace('__PATCH_B64__', base64.b64encode((ROOT/'deploy'/patch).read_bytes()).decode())
            source = source.replace('__ASSETS_B64__', base64.b64encode(json.dumps(assets).encode()).decode())
            print(f'Running {test}', flush=True)
            run_python(args.webkit_image, source, webkit=True)


if __name__ == '__main__':
    main()
