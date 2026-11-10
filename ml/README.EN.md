**English** | [한국어](README.md)

# ml — Stage-2 lightweight classifier training pipeline

Owner: Myunghee Cho

Trains a lightweight classifier (2D CNN or MLP) **on the PC** using self-reported ESM labels,
converts it to TFLite, and deploys it to the Raspberry Pi 4. The cloud is not used for inference.

## Positioning

The stage-2 classifier is a **proof of concept (demonstration)**. The backbone of the system's completeness is the stage-1 rule FSM;
the classifier complements and validates it. The system must work even if accuracy falls short of the target.

Why we frame it this way: it is hard to collect enough ESM labels within 2 months,
and for cognitive states like focus and fatigue, the ground truth itself is very noisy.

## Constraints

| Item | Target |
|---|---|
| Model size | Tens to hundreds of KB (reference: ST HPD 5th generation, fewer than 10,000 weights per neural network · 29KB Flash) |
| Inference latency | Must fit within the 500ms decision-cycle budget |
| Deployment format | TFLite, on-device on the Pi 4 |

## Layout

```
ml/
├── datasets/       collected data (gitignored — do not commit)
├── training/       preprocessing · training · evaluation scripts
├── models/         converted .tflite (gitignored, only deployed builds go to releases)
├── export_tflite.py
└── requirements.txt
```

## Data

| Source | License · handling |
|---|---|
| Self-reported ESM labels | Collected by team members, **private — do not commit to the repository** |
| ST VL53L8CX Hand Posture Dataset | Attribution required |
| nQSI Dataset (nQ Medical) | Reference for keystroke fatigue research |

`datasets/` and `models/` are blocked by `.gitignore`.
Data lives on the team Google Drive; only download scripts and checksums are committed here.

## Training windows and augmentation pipeline

`training/build_dataset.py` reads a synthetic dry-run or matching pairs of `frames-*.jsonl` and `state-*.jsonl`
and builds 6 tick × 17 feature windows. `--esm` can override with correction labels. After splitting
train/eval by session, it applies noise · time warping · sensor dropout · baseline shift · sensitivity scaling **to training windows only**.
Real logs and ESM files are not put in the repository.

```sh
python -m pip install -r ml/requirements.txt
python ml/training/build_dataset.py --name synth-v1 --synthetic default,short,demo --seeds 1-20 --offsets 0,3,7 --augment 4
python ml/training/evidence.py ml/datasets/synth-v1 --out docs/ml-augmentation-evidence.md
python -m pytest ml -q
```

The outputs are `ml/datasets/<name>/windows.npz` and `manifest.json`. The NPZ's `meta` is
a JSON string array ordered as training windows followed by evaluation windows, holding the session ID · start tick · label source · augmentation type · split.
The accuracy in the evidence document is a sanity check against synthetic weak labels and should not be read as real-world performance.
Columns that are not sensor measurements, such as breathing and elapsed time (`augment.EXEMPT`), get no noise · offset · scaling.
The evidence document also includes a class-weighted softmax (checking the rare rest class) and **robustness to missing data and individual differences** (applying dropout · baseline shift · sensitivity differences to evaluation windows
and comparing training on original vs. augmented data). Public dataset candidates are in [`docs/dataset-survey.md`](../docs/dataset-survey.md).

## ESM labeling scheme

User feedback from the display terminal (`accept` / `reject` / `correct`) becomes the label as-is.
Rather than building a separate labeling app, using the feedback UI as the label collection channel
is better for gathering enough labels. The label schema will be recorded in this document once finalized.


## Next development for stage-2 personalization

The development order follows the [stage-2 personalization plan](../docs/plan/personalization-stage2-plan.md),
and the input/output and metadata contract follows the [model contract](../docs/personalization-model-contract.md).
The shared 1D CNN and per-user head retraining · comparison · fallback have been implemented and verified by running them. TFLite conversion and real inference on the PC have also been verified; the hub observation hookup is disabled by default.
Training commands and results are in [shared model training](../docs/common-model-training.md).

Per-user head commands and results are in [per-user head retraining](../docs/personal-head-training.md).

Conversion commands and results are in [TFLite validation](../docs/tflite-export-validation.md).

Observation comparison commands and results are in [same-frame comparison](../docs/model-replay-comparison.md). Fusion is not applied.
