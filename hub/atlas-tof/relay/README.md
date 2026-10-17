# relay — hub 없이 UART 0x05 를 자세 서비스로

`uart_relay_board` 는 Pi 4 에서 `/dev/ttyAMA0`(ESP32 UART2, COBS + CRC-16/CCITT-FALSE)를 읽어 TYPE `0x05` 만
`UART\t{"type":5,"seq":S,"ts_ms":T,"payload_hex":"..."}` 줄로 바꿔 `127.0.0.1:9100`(deskmate_tofposture)에 넣는다.
HTTP `:8772/raw` 에 센서별 마지막 원본 프레임(거리 mm·상태·frame_id·flags)을 JSON 으로 낸다.

- **hub 와 동시에 쓰지 않는다.** 같은 UART 를 둘이 읽으면 바이트가 갈린다. 운영은 hub 의 `uart_relay`(PR #44).
- payload 는 서비스가 읽는 v1 배치로 바꿔 넣는다. 방향 비트는 0(펌웨어가 이미 장면 방향으로 보냄).
  표준 배치를 직접 읽는 서비스 판에서는 변환이 필요 없지만, 옛 서비스와도 맞게 남겨 둔다.

빌드(aarch64 정적, Zig 0.13 — `tools/build_static_aarch64.sh` 와 같은 도구):

```sh
python -m ziglang c++ -target aarch64-linux-musl -O2 -std=c++17 -static -s -o uart_relay_board uart_relay_board.cpp
scp uart_relay_board relay.sh root@<Pi4>:/mnt/rawdata/deskmate_tof/
ssh root@<Pi4> sh /mnt/rawdata/deskmate_tof/relay.sh          # stop · status
```

PC 에서 같은 일을 하는 예비판: `../pc/uart_relay_pc.py --to <Pi4>:9100` (ssh 로 `cat /dev/ttyAMA0` 를 끌어온다).
