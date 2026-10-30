#!/usr/bin/env bash
# run_forever.ps1 의 리눅스판: 정해진 시간 동안 train.py 를 계속 돌리고, 죽으면 이어서 다시 시작한다.
#
#   bash run_forever.sh l8_2weeks 336             # 실행 이름, 시간(h)
#   WORKERS=30 bash run_forever.sh l8_2weeks 336  # 데이터 생성 프로세스 수 (기본: 코어 수 - 2)
#
# 공유 서버에서는 tmux/screen 안에서 돌리거나 nohup 으로:
#   nohup bash run_forever.sh l8_2weeks 336 > runs/l8_2weeks.out 2>&1 &
set -uo pipefail
cd "$(dirname "$0")"
RUN="${1:-l8_2weeks}"
HOURS="${2:-336}"
CORES="$(nproc)"
WORKERS="${WORKERS:-$(( CORES > 3 ? CORES - 2 : 1 ))}"
mkdir -p "runs/$RUN"
deadline=$(( $(date +%s) + ${HOURS%.*} * 3600 ))
echo "실행 '$RUN' — ${HOURS}시간, 생성 프로세스 ${WORKERS}개 (코어 ${CORES}개). 로그: runs/$RUN/train.log"

while [ "$(date +%s)" -lt "$deadline" ]; do
    if [ -f "runs/$RUN/STOP" ]; then
        echo "runs/$RUN/STOP 이 있어서 멈춤 (다시 돌리려면 이 파일을 지우고 실행)"
        break
    fi
    left=$(awk -v d="$deadline" -v n="$(date +%s)" 'BEGIN { printf "%.3f", (d - n) / 3600 }')
    .venv/bin/python -u train.py --run "$RUN" --resume --hours "$left" --workers "$WORKERS"
    code=$?
    [ "$code" -eq 0 ] && break      # 시간이 다 됐거나 Ctrl+C 로 멈춤
    echo "$(date -Is) train.py 종료 코드 $code, 30초 뒤 이어서 다시 시작" | tee -a "runs/$RUN/restarts.log"
    sleep 30
done
echo "실행 '$RUN' 끝."
