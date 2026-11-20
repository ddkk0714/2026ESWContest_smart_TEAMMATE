# PR81 provenance

Originally copied from PR #81 commit 8285600 (laptop changes are listed below):
https://github.com/ddkk0714/2026ESWContest_smart_TEAMMATE/pull/81

- poc/session.py, poc/worker.py, poc/fixture.json
- tools/thinq_connect/probe.py, air_quality.py, manual_trial.py and package markers

The laptop host uses Session validation and Transport restrictions. worker/fixture are used only by tests.
The Pi5 native credential broker, account rules and Atlas permissions remain unchanged.
Official SDK: https://github.com/thinq-connect/pythinqconnect (pinned thinqconnect 1.0.14 in Dockerfile.thinq).


## Laptop changes in the isolated 2026-11-04 work
- poc/session.py: recheck approval deadline and owner authorization AFTER the fresh GET and immediately BEFORE POST; expose job/wind through the same Profile-based field validator.
- The remaining PoC parser/transport/fixture code is reused. These changes are not applied to the existing Pi5 PoC or installed devices.
- appliance_contract.py: exact copy of the previously verified ApplianceCache from `thinq-board-integrated-host-20261104-xos8jkdk/source/hub/deskmate_hub/ingest/appliance.py`. This is a validated successor of the 1ee37c6 bridge, not a claim of byte identity with that commit. The current isolated Hub copy is byte-identical to this vendored copy.
- No official SDK, platform binary or credential is vendored. thinqconnect remains 1.0.14.
