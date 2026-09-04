# DESKMATE PC Node-RED 모니터

제품 통신 경로에 포함되지 않는 PC-local 개발 도구다. Pi 4 MQTT broker가 준비되면
`deskmate/#`를 구독해 실시간 상태와 피드백을 Debug sidebar에서 확인한다.

```powershell
cd tools/node-red-visualizer
npm.cmd install
npm.cmd start
```

브라우저에서 <http://127.0.0.1:1880>을 열고, Debug sidebar를 연다. `데모:` inject 노드를
누르면 MQTT broker 없이도 흐름과 메시지 형식을 확인할 수 있다.

`settings.js`는 loopback만 사용한다. 외부 공개·포트 포워딩을 하지 않는다.
