#!/bin/sh
# Run ON an ATLAS board (Pi 4 / Pi 5, BusyBox sh). Joins a WPA-PSK Wi-Fi network
# (e.g. a phone hotspot) through the NetworkManager-compatible D-Bus API that
# connman-adapter exposes. ConnMan *.config provisioning and the connmanctl agent
# do not work on ATLAS 26.06, this path does.
#
# Usage:  sh board_wifi_connect.sh <SSID> <PASSPHRASE>
# The passphrase lives only in the board's D-Bus session (gone after reboot).
set -e
SSID="$1"; PASS="$2"
[ -n "$SSID" ] && [ -n "$PASS" ] || { echo "usage: $0 <SSID> <PASSPHRASE>" >&2; exit 2; }
NM=org.freedesktop.NetworkManager

# already on this SSID?
if iw dev wlan0 link 2>/dev/null | grep -q "SSID: $SSID\$" && ip -4 -o addr show wlan0 | grep -q inet; then
    echo "already connected: $(ip -4 -o addr show wlan0 | awk '{print $4}')"
    exit 0
fi

# ConnMan's scan list sometimes goes empty after a disconnect; a wifi power
# toggle brings it back (and re-associates on its own if the PSK is cached).
scan_has_ssid() { connmanctl services 2>/dev/null | grep -q " $SSID  *wifi_"; }
connmanctl enable wifi >/dev/null 2>&1 || true
connmanctl scan wifi >/dev/null 2>&1 || true
sleep 4
if ! scan_has_ssid; then
    connmanctl disable wifi >/dev/null 2>&1; sleep 3
    connmanctl enable wifi  >/dev/null 2>&1; sleep 8
    connmanctl scan wifi >/dev/null 2>&1 || true
    sleep 5
fi

# SSID as a D-Bus byte array: "ay <len> <b1> <b2> ..."
SSID_LEN=$(printf '%s' "$SSID" | wc -c)
SSID_BYTES=$(printf '%s' "$SSID" | od -An -tu1 | tr -s ' \n' ' ' | sed 's/ *$//')
SSID_AY="$SSID_LEN$SSID_BYTES"

# wlan0 device object
WDEV=""
for d in $(busctl call $NM /org/freedesktop/NetworkManager $NM GetDevices | tr ' ' '\n' | grep Devices | tr -d '"'); do
    case "$(busctl get-property $NM "$d" $NM.Device Interface)" in *wlan0*) WDEV=$d;; esac
done
[ -n "$WDEV" ] || { echo "wlan0 device not found on NM adapter" >&2; exit 1; }

# access point object whose Ssid matches
AP=""
for p in $(busctl call $NM "$WDEV" $NM.Device.Wireless GetAllAccessPoints | tr ' ' '\n' | grep AccessPoint | tr -d '"'); do
    s=$(busctl get-property $NM "$p" $NM.AccessPoint Ssid 2>/dev/null | sed 's/^ay //')
    [ "$s" = "$SSID_AY" ] && AP=$p
done
[ -n "$AP" ] || { echo "SSID '$SSID' not in scan results (hotspot off? 5GHz only? try 2.4GHz)" >&2; exit 1; }

# unsaved connection carrying the PSK, then activate against that AP
CONN=$(busctl call $NM /org/freedesktop/NetworkManager/Settings $NM.Settings AddConnectionUnsaved 'a{sa{sv}}' 3 \
    connection 2 id s "$SSID" type s 802-11-wireless \
    802-11-wireless 1 ssid ay $SSID_AY \
    802-11-wireless-security 2 key-mgmt s wpa-psk psk s "$PASS" | awk '{print $2}' | tr -d '"')

# 5 GHz hotspots sometimes time out on the first association; retry a few times.
n=0
while [ $n -lt 3 ]; do
    busctl call $NM /org/freedesktop/NetworkManager $NM ActivateConnection ooo "$CONN" "$WDEV" "$AP" >/dev/null 2>&1 || true
    i=0
    while [ $i -lt 12 ]; do
        sleep 2; i=$((i+1))
        if ip -4 -o addr show wlan0 | grep -q inet; then
            echo "connected: $(ip -4 -o addr show wlan0 | awk '{print $4}')"
            exit 0
        fi
    done
    n=$((n+1)); sleep 10
done
echo "association failed after retries; see: journalctl -u connman -u wpa_supplicant -n 30" >&2
exit 1
