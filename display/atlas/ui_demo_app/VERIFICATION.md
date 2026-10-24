# V18 verification (2026-10-25)

- flutter analyze --no-pub: No issues found.
- flutter test --no-pub: 56 passed (V17 25 + live_test 30 + preview 1). demo_test·preview_test 의
  프로필 등록 반복 횟수만 9 질문에 맞췄다.
- 새 시험(test/live_test.dart): UTF-8 해석, state/phase → 홈 항목(빠진 키 포함), 카드 kind 5종 × accept/reject →
  feedback/user, 만료 timeout, 상태 무관 카드, 프로필 발행 스키마·retain·이름 없음, 장치 위치 POST 성공·500·404·닿지 않음
  (로컬 HttpServer), 기기 상태 표시, 연결 실패·상태 오래됨·끊김 fallback, FSM 흐름, 설정 연결 탭 키보드.
- 화면 검토 PNG: build/qa/live-home.png, live-card.png, profile-placement.png.
- flutter-atlas build atlas --no-pub --ipk --release: exit 0.
- 실기(Pi 5) 설치·연결 확인은 아직 하지 않았다.

# V17 verification (2026-10-25)

- flutter analyze --no-pub: no issues, exit 0.
- flutter test --no-pub: 25 passed, exit 0.
- Transition test checks both pages sit near 50% opacity at the 250ms midpoint
  of the 500ms dissolve, with no slide, scale or translation widgets.
- personal_settings_test fixed pumps raised from 500ms to 600ms to clear the
  longer 500ms transition.
- V15 fatigue-card regression test kept and passing.
- flutter-atlas build atlas --no-pub --ipk --release: exit 0.
- IPK SHA256: 0b1350b5c0b43a5db23932446c3f011c0359e3a8e7c3bd1701023afb19420ec7.
  Host and Pi transfer hashes matched. Installed separately and synced.
- Pi V17 reports Running. Home capture: build/qa/pi5-v17-home.png.
  Pi app list shows numbered V8-V17.
- V16 source archived to ../ui_demo_versions/v16. V16 app remains installed.
- No oxlint config present.
- Hardware/audio boundaries and in-memory persistence are unchanged.
- No commit/push, production control or OS setting changes.
