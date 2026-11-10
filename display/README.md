**English** | [한국어](README.ko.md)

# display — Raspberry Pi 5 display terminal

## Development environment

The Atlas Flutter implementation and cross-builds are done in the Docker Compose environment described in [atlas/README.md](atlas/README.md). Vendor assets used by Docker are prepared in the gitignored `atlas/vendor/` inside the repo; no external paths are used in the Docker configuration.

The actual iteration loop is `edit code and Docker cross-build on the dev PC → install and run on the Pi 5 over SSH →
check the run console/DevTools logs → edit code`. The Pi 5 runs the `.ipk` app natively on AI Native OS
and does not run Docker.

Owner: Minkyung Choi

The output terminal of the desk-top display appliance. It subscribes to `deskmate/state/phase` and
shows the current phase · suggestion cards · rest notices · work reports,
and publishes user feedback to `deskmate/feedback/user`.

## Screens (state-adaptive — the 4 screens from the poster)

| Screen | Contents | Status |
|---|---|---|
| Standby (AOD) | Shows only the time · environment summary (CO₂ · illuminance · temperature/humidity) · presence | 🟡 The dashboard has an environment summary; a dedicated low-information standby screen is not yet separated |
| Low-stimulus focus | A screen that reduces unnecessary information while focused. Tapping reveals details (focus time · environment · manual control) | 🟡 The dashboard always shows details. Low-stimulus/detail toggle not implemented |
| Suggestion card / auto-action notice | In the `conf_suggest` (0.45) – `conf_auto` (0.75) range, accept / reject; at or above `conf_auto`, after-the-fact notice + undo. Shows the reason for the action | 🟡 Suggestion card and feedback sending exist. Auto-action notice, undo, and reason text not implemented |
| Session report | At the end, a summary of focus time · state changes · intervention outcomes · main patterns | ⬜ Not implemented (`hub` `report.py` is scheduled to land in main via the `feat/merge-pending` PR) |

Other development helper screens include an 18-state transition graph, a sensor test (Pi 4 `/api/test-frame`), a keystroke panel, and an audio playback toggle.

The **PC Keystroke** tab under the keyboard icon at the top shows the PC collector's hold time and inter-key interval (mean and
standard deviation), rhythm coefficient of variation, space and correction ratios, mouse activity, and the trend of the last 60 samples.
Over an MQTT connection it subscribes directly to `deskmate/sensor/keystroke` at 1 Hz; the HTTP development path
uses the hub's summary values. Keyboard input on the board does not overwrite this tab's PC values.
It distinguishes demo, collector waiting, no input, and reception lost for more than 5 seconds.

Connect the PC, Pi 4, and Pi 5 to the same network, set the Pi 4 MQTT address and port in the app's connection settings,
then run the following from the repository root on the PC. The default port is 1883.

```bash
python -m pip install -r collector/requirements.txt
python -m collector --broker <Pi4-IP> --port 1883
```

Bluetooth pairing alone does not send PC input features. In the current development plan and the collector
implementation, the communication path is MQTT. Key contents are not transmitted, and the app's graphs reset when you leave the tab.

## Feedback design

The user's accept · reject · correct responses become the ESM labels for the stage-2 classifier as-is,
so the feedback UI is also a **label collection channel**. Lowering the input burden determines how many labels we get.

- A suggestion card must be answerable within 2 taps
- A correction (`correct`) is a simple choice among the 4 phases
- No response is also recorded as a signal (neither accept nor reject)

## Technology choice

We settled on **Atlas Flutter** on the Pi 5 AI Native OS Video Profile. The app source is in
[`atlas/ui_env_app/`](atlas/ui_env_app/); the Atlas platform runner reviewed from the LG-provided sample was copied and then
separated out with a DESKMATE-specific app ID and minimal permissions.

- No hub URL set: the built-in demo cycles through 5 representative states.
- `DESKMATE_HUB_URL` set: polls the Pi 4 preview API every 1 second and returns accept/reject (development fallback).
- `DESKMATE_MQTT_HOST` set: **the final path.** Subscribes to `deskmate/state/phase` (retain) · `interaction/request` · `display/message` at QoS 1 and
  publishes `feedback/user`. If both HTTP and MQTT are given, MQTT takes precedence.

## Current deployment status (2026-09-14)

- Atlas Flutter app source and platform runner: implemented
- Pi 4 synthetic FSM state and feedback connection (HTTP): implemented
- MQTT subscribe/publish (`MqttStateSource`): implemented. However, **the hub does not publish over MQTT yet**, so so far it has been verified with Node-RED injection
- Dashboard redesign · 18-state graph · keystroke panel · audio: implemented
- Atlas SDK import procedure on the dev PC: implemented, assets excluded from Git
- Release `.ipk` build and Pi 5 install/run: done
- **Touch on real hardware: working** — touch confirmed after replacing the display on 2026-09-14 (the earlier `xhci-hcd.1` failure is history)
- Auto-start on reboot: not implemented
- Session report screen, low-stimulus/detail toggle, auto-action notice and undo: not implemented

> The Pi 5 practically requires a 27W PD adapter and an active cooler. Throttling under always-on display output +
> heat directly affects long-running stability.
