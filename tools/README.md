**English** | [한국어](README.ko.md)

# tools — development and experiment helper scripts

A collection of tools for moving development forward even without the real hardware. They are not part of the product's production path.

## What's here

| Tool | Purpose |
|---|---|
| [`node-red-visualizer/`](node-red-visualizer/README.md) | Subscribes to and observes the Pi 4 MQTT broker from a PC, and injects synthetic `state/phase`, `feedback/user`, and `display/message` messages. For checking the display path before hub MQTT publishing existed |
| [`sensor_monitor.py`](sensor_monitor.py) | Reads the ESP32 USB directly and shows **raw data and decisions together** on a single browser page (default `127.0.0.1:8777`) — environment and mmWave values, raw 8×8 heatmaps from both ToF sensors (`tof_grid` lines turned on with `z` at startup, firmware 0x0123+), posture/presence/drowsiness cards, 60 s trends, per-line reception rates, board log. Does not reset the board when opening the port. `python tools/sensor_monitor.py --port COM8 [--record out.jsonl]` |
| [`uart_mqtt_bridge.py`](uart_mqtt_bridge.py) | Converts ESP32 USB (UART0) JSON lines into MQTT sensor envelopes and records them as per-day JSONL |
| [`mqtt_scenario_sim.py`](mqtt_scenario_sim.py) | Publishes a synthetic sensor scenario (sit down → typing → still/drowsy → recovery → leave) over MQTT exactly per the contract. Rehearse the hub, Node-RED, and Pi 5 without the ESP32 or collector |
| [`rehearsal_local.py`](rehearsal_local.py) | Launches an amqtt local broker + mock plug + sim (short·demo) + `hub run --config fsm.demo.yaml` all at once and summarizes transitions (about 6 minutes). With `--host 0.0.0.0` the Pi 5 app can connect too |
| [`find-boards.ps1`](find-boards.ps1) | Finds the Pi 4 and Pi 5 by MAC on the current Wi-Fi (e.g. a phone hotspot) and prints ssh commands; with `-UpdateSshConfig` it updates the `ssh atlas-hs` and `ssh pi5-hs` aliases |
| [`ui_step_sim.py`](ui_step_sim.py) | Without the hub, sends the messages the app receives (state, request, report) at 10-second intervals to check the Pi 5 screen flow in 1–2 minutes. Shows the screen's responses on the console |
| [`mqtt_watch.py`](mqtt_watch.py) | MQTT monitor for on-device checks (read-only) — one line per topic with only the values needed for the check. Checklist: `docs/field-checklist.md` |
| [`fatigue_logger.py`](fatigue_logger.py) | Fatigue data collection recorder (read-only) — for each work session, writes `state/phase` ticks and Pi 5 KSS responses to `ml/datasets/fatigue/raw/<profile>/<session>.jsonl`. Procedure: `docs/fatigue-dataset.md` |
| [`measure_tick.py`](measure_tick.py) | Time distribution (p50, p95, p99) of a single hub tick — check the decision cycle ≤ 500 ms on a PC first. Measure again on the real Pi 4 |
| [`demo_dryrun.py`](demo_dryrun.py) | Feeds the demo scenario through the hub without a broker or real time, to check transitions, control, and reports in a few seconds. Flow test: `test_demo_scenario.py`; the flow itself: [`docs/demo-scenario.md`](../docs/demo-scenario.md) |
| [`mock_plug.py`](mock_plug.py) | Mock plug that receives `deskmate/control/cmd` and responds on `control/result` (delay/failure options, `control/state/<target>` retain). For rehearsing the control path before choosing a real plug model |
| [`ble_fan.py`](ble_fan.py) | Control CLI for the Bluetooth fan (Pico 2 W, `firmware/pico2w_ble_fan/`) (`scan`, `on`, `speed 60`, `watch`…) and a laptop `vent_fan` adapter (`bridge --broker <ip>`). Normally vent_fan is handled by the Pi 5 app, so use bridge only when testing without the Pi 5. Requires `pip install bleak` |
| [`send_control.py`](send_control.py) | Sends a single `control/cmd` in place of the hub and waits for the result (`vent_fan set_level 3`, etc.). For checking the Pi 4 → Pi 5 → device path without the FSM |
| [`uart_frame_tool.py`](uart_frame_tool.py) | Prints test vectors for, encodes, and decodes UART2 binary frames (COBS+CRC-16), and reads ESP32 UART2 directly via USB-TTL. Shares the codec in `hub/deskmate_hub/ingest/uart_frame.py` |
| `connect-deskmate-pi4.ps1` | Searches for the Pi 4 address, which changes via DHCP, and updates the SSH aliases (`atlas`, `rpi4`, `deskmate-pi4`) |
| [`atlas-hotspot-broker/`](atlas-hotspot-broker/README.md) | Development communication path that joins the Pi 4 and Pi 5 via a phone hotspot and runs a statically built Mosquitto on the Pi 4 (for environments where opkg is unavailable) |

## Useful to build (in priority order)

| Tool | Purpose | When it's needed |
|---|---|---|
| `log_recorder.py` | Records all topics and UART lines as JSONL | Accumulating measurement logs → source for `--replay` tuning |
| `plot_session.py` | Session timeline visualization (posture · body motion · typing · CO₂ · phase) | Threshold tuning, report figures |
| `tof_probe.py` | Real-time view of VL53L9CX raw and downscaled depth maps | Path A/B spike and posture feature validation |

## Usage

```bash
python -m pip install -r tools/requirements.txt
python tools/uart_mqtt_bridge.py --port COM5 --baud 115200 --broker <pi4-ip> --node esp32
python tools/mqtt_scenario_sim.py --broker <broker-ip> --scenario default   # real timers, about 25 minutes
python tools/rehearsal_local.py 400                                          # PC only, short-timer profile
python tools/demo_dryrun.py                                                  # check the 5-minute demo flow in a few seconds
python tools/measure_tick.py                                                 # decision cycle p95 (PC)
python tools/rehearsal_local.py --scenario demo --host 0.0.0.0              # real-time rehearsal of the 5-minute demo (+Pi 5 screen)
python tools/log_recorder.py --broker <pi4-ip> --out logs/
python tools/plot_session.py logs/2026-08-01.jsonl
```

Bridge options can also be set via the environment variables `DESKMATE_SERIAL_PORT`, `DESKMATE_SERIAL_BAUD`, `DESKMATE_MQTT_BROKER`,
`DESKMATE_MQTT_PORT`, `DESKMATE_NODE`, and `DESKMATE_LOG_DIR`.
Sensor envelopes are written to `logs/<node>-YYYYMMDD.jsonl` regardless of whether MQTT is connected.

## Intervention cycle verification

- [intervention_check.py](intervention_check.py): read-only collection of intervention MQTT traffic and evaluation of what was stored. Does not publish sensor, feedback, or device control messages.
- [test_intervention_mqtt.py](test_intervention_mqtt.py): uses a loopback broker, LiveHub, and external mocks to verify the 5 response types as well as no response on the screen, control failure, and missing results.
- Installation, execution, and evaluation limits: [개입 한 사이클 검증](../docs/intervention-cycle-check.md) (Verifying one intervention cycle).
