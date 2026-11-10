**English** | [한국어](README.md)

# hub — Raspberry Pi 4 central inference hub

Receives the sensor streams sent by the ESP32 node (UART2) and the PC collector (MQTT), builds feature vectors over sliding windows (30 s – 5 min),
computes the state and its confidence, and publishes them over MQTT. Control decisions are also made here.

**Runtime (final):** The Pi 4 runs AI Native OS Headless (ATLAS). The image has no Python or compiler, so
the native service IPK in [`atlas/`](atlas/README.md) runs this package with `/restricted/python3`.
UART decoding (COBS/CRC-16) and the MQTT client live in that C++ service, which exchanges data with Python over a line protocol (the `bridge` subcommand).
No development tools are installed on the board.

## Modules (2026-09-14)

| Directory | Owner | Contents | Status |
|---|---|---|---|
| `atlas/` | Shared | ARC IPK native service. Runs the FSM with restricted Python, HTTP 8765 development API | ✅ Running. UART receive and MQTT publish not yet implemented |
| `mqtt/` | Minhyuk Lee | Pi 4 Mosquitto configuration | ✅ |
| `ingest/` | Minhyuk Lee | MQTT sensor topics (mmwave·env·keystroke) → `SensorCache` → 10 s `SensorFrame` (provisional scales in `config/ingest.yaml`), freshness·seq gaps, receives `feedback/user` | 🟡 MVP path implemented (09-14). UART line input to be added to the same cache in the integration MVP |
| `features/` | Taehwan Kim | `baseline.py`: session START calibration window → per-metric median·MAD → Modified z → `phi/delta`, time-of-day bucket seed, opt-in persistence | 🟡 Implemented 09-16 (wired to ingest). ToF geometric features and environment trends come after the sensors |
| `inference/` | Soyeon Park | Rule-based FSM (stage 1), confidence formula, confidence gate | ✅ 18 states, 46 tests (45 pass · 1 xfail) |
| `inference/` | Myeonghee Cho | TFLite lightweight classifier loading · inference (stage 2) | ⬜ Optional dependency |
| `control/` | Myeonghee Cho | `dispatcher.py`: ACTION_ENV → `control/cmd` (reversible actions only, cooldown, no automatic irreversible actions) → `control/result`/timeout → action_done, pending/expiring suggestions, undo on reject. `config/control.yaml`. Built-in Mock adapter | 🟡 09-16. Real smart-plug adapter after the model is chosen |
| `config/` | Shared | Thresholds · topics · device settings (YAML) | ✅ `fsm.yaml`, `ingest.yaml` (provisional sensor→signal scales) |
| `live.py` | Shared | `run` command: ingest → FSM → publishes `deskmate/state/phase` (retain, QoS 1), records frames as replay-compatible JSONL | 🟡 MVP (09-14). E2E verified with a local amqtt broker, waiting on real Pi 4 Mosquitto integration |
| `ingest/uart_frame.py` `uart_source.py` | Shared | UART2 frame codec (COBS·CRC-16/CCITT-FALSE·TYPE structs) and `UART\t` line / raw serial sources. 10-17: decoder for raw ToF 8×8 per-sensor frames `0x05` and `TofGridAssembler` (merges sensor frames with the same frame_id into one sample, `SensorCache.tof_grid`, memory only) | ✅ 09-15, 14 tests + 4 for 0x05 |
| `ingest/tof_service.py` | Shared | **Verdicts from the separate posture service (deskmate_tofposture, GRU, HTTP 8771) → FSM** (10-23). Receives `/api/state` via `TOFSVC	<json>` lines (on the board: the `hub/atlas` C++ polls every 1 s) or a Python poller (PC) and stores it as a `tof_service` sample; if `ingest.yaml tof.posture_source: service`, that posture and confidence become the posture Signal (9 labels, drowsy → fatigue evidence). If absent, falls back to MLP → 0x03 rules | ✅ Default in the Pi 4 release. Tests in `tests/test_tof_service.py` |
| `posture/tof_mlp.py` | Shared | **ToF posture MLP, pure Python** (10-17: the model runs on the Pi 4, not the ESP32). Per-sensor 8×8 → per-sensor baseline (median of a quiet 10 s after sitting down) · 192 features · 192→64→10 → sum of both sensors' log-softmax → 1 s majority vote → `SensorCache.tof_mlp` → `mapping.py` posture signal (`tof.posture_source: mlp`; if it cannot produce one, falls back to the 0x03 rule verdict). Weights in `config/tof_mlp.json` (`dataset/tof_sim/export_mlp_json.py`, bundled in the Atlas zip) | ✅ 8 tests. **Candidate A (undecided)** — candidate B is the separate service `deskmate_tofposture` (HTTP 8771, `tof_sim_v5` PostureNet, with `hub/atlas` relaying 0x05 to tcp:9100). To be chosen by measured accuracy. Simulated 8×8 weights — waiting on a measured 16×8 model |
| `replay.py` `demo.py` `preview_api.py` `service_bridge.py` | Soyeon Park | Replay harness · synthetic demo · HTTP preview · Atlas line bridge | ✅ Includes `report.py` · `--report` |

