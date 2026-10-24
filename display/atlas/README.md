# Pi 5 Atlas 개발·빌드·배포 가이드

## 일상 화면 구성 (2026-10-18)

앱을 켜면 캘린더·시계·집중·날씨·실내·기기 제어·설정 메뉴가 보인다. 원형 홈 버튼은
왼쪽 메뉴 아래에 고정되어 메뉴를 스크롤해도 남는다. 좁은 화면에서는 하단 메뉴 오른쪽에 고정된다. 기존 센서·FSM·연결 진단은
**설정 → 개발자 화면**에서 연다. **기기 제어 → 기기 연결**은 기존 Bluetooth 연결 도구를 연다.

메뉴는 아이콘 위치에 맞춰 스냅하지 않고 연속 드래그·관성으로 움직이며, 휠 입력도 누적해서
230ms 완화 곡선으로 이어진다. 아이콘은 가장자리에서 일부 잘릴 수 있다. 메뉴와 콘텐츠 사이에는
1px 옅은 구분선을 두며, 좁은 화면에서는 하단 메뉴 위에 같은 구분선을 둔다.

- 홈 화면의 빈 곳이나 상단 로고 오른쪽 여백을 길게 누르면 위젯 편집을 연다.
  드래그로 순서를 바꾸고, 시계·음악은 오른쪽 아래 조절점을 좌우로 끌거나 눌러 1칸·2칸을 고른다.
  작은 카드가 빈칸을 채우도록 자동 재배치하며 숨기기·다시 추가하기도 가능하다.
  완료 시 위치·크기·표시를 저장하고 취소 시 함께 되돌린다. 기존 설정의 편집 버튼은 제거했다.
  라이트·다크·자동 테마는 설정에서 고른다.
- 홈은 화면 높이에 맞춰 행 높이를 나누며 세로 스크롤하지 않는다. 각 위젯을 누르면
  같은 왼쪽 메뉴의 해당 페이지로 이동한다. 음악은 기기 제어의 뮤직 상세 화면으로 이동한다.
  홈의 재생·탐색 버튼은 페이지를 바꾸지 않고 작동한다. 달력·집중·날씨·음악의 이전 확대 팝업은 제거했다.
- 캘린더에서 일정의 시작·끝 시간과 장소를 입력하고 수정·삭제한다. 홈 달력은 일정 수를 점으로,
  확대 달력은 일정 이름으로 표시하며 선택 날짜의 일정을 시간순 세로 타임라인으로 보여준다.
  D-day 표시·선택은 제거했다. 이전 버전의 시작 시간만 있는 일정도 그대로 읽는다.
- 집중 타이머는 페이지를 바꿔도 진행된다. 시계는 센서 메시지와 독립적으로 갱신된다.
- 별도 시계 페이지에는 일반 타이머·스톱워치(랩)·매일 알람을 제공한다. 타이머와 스톱워치는
  페이지 이동과 무관하게 진행되며, 타이머 기본 시간·알람 설정·마지막 알림 날짜는 보관한다.
  알람과 타이머 알림은 앱 실행 중에 표시하며 시스템 알림음을 요청한다. 표시 시각은 한국 시각을 사용한다.
- 시계는 다른 카드와 같은 밝은 배경에 짙은 숫자를 표시한다. 시계 아래 날짜는 생략한다.
  시·분은 작게 줄여 좌우에 약 1.6도 기울이며, 콜론은 두 숫자 사이의 정확한 중앙에서 깜빡이지 않는다.
- 전체 UI는 연한 하늘색·화이트 배경과 블루 강조색을 사용한다. 다크 모드는 네이비 계열이다.
  홈 카드는 같은 하늘색 계열 안에서 명도를 조금씩 다르게 두고, 얇은 경계·옅은 그림자와
  16px 간격으로 구분한다. 시계 숫자는 축소한 버전보다 약 9% 키웠다.
