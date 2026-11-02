# PR81 provenance

Copied without behavior changes from PR #81 commit 8285600:
https://github.com/ddkk0714/2026ESWContest_smart_TEAMMATE/pull/81

- poc/session.py, poc/worker.py, poc/fixture.json
- tools/thinq_connect/probe.py, air_quality.py, manual_trial.py and package markers

The laptop host uses Session validation and Transport restrictions. worker/fixture are used only by tests.
The Pi5 native credential broker, account rules and Atlas permissions remain unchanged.
Official SDK: https://github.com/thinq-connect/pythinqconnect (pinned thinqconnect 1.0.14 in Dockerfile.thinq).
