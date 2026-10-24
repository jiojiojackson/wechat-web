#!/usr/bin/env bash
set -Eeuo pipefail
wechat_source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/bootstrap.sh
source "$wechat_source_dir/bootstrap.sh"
bootstrap_authorize
if [[ $EUID -ne 0 ]]; then
    exec sudo bash -- "$wechat_source_dir/install.sh" "$@"
fi
bootstrap_dependencies
exec python3 "$wechat_source_dir/setup.py" "$@"
