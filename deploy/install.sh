#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $EUID -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || { echo '请使用 root 或具备 sudo 权限的账号。' >&2; exit 1; }
    exec sudo bash "$0" "$@"
fi
wechat_source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/bootstrap.sh
source "$wechat_source_dir/bootstrap.sh"
bootstrap_dependencies
exec python3 "$wechat_source_dir/setup.py" "$@"
