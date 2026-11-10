**English** | [한국어](README.ko.md)

# DESKMATE

**24th Embedded Software Contest (Korea) · Smart Home Appliance Track · Team TEAMMATE**
**A smart desk that monitors cognitive state with contactless sensing**

> **Just place it on your desk — it acts before you have to ask.**
> DESKMATE fuses four signal groups — ToF posture, 60 GHz mmWave, keystroke timing, and the room environment — to infer your work state (start · focus · fatigue · end) **every 10 seconds**.
> It then picks one intervention matched to the cause of fatigue (environment · posture · elapsed time) — lighting, a fan, or a break reminder — and either **suggests it or runs it automatically in a way you can undo**.

- **No camera, no microphone, no key values collected.** Inference, control, and personalization all run on an ESP32, a Raspberry Pi 4, and a Raspberry Pi 5, with no internet connection.
- Decisions are made by a rule-based FSM; each person's different reactions are learned on the Pi 4 and fed into the next intervention.

## Demo video

[![DESKMATE demo video](https://img.youtube.com/vi/7c-AGWE3HJg/maxresdefault.jpg)](https://www.youtube.com/watch?v=7c-AGWE3HJg)

▶ [2026ESWContest Smart Home Appliance — TEAMMATE demo video (YouTube, Korean)](https://www.youtube.com/watch?v=7c-AGWE3HJg)

| Source code | Demo video | Final report · Project description |
|---|---|---|
| This repository | [YouTube](https://www.youtube.com/watch?v=7c-AGWE3HJg) | Submitted for the finals |

---

## What it does

| Stage | What DESKMATE senses and decides | What you see on the 7" screen |
|---|---|---|
| **Sit down · start** | Detects you sitting down with mmWave; collects posture, typing, and environment baselines over the first 5 minutes; classifies the session as PC / mixed / non-PC | Profile selection |
| **Focus** | Every 10 s, computes `C_fatigue` and `C_focus` from ToF posture, keystroke timing, and CO₂ | A quiet clock, dimmed while you are focused |
| **Fatigue builds up** | `C_fatigue` ≥ 0.40 for 180 s → suspected, ≥ 0.70 for 180 s → confirmed | — (you may not notice yet) |
| **Intervention** | Picks the single largest contributing cause and acts through a 3-level confidence gate | Suggestion card (reason · sensor values · response) |
| **Check · recovery** | Checks the effect 10 minutes after the intervention. If things improved → recovered; otherwise → next cause. Leaving the desk ends the session | Session summary (number of fatigue episodes · one-line feedback) |

**Confidence gate:** **< 0.45 log only · 0.45 – 0.75 + α suggest · ≥ 0.75 + α run automatically.**
α is a per-person threshold that rises by +0.05 (up to 0.25) every time the user undoes an action. An intervention that was undone during a session is asked about (never auto-run) for the rest of that session.
Irreversible actions are never auto-run, and every automatic action **can be undone within 60 seconds.** The same command has a 300 s cooldown and is limited to 6 times per session.

---

## System architecture

```
              [ Sensing ]                       [ Decision ]                          [ Output ]

  ESP32 · Arduino C++ ─────┐
   VL53L8CX ToF 8×8 ×2     │  USB serial               Raspberry Pi 4                   Raspberry Pi 5
   C1001 60 GHz mmWave     ├─ COBS + CRC-16 ──────►   AI Native OS Headless ─Ethernet─► AI Native OS Video
   SCD41 CO₂ · BH1750 lux  │  115200 · wired           · native service (C++17)    MQTT  · Atlas Flutter 7" touch app
   DHT22 temp/humidity     │                          · ToF posture service       ◄──── · suggestion cards · accept · undo
                           │                            (CNN+GRU)                          │
                           │                          · 18-state FSM (Python)              │
  PC collector ─ Wi-Fi MQTT┘                          · control · personalization      │ BLE · A2DP
   pynput · key timing only                             · bandit                           ▼
                                                      · Mosquitto 2.0.20            iLink mood lamp · Pico 2 W fan · speaker
                                                                                    LG ThinQ air purifier (integration planned)
```

| Layer | Device · runtime | Responsibilities | Directory |
|---|---|---|---|
| Sensing | ESP32 · Arduino C++ (PlatformIO) | Reads 2× ToF, mmWave, and 3 environment sensors; first-stage preprocessing; COBS transmission. 5 Hz per ToF sensor | [`firmware/esp32_sensor_node`](firmware/esp32_sensor_node) |
| Sensing | PC · Python | Input timing without key values (dwell · flight · idle · correction) | [`collector/`](collector/) |
| Decision | Raspberry Pi 4 · AI Native OS Headless | USB decoder and MQTT 3.1.1 client (C++), posture inference, sensor fusion, FSM, intervention selection, MQTT broker | [`hub/`](hub/) |
| Output | Raspberry Pi 5 · AI Native OS Video | Flutter 7" touch app, suggestion cards, drives the BLE mood lamp, fan, and speaker | [`display/`](display/) |
| Controlled device | Pico 2 W | BLE fan (speeds 1 · 2 · 3, natural-wind mode, off timer) | [`firmware/pico2w_ble_fan`](firmware/pico2w_ble_fan) |
| Training | PC · PyTorch · ONNX · MuJoCo | Posture model training, MuJoCo simulation, shared models | [`ml/`](ml/), [`dataset/`](dataset/) |

> DESKMATE meets both contest bonus criteria: **integration of three hardware boards** (ESP32 + Pi 4 + Pi 5; the organizers supplied a Pi 5 in place of the Pi Zero) and **multimodality** (four signal groups judged together in one 10 s window).
> If the Pi 5 goes down, the Pi 4 keeps inferring state and making control decisions. Thanks to MQTT retained messages, state is synchronized immediately on reconnect.

---

## Key implementation

### 1. Four signal groups judged together in one 10 s window

| Signal | Samples per 10 s window | On its own | Combined |
|---|---|---|---|
| **ToF** VL53L8CX ×2 | 50 × 2 | No judgement possible if occluded or faulty | Raw 8×8 per sensor goes only to the Pi 4; inference per sensor, then probability fusion; falls back if one sensor fails |
| **mmWave** C1001 | 10 | Instantaneous heart rate swings between 86 and 126 | Only presence, body movement, and loss of breathing signal are used as evidence; always on |
| **Keystrokes** | 10 | No signal for work away from the PC | The PC-usage ratio switches the context so posture and environment get more weight |
| **Environment** SCD41 · BH1750 · DHT22 | 2 | High CO₂ means nothing if nobody is there | Identifies the environment as the cause of fatigue and selects ventilation or lighting interventions |

A missing signal is removed from both the numerator and denominator and the weights are renormalized. Unplug one sensor and the score keeps going.

### 2. ToF posture classification — trained in simulation, inferred on the board, calibrated on real data

- **9 posture classes:** upright · leaning forward · leaning back · face-down on desk · chin on hand · drowsy · away from desk · sensor misplaced · forward head (turtle neck).
- With no real labels available, the model is trained on a **MuJoCo virtual human and virtual sensor** (`dataset/tof_sim_v5`), and a per-sensor CNN+GRU runs on the Pi 4 in a standard C++ service (`hub/atlas-tof`).
- **Models were chosen by real-world scoring, not simulation score.** Out of 17 candidates, we used the one ranked first with a real-world macro-F1 of 0.456.
- Instead of retraining, we calibrated on the board: a face-down stage detection rule and a misplacement gate gave **100 % face-down stage detection (0 false positives) and reduced false "sensor misplaced" calls from 76 % to 0 %** (real-data replay, 2026-10-26).

### 3. Rule-based FSM — 18 states, cause routing

- **18 states in 6 layers** (idle · start · focus · judgement · follow-up · output), condensed to 6 phases on screen. Evaluated every 10 s, at most one transition per tick. Spec: [`docs/fsm-spec.md`](docs/fsm-spec.md).
- **Fatigue weights (PC / non-PC):** typing 0.35 / — · posture 0.25 / 0.40 · breathing (off) 0.15 / 0.25 · environment 0.10 / 0.20 · elapsed time 0.15 / 0.15.
- It picks **"why you are tired"**, not just whether you are. It intervenes only on the single largest cause (environment · posture · cognitive), and moves on to the next cause if there is no effect.
- Direct rules: leaning forward + typing stopped for 60 s → posture; high temperature for 120 s → environment. A combined-condition (guard) layer evaluates multi-signal ANDs in parallel with the weighted sum.
- All weights, thresholds, and timers live in [`hub/deskmate_hub/config/*.yaml`](hub/deskmate_hub/config). After field measurements, only values change — no code changes.

### 4. Shared models and on-board personalization

- **Personal baseline:** calibrated within the first 5 minutes using a median/MAD modified z-score.
- **Contextual-bandit intervention selection:** looks at time of day, illuminance, CO₂, and drowsiness, and chooses among per-cause candidates (mood-lamp wake-up · brighter/dimmer light · voice guidance · chime · break suggestion · ventilation fan). Acceptance, undo, and the effect 10 minutes later feed into the next choice.
- **Reaction learning:** `c = c_FSM + clip(α · (p − c_FSM))`, bounded to −0.05 … +0.25, with α growing from 0 → 0.8 as labels accumulate. Labels come from KSS responses, accept, reject, undo, no response, and confirmation cards.
- **Shared models:** a 1D CNN over 17 features × the last 6 ticks (1,956 parameters, 0.78 simulation accuracy) and a common reaction model (24 virtual people × 14 days, 4,756 labels). Shared models are **observation-only** and never change FSM decisions.
- Light, sound, and wind preferences and sleep time from the initial setup are used as policy priors, stored separately per `profile_id`.

### 5. From command to device

| Device | Connection | Actions |
|---|---|---|
| iLink mood lamp | Pi 5 BLE GATT | On · off · brightness relative to current (wake-up +60 % · environment +30 %); rediscovers and reconnects if dropped |
| DESKMATE fan (Pico 2 W) | Pi 5 BLE | Speeds 1 · 2 · 3 (duty 30 · 45 · 60 %) · natural-wind mode · off timer |
| Speaker | Pi 5 A2DP | 3 chimes · voice guidance · focus music |
| LG air purifier | ThinQ API | Fan-speed control and air-quality readings (PM1 · PM2.5 · PM10 · odor) verified on the real device. FSM integration planned |

- Pi 4 FSM → `control/cmd` → the Pi 5 app executes over BLE → `control/result` (QoS 1, result within 15 s). Command, execution, and result are bound into a single contract.
- Results carry `previous` and `actual`, so undo restores the previous state. A value the user set manually takes priority over automatic commands.
- **After 1 minute away from the desk**, the mood lamp, fan, and music are turned off; after sitting back down with 10 s of presence, they are restored to their state just before shutting off.
- Connection failures are handled with retries, ordering, and silence: the three devices (lamp · fan · speaker) are restored in order, up to 3 attempts each, and live links are never dropped.
- For each command, a control progress log records **"why → what → result → time taken → undo"**. A posture intervention counts as successful when the person returns to an upright posture.

### 6. Pi 5 touch app and developer view

- Low-stimulation focus screen, profile selection, suggestion cards (reason · actual observed values · target device · time remaining), automatic-action notices with undo, and a session report.
- Developer mode and a laptop developer view (`hub/atlas-tof/pc/devview`) show raw sensor values, keystrokes, inference evidence, and control progress as they are.

---

## Privacy · security

- **No camera, no microphone.** Only distance grids, radar, key timing, and environment data are fused.
- **Key values are never received.** Only timestamp-based aggregates are used.
- Raw 8×8 ToF frames never leave the Pi 4 (not sent over MQTT, not logged, not shown on screen).
- Inference and control finish on the device. Personalization is consent-based and can be deleted.
- Credentials, personal logs, self-reported labels, and trained models are not in this repository.

Details: [`docs/security-privacy.md`](docs/security-privacy.md) (Korean).

---

## Running it

### Try it on a PC (no boards)

```bash
cd hub
python -m venv .venv && source .venv/bin/activate     # Linux (some tests use fcntl)
pip install -r requirements.txt
python -m pytest tests -q
python -m deskmate_hub demo --host 0.0.0.0 --port 8765    # FSM loop with synthetic sensors, http://127.0.0.1:8765/api/state
```

For rehearsing the 5-minute demo scenario and injecting synthetic sensor data, see [`tools/README.md`](tools/README.md) (`demo_dryrun.py` · `mqtt_scenario_sim.py` · `rehearsal_local.py`).

### On the real hardware (ESP32 · Pi 4 · Pi 5)

1. **ESP32** — flash `firmware/esp32_sensor_node` with PlatformIO. Wiring: [`docs/hardware.md`](docs/hardware.md) · [`docs/breadboard-esp32-node.md`](docs/breadboard-esp32-node.md).
2. **Pi 4** — install the hub IPK (`hub/atlas`) and the posture service (`hub/atlas-tof`). See [`hub/atlas/README.md`](hub/atlas/README.md).
3. **Pi 5** — build and install the app with Docker (Atlas SDK) on a development PC. See [`display/atlas/README.md`](display/atlas/README.md).
4. **Once on the Pi 4 after every boot**, start the broker and hub. The current ATLAS image does not support auto-start at boot.

```sh
/mnt/rawdata/deskmate_tof/ch340cfg auto 115200                    # claim the ESP32 USB serial port (every time it is plugged in)
sh /data/share/deskmate/recovery/install.sh pi4 --runtime-only   # start broker + hub under a supervisor (restarts on crash)
```

On the Pi 5: `sh /data/share/deskmate/recovery/install.sh pi5 --runtime-only`. Full procedure and troubleshooting: [`docs/reboot-recovery.md`](docs/reboot-recovery.md) (Korean).

> **Note:** If you pair the mood lamp or fan in the ATLAS Settings app, the system holds the link and the app's connection is refused. Connect them only from the DESKMATE app.

---

## Field and test results

| Item | Result |
|---|---|
| ESP32 → Pi 4 USB serial | 18.6 frames/s, 0 CRC errors |
| ToF reception | 5 Hz received on real hardware, 0 frame loss |
| Accept → mood lamp action | 0.4 s |
| App reception after broker restart | 3/3 received via resubscription and state watching |
| ToF posture (real-data replay, 2026-10-26) | Face-down stage detection 100 % · 0 false positives, false "misplaced" 76 → 0 %, real-world macro-F1 0.456 |
| Controlling three devices at once (on hardware, 2026-11-02) | Lamp, fan, and speaker controlled together from one app; all three connect even in the worst case where A2DP attaches first |
| Automated tests (2026-11-02, `main`) | hub pytest **803** passed · 9 skipped · 1 xfail / Flutter apps **332 · 467 · 77 · 9** passed (0 analyze issues) / laptop developer view 108 passed / ml 36 passed |

Simulation figures are not real-world measurements. Intervention effects (ventilation · lighting · breaks · posture guidance) are based on prior research; real-use measurements of DESKMATE come after the finals.
Sources and conditions are in [`docs/sim-results/`](docs/sim-results) · [`docs/ondevice-field-validation.md`](docs/ondevice-field-validation.md) (Korean).

---

## Next version

- **Right after the finals:** calibrate the model with real seated-posture sessions, measure intervention effects, run the Pi 4 continuously for 2 hours.
- **Higher-resolution main sensor:** L8 8×8 (64 zones) → L9 54×42 (2,268 zones, about 35×). The training tools already support the L9 grid.
- **Appliances as inputs:** add ThinQ air-purifier air quality and Wi-Fi CSI to see the whole room.
- **Cloud only when the user turns it on:** remote reports and shared-model updates. Inference and control will still finish on the device, as now.

---

## Directory layout

```
├── firmware/
│   ├── esp32_sensor_node/   ESP32 sensor node (2× ToF · mmWave · environment, COBS transmission)
│   └── pico2w_ble_fan/      Pico 2 W BLE fan firmware
├── collector/               PC keystroke-timing collector
├── hub/                     Raspberry Pi 4 hub
│   ├── atlas/                   ATLAS native service IPK (C++ decoder · MQTT client)
│   ├── atlas-tof/               ToF posture service (C++) · UART relay · laptop developer view (pc/devview)
│   ├── mqtt/                    Mosquitto configuration
│   └── deskmate_hub/
│       ├── ingest/              USB · MQTT → 10 s SensorFrame
│       ├── features/            Personal-baseline normalization
│       ├── inference/           18-state FSM · confidence gate · guard
│       ├── personalize/ learning/ personalization/   Bandit · reaction learning · on-device learning
│       ├── fatigue/             Shared fatigue model (KSS)
│       ├── control/             Control dispatcher · device adapters
│       └── config/              Weights · thresholds · timers (YAML)
├── display/atlas/           Pi 5 Flutter app · fatigue logging app · deployment scripts
├── ml/                      Shared-model training · conversion
├── dataset/                 MuJoCo ToF simulation · field recording tools
├── tools/                   Scenario simulation · rehearsal · sensor monitor · recovery installer · ThinQ
└── docs/                    Specs · hardware · test records
```

---

## Documentation

Detailed documents under `docs/` are written in Korean. Each folder's `README.md` is in English, with the Korean original in `README.ko.md`.

| Document | Contents |
|---|---|
| [`docs/README.md`](docs/README.md) | Documentation map |
| [`docs/requirements-spec.md`](docs/requirements-spec.md) · [`docs/data-spec.md`](docs/data-spec.md) | Requirements · data-layer contracts |
| [`docs/fsm-spec.md`](docs/fsm-spec.md) · [`docs/guard-spec.md`](docs/guard-spec.md) | 18-state transitions · confidence formula · combined conditions |
| [`docs/mqtt-topics.md`](docs/mqtt-topics.md) | Pi 4 ↔ Pi 5 MQTT topics · payloads |
| [`docs/hardware.md`](docs/hardware.md) | Wiring · sensors · communication |
| [`docs/personalization-spec.md`](docs/personalization-spec.md) · [`docs/ondevice-personalization.md`](docs/ondevice-personalization.md) | Personalization · on-device learning |
| [`docs/demo-scenario.md`](docs/demo-scenario.md) · [`docs/demo-day-runbook.md`](docs/demo-day-runbook.md) | Demo flow · power-on order on demo day |
| [`docs/security-privacy.md`](docs/security-privacy.md) | Security · privacy |

---

## Team TEAMMATE — University of Seoul, School of Electrical and Computer Engineering

| Role | Name | Development | Main directories | Other duties |
|---|---|---|---|---|
| Team lead | Taehwan Kim | Overall coordination · schedule, sensor interfacing, ToF preprocessing · presence/posture classification, calibration, HW structure · pin map, mmWave testing | `firmware` · `hub/features` · `dataset/tof_sim_v5` · `hub/atlas-tof` | Presentation |
| Member | Soyeon Park | Rule-based FSM inference engine, confidence formula · gate, work reports, keystroke pipeline, personalization | `hub/inference` · `ml` · `dataset/tof_sim` | Development photos · video |
| Member | Myunghee Cho | Lightweight classifiers · on-device optimization, ThinQ integration, ESM labeling scheme, FSM architecture, requirements · data specs | `ml` · `hub/control` · `docs` | Repository management |
| Member | Minhyuk Lee | ESP32 firmware · first-stage preprocessing, MQTT broker · topics, logging · time sync, ESP–Pi communication, CAD · housing, branding | `firmware` · `hub/ingest` | Notion organization |
| Member | Minkyung Choi | Display UI, ThinQ integration, feedback handling · report visualization, IPK · D-Bus research, auxiliary sensor pipeline | `display` · `collector` | Materials management |

## Development timeline (2026)

| Date | Milestone |
|---|---|
| 07-20 | Passed the preliminary round |
| 07-24 | Design documents · first FSM draft, first PR merged |
| 07-30 | First technical training; received a Pi 5 in place of the Pi Zero |
| 08-27 | Requirements spec finalized, first mmWave heart-rate test |
| 09-14 | 10 s decision cycle finalized, switched to a 7" display |
| 09-16 | First real ESP32 → Pi 4 reception, 0 CRC errors |
| 09-18 | Broker running on the Pi 4; works without a PC using the C++ MQTT client |
| 10-01 | Passed integrated rehearsal (full flow demonstrated with synthetic sensors) |
| 10-12 | L9 board fault → decided to switch to two L8 8×8 sensors |
| 10-16 | Two physical L8 sensors connected (addresses separated via LPn pins) |
| 10-19 | First real-world recording; model selected by real-world data |
| 10-26 | Board release completed; false "misplaced" calls 76 → 0 % |
| 10-28 | Finals documents submitted (report · project description · video) |
| 11-06 | Final presentation judging |

## License

[MIT](LICENSE). Sources for the apps' audio are listed in each app's `assets/audio/CREDITS.md`.
