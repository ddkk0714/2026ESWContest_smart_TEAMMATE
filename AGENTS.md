# DESKMATE 에이전트 작업 규칙

모든 자동화 에이전트는 작업 전에 [docs/development-rules.md](docs/development-rules.md)를 읽고 따른다.

- Codex: 구현, 수정, 테스트와 작업 인계를 담당한다. Git 스테이징, 커밋, 푸시는 하지 않는다.
- Claude: Codex 인계 내용을 검토한 뒤 스테이징, 커밋, 푸시를 담당한다.
- 같은 파일을 동시에 수정하지 않는다. 수정 대상이 겹치면 작업을 멈추고 인계 또는 사용자 조율을 요청한다.

Raspberry Pi 5 Atlas UI의 제공 샘플과 개발환경 안내는 [reference/raspberrypi/](reference/raspberrypi/)에 보관한다. 원본 SDK와 도구체인은 이 레포에 포함하지 않는다.
