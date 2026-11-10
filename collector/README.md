**English** | [한국어](README.ko.md)

# collector — PC keystroke timing collector

Owner: Soyeon Park *(role change: previously Minkyung Choi → Soyeon Park. The tables in the top-level `CLAUDE.md`/`README.md` will be updated after team confirmation)*

Collects the **fine-grained signal** that is only available while the user is working at the keyboard. It runs on a laptop (Windows),
computes a feature vector, and publishes it over MQTT; the Raspberry Pi 4 (hub) subscribes to it.

## Privacy principles (non-negotiable)

- **Key values are never collected.** Only input timestamps are extracted.
- Only statistical features are stored or transmitted. Even the raw timestamp sequence stays local.
- For the mouse, coordinates, click buttons, and scroll contents are not looked at either; **only the amount of activity** is counted.
- These principles are the basis of this project's "non-invasive private sensing" differentiator. They are checked especially strictly in code review.

## Extracted features

| Feature | Description |
|---|---|
| dwell time | How long a key is held down (mean, standard deviation) |
| flight time | Interval between keys (mean, standard deviation) |
| idle ratio | Fraction of time with no input |
| correction rate | Backspace frequency |

Captures the tendency for rhythm to slow down and become irregular, and for corrections to increase, when the user is fatigued.
`auto-repeat` (holding a key down) is treated as a single keystroke to avoid distorting the statistics.

### Additive signals (2026-09-14 **contract fixed to match the implementation**; fine-tuning later)
`typing_active` / `mouse_active` / `input_active` / `flight_cv` / `mouse_event_rate`.
`input_active` distinguishes **"mouse work (input present) vs. reading (no input at all)"** on the PC side, which
the hub uses to tell non-typing work apart from idleness.

## Publishing (contract — fixed to match the implementation on 2026-09-14)

Statistics over a **60-second sliding window at 1 Hz** to `deskmate/sensor/keystroke` (QoS 0).
Liveness is `deskmate/health/<node>` (LWT online/offline, retain). The schemas in [`docs/data-spec.md`](../docs/data-spec.md) §6.4 and
[`docs/mqtt-topics.md`](../docs/mqtt-topics.md) follow this implementation.

- Key intervals > 2 s are excluded from the rhythm statistics (flight); intervals ≥ 3 s are counted as idle (`flight_gap_max_s`, `idle_gap_s`)
- **No-input periods**: if the window contains no keydown, `typing_active=false`, `idle_ratio=1.0`, and the statistics are 0. The hub treats `typing_active=false` as the keystroke signal being **unavailable** and renormalizes the weights. `input_active` is true even if there is only mouse activity.
- Feature values are sent inside the `data` of the common envelope (`schema_version`, `ts`, `node`, `boot_id`, `seq`). Hub ingest accepts both flat and envelope forms.

```json
{
  "schema_version": "1.0", "ts": 1769000001.0,
  "node": "pc-collector", "boot_id": "7f2a91c4", "seq": 1042,
  "data": {
    "node": "pc-collector", "window_s": 60,
    "dwell_mean_ms": 92.4, "dwell_std_ms": 21.8,
    "flight_mean_ms": 148.2, "flight_std_ms": 63.5,
    "idle_ratio": 0.18, "correction_rate": 0.07,
    "typing_active": true, "mouse_active": true, "input_active": true,
    "flight_cv": 0.42, "mouse_event_rate": 96.0
  }
}
```

## Setting up the development environment

From the repository root, create a virtual environment with Python 3.10 or later and install the dependencies.

```bash
python -m venv .venv
python -m pip install -r collector/requirements.txt
python -m pytest collector/tests -q
```

The command to activate the virtual environment depends on the OS.

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- Linux/macOS: `source .venv/bin/activate`

## Running

Run as follows from the repository root.

```bash
# Connect to Pi4 (hub)
python -m collector --broker <pi4-ip>

# Laptop only (verify with local logging, no broker). Run from the repository root
python -m collector

# Tests (verify the logic without capturing real keys)
pytest collector/tests -v
```

### On-device (hotspot) integration — when checking exam-prep student / university student personalization

Attach the laptop to the same phone hotspot as the Pi 4, then from the repository root:

