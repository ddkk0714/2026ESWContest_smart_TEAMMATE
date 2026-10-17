# ESP32 VL53L8CX bring-up 펌웨어 (김태환, 2026-10-14 전달)

`tof-bringup-full-4MB.bin` = ESP32 4 MB 플래시 전체 덤프 (부트로더 0x1000, 파티션 0x8000, 앱 0x10000, PlatformIO Arduino, esp-idf 4.4.7).
소스는 팀 레포 어느 브랜치에도 없다. 펌웨어 문자열에서 알아낸 것:

- 부팅: `VL53L8CX I2C bring-up` → `Uploading firmware (init)...` → `init took N ms` → `Ranging started (8x8 @ 15 Hz)`
- 프레임: `F,<ms>,<64 거리>` (상태값 64개가 더 붙는지는 미확인)
- 온보드 규칙 판정: `P,<ms>,<present>,<posture>,<conf>,…` (posture: unknown/upright/recline/face_down/chin_rest/drowsy/away/lean_forward)
- 안내: `# posture baseline reset: sit upright for 10 s`, `# background captured from next frame`, `# rows flipped|normal, estimator restarted`
- 오류: `[ERR] <단계> failed (status=N)`, `no I2C ACK at 0x29 (check SDA/SCL, SPI_I2C_N, NCS)`
- USB 보드율: 미확인 (tof_live.py / hub/atlas-tof 는 자동 탐색)

굽기: `pip install esptool` 뒤 `esptool.py --chip esp32 --port COM5 --baud 460800 write_flash 0x0 tof-bringup-full-4MB.bin`
읽기: `tof-sim-v5/tof_live.py --ports COM5 --echo` (형식 확인) → `hub/atlas-tof` 서비스(Pi 4)와 같은 파서.
