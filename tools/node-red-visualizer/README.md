# DESKMATE PC Node-RED 시각화

PC에서 실행 중인 MQTT 브로커와 연결해 `deskmate/#` 메시지를 대시보드와 Debug sidebar에서 확인하는 개발 도구다.

```powershell
cd tools/node-red-visualizer
npm.cmd install
npm.cmd start
```

- 편집기: <http://127.0.0.1:1880>
- 대시보드: <http://127.0.0.1:1880/ui>
- MQTT 브로커: PC의 `127.0.0.1:1883`

대시보드에는 mmWave, 환경 센서, 키스트로크, FSM 및 통신 상태와 함께 다음 항목이 표시된다.

- **제어 패널**: `deskmate/control/cmd` 명령 발행
- **리포트 패널**: `deskmate/session/report` 세션 요약 표시

수신한 `deskmate/#` 메시지는 저장소 기준 `tools/node-red-visualizer/logs/` 아래의
`nodered-YYYYMMDD.jsonl` 파일에 한 줄당 하나의 JSON 객체로 기록된다. `logs/`는 `.gitignore` 적용 대상이다.

이 도구는 키 내용, 카메라, 상시 음성 및 운영 ToF raw 데이터를 수집하거나 발행하지 않는다.
브로커 주소를 바꿔야 할 때는 Node-RED 편집기의 MQTT configuration node에서 변경한 뒤 Deploy한다.