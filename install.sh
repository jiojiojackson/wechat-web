#!/usr/bin/env bash
# Published by scripts/build-pages.py with an immutable bundle and its hash.
set -Eeuo pipefail
readonly BASE_URL='https://jiojiojackson.github.io/wechat-web'
readonly RELEASE='@@RELEASE@@'
readonly BUNDLE_SHA256='@@BUNDLE_SHA256@@'
if [[ $RELEASE == *'@@'* ]]; then
    echo '源码安装请运行：sudo bash deploy/install.sh；本引导器需由 Pages 构建后使用。' >&2
    exit 1
fi
@@BOOTSTRAP@@
bootstrap_dependencies
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
chmod 0700 "$work_dir"
curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --connect-timeout 20 --max-time 180 \
    "$BASE_URL/releases/$RELEASE/wechat-web.tar.gz" -o "$work_dir/bundle.tar.gz"
printf '%s  %s\n' "$BUNDLE_SHA256" "$work_dir/bundle.tar.gz" | sha256sum -c -
python3 - "$work_dir" <<'PY'
from pathlib import Path, PurePosixPath
import sys, tarfile
root = Path(sys.argv[1])
with tarfile.open(root / 'bundle.tar.gz', 'r:gz') as archive:
    members = archive.getmembers()
    if len(members) > 200 or sum(x.size for x in members) > 20 * 1024**2:
        raise SystemExit('Installation bundle exceeds expected limits')
    for entry in members:
        path = PurePosixPath(entry.name)
        if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'wechat-web':
            raise SystemExit('Unsafe installation bundle path')
        if not (entry.isdir() or entry.isfile()):
            raise SystemExit('Links and special files are not allowed in installation bundles')
    for entry in members:
        target = root.joinpath(*PurePosixPath(entry.name).parts)
        if entry.isdir():
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(entry).read())
            target.chmod(0o600)
PY
echo "已验证安装包：$RELEASE"
if [[ $EUID -eq 0 ]]; then
    bash "$work_dir/wechat-web/deploy/install.sh"
else
    sudo bash "$work_dir/wechat-web/deploy/install.sh"
fi
