# Atlas IPK 빌드 진행 인계

> 갱신: 2026-09-01, 센서 FSM 테스트·18상태 시각화 clean release IPK 검증 완료
> 재시작 문구: `docs/atlas-build-handoff.md와 AGENTS.md를 읽고 Atlas Pi 5 실기 배포를 이어서 진행해줘.`

## 완료된 작업

- 실제 개발 흐름을 `개발 PC Docker → arm64 .ipk → SSH → Pi 5 AI Native OS 설치·실행 → 로그 기반 수정`으로 문서화했다.
- 문서 커밋 `af23144`를 `origin/feat/atlas-display-env`에 푸시했다.
- LG 공식 Atlas SDK 자산을 Git 제외 경로 `display/atlas/vendor/`에 import하고 검증했다.
- Windows 기능 `Microsoft-Windows-Subsystem-Linux`, `VirtualMachinePlatform`을 활성화했다.
- Docker Desktop 공식 설치 파일을 내려받고 Docker Inc Authenticode 서명이 `Valid`임을 확인했다.
- Docker Desktop 프로그램을 `D:\SW임베디드경진대회_LG\Toolchains\DockerDesktop`에 설치했다.
- Docker WSL 데이터 경로는 `D:\SW임베디드경진대회_LG\Toolchains\DockerData`로 지정했다.
- Docker Desktop 4.88.1, Docker Engine 29.7.2, Compose 5.4.0과 WSL2 동작을 확인했다.
- NodeSource의 현재 GPG 키 URL을 사용하도록 Dockerfile을 수정했다.
- 이미지 빌드 중 `flutter-atlas --version`을 실행해 Flutter SDK가 새 컨테이너에서도 즉시 준비되게 했다.
- 새 이미지를 처음부터 다시 빌드하고 새 컨테이너로 교체해 Flutter 3.27.4, Dart 3.6.2,
  Node 22.23.2, Atlas SDK 26.06.0, arc 0.5.0을 확인했다.
- 앱 테스트 2개가 모두 통과했고 Pi 4 URL 없는 내장 데모 release `.ipk`를 두 차례 빌드했다.
- Notion MCP OAuth 재인증을 완료했다. 현재 Codex 세션에서 계속 경고가 보이면 세션을 한 번 새로 연다.
- Pi 5에 SSH로 접속해 Raspberry Pi 5 Model B Rev 1.1과 ATLAS Platform
  26.06.0-246.scarthgap.s6를 확인했다.
- 컨테이너에서 custom devices 기능을 켜고 Pi 5를 `deskmate_pi5`로 등록했다.
- 내장 데모 debug IPK를 Pi 5에 업로드·설치·실행했고 Dart VM 연결과 앱 프로세스를 확인했다.
- debug 빌드 뒤 곧바로 release 빌드하면 debug bundle 파일이 섞이는 현상을 확인했다. `flutter clean`
  후 테스트와 release 빌드를 다시 수행해 정상 패키지를 생성했다.
- 기존 debug 앱을 제거하고 clean release IPK를 Pi 5에 업로드·설치·실행했다. 콘솔 분리 후에도
  `com.atlas.app.deskmate_display` fullscreen 프로세스가 계속 실행되는 것을 확인했다.
- 내장 데모 자동 순환을 화면 하단 버튼으로 멈추고 재개할 수 있게 했으며 widget test를 포함한
  전체 테스트 3개를 통과했다. 새 release 앱을 Pi 5에 교체 설치하고 프로세스를 확인했다.
- 우측 상단 종료 확인 버튼, 정규화 센서 기여도 슬라이더, 30초/3분 가상 진행,
  VER5 18상태 전체 그래프와 현재/다음 전이 강조를 추가했다.
- 센서 테스트는 Flutter 임계값 복제본이 아니라 Pi 4의 `/api/test-frame` 개발 API를 통해
  실제 `FSMEngine`·`config/fsm.yaml`을 사용한다. IPK에 URL을 고정하지 않고 앱에서 임시 입력할 수 있다.
- Flutter 분석 무경고, Flutter 테스트 22개, Hub 테스트 37개를 통과했다.
- 제공된 MP3를 앱 asset으로 포함하고 `audioplayers_atlas` 반복 재생 ON/OFF 버튼을 추가했다.
  IPK 내 MP3와 `libaudioplayers_atlas_plugin.so` 포함을 확인했다.

## 현재 상태

