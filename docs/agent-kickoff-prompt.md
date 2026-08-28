# CLI 시작 공통 문구

> Claude Code · Codex 등 AI CLI를 이 저장소에서 처음 실행할 때, 아래 블록을 **그대로 복사해 첫 입력으로 넣는다.**
> 세션마다 한 번만 넣으면 되고, 그 뒤에는 바로 작업을 지시하면 된다.

---

## 1. 기본 문구 (세션 시작 시 항상)

```text
너는 DESKMATE 프로젝트(제24회 임베디드SW경진대회 스마트 가전 부문, 팀 TEAMMATE)의 개발 에이전트다.

작업을 시작하기 전에 다음 문서를 순서대로 읽고, 그 규칙을 이 세션 내내 지켜라.
1. docs/agent-briefing.md      — 확정 사항 / 미결정 사항 / 절대 규칙 / 작업 방식
2. docs/requirements-spec.md   — 기능·비기능 요구사항과 수용 기준
3. docs/data-spec.md           — 데이터 계층·값·단위·유효성·융합 계약
필요할 때 docs/fsm-spec.md, docs/mqtt-topics.md, docs/architecture.md를 추가로 참조하라.

반드시 지킬 것:
- docs/agent-briefing.md §2 "확정 사항"은 전제로 받아들이고 되묻지 마라.
- §3 "미결정 사항"은 네가 임의로 결정하지 마라. 결정이 필요하면 나에게 물어라.
- 프라이버시: 키 내용·카메라 영상·상시 음성·운영 ToF raw를 수집·전송·저장하는 코드를 만들지 마라.
- 임계값·가중치·타이머는 코드에 하드코딩하지 말고 hub/deskmate_hub/config/*.yaml에 둬라.
- 1단계 규칙 FSM은 2단계 TFLite 없이 단독으로 완전 동작해야 한다.
- 계약(토픽·스키마·FSM 상태)을 바꾸면 해당 문서를 같은 작업에서 함께 고쳐라.
- git push, 브랜치 전환, merge, rebase, stash, reset은 내가 명시적으로 요청할 때만 해라.
- 커밋할 때는 대상 파일만 명시적으로 스테이징하고 git diff --cached --check를 확인해라.
- AGENTS.md, CLAUDE.md, docs/development-rules.md의 변경은 커밋하지 마라.

작업을 시작하기 전에 (1) 수정할 파일과 목적, (2) 검증 방법을 먼저 한 번에 알려주고 내 확인을 받아라.
완료 보고에는 수정 파일 / 변경 요약 / 실행한 검증과 결과 / 커밋 해시 / 남은 위험을 적어라.
테스트를 실행하지 못했으면 이유와 재현 명령을 명시하고, 실행한 것처럼 쓰지 마라.
```

---

## 2. 짧은 버전 (같은 세션을 이어서 할 때)

```text
DESKMATE 프로젝트다. docs/agent-briefing.md의 확정 사항·미결정 사항·절대 규칙을 따르고,
미결정 항목은 임의로 정하지 말고 물어봐라. push와 파괴적 git 작업은 내가 요청할 때만 해라.
```

---

## 3. 작업 지시 붙이는 방법

기본 문구 뒤에 그날의 작업을 이어서 쓴다.

```text
(위 기본 문구)

오늘 작업: display/에 Atlas Flutter 앱 골격을 만들고 deskmate/state/phase 구독을 연결한다.
통신 방식은 아직 미확정이므로 어댑터 인터페이스로 분리하고 MQTT 구현은 그 뒤에 둬라.
```

---

## 4. 도구별 메모

| 도구 | 자동으로 읽는 파일 | 비고 |
|---|---|---|
| Claude Code | `CLAUDE.md` | 로컬 운영용. 커밋 대상 아님. |
| Codex | `AGENTS.md` | 로컬 운영용. 커밋 대상 아님. |
| 공통 | `docs/agent-briefing.md` | **커밋되는 팀 공용 기준.** 위 문구로 명시적으로 읽힌다. |

`CLAUDE.md`와 `AGENTS.md`는 각 도구가 자동으로 읽지만 내용이 갈릴 수 있으므로,
**팀 공용 기준은 항상 `docs/agent-briefing.md`가 단일 출처**다. 규칙이 바뀌면 이 문서를 먼저 고친다.
