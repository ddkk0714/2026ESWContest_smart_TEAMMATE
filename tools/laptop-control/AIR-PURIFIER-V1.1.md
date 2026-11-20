# 공기청정기 V1.1 — FSM 입력 재사용과 HTML Mock 제안

기준일: 2026-11-05. 기존 `feat/thinq-laptop-gateway`의 미커밋 구현에 추가했다.
운영 보드 배포·설정 변경, Cloud GET/POST, 자동 전원 제어는 이번 구현에서 수행하지 않는다.

## 구현 경로

```text
기존 Pi 4 state/phase (frame.present, fsm_state, reasons, boot_id, seq)
 → 노트북 read-only devview의 PhaseFeed
 → Node의 전용 로컬 policy 조회
 → Python 인증 세션의 AirPurifierPolicy + 기존 GET snapshot
 → HTML 판단 이유·후보 표시

명시적 Fixture 시간축
 → 같은 AirPurifierPolicy → 사용자 Mock 수락/거절 → Mock 결과 표시
```

- 정책 원본: `hub/deskmate_hub/control/air_purifier_policy.py`.
- 임계값: `hub/deskmate_hub/config/air_purifier_policy.yaml`.
  제한 Python에서 추가 의존성 없이 읽을 수 있는 JSON 호환 YAML이다.
- **현재 정책 실행 위치는 노트북 shadow 경로다.** Pi 4에서 재사용할 소스는 마련했지만
  운영 `LiveHub.tick_once`에 연결하거나 설치 Native ELF에 배포하지 않았다.
- 기존 `ControlDispatcher`의 단일 에피소드·개인화·밴딧·FSM 점수·센서 입력은 변경하지 않는다.
- 신규 MQTT 토픽을 만들지 않는다. 제안·Mock 응답은 인증된 localhost HTTP 세션 내부에서만 다룬다.
  `control/cmd`, `control/result`를 운영 브로커에 발행하지 않는다.

## 판단과 안전 경계

| 조건 | 처리 |
| --- | --- |
| 실제 FSM START를 관측한 세션에서 재실 30초 지속 + 전원 OFF | `set_power: on` 제안. 풍량 AUTO를 함께 설정하지 않음 |
| 활성 세션 이후 부재로 FSM이 IDLE 전환하고 `absent_timeout` 보고 + 전원 ON | `set_power: off` 제안. 별도 재실 판정·10분 타이머를 만들지 않음 |
| 재실·활성 세션, 전원 ON·LOW, 검증된 BAD API 보고 지속 | `set_mode: auto` 제안 |
| 이미 AUTO | 유지 |
| Hub/ThinQ stale·결측, 연결 미확인, 미검증 풍량·공기질 등급 | 관련 후보 차단 |
| GET으로 전원·풍량의 외부 변경 관측 | 수동 조작 우선 hold, 기존 후보 무효화 |
| 재실 순간 소실, 세션·부팅 변경, 입력 단절, 후보 만료 | 재검사·무효화. 재연결 중간 상태를 새 세션으로 추정하지 않음 |

현재 설정의 공기질 지속 시간 60초는 **Fixture/Mock용 초기값**이다. 실제 정책 임계값 확정은 별도 검토한다.
서로 다른 API 수신 2회 이상을 요구하지만, 그것이 독립적인 센서 측정 2회라는 뜻은 아니다.
현재 확인 근거가 있는 `BAD`만 악화 등급으로 사용하고 CO₂를 풍량 판단에 사용하지 않는다.

Hub 갱신과 지속 시간은 노트북 monotonic 수신 시간을 사용한다. 중복·역순·retained 메시지로
수신 시각을 연장하지 않는다. 보드 간 시계 차이가 해결됐다는 의미는 아니다.
관측하지 못한 START를 추정하지 않으므로 세션 중간에 노트북을 연결하면 다음 실제 START까지
전원 ON 후보가 나오지 않을 수 있다.

실제 GET snapshot의 `connection`은 계속 `unknown`이다. 성공한 GET을 온라인 확인으로 바꾸지 않는다.
따라서 현재 Live 정책은 연결 미확인 이유로 실행을 차단한다. Fixture의 `online`은 명시적 테스트 입력이며
실제 GET 값이나 기기 연결 확인으로 표시하지 않는다.

수락 직전에 현재 후보 ID·유효기간·상태·수동 hold·데이터 최신성을 다시 검사한다.
수락/거절·만료한 후보는 해당 세션에서 재사용하지 않는다. Mock 결과는 항상
`transport_performed=false`, `state_confirmed=false`, `actual_value=null`이다.

전원 제어는 현재 검증된 실제 ThinQ LOW/AUTO 전송 계약에 추가하지 않았다.
`set_power_off`라는 기존 비가역 operation 분류와 `set_power`/`off` payload를 동치로 취급하지 않는다.
실제 전원 제어에는 별도의 Profile·State·승인·전송 계약 검증이 필요하다.

## HTML에서 확인

[LIVE-READ-ONLY.md](LIVE-READ-ONLY.md)의 WSL Fixture 3개 프로세스를 사용한다.
다른 프로세스가 사용하는 기존 18791/18891을 종료하지 말고 비어 있는 포트를 선택한다.

