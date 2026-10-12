#!/bin/bash
# Slurm 으로 tof_sim 학습을 돌린다. 작업 하나가 끝나면(시간 제한) 전체 마감 전까지
# 자기 자신을 다시 제출해서 이어 돌린다 — 작업당 시간 제한이 2주보다 짧아도 된다.
#
# 제출 (tof_sim 폴더에서):
#   sbatch slurm_train.sh
#   sbatch --export=ALL,RUN=mj_trial,TOTAL_H=336 slurm_train.sh   # 이어서 할 모델·전체 시간 지정
#
# 상태 보기:   squeue -u $USER
# 로그 보기:   tail -f runs/<RUN>/train.log
# 완전히 멈추기: scancel <작업번호>  그리고  touch runs/<RUN>/STOP   (다시 제출하지 않게)
#
# ▼ 서버 안내에 맞춰 고칠 곳 ─────────────────────────────────────────────
#   --partition : UBAI 파티션 이름 (주석 해제). GPU 는 필요 없다(병목은 CPU 데이터 생성).
#   --cpus-per-task : 받을 수 있는 만큼. 생성 프로세스 = 이 값 - 2
#   --time : 작업당 최대 시간. 바꾸면 아래 JOB_H 도 그보다 조금 짧게
#SBATCH --job-name=tof_sim
#SBATCH --cpus-per-task=32
#SBATCH --mem=16G
#SBATCH --time=2-00:00:00
#SBATCH --output=runs/slurm-%j.out
##SBATCH --partition=<파티션 이름>

set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"

RUN="${RUN:-l8_2weeks}"      # 실행 이름 (runs/<RUN>/ 에 저장, 같은 이름이면 이어서)
TOTAL_H="${TOTAL_H:-336}"    # 전체 학습 시간 (2주 = 336)
JOB_H="${JOB_H:-47.5}"       # 작업 하나에서 돌릴 시간. --time 보다 30분쯤 짧게(저장할 여유)
MAX_FAILS=5                  # 연속으로 이만큼 죽으면 다시 제출하지 않음

mkdir -p "runs/$RUN"
if [ -f "runs/$RUN/STOP" ]; then
    echo "runs/$RUN/STOP 이 있어서 시작하지 않음"
    exit 0
fi

# 전체 마감 시각은 처음 제출할 때 한 번만 정한다
DL="runs/$RUN/deadline"
[ -f "$DL" ] || echo $(( $(date +%s) + ${TOTAL_H%.*} * 3600 )) > "$DL"
deadline=$(cat "$DL")
left=$(awk -v d="$deadline" -v n="$(date +%s)" 'BEGIN { printf "%.3f", (d - n) / 3600 }')
if awk -v l="$left" 'BEGIN { exit !(l <= 0.05) }'; then
    echo "실행 '$RUN' 전체 시간이 끝남"
    exit 0
fi
hours=$(awk -v l="$left" -v j="$JOB_H" 'BEGIN { printf "%.3f", (l < j ? l : j) }')

CPUS="${SLURM_CPUS_PER_TASK:-4}"
WORKERS=$(( CPUS > 3 ? CPUS - 2 : 1 ))
echo "$(date -Is) 작업 ${SLURM_JOB_ID:-로컬} — 실행 '$RUN', 이번 작업 ${hours}시간 (전체 남은 ${left}시간), 생성 프로세스 ${WORKERS}개"

.venv/bin/python -u train.py --run "$RUN" --resume --hours "$hours" --workers "$WORKERS"
code=$?

# 연속 실패 횟수 (정상 종료면 0 으로)
FAILS="runs/$RUN/fails"
if [ "$code" -eq 0 ]; then
    echo 0 > "$FAILS"
else
    n=$(( $(cat "$FAILS" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$FAILS"
    echo "$(date -Is) train.py 종료 코드 $code (연속 실패 ${n}번)" | tee -a "runs/$RUN/restarts.log"
    if [ "$n" -ge "$MAX_FAILS" ]; then
        echo "연속 ${MAX_FAILS}번 실패해서 다시 제출하지 않음. runs/$RUN/train.log 확인"
        exit 1
    fi
fi

# 아직 마감 전이고 멈추라는 표시가 없으면 다음 작업을 제출해 이어 돌린다
if [ ! -f "runs/$RUN/STOP" ] && [ "$(date +%s)" -lt $(( deadline - 600 )) ]; then
    sbatch --export=ALL,RUN="$RUN",TOTAL_H="$TOTAL_H",JOB_H="$JOB_H" slurm_train.sh
else
    echo "실행 '$RUN' 끝."
fi
