# 노트북에서 DESKMATE 보기·조작

Pi5 DESKMATE V26 화면을 받아 클릭·드래그·휠 스크롤을 전달한다. 화면은 PNG 방식이며
문자 입력은 앱의 가상 키보드를 사용한다. 키 내용은 수집하거나 전달하지 않는다.

## 실행

Node.js 20+, OpenSSH, Docker, 기존 deskmate-atlas-dev:local 이미지와 신뢰한 Pi5 known_hosts가 필요하다.
PowerShell에서 ./start.ps1을 실행하고 http://127.0.0.1:8791/을 연다.
-Pi5와 -Pi4로 주소를 지정할 수 있으며, Pi4 주소는 화면에서도 변경할 수 있다.
기본 Pi5의 IPv6 %21은 현재 PC의 어댑터 인덱스이므로 환경에 맞춰 지정한다.
-AppId로 다른 설치 버전을 선택할 수 있다. 기본값은 com.atlas.app.deskmate_ui_v26이다.
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

V26 release 빌드와 Pi5 설치·Start 요청은 성공했다. 현재 통합본의 테스트·정적 분석·화면 조작 검증은
사용자 요청으로 생략했다. Pi4 주소가 설정되지 않아 실제 센서/프로필/카드 응답 교환은 미검증이다.
실제 PAT를 입력하지 않아 ThinQ Cloud 제어도 미검증이다. 과거 버전의 시험 결과를 현재 결과로 간주하지 않는다.

재현할 검사: node --test ./server.test.mjs, Python unittest test_thinq_host.py 및
hub/atlas-tof/pc/devview/test_devview.py. Atlas 앱 검사는 해당 Docker 환경에서 수행한다.