```bash
python -m collector --target hotspot    # = --broker 172.20.10.3 (Pi 4 wlan0)
python -m collector --target lan        # = --broker 172.16.34.199 (lab LAN · direct link to Pi 5)
```

- When started, `[mqtt] connected → 172.20.10.3:1883` appears and a `[PUB]` line is printed every second. If the hotspot drops, it prints `disconnected (자동 재연결)` ("auto-reconnect"), then
  reconnects with a 1→30 s backoff. In the meantime, values are kept only in `logs/keystroke.jsonl`.
- Check reception on the Pi 4: `ssh pi4-via-pi5 '/data/share/deskmate/bin/mosquitto_sub -h 127.0.0.1 -t deskmate/sensor/keystroke -v'`
- Check that the hub picks it up: `sensor_summary.keystroke` in `deskmate/state/phase` is this value, and while typing, `context` moves from `npc` → `mixed`/`pc`
  (the input ratio over a window that is 120 s for `pc_ratio_window_sec` in the demo profile, 900 s by default).
- Keep it running during university-student sessions while typing. If you also keep it running during exam-student (book/handwriting) sessions, you can confirm at the same time that, with no input, it is detected as `context: npc`.
- It is also visible in the keyboard tab of the Pi 5 developer app (PR #37, #43). That tab shows "수신 끊김" ("reception lost") if the difference between the sample `ts` (laptop clock) and the Pi 5 clock exceeds 5 s, so
  the two clocks must agree (measured 10-26: the Pi 5 was 88 s behind the laptop — if the Pi 5 is behind it passes; if the Pi 5 is ahead it appears as lost).
- Only timing statistics and keydown counts are sent. Which key was pressed is never recorded anywhere.

### Automatic Pi 4 discovery and auto-start at login (11-02)

```bash
python -m collector --broker auto       # finds the DESKMATE hub broker on the same Wi-Fi by itself
```

- Search order: the address found last time (`%LOCALAPPDATA%\DESKMATE\collector_broker.json`) → the `--target` alias addresses → the entire same /24 for each of the laptop's IPv4 addresses.
  Among devices with port 1883 open, it picks only a broker on which the hub's retained topics (`deskmate/health/hub`, `deskmate/state/phase`) actually arrive
  (hotspot routers and the like are filtered out). Measured on a hotspot on 11-02: found the Pi 4 `172.20.10.6` in 4.5 s without hints.
- Initially it retries every 15 s until it finds one; once connected, it searches again if disconnected for more than 30 s (when the hotspot or location changed). In between, values are
  kept only in `logs/keystroke.jsonl`.
- To start automatically at login without a window (Windows Task Scheduler, current user):

```powershell
powershell -ExecutionPolicy Bypass -File collector\autostart.ps1 -Python <venv>\Scripts\pythonw.exe   # register + start now
powershell -ExecutionPolicy Bypass -File collector\autostart.ps1 -Uninstall                           # unregister + stop
```

  The task name is "DESKMATE keystroke collector". If `-Python` is omitted, `<repository>\.venv\Scripts\pythonw.exe` is used.

Options: `--target {hotspot,lan}`, `--broker <ip|auto>`, `--port`, `--node`, `--window`, `--period`. They can also be set via environment variables (`DESKMATE_BROKER`, etc.).
If the broker is omitted, `localhost:1883` is used, and even when not connected, the same MQTT envelope is written to
`logs/keystroke.jsonl`. `logs/` is collected data and is not included in Git.

## Caveats

- OS-specific key hook permissions are required. Linux needs a desktop session and input permissions, macOS needs Accessibility
  permission, and Windows may require running as administrator depending on the privilege level of the target app.
- Global key/mouse hooking may be flagged as a keylogger by antivirus/SmartScreen, so add an exception on the demo PC.

## Structure

```
collector/
├── __main__.py     python -m collector entry point (argparse)
├── config.py       defaults ← environment variables ← CLI arguments
├── capture.py      pynput keyboard+mouse → events (time and type only)
├── features.py     sliding window → features → contract payload (to_payload)
├── publisher.py    common envelope wrapping + paho-mqtt publishing + local fallback when disconnected + health (LWT)
└── tests/          unit tests on synthetic events
```