## Design rules

- **Do not hard-code thresholds.** Move all of them into `config/` YAML.
  After the August field measurements it must be possible to re-run experiments by changing only the values.
- The stage-1 FSM must **work fully on its own**, without the stage-2 classifier.
  The classifier is an optional dependency so the hub does not die even if its import fails.
- Target decision cycle: 500 ms or less.
- Do not commit ThinQ credentials. Use environment variables or `config/secrets.yaml` (gitignored).

## Running

```bash
cd hub
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m deskmate_hub demo --host 0.0.0.0 --port 8765
```

The command above feeds synthetic sensor input into the FSM and cycles the state every second. The Pi 5 display
reads the state from `http://<Pi4-IP>:8765/api/state` and returns accept/reject via `/api/feedback`.
This HTTP API is an **adapter for cross-board screen verification before the final communication path is decided**. Regardless of
whether MQTT is adopted, the FSM and screen contracts stay the same and only the adapter is swapped.

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/api/state
```

When the Atlas app's **Sensor Test** screen sends input to `/api/test-frame`, automatic cycling stops and
virtual time advances using the same `FSMEngine` and `config/fsm.yaml`. The input is not raw sensor values but
normalized `phi`·`delta` contributions relative to the baseline; this is a development-only API and its input is not stored as operational input.
`reset` virtually advances through the configured baseline time to enter the focus state quickly, and
`tick` advances by the 30 seconds or 3 minutes chosen in the app.

```bash
python -m deskmate_hub --demo                            # synthetic session smoke test
python -m deskmate_hub --replay logs/2026-08-01.jsonl    # log replay
python -m deskmate_hub --demo --report                   # + session work report (focus time, fatigue episodes, intervention outcomes, ESM labels)
```

### Live with real sensors (MVP 2026-09-18)

```bash
python -m deskmate_hub run --broker <broker-ip>            # default port 1883, logs in logs/
python -m deskmate_hub run --broker <ip> --ingest-config my-ingest.yaml --log-dir logs
```

- Subscribes: `deskmate/sensor/#` (mmwave·env·keystroke — envelope (including collector) or legacy flat payload), `deskmate/feedback/user`
- Publishes: `deskmate/state/phase` (retain, QoS 1, every 10 s), `deskmate/interaction/request` (when entering ACTION_* through the suggestion gate, no retain), `deskmate/control/cmd` (ACTION_ENV and ACTION_POSTURE, `control.yaml actions.<cause>` — since 10-24 posture-related causes (drowsiness) also issue a `desk_lamp` command, and a personalized device arm takes precedence if present. Since 10-25 brightness is `adjust_brightness {percent}` = % relative to the current brightness (environment +30 · drowsiness +60); undo uses `set_brightness` with the result's previous value), `deskmate/health/hub` (LWT). Subscriptions include `deskmate/control/result`
- Without a control adapter, a command closes as timeout after `result_timeout_sec` and the FSM keeps going. For rehearsal use `python tools/mock_plug.py --broker <ip>`. With `control.yaml adapter: mock`, commands succeed immediately in-process
- Mapping lives in `config/ingest.yaml`. With `normalization: baseline` (default), the personal baseline built in the START calibration window (median·MAD Modified z) is used first; metrics without a baseline, and the period before calibration, fall back to a linear ramp. With `baseline.persist.enabled: true`, only median·MAD·sample count are saved to JSON (opt-in).
- `--config deskmate_hub/config/fsm.demo.yaml` is a **demo/rehearsal profile with the same thresholds but shorter timers** (baseline 30 s, away 60 s, etc.). It is not the operational default.
- Executing the arm chosen by the bandit (`control.yaml arm_notice:`, 10-26): device arms (wake_light, etc.) become control commands; arms without a device (posture card · chime · voice) are delivered, even through the automatic gate, as a notice card (`auto: true`) + `speaker speak`/`chime`, and in that case the default lamp is not triggered (the default lamp fires only when there is no policy decision). The demo profile uses `personalize.bandit.algorithm: greedy` (means only) so that the onboarding prior decides the device — for drowsiness, exam student (light high · sound low · wind off) gets wake_light 0.55 > sound_cue 0.35, and university student (light mid · sound low · wind off) gets wake_light 0.45 > 0.35 — both the mood lamp (margin 0.20 vs 0.10); with no onboarding or a neutral one, sound_cue 0.55 > wake_light 0.45, so a chime; **for the demo, onboarding must be sent first.** The board default is Thompson.
- REST entry notice (`control.yaml rest_notice:`, 10-26): whichever way REST is entered — accepting a rest suggestion, being forced after a failed intervention, or a drowsiness retry — it sends `interaction/request kind rest_notice` (a notice card with only a close button, with text · rest minutes · reason) + `control/cmd speaker speak {text}` (card only if the user's sound_pref is off). REST re-entry cooldown is 120 s, with one more reminder if it drags on (reminder_after_sec). The demo profile sets 30 s/120 s in its `control:` section.
- Personalization policy (`personalize.yaml`, 10-25): `policy.causes: [posture, cognitive]` — the bandit only picks arms (wake-up · posture · rest) for posture and cognitive causes; environmental causes only issue `control.yaml actions.environment` (fixed by the FSM). Environmental episodes are still logged with `source: fsm_fixed`.
- Stage-2 fatigue model (`personalization.yaml`, 10-25): Release `stage2_portable_v9_20261024` (config/stage2/, trained in simulation) runs on the board as portable_cnn in observe-only mode — `state/phase sensor_summary.personalization` (label · confidence). It is not an FSM input. Unavailable when normalization is linear (demo-b).
- The environment variable `DESKMATE_PROFILE=demo-a|demo-b` deep-merges the `fsm:` · `ingest:` · `personalize:` sections of `config/profiles/<name>.yaml` on top of the default config (both `run` and bridge, 10-24). demo-a = fatigue hold 20 s · MONITOR window 30 s · rest 60 s · auto 0.60 · PC window 120 s · elapsed time reset after rest; demo-b = demo-a + calibration 10 s · auto 0.55 · confirm 0.60 · linear normalization. The personalization policy gate.conf_auto is set to the same value (if they differ, the policy downgrades automatic actions to suggestions). **Demo only (simulation-based) — do not use it for recording measured successes.** On the board it is one line in `hub.env` (`hub/atlas/README.md`). With demo-a you must sit upright through the 300 s calibration before putting your head down (putting your head down during calibration keeps the score from rising); demo-b is the main demo profile. 10-26 field test (suspect 0.485 while sitting upright and typing): the demo profiles recalibrate the keystroke linear ramp above this collector's measured p90 (ingest `keystroke` — the default ramp is the fallback for baseline normalization, so in the linear demo 70–81 % of awake working windows had δ·φ at 1.0) and remove the 0x03 rule drowsiness flag and nodding from the score (`tof.rule_drowsy_delta` · `nod_delta_max` 0.3 → 0). Drowsiness escalation is handled by the model's face_down (floor 0.99) and drowsy (0.80).
- `logs/frames-*.jsonl` replays as-is with `--replay`, and `logs/state-*.jsonl` holds the published envelopes.
- One console line = one tick: state · ctx · C_fatigue/C_focus · present · pc_ratio · available signals (first letters `ekprs`, `.` = unavailable)

### Real-sensor mode of the Pi 4 native service

The `hub/atlas` C++ service launches `python -m deskmate_hub bridge`. With the environment variable `DESKMATE_HUB_MODE=live`, instead of the demo cycle it
feeds stdin `UART\t{"type","seq","ts_ms","payload_hex"}` lines (frames that the C++ side has COBS-decoded and CRC-checked) and, if `DESKMATE_MQTT_HOST` is set,
MQTT sensor topics together into `SensorCache`, and runs the same `LiveHub` loop. Results go out as `STATE\t` lines (HTTP 8765) and MQTT `state/phase`.
The frame protocol and payload structure are in `ingest/uart_frame.py` (= `docs/data-spec.md` §13.1); the PC verification tool is `tools/uart_frame_tool.py`.

Remaining: on the C++ side, `/dev/serial0` receive → COBS/CRC → `UART\t` line output (integration MVP 10-05). HTTP 8765 is kept as a fallback for sensor tests and separate screen verification.

## Tests

```bash
pytest tests/
```

FSM state transitions are unit-tested with synthetic input — the only part that can be verified without real hardware.
