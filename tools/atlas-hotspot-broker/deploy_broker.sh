#!/usr/bin/env bash
# Copy the static broker + clients to the Pi 4 and start it (dev only, /tmp = gone on reboot).
# Usage:  tools/atlas-hotspot-broker/deploy_broker.sh <pi4-ssh-host> [<pi5-ssh-host>]
#   e.g.  tools/atlas-hotspot-broker/deploy_broker.sh root@172.16.34.146 root@172.16.34.198
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PI4="${1:?pi4 ssh host}"; PI5="${2:-}"
DIST="$HERE/dist"
[ -f "$DIST/mosquitto" ] || { echo "run build_mosquitto.sh first" >&2; exit 1; }
SSH="ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
SCP="scp -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# Stop a running broker first (scp cannot overwrite a busy executable).
# NOTE: never `pkill -f mosquitto` over ssh - it matches the ssh shell itself.
$SSH "$PI4" 'for p in $(pidof mosquitto); do kill $p; done; sleep 1; rm -f /tmp/mosquitto /tmp/mosquitto_pub /tmp/mosquitto_sub'
$SCP "$DIST/mosquitto" "$DIST/mosquitto_pub" "$DIST/mosquitto_sub" "$HERE/mosquitto.conf" "$PI4:/tmp/"
$SSH "$PI4" 'chmod +x /tmp/mosquitto /tmp/mosquitto_pub /tmp/mosquitto_sub
setsid /tmp/mosquitto -c /tmp/mosquitto.conf -v > /tmp/mosquitto.log 2>&1 < /dev/null &
sleep 2
netstat -ltn | grep -q ":1883 " && echo "broker listening on 1883" || { cat /tmp/mosquitto.log; exit 1; }
/tmp/mosquitto_pub -h 127.0.0.1 -t deskmate/selftest -m ok && echo "local pub ok"
echo "wlan0: $(ip -4 -o addr show wlan0 2>/dev/null | awk "{print \$4}")"'

if [ -n "$PI5" ]; then
    $SSH "$PI5" 'rm -f /tmp/mosquitto_pub /tmp/mosquitto_sub'
    $SCP "$DIST/mosquitto_pub" "$DIST/mosquitto_sub" "$PI5:/tmp/"
    $SSH "$PI5" 'chmod +x /tmp/mosquitto_pub /tmp/mosquitto_sub; echo "clients copied to pi5"'
fi
