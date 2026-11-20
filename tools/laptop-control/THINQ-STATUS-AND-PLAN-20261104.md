# DESKMATE ThinQ 진행 현황과 후속 계획

작성일: 2026-11-04. 현재 하드웨어에 접근할 수 없으며 실기 시험은 다음 장비 연결 후 진행한다.
이 문서는 현재 소스 확인, 기존 호스트 검증 기록, 대화에서 사용자가 확인한 실기 결과를 구분해 정리한다.
실제 PAT, Client ID 값, Device ID, MAC 주소, 원본 API 응답은 포함하지 않는다.

## 1. 현재 결론

- 노트북의 `http://127.0.0.1:18891/`에서 **실제 공기청정기 Cloud GET 데이터 읽기에 성공했다는 사용자 확인**을 받았다.
- 대상은 LG AS183HWWA이며, 검증 코드가 사용하는 API 모델명은 `AIR_910604_WW`다.
- 현재 Live Read-only 화면은 단발 GET 전용이다. 실제 POST, 자동 폴링, 운영 MQTT 자동 발행은 차단한다.
- Fixture 화면은 별도로 유지하며 Fake LOW/AUTO 승인 흐름을 시험할 수 있다.
- ThinQ 관측값을 MQTT 계약으로 변환하고 Hub 별도 캐시·화면용 상태로 전달하는 코드는 호스트에서 구현·검증했다.
- 현재 MQTT 제어 명령 경로는 **관찰 기능**이다. 실제 ThinQ POST를 실행하는 Live Gateway는 아직 연결하지 않았다.
- Pi 4 운영 Native Hub와 Pi 5 기존 운영 UI의 ThinQ 업데이트는 보류한다. 과거 V48 정상 동작은 현재 보드 상태를 보증하지 않는다.
- 최종 제품 목표는 PC 없는 독립 실행이다. 결선 준비에서는 노트북을 임시 Gateway로 사용한다.

## 2. 개발 기준과 원본 보존

| 항목 | 현재 기준 |
| --- | --- |
| 기능 브랜치 | `feat/thinq-laptop-gateway` |
| worktree | `$HOME/dev/ESWContest_LG/2026ESWContest_smart_TEAMMATE-thinq-laptop-gateway` |
| 로컬 HEAD / 브랜치 생성 기준 | `27b8fc51996fcd491976fd4495dac0f9b02e1be3` |
| 현재 구현 | 위 HEAD 이후 미커밋 변경을 포함함. HEAD만으로 현재 구현 전체를 복원할 수 없음 |
| 원격 상태 | 이번 문서 작성에서는 최신 원격 main을 조회하지 않음 |
| UI 호환 기준 | PR #106 개발자 화면 보존, PR #107의 Hub 되돌림 유지. PR #105를 임의 복원하지 않음 |
| 이전 ThinQ 브리지 | 로컬 커밋 `1ee37c61afad66cc343cd2ca57f05c3611a19922`와 후속 검증본 재사용 |
| Python SDK | 기존 `thinqconnect==1.0.14` 환경 재사용 |

기존 main, 다른 worktree, 운영 보드, 영속 백업을 보존한다. 이 문서 작성으로 커밋·Push·PR 생성은 수행하지 않는다.

## 3. 지금까지의 진행과 검증 수준

