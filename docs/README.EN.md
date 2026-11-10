**English** | [한국어](README.md)

# DESKMATE documentation

> Note: the detailed documents linked below are written in Korean. This index describes each one in English.

The documents are split into contracts, current plans, and operational references so their roles do not overlap. Past progress history and
draft plans are not kept separately; the current status is always reflected in the roadmap.

## Read these first

| Document | Purpose |
|---|---|
| [`agent-briefing.md`](agent-briefing.md) | Pre-work project summary, decided and undecided items, safety constraints |
| [`roadmap.md`](roadmap.md) | Current implementation status, gaps toward the finals, weekly priorities |
| [`development-rules.md`](development-rules.md) | Collaboration · Git · verification rules |
| [`plan/next-development-plan.md`](plan/next-development-plan.md) | Next tasks W1–W7 that can be done without hardware, and Codex prompts (10-01) |
| [`plan/app-connection-robustness-plan.md`](plan/app-connection-robustness-plan.md) | App connection robustness plan, stages 0–3 (done, PR #30–#32) |

## Implementation contracts

| Document | Scope |
|---|---|
| [`requirements-spec.md`](requirements-spec.md) | MVP functional and non-functional requirements and acceptance criteria |
| [`data-spec.md`](data-spec.md) | Sensor features; validity, normalization, and integration contract |
| [`fsm-spec.md`](fsm-spec.md) | FSM states, transitions, scores, and intervention rules |
| [`mqtt-topics.md`](mqtt-topics.md) | MQTT topics and payload schemas |
| [`personalization-spec.md`](personalization-spec.md) | Three-layer policy for choosing personalized interventions (rule FSM · lifestyle profile · contextual bandit), rewards, bedtime rules, cold start, demo plan |
| [`personalization-log.md`](personalization-log.md) | One-line JSONL schema for personalization logs (episodes · session summaries) with examples |

## Hardware · deployment · submission

| Document | Purpose |
|---|---|
| [`hardware.md`](hardware.md) | Device roles, wiring, sensors, BOM |
| [`breadboard-esp32-node.md`](breadboard-esp32-node.md) | ESP32 node breadboard pin layout (VL53L8CX ×2 · C1001 · environment sensors · Pi 4) — pin conflict resolution, address assignment, first checks |
| [`atlas-build-handoff.md`](atlas-build-handoff.md) | Pi 5 Atlas IPK build and on-device deployment checks |
| [`ilink-bluetooth-lamp.md`](ilink-bluetooth-lamp.md) | iLink BLE lamp protocol, state feedback, PC connection procedure |
| [`submission.md`](submission.md) | Finals deliverables, deadlines, demo rules |
| [`field-checklist.md`](field-checklist.md) | Hands-on hardware checklist — from communication through sensors, FSM, intervention screens, Bluetooth, and the demo, with record sheets |
| [`demo-scenario.md`](demo-scenario.md) | 5-minute demo flow, rehearsal commands, pre-stage checks |
| [`demo-day-runbook.md`](demo-day-runbook.md) | **One-page demo-day sheet** — power-on order, Bluetooth rules, things not to do, prep for judges' questions |
| [`security-privacy.md`](security-privacy.md) | Draft security and privacy section for the report (what is not collected · on-device processing · storage locations · remaining risks) |
| [`dataset-survey.md`](dataset-survey.md) | Survey of public datasets for stage-2 training |
| [`fatigue-pipeline-literature-review.md`](fatigue-pipeline-literature-review.md) | Literature and regulatory grounding for the whole fatigue/drowsiness pipeline (keystrokes · drowsy behavior · mmWave heart rate · indoor environment · elapsed time · interventions · labels), cross-checked against 45 current config values, and priority measurement items (with a Word `.docx` version) |

Changes that alter a contract update the relevant document together with the implementation and tests in the same commit.
