# Pi 5 배포

경로가 두 개다. 대부분은 **A** 면 된다.

| | 필요한 것 | 쓰는 때 |
|---|---|---|
| **A. 받아서 바로 설치** | `ssh` · `scp` 만 | 남이 구워 준 `.ipk` 를 보드에 올릴 때 |
| **B. 직접 빌드해서 설치** | Docker · Atlas SDK · 레포 | 코드를 고쳐서 올릴 때 |

---

# A. 받아서 바로 설치 — 개발 환경 불필요

Releases 에서 `.ipk` 와 설치 스크립트를 받아 같은 폴더에 두고 실행한다.
Flutter·Docker·Atlas SDK 아무것도 필요 없다. **`ssh` 와 `scp` 만 있으면 된다.**

Windows PowerShell:

```powershell
.\install-to-pi.ps1 -Ip 192.168.0.100
```

macOS · Linux · WSL:

```bash
bash install-to-pi.sh 192.168.0.100
```

보드 IP 만 주면 업로드 → 이전 버전 제거 → 설치 → 실행까지 한 번에 한다.
앱 ID 는 `.ipk` 파일 이름에서 읽는다.

| 자주 쓰는 옵션 | PowerShell | bash |
|---|---|---|
| 설치만 하고 실행 안 함 | `-NoStart` | `NO_START=1` |
| SSH 키 지정 | `-SshKey <경로>` | `SSH_KEY=<경로>` |
| 계정 · 포트 | `-BoardUser` · `-Port` | `BOARD_USER=` · `BOARD_PORT=` |

`ssh` 가 없다면 Windows 는 *설정 > 시스템 > 선택적 기능* 에서 **OpenSSH 클라이언트**를 켜면 된다.

**보드 IP 를 모를 때**는 공유기 DHCP 목록에서 보드 MAC 을 찾거나, 보드 콘솔에서 `ip -br addr` 를 친다.
DHCP 주소는 재부팅하면 바뀔 수 있다.

**안 될 때**

| 증상 | 확인 |
|---|---|
| `SSH 로 못 붙습니다` | 보드 전원 · IP. `ping <IP>` 부터 |
| `전송이 깨졌습니다` | 다시 실행. 용량이 30MB 라 무선이 불안하면 끊긴다 |
| 설치는 됐는데 실행 확인 안 됨 | 스크립트가 출력하는 `journalctl` 명령으로 로그 확인 |

---

# B. 직접 빌드해서 설치

[../README.md](../README.md) 의 **§4 Pi 5 를 SSH 장치로 등록**과 **§5 설치·실행**을 스크립트로 고정한 것이다.
수동 절차와 결과는 같고, 다음 두 가지를 해결한다.

- `flutter-atlas custom-devices add` 는 대화형이라 사람마다 입력이 갈린다 → `device.env` 한 파일로 고정한다
- 컨테이너를 재생성하면 장치 등록이 사라진다 → `compose.yaml` 이 `/root/.config/flutter` 와 `/root/.ssh` 를 named volume 으로 유지한다

## 대상 앱

| 폴더 | 앱 ID |
|---|---|
| `display/atlas/ui_env_app` | `atlas/meta/appinfo.json` 의 `id` |

앱 ID 는 `appinfo.json` 이 정하고, 빌드 산출물 이름(`<앱 ID>.ipk`)과 설치 대상이 모두 이 값을 따른다.
ID 가 다른 빌드는 보드에 **나란히 설치**되므로, 기존 앱을 지우지 않고 새 버전을 옆에 올려 비교할 수 있다.
그때는 `appinfo.json` 의 `id` · `name` · `entry` 와 `atlas/CMakeLists.txt` 의 `ATLAS_APP_ID` · `BINARY_NAME`
을 **함께** 바꾼다. `BINARY_NAME` 이 `entry` 첫 토큰과 다르면 설치는 되지만 실행되지 않는다.

기본 대상은 `device.env` 의 `ATLAS_APP_DIR` 이고, 한 번만 바꿔 쓸 때는 `-AppDir` 로 덮어쓴다.

```powershell
.\display\atlas\scripts\pi-deploy.ps1 -AppDir display/atlas/ui_env_app
```

## 파일

| 경로 | 역할 |
|---|---|
| `deploy/install-to-pi.sh` · `deploy/install-to-pi.ps1` | **A 경로.** 빌드된 `.ipk` 를 ssh 만으로 설치 |
| `deploy/device.env.example` | 장치 · 앱 · Hub URL 설정 서식. `device.env` 로 복사해 쓴다 |
| `deploy/device.env` | 개발자별 실제 값. `.gitignore` 대상 |
| `deploy/ssh/` | Pi 5 접속용 개인키를 두는 곳. `.gitignore` 대상 |
| `scripts/setup-device.sh` | 컨테이너 안에서 SSH 키 · ssh config · `custom_devices.json` 을 구성한다 |
| `scripts/deploy-app.sh` | 테스트 → ipk 빌드 → 설치 · 실행 |
| `scripts/pi-setup.ps1` · `scripts/pi-deploy.ps1` | 호스트 PowerShell 에서 위 두 스크립트를 부르는 래퍼 |

