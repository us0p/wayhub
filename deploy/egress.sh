#!/bin/sh
# Blocks traffic from Mentor's containers to private networks (the home LAN, the router, other
# containers' networks), behind the app's SSRF guard (D79, D89). Installed as
# /usr/local/sbin/mentor-egress (root-owned) and run at boot, after Docker, by
# mentor-egress.service; safe to run again.
set -eu

MENTOR_NET=172.29.42.0/24  # compose.prod.yml's network

iptables -N MENTOR-EGRESS 2>/dev/null || iptables -F MENTOR-EGRESS
iptables -A MENTOR-EGRESS -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -A MENTOR-EGRESS -d "$MENTOR_NET" -j RETURN  # the app and the worker reach the db
for range in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10; do
  iptables -A MENTOR-EGRESS -d "$range" -j REJECT
done
iptables -C DOCKER-USER -s "$MENTOR_NET" -j MENTOR-EGRESS 2>/dev/null ||
  iptables -I DOCKER-USER -s "$MENTOR_NET" -j MENTOR-EGRESS
