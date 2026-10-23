# 공용 피로 모델 — 데이터 수집과 학습 (2026-10-20)

센서 융합(ToF 자세 · mmWave · 키스트로크 · 환경)으로 **KSS 1~9 피로 점수**를 내는 공용 모델과,
프로필마다 그 점수를 보정하는 Pi 4 개인 head 1단계. 근거 사례는 노션 "피로도 측정 논문".

현재 상태: 수집 앱·기록기·특징·학습·내보내기·기기 쪽 예측 코드까지. **hub 라이브 루프 연결과 프로필별 보정 저장은 아직**이고,
학습 결과는 합성 데이터로 동작만 확인했다(성능 근거 아님).

## 1. 수집 절차

준비: Pi 4(hub·broker), Pi 5 앱, ESP32 센서, PC 키스트로크 수집기가 평소처럼 돈다. PC 에서 기록기를 켠다.

```sh
python -m pip install -r tools/requirements.txt
python tools/fatigue_logger.py --broker <PI4_IP>        # 저장: ml/datasets/fatigue/raw/ (gitignore)
```

Pi 5 에는 기본 DESKMATE 앱과 따로 **피로 기록 앱**(`display/atlas/fatigue_app`, `com.atlas.app.deskmate_fatigue`)을 설치한다.
수집하는 동안은 이 앱을 띄워 둔다(전체 화면이라 기본 앱과 동시에 보이지 않는다 — 15분 질문을 놓치지 않게).

```bash
# atlas-dev 컨테이너 안 (display/atlas/README.md §3 과 같은 절차)
cd /workspace/display/atlas/fatigue_app && flutter pub get && flutter test
flutter-atlas build atlas --ipk --release --dart-define=DESKMATE_MQTT_HOST=<Pi4-IP>
bash /workspace/display/atlas/scripts/deploy-app.sh --app-dir display/atlas/fatigue_app --install
```

브로커 주소는 빌드 값 → 기본 앱이 저장한 settings.json → 앱 위쪽 주소 버튼(숫자 키패드) 순으로 정한다.
위쪽 줄에 연결 상태와 미전송 건수가 보인다(끊긴 동안의 응답은 쌓였다가 다시 보낸다).


1. 프로필 슬롯 A~F 를 고른다. 처음이면 아침형/저녁형·평소 수면·주 작업을 답한다. 다른 사람이 쓰면 다른 슬롯을 쓴다.
   같은 슬롯을 다른 사람에게 넘길 때는 **프로필 다시 만들기**(새 id 가 된다).
2. 어젯밤 수면 시간과 지금 졸림(KSS)을 고르고 **작업 시작**.
3. 15분마다 어느 화면에서든 "지금 얼마나 졸리세요?" 카드가 뜬다. 3분 안에 답하지 않으면 놓침으로 남는다.
4. 졸리면 언제든 **졸려요**. 끝낼 때 **작업 종료**에서 마지막 KSS.

KSS: 1 매우 또렷함 · 3 또렷함 · 5 보통 · 7 졸리지만 버틸 만함 · 9 매우 졸림(잠과 싸우는 중).

목표량: 5명 이상 × 4세션 × 90~120분. **피로 범위가 넓어야 한다** — 1인당 오전·점심 직후·밤늦게를 섞고,
덜 잔 날을 한 번은 넣는다. 오전 세션만 모으면 모델이 피로를 배울 수 없다.

## 2. 무엇이 남나

`<profile_id>/<session_id>.jsonl` 한 줄이 한 기록:

- `kind: label` — `deskmate/fatigue/label` 의 data 그대로(+`rx_ts`). [mqtt-topics.md](mqtt-topics.md) "피로 데이터 수집".
- `kind: tick` — 세션 중 `deskmate/state/phase`(10 s)의 `fsm_state`·`c_fatigue`·`sensor_summary` 등.
  sensor_summary 에는 ToF 자세 클래스·신뢰도·끄덕임, mmWave 체동·호흡, CO₂·온습도·조도 원값, 키스트로크 타이밍 통계가 있다.
  ToF 원본 격자·키 내용은 없다(운영 원칙).

자가기록은 **개인 기록이다 — 저장소에 커밋하지 않는다.** 외부 서버(UBAI 등)로 옮길 때는 팀 확인 후 가명 id 상태로만.

## 3. 특징 (hub/deskmate_hub/fatigue/features.py)

라벨 시각(질문이 뜬 시각)으로 끝나는 **5분 창**. 학습과 기기 추론이 같은 함수를 쓴다(표준 라이브러리만).

- 자세: ToF 10클래스를 6묶음 비율(upright·forward·head_down·chin_rest·recline·side) + 분당 전환 수, 끄덕임·drowsy·움직임
- mmWave: 체동 평균·표준편차, 호흡·심박 중앙값(`*_valid` 인 값만)
- 키스트로크: 분당 입력, dwell 평균·표준편차, flight 평균·변동계수, 공백 비율, 정정 비율
- 환경: CO₂ 평균·분당 기울기, 온도, 습도, 조도
- 문맥: 경과 분, 시각(sin·cos), 시작 KSS, 어젯밤 수면, 수면 부족(평소−어젯밤), 아침/저녁형, FSM C_fatigue 평균

