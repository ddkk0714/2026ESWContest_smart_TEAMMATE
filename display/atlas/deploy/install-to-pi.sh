#!/usr/bin/env bash
#
# 빌드된 .ipk 를 Pi 5(ATLAS)에 설치한다. 개발 환경이 필요 없다 — ssh · scp 만 있으면 된다.
#
#   bash install-to-pi.sh <보드IP> [ipk파일]
#   bash install-to-pi.sh 192.168.0.100
#   bash install-to-pi.sh 192.168.0.100 com.atlas.app.deskmate_display_ui_test.ipk
#
# 옵션(환경변수):
#   BOARD_USER   SSH 계정 (기본 root)
#   BOARD_PORT   SSH 포트 (기본 22)
#   SSH_KEY      개인키 경로 (기본: 없음 — ATLAS 기본 이미지는 키 없이 붙는다)
#   NO_START=1   설치만 하고 실행하지 않는다
#
# .ipk 를 생략하면 스크립트와 같은 폴더에서 찾는다. 여러 개면 골라 달라고 한다.
# 앱 ID 는 파일 이름에서 가져온다(flutter-atlas 가 "<앱ID>.ipk" 로 굽는다).
set -uo pipefail

die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
ok()  { printf '\033[32m✓\033[0m %s\n' "$*"; }
step(){ printf '\n\033[1m%s\033[0m\n' "$*"; }

IP="${1:-}"
IPK="${2:-}"
USER_ON_BOARD="${BOARD_USER:-root}"
PORT="${BOARD_PORT:-22}"

[ -n "$IP" ] || die "보드 IP 가 필요합니다.
    예) bash install-to-pi.sh 192.168.0.100
    IP 를 모르면 공유기 DHCP 목록이나 보드 콘솔에서 'ip -br addr' 로 확인하세요."

command -v ssh >/dev/null || die "ssh 가 없습니다. Windows 10/11 은 '설정 > 앱 > 선택적 기능' 에서 OpenSSH 클라이언트를 켜세요."
command -v scp >/dev/null || die "scp 가 없습니다. OpenSSH 클라이언트를 설치하세요."

# ---- .ipk 찾기 -------------------------------------------------------------
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ -z "$IPK" ]; then
    # shellcheck disable=SC2207
    FOUND=($(find "$HERE" -maxdepth 1 -name '*.ipk' | sort))
    case ${#FOUND[@]} in
        0) die "이 폴더에 .ipk 가 없습니다: $HERE
    릴리스에서 받은 .ipk 를 이 스크립트 옆에 두거나, 두 번째 인자로 경로를 주세요." ;;
        1) IPK="${FOUND[0]}" ;;
        *) printf '여러 개를 찾았습니다. 두 번째 인자로 하나를 지정하세요:\n' >&2
           printf '  %s\n' "${FOUND[@]##*/}" >&2; exit 1 ;;
    esac
fi
[ -f "$IPK" ] || die ".ipk 를 찾을 수 없습니다: $IPK"

BASE=$(basename "$IPK")
APP_ID="${APP_ID:-${BASE%.ipk}}"
case "$APP_ID" in
    com.atlas.app.*) ;;
    *) die "앱 ID 를 파일 이름에서 못 읽었습니다: $BASE
    'com.atlas.app.<이름>.ipk' 형식이어야 합니다. 다르면 APP_ID 환경변수로 직접 주세요." ;;
esac

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10 -p "$PORT")
SCP_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10 -P "$PORT")
if [ -n "${SSH_KEY:-}" ]; then
    [ -f "$SSH_KEY" ] || die "SSH 키가 없습니다: $SSH_KEY"
    SSH_OPTS+=(-i "$SSH_KEY"); SCP_OPTS+=(-i "$SSH_KEY")
fi
TARGET="$USER_ON_BOARD@$IP"
sshx() { ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }

printf '앱   : %s\n보드 : %s:%s\n파일 : %s (%s bytes)\n' \
    "$APP_ID" "$TARGET" "$PORT" "$BASE" "$(wc -c < "$IPK" | tr -d ' ')"

