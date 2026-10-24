#!/bin/bash
set -u

# Keep the desktop bootstrap and upstream WeChat binary; do not scan downloaded
# .desktop files or expose additional application launchers in the menu.
mkdir -p /config/.config/openbox /config/Desktop
if [ ! -f /config/.config/openbox/rc.xml ]; then
    cp /etc/xdg/openbox/rc.xml /config/.config/openbox/rc.xml
fi
cp /defaults/menu.xml /config/.config/openbox/menu.xml
openbox --reconfigure 2>/dev/null || true
nohup stalonetray --dockapp-mode simple >/tmp/wechat-tray.log 2>&1 &
nohup /usr/bin/wechat >/tmp/wechat-client.log 2>&1 &
