# collector — PC 키스트로크 타이밍 수집기

담당: 박소연 *(역할 조정: 기존 최민경 → 박소연. 상위 문서 `CLAUDE.md`/`README.md` 표는 팀 확인 후 갱신)*

키보드 작업 중에만 가용한 **정밀 신호**를 수집한다. 노트북(Windows)에서 돌며
특징 벡터를 계산해 MQTT 로 발행하고, Raspberry Pi 4(hub)가 구독한다.

## 프라이버시 원칙 (타협 불가)

- **키 값을 절대 수집하지 않는다.** 입력 타임스탬프만 추출한다.
- 저장·전송하는 것은 통계 특징뿐이다. 원시 타임스탬프 시퀀스도 로컬에만 둔다.
- 마우스도 좌표·클릭 버튼·스크롤 내용을 보지 않고 **활동량만** 센다.
- 이 원칙이 본 작품의 "비침습 프라이빗 센싱" 차별점의 근거다. 코드 리뷰에서 특히 엄격히 본다.

## 추출 특징

| 특징 | 설명 |
|---|---|
| dwell time | 키 누름 유지 시간 (평균·표준편차) |
| flight time | 키 간격 (평균·표준편차) |
| idle ratio | 입력 공백 비율 |
| correction rate | 백스페이스 빈도 |

피로 시 리듬이 느려지고 불규칙해지며 정정이 느는 경향을 포착한다.
`auto-repeat`(키 꾹 누름)는 한 번의 타건으로 취급해 통계 왜곡을 막는다.

### additive 신호 (규약 추가분 — 이민혁·`docs/mqtt-topics.md` 확정 대기)
`typing_active` / `mouse_active` / `input_active` / `flight_cv` / `mouse_event_rate`.
`input_active` 는 **"마우스 작업(입력 O) vs 글 읽기(입력 전무)"** 를 PC 단에서 구분해
hub 가 비타이핑 작업과 유휴를 가르는 데 쓴다.

## 발행

`deskmate/sensor/keystroke` 로 1Hz, 60초 윈도우 통계 (QoS 0).
생존 신호는 `deskmate/health/<node>`. 스키마는 [`docs/mqtt-topics.md`](../docs/mqtt-topics.md) 참조.

```json
{
  "ts": 1769000001.0, "node": "pc-collector", "window_s": 60,
  "dwell_mean_ms": 92.4, "dwell_std_ms": 21.8,
  "flight_mean_ms": 148.2, "flight_std_ms": 63.5,
  "idle_ratio": 0.18, "correction_rate": 0.07,
  "typing_active": true, "mouse_active": true, "input_active": true,
  "flight_cv": 0.42, "mouse_event_rate": 96.0
}
```

## 실행

```bash
pip install -r requirements.txt

# Pi4(hub) 연동
python -m collector --broker <pi4-ip>

# 노트북 단독(브로커 없이 로컬 로깅으로 검증). 저장소 루트에서 실행
python -m collector

# 테스트 (실제 키 캡처 없이 로직 검증)
pytest collector/tests -v
```

옵션: `--port`, `--node`, `--window`, `--period`. 환경변수(`DESKMATE_BROKER` 등)로도 지정 가능.
브로커가 없거나 끊겨도 로컬 `logs/keystroke.jsonl` 로 계속 기록한다(8월 튜닝용 원천 로그).

## 주의

- OS 별 키 훅 권한: Windows 는 관리자 권한 없이 `pynput` 리스닝 가능. Linux 는 input group/X11.
- 전역 키/마우스 후킹은 백신·SmartScreen 이 키로거로 의심할 수 있어 데모 PC 에서 예외 처리.

## 구조

```
collector/
├── __main__.py     python -m collector 엔트리 (argparse)
├── config.py       기본값 ← 환경변수 ← CLI 인자
├── capture.py      pynput 키보드+마우스 → 이벤트(시간·종류만)
├── features.py     슬라이딩 윈도 → 특징 → 규약 페이로드(to_payload)
├── publisher.py    paho-mqtt 발행 + 미연결 시 로컬 폴백 + health(LWT)
└── tests/          합성 이벤트 단위 테스트
```
