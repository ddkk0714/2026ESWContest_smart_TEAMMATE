# iLink Bluetooth 조명 피드백

## 검증 결과와 범위

2026-09-18에 iLink 앱 패키지명 `com.jwtian.smartbt` 램프에 실제 BLE 프레임을 전송해 다음 순서를 확인했다: 켜기 → 빨강 → 파랑 → 25% 밝기 → 100% 밝기 → 끄기 → 켜기. 이 램프는 iLink 호환 공개 구현의 서비스/특성 UUID와 일치한다.

- Service: `0000A032-0000-1000-8000-00805F9B34FB`
- 쓰기 특성: `0000A040-0000-1000-8000-00805F9B34FB`
- 상태 알림 특성: `0000A042-0000-1000-8000-00805F9B34FB`
- 출처: [donandren/ilink_light](https://github.com/donandren/ilink_light) — BLE 자동 탐색/주소 수동 등록 및 전원·RGB·밝기·색온도 기능을 제공하는 iLink 호환 구현이다.

이 기능은 조명만 다룬다. Pi 5 Atlas 앱에 있던 스피커 음소거·볼륨 제어는 그대로 유지하며, 마이크나 음성 입력을 추가하지 않는다.

## 현재 적용: Pi 5 Atlas 앱 우선

현재 시연 경로는 PC 컨트롤러가 아니라 **Pi 5 Atlas 앱**이다. 앱의 Bluetooth 화면에서 기기를 검색하고, 스피커는 Atlas A2DP로 페어링·연결한다. 음악은 기존 번들 MP3를 선택된 시스템 오디오 출력(연결된 Bluetooth 스피커 포함)으로 재생한다.

iLink 램프는 앱에서 선택한 BLE 장치에 Atlas `Bluetooth1.Gatt`로 연결해 A040 특성에 프레임을 쓴다. `state/phase`가 바뀔 때만 FSM 피드백을 적용하며, 자동 피드백 스위치를 사용자가 켜기 전에는 조명·음악을 자동 실행하지 않는다. `fatigue`는 주황 조명과 음악 재생, `recovery`·`idle`·`end`는 음악 일시정지이며, 색·밝기 정책은 앱 코드의 `feedback_policy.dart`에 있다.

Pi 5 배포 전 검증은 Atlas Docker에서 Flutter test와 release IPK 빌드로 한다. 실제 Bluetooth 페어링·GATT 쓰기는 물리 장비가 있는 Pi 5에서만 별도로 검증한다.
## 프로토콜

프레임은 `55 AA <length> <command...> <checksum>`이다. 이 장비에서 확인한 체크섬은 **`0xFF - Σ(command...)`** 이며 `length` 바이트는 합계에서 제외한다. 결과는 8비트로 자른다.

| 동작 | 프레임 |
| --- | --- |
| 켜기 | `55 AA 01 08 05 01 F1` |
| 끄기 | `55 AA 01 08 05 00 F2` |
| 밝기 `v` (0–255) | `55 AA 01 08 01 <v> cs` |
| RGB | `55 AA 03 08 02 <R> <G> <B> cs` |
| 색온도 | `55 AA 01 08 09 <n> cs`, `n=1`(6000K) ~ `5`(3000K) |
| 상태 요청 | `55 AA 01 08 15 06 DC` → A042에 `55 AA 09 88 15 …` |

## 적용 구조

```text
Pi 4 Hub ── MQTT deskmate/state/phase (retain) ──> PC iLink controller ── BLE ──> lamp
                 └─ MQTT deskmate/control/cmd ──> PC iLink controller ── BLE ──> lamp
```

Pi 4 AI Native OS의 제한 Python에는 BLE 패키지를 추가하지 않는다. Bluetooth 어댑터는 Bluetooth가 있는 개발 PC에서 별도 실행한다. Hub/FSM이 중단돼도 램프 제어기가 판정을 대신하지 않으며, 잘못된/알 수 없는 phase는 명령을 보내지 않는다.

상태별 정책은 [`hub/deskmate_hub/control/control.yaml`](../hub/deskmate_hub/control/control.yaml)에 있고, 초기값은 다음처럼 모두 되돌릴 수 있는 조명 변화다.

| phase | 피드백 |
| --- | --- |
| `idle`, `end` | 끔 |
| `start` | 낮은 밝기의 따뜻한 색 |
| `focus` | 중간 밝기의 차가운 백색 |
| `fatigue` | 주황 경고색 |
| `recovery` | 낮은 밝기의 파랑 |

정책 값과 쿨다운은 YAML에서만 조정한다. 피로가 진단이거나 위험 신호라는 뜻은 아니며, 개입 제안용 시각 피드백이다.

## 연결 및 테스트

1. PC에서 `hub/requirements.txt`를 설치한 가상환경에 Bluetooth 옵션을 설치한다.

   ```bash
   pip install bleak
   ```

2. OS Bluetooth 설정 또는 `BleakScanner`로 램프 MAC 주소를 확인해 `control.yaml`의 `bluetooth.address`에 입력한다. 주소는 비밀값은 아니지만 현장 장비 식별자이므로 공용 문서에는 기록하지 않는다.
3. 우선 프레임만 확인한다. `enabled: true`여도 `--dry-run`은 BLE에 접속하지 않는다.

   ```bash
   cd hub
   python -m deskmate_hub.control.ilink_light --broker <PI4_MQTT_IP> --dry-run
   ```

4. 실제 램프가 가까이 있고 주소가 정확한 것을 확인한 뒤 `control.yaml`에서 `enabled: true`로 바꾸고 같은 명령에서 `--dry-run`을 뺀다.

   ```bash
   python -m deskmate_hub.control.ilink_light --broker <PI4_MQTT_IP>
   ```

컨트롤러는 `state/phase`의 phase가 바뀔 때만 실행하고, `cooldown_sec` 안의 반복 명령은 무시한다. `deskmate/control/cmd`에서는 `target: desk_lamp` 및 `power`, `set_brightness`, `set_rgb`, `set_color_temperature`만 수용한다. 중지하려면 PC 프로세스를 종료하고 `control.yaml`의 `enabled: false`를 복원한다.

## 검증 범위

단위 테스트는 프레임(켜기·끄기·밝기·RGB·색온도·상태 요청), YAML phase 매핑, 명시 명령 필터를 검증한다. 실제 BLE 연결은 램프 위치·PC 어댑터 상태에 의존하므로 현장에서는 위 dry-run 후 한 번씩 전원·색·밝기 동작을 확인한다.