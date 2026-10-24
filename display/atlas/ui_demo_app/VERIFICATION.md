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