자세·mmWave·키스트로크 일부(`RELATIVE`)는 **세션 처음 10분 대비 차이**로 바꾼다 — 사람마다 다른 평소 값을 지운다.
없는 값은 비워 두고(0 으로 채우지 않음) 모델이 학습 평균으로 대신한다.

## 4. 학습

```sh
python -m ml.training.fatigue_train --raw ml/datasets/fatigue/raw --out ml/models/fatigue/fatigue_linear.json
python -m ml.training.fatigue_train --synthetic --out <임시 경로>      # 파이프라인 확인용
```

- 모델: 표준화 + 릿지 회귀 → KSS. 데이터가 수백 개 수준이라 더 큰 모델은 사람 단위 검증에서 이기기 어렵다.
- 보조 표본: 응답 사이를 선형 보간한 1분 간격 창(학습 전용). 평가는 실제 응답만.
- 검증: **프로필 단위로 빼고 검증(LOSO)**, 릿지 강도도 학습 프로필 안에서만 고른다.
- 리포트: `mean`(평균 찍기) · `start`(시작 KSS 유지) · `model` · `personal`(모델 + 그 사람의 앞선 응답으로 보정) 의
  MAE · 순위 상관 · KSS≥7 판별 AUC. **model 이 mean·start 를 못 이기면 센서가 쓸모 있다는 근거가 없다.**
- 내보내기: JSON(`deskmate-fatigue-linear-1`). 내보낸 뒤 기기 쪽 코드(`FatigueModel`)로 다시 읽어 numpy 예측과 같은지 확인한다.

합성 데이터(6명×4세션) 동작 확인 값 — 실제 성능이 아니다: MAE mean 1.50 · start 1.80 · model 0.69 · personal 0.64.

## 5. 개인 head 1단계 (PersonalBias)

프로필마다 "공용 예측이 평소 얼마나 빗나가는가" 하나만 배운다. 응답 (실제 KSS − 공용 예측) 을 누적하고
`prior_n=3` 만큼 0 쪽으로 당긴다(응답 1개로 크게 흔들리지 않게). 응답이 20개 넘게 쌓이면 2단계(계수 일부 재학습)를 검토한다.

## 6. hub 라이브 점수 — 관찰 전용 (2026-10-23)

`hub/deskmate_hub/fatigue/live.py` · 설정 `config/fatigue.yaml`. **FSM 입력이 아니다** — 화면·기록용으로 `state/phase` 에만 싣는다.

- 수신: Pi 4 C++ 브리지(`hub/atlas/src/mqtt_bridge.cpp`)가 `deskmate/fatigue/label` 을 QoS 1 로 구독해 `MQTT` 줄로 넘기고,
  PC 경로(`MqttSource`)도 같은 토픽을 구독한다. 앱 `session_start` 의 시작 KSS·수면·프로필이 세션 문맥이다.
  앱 세션이 없으면 hub FSM 세션 시작 시각만 쓴다(문맥 답변 특징은 비고 모델이 학습 평균으로 대신한다).
- 계산: tick(10 s)마다 state/phase 와 같은 모양(`ts`·`c_fatigue`·`sensor_summary`)을 모으고 `period_sec`(60 s)마다
  그 시각으로 끝나는 창을 §3 의 같은 함수로 만든다 → `FatigueModel`. 학습 tick(fatigue_logger)과 같은 입력이다.
- 출력: `state/phase.data.sensor_summary.fatigue = {kss, kss_personal, window_s, n_ticks, model_id, valid, ts}`.
  `valid` = 창 안 tick 이 기대 개수(window_s / frame_period_sec)의 `min_tick_ratio`(0.8) 이상. `ts` = 계산 시각(1분마다 바뀜).
  `kss_personal` 은 프로필 보정이 허용되고 그 프로필의 보정값이 있을 때만, 아니면 `null`.
- 프로필 보정: KSS 응답이 오면 질문 시각(`prompt_ts`) 창의 공용 예측과 비교해 `PersonalBias` 갱신 →
  `logs/personalization/fatigue_bias.json`(모델 id 와 함께, 모델이 바뀌면 새로 시작). `personal.require_consent: true`(기본)면
  `privacy.yaml` 이 켜져 있고 동의했을 때만 갱신·저장·적용하고, 동의 철회·삭제 때 파일을 지운다.
- 모델: 패키지 `config/fatigue_linear.json`(있으면 IPK 빌드가 넣는다, gitignore) 또는 환경변수 `DESKMATE_FATIGUE_MODEL=<파일>`.
  둘 다 없으면 피로 점수만 꺼지고 허브는 그대로 돈다(`[fatigue] 모델 JSON 없음` 한 줄).

## 남은 일

1. 실기: 피로 기록 앱 + 기록기와 함께 보드에서 `sensor_summary.fatigue` 확인(모델은 합성 학습본으로 시험 가능 —
   `python -m ml.training.fatigue_train --synthetic --out <파일>` 을 `DESKMATE_FATIGUE_MODEL` 로).
2. FSM 연결 방식: 계획서대로 2단계 확신도 → 게이트(기본 관찰 전용으로 시작).
3. 실데이터로 첫 학습 후 특징 중요도·결측률 보고, 쓸모없는 특징 정리.