# ---- 1. 연결 ---------------------------------------------------------------
step "1/5 보드 연결"
sshx true 2>/dev/null || die "SSH 로 못 붙습니다: $TARGET:$PORT
    · 보드 전원과 IP 를 확인하세요 (DHCP 주소는 바뀝니다)
    · 키 인증이 필요하면 SSH_KEY=<키경로> 를 주세요"
sshx 'command -v abusctl >/dev/null' || die "보드에 abusctl 이 없습니다. ATLAS 이미지가 맞는지 확인하세요."
ok "연결됨 ($(sshx 'uname -r' 2>/dev/null))"

# ---- 2. 업로드 -------------------------------------------------------------
step "2/5 업로드"
# /tmp 는 재부팅 때 비워지므로 매번 만든다.
sshx 'mkdir -p /tmp/download' || die "보드에 /tmp/download 를 만들지 못했습니다."
scp "${SCP_OPTS[@]}" "$IPK" "$TARGET:/tmp/download/" || die "전송 실패. 용량과 네트워크를 확인하세요."
REMOTE="/tmp/download/$BASE"
# 전송이 깨지면 설치가 이상하게 실패하므로 크기를 맞춰 본다.
LOCAL_SIZE=$(wc -c < "$IPK" | tr -d ' ')
REMOTE_SIZE=$(sshx "wc -c < '$REMOTE'" 2>/dev/null | tr -d ' \r')
[ "$LOCAL_SIZE" = "$REMOTE_SIZE" ] || die "전송이 깨졌습니다 (로컬 $LOCAL_SIZE / 보드 $REMOTE_SIZE). 다시 실행하세요."
ok "전송 완료 · 크기 일치"

# ---- 3. 기존 설치 제거 -----------------------------------------------------
step "3/5 기존 설치 정리"
# 같은 ID·같은 버전이 이미 있으면 설치가 거부된다. 먼저 멈추고 지운다.
if sshx "abusctl call com.atlas.AppManager1 ListAllApps 2>/dev/null | grep -q '$APP_ID'"; then
    sshx "abusctl call com.atlas.AppManager1 Stop \"$APP_ID\"" >/dev/null 2>&1 || true
    sshx "abusctl call com.atlas.PackageManager1 Remove \"$APP_ID\"" >/dev/null 2>&1 || true
    ok "이전 버전 제거"
else
    ok "처음 설치"
fi

# ---- 4. 설치 ---------------------------------------------------------------
step "4/5 설치"
sshx "abusctl call com.atlas.PackageManager1 Install \"$APP_ID\" \"$REMOTE\"" >/dev/null \
    || die "설치 실패. 보드에서 직접 확인하세요:
    ssh $TARGET 'abusctl call com.atlas.PackageManager1 Install \"$APP_ID\" \"$REMOTE\"'"
sshx "abusctl call com.atlas.AppManager1 ListAllApps 2>/dev/null | grep -q '$APP_ID'" \
    || die "설치는 끝났는데 앱 목록에 없습니다. 위 명령을 직접 실행해 출력을 보세요."
ok "설치됨"

# ---- 5. 실행 ---------------------------------------------------------------
if [ "${NO_START:-}" = "1" ]; then
    step "5/5 실행 생략 (NO_START=1)"
    ok "끝났습니다. 실행: ssh $TARGET 'abusctl call com.atlas.AppManager1 Start \"$APP_ID\"'"
    exit 0
fi
step "5/5 실행"
sshx "abusctl call com.atlas.AppManager1 Start \"$APP_ID\"" >/dev/null || die "실행 명령이 실패했습니다."
sleep 2
if sshx "abusctl call com.atlas.AppManager1 ListRunningApps 2>/dev/null | grep -q '$APP_ID'"; then
    ok "실행 중입니다. 보드 화면을 보세요."
else
    printf '\033[33m!\033[0m 설치는 됐는데 실행 상태가 확인되지 않습니다. 로그를 보세요:\n'
    printf "    ssh %s 'journalctl -u am.%s.service -n 50 --no-pager'\n" "$TARGET" "$APP_ID"
    exit 1
fi

printf '\n멈추기 : ssh %s '\''abusctl call com.atlas.AppManager1 Stop "%s"'\''\n' "$TARGET" "$APP_ID"
printf '지우기 : ssh %s '\''abusctl call com.atlas.PackageManager1 Remove "%s"'\''\n' "$TARGET" "$APP_ID"
