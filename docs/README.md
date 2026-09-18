# DESKMATE 문서

문서는 역할이 겹치지 않도록 계약, 현재 계획, 운영 참고로 나눈다. 과거 진행 이력과
초안 계획은 별도로 보관하지 않으며, 현재 상태는 항상 로드맵에 반영한다.

## 먼저 읽을 문서

| 문서 | 용도 |
|---|---|
| [`agent-briefing.md`](agent-briefing.md) | 작업 전 프로젝트 요약, 확정·미결정 사항, 안전 제약 |
| [`roadmap.md`](roadmap.md) | 현재 구현 상태, 결선 간극, 주간 우선순위 |
| [`development-rules.md`](development-rules.md) | 협업·Git·검증 규칙 |

## 구현 계약

| 문서 | 기준 범위 |
|---|---|
| [`requirements-spec.md`](requirements-spec.md) | MVP 기능·비기능 요구사항과 수용 기준 |
| [`data-spec.md`](data-spec.md) | 센서 특징, 유효성·정규화·통합 계약 |
| [`fsm-spec.md`](fsm-spec.md) | FSM 상태·전이·점수·개입 규칙 |
| [`mqtt-topics.md`](mqtt-topics.md) | MQTT topic과 payload 스키마 |

## 장비·배포·제출

| 문서 | 용도 |
|---|---|
| [`hardware.md`](hardware.md) | 장비 역할, 배선, 센서, BOM |
| [`atlas-build-handoff.md`](atlas-build-handoff.md) | Pi 5 Atlas IPK 빌드·실기 배포 점검 |
| [`posture-camera.md`](posture-camera.md) | ESP32-CAM 자세 판정 — 판정 위치, 보드 준비, 캘리브레이션, 진단 |
| [`ilink-bluetooth-lamp.md`](ilink-bluetooth-lamp.md) | iLink BLE 조명 프로토콜·상태 피드백·PC 연결 절차 |
| [`submission.md`](submission.md) | 결선 제출물·마감·시연 규칙 |

계약을 바꾸는 변경은 해당 문서와 구현·테스트를 같은 커밋에서 함께 갱신한다.
