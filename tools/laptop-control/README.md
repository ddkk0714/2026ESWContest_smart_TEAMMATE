# 노트북에서 DESKMATE 보기·조작

> Claude 인수인계: [현재 구현·검증·제약](CLAUDE-HANDOFF.md) · [그대로 붙여 넣을 시작 메시지](CLAUDE-START.md)

> 현재 통합 실행 주소는 **http://127.0.0.1:19891/** 입니다. [단일 콘솔 실행·안전 조건](UNIFIED-CONSOLE.md)을 먼저 확인하세요. 아래의 기존 주소는 개발 이력/별도 시험 모드입니다.


Pi5 DESKMATE V27 화면을 받아 클릭·드래그·휠 스크롤을 전달한다. 화면은 PNG 방식이며
문자 입력은 앱의 가상 키보드를 사용한다. 키 내용은 수집하거나 전달하지 않는다.

## 실행

Node.js 20+, OpenSSH, Docker, 기존 deskmate-atlas-dev:local 이미지와 신뢰한 Pi5 known_hosts가 필요하다.
PowerShell에서 ./start.ps1을 실행하고 http://127.0.0.1:8791/을 연다.
-Pi5와 -Pi4로 주소를 지정할 수 있으며, Pi4 주소는 화면에서도 변경할 수 있다.
기본 Pi5의 IPv6 %21은 현재 PC의 어댑터 인덱스이므로 환경에 맞춰 지정한다.
-AppId로 다른 설치 버전을 선택할 수 있다. 기본값은 com.atlas.app.deskmate_ui_v27이다.
start.ps1은 입력 도구와 PyYAML 6.0.2를 포함한 센서 이미지를 빌드한다.

## 통합 범위

- PR78 1d64956: 센서·키스트로크·추론·명령 근거·ToF 화면, 프로필 전송과 카드 accept/reject/undo.
  센서 컨테이너는 호스트 127.0.0.1:8790에만 노출한다. 프록시는 GET /sensors·/data와
  POST /api/profile·/api/feedback만 허용하며, 변경 요청에 Origin·Host·세션 토큰을 확인한다.
  프로필/카드 응답은 원본 화면의 확인 절차 후 전송한다. 일반 실행에 --fake/--act-laptop은 넣지 않는다.
- Pi5 조작은 앱 자체의 권한과 확인 절차를 사용하는 SSH 원격 터치 경로다.
  임의 셸 실행 API나 직접 MQTT 제어 명령을 보내는 웹 API는 제공하지 않는다.
- PR81 8285600의 Session·모델 검증·단일 승인·재전송 방지 코드를 재사용한다.
  Pi5 인증/권한 정책은 그대로 두며 ThinQ Cloud 연결은 별도 노트북 인증 프로세스가 담당한다.

## ThinQ 공기청정기

다른 PowerShell 창에서 `./connect-thinq.ps1`을 실행하고 ThinQ PAT를 숨김 입력한다.
PAT는 이 터미널에서만 입력한다. 브라우저·채팅·명령 인자·환경 변수·파일에 넣지 않는다.
공식 SDK `thinqconnect==1.0.14`를 사용하며 인증 프로세스 메모리에서 20분 후 종료한다.
Docker 호스트 포트는 `127.0.0.1:8792`만 사용하고 Node는 자격증명 대신 단기 세션 키만 보관한다.

웹 ThinQ 연결 → 인증 세션 연결 → 현재 상태 읽기 → 약풍으로 변경 → 조건 확인/승인 →
API·읽기 결과와 실제 기기 확인 → 자동으로 복원 순서로 조작한다.
지원 대상은 #81의 `AIR_910604_WW / AS183HWWA` 한 대다. 최초 상태는 POWER_ON/CLEAN/AUTO,
각 변경 승인은 30초, LOW와 AUTO 각 1회다. 응답 유실·상태 불일치 때 재전송하거나 자동 복구하지 않는다.
실제 기기에서 결과를 확인해야 한다. 세션 종료 버튼이나 인증 터미널 Ctrl+C로 인증을 종료한다.
다시 실행하면 새 세션이므로 기기 상태를 먼저 확인한다.

## 접근 범위

웹 서버는 `127.0.0.1`에만 연다. SSH는 기존 호스트 키를 엄격하게 확인한다.
변경 API는 로컬 Origin·Host·세션 토큰을 확인하고, 좌표를 1024×600 안으로 제한한다.
사진·키 내용·토큰을 외부 서버로 전송하지 않는다. 화면은 현재 PNG 하나만 Pi5 `/tmp`에 둔다.

## 검증 범위

이전 V26 release 빌드와 Pi5 설치·Start 요청은 성공했다. V27은 PR83 최신 변경을 통합한 소스다. PR84 최초 통합 시 테스트·정적 분석·화면 조작 검증은
사용자 요청으로 생략했었다. 이후 호스트 검증은 아래 2026-11-04 절을 참고한다. Pi4 주소가 설정되지 않아 실제 센서/프로필/카드 응답 교환은 미검증이다.
실제 PAT를 입력하지 않아 ThinQ Cloud 제어도 미검증이다. 과거 버전의 시험 결과를 현재 결과로 간주하지 않는다.

