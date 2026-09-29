#!/usr/bin/env bash
set -euo pipefail
# The short root bootstrap configures only this container's network namespace.
# The developer, Pi, and editor run without any Linux capabilities afterward.
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -d 127.0.0.0/8 -j ACCEPT
while read -r key value rest; do
    if [ "$key" = nameserver ] && [[ "$value" != *:* ]]; then
        iptables -A OUTPUT -d "$value" -p udp --dport 53 -j ACCEPT
        iptables -A OUTPUT -d "$value" -p tcp --dport 53 -j ACCEPT
    fi
done < /etc/resolv.conf
for network in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16; do
    iptables -A OUTPUT -d "$network" -j REJECT
done
# Docker's default bridge has no IPv6 route. Reject new IPv6 connections as well
# if enabled later; established browser connections can still receive replies.
if [ -e /proc/net/if_inet6 ]; then
    ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    ip6tables -A OUTPUT -d ::1/128 -j ACCEPT
    ip6tables -A OUTPUT -j REJECT
fi
exec setpriv --reuid="${WORKSPACE_UID:-1000}" --regid="${WORKSPACE_GID:-1000}" \
    --clear-groups --bounding-set=-all --inh-caps=-all --ambient-caps=-all \
    bash /opt/workspace/start.sh
