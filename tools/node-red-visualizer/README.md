# DESKMATE PC Node-RED 시각화

PC에서 실행 중인 MQTT 브로커와 연결해 `deskmate/#` 메시지를 대시보드와 Debug sidebar에서 확인하는 개발 도구다.

```powershell
cd tools/node-red-visualizer
npm.cmd install
npm.cmd start
```

- 편집기: <http://127.0.0.1:1880>
- 대시보드: <http://127.0.0.1:1880/ui>
- MQTT 브로커: PC의 `127.0.0.1:18883`

편집기의 기본 흐름은 이 폴더의 `flows.json`이다. 실행 위치와 관계없이 같은 파일을
읽으며, 편집 후 **Deploy**하면 이 파일에 저장된다. 브라우저 새로고침과 Node-RED
재실행 후에도 유지된다. `npm.cmd start`로 실행하고 임시 flows 파일을 인자로 넘기지 않는다.

상단에는 실제 MQTT 수신, 센서별 분기, mmWave·환경·PC 키스트로크·FSM 표시,
토픽별 마지막 수신 시각·seq 갭 및 JSONL 기록 흐름이 배치돼 있다. 하단 데모 버튼은
화면에만 합성값을 주입하며 실제 MQTT 상태·제어 명령을 발행하지 않는다.
수신값이 없으면 게이지·차트가 비어 있는 것이 정상이다. Pi 5가 꺼져 있어도
PC 브로커와 PC 수집기가 실행 중이면 키스트로크 토픽은 수신할 수 있다.

대시보드에는 mmWave, 환경 센서, 키스트로크, FSM 및 통신 상태와 함께 다음 항목이 표시된다.

- **제어 패널**: `deskmate/control/cmd` 명령과 `deskmate/control/result` 실행 결과 관찰
- **리포트 패널**: `deskmate/session/report` 세션 요약 표시

수신한 `deskmate/#` 메시지는 저장소 기준 `tools/node-red-visualizer/logs/` 아래의
`nodered-YYYYMMDD.jsonl` 파일에 한 줄당 하나의 JSON 객체로 기록된다. `logs/`는 `.gitignore` 적용 대상이다.

이 도구는 키 내용, 카메라, 상시 음성 및 운영 ToF raw 데이터를 수집하거나 발행하지 않는다.
브로커 주소를 바꿔야 할 때는 Node-RED 편집기의 MQTT configuration node에서 변경한 뒤 Deploy한다.
