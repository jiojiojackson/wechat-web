#!/bin/bash
set -euo pipefail
umask 077
exec 9>/run/lock/wechat-web-backup.lock
flock -n 9 || exit 0

backup_dir=/var/backups/wechat-web
mountpoint -q /var/lib/wechat-web/config
install -d -m 0700 "$backup_dir"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$backup_dir/config-$stamp.tar.gz"
paused=false
freeze_wechat() {
    docker exec -i wechat-web /lsiopy/bin/python3 - "$1" <<'PY'
from pathlib import Path
import json,os,signal,sys
record=Path('/run/wechat-web-backup-frozen.json')
if sys.argv[1]=='resume':
    if record.exists():
        for pid in json.loads(record.read_text()):
            try:os.kill(pid,signal.SIGCONT)
            except ProcessLookupError:pass
        record.unlink()
else:
    if record.exists():raise SystemExit('A previous application freeze requires recovery')
    pids=[]
    # Stop the client before helpers, leaving Nginx, verification and health checks live.
    for wanted in ('wechat','WeChatAppEx','crashpad_handle'):
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():continue
            try:
                if (entry/'comm').read_text().strip()!=wanted:continue
                pid=int(entry.name)
                pids.append(pid)
                record.write_text(json.dumps(pids));record.chmod(0o600)
                os.kill(pid,signal.SIGSTOP)
            except (FileNotFoundError,ProcessLookupError):pass
PY
}
cleanup() {
    if [ "$paused" = true ]; then freeze_wechat resume || true; fi
    if [ -f "$archive.partial" ]; then rm -f "$archive.partial"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM
if [ "${1:-}" = --resume ]; then
    freeze_wechat resume
    exit 0
fi
if [ "$(docker inspect --format '{{.State.Running}}' wechat-web)" = true ]; then
    paused=true
    freeze_wechat freeze
fi
timeout 120 tar -C /var/lib/wechat-web -I 'gzip -1' -cf "$archive.partial" config
if [ "$paused" = true ]; then
    freeze_wechat resume
    paused=false
fi
mv "$archive.partial" "$archive"
chmod 0600 "$archive"
find "$backup_dir" -maxdepth 1 -type f -name 'config-*.tar.gz' -mtime +7 -delete
python3 - "$backup_dir" <<'PY'
from pathlib import Path
import sys
files = sorted(Path(sys.argv[1]).glob('config-*.tar.gz'))
total = sum(p.stat().st_size for p in files)
while len(files) > 1 and total > 16 * 1024**3:
    oldest = files.pop(0)
    total -= oldest.stat().st_size
    oldest.unlink()
PY
printf 'Backup completed: %s\n' "$archive"
