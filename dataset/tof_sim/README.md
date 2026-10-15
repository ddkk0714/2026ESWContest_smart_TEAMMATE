# tof-sim — 합성 ToF 데이터로 자세·졸음 분류기 학습

책상 앞 사람을 **MuJoCo** 관절 모델로 만들고, 가상 VL53L8CX(또는 VL53L9CX 축소 격자)로 읽어
**정답이 붙은 프레임을 끝없이** 만든다. 그 프레임으로 작은 스트리밍 분류기를 계속 학습한다.
펌웨어 규칙 판정(`rules/TofPosture`)도 같은 데이터로 채점해 둘을 비교한다.
MuJoCo 뷰어로 사람이 움직이는 모습과 센서 빔이 맞는 자리를 직접 볼 수 있다.

DESKMATE 팀 레포(`2026ESWContest_smart_TEAMMATE`)의 `dataset/tof_sim` 을 커밋 기록째 떼어낸 독립 레포다.
학습 서버에서는 이 레포만 받으면 된다.

```
git clone <이 레포 주소> tof-sim && cd tof-sim
bash setup.sh                                   # 리눅스: 가상환경·패키지 설치 (한 번)
sbatch --export=ALL,RUN=ubai_v1,TOTAL_H=336 slurm_train.sh   # Slurm (UBAI cpu2 기준으로 맞춰 둠)
bash run_forever.sh ubai_v1 336                 # Slurm 이 아닌 서버·PC
```

`rules/TofPosture.{h,cpp}` 는 팀 레포 `firmware/esp32_sensor_node/src/preprocess/tof/` 의 사본이다
(팀 레포 커밋 250f801 기준). 팀 레포의 최신 펌웨어로 채점하려면 `TOFPOSTURE_SRC=<그 폴더>` 를 주고
`tools/rule_replay(.exe)` 를 지운 뒤 `eval_rules.py` 를 실행한다.

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
tofsim/session.py  세션 무작위화(체격·옷/머리 반사율·의자·책상·벽·센서 배치 arc/random·주변광)와
                   자세 타임라인(바른자세·상체 앞으로·젖힘·엎드림·턱괴기·꾸벅 졸음·자리비움)
                   + 헷갈리는 동작(키보드 힐끗·컵 마시기·고개 돌리기·타이핑·호흡·흔들림)
tofsim/data.py     모델 입력 변환, 워커 프로세스 스트리밍, 고정 검증셋
tofsim/model.py    프레임마다 작은 CNN → GRU → 8클래스 (약 7만 파라미터, MCU 탑재 크기)
train.py           무한 학습 루프 (체크포인트·재개·평가·최고 모델·로그)
eval_rules.py      펌웨어 규칙 판정을 같은 검증셋으로 채점
preview.py         자세별 프레임 눈으로 확인, 생성 속도 측정
view_session.py    MuJoCo 뷰어로 세션 재생 + 8×8 빔이 맞는 자리 표시 (라벨은 터미널)
render_check.py    자세별 MuJoCo 화면(옆·모니터 뒤)과 시뮬 8×8 측정을 PNG 로 저장
run_forever.ps1    정해진 시간 동안 train.py 를 계속 돌림 (죽으면 이어서 재시작)
```

백엔드는 `--backend mujoco`(기본)와 `--backend numpy` 가 있다. 같은 센서 모델·타임라인·라벨을 쓰고
장면·광선 계산만 다르다. 이 노트북 기준 워커 1개가 MuJoCo 약 240 f/s, numpy 약 100 f/s.

라벨은 펌웨어 enum 에 둘을 더한 것이다: `0 upright · 1 lean_forward · 2 recline · 3 face_down · 4 chin_rest · 5 drowsy · 6 away · 7 misplaced · 8 forward_head`.
`misplaced` 는 자세가 아니라 "장치를 다시 놓으라"는 뜻이다(거꾸로·너무 가까이·너무 멀리·옆을 봄·많이 기울임). `--placement random` 에서만 나온다(아래 "센서 배치").
`forward_head` 는 거북목이다: 상체는 거의 바로 세운 채 머리만 화면 쪽으로 5~8.5 cm 나간 자세(`Pose.head_forward`). 평소 자세에도 0~3 cm 의 자연스러운
머리 위치 변화가 섞여 있고, 5 cm 이상 지속되는 세그먼트(`turtle`)만 이 라벨이다. 펌웨어 enum 에도 한 칸을 더해야 한다.
졸음 구간에서 첫 끄덕임 전 프레임은 `upright` 로 둔다 — 센서로 볼 수 없는 라벨은 학습 잡음이다.

## 설치

```
python -m venv .venv
.venv\Scripts\python -m pip install "numpy>=1.26,<2.0"
.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python -m pip install mujoco
.venv\Scripts\python -m pip install onnx onnxruntime        # export.py 의 ONNX 내보내기와 확인용
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

