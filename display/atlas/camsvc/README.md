# camsvc — ESP32-CAM 스켈레톤 자세 판정 서비스 (Pi 5 / ATLAS native-service)

```
ESP32-CAM ─UART 921600─> /dev/ttyACM1 ─> camsvc ─HTTP 127.0.0.1:8770─> DESKMATE 앱
                         (Vision Stream)   │
                                           ├ 프레임 디코딩 (A5 5A … CRC-16)
                                           ├ BlazePose 검출기 224x224
                                           ├ BlazePose 랜드마크 256x256
                                           └ 스켈레톤 자세 판정
```

**왜 앱이 아니라 서비스인가.** 판정이 MediaPipe 모델 두 개를 돌려야 하는데 Dart 에는
TFLite 를 쓸 길이 없다. ATLAS 에는 Python 도 없다. 그래서 모델을 쓰는 판정만 네이티브로
빼고, 앱(`display/atlas/app`)은 보여 주기만 한다.

## 원본과 포팅

| 이 레포 | 원본 | 채점 |
|---|---|---|
| `src/posture_pose.{h,cpp}` | `pico_esp32-cam_ftdi/tools/posture_pose.py` | `test/pose_golden.txt` · 8시나리오 463프레임 |
| `src/vision_link.{h,cpp}` | `tools/esp_source.py` | `src/vision_link_host_test.cpp` · 8항목 |
| `src/pose_geometry.{h,cpp}` | MediaPipe 라이브러리(그래프 계산기들) | `src/pose_geometry_host_test.cpp` · 32항목 |
| `src/pose_model.{h,cpp}` | MediaPipe PoseLandmarker(VIDEO 모드) | `test/pose_landmarks_golden.txt` (아래) |

`pose_geometry` 가 있는 이유: `.task` 안에는 **모델 두 개뿐**이고, 앵커 생성·박스
디코딩·NMS·ROI 계산·크롭·역투영은 전부 MediaPipe 라이브러리 쪽에 있다. 모델만 꺼내
돌리면 아무것도 안 나온다. 그 접착부를 여기서 다시 만든다.

### 조용히 틀리는 것들 (전부 실측으로 잡았다)

- **검출기 입력은 −1~1, 랜드마크 모델은 0~1.** 둘 다 0~1 로 넣으면 모델은 오류 없이
  돌면서 사람을 못 찾는다. `kDetectorRangeMin` / `kLandmarkRangeMin`.
- **앵커는 정확히 2254개.** stride 8·16·32·32·32, 셀당 2개이고 stride 32 인 층 셋이
  묶여 그 자리만 6개다. 수가 다르면 전부 어긋난다.
- **보조 랜드마크 33·34번이 다음 프레임 ROI 를 정한다.** 모델이 39개를 내는 이유가
  그것이다. 이게 없으면 매 프레임 검출기를 돌게 되고, 원본(`RunningMode.VIDEO`)과
  다른 궤적이 나온다.
- **ROI 확대율은 1.25.** 정답지와 맞춰 보면 1.4·1.5·1.6·1.75 로 갈수록 어긋남이
  5.2 → 8.8 → 11.1 → 12.4 → 17.8px 로 단조 증가한다.

## 빌드

`arc build` 는 SDK 환경을 스스로 켜지 않는다. **먼저 source 해야** 호스트 컴파일러가
아니라 크로스 툴체인이 잡힌다.

```bash
# atlas-dev 컨테이너, 레포 루트가 /workspace
unset LD_LIBRARY_PATH
source /opt/atlas-sdk-x86_64/environment-setup-armv8a-atlas-linux
arc build display/atlas/camsvc
# -> display/atlas/camsvc/build/arm64/ipk/com.deskmate.camsvc1.ipk
```

호스트 테스트는 TFLite 없이 돈다(모델을 안 쓰는 부분만 검사한다):

```bash
cmake -S . -B build/host -DBUILD_TESTING=ON && cmake --build build/host && (cd build/host && ctest)
```

## 설치

