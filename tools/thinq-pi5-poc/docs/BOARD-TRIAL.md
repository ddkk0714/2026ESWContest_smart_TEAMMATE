# Separate Pi 5 trial — NOT EXECUTED (PAT session revision, 2026-10-25)

Everything here is a future checklist, not authorization. No board/SSH/Cloud
operation was performed by this revision. Existing DESKMATE app, broker, Hub/UART,
old diagnostic package and policies must remain unchanged. A Pi 5 PoC success
would NOT prove the final Pi 4 service or a complete DESKMATE E2E.

## 0. Choose the artifact and gates

Use `artifacts/pat-security-20261025-*/packages-pat-session/package-inspection.json` and its
exact IPKs, not the initial root artifacts. New version is 0.2.0. Default build
has NO concrete D-Bus policy or verified account bindings: **Exchange, credential
receiver and Live are blocked**. Installing this artifact is not enough to enable
Fixture on the board, much less Live. Stop at unverified gates; do not add permissive
rules, guess a UID or run the service as root.

A reviewed policy template exists at
`native/files/dbus/com.deskmate.thinqpoc1.conf.in`. `native/configure_bindings.py`
can generate a header and exact policy from NONSECRET account names/verification
flags. It is a build tool, not an installer. A policy-bound build is a separate
review/approval step after actual mapping/profile verification. All production
roles (service, Flutter, SSH operator) must be distinct nonroot accounts.

## 1. Trusted terminal / read-only board inspection

Execution locations are explicit below. In WSL, set nonsecret values only:

```bash
cd ${TEAMMATE_ROOT}/tools/thinq-pi5-poc
PI5_HOST='<current-verified-IP>'
PI5_INSPECT_USER='<existing-inspection-account>'
PI5_KNOWN_HOSTS='<existing-trusted-known_hosts-absolute-path>'
PI5_IDENTITY='<existing-private-key-absolute-path>'
```

Confirm the SSH host fingerprint through a trusted prior record or local console.
`ssh-keyscan` alone is NOT identity verification. Do not disable checks, accept a
new key automatically, print/copy the private key, or put any PAT in these variables.

```bash
ssh -F /dev/null -T -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$PI5_KNOWN_HOSTS" -o GlobalKnownHostsFile=/dev/null \
  -o BatchMode=yes -o IdentitiesOnly=yes -i "$PI5_IDENTITY" \
  "$PI5_INSPECT_USER@$PI5_HOST"
```

On Pi 5 (read-only, existing inspection permissions only):

```bash
uname -a
cat /etc/os-release
busctl --system list
ps -eo pid,uid,gid,comm
cat /etc/dbus-1/system.conf
cat /var/lib/package-manager/uids/com.deskmate.thinqpoc1.uid
cat /var/lib/package-manager/uids/com.atlas.app.deskmate_thinq_poc.uid
cat /data/share/usr/share/dbus-1/system-services/com.deskmate.thinqpoc1.service
```

Missing PoC files before installation are expected. Listing a bus is not calling
Exchange. Do NOT introspect the PoC yet: introspection can activate it. Read the
installed DESKMATE app state through its existing documented AppManager query;
verify Pi 4 broker through the already configured wired path without restarting it.
If those exact current calls/addresses are unavailable, obtain them read-only
instead of inventing method signatures or assuming past IPs.

## 2. Old temporary policy rollback / package collision

Read whether diagnostic-specific files remain in the directories actually included
by system.conf; compare against the old experiment record. **Old pythondiag policy
rollback remains unconfirmed.** Do not remove/change it as part of PoC setup. On any
unexplained broad grant, stop and report. Check both PoC IDs are unused or identify
an existing approved install before proceeding. Existing
`com.atlas.app.deskmate_display` is never replaced.

## 3. Separate installation approval

Ordinary ARC 0.5 `arc install` explicitly executes Remove -> Install -> service
introspection. It is NOT an install-only command. Use an approved install-only
PackageManager sequence; inspect its live signature before use. The existing ARC
source uses the following two-argument contract (do not issue Remove):

