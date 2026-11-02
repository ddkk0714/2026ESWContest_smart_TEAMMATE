# PR #74 developer tools in UI18

Source: PR #74, commit `7bef8a0`, `display/atlas/ui_env_app/lib`.
`pr74/` contains the dependency closure of its sensor, PC keystroke, developer,
preview, developer mode, connection, and Bluetooth pages and services.
`diag.dart` and `hub_config.dart` originate from the same commit's camtest helpers.
No camera page, camera stream, or keyboard-content capture is imported.
Existing project licensing and audio attribution apply to these reused files/assets.

Integration differences:

- UI19 uses one LiveLink MQTT connection for both product UI and developer tools.
  PC aggregate and feedback topics are added to the existing subscriptions.
- PR74 Bluetooth permission, scan, speaker/lamp/fan selection, saved reconnect,
  volume, manual tests, feedback switch, command validation and deduplication remain.
  Stop the separate PR74 app before using this app as the Bluetooth executor.
- Developer presets use an isolated UI18 model and local LiveLink. They cannot
  publish feedback, persist real profile changes, or actuate Bluetooth devices.
  The other diagnostic tabs always show the real received state, not the override.
- Raw JSON stays in memory. ToF raw remains confined to the explicit diagnostic
  tabs. Default HTTP polling is 500ms, at most 2Hz per active diagnostic view.
- Device setup is a dialog above the existing UI, and its services survive closing it.
- Imported colors match UI18; inherited presentation badges are removed.
- Native audioplayers registration, generated registrants and CMake linkage are
  ported from PR74 as well; UI18's previous native runner had no plugin registration.
- Connection/profile settings remain app local; placement changes use UI18's
  existing profile preferences and placement POST path.

UI19 extends UI18's previous display-only device boundary with the Bluetooth
execution explicitly requested for this revision. It does not add ThinQ cloud
credentials, camera collection, key content, or new hub control policies.

V25/V26 ports PR83 ad1e1ee: sequential restore, feedback persistence (initial OFF retained), fan retries, audio route reuse, ToF B/A ordering. MQTT recovery is adapted into the shared LiveLink transport and watchdog. V26 restyles the Bluetooth popup and concise controls without changing device service callbacks.

V27 imports PR83 e00a6f21 (app 0.1.10): speaker commands/chimes, rest and automatic notice routing, GATT recovery reports, 3s settle/30s rescan limits and 45s lamp keeper. The local GATT generation checks, cleanup and shared adapter queue are retained. Speech/audio resources are disposed with the controller. Firmware changes are source-only.
