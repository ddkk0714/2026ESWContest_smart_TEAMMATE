# tof_sim — 합성 ToF 데이터로 자세·졸음 분류기 학습

책상 앞 사람을 **MuJoCo** 관절 모델로 만들고, 가상 VL53L8CX(또는 VL53L9CX 축소 격자)로 읽어
**정답이 붙은 프레임을 끝없이** 만든다. 그 프레임으로 작은 스트리밍 분류기를 계속 학습한다.
펌웨어 규칙 판정(`firmware/.../preprocess/tof/TofPosture`)도 같은 데이터로 채점해 둘을 비교한다.
MuJoCo 뷰어로 사람이 움직이는 모습과 센서 빔이 맞는 자리를 직접 볼 수 있다.

> **시뮬 결과는 실제 성능이 아니다.** 센서 잡음·반사율·사람 움직임 모델은 그럴듯한 가정이고 실측으로
> 맞추지 않았다. 실제 판단은 실측 데이터(`depth_teacher` 라벨 + 동시 녹화)로 한다(아래 "실측으로 확인").

## 구성

```
tofsim/mj_scene.py MuJoCo 장면(기본 백엔드) — 관절 있는 사람(골반 이동·몸통 굽힘/기울임·호흡·
                   목/고개 3축·어깨 볼·팔꿈치)·책상·키보드·의자·벽·잡동사니를 세션마다 MJCF 로 생성,
                   팔은 2관절 IK 로 키보드·턱·입·책상에 손을 맞춤, mj_multiRay 로 광선(법선 포함)
tofsim/raycast.py  numpy 백엔드: 광선-도형 교차 (타원체·캡슐·상자)
tofsim/body.py     numpy 백엔드 사람·방 모델, 그리고 두 백엔드가 공유하는 Pose/Person/Room
tofsim/sensor.py   다중 zone direct ToF 모델 — zone 당 k×k 부광선, 60 cm 안쪽 표면은 한 봉우리로
                   합쳐짐(혼합 픽셀), 신호 ∝ 반사율×cos(입사각)/거리², 가장 가까운 유효 봉우리 보고
                   (Closest), 신호 대 문턱 비에 따른 거리 잡음·상태값(5/9/6/10/4/255)·결측
tofsim/session.py  세션 무작위화(체격·옷/머리 반사율·의자·책상·벽·센서 높이/각도·주변광)와
                   자세 타임라인(바른자세·상체 앞으로·젖힘·엎드림·턱괴기·꾸벅 졸음·자리비움)
                   + 헷갈리는 동작(키보드 힐끗·컵 마시기·고개 돌리기·타이핑·호흡·흔들림)
tofsim/data.py     모델 입력 변환, 워커 프로세스 스트리밍, 고정 검증셋
tofsim/model.py    프레임마다 작은 CNN → GRU → 7클래스 (약 7만 파라미터, MCU 탑재 크기)
train.py           무한 학습 루프 (체크포인트·재개·평가·최고 모델·로그)
eval_rules.py      펌웨어 규칙 판정을 같은 검증셋으로 채점
preview.py         자세별 프레임 눈으로 확인, 생성 속도 측정
view_session.py    MuJoCo 뷰어로 세션 재생 + 8×8 빔이 맞는 자리 표시 (라벨은 터미널)
render_check.py    자세별 MuJoCo 화면(옆·모니터 뒤)과 시뮬 8×8 측정을 PNG 로 저장
run_forever.ps1    정해진 시간 동안 train.py 를 계속 돌림 (죽으면 이어서 재시작)
```

백엔드는 `--backend mujoco`(기본)와 `--backend numpy` 가 있다. 같은 센서 모델·타임라인·라벨을 쓰고
장면·광선 계산만 다르다. 이 노트북 기준 워커 1개가 MuJoCo 약 240 f/s, numpy 약 100 f/s.

라벨은 펌웨어 enum 과 같다: `0 upright · 1 lean_forward · 2 recline · 3 face_down · 4 chin_rest · 5 drowsy · 6 away`.
졸음 구간에서 첫 끄덕임 전 프레임은 `upright` 로 둔다 — 센서로 볼 수 없는 라벨은 학습 잡음이다.

## 설치

```
python -m venv .venv
.venv\Scripts\python -m pip install "numpy>=1.26,<2.0"
.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python -m pip install mujoco
```

규칙 판정 채점(`eval_rules.py`)은 `g++` 가 있어야 한다(처음 실행할 때 `tools/rule_replay.exe` 를 빌드).

## 사용법

```
.venv\Scripts\python view_session.py --seed 3        # MuJoCo 뷰어로 세션 보기
.venv\Scripts\python render_check.py --seed 3        # 자세별 PNG (runs/render)
.venv\Scripts\python preview.py                      # 자세별 프레임 출력
.venv\Scripts\python preview.py --bench 1500         # 워커 1개 생성 속도
.venv\Scripts\python train.py --run l8_v1            # 학습 시작 (Ctrl+C = 저장 후 종료)
.venv\Scripts\python train.py --run l8_v1 --resume   # 이어서
.\run_forever.ps1 -Run l8_2weeks -Hours 336          # 2주 연속 (죽으면 자동 재시작)
.venv\Scripts\python eval_rules.py runs\valset_mujoco_vl53l8cx_24x300.npz   # 규칙 판정 점수
```

