# AS183HWWA 전원 수동 시험 준비

## 최신 상태 (2026-11-05)

사용자가 실제 HTML에서 LOW/AUTO와 전원 OFF/ON을 각각 승인하고 본체 동작을 확인했다.
후속 V1.1 ON 제안 시험에서도 로컬 결과의 API 접수·별도 GET 상태 일치 및
사용자의 본체 전원 켜짐 확인을 각각 확인했다. 자동 전원 제어는 계속 비활성이다.
아래의 157개 Python 테스트 및 실기 미실시 표기는 최초 전원 구현 시점의 기록이다.
최신 통합 검증과 정책 경계는 [SESSION-ATTACH.md](SESSION-ATTACH.md),
[POLICY-MANUAL-LINK.md](POLICY-MANUAL-LINK.md)를 따른다.

후속 브랜치 `feat/thinq-manual-power`, 기준 PR #110 `d9018f4`.
기존 18891 GET, 19091 LOW/AUTO 및 기존 Fixture 서버는 유지한다.
새 옵션 `--power-control`을 명시한 별도 프로세스에서만 전원을 시험한다.
기존 `Session → DeviceIO → GuardedSdk → Transport`와 30초 일회 승인을 재사용한다.

## 검증 범위

- Fixture Profile은 합성 자료다. 실제 Profile 쓰기 지원을 재확인한 것으로 취급하지 않는다.
- 매 prepare와 POST 직전 GET에서 `operation.airPurifierOperationMode`가 enum이고
  mode r/w 및 value.r/value.w 양쪽에 POWER_ON/POWER_OFF가 있는지 검사한다.
- `set_power off/on`은 정확히 `operation.airPurifierOperationMode: POWER_OFF/POWER_ON`만 쓴다.
  ON에 AUTO나 다른 설정 변경을 포함하지 않는다. 기기 자체의 기동 모드는 별도로 확인한다.
- `connection=unknown`은 유지한다. 성공 GET은 PAT 쓰기 권한이나 본체 온라인의 증거가 아니다.
- 동일 세션에서 OFF/ON 각 1회만 허용한다. 자동 재시도·자동 복원은 없다.
  API 접수·GET 전원 상태 일치·사용자 본체 확인은 독립된 증거다.
- 실패/응답 유실/불일치 후 추가 POST를 잠근다. 다음 단계 전에는 이전 본체 확인이 필요하다.
- 최초 전원 구현은 V1.1과 분리했고, 후속 정책 연결은 별도 opt-in 모드에만 적용했다.
  제안 수락은 승인창 준비까지이며 실제 POST에는 새로운 개별 승인이 필요하다.

## Fixture HTML (실제 Cloud 0회)

두 WSL 터미널에서 새 worktree를 사용한다.

```bash
cd "$HOME/dev/ESWContest_LG/2026ESWContest_smart_TEAMMATE-thinq-manual-power"
~/.local/share/deskmate/thinq-gateway-venv/bin/python \
  tools/laptop-control/thinq_host.py --fixture --allow-control --power-control --port 19192
```

```bash
cd "$HOME/dev/ESWContest_LG/2026ESWContest_smart_TEAMMATE-thinq-manual-power"
node tools/laptop-control/server.mjs --mode fixture --power-control true \
  --port 19191 --thinq-port 19192 --sensor-port 18990
```

18990은 기존 가짜 센서 서버다. 없으면 iframe만 미연결이며 ThinQ Fixture는 독립 실행된다.
주소 `http://127.0.0.1:19191/`. 팝업 → 인증 연결 → 현재 상태 읽기 → 전원 끄기 →
두 조건 체크 → 변경 승인 → Fixture 확인 → 전원 켜기도 별도 승인한다.
Fixture의 본체 확인 버튼은 실제 물리 동작 증거가 아니다.
테스트 세션은 20분 또는 OFF/ON 각 1회 후 끝나므로 다시 시험하려면 본인이 시작한
19192 프로세스만 Ctrl+C로 종료하고 재실행한다. 자동 복원은 하지 않는다.

## 실제 Profile GET 재확인 (POST 불가)

