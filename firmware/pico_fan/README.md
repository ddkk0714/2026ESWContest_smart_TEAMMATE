# pico_fan — Pico 2 W 보조 환기팬 (BLE 주변기기)

Pi 5 앱이 램프(iLink)에 하듯 BLE 중앙으로 붙어 GATT 특성에 바이트를 쓰면 팬이 돈다.
허브·MQTT 와는 직접 통신하지 않는다. `control/cmd` 의 `vent_fan` 을 앱이 받아 이 장치에
쓰고 `control/result` 를 돌려주는 식으로 연결하면 된다.

## 부품 · 결선

| 모듈 (Keyes L9110 Fan) | Pico 2 W |
|---|---|
| GND | GND (핀 38) |
| VCC | VBUS 5V (핀 40) — **3V3 OUT 금지** |
| INA | GP16 (핀 21, PWM) |
| INB | GP17 (핀 22, 항상 LOW) |

VCC–GND 사이에 100 µF 전해 캐패시터(극성 주의). USB 전원이면 추가 전원 불필요.

## 올리기

1. [MicroPython RPI_PICO2_W 펌웨어](https://micropython.org/download/RPI_PICO2_W/) `.uf2` 를
   BOOTSEL 누른 채 꽂아 드라이브에 복사.
2. Thonny 또는 `mpremote cp main.py :main.py` 로 `main.py` 를 보드에 복사.
3. 리셋하면 `DESKMATE-FAN` 으로 광고 시작. 보드 LED 가 깜빡이면 광고 중, 켜져 있으면 연결됨.

## BLE 프로토콜

| 항목 | UUID | 속성 | 내용 |
|---|---|---|---|
| 서비스 | `d35e0001-5f4a-4b2c-9c1e-0f6a7b8c9d01` | | |
| CMD | `d35e0002-5f4a-4b2c-9c1e-0f6a7b8c9d01` | write, write‑no‑rsp | 1 바이트 `0..100` = 속도 % (0 = 끄기). ASCII `on`/`off`/`"60"` 도 허용 |
| STATUS | `d35e0003-5f4a-4b2c-9c1e-0f6a7b8c9d01` | read, notify | 1 바이트 현재 속도 % |

앱 쪽 매핑 제안: `set_power on` → `[100]`, `off` → `[0]`. 쓰기 성공 후 STATUS 를 읽어
`actual_value` 로 돌려주면 램프보다 한 단계 더 확실한 `succeeded` 가 된다.

## PC 에서 손 테스트

```bash
pip install bleak
python firmware/pico_fan/fan_ble_test.py 60
```

nRF Connect(폰)로도 CMD 특성에 `3C`(=60) 한 바이트, 또는 텍스트 `on` 을 쓰면 된다.

## 동작 메모

- PWM 20 kHz, 40 % 미만에서 시동할 때 150 ms 동안 100 % 를 줘서 모터 멈춤을 막는다.
- 연결이 끊겨도 마지막 속도를 유지하고 다시 광고한다. 자동 꺼짐 타이머는 없다.
- 역회전은 쓰지 않는다 (INB 고정 LOW).