2주 돌릴 때: Windows 절전(잠자기)을 꺼 둔다. 기본 설정은 CPU 코어 − 2 개를 생성 워커로 쓴다(`--workers`).
노트북은 배터리로 돌면 CPU 가 크게 느려진다(i5-1335U 실측 약 1/5). 충전기를 꽂고 돌린다.

### 리눅스 서버 (UBAI 등)

GPU 는 거의 필요 없다. 병목은 CPU 데이터 생성이라 **CPU 코어를 많이** 받는다.

```
bash setup.sh                                   # 가상환경·패키지 설치 + 생성 속도 측정 (한 번)
tmux new -s tof                                 # 직접 실행하는 경우
bash run_forever.sh l8_2weeks 336               #   정해진 시간 동안 계속, 죽으면 이어서
sbatch slurm_train.sh                           # Slurm 인 경우 (파티션·CPU·시간은 파일 위쪽에서 수정)
sbatch --export=ALL,RUN=mj_trial,TOTAL_H=336 slurm_train.sh   # 이어서 할 모델 지정
```

`slurm_train.sh` 는 작업 하나가 시간 제한으로 끝나면 전체 마감(`TOTAL_H`) 전까지 자기 자신을 다시
제출한다. 연속 5번 실패하면 스스로 멈춘다.

### 중간에 멈추기·뽑아내기

| 하고 싶은 것 | 방법 |
|---|---|
| 지금 상태를 바로 저장 (학습은 계속) | `touch runs/<RUN>/SNAPSHOT` — 몇 초 안에 `ckpt_last.pt` 저장 |
| 저장하고 멈춤 (다시 제출·재시작도 안 함) | `touch runs/<RUN>/STOP` — 이번 단계를 마치고 저장 후 종료. Ctrl+C·`scancel`(SIGTERM)도 저장하고 끝남 |
| 모델 뽑아내기 (학습 중에도 됨) | `python export.py --run <RUN>` (최고 모델) / `--which last` (최신) |
| 다시 이어서 | `STOP` 파일을 지우고 같은 이름으로 다시 실행/제출 (`train.py` 를 직접 시작하면 남은 `STOP` 을 알아서 지움) |

`export.py` 결과는 `runs/<RUN>/export_<시각>_<best|last>/` 와 같은 이름의 `.zip` 이다.
`posture_net.pt`(가중치+정보), `weights.npz`(numpy), `posture_net.onnx`(한 프레임 스트리밍 추론, PyTorch 와
오차 1e-6 확인), `report.md`(클래스별 점수·혼동행렬·학습 중 점수 변화), `model_card.json`(입력·출력 형식).

### 출력 (`runs/<run>/`, 커밋 안 함)

- `train.log` — 1분마다 처리량·손실, 30분마다 검증 점수
- `metrics.jsonl` — 같은 내용을 기계가 읽는 형식으로 (혼동행렬 포함)
- `status.json` — 지금 상태 한 장
- `ckpt_last.pt` (10분마다), `ckpt_best.pt` (검증 macro-F1 최고)

디스크는 거의 늘지 않는다. 생성한 프레임은 학습에 쓰고 버리며, 고정 검증셋(약 4 MB)·체크포인트(약 1 MB)·로그만 남는다.

## 실측으로 확인

시뮬에서 잘 나와도 실제에서는 다를 수 있다. 실측 데이터를 같은 형식의 `.npz` 로 만들어 `--real` 로 주면
학습 중 평가 때마다 함께 채점한다.

```
dist   int16 [T,H,W]   거리 mm, 타깃 없음 -1
status uint8 [T,H,W]   VL53 target status
label  int8  [T]       위 라벨 번호 (depth_teacher posture/drowsy 를 매핑)
flags  uint8 [T]       선택. bit0 = 전환 구간(채점 제외)
starts int   [S]       선택. 세션 시작 프레임 번호
```

`depth_teacher` 의 `frames.csv`(t_host) 와 VL53L8 시리얼 로그를 시간으로 맞추면 이 파일을 만들 수 있다(도구는 아직 없음).

## 결과

| 날짜 | 판정 | 검증셋 | 정확도 | macro-F1 | 메모 |
|---|---|---|---|---|---|
| 2026-10-10 | 펌웨어 규칙 (TofPosture) | numpy sim 24×300 s | 0.368 | 0.303 | 힐끗 보기→졸음, 상체 옆면→턱괴기, 기준선 드리프트→젖힘, 켤 때 의자→자리비움 0 |
| 2026-10-10 | 학습 모델 10분 | numpy sim 24×300 s | 0.492 | 0.403 | replay 3, 졸음 재현율 0 (학습 초기) |
| 2026-10-12 | 펌웨어 규칙 (TofPosture) | MuJoCo sim 24×300 s | 0.357 | 0.258 | 관절 몸이라 엎드려도 등·어깨가 위에 남아 엎드림 재현율 0.04 |
