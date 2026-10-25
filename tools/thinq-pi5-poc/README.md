# DESKMATE independent ThinQ PoC — 2026-10-25

This is a separate source repository. It does not use the original
DESKMATE app ID, Git index, merge state, MQTT, Hub, UART or BLE. No board or Cloud
call was made in this work. Fixture results are never evidence of real control.

- App/package: `com.atlas.app.deskmate_thinq_poc`
- Native service/package: `com.deskmate.thinqpoc1`
- D-Bus path/interface: `/com/deskmate/thinqpoc1`, `com.deskmate.thinqpoc1`
- Method: `Exchange(s) -> s`, fixed operation vocabulary, no token/device-ID input.
- Source reuse: `PROVENANCE.json`, copied verbatim from ThinQ commit `1ee37c6`.
- Controls: only `airFlow.windStrength` LOW then AUTO, no windStrengthDetail write.

**Live GET and POST are deliberately blocked.** `verifiedRuntimeAccess()` returns
no credential provider or verified caller policy. No RPC, environment variable,
UI switch, runtime file or command argument can bypass this boundary. The UI switch
only enables clearly labelled Fixture POST. This is NOT a ready-to-run real-device
trial or a completed Live Gateway. No SDK wheels, PAT, real identifiers, user
credentials, account data, private API key, D-Bus .conf or AppArmor policy is shipped.

## Architecture and execution evidence

```text
Independent Flutter UI (default OFF; explicit approval)
  -> D-Bus Exchange (board caller/owner policy NOT provisioned)
  -> C++ bounded fork/execve supervisor (service's OWN OS profile)
  -> embedded Python session + unchanged ThinQ parser/manual preconditions
  -> Fixture SDK, or gated native HTTPS callback
  -> C++ GuardedHttps/libcurl/OpenSSL -> KR ThinQ Connect endpoints [LIVE BLOCKED]
```

The C++ HTTPS code follows installed official SDK 1.0.14 request endpoints/headers,
including response envelope and `x-conditional-control`, TLS peer/host verification,
no redirects/proxy inheritance, fixed KR host, pinned target, bounded response and
single-use POST permit. No actual TLS handshake or Cloud response is verified here.
The service has no verified credential source: installing these IPKs cannot fix
that missing integration. A separately approved secure provider + caller policy,
then another review/test/build, is required before Live use.

Existing Python SDK reuse was considered first. Its aiohttp/cryptography/AWS native
extensions have not been verified for a new Pi 5 service profile/ARM64. We reuse the
existing parsing/precondition code without bundling x86_64 wheels. The hybrid
still needs Python 3.12 standard-library/native modules (including asyncio's
_socket dependency); successful host execution does NOT prove the new board
profile can import them. Pure C++ validation/mapping is NOT claimed implemented.

The previous Python diagnostic source is retained in the original worktree. Its
README's earlier host evidence, previous-session board reports and this new
service's runtime status are separate. Temporary diagnostic D-Bus policy rollback
remains **unconfirmed**; this PoC neither checks nor changes it.

## Safety

One matching DEVICE_AIR_PURIFIER / AIR_910604_WW is required. Profile LOW/AUTO
read+write support and POWER_ON/CLEAN/AUTO baseline are checked. Prepare returns a
30-second monotonic challenge. Execute consumes it in both parent and child before
fallible I/O, requires explicit acceptance + online/no-external-operation assertion,
then fetches Profile and State again. External configuration change blocks POST.
AUTO requires this session's accepted LOW, matching low configuration and separate
user attestation. Fixture attestation remains `physical_confirmed=false`.

API acceptance, subsequent GET match and physical observation are separate values.
A lost POST response permits only GET reconciliation, never resend or automatic
rollback; uncertainty locks further control. A restart loses approval, not a reason
to repeat POST. Parent timeout/exec failure cannot preserve a consumable challenge.
There are no timers for autonomous polling/control, no automatic services, and the
Native Service exits after ten minutes. Each child is bounded to 25 seconds and
1 MiB output, uses clean environment/stdin pipes, and is reaped on exit/timeout.

PM units, PM2's precise meaning and physical measurement time remain unverified.
API receipt time is not evidence of a new measurement. Fixture sensor values are
explicitly reconstructed, not new real measurements. UI snapshots are labelled.

## Build and tests

Use the existing `deskmate-atlas-atlas-dev-1` container/image and SDK, never /workspace.
Build working copy is `/tmp/deskmate-thinq-poc-20261025`, marked `.poc-only`.
Do not recreate the container. Artifacts/logs stay outside team Git repositories.

```bash
./scripts/prepare_workspace.sh
./scripts/build.sh
python3 scripts/inspect_packages.py
```

The script fails if the private marker is absent. First-time workspace setup must
use a new empty directory, verify no collision and place that marker deliberately.
The SDK / Flutter plugin cache must already exist; no system dependency install.
`artifacts/package-inspection.json` records actual SHA-256 and ELF dependencies.
No install/activation command is hidden in the build script. The independent
repository has no remote or shared Git object directory. Local commits require
explicit user approval.

See `docs/BOARD-TRIAL.md` for the separate approval and platform/security gates.
See `artifacts/validation.json` for final host/build evidence and current limits.

State comparisons detect visible configuration changes, not an unobservable
change-away-and-back history. User no-external-operation attestation remains
necessary; equal GET values alone do not prove exclusive control.

ARC 0.5 emits `Architecture: all` in the IPK control metadata. Inspection records
that exact value and separately requires every embedded ELF/AOT binary to be
AArch64; it does not infer ARM64 from the control field or filename.

The selected private device ID and backend mode are pinned across child RPCs in
the parent's in-memory context, stripped from public replies. Same-model device
replacement and Fixture-to-Live authorization reuse are blocked. No private
context is written to disk or broadcast over D-Bus.
