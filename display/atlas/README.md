# Pi 5 Atlas 개발·빌드·배포 가이드

이 문서가 DESKMATE Atlas 앱의 **준비 → 테스트 → `.ipk` 빌드 → Pi 5 설치·실행 → 로그 확인**
절차의 기준이다. Docker 설정과 앱 소스는 저장소에 두고, 공급사 SDK는 Git에서 제외한다.

```text
개발 PC (x86_64)                                  Raspberry Pi 5 (arm64)
┌─ Docker 컨테이너 ─────────────────────────┐ SSH  ┌─ AI Native OS ───────────┐
│ Flutter 소스 → 테스트 → Atlas 크로스 빌드  ├────►│ .ipk 설치 → 네이티브 실행 │
│      ↑                    ↓               │      │ 화면·터치·로그 확인        │
│      └──────── 로그 기반 수정              │◄────┤                          │
└────────────────────────────────────────────┘      └─────────────────────────┘
```

Docker는 빌드 PC에서만 실행한다. Pi 5에 Docker를 설치하거나 컨테이너 안에서 앱을 실행하는
구성이 아니다. `.ipk`는 실행 파일 그 자체가 아니라 AI Native OS에 설치하는 opkg 패키지다.

## 0. 선행 조건

- x86_64 개발 PC: Docker Engine + Docker Compose V2
- Windows 개발 PC: WSL2 backend를 사용하는 Docker Desktop 권장
- Pi 5: LG AI Native OS Video Profile, 전원·화면·네트워크 연결, SSH 접근 가능
- 개발 PC, Pi 4, Pi 5: 같은 개발 LAN 권장

다음 명령이 모두 성공해야 한다.

```powershell
docker version
docker compose version
```

이 프로젝트의 Windows 준비 스크립트는 관리자 PowerShell에서 WSL2 필수 기능을 활성화하고
대용량 도구용 D 드라이브 폴더를 만든다.

```powershell
PowerShell -ExecutionPolicy Bypass -File .\display\atlas\scripts\setup-windows-prerequisites.ps1 `
  -ToolRoot 'D:\SW임베디드경진대회_LG\Toolchains'
```

Ubuntu의 Docker Engine 설치는 LG 원본을 보존한
[`reference/raspberrypi/atlas-docker-env-guide.md`](../../reference/raspberrypi/atlas-docker-env-guide.md)를 참고한다.

## 1. 공급사 자산 준비 — 최초 1회

공식 Atlas 패키지를 확보한 뒤 저장소 루트에서 실행한다.

```powershell
PowerShell -ExecutionPolicy Bypass -File .\display\atlas\scripts\import-atlas-vendor.ps1 `
  -SourcePath '<공식 Atlas 패키지를 푼 폴더>'
PowerShell -ExecutionPolicy Bypass -File .\display\atlas\scripts\verify-atlas-vendor.ps1
```

`vendor/`는 `.gitignore` 대상이다. 다른 PC에서도 같은 공식 패키지로 위 과정을 반복한다.

```text
display/atlas/vendor/
├── atlas_engine/
├── flutter-atlas-plugins/
├── flutter-elinux-atlas/
├── arc-0.5.0.tgz
└── atlas-sdk-x86_64-armv8a-generic_arm64-toolchain-*.sh
```

## 2. 개발 컨테이너 준비

호스트 PowerShell에서 실행한다.

```powershell
docker compose -f display/atlas/compose.yaml build atlas-dev
docker compose -f display/atlas/compose.yaml up -d
docker compose -f display/atlas/compose.yaml exec atlas-dev bash
```

이미지 빌드 과정에서 Flutter SDK까지 초기화하므로 새 컨테이너의 첫 실행 때 SDK를 다시 내려받지 않는다.
컨테이너의 `/workspace`는 저장소 루트와 연결된다. 호스트에서 코드를 수정하면 컨테이너에도
즉시 반영되므로 소스를 따로 복사하지 않는다.

