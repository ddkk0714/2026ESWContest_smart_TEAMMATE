# DESKMATE 개발 및 에이전트 협업 규칙

이 문서는 Codex와 Claude가 같은 작업 디렉터리를 공유할 때의 단일 운영 기준이다. 기존 설계 규칙과 충돌하면 보안·개인정보 규칙 및 사용자 지시를 우선한다.

## 1. 역할과 Git 책임

- **Codex**는 요구사항 분석, 구현, 문서화, 로컬 검증, 변경사항 인계를 담당한다.
- **Claude**만 `git add`, `git commit`, `git push`, 브랜치 생성·전환, 병합·리베이스·stash를 수행한다.
- Codex는 스테이징 영역과 Git 이력을 변경하지 않는다. Git 저장소를 찾을 수 없거나 상태를 확인할 수 없으면 그 사실을 인계에 적고 Git 작업을 시도하지 않는다.
- Claude는 커밋 직전에 대상 파일만 명시적으로 스테이징하고 `git diff --cached --check` 및 관련 테스트를 확인한다. 무관한 변경은 커밋에 포함하지 않는다.

## 2. 파일 충돌 방지 절차

작업을 시작하기 전에 각 에이전트는 수정할 파일·디렉터리와 목적을 대화에 선언한다.

1. 이미 수정된 파일, 스테이지된 파일, 또는 다른 에이전트가 작업 중인 파일은 소유자 확인 없이 수정하지 않는다.
2. 작업 중 새 변경이 같은 파일에 나타나면 즉시 해당 파일 편집을 중단한다. 덮어쓰기, 자동 포맷, stash, restore, reset으로 해결하지 않는다.
3. 겹치는 변경이 필요하면 한 에이전트가 파일 단위로 작업을 끝내고 인계한 다음, 다음 에이전트가 이어서 수정한다.
4. 대규모 포맷·이름 변경·폴더 이동은 별도 작업으로 분리하고, 관련 기능 변경과 같은 커밋에 섞지 않는다.

## 3. 작업 단위와 인계

- 한 작업은 하나의 기능 또는 버그 수정으로 작게 유지한다.
- Codex의 완료 인계에는 반드시 다음을 적는다: `수정 파일`, `변경 요약`, `실행한 검증과 결과`, `Claude가 커밋할 파일 목록`, `남은 위험 또는 미검증 항목`.
- Claude는 인계 파일 목록과 실제 diff가 다르면 커밋 전에 차이를 확인한다.
- 커밋은 한 목적만 담는다. 메시지는 `feat(scope): 내용`, `fix(scope): 내용`, `docs(scope): 내용`, `chore(scope): 내용` 형식을 사용한다.

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
- Codex는 인계 후 멈추고, Claude의 커밋·푸시 결과를 전제로 작업하지 않는다.