```bash
# Existing ATLAS Docker terminal, only AFTER installation approval.
arc push --help
arc shell --help
# Copy each approved IPK to a NEW nonsecret staging path on the target using arc push.
# On the approved target shell, after checking the Install signature:
abusctl call com.atlas.PackageManager1 Install 'com.deskmate.thinqpoc1' '<staged-service-ipk-path>'
abusctl call com.atlas.PackageManager1 Install 'com.atlas.app.deskmate_thinq_poc' '<staged-app-ipk-path>'
```

No real installation was performed here. Actual device ID, target file path,
AppManager launch/stop methods and PackageManager current state must be confirmed
on that day's board. Do not substitute `arc run` (build/install/activate) for a
controlled install. `run_after_installation=false` must be present and respected.

## 4. Mapping, policy and runtime approval gates (STOP if any mismatch)

Before activation, compare PackageManager mapping, passwd UID/GID, activation User,
sole own-name policy account and exact UI/operator method allowlists. Inspect final
policy at `/data/share/usr/share/dbus-1/system.d/` only if system.conf includes it.
ARC bundles `files/dbus/*.conf` to `usr/share/dbus-1/system.d/`; inspect the IPK and
actual installed location, never just CMake install output. No root own, global
allow, borrowed perm_thinq, new privileged API or AppArmor weakening.

A rebuild's account snapshot can become stale during update. Preservation or
PackageManager rewriting is NOT proven for this package. Recheck all three
accounts after any install; do not keep reinstalling or editing mappings to make
names match. Also verify the reviewed nonroot SSH operator can execute this same
service binary's helper subcommand under its OWN profile. If only root SSH exists,
or exec/AF_UNIX/peer credentials are denied, **A is blocked**. Do not use sudo,
runuser, setuid or another platform service's profile.

D-Bus `AuthStatus` and `Exchange` have kernel-provided calling EUID checks in
addition to exact policy rules. Credential transfer is not a D-Bus method. A host
XML test is not evidence that the board daemon loaded the policy. Any need for
new policy installation/reload is another explicit approval, not a step performed
by the laptop credential tool. No daemon restart is proposed.

## 5. Fixture activation, Python and CA/TLS

Only after policy/runtime bindings are reviewed and separate activation is approved,
launch the PoC via the installed AppManager method verified by introspection (not
a guessed command). Test default OFF, read, cancel, explicit Fixture LOW approval,
separate GET match, Fixture attestation, separate AUTO. Zero Cloud traffic is the
Fixture requirement. Fixture attestation is not real physical evidence.

The native service forks its own bounded Python worker. Verify exact child path,
service UID, AppArmor profile and Python stdlib imports against the diagnostic
record. An exec/import error stops the trial. No SDK/native wheel installation,
profile transition or library copying. Check libcurl/OpenSSL/CA roots and current
wall clock read-only; an offline library inventory or Fixture result is **not a
TLS handshake test**. Actual HTTPS verification must use CA+hostname validation in
the first separately approved GET. Never use `curl -k` or substitute plain HTTP.

## 6. Prepare a reviewed read-only build/session (no PAT yet)

First prove operator/helper/peer checks using **only random Fake Tokens**. Proposed
binding input has the exact six keys below; placeholder names are intentionally
invalid and must NOT be sent directly to the generator:

```json
{"service_user":"<verified-package-account>","ui_user":"<verified-UI-account>","operator_user":"<verified-nonroot-SSH-account>","reviewed":true,"read_only_verified":false,"manual_verified":false}
```

Only a separately approved, reviewed build may set `read_only_verified=true` after
its prerequisite tests. `manual_verified` remains false. No environment variable,
D-Bus payload or UI selection can enable missing compiled verification flags.
If updating that build changes accounts or runtime permissions, stop again.
The default newly built IPKs in this revision intentionally do not pass these gates.

## 7. PAT supply — only after all gates and separate GET approval

On the trusted WSL terminal (not Codex chat), use the existing reviewed Python
3.12/thinqconnect 1.0.14 environment. Do not auto-install it in the board or change
PlatformIO/system Python. The helper binary path is packaged as the service's
existing executable; ARC 0.5 does not bundle an arbitrary second executable.

