#!/usr/bin/env bash
set -Eeuo pipefail
exec 8>/run/lock/wechat-web-firewall.lock
flock -x 8

# Only the dedicated application bridge is affected. Preserve host SSH, other
# services, cloud firewall rules, and the existing INPUT/FORWARD policies.
if [[ ${1:-} == --check ]]; then
    iptables -w -C DOCKER-USER -j WXWEB-ISOLATE
    iptables -w -C INPUT -j WXWEB-HOST4
    ip6tables -w -C INPUT -j WXWEB-HOST6
    for network in 10.0.0.0/8 169.254.0.0/16 172.16.0.0/12; do
        iptables -w -C WXWEB-ISOLATE -i br-wxweb -d "$network" -j REJECT
    done
    iptables -w -C WXWEB-ISOLATE ! -i br-wxweb -o br-wxweb -m conntrack --ctstate NEW -j DROP
    exit 0
fi

iptables -w -N DOCKER-USER 2>/dev/null || true
for chain in WXWEB-ISOLATE WXWEB-HOST4; do iptables -w -N "$chain" 2>/dev/null || true; done
ip6tables -w -N WXWEB-HOST6 2>/dev/null || true

# iptables-restore commits each table atomically, avoiding a flushed chain
# while an existing container is still running.
{
    echo '*filter'
    echo '-F WXWEB-ISOLATE'
    for network in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 224.0.0.0/4 240.0.0.0/4; do
        echo "-A WXWEB-ISOLATE -i br-wxweb -d $network -j REJECT"
    done
    echo '-A WXWEB-ISOLATE -i br-wxweb -o br-wxweb -j REJECT'
    echo '-A WXWEB-ISOLATE ! -i br-wxweb -o br-wxweb -m conntrack --ctstate NEW -j DROP'
    echo '-A WXWEB-ISOLATE -j RETURN'
    echo '-F WXWEB-HOST4'
    echo '-A WXWEB-HOST4 -i br-wxweb -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT'
    echo '-A WXWEB-HOST4 -i br-wxweb -j REJECT'
    echo '-A WXWEB-HOST4 -j RETURN'
    echo COMMIT
} | iptables-restore -w --noflush
ip6tables-restore -w --noflush <<'RULES'
*filter
-F WXWEB-HOST6
-A WXWEB-HOST6 -i br-wxweb -j DROP
-A WXWEB-HOST6 -j RETURN
COMMIT
RULES

iptables -w -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -w -I FORWARD 1 -j DOCKER-USER
iptables -w -C DOCKER-USER -j WXWEB-ISOLATE 2>/dev/null || iptables -w -I DOCKER-USER 1 -j WXWEB-ISOLATE
iptables -w -C INPUT -j WXWEB-HOST4 2>/dev/null || iptables -w -I INPUT 1 -j WXWEB-HOST4
ip6tables -w -C INPUT -j WXWEB-HOST6 2>/dev/null || ip6tables -w -I INPUT 1 -j WXWEB-HOST6
iptables -w -C DOCKER-USER -j WXWEB-ISOLATE
iptables -w -C INPUT -j WXWEB-HOST4
ip6tables -w -C INPUT -j WXWEB-HOST6
