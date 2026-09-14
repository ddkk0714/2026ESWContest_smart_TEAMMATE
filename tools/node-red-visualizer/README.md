# DESKMATE PC Node-RED 모니터

제품 통신 경로에 포함되지 않는 PC-local 개발 도구다. Pi 4 MQTT broker가 준비되면
`deskmate/#`를 구독해 실시간 상태와 피드백을 Debug sidebar에서 확인한다.

```powershell
cd tools/node-red-visualizer
npm.cmd install
npm.cmd start
```

브라우저에서 편집기는 <http://127.0.0.1:1880>, 대시보드는 <http://127.0.0.1:1880/ui>로 연다.
`데모:` inject 노드를 누르면 합성 상태·피드백·화면 메시지를 확인할 수 있다.

대시보드에는 다음 패널이 있다.

- mmWave: motion level·거리·유효한 호흡수 차트, 재실·motion·졸음 상태
- 환경: CO₂(400~2000 ppm, 1000 ppm부터 경고색)·온도·습도·조도 게이지
- 키스트로크: dwell/flight 평균·idle ratio·flight CV와 입력 활성 상태
- FSM: 상태·phase·context·gate·reasons와 C_fatigue/C_focus 차트
- 수신 상태: 토픽별 마지막 수신 경과 시간과 seq 불연속 횟수

수신한 `deskmate/#` 메시지는 `logs/nodered-YYYYMMDD.jsonl`에 한 줄씩 기록된다. `logs/`는
저장소 `.gitignore` 대상이다.

사진·데모용으로 `/ui` 페이지가 포커스를 가진 동안 브라우저 키보드의 타이밍 통계만 Node-RED 내부에
임시 생성한다. 키 문자나 키 코드는 수집하지 않으며 MQTT broker로도 발행하지 않는다. 대시보드를 닫으면
수집도 즉시 끝난다.

브로커 주소는 편집기의 설정 노드 `pi4-mqtt-broker` 하나에서만 바꾼다. 우측 상단 메뉴의
Configuration nodes에서 해당 노드를 열어 Server를 Pi 4 주소로 변경하고 Deploy한다.

`settings.js`는 loopback만 사용한다. 외부 공개·포트 포워딩을 하지 않는다.