Read-only confirm the current service PID after approved activation; the helper
verifies kernel SO_PEERCRED service UID AND that PID. Use current mapping, not old
`u0_a` examples. Keep this terminal open, max 300-second authorization:

```bash
# WSL; nonsecret metadata only. Replace these placeholders after verified checks.
<reviewed-python3.12> tools/pat_session.py \
  --host "$PI5_HOST" --user '<verified-nonroot-operator>' \
  --known-hosts "$PI5_KNOWN_HOSTS" --identity "$PI5_IDENTITY" \
  --service-user '<verified-service-account>' --service-pid '<verified-PID>' \
  --ttl 120 --host-key-verified --approved-terminal
```

The command waits for authenticated READY before hidden `getpass`; no PAT argv,
env, history, temp file, D-Bus or Flutter. Do not redirect/record the terminal or
pipe a token. If SSH/key/protection/READY validation fails, it must not prompt for
PAT. Reply contains only session metadata. New Client ID and official SDK header
key travel in the same protected stdin channel, not environment or files. The
credential tool imports the reviewed SDK constant only then; no key is in the IPK.

`--fake` uses random Fake Token and Fixture mode, never Cloud. It still requires
verified SSH/roles; do not run it against a board without execution approval.
No actual PAT/SSH input has been used during development.

## 8. Pi-native GET and comparison

In Flutter select `live_read_only`, refresh credential-session status, then request
GET. Actual service authorization is rechecked for each Exchange; list exactly one
DEVICE_AIR_PURIFIER / AIR_910604_WW, then Profile and State. Record sanitized HTTP
status/allowlisted metrics only. Compare physical display/mode and parser results;
PM units, PM2 meaning and actual measurement time stay unknown. Repeated GET is not
evidence of a new measurement. Stop on 401/403, offline, TLS failure, timeout,
profile mismatch or unexpected target. 401/403 revokes the credential lease.

## 9. Manual LOW/AUTO — separate approval/build capability required

PAT supply alone DOES NOT authorize POST. A separate reviewed manual-capable build
and explicit operator manual arming are required (`--manual` additionally requests
a typed session-arming phrase); this cannot convert default/RO-only builds. Manual
arming still requires per-command Flutter approval, one-use 30-second nonce, same
credential session and fixed target, fresh Profile and State. Select `live_manual`.

POWER_ON/CLEAN/AUTO + LOW/AUTO writable profile + no external change -> explicit
LOW approval -> exactly one `airFlow.windStrength=LOW` POST -> distinct GET readback
-> user verifies actual body/airflow and records attestation. Then separate AUTO
approval is allowed only for the same session's confirmed LOW with unchanged
configuration and user attestation. No `windStrengthDetail` write. If expired,
restarted, response lost, offline or externally changed, do not resend or auto
restore. User may restore via the ThinQ app; record uncertain outcome.

## 10. Revoke, stop, rollback

Press Enter in the provisioning terminal (or close it); helper requests revoke and
closes the socket. Native lease is wiped on EOF, expiry, explicit revoke or exit;
new requests are denied. In-flight cancellation is best effort and bounded by curl
connect/request timeouts (5/10 seconds), supervisor 25 seconds. Already accepted
POST can still take effect; revocation is not an undo/automatic restore.

Check AuthStatus is credential_unavailable, no child remains and service exits
(max 10 minutes) or stop ONLY the approved PoC PID through the supported lifecycle.
Never stop the original UI/broker, restart D-Bus, reboot or edit shared policies.
If removal is separately approved, use only these two PoC IDs with official
PackageManager/ARC removal. Review account/policy cleanup; failure is a stop
condition, not a reason for forced removal. Original root artifacts remain intact.

Finally sign into LG's official PAT portal in the user's own browser:
https://connect-pat.lgthinq.com/ . Select the experiment-only PAT and use its current
revoke/delete action, confirming it is no longer active. Exact current UI wording
has not been verified (JS/login page), so do not automate/login/revoke via guessed
APIs. Revoke unused experiment tokens too. Local memory expiry is NOT cloud-side
PAT revocation. No PAT or screenshot containing it should enter Codex/logs/Git.
