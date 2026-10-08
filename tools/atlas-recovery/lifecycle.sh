#!/bin/sh
# ATLAS lifecycle operations. Native services are activated through D-Bus, never run as root.
set -eu
PREFIX=${DESKMATE_RECOVERY_PREFIX:-/data/share/deskmate/recovery}
CONFIG=${DESKMATE_RECOVERY_CONFIG:-$PREFIX/recovery.env}
[ -f "$CONFIG" ] || { echo "missing config: $CONFIG" >&2; exit 1; }
. "$CONFIG"
SERVICE_ID=com.deskmate.hub1
fail() { echo "deskmate recovery: $*" >&2; exit 1; }
for value in "$CHECK_FAILURES" "$RECOVERY_COOLDOWN_SEC" "$SUPERVISE_POLL_SEC"; do
    case "$value" in ''|*[!0-9]*) fail "invalid timer/count";; esac
    [ "$value" -gt 0 ] || fail "nonpositive timer/count"
done
case "$ROLE" in pi4|pi5) ;; *) fail "invalid role";; esac
case "$APP_ID" in com.atlas.app.*) ;; *) fail "invalid app ID";; esac
case "$APP_ID" in *[!A-Za-z0-9_.]*) fail "invalid app ID characters";; esac
if [ "${1:-}" != status ]; then
    mkdir -p "$STATE_DIR"
    chmod 700 "$STATE_DIR"
fi

