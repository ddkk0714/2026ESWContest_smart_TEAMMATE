# UI demo version policy

The user requested a separately installed app for every UI revision: V1, V2, V3, etc.
Before the next UI revision, archive this version's source under ../ui_demo_versions/vN,
excluding generated files, SDKs, logs and build artifacts. Preserve the installed older app.
Increment the app ID, display name, executable, and package version together. The user requested no version badge in the UI; keep versions in app names only.
See ../UI_DEMO_VERSIONS.md for the current version registry.
Both normal and focused views must retain the top-right app exit button.

## User-facing language and motion

Never add user-facing labels such as UI 시연, 시연, 데모, Demo, simulation, mock or sample badges. This applies to screens, tooltips, dialogs, reports and launcher metadata. Keep implementation limits documented in source/docs without adding these labels to the product UI. New launcher names are DESKMATE V<N>. Keep hardware boundaries unchanged. Use the shared gentle page transition for new page/tab flows; respect reduced-motion preferences.