| 개발 항목 | 확인된 결과 | 판단 범위와 한계 |
| --- | --- | --- |
| WSL SDK 직접 조회 | 사용자가 공유한 GET 결과에서 Devices/Profile/State HTTP 200, `api_status=ok`, `all_required_fields_match=true` | 당시 실제 API 조회 성공. POST·자동 재시도 OFF. 현재 상태를 대신하지 않음 |
| 과거 수동 풍량 제어 | PR #65 계열 개발에서 AUTO→LOW→AUTO 성공 보고가 있음 | 과거 경로의 보고이며 현재 HTML·MQTT·Pi 5 승인 경로의 물리 제어 성공으로 취급하지 않음 |
| 파싱·유효성 | 기존 Profile/State 검증, 공기질 파서 재사용 | API 수신 최신성과 센서의 실제 측정 최신성을 구분 |
| ThinQ→MQTT→Hub 캐시 | 비식별 Fixture 기반 호스트 시험 기록 있음 | 운영 브로커·실제 Pi 4 Native 배포 검증은 아님 |
| V48 ThinQ 위젯 | 이전 격리 통합본에서 Flutter 360 passed, analyze 이상 없음 기록 | 현재 HTML 브랜치 시험 결과가 아니며 실기 위젯 설치·화면 확인은 미완료 |
| Pi 4 보드 코드 보존·통합 | Python 8개·YAML 7개 보존, 이전 통합본 Hub 953 passed / 2 failed / 3 skipped / 1 xfailed 기록 | 실패 2건은 기존 원본에서도 재현됐다는 이전 기록. Native 소스 대응·복구 문제로 배포 No-Go |
| Pi 5 독립 Native PoC | Flutter v0.2.1 실행·종료 사용자 확인. Native v0.2.2 설치 보고, v0.2.3 호스트 192 passed 및 ARM64 빌드 기록 | UID·D-Bus·신뢰 경계 문제 미해결. Python Fixture 종단 성공은 확인하지 못함. 추가 버전 생성은 현재 우선순위에서 제외 |
| 노트북 HTML Fixture | 팝업·카드·Fake GET/승인/LOW·AUTO 흐름 및 관련 호스트 회귀 시험 진행 | Fixture 결과는 실측·물리 제어 결과가 아님 |
| 현재 HTML 실제 GET | `18891`에서 값 읽기 성공을 사용자 확인 | 실제 Cloud 조회→노트북 HTML 표시 성공. 이번 문서 작성 중 재호출하거나 원본 응답을 수집하지 않음 |
| Client ID 영속화 이후 회귀 | Python unittest 30개 통과, `git diff --check` 통과 | Fixture·Mock·localhost 기반. 별도 pytest MQTT E2E 전체를 이 30개에 포함했다고 해석하지 않음. Ruff는 미설치로 미실행 |

## 4. 현재 구현된 경로와 목표 경로

### 현재 노트북 조회

```text
사용자 터미널의 PAT 숨김 입력
  → 노트북 Python 인증 프로세스(20분, 메모리)
  → 사용자 버튼에 의한 ThinQ SDK GET
  → Profile/State 검증 → ReadSnapshot
  → loopback Node 서버 → HTML ThinQ 카드·팝업

ESP32 → 기존 Pi 4 Hub·MQTT/개발자 HTTP
  → 노트북 read-only devview → 같은 HTML의 센서 화면
```

ThinQ GET 성공과 Pi 4 센서 연결 성공은 독립적으로 판정한다. 두 데이터가 같은 화면에서
동시에 갱신되는지와 수신 시각 대조는 다음 실기에서 재확인한다.

### 양방향 통합 목표

```text
공기청정기 → ThinQ Cloud → 노트북 GET·검증
  → 인증·접근 통제된 MQTT → Pi 4 ApplianceCache
  → 별도 검토한 센서 융합·정책 → Pi 5 표시·제안

Pi 4 제안 → Pi 5 수락/거절 → Pi 4 최종 명령
  → deskmate/control/cmd → 노트북 Live Gateway
  → 승인·기기·명령·만료 검증 → ThinQ POST
  → 별도 GET readback → deskmate/control/result → Pi 4·Pi 5·HTML
```

이 목표에서 아직 연결하지 않은 부분은 MQTT 실데이터 발행, 운영 Hub 캐시 적용,
ThinQ 값을 정책에 사용하는 판단, Pi 5 승인 바인딩, Live POST·결과 회신이다.
현재 캐시는 ESP32 환경 슬롯·FSM 점수·Registry 상태를 변경하지 않는다.

## 5. 코드 지도

아래 경로는 저장소 루트 기준이며, 상세 계약은 [THINQ-INTEGRATION.md](THINQ-INTEGRATION.md)를 따른다.

