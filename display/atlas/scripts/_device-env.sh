# display/atlas/deploy/device.env 를 읽어 배포 변수로 export 한다.
# setup-device.sh 와 deploy-app.sh 가 공통으로 source 한다. 단독 실행하지 않는다.

REPO_ROOT=${ATLAS_REPO_ROOT:-/workspace}
ENV_FILE=${ATLAS_DEVICE_ENV:-$REPO_ROOT/display/atlas/deploy/device.env}

die() { echo "error: $*" >&2; exit 1; }

[ -f "$ENV_FILE" ] || die "장치 설정이 없다: $ENV_FILE (device.env.example 을 device.env 로 복사해 값을 채운다)"

# 값에 공백이 있어도 되도록 source 대신 직접 파싱하고,
# Windows 에서 편집된 CRLF 줄바꿈도 걷어낸다.
while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key=${line%%=*}
    val=${line#*=}
    case "$key" in [A-Za-z_]*) ;; *) continue ;; esac
    val=${val#\"}; val=${val%\"}
    val=${val#\'}; val=${val%\'}
    printf -v "$key" '%s' "$val"
    export "$key"
done < "$ENV_FILE"

: "${ATLAS_DEVICE_ID:?device.env 에 ATLAS_DEVICE_ID 가 없다}"
: "${ATLAS_DEVICE_IP:?device.env 에 ATLAS_DEVICE_IP 가 없다}"

ATLAS_DEVICE_LABEL=${ATLAS_DEVICE_LABEL:-$ATLAS_DEVICE_ID}
ATLAS_SSH_USER=${ATLAS_SSH_USER:-root}
ATLAS_SSH_PORT=${ATLAS_SSH_PORT:-22}
ATLAS_SSH_KEY=${ATLAS_SSH_KEY:-}
ATLAS_APP_DIR=${ATLAS_APP_DIR:-display/atlas/app}
ATLAS_HUB_URL=${ATLAS_HUB_URL:-}
ATLAS_BUILD_MODE=${ATLAS_BUILD_MODE:-release}

export ATLAS_DEVICE_ID ATLAS_DEVICE_LABEL ATLAS_DEVICE_IP
export ATLAS_SSH_USER ATLAS_SSH_PORT ATLAS_SSH_KEY
export ATLAS_APP_DIR ATLAS_HUB_URL ATLAS_BUILD_MODE

case "$ATLAS_DEVICE_ID" in
    *[!A-Za-z0-9_]*) die "ATLAS_DEVICE_ID 는 영숫자와 밑줄만 쓸 수 있다: $ATLAS_DEVICE_ID" ;;
esac