- 개발 이미지와 내장 데모 `.ipk`의 로컬 재현 검증까지 완료했다.
- 산출물: `display/atlas/app/build/atlas/arm64/release/ipk/com.atlas.app.deskmate_display.ipk`
- 크기: `9,165,442 bytes`
- SHA-256: `A77C537F2327C725DDA196F4964D722B9FFD51864D04F7BD608FB3DBF76E8FD3`
- 패키지 내부 실행 파일과 `libapp.so`가 AArch64임을 확인했다.
- Pi 5 release 앱의 화면·터치 육안 확인과 장시간 실기 로그 확인은 아직 남아 있다.
- Pi 4 주소와 최종 통신 방식은 미결정 상태이므로 내장 데모 IPK에는 Hub URL을 넣지 않았다.
- Pi 5를 재부팅하면 DESKMATE 앱이 자동 실행되지 않는다. 현재 배포 검증은
  `flutter-atlas run -d deskmate_pi5 --release`로 다시 설치·실행하는 방식이며, 자동 시작 등록은 별도 후속 작업이다.

### 현재 터치 장애 (2026-08-31 재현)

- 화면의 USB 터치 컨트롤러는 `0416:c168`, `TSTP MTouch`, serial `CMTP_1.0`으로 식별됐다.
- 부팅 약 3초에 `usb 1-1`/`xhci-hcd.0`에서 열거되지만 Linux input event 노드를 만들지 못한다.
- 부팅 약 13초에 `xHCI host controller not responding, assume dead`와 `HC died; cleaning up`이 발생해
  해당 컨트롤러와 MTouch가 분리된다. 재부팅 후에도 같은 순서로 재현됐다.
- 현재 `lsusb`와 `/proc/bus/input/devices`에는 MTouch가 없으므로 Flutter 앱까지 전달되는 터치 이벤트도 없다.
- 키보드 `046d:c34b`는 별도 `xhci-hcd.1`/`usb 3-1`에서 정상 동작한다. 따라서 앱의 버튼 구현보다
  화면 전원·USB 배선, 케이블 또는 터치 컨트롤러 쪽을 먼저 확인해야 한다.
- 커널 로그에는 명시적인 `under-voltage`/`over-current` 문구가 없었지만 전기적 원인을 배제한 것은 아니다.
  정확한 화면 모델과 배선도를 확인하기 전 화면의 `5V+GND` 전원과 USB VBUS를 동시에 연결하거나
  반복 재연결하지 않는다.

### 현재 Pi 5 접속 메모

- 마지막 확인 주소: `172.16.34.197/24`, SSH 22, 사용자 `root`
- 유선 MAC: `88:A2:9E:3C:CC:CA`
- 보드 호스트명: `atlas`
- 이 PC의 로컬 SSH 별명: `atlas-pi5`, `pi5`, `deskmate-pi5`
- ConnMan 설정은 `IPv4.method=dhcp`이며 `.197`은 마지막 DHCP 주소다.
- 현재 공급 이미지의 Dropbear는 root 빈 비밀번호 로그인을 허용한다. 정상 배포 인증으로 간주해
  의존하지 말고, 신뢰할 수 있는 개발망 밖에 연결하기 전 키 인증과 접근 제한을 적용한다.
- 교내 LAN에서 `.197`을 보드에 강제 고정하지 않는다. 고정이 필요하면 네트워크 관리자에게 위 MAC의
  DHCP 예약을 요청한다.

## 다음 할 일

1. 화면 뒷면 모델명, 전원 입력 표기, `5V+GND`와 `5V+Touch(USB)` 단자 사진 또는 제조사 배선도를 확보한다.
2. Pi 5와 화면 전원을 끈 상태에서 단일 전원 경로와 데이터 케이블을 확정한 뒤 다시 연결한다.
3. 재부팅 후 `lsusb`, `/proc/bus/input/devices`, `dmesg`에서 MTouch와 input event 노드가 유지되는지 확인한다.
4. DESKMATE를 다시 실행하고 화면 하단 `자동 순환: ON/OFF` 버튼으로 순환 정지·재개와 스낵바를 육안 확인한다.
5. Pi 4 주소가 정해지면 `DESKMATE_HUB_URL`을 넣어 실제 Hub 연동을 검증한다.
6. 실기 검증이 끝난 상태를 release `.ipk`로 다시 고정한다.

WSL 파일 시스템 성능 최적화는 별도 후속 작업이다. 현재 저장소는 D 드라이브 bind mount라서
작은 파일 I/O가 많은 Flutter 빌드가 느릴 수 있다. 장기적으로 WSL ext4 내부에 저장소를 두는 편이
유리하지만, 현재 빌드·배포 경로는 정상 동작하므로 Pi 5 실기 검증을 먼저 끝낸다.

## 사용자에게 받을 정보

Pi 5 실기 배포 직전 다음 정보가 필요하다. 자격증명이나 개인키 내용은 채팅·Git에 넣지 않는다.

- Pi 5 IP 주소
- SSH 포트(기본 22인지)
- SSH 사용자명 및 비밀번호/개인키 중 인증 방식
- Pi 4 IP 주소 또는 우선 내장 데모만 시험할지 여부