### 센서 배치 (`--placement`)

| 모드 | 뜻 |
|---|---|
| `arc` (기본) | 시연 배치. 턱에서 0.60~1.10 m 떨어진 호 위 7자리 중 하나, 사용자를 정확히 향함(조준 오차 sd 2°). 책상·키보드가 보이면 다시 뽑는다 |
| `random` | 실제 사용자가 놓듯이. 거리 0.35~1.45 m, 방위 ±60°, 조준 오차 sd 6°, 기울기 오차 sd 3°, 25% 는 3~15 cm 받침 위, 15% 는 일부러 잘못 놓음(거꾸로·너무 가까이·너무 멀리·옆을 봄·많이 기울임). 책상·키보드가 보여도 그대로 쓴다 |
| `mixed` | 세션마다 `arc` / `random` 반반 |

`random` 에서 바른자세 머리 중심이 센서에서 0.45~1.30 m 를 벗어나거나, 시야 밖으로 나가거나(가로는 가장자리에서 4° 안쪽까지),
장치가 뒤집혀 있으면 그 세션은 `misplaced` 다. 자리비움 구간까지 세션의 모든 프레임이 그 라벨이다(한 세션 안에서 빈 화면이
`away` 와 `misplaced` 두 라벨을 갖지 않도록). 세션의 약 1/4 이 그렇게 나온다(센서 하나 기준; 장치의 두 센서 중 한쪽만 옆을
보는 경우도 그 센서는 `misplaced` 다). 기준은 `tofsim/session.py` 의 `OK_*`, 뽑는 분포는 `RANDOM_*` 상수.

검증셋은 학습 배치 모드 것(이걸로 `ckpt_best` 를 고름)과 `arc` 것을 둘 다 만들어 매 평가 때 같이 찍는다.
파일 이름에 모드가 붙는다(`valset_mujoco_vl53l8cx_random_24x300.npz`); `arc` 는 예전 이름 그대로라 캐시가 유지된다.

```
.venv\Scripts\python train.py --run l8_random --placement random --init ..\tof-sim_ubai_v3\ckpt_best.pt   # 표준 모델에서 이어서 (7클래스 head 는 8클래스로 넓혀 줌)
.venv\Scripts\python train.py --run l8_random_scratch --placement random                          # 처음부터
.venv\Scripts\python train.py --run l8_h128 --placement random --hidden 128 --val-sessions 48       # 큰 모델(약 17만 파라미터), 넓은 검증셋
.venv\Scripts\python export_arc.py . viewer\arc_random.json 7 240 5 - random                       # 뷰어용 (무작위 배치 세션 하나)
```

`arc` 로만 학습한 모델은 무작위 배치에서 쓸 수 없다(2026-10-14, ubai_v3 best 를 v5 시뮬 16세션에서 센서 자세만 바꿔 측정:
기준 정확도 0.75 → 조준 10° 틀어지면 0.49, 옆으로 25 cm 치우치면 0.31, 0.45 m 에서 0.11, 거꾸로 0.11).