1. Fixture HTML에서 ThinQ 인증 세션을 연결한다. PAT는 필요 없다.
2. V1.1 카드의 `재실 → ON`, `부재 IDLE → OFF`, `BAD 지속 → AUTO` 중 하나를 선택한다.
3. 후보의 operation/value·판단 이유·Fixture 출처를 확인한다.
4. `Mock 수락` 또는 `거절`을 누른다. 실제 전송·상태 확인이 없음을 확인한다.
5. 기존 Fake LOW/AUTO 승인 팝업과 GET 카드가 계속 동작하는지 확인한다.

Fixture 시간축 버튼은 기존 Cloud snapshot·센서 카드 값을 바꾸지 않는다.
Live Read-only에서는 이 버튼들을 숨기고 수락/거절을 차단한다. 정책 화면 주기 조회는
메모리·로컬 Hub 수신 상태만 읽으며 Cloud 자동 폴링을 추가하지 않는다.
이번 파일 변경은 이미 떠 있는 Node/Python 프로세스에 자동 반영되지 않는다.
반영하려면 사용자가 실행한 해당 노트북 프로세스만 종료·재실행하고 브라우저를 새로고침한다.

WSL 전체 저장소에서 정책 소스를 읽는다. 기존 소규모 Docker 이미지에는 Hub 정책 소스·설정이
없으므로 정책은 `policy_sources_not_packaged`로 차단하고 기존 GET은 유지한다.
현재 V1.1 시험은 WSL 실행 경로를 사용하며 Docker 보완·보드 패키징은 별도 단계다.

## 현재 보드와의 대조 및 남은 단계

- 신뢰된 기존 호스트 키로 Pi 5 SSH 및 Pi 5 경유 Pi 4 SSH에 연결했다.
- Pi 4 Hub·Python·Mosquitto·ToF 프로세스를 확인했다.
- `state/phase` 구독에서 최초 retained 메시지 후 새 시퀀스 405→406 증가를 확인했다.
  이 구독은 판단·제어 명령을 발행하지 않았다.
- 추가로 임시 노트북 SSH 터널과 실제 devview MQTT 수신 함수를 사용해
  새 `PhaseFeed`→`AirPurifierPolicy`까지 시퀀스 547→548 및 재실 true→false 입력을 확인했다.
  retained 1개는 무시됐으며, ThinQ 관측 입력을 공급하지 않아 `thinq_missing_or_stale`로
  후보가 차단됐다. 실제 control/cmd 수신·실행 또는 Pi 4 내부 정책 실행을 검증한 것은 아니다.
- 설치 ELF의 Python ZIP을 메모리에서 읽었다. 브랜치 HEAD와 공통 파일 AST 비교에서
  `demo_mode.py`, `ingest/cache.py`, `ingest/mapping.py`, `ingest/tof_service.py`,
  `live.py`, `personalize/record.py`가 달랐다. 설치본은 개인화·자세 보정 관련 로컬 동작을 포함한다.
  이 파일들을 로컬 main 버전으로 덮어쓰지 않는다.
- 기존 `frame.present` 발행과 FSM의 `absent_timeout` 동작은 확인했지만, 실제 사람이
  30초 재실/10분 부재하여 새 정책 후보를 생성하는 실기 시험은 아직 수행하지 않았다.

다음 실기 순서는 기존 센서·FSM 갱신 확인 → 노트북 shadow 이유 확인 → Fixture Mock 화면 확인이다.
이후 실제 전원 Profile 지원, 연결 확인 방법, MQTT 인증·ACL, 명령 시간 예산과 결과 계약을
검토한 뒤 별도 승인된 수동 제어를 준비한다. 실제 POST·자동 전원·운영 Hub 배포는 계속 보류한다.

## 호스트 검증 기록

- 선택 회귀 **328 passed**: V1.1 17개, 기존 ThinQ GET·승인·읽기 전용 경계,
  localhost MQTT Mock E2E, FSM 전이·부재, 환경 정책, Gateway·복구, 개인화 정책,
  센서 매핑·presentation 및 devview 테스트를 포함한다. Hub 전체 시험 수치로 표현하지 않는다.
- Node/HTML **18 passed**: 기존 팝업·센서 화면·모드 분리와 새 정책 버튼·로컬 인증·Live 차단을 확인했다.
- 실제 Node/Python Fixture 서버를 임시 localhost 포트에서 실행해 ON/OFF/AUTO 후보 생성·Mock 수락 결과를 확인했다.
  기존 GET snapshot 유지, 실제 전송·상태 확인 없음, 테스트 자식 프로세스 종료를 확인했다.
- 실제 브라우저 화면의 새 V1.1 카드 수동 조작과 Pi 5 표시, 실제 전원·풍량 제어는 미검증이다.

새 정책만 재현할 때:

```bash
PYTHONPATH=tools/laptop-control:hub:hub/tests \
  "$HOME/.local/share/deskmate/thinq-gateway-venv/bin/python" \
  -m pytest -q tools/laptop-control/test_air_purifier_policy.py
node --test tools/laptop-control/server.test.mjs tools/laptop-control/thinq-ui.test.mjs
```