- 집중 위젯은 불꽃·이루매·경과 시간(mm:ss)과 목표 시간(min)을 표시하며, 누르면 25·50·90분 또는 사용자 지정 시간을 설정한다.
  마지막 설정 시간을 보관하고, 내장 데모에서는 앱 실행 시 카운트다운을 시작한다.
  홈 집중 숫자와 목표 표시는 날씨와 같은 학교안심 그림일기체를 쓴다.
- 상단 오른쪽에는 휴대폰 형태의 Wi-Fi 신호 아이콘을 표시한다. Atlas ConnMan의 공개 읽기 전용
  `GetServices`로 5초마다 연결·신호 세기를 확인하며, 누르면 연결 이름과 신호 또는 유선 LAN 상태를 보여준다.
  저장된 AP를 연결로 취급하지 않으며 유선 연결은 별도 아이콘으로 구분한다. 조회 실패는 확인 불가로 표시한다.
  네트워크 검색·연결 변경·비밀번호 조회나 네트워크 관리 권한 추가는 하지 않는다.
- 이루매는 인사·집중 작업·완료 축하·휴식·중도 종료를 각각 애니메이션으로 표시한다.
  일시정지는 포기로 처리하지 않는다. 잠든 포즈는 명시적 쉬기/REST에만 연결하며 무입력으로 수면을 추정하지 않는다.
  생성 자산·문구·상태 연결은 [이루매 자산 기록](ui_env_app/assets/mascot/README.md)에 정리했다.
- 실내 환경은 온도·습도·CO₂를 가로 세 줄로 표시하고 조도는 생략한다. 데모는 24.6°C·48%·좋음이다.
  CO₂는 숫자 대신 5단계 표정과 상태로 표시한다. `assets/config/co2_bands.json`은 화면용 표시 구간이며
  허브 FSM이나 자동 제어 기준을 바꾸지 않는다. 1000 ppm 미만 세분화는 데모 UI의 자체 구간이다.
- 음악 위젯은 곡/아티스트·진행 막대·즐겨찾기·이전/재생/다음·볼륨으로 구성한다.
  1칸에서는 곡 정보와 이전/재생/다음을 우선 표시하며 즐겨찾기·볼륨은 뮤직 보관함에서도 연다.
- 기기 제어 목록은 별명·종류·연결 상태가 있는 1×1 카드다. 조명·음표 스피커 아이콘은
  코드로 그린 가전 형태이며 별명은 상세 화면에서 변경·저장한다. 저장된 주소만으로 연결됨을 표시하지 않는다.
  조명 밝기는 끄기·매우 어둡게·어둡게·보통·밝게·매우 밝게·최대의 7단계다.
  색온도 슬라이더는 노란빛→하얀빛 7칸이며, 실제 iLink의 검증된 5단계 명령에는
  `5,4,4,3,2,2,1`로 대응한다. 밝기 끄기는 전원 OFF 명령으로 보낸다. 미연결 데모는 화면 값만 바꾼다.
- 뮤직 상세 하단은 라디오·보관함·플레이리스트다. 라디오는 내장곡의 데모 스테이션이며
  온라인 방송을 가장하지 않는다. 직접 만든 플레이리스트와 즐겨찾기 큐 안에서 순환 재생한다.
  플레이리스트 이름·선택곡은 저장되며, 기존 iLink 조명과 실제 음악 재생기를 계속 사용한다.
  재생 위치·길이는 네이티브 플레이어가 보고한 값만 표시하며, 길이를 받기 전에는 탐색을 비활성화한다.
- 자동 제어 승인과 조명·스피커별 대상 선택은 설정에만 있다. 제외한 기기는 자동 제어하지 않으며
  직접 조절은 가능하다. 승인 기본값은 OFF다.
- 실제 자동 제어 결과는 완료·실패 알림으로 표시한다. 완료 알림은 8초 뒤 사라지고,
  기기 제어의 작은 **자동 제어 내역** 버튼에서 이번 실행의 기록을 다시 볼 수 있다.