### 배경·자리비움·자세 변형

- `--clutter N`: 뒷벽 앞에 상자·선반 같은 잡동사니를 세션마다 0~N개 둔다(기본은 `session.CLUTTER_MAX`, 지금 0). 검증셋 파일명에 `clutterN_` 이 붙는다.
- `--scene rich`(기본): 책상 위 물건(모니터·보조 모니터·노트북·컵/병·책 더미·스탠드·가방)과 방 안 가구(옆 책장·파티션·옆 책상에 앉은
  사람·뒷벽 책장·창문(유리라 무응답)·화분)를 세션마다 각각의 확률로 둔다. 장치 발자국·키보드·손 구역은 비우고, 센서 시야에 드는 물건은
  맨 아래 줄 6 cm 까지만 허용한다. 지나가는 사람도 더 자주, 더 다양하게(옆에 서서 얘기, 어깨 너머로 보기, 가까이 지나가기). `--scene plain` 은
  예전 그대로의 빈 책상·빈 벽. 검증셋 파일명에 `rich_` 가 붙는다.
- 자리비움은 사람이 그냥 사라지지 않는다. 의자를 뒤로 밀며 일어서고(0.6~1.2 s), 옆으로 걸어 나가고(bystander 와 같은 걷는 몸체), 돌아올 때는 옆에서 걸어 들어와 의자에 앉는다. 그 프레임은 전환(FLAG_TRANSITION)이라 채점에서 빠지고 학습 가중치가 낮다. 짧은 자리비움은 빨리 걷는다.
- 자세 변형(`session.SEGMENTS`, 라벨은 `SEGMENT_LABEL` 로 기존 7개에 붙음): 팔걸이에 기대기(`armrest`→upright),
  옆 모니터·옆사람 보기(`look_aside`, 고개 30~60° 돌린 채 5~30 s→upright), 팔꿈치 괴고 숙이기(`elbows`→lean_forward),
  기지개(`stretch`, 3~7 s→recline), 연필로 공부하기(`writing`, 고개 20~40° 숙이고 상체 12~28° 앞으로, 손은 책상 위에서 움직임→lean_forward).
  책 들고 읽기(`reading`→upright), 마우스 작업(`mouse_work`→upright), 전화 통화(`phone_call`, 오른손을 귀에→upright),
  거북목(`turtle`→forward_head). 비중은 `SEGMENTS` 주석의 라벨 시간 비율이 되도록 맞춰 두었다(바른자세 계열 약 45 %).
  기본 자세들의 각도 범위도 조금 넓혔다. 폰 내려다보기는 졸음과 구분이 애매해 넣지 않았다.
- 자세 안의 짧은 동작(look-alike, 라벨은 그 자세 그대로): 키보드 힐끗 보기, 컵으로 마시기, 고개 돌리기, 과자 먹기(손이 입과 책상을
  2~4 s 주기로 오감, 5~30 s), 바닥 가방 뒤지기(오른쪽으로 숙임 2~5 s, 이 구간은 전환으로 표시해 채점에서 뺌).

### 중간에 멈추기·뽑아내기

| 하고 싶은 것 | 방법 |
|---|---|
| 지금 상태를 바로 저장 (학습은 계속) | `touch runs/<RUN>/SNAPSHOT` — 몇 초 안에 `ckpt_last.pt` 저장 |
| 저장하고 멈춤 (다시 제출·재시작도 안 함) | `touch runs/<RUN>/STOP` — 이번 단계를 마치고 저장 후 종료. Ctrl+C·`scancel`(SIGTERM)도 저장하고 끝남 |
| 모델 뽑아내기 (학습 중에도 됨) | `python export.py --run <RUN>` (최고 모델) / `--which last` (최신) |
| 다시 이어서 | `STOP` 파일을 지우고 같은 이름으로 다시 실행/제출 (`train.py` 를 직접 시작하면 남은 `STOP` 을 알아서 지움) |

