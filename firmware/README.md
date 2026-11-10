**English** | [한국어](README.ko.md)

# firmware — ESP32 sensor edge node

> [`pico2w_ble_fan/`](pico2w_ble_fan/README.md) in the same folder is not a sensor node; it is the firmware for the controlled `vent_fan` Bluetooth fan (Pico 2 W + L9110).

Owners: Minhyuk Lee (firmware · MQTT) · Taehwan Kim (sensor interfacing · ToF preprocessing)

One unit is placed inside the dock. It reads the mmWave and environment sensors, does first-stage preprocessing on the node (moving average, outlier removal, drowsiness FSM), and then
sends the data to the Pi 4 as **UART2 binary frames (COBS + CRC-16/CCITT-FALSE)**. On 2026-09-11 we measured C1001 → ESP32 → Pi 4 `/dev/serial0` delivery on real hardware.
For the VL53L9CX, the connection path (Path A: Pi 4 CSI-2 / Path B: ESP32 I2C 1 MHz binning) is pending a decision on 09-19; it attaches to this node only with Path B.

> **Current status (2026-09-15):** `esp32_sensor_node/` now contains a PlatformIO project, `C1001Passive` based on the
> official DFRobot C1001 library, a 5-state `DrowsyDetector`, and UART0 (USB) JSON output. The environment sensors are MVP stubs that, every 5 seconds,
> output all values as `null` with the valid flags set to `false`.
>
> **Added 2026-09-16:** UART2 now sends binary frames following the provisional protocol in `docs/data-spec.md` §13.1 —
> `transport/frame.cpp` (header, CRC-16/CCITT-FALSE, COBS, `0x00` terminator), `include/frame_types.h` (TYPE 0x20 mmWave 1 Hz ·
> 0x10 environment 0.2 Hz (valid_bits 0 when stubbed) · 0xF0 heartbeat 1 Hz). Build flags `-DDESKMATE_UART2_TX=1` (on by default),
> `-DDESKMATE_FIRMWARE_VERSION`. Cross-checked against the Python codec (`hub/deskmate_hub/ingest/uart_frame.py`) with test vectors.
> **An actual `pio run` build and UART verification on a real board have not been done yet.** Real drivers for the environment sensors (SCD41, BH1750, DHT22) are not implemented.
>
> **Updated 2026-09-29:** Both items above have since been resolved (09-16 real-board flash and real UART reception, PR #21 environment drivers).
> The C1001 is read with a **receive-only non-blocking parser** (`sensors/c1001/C1001Passive.cpp`) instead of vendor polling. It does not drop
> a single active report, and if reports stop for more than 2 s it wakes the sensor with a query (`nudgeIfSilent`). Before initialization, a 300 ms `probe()`
> checks that the sensor is alive; if the sensor is absent, it reattaches with a backoff from 10 s up to 120 s. The drowsiness thresholds are in `DrowsyDetector.h`
> with comments giving the measurement evidence, and the only knob for shortening the confirmation time for demos is `-DDESKMATE_DROWSY_HOLD_SCALE_PCT=<percent>`.
> Host tests: `pio test -e native` (drowsiness 12 · environment validation 9). If gcc is not available, as on this PC, use Docker:
> `docker run --rm -v "<firmware path>:/fw:ro" python:3.11 bash -c "apt-get -qq update && apt-get -qq install -y g++ && pip -q install platformio && cp -r /fw /tmp/fw && cd /tmp/fw && pio test -e native"`.

## Wiring and UART assignment (measured)

| Channel | Purpose | Pins |
|---|---|---|
| UART0 (`Serial`) | Debug monitor · `log2file` logging · segment marker input | USB |
| UART1 (`Serial1`) | C1001 mmWave, 115200 8N1 | GPIO16 RX / GPIO17 TX |
| UART2 | Pi 4 link, 115200 8N1 (target 460,800+) | GPIO25 TX → Pi 4 GPIO15 (physical 10) / GPIO26 RX ← Pi 4 GPIO14 (physical 8) |
| I2C0 (`Wire`) | SCD41 `0x62` · BH1750 `0x23`, 100 kHz | GPIO21 SDA / GPIO22 SCL |
| I2C1 (`Wire1`) | **VL53L8CX ×2**, 1 MHz. A `0x54` (changed by firmware) · B `0x52` | GPIO23 SDA / GPIO18 SCL · LPn A GPIO32 / B GPIO33 · NCS·SPI_I2C_N = GND |
| GPIO27 | DHT22 DATA | |

Full layout and pin conflict notes (LPn 16 and SPI_I2C_N 17 in 76EHwan/ESP32_VL53L8 collide with the C1001): [`docs/breadboard-esp32-node.md`](../docs/breadboard-esp32-node.md).

GPIO16/17 are used for PSRAM on WROVER-family modules. This doesn't matter with the current `esp32dev` (WROOM), but move the pins if you change boards.
A common GND is mandatory. Do not connect 5V directly to the Pi 4. If upload fails, hold BOOT and press EN once (`Wrong boot mode` = failed to enter upload mode).

## Principles

- **Raw ToF data goes only as far as the Pi 4, in per-sensor 8×8 units (standard as of 2026-10-17).** Since the posture AI model was decided to run on the Pi 4 (whether as an MLP inside the hub or as a separate
  service will be decided by measured performance — `docs/data-spec.md` §6.1 candidates A/B), this node passes the VL53L8CX raw 8×8 distance and status to the Pi 4 over UART `0x05`, **one
  frame per sensor** (same frame_id and TS, 5 Hz). This is because the two sensors are aimed 8° outward, so their fields of view mostly overlap and stitching them into 16×8 would not form a single
  scene; the Pi 4 runs inference per sensor and fuses the results. The 16×8 stitching in `TofArray` is an internal approximation used only for the rule-based estimator (`0x03`) and USB display.
  Raw data is handled only on the wired UART and in Pi 4 memory; it never goes out to MQTT, logs, or the screen. A downscaled depth map (`0x01`) is a separate reservation, allowed at up to 2 Hz
  only in an explicit debug/UI mode.
- Every frame carries `SEQ` and `TS` (loss detection, alignment of the Pi 4 sliding window).
- CRC-16 is applied only to UART binary frames. MQTT JSON from the Pi 4 onward carries no separate application-level CRC.
- Even if instantaneous mmWave respiration/heart-rate values are sent, they are not the basis for Pi 4 decisions. The contract covers the body-motion moving average (τ=15 s), heart-rate median (120 s), arousal baseline (τ=600 s), and the drowsiness FSM state/evidence.

## Frame protocol (before contract finalization — `docs/data-spec.md` takes precedence)

```
[SOF 1B][VER 1B][TYPE 1B][SEQ 2B][LEN 2B][TS 4B][PAYLOAD N][CRC16 2B]   → COBS encoded + trailing 0x00
```

| TYPE | Contents | Rate | Status |
|---|---|---|---|
| 0x01 | (debug) binning depth map | ≤ 2 Hz | Reserved, not implemented. The temporary use for mmWave in the 09-11 experimental firmware was resolved by moving it to 0x20. The production raw grid is 0x05, so no conflict |
| 0x03 / 0x04 | ToF `tof_feature` (26 B) / posture summary (4 B) — `src/sensors/tof/TofPayload.h` | 5 Hz / 1 Hz | **Implemented 10-16.** `docs/data-spec.md` §13.1. posture is the node's rule-based decision (fallback for the Pi 4 MLP) |
| 0x05 | ToF raw 8×8 sensor frame `tof_grid` — 8 B header (grid_w, grid_h, sensor, sensors, frame_id, sensor_mask, flags, reserved) + dist i16×64 + status u8×64 = 200 B. One per sensor, same frame_id | 5 Hz × number of sensors | **Implemented 10-17.** Unit is per-sensor 8×8 (overlapping fields of view, LEN ≤ 256 B). Rationale and golden hex in `docs/data-spec.md` §13.1 |
| 0x10 | Environment bundle (SCD41 CO₂ 2B + DHT22 T 2B + RH 2B + BH1750 lux 2B = 8B). SCD41 T/RH is not sent (no calibration, single sensor; decided 09-14) | 0.2 Hz | Not implemented |
| **Unassigned** | mmWave drowsiness state + evidence + summary values (10–16 B) | 1 Hz | **TYPE assignment needed** |
| 0xF0 | Status/heartbeat | 1 Hz | |

## Structure (target)

```
esp32_sensor_node/
├── platformio.ini
├── include/          config headers (node ID · pins · thresholds)
└── src/
    ├── main.cpp
    ├── sensors/      c1001 (parser · DrowsyDetector) / scd41 / bh1750 / dht22 / vl53l9cx (Path B)
    ├── preprocess/   moving average · outlier removal · feature extraction
    └── transport/    uart2 framing (COBS · CRC-16) · heartbeat
```

## Development environment

PlatformIO (Arduino framework) or ESP-IDF.
Board and library dependencies are pinned in `platformio.ini`.

The ESP32 is wired to the Pi 4, so Wi-Fi credentials are not needed on the default path. If they become necessary, copy `include/secrets.h.example` to `include/secrets.h` and do not commit it.

## ToF posture estimation (Path B preprocessing)

`src/preprocess/tof/TofPosture.{h,cpp}` — multizone distance frame → the 7 geometric features from roadmap §4-4 →
`docs/data-spec.md` §6.1 `tof_feature` (posture enum · `motion_score` · `head_delta_mm` · `nod_rate_hz`).
It is pure C++ with no Arduino dependency. **From 10-17, posture model decisions belong to the Pi 4, and this rule-based decision is for comparison and fallback** — the raw grid goes to the
Pi 4 via `0x05`, and the model on the Pi 4 (candidate A, hub `posture/tof_mlp.py` / candidate B, `deskmate_tofposture`; undecided) produces the posture.
Nodding and drowsiness are temporal phenomena, so they continue to be decided here and sent in the `0x03` flags.

- **Postures:** upright · leaning forward · reclining · face-down (slumped on the desk) · chin-resting · nodding off · away from seat.
  The definitions port two decision methods the team had already validated into ToF physical quantities — `display/atlas/camsvc/src/posture_pose`
  (posture categories, decision order, hysteresis, 1.5 s hold) and `dataset/depth_teacher` (drowsiness from head dynamics alone).
  We could not find any public dataset that covers seated posture with 8×8 or 54×42 ToF.
- **Grid-agnostic.** VL53L9CX 54×42 raw, binning-downscaled grids (24×20 · 8×6 …), and VL53L8CX 8×8 are all handled by the same code.
  RAM is set by `DESKMATE_TOF_MAX_ZONES` (default 2268 → about 35 KB). Objects are kept global/static.
- **Flow.** zone status check (5·9, optionally 6·10) → 3-frame median · hold through short dropouts → foreground separation against
  a background learned from the empty seat → 7 features + head/torso distance, head drop, arm columns → the first quiet 10 s after sitting down is taken as the "upright" baseline
  → classification relative to the baseline → 1.5 s hold.

| Posture | ToF decision (relative to baseline, on/off) | Source |
|---|---|---|
| Chin-resting | Columns that are continuous from just below the head and at least 60 mm closer than the chest occupy at least 60%/40% of the upper-body rows, and the head does not drop as far as face-down | camsvc "chin-resting is a fact about the arm" |
| Reclining | Head distance at least 1.11×/1.05× | camsvc shoulder width 0.90/0.95× ↔ distance ratio |
| Face-down | Top of head drops at least 135/75 mm (row change × vertical FoV × distance) | camsvc head drop 0.35/0.20 shoulder width × 380 mm |
| Leaning forward | Head at least 80 mm closer | depth_teacher lean_forward 5 cm |
| Nodding off | Nods (drop of at least 40 mm within 0.2–1.5 s and 60% recovery within 1.5 s, torso within 80 mm) twice within 60 s, or still for 5 s with the head dropped as in a nod. 5 s hold | depth_teacher drowsiness |

- **Thresholds** are all in `TofPostureConfig`. Even the values carried over from the sources include camera→ToF conversion assumptions (shoulder width 380 mm, rotation radius 15 cm),
  so they must be calibrated against measurements recorded simultaneously with depth_teacher labels.
- **Host tests:** `pio test -e native -f test_tof_posture` (28 — background separation · baseline · 6 postures · decision order ·
  typing arms are not chin-resting · 1.5 s hold · robustness to dropouts/spikes · a single nod is not drowsiness · two nods / still with head dropped is drowsiness ·
  upper-body bending / forward head (turtle neck) is not a nod · identical decisions across 4 grid types, etc.).

**2026-10-16: sensor connection, UART, and hub mapping are in (branch `feat/tof-node-hw`, not yet verified on hardware).**

- `src/sensors/tof/TofArray.{h,cpp}` — reads two VL53L8CX (ST STM32duino 2.1.0) on `Wire1`, keeps them as per-sensor 8×8 (`sensorDistance(i)`, for 0x05),
  and stitches them into a single grid only for the rule-based estimator, feeding it to `TofPostureEstimator` (the two fields of view mostly overlap, so stitching is an approximation — 10-17 standard). At boot, address assignment holds B's LPn low while changing A to 0x54; a dead sensor is retried every 5 s;
  if only one survives, it falls back to 8×8. Placement and orientation via `-DDESKMATE_TOF_STACK_ROWS/FLIP_ROWS/FLIP_COLS/SWAP/COUNT`.
- `src/sensors/tof/TofPayload.{h,cpp}` — encoders for TYPE 0x03 (26 B, 5 Hz) and 0x04 (4 B, 1 Hz). The 4 host tests in `test_tof_payload`
  pin the same golden hex as hub `test_uart_frame`. A 1 B `tof_ok` was added to the 0xF0 heartbeat (firmware 0x0120).
- `main.cpp` — ToF polling, UART transmission, USB `{"t":"tof_feature"}` at 1 Hz, boot status line (`VL53L8CX x2: ok_mask=3 grid=16x8`),
  USB commands `b` (baseline), `g` (background), `r` (reset). `SET_LOOP_TASK_STACK_SIZE(16 KB)` — the ST driver uses 4 KB of stack.
- hub: `ingest/uart_frame.py` decoder, `SensorCache.tof`, the posture signal via the `tof:` section of `ingest.yaml` (mmWave substitutes only when ToF is stale),
  `sensor_summary.tof`, and `tof.posture` in the Pi 4 C++ native summary as well.

**2026-10-17: raw grid UART transmission (TYPE 0x05) — the posture AI model runs on the Pi 4.**

- `TofPayload.{h,cpp}` `encodeTofSensorFrame` — packs `TofArray::sensorDistance(i)/sensorStatus(i)` (per-sensor 8×8 in scene orientation) into a
  200 B frame per sensor. `writeTofGridUart` in `main.cpp` sends only live sensors at 5 Hz (`kTofGridPeriodMs`) after a new sample arrives,
  with the same `TS` and `frame_id` on every frame, SEQ from a separate counter (`tof_grid_sequence`), and the applied FLIP/STACK/SWAP in `flags`.
  Firmware version 0x0121. 6 host tests in `test_tof_payload` (2 golden frame hex strings shared with hub `test_uart_frame`).
- Pi 4 side, candidate A (implemented in this commit): `TofGridAssembler` in `hub/deskmate_hub/ingest/uart_frame.py` assembles the frames and stores them in `SensorCache.tof_grid`,
  and `hub/deskmate_hub/posture/tof_mlp.py` produces the posture with a pure-Python MLP without numpy (192→64→10, weights from `dataset/tof_sim/train_mlp.py`
  converted with `export_mlp_json.py`). The 0x03 rule-based decision is kept as the fallback for when the MLP cannot produce a result.
- Pi 4 side, candidate B (not yet in this repository): a separate service `deskmate_tofposture` (HTTP 8771, `dataset/tof_sim_v5` PostureNet CNN+GRU,
  (2,8,8) input per sensor, GRU state retained, fusion of the two sensors) makes the decision, and `hub/atlas` only relays 0x05 to tcp:9100.
  **Which of the two to use is undecided and will be determined by accuracy in measurement sessions.** Both assume per-sensor 8×8 inference + fusion (in candidate A, `tof_mlp.py`
  does per-sensor baselines and inference, then sums the log-softmax outputs).

**Not done yet: hardware testing.** Address assignment for the two sensors, sustaining 15 Hz ×2, grid orientation, and thresholds can only be known once attached to the board.
For what to check on first power-up, see "처음 켤 때 보는 것" ("What to check on first power-up") in `docs/breadboard-esp32-node.md`. What to check on hardware for 0x05:
whether Pi 4 `UART	{"type":5,...}` lines arrive at 5 Hz × 2 (number of sensors) (`uart_rx` CRC drop count 0), whether hub `UartStats.grids` increases by 5 per
second (if frames are lost, `TofGridAssembler.discarded` increases), whether mmWave and environment frames get delayed at 115200 (if so, raise the Pi 4 link to
460800 or increase `kTofGridPeriodMs`), whether only one frame is sent, with that sensor's number, when only one sensor is alive (`sensors` 1), and whether the
per-sensor 8×8 orientation (FLIP/SWAP in `flags`) matches the orientation the model was trained on (check by raising a hand; see "방향 확인" ("Orientation check") in `dataset/tof_logger/README.md`).

**2026-10-17 measurement — rebuilt and reinstalled the Pi 4 hub IPK from `feat/tof-node-hw` (382e821) and confirmed 0x05 assembly on the board.**
ESP32 firmware 0x0121, Pi 4 `/dev/serial0 → ttyAMA0` 115200 (getty in failed state, device node `g0_a5015` 660), service account u0_a5015,
built in WSL docker `atlas-dev:latest` with `pip3 install --break-system-packages pyyaml paho-mqtt` →
`source /opt/atlas-sdk-x86_64/environment-setup-armv8a-atlas-linux` → `arc build hub/atlas` (if the SDK environment is not sourced: `spawn cmake ENOENT`).
4 consecutive 30 s reports (no person present, both sensors alive, `sensors_ok 3`):

- C++ `uart_rx`: `rx` 516 → 982 /30 s (≈15.5 frames/s total), **`discarded=0 crc_errors=0`**.
- hub bridge (`[bridge] uart …`; from this commit, 30 s period, includes the 0x05 counters and MLP decisions): **`parts` +238–239 /30 s ≈ 7.9 /s = ≈4 Hz per sensor**
  (not 5 Hz because of a firmware timer phase issue — fixed in measurement (2) below to 10.2 /s), **`grids` +119–120 /30 s ≈ 4.0 /s** (samples where both sensors' frames were collected),
  **`grid_discarded=0`**, `dropped=0`, heartbeat ≈1 /s, `unknown={}`.
- At 115200, 0x03/0x04, mmWave 0x20, and environment 0x10 all arrive together, and `/api/state` `sensor_summary` shows temp 23.9, humidity 47.9, lux 418, and mmwave
  as-is — no delay. There is no reason to raise the link speed yet.
- MLP (`[tof_mlp] 192→64→10 (8x8, mlp_sim60)`): called for every sample (`mlp frames` == `grids`). With the seat empty, no baseline is captured, so it is `unknown/0.0 used=0`,
  and `sensor_summary.tof.posture_source` is `rule` (rule-based decision recline, `head_delta_mm` 605, `coverage_ratio` 0.07–0.13 = nobody present) — the fallback
  working as designed. However, the MLP's `present=1` (zones within 150–1300 mm are 6% or more — apparently the desk surface, etc.) needs to be rechecked together with baseline capture and decisions when a person is seated.
- Not checked yet: when only one sensor is alive (`sensors` 1), grid orientation (hand raise), MLP baseline and decision accuracy with a person seated.
- For installation, instead of `arc install`, the build output `hub/atlas/build/arm64/deskmate_hub_service` (ELF including the zip payload) was copied over the service directory via scp,
  followed by `chown u0_a5015:g0_a5015` and then `pi4-hub-activate.sh` (to avoid account reassignment). The board clock is about a day behind real time (journal 10-16 16:5x KST).

**2026-10-17 measurement (2) — 0x05/0x03 transmission rate made exactly 5 Hz; boot after ESP32 soft reset 13.3 s → 4.2 s.** Firmware 0x0122.

- **Transmission rate.** The ToF timer in `main.cpp` was changed from "overwrite the deadline with now" to `periodDue()` (advance the deadline by one period, and resync to
  now if more than one period behind). The overwrite approach got caught on the phase of the 15 Hz samples (66 ms), so 0x05 came every 264 ms (≈4 Hz), and 0x03 lagged by the loop delay to 4.2 Hz.
  Measured by capturing 8 s of raw data from Pi 4 `/dev/ttyAMA0` (with the hub not running, `stty -F /dev/ttyAMA0 115200 raw -echo; cat > /tmp/esp.bin`) and counting with
  `split_stream/decode_frame` in `hub/deskmate_hub/ingest/uart_frame.py`: **0x05 80 frames = 10.17 /s (40 each for sensors 0 and 1), SEQ gaps 0**,
  sample intervals mostly 198–199 ms (max 220); **0x03 40 = 5.09 /s**, intervals 198–200 ms (0x03 and 0x04 share SEQ, so they are contiguous when combined);
  0x04, 0x20, 0xF0 1.02 /s, 0x10 0.25 /s, decode/CRC errors 0. Cumulative `tx_grid`/`tx_feature` counters were added to the USB `tof_feature` line — the 1 s differences
  are 9.8–10.0 / 4.9–5.0 (30 s observation).
- **Boot time.** The cause was `TofArray::parkStuckSensor()`. When only the ESP32 is reset, both sensors stay powered and keep ranging at their previous addresses (A 0x54, B 0x52),
  **ignoring LPn LOW** (the scan shows `[all LPn low] 0x52,0x54`). In this state 0x52 responds to address ACK, `is_alive`, and register reads, but
  calling `init()` with LPn LOW gave code 3 (NACK 2 | poll timeout 1) after a 2 s poll → `stop_ranging` 5 s timeout → 2 s poll = **spending 9.3 s and failing**
  (the 0x56 "move out of the way" never succeeded once); afterwards, when `startSensor` raised LPn and ran init, it succeeded on the first try even while ranging (1.32 s each for A and B).
  Fixes: (1) If both A's target address and 0x52 respond (a soft reset after the previous boot completed), the addresses don't overlap, so park is skipped.
  (2) If only 0x52 responds (the previous boot was cut off before A's address change), only the SW reboot sequence at the very beginning of `vl53l8cx_init()` is sent (`softReboot`, ≈100 ms)
  — the rebooted sensor is uninitialized, so it follows LPn LOW again and drops off the bus (measured `acks after=0`); from there it follows the same path as a power cycle.
  (3) Removed the 0x56 candidate and `kTofParkedAddress`, and added a per-sensor diagnostic line `tof A: init=0 (1319 ms)`.
  Measured (USB boot log, host time): in both soft resets, `VL53L8CX x2: ok_mask=3` at **4.16–4.17 s** (scan 0.4 s + A 1.32 s + B 1.32 s;
  previously 13.33 s). The "only 0x52 responds" path was reproduced by resetting right after SW-rebooting A with a temporary USB command — `[all LPn low] 0x52` → `SW reboot,
  acks after=0` → `ok_mask=3` 4.17 s.
  **Calling `stop_ranging()` first on an uninitialized sensor was not used**: the library polls bit 7 of register 0x06 10 ms × 500 times, so
  on a sensor that hasn't booted it runs to the 5 s timeout (`vl53l8cx_api.c` `vl53l8cx_stop_ranging`). Register 0x06, which we looked at to detect ranging, is also 0x31 while ranging,
  so bit 7 cannot distinguish the cases.
- **The power-cycle path (both uninitialized, LPn normal) was also confirmed by measurement.** The code is unchanged (if 0x52 does not respond, park returns immediately; the `startSensor`
  logic "init at 0x52 → change only A to 0x54" is unchanged). Captured from the moment the port reopened while unplugging and replugging USB-C (with all other sensors connected):
  `tof A: init=0 (1320 ms)` → `tof B: init=0 (1319 ms)` → `VL53L8CX x2: ok_mask=3 grid=16x8`, and the `ms` of the following `tof_feature` = 3611.
  That is, **both sensors OK at 3.6 s** in ESP32 time — the same as `ms`=3630 for the soft reset (the 4.2 s host-side figure includes ROM boot and 0.5 s of USB delay).
  Because of the port enumeration delay (≈1 s), the scan and park lines could not be seen over USB. In the same boot, the SCD41 came up with `periodic measurement started`
  (the `error=268` after a soft reset appears to be the SCD41 refusing re-initialization while still powered — the same kind of phenomenon as the ToF, a separate issue).
- Pi 4 status note: at the time of this capture there was no hub process (`ps` 0, no `[bridge]` lines in the journal) — which is why the port could be read directly.
  To bring the hub back up, run `pi4-hub-activate.sh`.

**2026-10-17 PC diagnostics: raw 8×8 line `{"t":"tof_grid"}` toggled by USB command `z` + `tools/sensor_monitor.py`.** Firmware 0x0123.
The same samples and the same `frame_id` as UART 0x05 are also output over USB as JSON, with 64 `d` (mm, -1) and 64 `st` (255) values per sensor (5 Hz, ≈1.1 kB per line).
**It is off by default** and `z` toggles it (`# tof_grid usb on/off`), so the production output and the "no raw ToF in production" principle are unchanged — like the collection mode (`env:tof_logger`),
it is viewed only on a development PC. `Serial.setTxBufferSize(4096)` enlarges the USB TX ring buffer so printing 1.1 kB does not block the loop (95 ms at 115200).
`tools/sensor_monitor.py --port COM8` sends `z` at startup and sends it again on exit to turn it off. Measured: monitor received `tof_grid` at 4.99 Hz, and with it on,
the `tx_grid`/`tx_feature` differences stayed at 10/5.

**2026-10-17 fix for SCD41 `error=268` after soft reset + USB `i` environment I2C scan.** 268 = Sensirion `WriteError|I2cAddressNack`: when only the ESP32
is reset, the SCD41 stays powered and the periodic measurement from the previous run keeps going, and in that state it does not even ACK its address for `start_periodic_measurement`
(after a power cycle it works immediately — the same kind of issue as the ToF ignoring LPn). If `beginScd41` fails, it calls `stop_periodic_measurement` (500 ms) and then tries
starting once more. USB `i` probes Wire (GPIO21/22) 0x08–0x77 and prints the ACKing addresses — measured `0x23,0x62` (BH1750, SCD41); right after flashing (soft reset),
CO₂ 622 ppm was valid. The `i 환경 I2C 스캔` ("i environment I2C scan") button on the monitor page sends the same command.

**Training data collection mode (`env:tof_logger`, not production).** Reads the VL53L8CX 8×8 with the official ST library and sends raw zones and
rule-based decisions to the PC as one-line JSON over USB (`{"t":"tof",...}`, 921600). `src/tof_logger/` is excluded from the production build
(`build_src_filter` of `env:esp32dev`). For the PC-side collector, wiring, and orientation check, see [`dataset/tof_logger/README.md`](../dataset/tof_logger/README.md).
Line format test: `pio test -e native -f test_tof_line`.

## Communication contract

See [`docs/data-spec.md`](../docs/data-spec.md) for the logical schema and [`docs/mqtt-topics.md`](../docs/mqtt-topics.md) for the MQTT mapping from the Pi 4 onward.
Measurement records: Notion pages 「ESP32–Pi4 UART 하드웨어 연결·실측 검증」 ("ESP32–Pi4 UART hardware connection and measurement verification") and 「센서별 데이터·처리·전송 스펙」 ("Per-sensor data, processing, and transmission spec").

### MVP USB (UART0) JSON lines

At 115200 8N1, one JSON object is written per line, terminated with `\n`. JSON sensor lines always start with `{"t":`, so
they can coexist with ordinary debug logs on the same port.

```json
{"t":"mmwave","ms":1234,"present":true,"motion_state":"still","motion_level":12,"distance_cm":87,"resp_bpm":15,"resp_valid":true,"heart_bpm":null,"heart_valid":false,"drowsy_state":"AWAKE","valid":true}
{"t":"env","ms":5000,"co2_ppm":null,"temp_c":null,"humidity_pct":null,"lux":null,"co2_valid":false,"temp_valid":false,"humidity_valid":false,"lux_valid":false}
```

- mmWave is 1 Hz; environment is 0.2 Hz (every 5 seconds).
- `motion_state` is `none|still|active`; `drowsy_state` is `NOPERSON|NOLOCK|WARMUP|AWAKE|DROWSY`.
- Invalid or missing values are output as `null` with the corresponding valid flag set to `false`.
- Warmup, stillness duration, and motion level thresholds are adjusted in `build_flags` in `platformio.ini`.
- Build: `cd firmware/esp32_sensor_node; pio run`
- If upload fails, hold BOOT, press EN once, then upload.
