# Eclipse Paho MQTT C — 헤더만 동봉

- 출처: https://github.com/eclipse-paho/paho.mqtt.c `v1.3.13` `src/` (2026-09-17 업스트림과 바이트 동일 확인)
- 라이선스: Eclipse Public License v2.0 **또는** Eclipse Distribution License v1.0 (이중 라이선스, [`epl-v20`](epl-v20) · [`edl-v10`](edl-v10))
- 동봉 이유: Atlas SDK 컨테이너에는 Paho 헤더·cross-link 라이브러리가 없다. Pi 4 ATLAS 런타임에는
  `/usr/lib/libpaho-mqtt3a.so.1.3.13`(`paho-mqtt-c 1.3.13-r0`, `thinq-client` 의존)이 이미 있으므로
  `hub/atlas/src/mqtt_bridge.cpp` 가 이 헤더로 컴파일하고 실행 시 `dlopen("libpaho-mqtt3a.so.1")` 으로 붙는다.
  링크 타임 의존이 없어 IPK 는 Paho 바이너리를 포함하지 않는다.
- 수정 금지. 버전을 올릴 때는 보드의 `opkg list-installed | grep paho` 와 맞춘다.
