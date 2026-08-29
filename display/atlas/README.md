# Pi 5 Atlas Docker 개발 환경

이 환경은 Docker 설정과 작업 소스를 모두 이 레포 안에 둔다. 공급사 Atlas SDK·엔진·플러그인·toolchain은 용량과 배포 조건상 Git에 저장하지 않으며, 각 개발자가 제공받은 공식 Atlas 패키지에서 레포 내부 `display/atlas/vendor/`로 준비한다. Docker는 외부 경로를 참조하지 않는다.

## 최초 1회 준비

공식 Atlas 패키지를 확보한 뒤 아래 명령을 저장소 루트에서 실행한다.

```powershell
.\display\atlas\scripts\import-atlas-vendor.ps1 -SourcePath '공식 Atlas 패키지를 푼 폴더'
.\display\atlas\scripts\verify-atlas-vendor.ps1
```

`vendor/`는 `.gitignore` 대상이다. 다른 컴퓨터에서도 동일한 공식 패키지로 위 준비 과정을 한 번 실행하면 같은 Docker 빌드 입력을 사용한다.

## 빌드와 접속

```powershell
docker compose -f display/atlas/compose.yaml build atlas-dev
docker compose -f display/atlas/compose.yaml up -d
docker compose -f display/atlas/compose.yaml exec atlas-dev bash
```

컨테이너의 `/workspace`는 이 레포 루트이며 기본 작업 위치는 `/workspace/display`다. Atlas SDK 환경을 사용하기 전 다음을 실행한다.

```bash
source "$ATLAS_FLUTTER_NDK_ENV"
cd /workspace/display/atlas/app
flutter pub get
flutter test
flutter-atlas build atlas --ipk --release \
  --dart-define=DESKMATE_HUB_URL=http://192.168.0.40:8765
```

`192.168.0.40`은 예시이므로 Pi 4의 실제 고정/예약 IP로 바꾼다. URL을 빼고 빌드하면
Pi 4 없이도 화면 내장 데모가 실행된다. `--ipk`를 붙이면 Pi 5에 설치할
`com.atlas.app.<앱ID>.ipk` 패키지가 만들어진다. 공급사 도구 버전에 따라 옵션이 다르면
컨테이너에서 `flutter-atlas build --help`를 먼저 확인한다. 생성된 SDK·bundle·ipk는 Git에 넣지 않는다.

## Pi 5 설치와 실행

**Docker 컨테이너는 개발 PC에서만 돈다.** Atlas SDK가 x86_64 호스트에서 arm64를 겨냥하는
크로스 툴체인(`atlas-sdk-x86_64-…-generic_arm64-toolchain`)이라 컨테이너는 빌드 전용이다.
Pi 5에는 Docker를 올리지 않는다. 만들어진 `.ipk`는 Pi 5의 AI Native OS에 설치되어
**컨테이너 밖에서 네이티브로** 실행된다. `.ipk`는 Yocto/OpenWrt 계열 opkg 패키지 형식이며
Windows `.exe`가 아니라 `.deb`·`.apk`에 가깝다.

Pi 5를 SSH 대상 장치로 한 번 등록해두면 업로드·설치·실행이 한 명령으로 끝난다.

```bash
# 최초 1회: Pi 5를 custom device로 등록 (id, IP, ssh 포트, 개인키 경로를 물어본다)
flutter-atlas custom-devices add

# 이후: 빌드된 ipk를 업로드 → 설치 → 실행 (hot reload 연결까지)
flutter-atlas run -d <device_id> --release
```

`run`이 내부적으로 기존 앱 uninstall → ipk upload → install → run 순으로 수행하므로
`.ipk`를 손으로 복사할 필요가 없다. debug/profile 모드로 실행하면 콘솔에 DevTools URL이 나온다.

Flutter가 아닌 Native C/C++ 앱·서비스는 `arc` CLI를 쓴다. `arc doctor`로 환경을 확인한 뒤
`arc build && arc install`로 빌드와 장치 설치를 함께 수행한다.

작업을 끝내면 다음으로 컨테이너를 정리한다.

```powershell
docker compose -f display/atlas/compose.yaml down
```

Docker Desktop이 없는 현재 PC에서는 Compose 검증·이미지 빌드를 실행할 수 없다. Windows Docker Desktop에서 host network 제약이 있으므로 MQTT·실장치 통신 검증은 Raspberry Pi 또는 WSL/Linux에서 수행한다.
