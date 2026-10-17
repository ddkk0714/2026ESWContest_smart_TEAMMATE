# keystroke — Pi 5 국면 대시보드

`display/atlas/app`(`com.atlas.app.deskmate_display`) 옆에 두는 두 번째 Atlas Flutter 앱이다.
앱 ID 가 `com.atlas.app.keystroke` 로 달라 Pi 5 에 두 앱을 동시에 설치할 수 있다.

첫 화면은 현재 작업 국면 대시보드다. 데이터 모양은 [docs/mqtt-topics.md](../../../docs/mqtt-topics.md) 의
`deskmate/state/phase` 규약에 맞췄고, 지금은 mock 소스가 국면을 순환시킨다.
실제 연동 시 mock 자리를 MQTT 구독으로 교체한다.

## 빌드와 배포

Atlas 컨테이너 안에서 수행한다. 절차와 스크립트는 [../deploy/README.md](../deploy/README.md) 를 따른다.

```powershell
.\display\atlas\scripts\pi-deploy.ps1                              # device.env 기본 대상이 이 앱일 때
.\display\atlas\scripts\pi-deploy.ps1 -AppDir display/atlas/keystroke
```

컨테이너에서 직접 빌드할 때는 다음과 같다.

```bash
source "$ATLAS_FLUTTER_NDK_ENV"
cd /workspace/display/atlas/keystroke
flutter pub get
flutter test
flutter-atlas build atlas --ipk --release
```

## 이름을 바꿀 때

앱 이름 · ID 는 세 곳에 있고 서로 맞아야 한다.

- `pubspec.yaml` 의 `name`
- `atlas/CMakeLists.txt` 의 `ATLAS_APP_ID` 와 `BINARY_NAME`
- `atlas/meta/appinfo.json` 의 `id` · `name` · `entry`

`BINARY_NAME` 이 `appinfo.json` 의 `entry` 첫 토큰과 다르면 설치는 되지만 실행되지 않는다.
