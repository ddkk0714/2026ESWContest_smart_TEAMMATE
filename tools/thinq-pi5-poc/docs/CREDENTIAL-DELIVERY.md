# PAT delivery decision and threat model — 2026-10-25

Written BEFORE implementation. No real PAT, Pi access or Cloud call is authorized.
A is the conditional PoC design; it is NOT established deployable on ATLAS yet.

| Concern | A: verified SSH -> local native receiver -> Pi HTTPS | B: authenticated Pi UI -> laptop Gateway -> Cloud |
|---|---|---|
| Token exposure | Trusted laptop terminal/OpenSSH, remote SSH/helper, Native memory, TLS library | Laptop process/TLS library only |
| Pi compromise | Service/root compromise can steal/use PAT or issue permitted commands | Can abuse authorized Gateway capabilities; no PAT resident on Pi |
| Laptop compromise | Can steal input/key, impersonate authorized operator | Can steal PAT and manipulate all Gateway operations |
| Request authentication | SSH host key + user key; kernel Unix peer UID/PID; exact D-Bus UI UID | Requires mTLS or equivalently authenticated SSH channel, capability authorization, nonce/expiry/replay protection |
| Network risk | SSH encryption/host verification, HTTPS CA/host verification; no TCP receiver | An unauthenticated HTTP/LAN gateway is forbidden; TLS plus peer authentication and replay controls needed |
| Complexity | ATLAS UID/profile/Unix IPC and safe memory/credential lifetime must be proven | More familiar laptop runtime, but additional exposed Gateway identity and network trust |
| What success proves | Pi itself performs HTTPS GET/POST, if its runtime and policy pass | Laptop performs API calls; it DOES NOT prove Pi ThinQ/TLS execution |
| Independence | Temporary laptop provisioning only, then short-lived Pi calls while authorized | Requires laptop continuously; not final independent product |
| Pi 4 portability | Native memory/Unix IPC/HTTPS guards reusable; Pi 4 runtime still separate | Network contract reusable, not proof of Pi 4 service support |

## A transport selection

1. Pathname Unix socket: 0700 parent + 0600 socket works for same UID; another SSH
   operator needs a verified group/ACL. No writable ATLAS path/group is assumed.
2. stdin/anonymous FD: ideal when supervisor can safely launch the *existing*
   service under its proper profile. SSH-shell root execution/runuser/setuid is
   NOT an approved substitute for PackageManager activation.
3. Chosen conditional prototype: Linux AF_UNIX SOCK_SEQPACKET abstract endpoint,
   avoiding invented writable directories/ACLs. Abstract sockets have NO file-mode
   access control. Kernel SO_PEERCRED UID is the mandatory server allowlist;
   helper verifies server UID AND read-only-confirmed service PID before reading
   token bytes. Socket names are not secrets/authentication. Root/default wildcard
   sender is rejected. Same-UID compromise remains in the trust boundary. Untrusted
   UIDs may attempt connection/DoS, but are rejected before supply; no token/HTTP
   processing occurs. Native listener has bounded packets/timeouts/one session.
   Endpoint exists only if platform bindings are verified; absent those, BLOCK.

This is Linux contract evidence, not proof AppArmor permits abstract Unix sockets.
- https://man7.org/linux/man-pages/man7/unix.7.html
- https://man.openbsd.org/ssh_config (StrictHostKeyChecking, BatchMode, forwarding)
- https://man7.org/linux/man-pages/man2/madvise.2.html (DONTFORK/DONTDUMP)

The supplied Build 246 CredentialManager1 describes password enrollment/verification
and a secure FD pattern, not an established PAT retrieval vault. KeyManager1 covers
key operations; PAT provisioning rights/use remain unverified. No new platform
API, permission group, uid persistence or password-enrollment workaround is invented.

## Roles and authorization

- Native service: its current PackageManager nonroot account.
- Operator: explicit, nonroot, SSH-capable principal; kernel-verified UID.
- Flutter: distinct package UID, allowed only Exchange/status, NEVER credential IPC.
- Production requires all three UIDs distinct. Host same-UID fixtures do not prove
  that actual ATLAS role separation or a usable nonroot SSH account exists.
- Exact service/UI/operator names must be read and reviewed after install. No
  numeric UID snapshot is hardcoded. If only root SSH is available, STOP: no auto
  root receiver, sudo, runuser, account/group change or profile relaxation.
- D-Bus sender UID is obtained using SDK Message.getCredsEuid(), not request JSON.
- Template own/call policy is generated only from reviewed names. Default IPK has
  no guessed account/policy and Live/readiness gates stay false.

## Secret and session lifecycle

User creates a least-scope experiment PAT manually at LG's official portal.
Tool waits for authenticated remote READY before getpass; PAT is never argv/env/
URL/D-Bus/MQTT/file. Binary stdin packet also carries SDK application header key
and a new per-experiment Client ID, without printing either. Neither enters Python
worker or Flutter. Standard required KR SDK headers only; no cookies/extra identity.

Native receiver keeps one locked anonymous buffer, DONTDUMP/DONTFORK, explicitly
wipes it; no disk persistence. Service restart ALWAYS loses credentials/session.
Token delivery does NOT arm manual control: session begins read-only, requires a
separate operator manual authorization AND per-command UI nonce/Profile/State checks.
Fixture never calls network. Verified platform flags separately gate read-only and
manual Live. Token receipt alone bypasses none of them.

TTL uses monotonic time; max 300 seconds, no refresh/rotation/duplicate injection.
SSH helper EOF/disconnect, explicit revoke, expiry or service shutdown closes the
lease; new requests stop, in-flight curl uses a cancellation callback plus bounded
connect/request timeouts. An already accepted POST cannot be undone by cancellation
or token revocation. Ambiguous outcomes remain unconfirmed; no retry/cleanup POST.
New credentials/session invalidate old command contexts and nonces.

Same-token re-injection after revoke/expiry is denied during this process using an
in-memory digest (never printed/persisted). Old session/boot envelopes never reopen.
A fresh service cannot remember raw PAT revocation without persistent state: it
requires new explicit provisioning; only LG-side revoke/expiry invalidates the PAT
itself globally. We do NOT claim cross-restart global PAT replay detection.

Memory is NOT complete protection from root, same-principal compromise, kernel,
RAM/SSH/TLS internals or physical attacks. Python getpass/OpenSSH necessarily hold
transient copies which cannot be rigorously erased. Core dumps are disabled and
native locked buffers wiped; TLS/header copies are minimized and wiped after use.
No claim is made that revocation cancels the device's completed action.

## Acceptance boundary

Fake-token host tests exercise Linux peer credentials/session lifetime/denial and
mock HTTPS; SDK D-Bus UID API + policy XML are compiled/static tested. Actual Pi UID,
AppArmor, daemon policy load, helper execution and CA/TLS remain untested. B is a
fallback design only; no unauthenticated laptop Gateway is implemented.