`export.py` 결과는 `runs/<RUN>/export_<시각>_<best|last>/` 와 같은 이름의 `.zip` 이다.
`posture_net.pt`(가중치+정보), `weights.npz`(numpy), `posture_net.onnx`(한 프레임 스트리밍 추론, PyTorch 와
오차 1e-6 확인), `report.md`(클래스별 점수·혼동행렬·학습 중 점수 변화), `model_card.json`(입력·출력 형식),
그리고 보드 이식용 `posture_net.bin`(평탄 float32 가중치, 24바이트 헤더) + `golden.txt`(PyTorch 가 만든 240프레임 입력·logits).

### 보드(Raspberry Pi 4) 이식

Pi 4 허브는 AI Native OS Headless 라 numpy·torch·컴파일러가 없고 C++ 서비스(IPK)만 올라간다. 팀 레포
**`hub/atlas-tof/`** 가 그 서비스다: `tools/posture_net_ref.cpp` 의 **의존성 없는 C++ 순전파**(`tof::PostureNet`:
`load` → 세션 시작 `reset` → 프레임마다 `step`)를 그대로 쓰고, ESP32(`env:tof_logger`) USB 줄을 센서마다 읽어
10 Hz 로 모델을 돌린 뒤 log-softmax 합으로 합쳐 HTTP 8771 로 낸다. 빌드·설치·검증 순서는 그 폴더의 README.

```
g++ -O2 -std=c++17 tools/posture_net_ref.cpp -o tools/posture_net_ref
tools/posture_net_ref <export>/posture_net.bin <export>/golden.txt      # PASS + us/frame (PyTorch 대비 2.5e-5)
```

보드 없이 실제 센서로 모델을 돌려 보려면 PC 에서 `tof_live.py --ports COM5,COM6 --model <export>/posture_net.onnx`
(pyserial + onnxruntime, 처리 경로는 서비스와 같음, `--record` 로 실측 세션 저장).
이 PC 에는 g++ 가 없어 UBAI 로그인 노드(gcc 8.5)에서 확인했다.

### 센서 두 개 (한 장치의 좌·우)

모델은 센서 하나의 8×8 을 본다(세션마다 `DEVICE_SENSORS` 둘 중 하나를 무작위로 골라 학습). 보드에서는 센서마다
모델 하나씩 돌리고 합친다. `eval_pair.py` 가 같은 세션을 두 센서 뷰로 렌더해 규칙별 점수를 내고, `--dump` 결과로
`fit_fusion.py` 가 선형 융합을 세션 교차검증한다. `tools/sim_to_toflines.py` 는 한 세션을 두 센서 뷰의 펌웨어 줄
형식(+정답)으로 저장해 서비스 리플레이 테스트에 쓴다.

```
python eval_pair.py --ckpt runs/<RUN>/ckpt_best.pt --placement random --sessions 24 --out runs/pair.json --dump runs/pair.npz
python fit_fusion.py runs/pair.npz
python tools/sim_to_toflines.py --seed 7001 --placement random --out ../2026ESWContest_smart_TEAMMATE/hub/atlas-tof/test
```

2026-10-14 측정(ubai_random 4 h 체크포인트, random 배치, 정확도): 단일 센서 0.62~0.67, softmax 평균 0.668~0.705,
log-softmax 합 0.682~0.718, "둘 중 하나라도 맞으면" 상한 0.854. 규칙 차이는 세션 편차 안쪽이고 상한과의 간격은
두 센서를 한 모델이 같이 보는 구조(학습 변경)의 몫이다. ubai_v3(SMPL-X 이전 arc 모델)는 지금 시뮬에서 0.52 로
떨어진다 — arc 시연 모델은 ubai_v5 계열을 써야 한다.

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
