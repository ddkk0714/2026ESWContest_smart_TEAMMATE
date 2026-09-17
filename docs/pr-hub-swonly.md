# PR: feat/hub-swonly → main

> `gh` 가 PC 에 없어 본문을 여기 둔다. 생성: https://github.com/ddkk0714/2026ESWContest_smart_TEAMMATE/compare/main...feat/hub-swonly?expand=1
> 머지 후 이 파일은 지워도 된다.

**제목**: feat(hub,atlas,firmware): 실보드 기동 — Pi 4 hub IPK · C++ 네이티브 MQTT · 개인 기준선·제어·리포트 · 환경센서 드라이버

## 요약 (9 커밋, 51 파일)

**실보드 (09-16)**
- ESP32 실기 플래시(COM8) → USB JSON 1 Hz → `uart_mqtt_bridge` → 브로커 → `hub run` 체인 확인
- Pi 4 ATLAS 에 hub IPK **첫 빌드·설치·D-Bus 활성화·live FSM 기동** (`hub/atlas/tools/build_ipk.sh`)
- ESP32 → Pi 4 UART2 실수신 rx=132 · discarded 0 · crc_errors 0 (Codex, `docs/codex-handoff-2026-09-16.md`)

**보드 제약 대응 (09-17)**
- 보드 `/restricted/python3` 에 `_socket`·`_random`·`_ssl` 없음 → **MQTT 를 C++ 네이티브로**: `hub/atlas/src/mqtt_client.{h,cpp}`
  (자체 MQTT 3.1.1: CONNECT+LWT · SUBSCRIBE · PUBLISH QoS0/1 retain · keepalive · 재접속). 호스트 테스트 = 패킷 벡터 + 실브로커 왕복
- Python 은 라인만: stdin `MQTT\t<topic>\t<payload>` → `ingest/mqtt_lines.py`; stdout `STATE/REQUEST/REPORT/CMD` → C++ 발행
- `hub.env` 런타임 설정(서비스 디렉터리 > payload 동봉 기본값), `statistics`/`importlib.resources` 의존 제거

**hub 기능**
- `features/baseline.py` 개인 기준선(중앙값·MAD Modified z, 세션 보정 창, 시간대 버킷, opt-in 저장)
- `control/` ACTION_ENV 디스패처(auto 즉시 / suggest 수락 후 / 거절 undo / 쿨다운 / 비가역 자동 금지) + mock 플러그
- 세션 종료 시 `deskmate/session/report`(retain), 10 s 주기 채터링·지연 테스트

**펌웨어**
- C1001 미연결 시 10 s 재시도 + 300 ms 프로브(벤더 begin() 15 s 블로킹 회피)
- 환경 센서 드라이버 SCD41(CO₂만)·BH1750·DHT22, `-DDESKMATE_HAS_*` 스위치, on/off 둘 다 `pio run` 통과. env 프레임 lux 는 §13.1 대로 raw u16

**도구·문서**
- `tools/pc_broker.py`(브로커 배치 결정: PC + 공유기 포트포워딩), Node-RED 제어·리포트 패널(52 노드 실로드), `docs/roadmap.md`·`hub/atlas/README.md`

## 검증
- hub `pytest`: **105 passed, 1 xfailed** (bridge 프로세스 테스트 포함)
- C++: `deskmate_uart_rx_host_test`, `deskmate_mqtt_host_test`(amqtt 왕복) 통과, ARC 크로스 빌드 IPK 생성
- firmware: `pio run` 두 구성 통과, 실기 플래시
- 실보드: Pi 4 `/health` ok · live tick · UART rx (MQTT 실브로커 연결은 포트포워딩 뒤)

## 머지 후
- Codex 의 `feat/edge-mvp-nodered` 추가 커밋 2개(`d55bb37`, `237f531`)는 이 PR 내용의 복사본이라 **머지하지 않는다**
- 메인 폴더 미커밋 파일(Codex 펌웨어·Node-RED)은 이 PR 에 병합돼 있으므로 `git checkout -- .` 로 정리 가능(`Claude outputs/`·mp3 제외)