컨테이너 셸에서 도구체인을 확인한다.

```bash
source "$ATLAS_FLUTTER_NDK_ENV"
flutter --version
flutter-atlas doctor -v
```

## 3. Flutter 테스트와 release IPK 빌드

Pi 4의 실제 IP를 먼저 정하고 컨테이너에서 실행한다.

```bash
source "$ATLAS_FLUTTER_NDK_ENV"
cd /workspace/display/atlas/app
flutter pub get
flutter test
flutter-atlas build atlas --ipk --release \
  --dart-define=DESKMATE_HUB_URL=http://<Pi4-IP>:8765

find build -type f -name '*.ipk' -print
```

같은 작업 트리에서 debug 실행 후 release IPK를 만들 때는 먼저 `flutter clean`을 실행한다.
공급사 도구가 기존 bundle을 완전히 비우지 않아 `kernel_blob.bin`과 snapshot 같은 debug 산출물이
release IPK에 섞일 수 있다. clean release IPK에는 해당 파일들이 없어야 한다.

URL을 빼고 빌드하면 Pi 4 없이 화면 내장 데모가 순환한다. URL을 넣으면 **실행 중인 Pi 5가**
Pi 4에 직접 접속한다. 빌드 컨테이너가 Pi 4 상태 API를 대신 중계하지 않는다.

내장 데모는 화면과 상태별 UI를 빠르게 확인하기 위해 1초마다
`START → FOCUS_PC → FATIGUE_SUSPECT → ACTION_ENV → RECOVERY`를 갱신하고 5초마다 반복한다.
따라서 화면 수치·문구·색상이 계속 바뀌는 것은 정상이다. 실제 Hub URL을 넣은 빌드에서는
1초마다 `/api/state`를 조회하며, 화면은 Hub가 반환한 상태에 따라 갱신된다.

내장 데모 화면 하단의 `자동 순환: ON/OFF` 버튼은 터치 확인용이다. OFF로 바꾸면 현재 데모
상태에서 멈추고 스낵바가 표시되며, ON으로 바꾸면 즉시 상태 갱신을 재개한다. 실제 Hub 연결
빌드에는 이 데모 전용 버튼이 표시되지 않는다.

성공 기준은 테스트 통과, build 명령 exit code 0, `find` 결과에
`com.atlas.app.deskmate_display`의 `.ipk`가 존재하는 것이다. SDK·bundle·`.ipk`는 Git에 넣지 않는다.
공급사 도구 버전이 다르면 먼저 `flutter-atlas build atlas --help`로 `--ipk` 지원을 확인한다.

## 4. Pi 5를 SSH 장치로 등록 — 최초 1회

컨테이너에서 다음을 실행하고 장치 ID, Pi 5 IP, SSH 포트, 개인키 경로를 입력한다.

```bash
flutter config --enable-custom-devices
flutter-atlas custom-devices add
flutter-atlas devices
```

장치 ID는 공백 없이 예를 들어 `deskmate_pi5`로 둔다. 개인키를 쓸 경우 저장소 밖의 경로를
등록하고 키 파일을 Git에 넣지 않는다. 컨테이너에서 Pi 5의 SSH 포트에 접근할 수 있어야 한다.
현재 Compose는 `/root/.config/flutter`를 영속 볼륨으로 두지 않으므로 컨테이너를 재생성하면
custom device 등록도 다시 해야 한다. IP와 로컬 인증 설정이 든 이 파일을 저장소에 커밋하지 않는다.

보드 주소가 DHCP라면 저장소에 적힌 마지막 IP를 고정값으로 간주하지 않는다. 공유·교내 LAN에서
보드에 임의의 정적 IP를 지정하면 충돌할 수 있으므로, 가능하면 보드 유선 MAC을 기준으로 DHCP
예약을 요청한다. 개발 PC의 `~/.ssh/config` 별명은 편리한 로컬 설정일 뿐 다른 개발자에게 자동으로
공유되지 않는다.

