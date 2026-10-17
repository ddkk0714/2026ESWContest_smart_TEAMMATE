// DESKMATE — ToF 자세 화면 (Pi 5 ATLAS Flutter)
//
// Pi 4 의 자세 서비스(deskmate_tofposture, :8771)가 ESP32 의 VL53L8CX 두 센서 8×8 프레임으로
// 낸 자세 판정을 `GET /api/state` 로 읽어 보여 준다. 판정은 전부 Pi 4 에서 나고 이 앱은
// 그리기만 한다 — `pc/posture_view.html`(노트북 뷰)과 같은 내용, 같은 라벨·색.
//
// 주소는 셋 중 먼저 있는 것: 보드에 저장된 파일 → 빌드 define `DESKMATE_TOF_URL` →
// 기본값(유선 직결 172.16.34.199). 화면 위 주소 칸을 누르면 바꾸고 저장한다.
//
// 옆 앱(ui_env_app 통합 화면, camtest)과 앱 ID 가 달라 Pi 5 에 나란히 설치된다.
import 'package:flutter/material.dart';

import 'address_store.dart';
import 'labels.dart';
import 'posture_view.dart';
import 'tof_client.dart';

/// 빌드에 박는 주소(배포 스크립트에서 `--dart-define=DESKMATE_TOF_URL=...`). 비워도 된다.
const _definedUrl = String.fromEnvironment('DESKMATE_TOF_URL');

void main() => runApp(const PostureViewApp());

/// 시작 주소를 고른다. 저장 파일 → define → 기본값.
String resolveInitialUrl(AddressStore store, {String defined = _definedUrl}) {
  final saved = store.load();
  if (saved != null) {
    final url = normalizeBaseUrl(saved, defaultPort: kTofPort);
    if (url != null) return url;
  }
  if (defined.isNotEmpty) {
    final url = normalizeBaseUrl(defined, defaultPort: kTofPort);
    if (url != null) return url;
  }
  return kDefaultBaseUrl;
}

class PostureViewApp extends StatelessWidget {
  const PostureViewApp({super.key, this.store, this.sourceFactory});

  /// 테스트는 임시 경로의 저장소를 넣는다.
  final AddressStore? store;

  /// 테스트는 가짜 소스를 넣어 네트워크 없이 그린다.
  final TofSourceFactory? sourceFactory;

  @override
  Widget build(BuildContext context) {
    final store = this.store ?? AddressStore();
    return MaterialApp(
      title: 'DESKMATE ToF 자세',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: kBg,
        colorScheme: const ColorScheme.dark(
          primary: kBar,
          surface: kCard,
          onSurface: kFg,
        ),
        useMaterial3: true,
      ),
      home: Scaffold(
        body: SafeArea(
          child: TofPostureView(
            initialBaseUrl: resolveInitialUrl(store),
            store: store,
            sourceFactory: sourceFactory ?? HttpTofSource.new,
          ),
        ),
      ),
    );
  }
}