## 최초 1회

```powershell
Copy-Item display\atlas\deploy\device.env.example display\atlas\deploy\device.env
# device.env 에 Pi 5 IP · SSH 계정 · 개인키 경로 · Hub URL 을 채운다

docker compose -f display/atlas/compose.yaml up -d
.\display\atlas\scripts\pi-setup.ps1
```

`pi-setup.ps1` 은 컨테이너 안에서 다음을 한다.

1. `device.env` 의 개인키를 `~/.ssh/<device_id>.key` 로 복사하고 권한을 600 으로 맞춘다
   (Windows 바인드 마운트는 권한이 열려 있어 ssh 가 마운트된 키를 그대로 거부한다)
2. `~/.ssh/config` 에 장치 항목을 쓴다
3. `~/.config/flutter/custom_devices.json` 에 장치를 등록한다
4. SSH 접속과 `abusctl` 존재를 확인하고 `flutter-atlas devices` 를 출력한다

> `custom_devices.json` 의 `sshIdentityFile` 은 쓰지 않는다. flutter-atlas 가 이 값을 `'-i '` 로 넘기면서
> 인자 끝에 공백이 붙어 ssh 에 키 경로가 제대로 전달되지 않는다
> (`flutter-elinux-atlas/lib/atlas_remote_device_config.dart`). 그래서 키는 ssh config 로 지정한다.

## 반복 작업

```powershell
.\display\atlas\scripts\pi-deploy.ps1                      # 테스트 + release 빌드 + 설치 + 실행
.\display\atlas\scripts\pi-deploy.ps1 -Mode debug          # hot reload · DevTools
.\display\atlas\scripts\pi-deploy.ps1 -Action install      # 설치까지만
.\display\atlas\scripts\pi-deploy.ps1 -Action build        # ipk 만 생성
.\display\atlas\scripts\pi-deploy.ps1 -Action uninstall    # 장치에서 제거
```

컨테이너 셸에서 직접 실행해도 된다.

```bash
bash /workspace/display/atlas/scripts/deploy-app.sh --install --mode release
```

모드를 바꿔 빌드하면 이전 모드 산출물이 ipk 에 섞이므로 자동으로 `flutter-atlas clean` 을 먼저 돌린다
(`-Clean` 으로 강제할 수도 있다). 생성 경로는 다음과 같다.

```
<앱 폴더>/build/atlas/arm64/<mode>/ipk/<앱 ID>.ipk
```

## Docker 가 WSL 안에만 있는 PC

`pi-setup.ps1` · `pi-deploy.ps1` 은 호스트 PATH 의 `docker` 를 부른다. Docker Desktop 없이
WSL 안에만 Docker Engine 이 있는 PC에서는 WSL 셸에서 직접 실행한다.

```bash
cd /mnt/c/school/26summer/swcontest/deskmate-app
docker compose -f display/atlas/compose.yaml up -d
docker compose -f display/atlas/compose.yaml exec atlas-dev bash /workspace/display/atlas/scripts/setup-device.sh
docker compose -f display/atlas/compose.yaml exec atlas-dev bash /workspace/display/atlas/scripts/deploy-app.sh --install
```

이미 만들어 둔 Atlas 이미지가 있으면 compose 가 다시 빌드하지 않도록 태그만 붙여 재사용한다.

```bash
docker tag atlas-dev:latest deskmate-atlas-dev:local
```

## 문제 해결

| 증상 | 확인할 것 |
|---|---|
| `flutter-atlas devices` 에 장치가 없다 | `pi-setup.ps1` 재실행. `ATLAS_DEVICE_ID` 는 영숫자와 밑줄만 쓴다 |
| SSH 접속 실패 | Pi 전원 · IP · `authorized_keys` 확인. 컨테이너에서 `ssh <user>@<ip> uname -a` 로 직접 확인한다 |
| `UNPROTECTED PRIVATE KEY FILE` | 키를 직접 마운트해 쓰지 말고 `pi-setup.ps1` 이 복사한 `~/.ssh/<device_id>.key` 를 쓴다 |
| `abusctl` 없음 경고 | Pi 가 ATLAS 이미지가 아니다. 일반 Raspberry Pi OS 에는 PackageManager 가 없어 ipk 설치가 되지 않는다 |
| 앱 폴더를 찾지 못한다 | 앱은 레포 안에 있어야 한다. `ATLAS_APP_DIR` 은 레포 루트 기준 상대 경로다 |
| `bad interpreter: No such file` | `.sh` 가 CRLF 로 체크아웃됐다. 레포 루트 `.gitattributes` 적용 여부를 확인한다 |