**모델 두 개는 IPK 에 안 들어간다.** ARC 0.5 는 서비스 실행 파일 하나만 담고, 팀 규약상
학습 모델은 커밋하지 않는다. 설치 뒤 따로 넣는다.

```bash
D=/data/share/usr/atlas/services/com.deskmate.camsvc1
abusctl call com.atlas.PackageManager1 Install "com.deskmate.camsvc1" "/tmp/download/com.deskmate.camsvc1.ipk"
scp models/*.tflite root@<Pi5>:$D/models/            # 먼저 mkdir
ssh root@<Pi5> "chown -R 5059:1011 $D/models"        # 서비스와 같은 주인으로
```

모델은 `pose_landmarker_full.task`(MediaPipe 공개 모델) 안의 zip 두 개다. 받는 곳은
`thermal-pose/README.md` 에 적혀 있고, 꺼내는 것은 그냥 unzip 이다.

```bash
python -c "import zipfile; zipfile.ZipFile('pose_landmarker_full.task').extractall('models')"
```

## 설정 (환경변수)

| 이름 | 기본값 | 쓰임 |
|---|---|---|
| `CAMSVC_PORT` | 8770 | HTTP 포트. 8765 가 아닌 이유는 보드에서 그 번호를 이미 쓰는 것이 있어서다 |
| `CAMSVC_DEVICE` | 자동 | 비우면 `Vision Stream` 이라고 적힌 ttyACM 을 찾는다 |
| `CAMSVC_MODELS` | `<실행파일>/models` | 모델 폴더 |
| `CAMSVC_NO_TRACKING` | 꺼짐 | 매 프레임 검출기부터 다시 돈다. **채점·진단용** |
| `CAMSVC_ROI_SCALE` | 1.25 | 검출 -> ROI 확대율. 채점용 |
| `CAMSVC_LM_RANGE` | `unit` | `pm1` 이면 랜드마크 모델 입력을 −1~1 로. 채점용 |
| `CAMSVC_PROJ_SCALE` | 1.0 | 되돌릴 때 쓰는 사각형만 따로 키운다. 채점용 |

## API (앱이 읽는 것)

앱에 이미 있던 Pi 4 자세 노드 클라이언트를 그대로 쓴다. **봉투 모양을 바꾸지 말 것** —
`app/lib/posture/posture_state.dart` 의 `fromEnvelope` 가 `schema_version` 이 `"1.0"` 이
아니면 던진다.

- `GET /health` — 포트·프레임 수·버린 바이트·CRC 오류·fps·모델 시간·검출 점수·존재 점수·
  프레임 밝기/대비
- `GET /api/state` — 판정 봉투. 첫 판정 전에는 503
- `GET /preview.raw` — 마지막 프레임 + 그 위의 랜드마크 (앱의 "영상 보기")
- `POST /api/calibrate` — 기준 자세 다시 잡기. **202 를 낸다** (앱이 200 을 실패로 읽는다)

`/preview.raw` 는 그림과 뼈대를 **한 응답에** 담는다. 따로 내주면 화면이 두 번 물어야
하고 그 사이 프레임이 바뀌어 뼈대가 엉뚱한 자리에 얹힌다. PNG 가 아닌 이유는 보드에
인코더를 하나 더 두지 않으려고다 - 19KB 라 그냥 보내는 편이 싸다. 규약은
`src/service_api.h` 에 적혀 있다.

`/health` 의 `best_score`·`presence`·`frame_mean`·`frame_stddev` 는 **화면을 안 보고
원인을 가르기 위한 것**이다. 사람을 못 찾을 때 검출에서 떨어졌는지(`best_score` 낮음),
랜드마크에서 떨어졌는지(`presence` 낮음), 아예 카메라 앞이 비었는지(`frame_stddev`
거의 0) 가 이 셋으로 갈린다. **영상은 어디에도 저장하지 않는다.**

## 랜드마크 채점