hub_pid() {
    answer=$(busctl --system call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus GetConnectionUnixProcessID s "$SERVICE_ID" 2>/dev/null) || return 1
    set -- $answer
    [ "${1:-}" = u ] || return 1
    case "${2:-}" in ''|*[!0-9]*) return 1;; esac
    [ "$2" -gt 1 ] || return 1
    printf '%s\n' "$2"
}
owner() {
    BIN=$SERVICE_DIR/deskmate_hub_service
    [ -x "$BIN" ] || fail "missing installed hub: $BIN"
    OWNER=$(ls -ln "$BIN" | awk '{print $3}')
    GROUP=$(ls -ln "$BIN" | awk '{print $4}')
    case "$OWNER:$GROUP" in *[!0-9:]*|:*) fail "invalid installed owner";; esac
    # Numeric identities follow ATLAS reinstallation without hard-coded u0_aNNNN.
}
atomic_update() {
    target=$1
    temporary=$target.deskmate-new
    cat > "$temporary"
    chmod 644 "$temporary"
    if cmp -s "$target" "$temporary"; then
        rm -f "$temporary"
    else
        mv "$temporary" "$target"
        DBUS_CHANGED=1
    fi
}
uart_fix() {
    node=$(readlink -f "$UART_DEV" 2>/dev/null || true)
    [ -n "$node" ] || node=$UART_DEV
    if [ ! -c "$node" ]; then
        echo "UART absent: $node; FSM remains available" >&2
        return 0
    fi
    case "$node" in /dev/ttyS*|/dev/ttyAMA*|/dev/ttyUSB*|/dev/ttyACM*) ;; *) fail "unexpected UART node: $node";; esac
    systemctl stop "serial-getty@$(basename "$node").service" 2>/dev/null || true
    chgrp "$GROUP" "$node"
    chmod 660 "$node"
}
hub_prepare() {
    owner
    mkdir -p "$DBUS_DIR/system-services" "$DBUS_DIR/system.d"
    DBUS_CHANGED=0
    atomic_update "$DBUS_DIR/system-services/$SERVICE_ID.service" <<EOF
[D-BUS Service]
Name=$SERVICE_ID
Exec=$BIN
User=$OWNER
Group=$GROUP
EOF
    atomic_update "$DBUS_DIR/system.d/$SERVICE_ID.conf" <<EOF
<busconfig>
  <policy user="root"><allow own="$SERVICE_ID"/><allow send_destination="$SERVICE_ID"/><allow receive_sender="$SERVICE_ID"/></policy>
  <policy user="$OWNER"><allow own="$SERVICE_ID"/><allow send_destination="$SERVICE_ID"/><allow receive_sender="$SERVICE_ID"/></policy>
  <policy context="default"><allow send_destination="$SERVICE_ID"/><allow receive_sender="$SERVICE_ID"/></policy>
</busconfig>
EOF
    if [ ! -f "$SERVICE_DIR/hub.env" ]; then
        umask 027
        cat > "$SERVICE_DIR/hub.env" <<EOF
DESKMATE_HUB_MODE=live
DESKMATE_UART_DEV=$UART_DEV
DESKMATE_UART_BAUD=115200
DESKMATE_MQTT_HOST=$MQTT_HOST
DESKMATE_MQTT_PORT=$MQTT_PORT
EOF
    fi
    # Preserve existing connection settings, including custom UART path.
    configured_uart=$(sed -n 's/^DESKMATE_UART_DEV=//p' "$SERVICE_DIR/hub.env" | tail -n 1)
    [ -z "$configured_uart" ] || UART_DEV=$configured_uart
    chown "$OWNER:$GROUP" "$SERVICE_DIR/hub.env"
    chmod 640 "$SERVICE_DIR/hub.env"
    uart_fix
    [ "$DBUS_CHANGED" -eq 0 ] || systemctl reload dbus
}
app_running() {
    apps=$(abusctl call com.atlas.AppManager1 ListRunningApps) || return 2
    escaped=$(printf '%s' "$APP_ID" | sed 's/\./\\./g')
    printf '%s\n' "$apps" | grep -Eq "(^|[[:space:]\",])$escaped($|[[:space:]\",])"
}
recover_unit() {
    unit=$1
    now=$(date +%s)
    stamp=$STATE_DIR/$unit.last-restart
    last=0
    [ ! -f "$stamp" ] || last=$(cat "$stamp")
    case "$last" in ''|*[!0-9]*) last=0;; esac
    if [ "$now" -ge "$last" ] && [ $((now-last)) -lt "$RECOVERY_COOLDOWN_SEC" ]; then
        echo "cooldown: $unit"
        return 0
    fi
    echo "$now" > "$stamp"
    echo "recovering: $unit"
    # Do not reset systemd failure limits, or defeat an explicit stop during maintenance.
    systemctl try-restart "$unit"
}
watch() {
    if [ "$ROLE" = pi5 ]; then
        if app_running; then
            echo "display running"
        else
            recover_unit deskmate-display.service
        fi
        return
    fi
    owner
    configured_uart=$(sed -n 's/^DESKMATE_UART_DEV=//p' "$SERVICE_DIR/hub.env" 2>/dev/null | tail -n 1)
    [ -z "$configured_uart" ] || UART_DEV=$configured_uart
    uart_fix
    # MQTT disconnect does not justify restarting a functioning FSM.
    systemctl is-active --quiet deskmate-broker.service || echo "broker inactive; inspect restart limit" >&2
    if ! systemctl is-active --quiet deskmate-hub.service; then
        echo "hub inactive; inspect restart limit" >&2
        return
    fi
    state=$(wget -T 5 -q -O - "$HUB_URL/api/state" 2>/dev/null || true)
    # FSM envelopes emit boot_id and seq before data; take their first occurrences.
    boot=$(printf '%s' "$state" | grep -o '"boot_id"[[:space:]]*:[[:space:]]*"[^" ]*"' | head -n 1 | sed 's/.*:[[:space:]]*"//;s/"$//')
    seq=$(printf '%s' "$state" | grep -o '"seq"[[:space:]]*:[[:space:]]*[0-9][0-9]*' | head -n 1 | sed 's/.*:[[:space:]]*//')
    token=$boot:$seq
    previous=$(cat "$STATE_DIR/hub-last" 2>/dev/null || true)
    failed=$(cat "$STATE_DIR/hub-failures" 2>/dev/null || echo 0)
    case "$failed" in ''|*[!0-9]*) failed=0;; esac
    if [ -n "$boot" ] && [ -n "$seq" ] && printf '%s' "$state" | grep -Eq '"source"[[:space:]]*:[[:space:]]*"fsm"' && [ "$token" != "$previous" ]; then
        failed=0
    else
        failed=$((failed+1))
    fi
    printf '%s\n' "$token" > "$STATE_DIR/hub-last"
    printf '%s\n' "$failed" > "$STATE_DIR/hub-failures"
    echo "FSM progress failures=$failed"
    if [ "$failed" -ge "$CHECK_FAILURES" ]; then
        recover_unit deskmate-hub.service
        echo 0 > "$STATE_DIR/hub-failures"
    fi
}
case "${1:-}" in
hub-prepare) [ "$ROLE" = pi4 ] || fail "wrong role"; hub_prepare;;
hub-supervise)
    [ "$ROLE" = pi4 ] || fail "wrong role"
    hub_prepare
    busctl --system call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus StartServiceByName su "$SERVICE_ID" 0
    pid=$(hub_pid) || fail "D-Bus activation has no owner"
    trap 'current=$(hub_pid || true); [ "$current" != "$pid" ] || kill "$pid"; exit 0' TERM INT
    while [ "$(hub_pid || true)" = "$pid" ]; do sleep "$SUPERVISE_POLL_SEC"; done
    fail "hub D-Bus owner exited or changed";;
hub-stop) pid=$(hub_pid || true); [ -z "$pid" ] || kill "$pid";;
broker-run) [ "$ROLE" = pi4 ] || fail "wrong role"; exec "$BROKER_DIR/mosquitto" -c "$BROKER_DIR/mosquitto.conf";;
display-start)
    [ "$ROLE" = pi5 ] || fail "wrong role"
    if app_running; then exit 0; fi
    abusctl call com.atlas.AppManager1 Start "$APP_ID"
    app_running || fail "app launch not confirmed";;
display-stop) abusctl call com.atlas.AppManager1 Stop "$APP_ID";;
watch) watch;;
status)
    systemctl status 'deskmate-*' --no-pager || true
    if [ "$ROLE" = pi4 ]; then
        echo "hub_owner_pid=$(hub_pid || true)"
        echo "uart=$UART_DEV"; ls -l "$UART_DEV" 2>/dev/null || true
        wget -T 5 -q -O - "$HUB_URL/health" 2>/dev/null || true
        echo; cat "$STATE_DIR/hub-failures" 2>/dev/null || true
    else
        abusctl call com.atlas.AppManager1 ListRunningApps
    fi;;
*) fail "usage: lifecycle.sh hub-prepare|hub-supervise|hub-stop|broker-run|display-start|display-stop|watch|status";;
esac
