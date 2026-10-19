# ThinQ Connect read-only probe

이 도구는 공식 `thinqconnect` Python SDK로 기기 목록, 기기 프로필, 현재 상태만 조회한다.
제어 API는 의도적으로 제공하지 않는다. DESKMATE 제어 어댑터와 정책이 확정되기 전 실제 가전을
잘못 조작하지 않기 위해서다.

## PAT 준비

1. [LG Smart Solution API Developer](https://smartsolution.developer.lge.com/)에 LG ThinQ 계정으로 로그인한다.
2. **Cloud Developer → Docs → ThinQ Connect → PAT (Personal Access Token)**에서 PAT를 발급한다.
3. 계정 국가가 대한민국이면 country code는 `KR`을 사용한다.
4. 클라이언트마다 고정된 UUID v4 하나를 사용한다. 실행마다 새 ID를 만들면 API 호출이 차단될 수 있다.

PAT를 채팅, Git, 명령행 인자에 붙여 넣지 않는다. 가장 안전한 기본 방식은 도구가 표시하는 숨김
프롬프트에 직접 입력하는 것이다. 자동화가 필요하면 gitignore된 로컬 환경 파일이나 현재 셸의
`THINQ_PAT` 환경변수를 사용하고 작업 후 지운다.

```bash
python -m venv .venv-thinq
source .venv-thinq/bin/activate
python -m pip install thinqconnect

export THINQ_COUNTRY=KR
export THINQ_CLIENT_ID='<한 번 생성해 계속 사용할 UUID v4>'
python -m tools.thinq_connect.probe list
python -m tools.thinq_connect.probe list --show-device-id
python -m tools.thinq_connect.probe inspect '<deviceId>'
```

공식 SDK에서 사용하는 읽기 API는 다음과 같다.

- `async_get_device_list()` → `GET /devices`
- `async_get_device_profile(device_id)` → `GET /devices/{deviceId}/profile`
- `async_get_device_status(device_id)` → `GET /devices/{deviceId}/state`

프로필의 writable property와 제어 옵션이 그 모델에서 실제 허용하는 기능이다. 제품 종류만 보고
전원, 목표 온도, 운전 모드가 모두 가능하다고 가정하면 안 된다.

PAT 없이 구조를 확인할 때는 fixture를 사용한다.

```bash
python -m tools.thinq_connect.probe --mock-file /tmp/thinq-devices.json list
```

fixture root는 `devices`, `profiles`, `states` 키를 가진 JSON 객체다.

## DESKMATE 연결 경계

Pi 4에 설치된 ATLAS `ThinqClient1` D-Bus 서비스는 보드 등록과 cloud message 전송 인터페이스다.
현재 확인된 인터페이스만으로는 개인 ThinQ 계정의 `GET /devices` 및 가전 프로필/제어 API와 같은
계약임을 입증할 수 없다. 실제 가전 조회는 우선 공식 ThinQ Connect SDK로 검증한다.

향후 어댑터는 `deskmate/control/cmd`를 구독하고, 실제 성공/실패를
`deskmate/control/result`로 돌려준다. 온도·습도·CO2·조도에서 명령을 결정하는 정책은 이 API
어댑터 밖에 두며 기존 FSM 임계값과 섞지 않는다. 조명은 ThinQ 프로필에서 지원이 확인되기 전까지
기존 BLE 램프 경로를 유지한다.


보안 보완(2026-10-19): CLI는 SDK 예외 본문/traceback을 출력하지 않는다. 출력의 alias,
계정 식별자, serial/MAC/SSID, credential 필드는 재귀적으로 가린다. 기기 ID는 기본 마스킹하며
`list --show-device-id`는 로컬 선택을 위한 명시적 예외다. 이 출력도 저장소에 커밋하지 않는다.
알 수 없는 프로필의 자유 형식 필드까지 개인정보가 없다고 보장할 수 없으므로 실제 조회 결과는
공유 전 별도 검토해야 한다. 이번 테스트에는 합성 기기/프로필만 사용했다.
