#!/bin/bash
set -euo pipefail
# Keep the two other writable application directories in bounded memory mounts.
cp -a /opt/wechat-hardening/defaults/. /defaults/
chown 1000:1000 /app
exec /init "$@"