```bash
# PC: 정답지 만들기 (MediaPipe 가 여기서만 돈다)
../../../../thermal-pose/.venv/Scripts/python.exe tools/gen_pose_landmarks_golden.py

# 보드: 같은 프레임을 우리 파이프라인으로
scp test/frames/*.pgm root@<Pi5>:/tmp/camsvc_frames/
ssh root@<Pi5> "cd $D && CAMSVC_NO_TRACKING=1 ./deskmate_camsvc --replay /tmp/camsvc_frames" > replay.txt

# PC: 채점
python tools/score_pose_landmarks.py replay.txt --mode image
```

입력은 **책상 카메라 영상이 아니라** matplotlib 이 들고 다니는 공개 사진이다
(`grace_hopper.jpg`, 미 해군 공개 자료). 사람이 찍힌 진짜 사진이어야 포즈 모델이 돌고,
사적인 영상을 보드 밖으로 내보내지 않아도 된다. 카메라와 같은 조건으로 160x120 흑백으로
줄여 쓴다.

### 지금까지 나온 값 (2026-09-18)

| | |
|---|---|
| 판정 7점 평균 어긋남 | 5.20px (160x120 기준) |
| 닮음변환(배율·회전·이동)을 뺀 뒤 | **평균 1.76px · 최대 3.15px · 회전 0.02도** |
| 남은 배율 차 | 1.185배 |

즉 **모양은 맞고 크기만 다르다.** 남은 배율은 우리 ROI 와 MediaPipe 의 ROI 가 달라서
생긴다(우리 503px @ (75,195) · MediaPipe 추정 596px @ (70,219)). 판정이 쓰는 값은 전부
어깨 폭으로 나눈 비율이고 캡처한 기준 자세와 견주므로 일정한 배율은 대부분 상쇄된다.

### 사람이 나가면 놓는가 (2026-09-18)

정답지 뒤쪽 20장은 **사람 없는 책상**이다. 여기서 MediaPipe 는 20/20 놓고, 우리는
18/20 놓는다 - 차이는 검출기 재확인 주기(`kRecheckFrames`, 최대 10프레임 ≈ 2초)다.

이 20장이 정답지에 있는 이유는 실기에서 **사람이 나갔는데 "바른 자세" 가 계속 뜬**
버그를 봤기 때문이다. 원인은 랜드마크 모델이 크롭 안에 사람이 없어도 무언가는
반드시 찍는다는 것이고, 그때 존재 점수가 0.502·0.504 로 문턱(0.5)을 아슬아슬하게
안 깼다. **MediaPipe 는 같은 조건에서 놓으므로 이건 포팅 쪽 차이다** - 존재 점수가
왜 우리 쪽에서만 안 떨어지는지는 아직 못 밝혔고, 검출기 재확인으로 결과만 맞춰 뒀다.

**이 비교는 상반신 초상이라 최악 조건이다.** 몸이 화면 아래로 4배쯤 벗어나 있어 ROI 가
거의 외삽이다. 그 조건에서 MediaPipe 자신도 **같은 프레임을 20번 넣으면 판정 7점이
최대 93px 까지 흔들린다**(어깨 폭은 ±5% 유지). 우리 추적도 ROI 가 503 -> 440px 로
수렴하고 어깨 폭이 ±7% 흔들리는 같은 급이다.

## 실기 수치 (Pi 5, 2026-09-18)

| | |
|---|---|
| 모델 | 검출기 ~24ms + 랜드마크 ~20ms (XNNPACK, 2스레드) |
| preview | 4.3fps — 링크가 병목이다(19,212 B/장, 921600bps) |
| 링크 | CRC 오류 0 |

## 안 한 것

- 서비스 자동 시작. D-Bus activation 파일은 깔리지만 `.apptype` 이 IPK 에서 빠져
  (ARC 가 dotfile 을 안 담는다) 지금은 SSH 로 직접 띄운다.
- 실제 책상 영상으로 한 채점. 사적인 영상을 보드 밖으로 내보내지 않기로 해서,
  공개 사진으로만 쟀다.
- 존재 점수가 왜 MediaPipe 만큼 안 떨어지는지. 결과는 검출기 재확인으로 맞췄지만
  원인은 못 찾았다(위 "사람이 나가면 놓는가" 참고).
