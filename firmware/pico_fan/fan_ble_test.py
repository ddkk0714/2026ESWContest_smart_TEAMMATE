"""PC(Windows/맥/리눅스)에서 Pico 팬을 BLE 로 손 테스트.

    pip install bleak
    python fan_ble_test.py 60        # 60 % 로
    python fan_ble_test.py off       # 끄기
    python fan_ble_test.py           # 0 → 30 → 100 → 0 순서로 자동 데모

Pi 5 앱이 하는 일(스캔 → 연결 → CMD 특성에 1 바이트 write → STATUS 읽기)을 그대로 흉내 낸다.
"""
import asyncio
import sys

from bleak import BleakClient, BleakScanner

DEVICE_NAME = "DESKMATE-FAN"
CMD_UUID = "d35e0002-5f4a-4b2c-9c1e-0f6a7b8c9d01"
STATUS_UUID = "d35e0003-5f4a-4b2c-9c1e-0f6a7b8c9d01"


def parse(arg: str) -> int:
    arg = arg.strip().lower()
    if arg in ("on",):
        return 100
    if arg in ("off",):
        return 0
    value = int(arg)
    if not 0 <= value <= 100:
        raise SystemExit("속도는 0~100 또는 on/off")
    return value


async def main(speeds: list[int]) -> None:
    print("scanning for", DEVICE_NAME, "...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
    if device is None:
        raise SystemExit("못 찾음: Pico 전원·main.py 실행·광고 LED 깜빡임 확인")
    print("found", device.address)

    async with BleakClient(device) as client:
        def on_status(_, data: bytearray) -> None:
            print("  status notify:", data[0], "%")

        await client.start_notify(STATUS_UUID, on_status)
        for pct in speeds:
            print("write", pct, "%")
            await client.write_gatt_char(CMD_UUID, bytes([pct]), response=True)
            await asyncio.sleep(0.3)
            status = await client.read_gatt_char(STATUS_UUID)
            print("  status read :", status[0], "%")
            if len(speeds) > 1:
                await asyncio.sleep(2.0)
        await client.stop_notify(STATUS_UUID)


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(main([parse(a) for a in args] if args else [0, 30, 100, 0]))
