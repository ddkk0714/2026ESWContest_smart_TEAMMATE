# PAT session prototype — host evidence, 2026-10-25

This revision has NO board/SSH-host/Cloud execution evidence. Real PATs, real device
identifiers and SDK application credentials were not supplied or printed.

## Final result

- Python **149 passed**, including the original 106-test scope (explicit copied
  manual-trial and air-quality test files), new credential/session tests and the
  native-process rejection-before-fork regression.
- Existing ThinQ adapter **15 passed** (read-only source from the original ThinQ
  worktree, no cache/bytecode writes).
- C++ `-std=c++17 -Wall -Wextra -Werror`: host service/helper, host security harness,
  fake HTTPS transport guards pass. Real abstract AF_UNIX SOCK_SEQPACKET,
  SO_PEERCRED and helper stdin protocol were used on the Linux host.
- Flutter **6 passed**; `flutter analyze`: no issues. Original four tests retained.
- New/changed Python Ruff E4/E7/E9/F/I and format pass; AST, shell syntax and
  whitespace checks pass. The unmodified legacy probe test has a pre-existing
  I001 import-layout finding under a whole-tree Ruff scan; it was not rewritten.
- Version **0.2.0**, two IPKs built with existing `deskmate-atlas-atlas-dev-1`,
  ARC **0.5.0**, existing ARM64 SDK. No /workspace mount/config/container recreation.
- Final container source: `/tmp/deskmate-thinq-pat-97hm4xw8`.
- Final artifacts: `artifacts/pat-security-20261025-ejhelmrh/packages-pat-session/`.
  See its package-inspection.json for exact hashes, ELF and dynamic dependencies.
  ARC marks control Architecture=all; every embedded ELF/AOT was separately
  required to be AArch64. No generated concrete policy, AppArmor override, host
  test helper flag, static fake credential, SDK wheel or actual PAT is packaged.

## What host testing does and does not prove

Authenticated accept/unauthorized UID denial, wrong server PID, duplicate/busy
supply, expiry, revoke/disconnect, restart loss, old session replay rejection,
retired-token rejection within one process, single-use control permits, read-only
POST block, unapproved manual POST block, in-flight cancellation and no retry are
covered. Public outputs are checked against random fake credential values.
Native source contracts enforce fixed SDK headers, TLS/hostname verification,
no redirect/proxy inheritance, no verbose curl logging and one request attempt.
These are code/fixture checks, not an observed TLS request or LG authorization.

Host same-UID fixture operator/service is explicitly permitted ONLY by
POC_HOST_TEST. Production demands three distinct nonroot roles. The host harness
injects a UI identity as a test double; actual D-Bus getCredsEuid is SDK-compiled,
not exercised against ATLAS. XML method/owner allowlists are static-tested, NOT
installed/reloaded on a board. The remote helper command is tested locally; SSH
negotiation uses a mock, never an actual host/key. No cross-account ATLAS socket,
AppArmor, helper exec permission, Python service-profile imports or CA handshake
has been demonstrated.

## Failures found and corrected

- Linux may reset a denied/busy seqpacket connection when it closes with an unread
  body. Tests accept a closed/reset channel only alongside absence of granted
  credentials; no fake success or authorization bypass is introduced.
- Cancellation during fake HTTPS now retains an authorization-expired/revoked
  category rather than suggesting missing credentials; no automatic retry.
- Native RPC allowlist rejects forbidden credential fields BEFORE Python fork.
- Helper prints only allowlisted metadata, not arbitrary peer JSON or exceptions.
- Flutter's one analyzer style finding was fixed; all assertions retained.

## Preservation

Original independent commit remains
`cf78ef8185ac956c39f0568ada5d8bf62537c525`; no commit/staging/push/remote was added.
Initial 53 sources are git-archived and hash-verified; old 13 artifacts (including
both IPKs and initial validation records) are copied and hash-verified under the
new run's backup/. Original artifact hashes remain unchanged. Eight other
worktree HEAD/status/index/source snapshots match. Integration remains 58 staged,
MERGE_HEAD=ffdaae02e0ec22d31b2e711f5888a1f49b16620d, no merge commit/conflict.
Existing persistent backup 313 hash entries are unchanged. See
preservation-final.json; the preliminary comparison used different untracked
formatting and is not evidence of modified files.

## Remaining release blockers

Default builds block Exchange/credential sockets/Live until exact installed role
mapping and policy/profile are reviewed. Nonroot SSH principal availability,
helper execute access, account persistence or PackageManager policy rewriting,
abstract socket permission, board Python/runtime/CA and old diagnostic temporary
policy rollback remain unverified. Do not run root, change AppArmor, add broad
policies or keep reinstalling until it appears to work.

Local lease expiration cannot invalidate a PAT at LG or undo an accepted command.
Cross-restart raw-PAT retirement cannot be proven without persistent state; fresh
processes are unauthenticated and require explicit supply again. Root/same-user
compromise or transient Python/OpenSSH/TLS copies are outside complete memory
protection. User cloud-side revocation remains necessary after the experiment.

Read BOARD-TRIAL.md in order. Tomorrow's immediately safe steps are read-only
inspection and review; install, Fixture activation, real PAT/GET and manual POST
are distinct approval gates. Today did not establish a deployable final Pi 4
service or the notebook-free product's credential bootstrap/lifecycle.