- 일정과 화면·승인 설정은 연결 설정과 별도인 `workspace.json`에 저장한다.
  패키지 Remove/Install은 보드의 앱 데이터 폴더를 다시 만들므로 UI 재배포 때는
  [설정 보존 업데이트 스크립트](scripts/update-ui-preserving-settings.sh)를 사용한다.
  `/tmp/download/deskmate-workspace.ipk`를 올린 뒤 보드에서 실행하며 배치·일정·기기 연결 설정을 보존한다.
- 시계는 제공받은 LG EI Headline 가변 폰트를 사용한다. 한글을 포함한 나머지 UI와 팝업·개발자 화면은
  사용자가 제공한 하이원 원추리체 B를 사용한다. 출처와 파일 구성은 [폰트 기록](ui_env_app/assets/fonts/README.md)에 정리했다.
  상단 DESKMATE 로고만 Comic Sans MS를 사용한다.

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
cd /workspace/display/atlas/ui_env_app
flutter pub get
flutter test
flutter-atlas build atlas --ipk --release \
  --dart-define=DESKMATE_HUB_URL=http://<Pi4-IP>:8765

find build -type f -name '*.ipk' -print
```

같은 작업 트리에서 debug 실행 후 release IPK를 만들 때는 먼저 `flutter clean`을 실행한다.
공급사 도구가 기존 bundle을 완전히 비우지 않아 `kernel_blob.bin`과 snapshot 같은 debug 산출물이
release IPK에 섞일 수 있다. clean release IPK에는 해당 파일들이 없어야 한다.

URL을 빼고 빌드하면 Pi 4 없이 화면 내장 데모가 순환한다. `DESKMATE_HUB_URL`을 넣으면
**실행 중인 Pi 5가** Pi 4 HTTP 개발 API를 직접 조회한다. 빌드 컨테이너가 Pi 4 상태 API를
대신 중계하지 않는다.

최종 Pi 4↔Pi 5 통신은 HTTP가 아니라 MQTT다. Pi 4 Mosquitto broker가 준비된 뒤에는 아래처럼
빌드한다. Pi 5는 `deskmate/state/phase`와 `deskmate/interaction/request`를 QoS 1로 구독하고,
터치 feedback을 `deskmate/feedback/user`로 QoS 1 발행한다. `state/phase`의 retain으로 display
재시작 직후에도 현재 상태를 받는다.

```bash
flutter-atlas build atlas --ipk --release \
  --dart-define=DESKMATE_MQTT_HOST=<Pi4-MQTT-IP> \
  --dart-define=DESKMATE_MQTT_PORT=1883
