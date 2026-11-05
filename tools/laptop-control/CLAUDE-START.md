# Claude Code 시작 메시지

DESKMATE ThinQ 개발을 이어받아 주세요.

저장소: https://github.com/ddkk0714/2026ESWContest_smart_TEAMMATE
작업 기준 브랜치: `feat/thinq-claude-handoff`
인수인계 문서: https://github.com/ddkk0714/2026ESWContest_smart_TEAMMATE/blob/feat/thinq-claude-handoff/tools/laptop-control/CLAUDE-HANDOFF.md
검증 코드 커밋: `852e160fb1489681de28872b3e6573e35d9ea96c` (그 후 문서 커밋 추가)

PR #110(`feat/thinq-laptop-gateway`, base main) → #111(`feat/thinq-manual-power`, base #110) → 인수인계 브랜치 순서로 읽어 주세요. 두 PR은 조회 시 미병합 Draft입니다. 최신 main을 임의 Merge/Rebase하지 마세요.

사용자 실기 확인: 실제 GET/공기질 표시, CLI·HTML LOW→AUTO, HTML 수동 OFF→ON, 실제 Pi 4 MQTT 입력, V1.1 ON 제안→수락→개별 승인→실제 ON 성공. 이 결과를 현재 새 브랜치 전체의 실기 성공으로 확대하지 마세요.

19891 통합 HTML, V1.1 정책/활성 세션 합류, 키스트로크 실제 ON/OFF·마우스 유지, SSH 재연결, 본체 확인 버튼 복구, 사전 동의형 1회 자동 OFF 코드는 이미 있습니다. 키스트로크를 새로 만들지 말고 Pi 4 수신 검증을 이어가세요. 자동 전원 기본 OFF, 자동 ON 없음. 자동 OFF는 opt-in/Mock 단계이며 실제 성공은 미검증입니다.

우선 확인할 제약: Session과 Transport 모두 같은 명령을 인증 세션당 1회로 제한합니다. 본체 확인을 빼먹으면 다음 조작이 막히며, 상세 거절 사유는 일반 오류로 압축됩니다. 안전 검사를 삭제하거나 실패 명령을 재전송해 해결하지 마세요. OFF/AUTO 정책 종단, 실제 키스트로크 MQTT, Pi 5 제품 카드/실제 제어, Pi 4 정식 배포, 노트북 없는 구조는 미완성입니다.

먼저 AGENTS.md·개발 규칙·MQTT/FSM 계약과 인수인계 문서를 읽고, git status/worktree list/HEAD/원격 main·PR 관계, 로컬 Python·Node·Windows collector 상태를 읽기 전용으로 확인해 주세요. Hub 전체와 Gateway 테스트는 tools.thinq_connect 이름 충돌 때문에 별도 Python 프로세스로 실행합니다. 이번 검증은 Hub835(3skip/1xfail), Gateway289, Windows25, Node44 통과입니다.

기존 Pi 4 Native/Hub/FSM/개인화/MQTT/UART/ToF, Pi 5 앱/BLE, 다른 worktree·백업·팀 AGENTS.md를 보호하세요. feat/demo-script/되돌린 Hub 코드를 통째로 복구하지 마세요. PAT·Client ID·실제 Device ID 원문과 개인 로그를 채팅으로 요청하거나 출력·커밋하지 마세요. 실제 GET/POST·PAT 공급·자동 제어·보드 설치/재시작·보안/시각 변경·Commit/Push/Merge는 별도 사용자 승인 전 금지합니다.

읽기 전용 확인 결과와 최소 후속 계획부터 보고해 주세요. 이후 키스트로크 Pi 4 검증 → V1.1 OFF → 실제 BAD가 있을 때 AUTO → 연결/중복/예외 안정성 순으로 진행하되, 결선 직전에는 기존 성공 경로 보존을 우선하세요.
