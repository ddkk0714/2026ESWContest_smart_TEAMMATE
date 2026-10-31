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

## Temporary-PAT security revision

The second team commit snapshots the reviewed post-cf78ef8 implementation without
changing the original independent repository. Personal paths are redacted and board
commands use TEAMMATE_ROOT/tools/thinq-pi5-poc. Relative code imports, memory/UID
checks, nonce assertions and default Live blocks are unchanged. Original build logs
and IPKs remain local and are not part of this PR. Run scripts/build.sh from this
directory with a fresh --output-dir; it creates a unique container workspace.

Minimal parser/probe/trial snapshots are deliberately self-contained for the Docker
copy and Python worker embedding. Their six PROVENANCE hashes remain exact; this
does not import PR #57, PR #65 or local 1ee37c6 history into this main-based branch.

Offline host reproduction: `scripts/test_host.sh --python <existing-python3.12>
--include-root <existing-host-compatible-headers> --output-dir <new-absolute-path>`.
It expects installed pytest/ruff/thinqconnect 1.0.14 and host libcurl.so.4/libcrypto.so.3;
the header root contains curl/, openssl/ and nlohmann/ (or use the system include
root with installed compatible development headers). It installs nothing and
never connects to a board/SSH/ThinQ Cloud. It is an Ubuntu/Linux test script,
not a target-board launcher. No /tmp cache path is required in the source.