```

자세 탭이 볼 Pi 4 자세 노드 주소는 **`DESKMATE_POSTURE_URL`**(배포 스크립트 `ATLAS_POSTURE_URL`)로 따로 준다.
보통은 비워 둔다 — 비어 있어야 같은 보드의 camsvc(127.0.0.1:8770) → ESP32-CAM 보드 직결 → 데모 순으로 내려간다.
예전처럼 `DESKMATE_HUB_URL` 을 읽으면 허브 주소로 빌드했을 때 자세 탭이 camsvc·보드 직결을 건너뛴다.
보드 직결에는 `appinfo.json` 의 `privileged.peripheral` 권한과 UART D-Bus 정책
(`ui_env_app/atlas/meta/com.atlas.app.deskmate_display.conf` → 보드 `/data/share/usr/share/dbus-1/system.d/`, `systemctl reload dbus`)이 필요하다.
앱은 연결 선택과 첫 성공·실패를 진단 로그 파일(`<앱 설치 경로>/camtest.log`)에 남긴다 — 이 보드는 앱 표준출력이 journal 에 남지 않는다.

**카메라 탭은 개발용, 프리뷰 모드는 ToF 뷰.** 개발 도구(**설정 → 개발자 화면**)의 헤더에서 카메라 '자세'(ESP32-CAM/camsvc) 탭은
숨겨 두고(`main.dart` 의 `kShowCameraPostureTab`, 코드는 그대로) 그 자리에 **프리뷰** 탭을 둔다 — 정식 기능이 아니라 "프리뷰 · 정식 기능 아님" 배지가 붙고
사용자 화면(워크스페이스)에는 나오지 않는다. 프리뷰 탭은 Pi 4 중계기 `:8772/raw`(센서별 ToF 8×8 거리·상태, 이미 방향이 적용된 배열이라 다시 뒤집지 않는다)와
자세 서비스 `:8771` 을 직접 폴링해 **열지도 두 장(A=왼쪽, B)과 ToF 자세 라벨**을 그리고(얼굴·영상 없음, 64칸 깊이값만), mmWave·환경·키스트로크·FSM·마지막 개입
설명(`state/phase` 의 `sensor_summary`)을 한 화면에 모은다. 아래 "지금 판정이 맞나요?" 는 기존 `feedback/user` 경로로 수락·거절·정정(+짧은 메모 `data.note`)을 보낸다.
Pi 4 주소는 `settings.json` 의 `preview_host` → MQTT 브로커 호스트 → `DESKMATE_HUB_URL` 순이고, 전부 없으면 탭 안의 입력칸에 적는다.

`DESKMATE_MQTT_HOST`와 `DESKMATE_HUB_URL`을 동시에 주면 MQTT를 우선한다. HTTP는 센서 테스트와
화면·Hub 분리 검증을 위한 개발용 fallback으로만 유지한다.

내장 데모는 화면과 상태별 UI를 빠르게 확인하기 위해, 사용자가
`자동 순환: ON`으로 바꾸면 1초마다
`START → FOCUS_PC → FATIGUE_SUSPECT → ACTION_ENV → RECOVERY`를 갱신하고 5초마다 반복한다.
기본값은 OFF이므로 화면은 첫 상태에서 멈춘다. 실제 Hub URL을 넣은 빌드에서는
1초마다 `/api/state`를 조회하며, 화면은 Hub가 반환한 상태에 따라 갱신된다.

내장 데모 화면 하단의 `자동 순환: ON/OFF` 버튼은 터치 확인용이다. OFF로 바꾸면 현재 데모
상태에서 멈추고 스낵바가 표시되며, ON으로 바꾸면 즉시 상태 갱신을 재개한다. 실제 Hub 연결
빌드에는 이 데모 전용 버튼이 표시되지 않는다.

상단 아이콘으로 다음 세 화면을 전환한다.

- **상태**: FSM 상태·점수·센서·키스트로크 지표
- **센서 테스트**: PC 비율과 키스트로크·ToF 자세·호흡·환경·경과 시간의
  정규화 집중 저하/피로 기여도를 조정해 Pi 4의 실제 FSM을 30초 또는 3분 단위로 진행
- **FSM 전체**: VER5 18개 상태와 전이를 계층별 직교선으로 보여주고, 현재 상태와 다음 가능
  상태를 각각 노랑·민트로 강조. 우측 상단의 `1 전체 / 2 중간 / 3 상세` 스위치 또는 마우스
  휠로 한 단계씩 확대·축소하며, 드래그로 이동 가능

센서 테스트는 `DESKMATE_HUB_URL`이 있으면 그 주소를 쓰고, 없는 빌드에서는 화면에서
Pi 4 URL을 임시로 입력해 연결한다. 주소는 저장하지 않는다. Flutter에 FSM 임계값을 복제하지 않고
Hub의 `/api/test-frame` 개발 API와 `config/fsm.yaml`을 사용한다.
우측 상단 스피커 `ON/OFF` 버튼은 패키지에 포함된 달빛 → 쇼팽 녹턴 Op.9 No.2 →
사티 짐노페디 1번 → 베토벤 엘리제를 위하여 → 모차르트 소나타 K.545 1악장 →
바흐 프렐류드 BWV 846을 `audioplayers_atlas`로 자동 순환 재생한다. OFF는 일시정지,
ON은 현재 곡 이어듣기다. ON/OFF 옆 곡 이름을 누르면 여섯 곡 중 재생할 곡을 선택한다.
ON 상태에서는 즉시 선택 곡으로 바뀌고, OFF 상태에서는 다음 ON 때 선택 곡을 시작한다.
곡이 끝나면 선택 곡의 다음 순서부터 자동 순환한다.
곡 이름과 ON/OFF 사이의 음량 아이콘을 누르면 0~100% 슬라이더가 열리며, 설정한
음량은 자동 또는 수동으로 다음 곡이 시작되어 새 플레이어가 만들어져도 유지된다.
Bluetooth 출력에서는 Atlas player gain이 실제 sink-input에 반영되지 않으므로, 앱은
`com.atlas.AudioManager1`에서 현재 활성 출력 장치를 조회해 그 장치의 `Volume`을 제어한다.
장치의 음량 변경 신호도 구독하므로 Bluetooth 스피커 버튼 조작이 UI에 반영된다. 특정
Bluetooth 장치 주소나 PulseAudio sink-input 번호에는 의존하지 않는다. 앱 manifest에는
공식 `com.atlas.permission.privileged.audiocontrol` 권한을 선언한다.
현재 Atlas 버전은 일부 Bluetooth AVRCP 버튼 변경에서 D-Bus 속성 변경 신호를 누락하므로,
앱은 활성 출력 장치의 음량을 0.5초 간격으로도 확인해 슬라이더를 동기화한다.
Atlas의 미디어 서비스는 이미 로드된 플레이어의 음원 교체를 거부(`already loaded`)하므로,
곡 전환 시 이전 플레이어를 dispose/unload한 뒤 새 플레이어를 생성한다.
음원 출처·이용 조건은
[`app/assets/audio/CREDITS.md`](app/assets/audio/CREDITS.md)에 기록하며 앱에도 포함한다.
전원 아이콘은 확인 대화상자 후 앱 프로세스를 종료한다.

**Bluetooth** 화면에서는 Atlas Bluetooth 서비스로 주변 기기를 검색한다. 스피커는 A2DP로 페어링·연결하면 기존 음악이 해당 시스템 출력으로 재생된다. iLink 램프를 선택한 뒤 `상태 기반 조명·음악 피드백`을 켜면 MQTT/HTTP/데모로 수신한 FSM phase 변화에 맞춰 조명 프레임을 전송하고, `fatigue`에서 음악을 재생하며 `recovery`·`idle`·`end`에서 일시정지한다. Bluetooth 권한은 설치 후 Atlas 설정에서 사용자 승인이 필요하다.

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
cd /workspace/display/atlas/ui_env_app
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
1. 개발 PC에서 display/atlas/ui_env_app/lib 수정
2. flutter test
3. flutter-atlas run --debug
4. Pi 5 화면·터치와 run 콘솔/DevTools 확인
5. 수정 후 hot reload 또는 재실행
6. 통과하면 --ipk --release 빌드
```

