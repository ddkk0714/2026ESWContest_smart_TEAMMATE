#!/bin/sh
# Install only into a verified writable systemd load path; never remount the OS.
set -eu
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PREFIX=${DESKMATE_RECOVERY_PREFIX:-/data/share/deskmate/recovery}
ROLE=${1:-}
[ "$ROLE" = pi4 ] || [ "$ROLE" = pi5 ] || { echo "usage: install.sh pi4|pi5 [--unit-dir PATH] [--runtime-only] [--no-start] [--remove]" >&2; exit 1; }
shift
RUNTIME=0
NO_START=0
REMOVE=0
UNIT_DIR=""
while [ $# -gt 0 ]; do
    case "$1" in
    --unit-dir) shift; [ $# -gt 0 ] || exit 1; UNIT_DIR=$1;;
    --runtime-only) RUNTIME=1;;
    --no-start) NO_START=1;;
    --remove) REMOVE=1;;
    *) echo "unknown option: $1" >&2; exit 1;;
    esac
    shift
done
[ "$(id -u)" = 0 ] || { echo "root required" >&2; exit 1; }
case "$PREFIX" in /*) ;; *) echo "absolute prefix required" >&2; exit 1;; esac
case "$PREFIX" in *[!A-Za-z0-9_./-]*|*/../*|*/..|/) echo "unsupported prefix" >&2; exit 1;; esac
LOAD_PATHS=$(systemctl show --property=UnitPath --value)
[ -n "$LOAD_PATHS" ] || { echo "cannot discover systemd UnitPath" >&2; exit 1; }
if [ -z "$UNIT_DIR" ]; then
    if [ "$RUNTIME" -eq 1 ]; then
        UNIT_DIR=/run/systemd/system
    else
        for candidate in $LOAD_PATHS; do
            case "$candidate" in /run/*) continue;; esac
            if [ -d "$candidate" ] && [ -w "$candidate" ]; then UNIT_DIR=$candidate; break; fi
        done
    fi
fi
[ -n "$UNIT_DIR" ] || { echo "no writable persistent unit path; boot registration unsupported on this image. Use --runtime-only for a reboot-volatile test." >&2; exit 1; }
case " $LOAD_PATHS " in *" $UNIT_DIR "*) ;; *) echo "unit-dir is outside systemd UnitPath" >&2; exit 1;; esac
case "$UNIT_DIR" in /run/*) [ "$RUNTIME" -eq 1 ] || { echo "volatile path requires --runtime-only" >&2; exit 1; };; esac
[ -d "$UNIT_DIR" ] || { echo "unit-dir missing" >&2; exit 1; }
[ -w "$UNIT_DIR" ] || { echo "unit-dir is read-only" >&2; exit 1; }
# Actual write probe catches a read-only mount even when permission bits allow root.
probe=$UNIT_DIR/.deskmate-write-probe.$$
(umask 077; : > "$probe") || { echo "unit-dir write failed" >&2; exit 1; }
rm -f "$probe"
if [ "$ROLE" = pi4 ]; then
    SERVICES="deskmate-broker.service deskmate-hub.service"
else
    SERVICES="deskmate-display.service"
fi
UNITS="$SERVICES deskmate-watch.service deskmate-watch.timer"
for unit in $UNITS; do
    if [ -e "$UNIT_DIR/$unit" ] && ! grep -q '^# DESKMATE managed$' "$UNIT_DIR/$unit"; then
        echo "refusing to replace unmanaged unit: $unit" >&2; exit 1
    fi
done
if [ "$REMOVE" -eq 1 ]; then
    systemctl stop deskmate-watch.timer deskmate-watch.service $SERVICES
    for unit in $UNITS; do
        rm -f "$UNIT_DIR/multi-user.target.wants/$unit" "$UNIT_DIR/timers.target.wants/$unit" "$UNIT_DIR/$unit"
    done
    systemctl daemon-reload
    echo "removed managed units; config and data retained"
    exit 0
fi
# Preflight before installing any units. Existing trusted config is preserved.
[ -f "$HERE/lifecycle.sh" ] && [ -f "$HERE/recovery.env.example" ] || exit 1
for unit in $UNITS; do
    [ -f "$HERE/$unit" ] || { echo "missing unit template: $unit" >&2; exit 1; }
done
mkdir -p "$PREFIX"
if [ ! -f "$PREFIX/recovery.env" ]; then
    sed "s/^ROLE=.*/ROLE=$ROLE/" "$HERE/recovery.env.example" > "$PREFIX/recovery.env"
fi
CONFIG_ROLE=$(sed -n 's/^ROLE=//p' "$PREFIX/recovery.env" | tail -n 1)
[ "$CONFIG_ROLE" = "$ROLE" ] || { echo "existing config role differs" >&2; exit 1; }
chown root:root "$PREFIX/recovery.env"
chmod 600 "$PREFIX/recovery.env"
if [ "$HERE/lifecycle.sh" != "$PREFIX/lifecycle.sh" ]; then cp "$HERE/lifecycle.sh" "$PREFIX/lifecycle.sh"; fi
chmod 755 "$PREFIX/lifecycle.sh"
for unit in $UNITS; do
    sed "s|@PREFIX@|$PREFIX|g" "$HERE/$unit" > "$UNIT_DIR/$unit"
    chmod 644 "$UNIT_DIR/$unit"
done
mkdir -p "$UNIT_DIR/multi-user.target.wants" "$UNIT_DIR/timers.target.wants"
for unit in $SERVICES; do ln -sf "../$unit" "$UNIT_DIR/multi-user.target.wants/$unit"; done
ln -sf ../deskmate-watch.timer "$UNIT_DIR/timers.target.wants/deskmate-watch.timer"
systemctl daemon-reload
if [ "$NO_START" -eq 0 ]; then systemctl start $SERVICES deskmate-watch.timer; fi
if [ "$RUNTIME" -eq 1 ]; then
    echo "installed runtime-only; DOES NOT survive reboot"
else
    echo "installed persistent units into $UNIT_DIR; physical reboot verification still required"
fi
