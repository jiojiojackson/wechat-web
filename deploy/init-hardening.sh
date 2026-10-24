#!/bin/bash
set -euo pipefail

install -m 0644 /opt/wechat-hardening/nginx.conf /etc/nginx/sites-available/default
test -s /run/secrets/web-password
htpasswd -i -B -C 12 -c /run/wechat-htpasswd wechat < /run/secrets/web-password
chown root:nogroup /run/wechat-htpasswd
chmod 0640 /run/wechat-htpasswd
/lsiopy/bin/python3 - <<'PY'
from pathlib import Path
import hashlib,hmac,json,os
key=bytes.fromhex(Path('/run/secrets/session-key').read_text().strip())
if len(key)!=32:raise SystemExit('Session signing key must contain 32 random bytes')
password=Path('/run/secrets/web-password').read_bytes().rstrip(b'\n')
config={'key':key.hex(),
        'credential_version':hmac.new(key,b'credential-version:'+password,hashlib.sha256).hexdigest(),
        'password_hash':Path('/run/wechat-htpasswd').read_text().strip().split(':',1)[1]}
path=Path('/run/wechat-session.json')
path.write_text(json.dumps(config))
os.chown(path,0,65534)
path.chmod(0o640)
PY
sed -i 's/^user .*/user abc;/' /etc/nginx/nginx.conf
printf 'ok\n' > /usr/share/selkies/web/healthz.txt
/lsiopy/bin/python3 /opt/wechat-hardening/install-mobile-ime.py

# The upstream init script copies proot-apps into the persistent home directory.
# Remove only those non-WeChat launchers, without touching chat data.
rm -f /config/.local/bin/proot-apps /config/.local/bin/proot-apps* /config/.local/bin/pversion
for file in /usr/bin/proot /usr/bin/docker /usr/bin/dockerd /usr/local/bin/docker /usr/local/bin/dockerd; do
    if [ -f "$file" ]; then chmod 0000 "$file"; fi
done
for file in /usr/bin/sudo /usr/bin/su /usr/bin/pkexec /usr/bin/newgrp /usr/bin/passwd /usr/bin/chsh /usr/bin/chfn; do
    if [ -f "$file" ]; then chmod 0000 "$file"; fi
done
find /usr /bin /sbin /opt -xdev -type f -perm /6000 -exec chmod a-s '{}' +
nginx -t
