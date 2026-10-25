# TEAMMATE source snapshot port

This directory is an isolated PoC, independent of the production DESKMATE app.
Initial source snapshot: cf78ef8185ac956c39f0568ada5d8bf62537c525
from the independent local PoC repository; its Git history is not imported.
The first team commit records that initial version. A second commit adds the
temporary-PAT security revision. Original repository, commits and IPKs are retained.

Port-only differences: personal absolute document/provenance paths are redacted,
initial Docker workspace is renamed to avoid the original build directory, and
third-party license notices are included. PoC-relative imports still use this
directory as the Python root. Run its tests from here, not mixed with Hub tests.
App ID com.atlas.app.deskmate_thinq_poc and service com.deskmate.thinqpoc1 remain
separate. No production app, Hub, MQTT, UART, BLE or FSM code is modified.

Minimal ThinQ snapshots are retained for reproducible private Docker copy/builds.
No PR #57/#65 history is merged into this branch; its parent is main. Upstream
parser changes require explicit snapshot/hash review instead of silently drifting.
No IPK, SDK, cache, logs, credential file, personal settings or backup is tracked.
