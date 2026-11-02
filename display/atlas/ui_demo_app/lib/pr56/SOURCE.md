# PR #56 workspace source

Source: https://github.com/ddkk0714/2026ESWContest_smart_TEAMMATE/pull/56
Base commit: `495d9551f0be0e367334cb8c7e1f8c602c8f2c7a`.
Local reference: `.worktrees/pr56-ui/display/atlas/ui_env_app`.

The local reference includes the previously verified font replacement:
`ComicSansMS.ttf` removed, `ComicNeue-Regular.ttf` and its OFL 1.1 license added,
and the workspace logo switched to `ComicNeue`. The reference worktree was not modified.

Imported the workspace shell, fixed-grid editing/packing/resizing, calendar, clock tools,
weather, environment, focus timer/reports, music/device pages, settings, and mascot rendering.
The default five-widget order and grid-capacity rules remain those of PR #56.
Weather is available from the original widget picker and navigation menu.

Integration changes:

- `workspace_shell.dart`: added optional clock-return and exit callbacks in the header.
- `music_playback.dart`: retained interfaces and track labels only. The player supplied by
  `../pr56_workspace_view.dart` is an in-memory simulator and does not load audio files.
- `feedback_policy.dart`: replaced physical Bluetooth/lamp feedback with an inert UI boundary.
  Demo lamp values are handled by the existing workspace's `demo` branch.
- `network_status_indicator.dart`: retained the header position but displays a clearly marked
  simulated status. No D-Bus or real network probe is started.
- `settings_store.dart`: empty candidate paths; the workspace receives a memory-only store.
  The production device settings and preferences are never read or overwritten.

No camtest, MQTT, D-Bus or audioplayers dependency is added to the UI demo app.
Existing source notices are retained. Comic Neue license is bundled under assets/fonts.
The referenced school/High1/LG font files and mascot PNG are copied unmodified.

V8: demo palette changed to the clock screen cream/green colours; text uses High1, LGText and LGClock. Widget layout and interactions are retained.

V9: removed side/bottom navigation; tiles open details and a header Home button returns to the grid. The initial workspace page remains home.

V18 (2026-10-25): the app now links to the board over MQTT (`../live/`), so the earlier
"No MQTT dependency" note applies only up to V17. Changes in this folder:
`display_state.dart` replaced by the ui_env_app copy (ToF posture, 2nd-stage observation,
personalization explanation); `workspace_shell.dart` prefers the board `state` for indoor
values and shows board-reported lamp/fan status via `deviceStatus` (display only, no control);
`device_widgets.dart` `DevicePreviewCard` accepts an icon/label for the fan card.