| 파일·영역 | 구현 역할 |
| --- | --- |
| `tools/laptop-control/index.html` | 기존 Pi 5/센서 탭, ThinQ 팝업·카드, 명령 관찰 카드. 열기·닫기·Esc 동작 복구 |
| `tools/laptop-control/thinq-ui.mjs` | ThinQ 값·유효성·stale·출처 표시, UI 상태 처리 |
| `tools/laptop-control/server.mjs` | HTML 및 Python 인증 프로세스 중계, Fixture/Live 모드 혼합 방지, Live 제어 차단 |
| `tools/laptop-control/thinq_host.py` | PAT 숨김 입력, SDK 전송 경계, 인증 세션, 단발 조회, Live Read-only·Fixture 실행 |
| `tools/laptop-control/thinq_vendor/poc/session.py` | Profile/State 필드 검증, 일회 승인, 만료·전송 직전 재검사, 결과 불확실 시 제어 잠금 |
| `tools/laptop-control/thinq_vendor/tools/thinq_connect/` | PR #65/#81 계열 파서·GET·수동 시험의 재사용 코드 |
| `tools/laptop-control/thinq_bridge.py` | `ReadSnapshot`: 비식별 관측 Envelope 생성. export와 실제 MQTT publish는 별개 |
| `tools/laptop-control/thinq_observer.py` | `CommandObserver`: 공기청정기 명령 검증·관찰. SDK·POST·결과 발행 기능 없음 |
| `hub/deskmate_hub/ingest/appliance.py`, `appliance_display.py` | 독립 ApplianceCache와 화면용 상태 투영 |
| `hub/deskmate_hub/ingest/cache.py`, `mqtt_lines.py`, `mqtt_source.py`, `live.py` | 호스트 Hub 수신·캐시·화면용 메시지 통합 변경 |
| `hub/atlas/src/mqtt_client.*` | Native MQTT 수신 단계의 ThinQ retained 차단 변경안. 보드 미배포 |
| `hub/atlas-tof/pc/devview/devview.py` | 기존 센서 화면과 read-only 수신, MQTT 명령 관찰 연결 |
| `docs/mqtt-topics.md` | 관측 및 명령·결과 계약의 현재 작업 변경 |

재사용 출처와 수정 범위는 [thinq_vendor/SOURCE.md](thinq_vendor/SOURCE.md)에 기록한다.
SDK·비공개 ATLAS 바이너리를 소스로 복제하지 않는다.

## 6. 표시·인증·제어 안전 기준

- PM1, **PM2(API 필드명)**, PM10, 냄새·종합 API 등급, 필터 잔여율, 전원·운전·풍량을 지원 필드에 한해 표시한다.
- PM2를 공식 근거 없이 PM2.5로 바꾸거나 PM 단위를 임의로 붙이지 않는다.
- API 성공 수신 시각은 기기 센서 측정 시각이 아니다. `measurement_freshness=unknown`과 `connection=unknown`을 유지하며, unknown을 offline으로 바꾸지 않는다.
- stale·결측·API 오류 값을 현재 정상값처럼 표시하지 않는다. ThinQ와 ESP32 출처를 분리한다.
- 기존 Client ID는 검색 범위에서 복구하지 못했다. 사용자 승인으로 새 UUIDv4를 한 번 생성해 `~/.config/deskmate-thinq/client-id`에 저장했다.
- 전용 디렉터리 `0700`, 파일 `0600`, 현재 사용자 소유·UUIDv4 형식을 검증했다. Live Read-only는 동일 값을 재사용하고 파일 오류 시 덮어쓰기·재생성 없이 중단한다. 실제 값은 문서에 넣지 않는다.
- PAT는 신뢰된 사용자 터미널에서 getpass로 입력하며 Python 인증 프로세스의 메모리에만 둔다. 종료·만료 후 재입력한다. 메모리는 침해·덤프에 대한 완전한 보호가 아니다.
- PAT·Device ID·Client ID를 HTML·MQTT·로그에 내보내지 않는다. TLS 인증서·SSH 호스트 키 검증을 유지한다.
- Fixture `18790/18791/18792`와 Live `18890/18891/18892`를 분리한다. 실행 절차는 [LIVE-READ-ONLY.md](LIVE-READ-ONLY.md)를 따른다.
- Live Read-only는 POST·LOW/AUTO·observation export·자동 MQTT 발행을 허용하지 않는다. 세션 연결과 화면 snapshot 갱신은 Cloud GET이 아니다.
- 실제 제어 검증 대상은 `airFlow.windStrength`의 LOW/AUTO뿐이다. OFF/MID/HIGH·전원·자동 제어·자동 복원·Undo는 별도 검증 전 활성화하지 않는다.
- POST 접수, GET 상태 일치, 사용자 물리 확인은 서로 다른 결과다. 응답 유실·timeout·불일치 시 자동 POST 재전송을 금지하고 잠금을 유지한다.