화면만 확인할 때는 내장 데모로 시작하고, 그다음 Pi 4 HTTP 미리보기, 마지막에 최종 통신
어댑터 순으로 연결한다. UI 문제와 보드 통신 문제를 한 번에 섞지 않는다.

현재 공급사 도구의 `run`은 기존 IPK가 있으면 이를 재사용할 수 있다. Dart UI 수정은
실행 중 `r`로 반영하지만, MP3 같은 패키지 asset을 추가한 경우에는 기존 실행을 `q`로
종료하고 `flutter-atlas build atlas --ipk --debug` 후 `flutter-atlas run -d <device_id> --debug`로
다시 적용한다. 새 asset이 IPK에 포함됐는지 확인한다.

## 7. 종료와 산출물 관리

작업을 끝내면 호스트 PowerShell에서 컨테이너를 정리한다.

```powershell
docker compose -f display/atlas/compose.yaml down
```

다음 항목은 커밋하지 않는다.

- `display/atlas/vendor/`
- `display/atlas/ui_env_app/build/`와 생성된 `.ipk`
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

## 9. 데모 홈 위젯 편집

홈 여백을 길게 누르면 편집 모드로 들어간다. 카드 내용은 유지한 채 편집 표시가 나타나며,
카드를 끌면 배치가 이동한다. 시계·사운드·날씨는 오른쪽 아래 모서리를 끌어 1칸/2칸으로 조절한다.
1칸 시계는 아날로그, 2칸 시계는 디지털이다. 홈은 한 화면을 유지하며, 공간이 부족하면 추가·확대를 막고 안내한다.

