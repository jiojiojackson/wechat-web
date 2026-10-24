#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $EUID -ne 0 ]]; then
    echo '请使用 sudo bash deploy/install.sh' >&2
    exit 1
fi
command -v python3 >/dev/null || { echo '需要 Python 3（Ubuntu Server 默认已安装）' >&2; exit 1; }
exec python3 "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/setup.py" install