사용자가 신뢰된 WSL 터미널에서 실행한다. PAT는 숨김 입력하고 기존 고정 Client ID를 읽는다.
현재 작업에서는 실제 PAT/Cloud 요청을 사용하지 않았다.

```bash
cd "$HOME/dev/ESWContest_LG/2026ESWContest_smart_TEAMMATE-thinq-manual-power"
~/.local/share/deskmate/thinq-gateway-venv/bin/python \
  tools/laptop-control/manual_control.py --power-control
```

정확히 한 대상 모델 선택, Devices/Profile/State GET 후 종료한다. `--manual`이 없으므로
POST할 수 없다. `power_profile_verified=true`, 상태 ready, 현재 전원을 확인한다.
불일치·인증/네트워크 실패·지원 불명확 시 중단한다. 원본 응답·기기 ID는 출력하지 않는다.

## 실제 OFF/ON: 사용자 별도 승인 후에만

기존 LOW/AUTO CLI/HTML에서 동시에 제어하지 않는다. 본체 온라인·현재 전원·외부 조작/예약
없음을 직접 확인한다. 제어용 PAT의 쓰기 권한은 실제 POST 성공 전까지 미검증이다.

```bash
~/.local/share/deskmate/thinq-gateway-venv/bin/python \
  tools/laptop-control/manual_control.py --power-control --manual
```

1. 현재 POWER_ON과 Profile 지원을 확인한 뒤 `set_power off` 입력.
2. 별도 승인 시 `APPROVE AS183HWWA POWER_OFF ONLINE NO OTHER CONTROL` 입력.
3. POST 1회 접수와 별도 GET POWER_OFF 일치를 확인한다. 실패하면 중단하며 재전송하지 않는다.
4. 직접 본체 전원 꺼짐 확인 후에만 `PHYSICAL POWER_OFF CONFIRMED` 입력.
5. ON은 새 승인이다. `set_power on`과 `APPROVE AS183HWWA POWER_ON ONLINE NO OTHER CONTROL`.
6. API 접수/GET POWER_ON/본체 켜짐을 각각 확인하고 `PHYSICAL POWER_ON CONFIRMED`.
7. 기동 후 풍량 모드는 관찰만 한다. AUTO 복원·전원 복원은 자동 실행하지 않는다.

HTML 실기는 별도 승인 후 다음 두 프로세스로 준비할 수 있다.

```bash
~/.local/share/deskmate/thinq-gateway-venv/bin/python \
  tools/laptop-control/thinq_host.py --live-manual --power-control --port 19292
# 별도 WSL 터미널, 같은 새 worktree에서
node tools/laptop-control/server.mjs --mode live-manual --power-control true \
  --port 19291 --thinq-port 19292 --sensor-port 18890
```

`http://127.0.0.1:19291/`은 기존 18891/19091과 독립이다. 각 전원 버튼 → 두 조건 확인 →
별도 변경 승인 → API/GET 결과 → 직접 본체 확인 순서다. 시작만으로 Cloud를 호출하지 않는다.
시험 종료 시 본인이 실행한 새 프로세스만 Ctrl+C. 이미 실행한 명령을 취소하거나 복원하지 않는다.
보드 앱·Hub·MQTT·정책 변경과 배포는 필요 없다.

## 이번 호스트 검증

- Python: laptop-control 전체와 기존 ThinQ Adapter / DeviceGateway / 통합 안전성 테스트
  157 passed. 기존 LOW/AUTO, V1.1 Mock, 권한·중복·만료 차단 포함.
- Node/HTML: 23 passed. 전원 옵션 일치, 별도 승인, 이중 클릭 전송 차단 포함.
- 실제 Chrome + localhost 합성 SDK: 팝업 열기/닫기, OFF→ON→LOW→AUTO,
  각 동작 별도 승인, GET readback 및 Fixture 확인 표시 통과.
- 실제 Profile 재조회, 전원 POST, 실제 본체 OFF/ON은 미실시.
  Fixture 성공은 실제 기기 전원 제어 지원이나 물리 동작 성공을 입증하지 않는다.