## 7. 남은 차단 조건

1. **운영 Hub 배포·복구:** 설치 Native ELF와 소스의 대응 및 복구 경로를 입증하지 못했다. Python만 바꿔 모든 MQTT 라우팅이 동작한다고 가정하지 않는다.
2. **MQTT 보안:** 과거 익명·평문 1883 관측이 있다. 현재 설정은 다음 실기에서 재확인하며, 인증·ACL/TLS 전환이 기존 클라이언트를 끊지 않도록 검증해야 한다. reader는 자기 관측 토픽 write, observer는 명령 토픽 read로 제한한다.
3. **시간:** 과거 Pi 4·Pi 5 시각 차이가 여러 차례 관측됐다. 현재 차이는 미확인이다. TTL을 늘려 회피하지 않고 공식 시간 동기화와 회귀 검증이 필요하다.
4. **승인 신뢰:** `gate=suggest`나 MQTT 명령 수신 자체가 사용자 승인 증명은 아니다. Pi 5의 정확한 request/command 바인딩과 승인 출처·유효기간을 검증해야 한다.
5. **지연·중복:** Hub 명령 15초 시간 예산과 사용자 승인·API GET/POST/readback 지연을 조율해야 한다. 명령 ID 저널, 전송 직전 만료 검사, 중복·retained 차단이 필요하다.
6. **보드 독립 실행:** ATLAS Python·SDK 의존성·계정·D-Bus/AppArmor·PAT 공급·자동 복구가 미해결이다. 노트북 성공은 Pi 4/Pi 5 직접 실행 성공을 증명하지 않는다.

## 8. 앞으로의 작업 순서

### 하드웨어 없이 준비할 사항

1. 현재 기능 브랜치의 diff와 기존 테스트 기록을 보존하고, 공개 전 비밀정보·식별자 검사를 수행한다. 커밋·Push·PR은 사용자 승인 후 진행한다.
2. 기존 Fixture와 오프라인 테스트로 인증 만료·승인 중복·POST 실패 잠금·모드 혼합 차단을 점검한다. 이미 통과한 시험은 변경·실패 근거 없이 반복하지 않는다.
3. 실데이터 MQTT 발행기와 Live Gateway를 새로 활성화하기 전에 데이터 계약·권한·승인 및 timeout 설계를 검토한다. 구현 필요성과 별도 승인 범위를 정한다.
4. 기존 노트북 단발 조회 경로를 결선의 우선 시연 후보로 유지한다. 새 Native Service 반복 제작은 보류한다.

### 다음 하드웨어 연결 후 단계별 시험

