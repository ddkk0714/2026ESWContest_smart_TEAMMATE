# DESKMATE 개발 규칙과 에이전트 참고 사항

이 문서는 두 가지를 담는다.

- **§4~§6 코드·문서 규칙과 완료 기준** — 팀 공통 규칙이다. 도구와 무관하게 적용된다.
- **§1~§3 에이전트 운영** — **참고 사항이다. 각자 편한 방식으로 쓰면 된다.** AI CLI 를 쓸 때 사고를 줄여주는 절차를 적어둔 것이지, 팀원의 개발 방식을 정하려는 게 아니다.

프로젝트 확정·미결정 사항과 프라이버시 제약은 [agent-briefing.md](agent-briefing.md)에 정리돼 있다.
설계 규칙과 충돌하면 보안·개인정보 규칙 및 사용자 지시를 우선한다.

## 1. 역할과 Git 책임 (참고)

- 요구사항 분석, 구현, 문서화, 로컬 검증, 스테이징과 커밋을 어디까지 에이전트에 맡길지는 각자 정하면 된다.
- 커밋 직전에 대상 파일만 명시적으로 스테이징하고 `git diff --cached --check` 및 관련 테스트를 확인하게 하면, 무관한 변경이 커밋에 섞이지 않는다.
- 푸시·브랜치 전환·병합·리베이스·stash·reset 을 명시적 요청 시에만 하도록 시켜두면 되돌리기 어려운 사고를 막을 수 있다.

## 2. 파일 충돌 방지 (참고)

여러 사람이나 여러 에이전트가 같은 작업 디렉터리를 건드릴 때 유용한 절차다.

1. 작업 시작 전에 수정할 파일·디렉터리와 목적을 먼저 선언하게 한다.
2. 이미 수정·스테이지된 파일, 다른 사람이 작업 중인 파일은 확인 없이 수정하지 않게 한다.
3. 작업 중 같은 파일에 새 변경이 나타나면 편집을 멈추고 차이를 확인하게 한다. 덮어쓰기, 자동 포맷, stash, restore, reset 으로 해결하지 않게 한다.
4. 대규모 포맷·이름 변경·폴더 이동은 기능 변경과 같은 커밋에 섞지 않는다.

## 3. 작업 단위와 보고 (참고)

- 한 작업은 하나의 기능 또는 버그 수정으로 작게 유지하면 리뷰가 쉽다.
- 완료 보고에 `수정 파일`, `변경 요약`, `실행한 검증과 결과`, `커밋 해시`, `남은 위험 또는 미검증 항목`을 적게 하면 인계가 편하다.
- 커밋은 한 목적만 담는다. 메시지는 `feat(scope): 내용`, `fix(scope): 내용`, `docs(scope): 내용`, `chore(scope): 내용` 형식을 사용한다. (팀 규칙)

## 4. 코드·문서 규칙

- MQTT topic 또는 payload를 바꾸면 `docs/mqtt-topics.md`를 함께 갱신한다.
- FSM 상태·전이·임계값을 바꾸면 `docs/fsm-spec.md`와 해당 테스트를 함께 갱신한다. 임계값은 코드에 하드코딩하지 않고 `hub/deskmate_hub/config/`의 YAML에 둔다.
- Pi 4 hub는 2단계 TFLite 모델을 불러오지 못해도 1단계 FSM으로 안전하게 동작해야 한다.
- Pi 5 display 구현은 `reference/raspberrypi/flutter-atlas-sample/`을 참고하되, 샘플 코드는 기능 코드로 옮겨 검토·수정한 뒤 사용한다. 참조본을 직접 수정하지 않는다.
- Pi 5 Atlas 빌드는 `display/atlas/compose.yaml`을 통해 Docker 안에서 수행한다. Docker 빌드 자산은 레포 내부의 gitignore된 `display/atlas/vendor/`에 공식 Atlas 패키지에서 준비하며, Docker 설정은 외부 경로를 참조하지 않는다.
- SDK, 빌드 산출물, 데이터셋, 모델 파일, 로그, 자격증명은 커밋하지 않는다. 비밀값은 `.env` 또는 gitignore된 `secrets.yaml`만 사용한다.

## 5. Raspberry Pi Atlas 참조 자산

- `reference/raspberrypi/flutter-atlas-sample/`: Pi 5 Atlas Flutter 앱 골격과 플러그인 사용 예시(제공 샘플 원본).
- `reference/raspberrypi/atlas-docker-env-guide.md`: Atlas Docker 개발 컨테이너 운용 안내.
- Atlas SDK·엔진·toolchain은 용량이 크므로 Git에 넣지 않는다. `display/atlas/scripts/import-atlas-vendor.ps1`로 공식 패키지에서 레포 내부 vendor 폴더로 준비한다.

## 6. 완료 기준

- 변경 범위에 맞는 테스트·정적 검사·수동 확인을 실행하고 결과를 남긴다.
- 테스트를 실행할 수 없으면 이유와 재현 명령을 명시한다.
- 에이전트를 어디까지 자율적으로 진행시킬지는 각자 정하면 된다. (참고)
