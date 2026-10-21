# DESKMATE 보조 환기팬 — Raspberry Pi Pico 2 W + Keyes L9110 팬 모듈
# MicroPython (RPI_PICO2_W 펌웨어). Pi 5 앱이 BLE 중앙으로 붙어 GATT 특성에 쓴다.
#
# 결선
#   모듈 GND -> Pico GND
#   모듈 VCC -> Pico VBUS(핀 40, USB 5V)   ※ 3V3(OUT) 금지
#   모듈 INA -> GP16 (PWM)
#   모듈 INB -> GP17 (항상 LOW, 정방향만)
#   VCC-GND 사이 100 µF 전해 캐패시터
#
# BLE 프로토콜 (모두 128-bit 사용자 UUID)
#   서비스   d35e0001-5f4a-4b2c-9c1e-0f6a7b8c9d01
#   CMD      d35e0002-...  write / write-no-response
#              1 바이트: 0~100 = 속도(%) (0 = 끄기)
#              또는 ASCII: "on" "off" "0".."100"  (nRF Connect 로 손 테스트용)
#   STATUS   d35e0003-...  read / notify
#              1 바이트: 현재 속도(%)
#
# 보드 LED: 광고 중 깜빡임, 연결되면 켜짐.

import bluetooth
import struct
import time
from machine import Pin, PWM, Timer
from micropython import const

# ---------- 핀 · PWM ----------
PIN_INA = 16
PIN_INB = 17
PWM_FREQ_HZ = 20_000          # 가청 영역 위로 올려 모터 소음 감소
KICK_MS = 150                 # 저속 시동 시 100 % 로 잠깐 밀어 주는 시간
KICK_BELOW_PCT = 40

ina = PWM(Pin(PIN_INA))
ina.freq(PWM_FREQ_HZ)
ina.duty_u16(0)
inb = Pin(PIN_INB, Pin.OUT, value=0)
led = Pin("LED", Pin.OUT, value=0)

_speed = 0  # 현재 속도 %


def set_speed(pct):
    """0~100 % 로 팬 속도 설정. 저속 시동은 짧게 100 % 를 줘 멈춤을 막는다."""
    global _speed
    pct = max(0, min(100, int(pct)))
    if pct > 0 and _speed == 0 and pct < KICK_BELOW_PCT:
        ina.duty_u16(65535)
        time.sleep_ms(KICK_MS)
    ina.duty_u16(pct * 65535 // 100)
    _speed = pct
    _notify_status()
    print("fan", pct, "%")


# ---------- BLE ----------
_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)

_FLAG_READ = const(0x0002)
_FLAG_WRITE_NO_RESPONSE = const(0x0004)
_FLAG_WRITE = const(0x0008)
_FLAG_NOTIFY = const(0x0010)

SVC_UUID = bluetooth.UUID("d35e0001-5f4a-4b2c-9c1e-0f6a7b8c9d01")
CMD_UUID = bluetooth.UUID("d35e0002-5f4a-4b2c-9c1e-0f6a7b8c9d01")
STATUS_UUID = bluetooth.UUID("d35e0003-5f4a-4b2c-9c1e-0f6a7b8c9d01")
DEVICE_NAME = "DESKMATE-FAN"

_SERVICE = (
    SVC_UUID,
    (
        (CMD_UUID, _FLAG_WRITE | _FLAG_WRITE_NO_RESPONSE),
        (STATUS_UUID, _FLAG_READ | _FLAG_NOTIFY),
    ),
)

ble = bluetooth.BLE()
ble.active(True)
ble.config(gap_name=DEVICE_NAME)
((_h_cmd, _h_status),) = ble.gatts_register_services((_SERVICE,))
ble.gatts_write(_h_status, bytes([0]))

_conns = set()


def _notify_status():
    payload = bytes([_speed])
    ble.gatts_write(_h_status, payload)
    for ch in _conns:
        try:
            ble.gatts_notify(ch, _h_status, payload)
        except OSError:
            pass


def _parse_cmd(data):
    """바이너리 1 바이트 또는 ASCII 문자열을 속도 % 로."""
    if len(data) == 1 and data[0] <= 100:
        return data[0]
    try:
        text = data.decode().strip().lower()
    except (UnicodeError, AttributeError):
        return None
    if text in ("on", "1"):
        return 100
    if text in ("off", "0"):
        return 0
    try:
        return int(text)
    except ValueError:
        return None


def _irq(event, data):
    if event == _IRQ_CENTRAL_CONNECT:
        conn_handle, _, _ = data
        _conns.add(conn_handle)
        led.on()
        print("connected", conn_handle)
    elif event == _IRQ_CENTRAL_DISCONNECT:
        conn_handle, _, _ = data
        _conns.discard(conn_handle)
        print("disconnected", conn_handle)
        _advertise()
    elif event == _IRQ_GATTS_WRITE:
        conn_handle, value_handle = data
        if value_handle == _h_cmd:
            raw = ble.gatts_read(_h_cmd)
            pct = _parse_cmd(raw)
            if pct is None:
                print("bad cmd", raw)
            else:
                set_speed(pct)


def _adv_payload():
    # Flags(LE general discoverable, BR/EDR 미지원) + Complete Local Name
    name = DEVICE_NAME.encode()
    p = bytearray()
    p += struct.pack("BB", 2, 0x01) + b"\x06"
    p += struct.pack("BB", len(name) + 1, 0x09) + name
    return bytes(p)


def _scan_resp_payload():
    # 128-bit 서비스 UUID 는 스캔 응답에 실어 광고 31 바이트 한도를 지킨다
    uuid_bytes = bytes(SVC_UUID)
    return struct.pack("BB", len(uuid_bytes) + 1, 0x07) + uuid_bytes


def _advertise():
    ble.gap_advertise(250_000, adv_data=_adv_payload(), resp_data=_scan_resp_payload(), connectable=True)
    print("advertising as", DEVICE_NAME)


ble.irq(_irq)
_advertise()

# 광고 중 LED 깜빡임 (연결되면 _irq 에서 켠 상태 유지)
_blink = Timer()


def _tick(_):
    if not _conns:
        led.toggle()


_blink.init(period=500, mode=Timer.PERIODIC, callback=_tick)

# 메인 루프는 할 일이 없다. BLE 이벤트는 IRQ 로 처리된다.
while True:
    time.sleep(1)