| 순서 | 작업 | 성공 판정 / 중단 조건 |
| --- | --- | --- |
| 1 | 신뢰된 SSH 호스트 키와 현재 IP, Pi 4 센서·FSM·MQTT 및 Pi 5 앱 상태 읽기 전용 확인 | 시퀀스·수신 시각·센서값 갱신 확인. 이상 시 ThinQ 배포보다 기존 시스템 진단 우선 |
| 2 | 노트북 Live HTML에서 센서 화면과 ThinQ 단발 GET 동시 확인 | 출처·갱신 시각 구분. 센서 연결 실패와 Cloud 인증 실패를 각각 판정 |
| 3 | 별도 승인된 노트북 수동 제어 시험 | 사용자 승인 AUTO→LOW, GET readback 및 물리 확인, 별도 승인 LOW→AUTO. 불확실 결과면 즉시 중단하고 재전송 금지 |
| 4 | MQTT 보안·시계·운영 설치본 백업과 복구 확인 | 기존 클라이언트 보존·복구를 입증하지 못하면 운영 발행·배포 보류 |
| 5 | 승인 후 Fixture→MQTT→Pi 4 캐시→Pi 5 표시 | Fixture 표시, 정상/stale/결측/unknown 구분, 기존 센서·FSM·ToF·BLE 회귀 확인. 복구 가능한 배포 경로 확보가 선행 조건 |
| 6 | 승인 후 실제 GET 관측값 전달 | 비식별 데이터만 수신하고 유효성·중복·역순·retained 차단 확인. FSM 사용은 별도 검토 |
| 7 | Pi 5 제안·수락→Pi 4 명령→노트북 Mock 결과 종단 확인 | 정확한 ID·승인·만료·대상 검증. 실제 POST 없이 먼저 통과 |
| 8 | 별도 승인 후 제한된 실제 제어 종단 시험 | POST 1회, GET readback, 물리 확인, 결과 회신을 분리 기록. 자동 제어는 계속 OFF |

전체 양방향 경로를 안전하게 완성하지 못하면 기존 센서·FSM·Pi 5 UI를 보존하고,
노트북 HTML의 검증된 실측 조회와 별도 승인된 수동 제어를 독립 시연 경로로 사용한다.

### 결선 이후 독립 제품 계획

Pi 4 중심 실행을 우선 검토하되 확정 배치로 취급하지 않는다. 검증된 파서·Session·MQTT 계약을
재사용하고, ATLAS가 지원하는 런타임·네트워크·계정 신뢰 경계·안전한 PAT 공급 및 자동 복구를
입증한 다음 노트북 Gateway를 대체한다. Pi 5는 UI·사용자 승인 역할을 우선한다.

## 9. 기록 갱신 원칙

- 시험마다 기준 소스, Fixture/Live, 실행 위치, 사용자 승인 범위와 결과를 기록한다.
- 호스트 테스트, 사용자 실기 확인, API readback, 물리 동작 확인을 합쳐서 성공으로 쓰지 않는다.
- 과거 테스트 수치와 보드 버전은 과거 기록으로 남기고, 새 증거 없이 현재 상태로 갱신하지 않는다.
- 이 문서는 진행 요약이다. 상세 실행 명령은 LIVE-READ-ONLY.md, MQTT·제어 계약은 THINQ-INTEGRATION.md와 원본 명세를 우선한다.

이번 문서 작성에서는 하드웨어 접속·테스트·Cloud 요청·운영 MQTT 발행·보드 변경을 수행하지 않았다.

## 후속 갱신 — 2026-11-05

위 내용은 11월 4일 기준 기록이다. 이후 구현·검증은 다음 문서를 우선한다.

- [V1.1 정책·HTML Mock](AIR-PURIFIER-V1.1.md): 기존 FSM 입력을 사용하는 노트북 shadow 경로.
  실제 MQTT 수신을 확인했으나 정책은 Mock 검증이며 운영 Pi 4 배포·자동 제어는 미실시다.
- [CLI 수동 시험](MANUAL-CONTROL.md): 사용자가 LOW/AUTO API 접수·GET 일치·본체 동작을 확인했다.
- [별도 수동 HTML](MANUAL-HTML.md): 사용자가 실제 LOW/AUTO 동작을 확인했다.
  이 결과는 18891 읽기 전용 경로나 V1.1의 실제 제어를 활성화한 것이 아니다.

공개 PR 준비 과정에서는 추가 Cloud GET/POST·보드 배포·제어 활성화를 수행하지 않는다.
