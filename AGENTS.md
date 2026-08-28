# DESKMATE 에이전트 참고 사항

AI CLI(Codex · Claude Code 등)로 이 저장소를 다룰 때 알아두면 좋은 것들이다.
**개발 방식은 각자 자유다. 이 파일은 규정이 아니라 참고 자료다.**

작업 전에 [docs/agent-briefing.md](docs/agent-briefing.md)를 읽으면 프로젝트 맥락을 한 번에 잡을 수 있다.
확정 사항(§2), 아직 정하지 않은 항목(§3), 프라이버시·보안 제약(§5)이 정리돼 있다.
세션 시작 문구 예시는 [docs/agent-kickoff-prompt.md](docs/agent-kickoff-prompt.md)에 있다.

에이전트에게 특히 자주 알려줘야 하는 것:

- 미결정 항목(통신 방식, `C_focus` 부호, Pi 4 런타임 등)을 혼자 결정해버리지 않게 한다.
- 키 내용·카메라·상시 음성·운영 ToF raw 를 다루는 코드를 만들지 않게 한다.
- 임계값을 코드에 박지 말고 `hub/deskmate_hub/config/*.yaml`에 두게 한다.
- push·브랜치 전환·병합·리베이스·reset 같은 원격/파괴적 Git 작업은 요청할 때만 하게 해두면 사고가 줄어든다.

팀 협업 규칙(`main` 직접 push 금지, 커밋 메시지 형식, 자격증명·데이터 커밋 금지)은
[README.md](README.md)와 [docs/development-rules.md](docs/development-rules.md)에 있다. 이건 도구와 무관하다.

Raspberry Pi 5 Atlas UI의 제공 샘플과 개발환경 안내는 [reference/raspberrypi/](reference/raspberrypi/)에 보관한다. 원본 SDK와 도구체인은 이 레포에 포함하지 않는다.
