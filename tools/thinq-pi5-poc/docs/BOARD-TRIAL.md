# Separate future board trial — NOT executed

## Approval and hard blockers

1. Reconfirm current IP/SSH, existing DESKMATE UI and Pi 4 broker without restarting.
2. Independently check whether the old pythondiag temporary policy was removed.
   Its cleanup was not established; do not assume rollback or alter it here.
3. Verify new package IDs are unused, hashes match, current SDK/OS ABI is compatible.
4. **Before any app/service activation**, decide the new service own-name and exact
   UI caller policy from official PackageManager mapping and installed identities.
   These IPKs intentionally contain NO .conf, no guessed UID, no root own or global
   allow. They may receive AccessDenied. Never relax existing/global policies.
5. Verify /restricted Python path, standard libraries, exec/fork/AppArmor under THIS
   new service profile, libcurl/OpenSSL/CA roots and TLS permissions. Earlier
   pythondiag host or board evidence is not this new service's verification.
6. Establish an officially supported, access-controlled credential delivery design
   without PAT/Client ID/device ID in files, shell args, env output, logs, UI or IPC
   broadcast. Anonymous FD delivery is a design candidate, NOT validated on ATLAS.
   No credential provider exists yet. Do NOT send a real PAT to this app or service.

If any gate fails, stop with its exact layer. **Live tests cannot be performed with
these IPKs by simply granting tomorrow's approval**: provider/policy integration,
review and a new build are still required. No hidden --live escape hatch exists.

## After separate approval: isolated Fixture installation first

Use existing ARC/PackageManager install-only procedure with
`run_after_installation=false`; inspect the actual API signature before calling.
Ordinary ARC install may introspect/activate, so do not use it as install-only.
Inspect serviceinfo, PackageManager assigned username/UID, activation, final own
and method policies BEFORE Exchange. Do not borrow perm_thinq or old diagnostic
account. Do not start/replace com.atlas.app.deskmate_display. A PoC window may take
foreground later; preserve the original process and obtain explicit run approval.

Run one read, default-OFF rejection, approval cancel, Fixture LOW -> GET match ->
Fixture attestation -> separately approved AUTO -> GET match. Confirm no external
HTTPS request occurred in Fixture mode and no existing Hub/MQTT/UART/BLE changes.
Record D-Bus activation separately from Python exec/import and Fixture logic.

## Only after secure provider and Live implementation are separately reviewed

1. Read-only PAT first: GET devices -> exactly one matching purifier -> Profile ->
   State. Keep private IDs entirely inside execution layer. Return allowlisted
   metrics, support/mode and safe HTTP/code only. Verify CA/TLS and no redirects.
2. Do not infer online or fresh physical sensing solely from HTTP 200. Stop on
   offline, 401/403, rate limit, malformed profile, missing prerequisites.
3. With control permission and new explicit approval, POWER_ON/CLEAN/AUTO,
   no external operation, actual Profile LOW/AUTO write support: one LOW POST.
4. Record API acceptance, separate GET LOW match, actual body/airflow observation
   as THREE independent checks. Response loss: GET only, no resend/cleanup POST.
5. AUTO restoration: separate approval, same device/session, LOW unchanged,
   physical LOW attested and no external change. One AUTO POST + separate GET +
   physical confirmation. If uncertain, stop and let user restore in ThinQ app.

Never use CO2 as a purifier fan command reason. This is a manual trial, not FSM or
Live Gateway control. Pi 5 success is not proof of Pi 4 deployment or full E2E.

## Rollback, only with explicit approval

No board changes were made today, so there is nothing to restore on a board.
After a future trial, close only the PoC app and stop/reap only the identified
com.deskmate.thinqpoc1 process; remove only these TWO new packages through supported
PackageManager/ARC procedure if removal is approved. Do not uninstall the old
DESKMATE app, change account mappings manually, restart D-Bus/OS or modify global
policy. Check original UI/Mosquitto again. Removal errors or unexpected policy
changes are stop conditions. There is NO automatic cleanup POST.

## Final product decision

Pi 4 independent service is the preferred final placement to evaluate. The
portable HTTPS guard and fixtures can be reused, but Pi 4's restricted Python,
asyncio/_socket, libraries, profile, secrets, time sync, lifecycle and MQTT all
require independent tests. This PoC deliberately leaves Hub/FSM/MQTT/UI integration
and PR #56/#57/#65 history untouched.

## Selected official-document check (read-only, Build 246)

The supplied CredentialManager1 reference was consulted for its public method
summary and password FD handoff. It describes password enrollment/verification,
not a verified PAT retrieval provider for this PoC. A secure-container FD pattern
is a relevant design lead, not proof that our new app/service may use it. The
KeyManager1 method overview concerns cryptographic keys/operations; suitability,
permissions and actual presence for PAT provisioning remain unverified. Do not
substitute password enrollment or privileged synthetic-password/keystore grants
for our missing credential provider. No such API was called. Original confidential
PDFs remain in <privately supplied LG documentation root>; none is copied into this project
or IPKs. These document findings require platform-owner/board review before code
can turn them into an approved credential handoff.
