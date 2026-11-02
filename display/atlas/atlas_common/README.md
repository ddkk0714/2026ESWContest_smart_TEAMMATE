# atlas_common

Atlas 앱(`ui_env_app` · `fatigue_app`)이 함께 쓰는 작은 조각만 모았다.

| 파일 | 내용 |
|---|---|
| `diag.dart` | 보드에서 앱 표준출력이 journal 에 남지 않아 쓰는 파일 진단 로그 |
| `hub_config.dart` | 허브 주소 저장·정규화(`HubStore`·`normalizeIp`) |
| `hub_setup.dart` | 허브 주소 입력 화면 |
| `palette.dart` | 공통 색 |

원래 `camtest`(ToF 도착 전에 쓰던 카메라 자세 개발 앱) 안에 있던 파일들이다.
그 앱을 지우면서 카메라와 상관없는 이 네 개만 옮겼다(2026-11-02).
