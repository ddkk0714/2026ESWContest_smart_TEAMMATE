"""특징 페이로드를 MQTT 로 발행. 브로커가 없으면 콘솔/파일 로깅으로 폴백한다.

- 토픽: config.topic (기본 deskmate/sensor/keystroke), QoS 0
- health: deskmate/health/<node> 에 LWT(online/offline) + 재연결 카운트
- 재연결: 지수 백오프 1s → 최대 30s (docs/mqtt-topics.md)
paho-mqtt 2.x API.
"""
from __future__ import annotations

import json
import os
import time

import paho.mqtt.client as mqtt


class FeaturePublisher:
    def __init__(self, cfg):
        self.cfg = cfg
        self.connected = False
        self._reconnects = -1  # 최초 connect 를 0 으로 세기 위해

        os.makedirs(cfg.log_dir, exist_ok=True)
        self._log_path = os.path.join(cfg.log_dir, "keystroke.jsonl")

        self._c = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{cfg.node}-{int(time.time())}",
        )
        self._c.on_connect = self._on_connect
        self._c.on_disconnect = self._on_disconnect
        self._c.reconnect_delay_set(min_delay=1, max_delay=30)
        self._c.will_set(cfg.health_topic, json.dumps({"node": cfg.node, "status": "offline"}),
                         qos=1, retain=True)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.connected = True
            self._reconnects += 1
            client.publish(
                self.cfg.health_topic,
                json.dumps({"ts": round(time.time(), 3), "node": self.cfg.node,
                            "status": "online", "reconnects": self._reconnects}),
                qos=1, retain=True,
            )
            print(f"[mqtt] connected → {self.cfg.broker_host}:{self.cfg.broker_port}, "
                  f"topic={self.cfg.topic}")
        else:
            print(f"[mqtt] connect failed: {reason_code}")

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        print("[mqtt] disconnected (자동 재연결)")

    def start(self):
        try:
            self._c.connect_async(self.cfg.broker_host, self.cfg.broker_port, keepalive=30)
            self._c.loop_start()
        except Exception as e:
            print(f"[mqtt] start error ({e}); 로컬 로깅으로 계속")

    def publish(self, payload: dict):
        payload = {"ts": round(time.time(), 3), **payload}
        line = json.dumps(payload, ensure_ascii=False)
        with open(self._log_path, "a", encoding="utf-8") as fp:
            fp.write(line + "\n")
        if self.connected:
            self._c.publish(self.cfg.topic, line, qos=self.cfg.qos)
            print(f"[PUB] {line}")
        else:
            print(f"[LOG] {line}")

    def stop(self):
        try:
            if self.connected:
                self._c.publish(self.cfg.health_topic,
                                json.dumps({"node": self.cfg.node, "status": "offline"}),
                                qos=1, retain=True)
            self._c.loop_stop()
            self._c.disconnect()
        except Exception:
            pass
