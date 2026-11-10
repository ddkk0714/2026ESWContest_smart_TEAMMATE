**English** | [한국어](README.md)

# dataset — building the L9 ToF training dataset

Goal: build a dataset for training a model that **judges seated posture and drowsiness from the VL53L9CX ToF (L9) alone**.

```
Depth camera (D435if) ─► depth_teacher ─► per-frame ground-truth labels (t_host)
                                                │  time synchronization
L9 ToF ────────────► (recording tool, planned) ─┴─► training dataset ─► ml/
```

- The depth camera's decisions are taken as correct, and those labels are attached to the L9 frames from the same moment.
- **Signals that the L9 can measure directly, such as eye blinks, are not used for the depth camera's decisions**
  (the teacher labels must be independent of the student's input).
- The depth camera is used only on the development PC to produce labels. It is not part of the product (board).

| Folder | Status | Contents |
|---|---|---|
| `depth_teacher/` | 🟡 | D435if depth camera posture/drowsiness decisions (teacher). Validation status is in its README |
| L9 recording & sync | ⬜ | Record L9 frames with `t_host` (epoch seconds) and align them with the teacher labels. **L9 is on hold due to a sensor mounting fault** |
| `tof_logger/` | 🟡 | Records **VL53L8CX 8×8** raw data + rule-based decisions + instructed postures as sessions (ESP32 `env:tof_logger`). Not yet verified on hardware |
| `tof_sim/` | 🟡 | MuJoCo virtual L8 — generates 8×8 sessions per posture, re-evaluates them with the firmware rules, MLP baseline. For testing before hardware (scores are not real accuracy) |
| `tof_sim_v5/` | 🟡 | Endless generation of synthetic ToF frames from a MuJoCo articulated body / SMPL-X + training and export (ONNX) of a streaming classifier (CNN→GRU, 9 labels: 7 postures + reposition + forward head (turtle neck)) + tools for turning real recordings into npz. Trained with randomized sensor placement; the Pi 4 `hub/atlas-tof` runs this model. Not yet calibrated on real measurements |

**Not committed**: recordings (`*.db3`, `*.mp4`), session CSVs, reference posture files (`data/`), models (`models/`).
These are videos showing faces and personal posture records, so the team rule (no committing of collected data) applies.
