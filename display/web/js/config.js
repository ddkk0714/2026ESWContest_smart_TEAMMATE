/* DESKMATE display 설정 — 보드에서 이 파일 한 줄만 고치면 된다.
 *
 * source : 화면에 채울 데이터의 출처.
 *   "mqtt"  — 허브 브로커에서 받는다. 실제 운영 경로.
 *   "local" — 보드에 직접 꽂힌 키보드를 web-app 이 캡처한다. 브로커도 collector 도 필요 없다.
 *             실기에서 "키보드 입력이 앱까지 오는가" 를 확인하는 용도. docs 는 display/README.md
 *
 * brokerUrl : MQTT-over-WebSocket 주소.  ws://<브로커IP>:9001
 *   - 브로커는 DESKMATE 허브(Pi 4)에 둔다. docs/mqtt-topics.md
 *   - 1883(TCP) 이 아니라 9001(WebSocket) 이다. 브라우저는 원시 TCP 소켓을 못 쓴다.
 *   - mosquitto 에 websockets 리스너를 열어야 한다 → broker/mosquitto-deskmate.conf
 *
 * URL 쿼리로 임시 덮어쓰기 가능:
 *   ?src=local   ?broker=ws://192.168.50.7:9001   ?demo=1
 */
window.DESKMATE = {
  source: "mqtt",

  brokerUrl: "ws://localhost:9001",

  topics: {
    keystroke: "deskmate/sensor/keystroke",
    phase: "deskmate/state/phase",
    health: "deskmate/health/+"
  },

  // source: "local" 일 때만 쓰인다. 상수는 collector/config.py 와 같은 값으로 맞춰 둔다.
  capture: {
    node: "pi-keyboard",   // PC collector 의 "pc-collector" 와 구분되는 노드 이름
    windowS: 60,
    periodMs: 1000,        // 팀 규약 1Hz
    flightGapMaxS: 2.0,
    idleGapS: 3.0
  },

  historyN: 300,        // 추이 링버퍼 길이 (1Hz 기준 5분)
  staleAfterMs: 5000    // 이 시간 넘게 수신 없으면 '끊김' 표시
};
