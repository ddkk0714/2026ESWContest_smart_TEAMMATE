# docs

| 문서 | 내용 | 담당 |
|---|---|---|
| [`agent-briefing.md`](agent-briefing.md) | AI 개발 참고 브리핑 · 확정/미결정 사항 · 프로젝트 제약 | 공통 |
| [`agent-kickoff-prompt.md`](agent-kickoff-prompt.md) | CLI 세션 시작 문구 예시 | 공통 |
| [`architecture.md`](architecture.md) | 계층 구조 · 신호 층위 · 성능 목표 · 리스크 | 공통 |
| [`development-progress.md`](development-progress.md) | 제품 방향 · 분야별 현황 · 우선순위 · 결정 필요 항목 | 공통 |
| [`requirements-spec.md`](requirements-spec.md) | MVP 기능·비기능 요구사항 · 수용 기준 | 조명희 |
| [`data-spec.md`](data-spec.md) | 센서 특징 · 단위 · 유효성 · 보정 · 융합 계약 | 조명희 · 김태환 |
| [`mqtt-topics.md`](mqtt-topics.md) | MQTT 토픽 · 페이로드 스키마 (모듈 간 계약) | 이민혁 |
| [`fsm-spec.md`](fsm-spec.md) | 규칙 기반 FSM 상태 · 전이 · 임계값 · 신뢰도 공식 | 박소연 |
| [`fsm-dev-plan.md`](fsm-dev-plan.md) | FSM 추론 엔진 구현 계획 | 박소연 |
| [`hardware.md`](hardware.md) | HW 구성 · 센서 · BOM · 장비 수령 체크리스트 | 김태환 |
| [`submission.md`](submission.md) | 결선 제출물 목록 · 마감 · 네이밍 규칙 | 공통 |
| `plan/` | 개발계획서 원본 | — |

## 규칙

`requirements-spec.md`, `data-spec.md`, `mqtt-topics.md`, `fsm-spec.md` 는 **모듈 간 계약**이다.
구현을 바꾸기 전에 문서를 먼저 바꾸고, 같은 PR 에 포함한다.

AI CLI(Claude Code · Codex)로 개발한다면 세션 시작 시
[`agent-kickoff-prompt.md`](agent-kickoff-prompt.md) 의 예시 문구를 넣고 [`agent-briefing.md`](agent-briefing.md) 를 읽히면 편하다.
`agent-briefing.md` 는 프로젝트 확정·미결정 사항과 제약을 모아둔 **참고 자료**다. 개발 방식은 각자 자유이며, 확정 사항이 바뀌면 이 문서를 고친다.
