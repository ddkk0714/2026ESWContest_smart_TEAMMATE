#!/usr/bin/env bash
# MqttStateSource(앱의 MQTT 수신 코드)가 브로커 재시작 뒤에도 메시지를 받는지 docker 로 확인한다.
# 10-26 Pi 5: Pi 4 bringup 이 Mosquitto 를 다시 띄울 때마다 앱이 "연결됨" 인 채로 귀가 먹었다.
#
#   bash display/atlas/ui_env_app/tool/mqtt_broker_restart/run.sh            # docker restart
#   OUTAGE=45 RUN_S=120 bash .../run.sh                                      # 45 s 꺼 뒀다 켜기(보드 재부팅)
#
# 필요: docker, atlas-dev:latest 이미지. 기대 출력: --- app 아래에 GOT before-1·2, after-1·2·3.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=${SRC:-$(cd "$HERE/../../../../.." && pwd)}
docker network create mqtt-repro >/dev/null 2>&1 || true
CONF=$(mktemp)
printf 'listener 1883
allow_anonymous true
' > "$CONF"
docker rm -f mqtt-broker >/dev/null 2>&1 || true
docker run -d --name mqtt-broker --network mqtt-repro \
  -v "$CONF:/mosquitto/config/mosquitto.conf:ro" eclipse-mosquitto:2.0.20 >/dev/null \
  || { echo "broker start failed"; exit 1; }
pub() {
  docker exec mqtt-broker mosquitto_pub -t deskmate/control/cmd \
    -m "{\"schema_version\":\"1.0\",\"node\":\"repro\",\"data\":{\"command_id\":\"$1\",\"target_id\":\"none\"}}"
  echo "$(date +%T) PUB $1"
}
LOG=$(mktemp)
docker run --rm --name mqtt-app --network mqtt-repro -e MQTT_HOST=mqtt-broker -e RUN_S="${RUN_S:-60}" \
  -v "$SRC:/src:ro" atlas-dev:latest bash -lc '
    cp -r /src /tmp/ws && cd /tmp/ws/display/atlas/ui_env_app &&
    cp tool/mqtt_broker_restart/broker_restart_test.dart test/ &&
    flutter pub get >/dev/null 2>&1 &&
    flutter test test/broker_restart_test.dart 2>&1 |
      grep --line-buffered -E "READY|GOT|fetch:|All tests|Some tests|\[E\]|Error"' > "$LOG" 2>&1 &
APP=$!
for _ in $(seq 1 120); do grep -q READY "$LOG" 2>/dev/null && break; sleep 2; done
echo "$(date +%T) app ready"
pub before-1; sleep 3; pub before-2; sleep 3
if [ -n "${OUTAGE:-}" ]; then
  echo "$(date +%T) STOP broker ${OUTAGE}s"; docker stop mqtt-broker >/dev/null
  sleep "$OUTAGE"; docker start mqtt-broker >/dev/null; echo "$(date +%T) START broker"
else
  echo "$(date +%T) RESTART broker"; docker restart mqtt-broker >/dev/null
fi
sleep 12
pub after-1; sleep 5; pub after-2; sleep 5; pub after-3
wait $APP
echo "--- app (컨테이너 시계는 UTC)"; grep -v "다시 연결 중" "$LOG"
echo "(재연결 대기 중 fetch 실패 $(grep -c "다시 연결 중" "$LOG")회)"
rm -f "$LOG" "$CONF"
docker rm -f mqtt-broker >/dev/null 2>&1
