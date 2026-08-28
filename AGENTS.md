# DESKMATE 에이전트 작업 규칙

이 저장소에서 작업하는 모든 AI CLI 에이전트(Claude Code · Codex 등)에게 적용된다.

작업을 시작하기 전에 [docs/agent-briefing.md](docs/agent-briefing.md)와 [docs/development-rules.md](docs/development-rules.md)를 읽고 따른다.
**팀 공용 단일 출처는 `docs/agent-briefing.md`**이며, 세션 시작 문구는 [docs/agent-kickoff-prompt.md](docs/agent-kickoff-prompt.md)에 있다.
규칙이 바뀌면 `docs/agent-briefing.md`를 먼저 고치고, 이 파일과 `CLAUDE.md`를 그에 맞춘다.

- 어느 에이전트든 요구사항 분석, 구현, 수정, 테스트, 문서화, Git 스테이징·커밋을 담당할 수 있다.
- 푸시·브랜치 전환·병합·리베이스·stash·reset 등 원격 또는 파괴적 Git 작업은 사용자가 명시적으로 요청한 경우에만 한다.
- 커밋 직전에 대상 파일만 명시적으로 스테이징하고 `git diff --cached --check`와 관련 테스트를 확인한다.
- 확정 사항(`agent-briefing.md` §2)은 전제로 받아들이고, 미결정 사항(§3)은 임의로 결정하지 않고 사용자에게 묻는다.

Raspberry Pi 5 Atlas UI의 제공 샘플과 개발환경 안내는 [reference/raspberrypi/](reference/raspberrypi/)에 보관한다. 원본 SDK와 도구체인은 이 레포에 포함하지 않는다.