재현할 검사: node --test ./server.test.mjs, Python unittest test_thinq_host.py 및
hub/atlas-tof/pc/devview/test_devview.py. Atlas 앱 검사는 해당 Docker 환경에서 수행한다.


## 2026-11-04: 양방향 ThinQ 호스트 준비 (보드 미배포)

기준은 main `27b8fc51996fcd491976fd4495dac0f9b02e1be3`이다. PR106 화면을 보존하고 PR107의 Hub 되돌림을 기준으로 한다. PR105를 다시 적용하지 않았다.

- 센서 탭의 기존 iframe 위에 ThinQ 별도 출처 카드와 공기청정기 명령 관찰 카드를 추가했다. 오판·개인화·ToF·FSM UI를 교체하지 않는다.
- PM2는 확인된 API 필드명으로 표시한다. PM2.5 의미와 단위를 임의 확정하지 않는다. `connection=unknown`을 offline으로 표시하지 않는다.
- `read`만 Cloud GET을 요청한다. 연결 버튼은 인증 세션만 연결한다. 화면의 주기 `snapshot` 조회는 인증 프로세스의 메모리 읽기이며 Cloud 자동 폴링이 아니다.
- default 인증 프로세스는 read-only. 실제 제어 시험 별도 승인 후에만 `connect-thinq.ps1 -AllowControl`을 사용한다. PAT는 기존 터미널 getpass로만 입력한다.
- 실패/응답 유실/상태 불일치 때 제어 버튼 잠금을 유지한다. 새 GET이나 finally 처리가 잠금을 풀지 않는다. API 접수·GET readback·물리 확인은 별개다.
- `observation`은 기존 ApplianceCache용 JSON을 내보낼 뿐 실제 MQTT publish를 하지 않는다. 실제 발행은 승인된 인증/TLS/ACL 전송기가 필요하다.
- devview의 명령 관찰은 POST/결과 발행/자동 승인을 절대 수행하지 않는다. `gate=suggest`만으로 Pi5 사용자 승인이 증명되지 않는다.

### 하드웨어 없는 Fixture 실행

새 임시 디렉터리로 복사한 소스에서 기존 검증 Python3.12 환경(aiohttp/PyYAML 등)과 Node20+를 사용한다. 기존 런타임을 업그레이드하지 않는다. 아래는 WSL의 독립 Fixture 실행이며 PowerShell/Docker 실기 검증을 대신하지 않는다.

```sh
# 터미널1: 기존 개발자 화면의 가짜 센서, 쓰기 기능 OFF
python hub/atlas-tof/pc/devview/devview.py --fake --read-only --port 18790
# 터미널2: PAT/실제 SDK/Cloud가 없는 Fixture 인증 프로세스
python tools/laptop-control/thinq_host.py --fixture --allow-control --port 18792
# 터미널3: 실제 Pi5 SSH를 생성하지 않는 Fixture HTML
node tools/laptop-control/server.mjs --fixture true --port 18791 --sensor-fixture-port 18790 --thinq-port 18792
```

http://127.0.0.1:18791 에서 테스트 라벨을 확인하고 ThinQ 세션 연결→단발 조회→Fake LOW/AUTO 승인을 시험한다. `--allow-control`은 여기서는 Fake SDK만 허용하며 실제 물리 동작 검증이 아니다. 종료는 각 Fixture 프로세스 Ctrl+C. 실제 보드 프로세스를 종료하지 않는다.

검증/남은 조건은 `THINQ-INTEGRATION.md` 참조. 실제 Cloud GET·POST·운영 MQTT·보드 배포는 별도 승인 전 금지.


## 노트북 전용 Live Read-only

Pi5 SSH 없이 실제 센서와 수동 Cloud GET을 확인하는 모드 및 Fixture 분리 실행은 [LIVE-READ-ONLY.md](LIVE-READ-ONLY.md)를 따른다. Live에서 제어·MQTT 발행은 금지한다.

## 공기청정기 정책 V1.1 (2026-11-05)

기존 FSM 재실·세션 입력을 사용하는 독립 정책과 HTML 제안·Mock 수락/거절은
[AIR-PURIFIER-V1.1.md](AIR-PURIFIER-V1.1.md)를 따른다. 정책은 현재 노트북 shadow 실행이며
운영 Pi 4 배포·실제 POST·자동 전원 제어는 활성화하지 않았다.

## 개별 승인 기반 수동 제어

[CLI 시험](MANUAL-CONTROL.md)과 [별도 수동 HTML](MANUAL-HTML.md)은 기존 Session·GuardedSdk를
재사용한다. 사용자가 실제 LOW/AUTO 동작을 확인했으며 Live Read-only·Fixture와 분리한다.
V1.1 정책 실행·운영 MQTT 제어·전원 ON/OFF는 이 실기 성공 범위에 포함하지 않는다.
