# DESKMATE 문서

문서는 역할이 겹치지 않도록 계약, 현재 계획, 운영 참고로 나눈다. 과거 진행 이력과
초안 계획은 별도로 보관하지 않으며, 현재 상태는 항상 로드맵에 반영한다.

## 먼저 읽을 문서

| 문서 | 용도 |
|---|---|
| [`agent-briefing.md`](agent-briefing.md) | 작업 전 프로젝트 요약, 확정·미결정 사항, 안전 제약 |
| [`roadmap.md`](roadmap.md) | 현재 구현 상태, 결선 간극, 주간 우선순위 |
| [`development-rules.md`](development-rules.md) | 협업·Git·검증 규칙 |
| [`plan/next-development-plan.md`](plan/next-development-plan.md) | 실기 없이 하는 다음 작업 W1~W7 과 Codex 프롬프트(10-01) |
| [`plan/app-connection-robustness-plan.md`](plan/app-connection-robustness-plan.md) | 앱 연결 안정화 0~3단계 계획(완료, PR #30~#32) |

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
| [`ilink-bluetooth-lamp.md`](ilink-bluetooth-lamp.md) | iLink BLE 조명 프로토콜·상태 피드백·PC 연결 절차 |
| [`submission.md`](submission.md) | 결선 제출물·마감·시연 규칙 |
| [`demo-scenario.md`](demo-scenario.md) | 5분 시연 동선·리허설 명령·무대 전 체크 |
| [`security-privacy.md`](security-privacy.md) | 보고서용 보안·프라이버시 절 초안(수집하지 않는 것·온디바이스·저장 위치·남은 위험) |
| [`dataset-survey.md`](dataset-survey.md) | 2단계 학습용 공개 데이터셋 조사 |

계약을 바꾸는 변경은 해당 문서와 구현·테스트를 같은 커밋에서 함께 갱신한다.
