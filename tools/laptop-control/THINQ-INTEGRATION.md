# ThinQ 양방향 통합 계약과 실기 경계

## 실제 구현된 호스트 경로
1. HTML → Node → 기존 Python 인증 프로세스 → 검증 Session. PAT는 Python 메모리에만 남으며 HTML/Node/MQTT에 전달하지 않는다.
2. Session의 Profile 기반 필드 검증 → ReadSnapshot → `observation` export. 기존 공통 Envelope(schema_version/ts/node/boot_id/seq/data)에 appliance_observation schema와 TTL/API수신시각/논리ID를 더한다.
3. topic `deskmate/sensor/appliance/thinq-reader/purifier-one`, qos0, retain=false. 실제 device_id/Client ID/계정은 없음. 재조회·캐시 표시·측정 발생을 구분한다.
4. 격리 main 사본의 MQTT 수신 → 독립 ApplianceCache → 추론이 끝난 뒤 `sensor_summary.appliances` 화면용 투영. 기존 env 슬롯·FSM 점수·자동 제어·Registry 상태에는 넣지 않는다.
5. 최신 devview가 받은 `deskmate/control/cmd`에서 air_purifier만 stdlib CommandObserver로 관찰. 기존 센서 화면과 분리된 카드에 표시한다. 실제 MQTT retained 비트도 보존한다.
6. 임시 localhost에서 실제 Dispatcher SUGGEST→accept→Mock DeviceGateway→control/result까지 검증했다. 모드 Mock이며 ThinQ SDK POST0회. 이 시험을 Pi5 수락·실제 제어 성공으로 해석하지 않는다.

## 실행하지 않는 것
- 현재 Node/인증 프로세스는 observation을 운영 MQTT로 자동 발행하지 않는다. export와 실제 wire 전송은 별도다.
- 실제 MQTT cmd를 Session.execute에 연결하지 않는다. 관찰 클래스에는 SDK/POST/publish 기능이 없다.
- Gateway 서비스는 여전히 Mock 전용. DeviceGateway._check도 live 실행을 허용하지 않는다.
- PM을 FSM 판단에 반영하거나 사용자 승인 없이 제어하는 기능은 없다. 실제 자동 제어 OFF.
- Pi4 Native ELF/Hub 배포와 Pi5 V48 변경 없음. 이전 No-Go 판단 유지.

## 출처/보안
collector.publisher의 Envelope와 paho 패턴은 재사용하되 FeaturePublisher를 그대로 ThinQ용으로 쓰지 않는다. 해당 클래스는 키스트로크용 boot_id/로그/health/fallback을 소유한다. SDK raw 응답을 publish/log에 넘기지 않는다.
collector.discover는 주소 후보 탐색/retained health 식별 도구이며 인증·인가를 증명하지 않는다. 이 작업에서 스캔/운영 연결하지 않았다.
실제 remote broker는 확인한 사용자/CA/TLS/ACL이 필요하다. 기존 익명1883을 안전하다고 가정하거나 localhost 프로필/SSH tunnel로 인증 검증을 위장하지 않는다. 기존 클라이언트 보호 이행 계획은 이전 MQTT-TIME-PLAN.md를 따른다.
reader는 자기 관측 토픽 write만, observer는 control/cmd read만. reader의 control/result/state/feedback 쓰기 금지. Mock 결과 writer는 호스트 임시 브로커에만 사용한다.

## 명령/제어 경계
command_id,target_id,operation,value,gate,requires_confirmation,expires_ts_ms와 Envelope node/schema/ts를 검사한다. 중복·변조 duplicate·retained·만료·시계 역행은 분류/차단한다. 유효한 명령도 observed_only/approval_unverified이므로 POST 권한이 아니다.
허용 제어 mapping은 향후 승인된 set_mode low→airFlow.windStrength LOW, auto→AUTO뿐이다. OFF/MID/HIGH/전원/Undo는 새 Profile 및 안전계약 확인 전 실행하지 않는다.
Hub 명령의15초 타임아웃과 사용자30초 승인, Profile/State GET 및 POST/readback 지연은 서로 다른 시간 예산이다. 현재 observer는 실행하지 않으므로 이 충돌을 숨기지 않는다. 실제 연결 시 만료 직전 재검사/명령-ID 저널/1회 승인/사용자 수동 우선/불확실 결과 잠금이 필요하다. 타임아웃을 임의 늘리거나 만료 명령을 새ID로 자동 재전송하지 않는다.
POST 응답이 사라져도 재전송하지 않는다. 인증 세션 종료나 토큰 폐기가 이미 실행된 명령을 취소하지 않는다.

## PR105/106/107 호환성
PR106의 화면은 보존했다. PR105는 PR107에서 되돌려져 최신 main의 Hub에는 일부 개인화 스위치/오판/보정 처리 차이가 있다. 실제 보드에는 별도8개 Python 변경이 있을 수 있으므로 화면 버튼이 존재하는 것과 수신 Hub가 처리하는 것은 별도 확인한다. 이번에 PR105를 임의 복구하지 않았다.
실기 Hub에 새 센서 topic을 보내기만 해서는 FSM에 자동 반영되지 않는다. 새 캐시/Native retained guard 배포도 기존 No-Go 조건을 해결한 뒤 별도 승인 대상이다.

## 다음 실기 순서
1. 기존 V48·센서·FSM 정상 관찰과 백업. 설치/재시작 없음.
2. 노트북 HTML과 인증 프로세스의 설치된 SDK1.0.14·보안 경계 확인. 실제 GET 전 별도 승인.
3. 단발 GET 후 지원 필드·수신시각·stale 표시·실제 상태 대조. 무제한 폴링 금지.
4. 승인된 독립 수동 경로에서만 AUTO→LOW, GET readback, 사용자 물리 확인, 별도 승인 LOW→AUTO. 자동 복원 없음.
5. 실측 JSON 표시·수동 제어 성공과 Pi4 양방향 연동 성공을 구분해 기록.
6. 실제 bidirectional MQTT는 시간/인증·ACL/TLS/Hub 배포/Live Gateway·Pi5 승인 바인딩/저널이 확보되기 전 보류.
최종 제품의 PC 없는 독립 실행은 이 임시 노트북 Gateway 경로로 증명되지 않는다.
