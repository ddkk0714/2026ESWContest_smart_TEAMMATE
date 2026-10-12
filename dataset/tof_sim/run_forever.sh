#!/usr/bin/env bash
# Linux version of run_forever.ps1: keep train.py running for HOURS, resuming after crashes.
#
#   bash run_forever.sh l8_2weeks 336            # run name, hours
#   WORKERS=30 bash run_forever.sh l8_2weeks 336 # generator processes (default: cores - 2)
#
# On a shared server start it inside tmux/screen, or with nohup:
#   nohup bash run_forever.sh l8_2weeks 336 > runs/l8_2weeks.out 2>&1 &
set -uo pipefail
cd "$(dirname "$0")"
RUN="${1:-l8_2weeks}"
HOURS="${2:-336}"
CORES="$(nproc)"
WORKERS="${WORKERS:-$(( CORES > 3 ? CORES - 2 : 1 ))}"
mkdir -p "runs/$RUN"
deadline=$(( $(date +%s) + ${HOURS%.*} * 3600 ))

while [ "$(date +%s)" -lt "$deadline" ]; do
    left=$(awk -v d="$deadline" -v n="$(date +%s)" 'BEGIN { printf "%.3f", (d - n) / 3600 }')
    .venv/bin/python -u train.py --run "$RUN" --resume --hours "$left" --workers "$WORKERS"
    code=$?
    [ "$code" -eq 0 ] && break      # finished, or stopped with Ctrl+C / SIGINT
    echo "$(date -Is) train.py exited with $code, restarting in 30 s" >> "runs/$RUN/restarts.log"
    sleep 30
done
