# Team-repository port validation — 2026-10-25

This PR contains source snapshots, not the independent repository's Git history.
The main parent is 7aecee437b106580341602dd4dfc6e9de0841a60. The first commit
ports independent initial source cf78ef8185ac956c39f0568ada5d8bf62537c525;
the second ports its reviewed temporary-PAT security work. Original source,
local changes, initial commit, artifacts and remote-free configuration remain intact.

## Re-executed against this nested source directory

- Python 149 passed, including original probe/manual/air-quality, nonce, mode,
  credential-delivery and native child-process boundary checks.
- C++ service/helper, credential harness and fake HTTPS transport compiled with
  -std=c++17 -Wall -Wextra -Werror; real Linux Unix peer-credential/stdin tests pass.
- Flutter 6 passed; flutter analyze reports no issues.
- New/changed Python Ruff/format, shell syntax and whitespace pass. Original copied
  legacy probe test's pre-existing I001 import layout is intentionally retained.
- Existing container deskmate-atlas-atlas-dev-1, ARC 0.5.0, original ARM64 SDK,
  newly created isolated container source directory; /workspace was not used.
- ARM64 service IPK 6,333,778 bytes, SHA-256
  8afc797f2b8da7baf92cd0f4d568ab2d3089822674fddb01e0c324745860fd35.
- ARM64 app IPK 7,352,832 bytes, SHA-256
  1d9218ac32780e5352e5b94ca705c8f98da1ac8b02c88e663e6f59ef1d20c7b3.
- IPKs and inspection records are LOCAL ONLY under artifacts/team-port-20261025,
  excluded from Git. ARC control says Architecture=all; every ELF/AOT was checked
  as AArch64. IDs match this PoC, no production app, concrete policy, static fake
  token, host-only helper entry flag, SDK wheel, log or credential file included.

## Public-source review

Original 53 initial sources plus minimal metadata are separated from the security
revision. Personal absolute paths are removed in both commits. Source snapshots
retain six parser/probe/trial fixture hashes. The existing public Atlas runner
scaffold is retained for standalone packaging; upstream BSD license and notices
are included. No confidential LG document or SDK implementation is copied.
The root MIT license and THIRD_PARTY_NOTICES.md describe the sources.

Review includes superseded initial-commit versions, current files, tracked/untracked
staging, Git object reachability relative to previously published refs and package
contents. Synthetic tokens/private aliases in tests are intentional fixtures,
not observed account/device values. Pattern scans alone do not establish absence
of all secrets; provenance and selected source contents were reviewed as well.

## Unverified / disabled

No Pi installation, activation, policy modification, SSH negotiation, real PAT,
ThinQ Cloud GET/POST, BLE command or physical-device operation occurred in this
port. Host SSH is mocked; Unix kernel IPC is real only on the Linux host. Host
same-UID harness is not proof of ATLAS's required three distinct nonroot roles.
Actual D-Bus caller identity, policy loading, UID persistence, AppArmor, Python,
helper execute access and CA/TLS require board verification. Old diagnostic
policy rollback is still a separate unconfirmed gate.

Default compiled account flags are false; no credential listener/Live/Exchange
is enabled by installing these default artifacts. Separate reviewed bindings,
policy/runtime tests and explicit approval are required before any live build.
The entire Python ThinQ SDK is NOT installed/run on Pi by this PoC: existing
Python parsing/preconditions are embedded; guarded native libcurl/OpenSSL REST
uses the reviewed official SDK request contract. The laptop provisioner alone
expects a reviewed thinqconnect 1.0.14 environment.

A future Pi 5 success proves neither Pi 4 execution nor full system integration.
This main-based experiment does not merge/replace PR #57 or #65; production
integration waits for team review, board tests and separate authorization.