개인키 경로를 비워도 접속되는 개발 이미지가 있을 수 있지만, 빈 비밀번호로 root 로그인을
허용하는 보드는 신뢰할 수 있는 격리 개발망에서만 사용한다. 외부 또는 공용망에 연결하기 전에
공급사 정책에 맞춰 키 인증과 접근 제한을 적용한다.

## 5. 설치·실행·로그 확인

빠른 개발 반복은 debug 모드를 사용한다.

```bash
cd /workspace/display/atlas/app
flutter-atlas run -d deskmate_pi5 --debug \
  --dart-define=DESKMATE_HUB_URL=http://<Pi4-IP>:8765
```

`run` 콘솔에서 앱 표준 출력, 오류, DevTools URL을 확인한다. 콘솔 키는 다음과 같다.

- `r`: hot reload
- `R`: hot restart
- `d`: CLI만 분리하고 Pi 5 앱은 계속 실행
- `q`: Pi 5 앱까지 종료

시연 후보를 고정할 때는 release로 다시 빌드·실행한다.

```bash
flutter-atlas build atlas --ipk --release \
  --dart-define=DESKMATE_HUB_URL=http://<Pi4-IP>:8765
flutter-atlas run -d deskmate_pi5 --release \
  --dart-define=DESKMATE_HUB_URL=http://<Pi4-IP>:8765
```

`run`은 공식 가이드 기준으로 기존 앱 uninstall → `.ipk` upload → install → run 순서를 수행한다.
정상 경로에서는 `.ipk`를 손으로 복사하거나 Pi 5에서 직접 `opkg` 명령을 실행하지 않는다.

## 6. 수정 반복

```text
1. 개발 PC에서 display/atlas/app/lib 수정
2. flutter test
3. flutter-atlas run --debug
4. Pi 5 화면·터치와 run 콘솔/DevTools 확인
5. 수정 후 hot reload 또는 재실행
6. 통과하면 --ipk --release 빌드
```

화면만 확인할 때는 내장 데모로 시작하고, 그다음 Pi 4 HTTP 미리보기, 마지막에 최종 통신
어댑터 순으로 연결한다. UI 문제와 보드 통신 문제를 한 번에 섞지 않는다.

## 7. 종료와 산출물 관리

작업을 끝내면 호스트 PowerShell에서 컨테이너를 정리한다.

```powershell
docker compose -f display/atlas/compose.yaml down
```

다음 항목은 커밋하지 않는다.

- `display/atlas/vendor/`
- `display/atlas/app/build/`와 생성된 `.ipk`
- Flutter/Atlas 캐시와 SDK
- Pi 5 SSH 개인키, 실행 로그, 사용자 데이터

Flutter가 아닌 Native C/C++ 앱·서비스만 `arc doctor`와 `arc build && arc install` 경로를 쓴다.
DESKMATE display는 Flutter 앱이므로 `flutter-atlas`가 기준이다.

## 8. 현재 확인 상태

- LG 공식 SDK 원본과 vendor import 스크립트: 확보
- Flutter 앱·Atlas 러너: 구현
- 이 Windows PC의 WSL2 기능과 Docker Desktop(D 드라이브): 설치·초기화·재현 빌드 확인
- Flutter 테스트와 내장 데모 release `.ipk`: 통과·생성 확인
- Pi 5 SSH 도달성과 `deskmate_pi5` 장치 등록: 확인
- Pi 5 터치 확인용 자동 순환 ON/OFF 버튼 포함 release 앱 교체 설치·fullscreen 실행: 확인
- Pi 5 버튼 터치 육안 확인: USB MTouch가 input event 생성 전에 xHCI 오류로 분리되어 하드웨어 점검 대기
- 최신 진행 인계: [`../../docs/atlas-build-handoff.md`](../../docs/atlas-build-handoff.md)
