# 시연 당일 한 장 — 켜는 순서와 하지 말 것

> 기준일 2026-11-02 (main `7e7289e`). 발표 심사 2026-11-06.
> 이 문서는 **부스에서 보는 한 장**이다. 왜 그런지와 막혔을 때 더 깊은 절차는
> [`reboot-recovery.md`](reboot-recovery.md)·[`field-checklist.md`](field-checklist.md)·시연 동선은 [`demo-scenario.md`](demo-scenario.md).

## 0. 순서가 중요하다

**ESP32 꽂기 → Pi 4 → Pi 5 → 블루투스 → PC 수집기.** 앞을 건너뛰면 뒤가 조용히 실패한다.
부팅 자동 시작은 현재 ATLAS 이미지에서 **안 된다**. 재부팅했으면 아래를 다시 한다.

## 1. Pi 4 (허브)

ESP32 를 Pi 4 USB-A 에 꽂은 **다음에** root 로 실행한다.

```sh
/mnt/rawdata/deskmate_tof/ch340cfg auto 115200                    # CH340 잡기 → /dev/ttyUSB0
sh /data/share/deskmate/recovery/install.sh pi4 --runtime-only    # 브로커 + hub 기동 + 감독
```

확인 — 셋 다 맞아야 넘어간다.

```sh
wget -q -O - http://127.0.0.1:8765/health      # {"status":"ok","state_ready":true,"mqtt":true}
/data/share/deskmate/bin/mosquitto_sub -h 127.0.0.1 -t 'deskmate/#' -v -C 5 -W 20
```

- `deskmate/health/hub` = `online`, `deskmate/state/phase` 가 10 s 마다 온다.
- 로그에 `DESKMATE UART rx=...  crc_errors=0` 이 늘어난다. **rx 가 0 이면** ESP32 를 다시 꽂고 `ch340cfg` → `install.sh` 를 다시 돌린다.

## 2. Pi 5 (화면)

```sh
sh /data/share/deskmate/recovery/install.sh pi5 --runtime-only
```

앱(DESKMATE V46)을 띄우고 **연결 점검**에서 Pi 4 주소가 초록인지 본다.

## 3. 블루투스 — 여기서 제일 많이 막힌다

| 기기 | 설정 앱에서 | 앱에서 |
|---|---|---|
| 스피커 `KLZS-L1` | **페어링해 둔다** (Paired) | 자동 연결 |
| 램프 `KLZS-L1 app` | **연결하지 않는다** | 자동 연결 |
| 선풍기 `DESKMATE-FAN` | **연결하지 않는다** | 자동 연결 |

- 복원 순서는 **램프 → 선풍기 → 스피커**다. 앱이 알아서 이 순서로 한다.
- `Already connected` 가 뜨면 이전 앱의 링크가 남은 것이다 → **Pi 5 재부팅**(1 단계부터 다시).
- 램프·선풍기를 설정 앱에서 연결해 두면 시스템이 링크를 잡아 앱이 못 쓴다.
- 자세한 근거는 `display/atlas/ui_demo_app/README.md` 맨 위 "블루투스 연결 규칙".

## 4. PC 키스트로크 수집기

같은 Wi-Fi 에서:

```powershell
python -m collector --broker auto      # hub 브로커를 스스로 찾는다
```

앱 **PC 키스트로크** 탭에 값이 들어오면 된다. 안 되면 `--broker <Pi4-IP>` 로 직접 준다.

## 5. 시연 직전 확인 (1분)

- [ ] 화면에 상태가 10 s 마다 갱신된다
- [ ] 센서 화면에 ToF 자세 · mmWave 재실 · 환경값이 들어온다
- [ ] 램프 · 선풍기 · 스피커가 앱에서 한 번씩 동작한다
- [ ] 제안 카드를 수락 → 기기가 움직이고, 되돌리기가 원래대로 되돌린다
- [ ] 개발자 화면 **제어 진행** 탭에 줄이 쌓인다

## 6. 빠른 시연 모드

피로 확정은 원래 수 분이 걸린다. 시연에서는 개발자 화면(또는 노트북 devview)의 **빠른 시연** 버튼을 켠다.

- `deskmate/demo/mode {"speed":"fast"}` 가 나가고, 화면에 "빠른 시연" 배지가 뜬다.
- 피로 유지 타이머만 짧아진다. **임계값·판정 경로는 그대로다.**
- 시연 프로파일(`DESKMATE_PROFILE=demo-*`)에서만 켜지고, 30분 뒤 자동으로 normal 로 돌아온다.
- 책 읽기·필기를 보여줄 거면 `demo-b-study` 프로파일을 쓴다(지금 자세 모델이 책 읽기를 거북목으로 오판한다).

## 7. 하지 말 것

- ATLAS 설정 앱에서 **램프·선풍기 연결**
- 시연 중 ESP32 USB 빼기 (빼면 `ch340cfg` 부터 다시)
- Pi 4 를 재부팅하고 1 단계를 건너뛰기
- 부스 Wi-Fi 를 바꾸고 수집기·브로커 주소를 그대로 두기

## 8. 심사위원 질문 대비 — 사실만

- **카메라를 쓰나?** 제품에는 쓰지 않는다. 자세는 ToF 거리 격자(8×8 두 개)로 판정한다.
  저장소의 `camsvc`·`camtest` 는 ToF 가 오기 전에 쓴 개발 앱이고, 깊이 카메라(D435if)는 **학습 데이터 라벨을 만드는 오프라인 도구**다.
  시연·제출 빌드에는 카메라 경로가 없다.
- **음성 안내가 되나?** 지금 Pi 5 이미지는 TTS 를 지원하지 않아 **알림음과 화면 문구**로 대신한다.
- **공기청정기는?** ThinQ API 로 풍량 제어와 공기질 읽기를 실물로 확인했고, FSM 자동 연동은 예정이다.
- **2시간 연속 구동은?** 아직 측정하지 않았다. 결선 이후 항목이다.
