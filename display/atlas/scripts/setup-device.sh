#!/usr/bin/env bash
# Pi 5(ATLAS) 를 flutter-atlas 배포 대상으로 등록한다. atlas-dev 컨테이너 안에서 실행한다.
#
#   docker compose -f display/atlas/compose.yaml exec atlas-dev \
#     bash /workspace/display/atlas/scripts/setup-device.sh
#
# 설정값은 display/atlas/deploy/device.env 에서 읽는다.
set -euo pipefail

. "$(dirname "$0")/_device-env.sh"

# 1. SSH 키를 컨테이너 홈으로 옮기고 권한을 600 으로 맞춘다.
#    Windows 바인드 마운트는 권한이 열려 있어 ssh 가 마운트된 키를 그대로 거부한다.
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
identity=
if [ -n "$ATLAS_SSH_KEY" ]; then
    src=$ATLAS_SSH_KEY
    case "$src" in /*) ;; *) src=$REPO_ROOT/$src ;; esac
    [ -f "$src" ] || die "SSH 키가 없다: $src"
    identity=$HOME/.ssh/${ATLAS_DEVICE_ID}.key
    install -m 600 "$src" "$identity"
    echo "SSH 키 설치: $identity"
fi

# 2. ~/.ssh/config 에 장치 항목을 쓴다.
#    flutter-atlas 는 custom_devices.json 의 sshIdentityFile 을 '-i ' 인자로 넘기는데
#    공백이 붙어 ssh 에 제대로 전달되지 않는다. 그래서 키는 ssh config 로 지정한다.
cfg=$HOME/.ssh/config
begin="# >>> deskmate:${ATLAS_DEVICE_ID} >>>"
end="# <<< deskmate:${ATLAS_DEVICE_ID} <<<"
touch "$cfg"
chmod 600 "$cfg"
if grep -qF "$begin" "$cfg"; then
    sed -i "\|^${begin}$|,\|^${end}$|d" "$cfg"
fi
{
    echo "$begin"
    echo "Host $ATLAS_DEVICE_IP"
    echo "  HostName $ATLAS_DEVICE_IP"
    echo "  User $ATLAS_SSH_USER"
    echo "  Port $ATLAS_SSH_PORT"
    if [ -n "$identity" ]; then
        echo "  IdentityFile $identity"
        echo "  IdentitiesOnly yes"
    fi
    echo "  StrictHostKeyChecking no"
    echo "  UserKnownHostsFile /dev/null"
    echo "$end"
} >> "$cfg"
echo "ssh config 갱신: $cfg"

# 3. custom_devices.json 에 장치를 등록한다.
#    먼저 list 를 한 번 돌려 flutter-atlas 가 스키마를 포함한 빈 파일을 만들게 한다.
flutter-atlas custom-devices list >/dev/null 2>&1 || true
python3 - <<'PY'
import json, os, pathlib

path = pathlib.Path(os.path.expanduser('~/.config/flutter/custom_devices.json'))
path.parent.mkdir(parents=True, exist_ok=True)

data = {}
if path.exists():
    try:
        data = json.loads(path.read_text(encoding='utf-8') or '{}')
    except json.JSONDecodeError:
        data = {}
if not isinstance(data, dict):
    data = {}

device_id = os.environ['ATLAS_DEVICE_ID']
entry = {
    'id': device_id,
    'label': os.environ['ATLAS_DEVICE_LABEL'],
    'sdkNameAndVersion': 'ATLAS AI Native OS (Raspberry Pi 5)',
    'platform': 'arm64',
    'backend': 'wayland',
    'enabled': True,
    'ipAddress': os.environ['ATLAS_DEVICE_IP'],
    'sshUser': os.environ['ATLAS_SSH_USER'],
    # sshPort 는 문자열이어야 한다. flutter-atlas 가 String 으로 캐스팅한다.
    'sshPort': str(os.environ['ATLAS_SSH_PORT']),
}

devices = [d for d in data.get('custom-devices', [])
           if isinstance(d, dict) and str(d.get('id', '')).lower() != device_id.lower()]
devices.append(entry)
data['custom-devices'] = devices
path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
print(f'장치 등록: {device_id} -> {path}')
PY

# 4. 실제로 붙는지 확인한다. Pi 가 꺼져 있어도 등록 자체는 끝났으므로 중단하지 않는다.
echo
if ssh -o BatchMode=yes -o ConnectTimeout=5 "$ATLAS_SSH_USER@$ATLAS_DEVICE_IP" \
        'echo "SSH OK: $(uname -srm)"'; then
    ssh -o BatchMode=yes -o ConnectTimeout=5 "$ATLAS_SSH_USER@$ATLAS_DEVICE_IP" \
        'command -v abusctl >/dev/null && echo "abusctl OK" || echo "warning: abusctl 이 없다. ATLAS 이미지가 맞는지 확인한다."'
else
    echo "warning: $ATLAS_SSH_USER@$ATLAS_DEVICE_IP 로 SSH 접속 실패. Pi 전원 · 네트워크 · 공개키 등록을 확인한다." >&2
fi

echo
flutter-atlas devices
