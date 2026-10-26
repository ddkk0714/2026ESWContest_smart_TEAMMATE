# V24 delivery (2026-10-26)

- User explicitly requested no tests for this revision. No unit/widget/integration tests, static analysis or screen interaction verification were run after the card changes.
- Release build completed, exit 0. IPK SHA256 73227787fba354152fa3829f44d83f0748d8319b02768b82ca8b5501203a01c3 matched host/Pi.
- Installed separate com.atlas.app.deskmate_ui_v24, copied prior .config/profiles, set owner5069:1011, requested Start and synced. V23 installation and source archive preserved.
- New 860px card: reason, actual observations, proposed changes, amount/current-to-proposed value when supplied, and acceptance guidance. Content scrolls while response buttons remain outside the scroll area.
- Optional request.adjustments metadata is display-only. Existing request fields/explanation/why/arm_label/minutes are retained. Unknown amounts remain unknown; no default percentage/duration is invented.
- Current legacy hub requests may not contain adjustment amounts, so that part can show missing information until supplied by hub. Control/accept/reject behavior is unchanged.
- Includes V23 production defaults and local presentation overlay. Prior V23 native bottom-sheet close was observed stopping the app; local overlay replacement was built but physical close confirmation was intentionally not continued after user requested no tests.
- V23 earlier checks: 212 tests passed for production defaults, actual board elapsed 00:00:03→00:00:06 over 3 seconds, default goals/speed off, no synthetic sensor values. These are not V24 test results.

# V22 profile choices verification (2026-10-26)

- Avatar choices expanded 8 → 32, same Twemoji source commit/license as existing assets. Original 8 IDs preserved.
- Shared categorized image selector (all/animals/plants/sky and objects/people) in profile creation, profile management and personal settings.
- Shared 18 occupation/daily-role choices in creation and settings; previous labels retained for saved profiles.
- Three new tests: create profile with astronaut/researcher; edit existing profile to penguin/developer and reload disk; bundled PNG signatures and category coverage.
- Existing creation test now scrolls to the legacy occupation chip before tapping, matching the expanded list.
- flutter analyze --no-pub: No issues found; full flutter test: 209 passed; release IPK build: exit 0.
- SHA256 ca110b6db5917ab1380a539a7ccb4d71b9c19771bac9db64cdf411c551ad482d matched host/Pi.
- V21 source archived ../ui_demo_versions/v21, installed V21 preserved. V22 installed separately, Running/active UID5067.
- V21 config/profiles copied to V22, profile cmp matched, owner5067:1011.
- Production-font screenshots reviewed. Actual Pi5 occupation settings and image picker reviewed: build/qa/pi5-v22-profile-settings.png, pi5-v22-avatars.png.
- No profile image/occupation was changed during actual-board inspection; user can choose in open picker.

# V21 focus return verification (2026-10-26)

- Root cause: header button sent active sessions to DemoPage.details, including from details itself. Changed active destination to DemoPage.focus.
- Regression test failed before fix (expected focus, actual details), passed after fix. Checks active session and elapsed time preserved.
- flutter analyze --no-pub: No issues found. Full flutter test: 206 passed. Release IPK build: exit 0.
- SHA256 922ec25e03b47ded0b3eccf6ec24771430733a79597ee3b0eac7df21b9909f21 matched host/Pi.
- V20 source archived to ../ui_demo_versions/v20; existing installed V20 preserved and stopped. V21 installed separately.
- V20 profile/config copied, profile cmp matched, owner 5066:1011.
- Actual Pi5: opened focus details, tapped top focus-return at (625,42); dark clock appeared immediately. Captures build/qa/pi5-v21-details.png and pi5-v21-focus-return.png.

# V20 Pi5 installation verification (2026-10-26)

- Rebuilt release IPK, exit 0. SHA256 26e9f96d256cf92a3880156d7f29e2163acf518b5926c9a775e159df5ca275ce matched on host/Pi.
- Ethernet restored at 1Gbps. Installed separate com.atlas.app.deskmate_ui_v20 and synced.
- V19 .config copied to V20, profile cmp matched. V19 and PR74 installed apps preserved and stopped.
- AppManager reports Running and active window, UID5065. Real 1024x600 home capture inspected: build/qa/pi5-v20-home.png.
- Bluetooth popup opens and scrolls on board; actual scan populated nearby BLE devices without permission errors. Adapter hci0 powered. No saved speaker/lamp/fan; unknown devices were not connected. Actual three-device operation remains unverified.
- Board clock currently Oct19 although host date Oct26; clock settings unchanged.

# V20 Bluetooth verification (2026-10-26)

- V19 source archived to ../ui_demo_versions/v19, generated files excluded. V19 installation preserved.
- App ID/entry/CMake executable/version advanced together to DESKMATE V20, 0.20.0+20.
- Adapter operations serialize scan, speaker connect and BLE connect/discovery without dropping other connected clients.
- Per-address GATT clients reused; select/forget/write retry release old client IDs; late connection responses are discarded and released.
- Initial regression: three concurrent connects reached peak 3 (expected 1); duplicate address created two client IDs; changing/forgetting failed to release; stale lamp connection incorrectly succeeded. All reproduced before fixes.
- flutter analyze --no-pub: No issues found.
- flutter test --no-pub --reporter expanded: 205 passed, including 6 multi-device regression tests.
- Physical adapter limit not yet determined. Windows Ethernet is Disconnected/0bps; Pi5 SSH and real three-device simultaneous operation cannot be tested until reconnect.
- flutter-atlas build atlas --no-pub --ipk --release: exit 0.
- V20 IPK SHA256: 5f241d3a65c71df7d1bf91127e8a916fed3fde91dc3b376b3c1016659b76dd48.
- Not deployed: Ethernet disconnected. No claim of actual three-device fix yet. No commit/push.