상단 `위젯`에서 위쪽의 초록색 +를 누르면 홈에 추가되어 아래 사용 중 목록으로 이동한다.
아래쪽의 빨간색 −를 누르면 홈에서 빠지고 위쪽 목록으로 이동한다. `취소`는 편집 전 배치·크기를 복원하고
`완료`는 저장한다. 새 날씨 위젯은 이 목록에서 추가한다.
날씨는 1칸 요약 / 2칸 시간대별 예보 구성이며, 현재 데모 모드의 서울 예시 값이다.
실제 위치·날씨 API는 연결하지 않았으며, 데모가 아니면 연결 대기 상태를 표시한다.

배치·날씨 표현 참고: [Apple 위젯 편집 안내](https://support.apple.com/en-ie/118610),
[Apple 날씨 위젯 안내](https://support.apple.com/guide/iphone/use-weather-widgets-iph8bf15cb61/ios).

### 날씨 위젯 표시와 데모 선택

날씨 위젯은 홈과 같은 연한 하늘색·화이트 카드, 블루 글자·아이콘을 사용한다.
1칸은 온도 옆의 원형 날씨 아이콘과 하단 최고·최저·강수 정보를 표시한다.
2칸은 왼쪽 현재 정보 / 오른쪽 시간대별 예보(기본 크기에서는 6시간)로 나눈다.
서울특별시·위치 화살표 대신 짧은 지역명과 작은 위치 아이콘을 사용한다.
맑음·흐림·비·눈·뇌우·안개와 낮/밤에 따라 배경의 옅은 색조·구름·빗줄기·눈·별이 달라진다.
날씨의 밤하늘도 밝은 앱에서는 연한 색을 유지하고, 앱 다크 모드에서는 다른 홈 카드와 같은 어두운 블루를 쓴다.
배경은 정적인 표현이며 선택을 바꾸면 부드럽게 전환한다.

데모 모드에서 날씨 위젯을 누르면 `날씨 미리보기`가 열린다. 날씨와 `밤하늘`을 선택하면
현재 온도·상태 아이콘·시간대별 예보·배경이 함께 바뀐다. 선택은 현재 실행에만 유지하며,
사용자의 위젯 배치·자동 제어 설정을 바꾸지 않는다. 실제 날씨 연결은 아직 없으며,
데모 값에는 `데모`를 표시한다. 연결 대기 중에는 가상의 실시간 날씨를 표시하지 않는다.


집중 완료 시 홈으로 돌아와 리포트를 띄우며 자동으로 닫히지 않는다. 바깥 여백 또는 닫기로 닫고, 집중 화면의 집중 기록에서 다시 볼 수 있다. 최근 완료 타이머 200회를 workspace.json에 저장한다. 오늘 누적은 종료 날짜 기준 완료 타이머 합계이며 일시정지 시간·중도 종료·미리보기를 합산하지 않는다. 데모/실제 모드는 집계를 분리한다. 실제 모드는 집중 비율·자세·집중의 질을 분석 연결 대기로 표시한다. 데모는 84% 집중·78% 바른 자세 및 예시 설명을 실제 측정과 구분한다. 기존 허브 session_report 계약·추론·카메라 경로는 변경하지 않는다.