# V19 verification (2026-10-26)

- Base: PR79 `93e5db1`; imported developer/Bluetooth dependency closure: PR74 `7bef8a0`.
- Branch/worktree: `feat/ui18-developer-tools`, `.worktrees/ui18-developer-tools`.
- `flutter analyze --no-pub`: No issues found.
- `flutter test --no-pub --reporter expanded --concurrency=1`: **199 passed**.
  Includes UI18 tests, imported PR74 service/page tests, and new integrated entry,
  all-six-tab, Raw dialog, isolated UI18 preview, shared MQTT and Bluetooth-dialog tests.
- `flutter-atlas build atlas --no-pub --ipk --release`: exit 0.
  Native audioplayers registration and generated plugin CMake wiring were required;
  the first installation ran without a window until these were ported from PR74.
- Final IPK SHA256: `88ffee4e1e7234bbfd18191d861115859dfb860fc6a0a6658d45ddb1162b0552`.
  Host/Pi hashes match. Installed as `com.atlas.app.deskmate_ui_v19`; synced.
- Pi5 native touch verification: controls entry, sensors, PC keystroke, developer
  focus preset rendering UI18, ToF preview, developer mode, connection tab,
  return to clock and Bluetooth popup. Final status: Running / active, developer mode open.
  Screenshots: `build/qa/pi5-v19-{controls,sensors,keys,dev,focuspreview,tof,mode,connections,bluetooth}.png`.
- V18 profile file was copied into V19, ownership corrected, and `cmp` matched.
  V18 and PR74 installations remain. PR74 is stopped to avoid duplicate BLE execution.
  Temporary native-touch helper removed. LG OS home/notification left running.
- `git diff --check`: clean. No oxlint config. No commit/push/merge.
- Limitation: Pi4 address is currently unset, so live sensor/ToF data and physical
  Bluetooth device control have not been verified on this installation.

# V18 verification (2026-10-25)

- flutter analyze --no-pub: No issues found.
- flutter test --no-pub: 59 passed (V17 25 + live_test 33 + preview 1). 두 번째 커밋에서 프로필 저장 3개 추가. demo_test·preview_test 의
  프로필 등록 반복 횟수만 9 질문에 맞췄다.
- 새 시험(test/live_test.dart): UTF-8 해석, state/phase → 홈 항목(빠진 키 포함), 카드 kind 5종 × accept/reject →
  feedback/user, 만료 timeout, 상태 무관 카드, 프로필 발행 스키마·retain·이름 없음, 장치 위치 POST 성공·500·404·닿지 않음
  (로컬 HttpServer), 프로필 저장(재시작 뒤 같은 profile_id·선택, 깨진 파일), 기기 상태 표시, 연결 실패·상태 오래됨·끊김 fallback, FSM 흐름, 설정 연결 탭 키보드.
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

# V25 delivery (2026-10-26)

- Source: PR78 1d64956b9c2eb6efedae6efa50f871b04e15c106; PR83 ad1e1ee4b1c903887938202ce87fab69b63f01b9.
- Release IPK build exited 0. SHA256: f47459cac31658e21eb9eacbf13a4ce09b0377caead12bab98bf90385b77b3a0.
- PackageManager Install and AppManager Start returned success for com.atlas.app.deskmate_ui_v25. V24 .config copied to V25, ownership 5070:1011, synced.
- Tests, analyzer and UI/hardware interaction checks skipped at user request. No oxlint configuration found.
- Laptop sensor Docker image built successfully with PyYAML 6.0.2. Node server restarted at http://127.0.0.1:8791 targeting V25. Pi4 address is not configured; live sensor/profile/feedback exchange has not been exercised.
- V24 archived and remains installed. No commit, push or PR merge.

# V26 delivery (2026-10-26)

- Fixed the manual ventilation button: it opens a local SuggestionCard instead of invoking presentation-only fatigue(). No synthetic sensor event, device write or fabricated MQTT feedback is emitted.
- Bluetooth popup uses main UI font/palette, rounded cards and concise controls. Removed protocol/address descriptions and irrelevant disabled connection actions. Preserved service callbacks and setting persistence.
- Final release IPK build exited 0. SHA256: 706a62a8502d29d3942f52aadfd00188c16a608b7dd3f9a46c75ce47936839c7.
- Pi5 PackageManager Install and AppManager Start returned success for V26. V25 settings copied to V26, ownership 5071:1011, synced. V25 remains installed and its source/audio is archived.
- Laptop server restarted targeting V26 at http://127.0.0.1:8791/.
- Tests, analyzer and UI/hardware interaction checks skipped as requested. No claim of runtime button or visual verification.
