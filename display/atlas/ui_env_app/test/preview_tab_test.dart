import 'package:deskmate_display/main.dart';
import 'package:deskmate_display/settings_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'workspace_test_helpers.dart';

/// 개발 도구(설정 → 개발자 화면) 헤더의 탭. '프리뷰' 가 카메라 '자세' 탭 자리에 있다.
const _devTabs = [
  '상태',
  '센서 전체',
  'PC 키스트로크',
  '개발자',
  '프리뷰',
  'FSM 전체',
  'Bluetooth',
  '세션 리포트',
  '연결',
];

void main() {
  testWidgets('developer tools show the preview tab instead of the camera tab',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await pumpDeveloperApp(
        tester,
        DeskmateApp(
            settings: SettingsStore(
                candidates: const [], legacyPostureCandidates: const [])));
    await tester.pump();

    // 사용자 화면(워크스페이스)에는 프리뷰가 없다 — 개발 도구 안에만.
    expect(find.text('개발 도구'), findsOneWidget);
    for (final t in _devTabs) {
      expect(find.byTooltip(t), findsOneWidget, reason: t);
    }
    expect(find.byTooltip('자세'), findsNothing);
    expect(kShowCameraPostureTab, isFalse);

    await tester.tap(find.byTooltip('프리뷰'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('preview-badge')), findsOneWidget);
    expect(find.text('프리뷰 · 정식 기능 아님'), findsOneWidget);
    expect(find.text('얼굴·영상 없음 · 64칸 깊이값만'), findsOneWidget);
    // 열지도 두 장(A·B) 64 칸씩
    expect(find.byKey(const ValueKey('tof-a-zone-0')), findsOneWidget);
    expect(find.byKey(const ValueKey('tof-a-zone-63')), findsOneWidget);
    expect(find.byKey(const ValueKey('tof-b-zone-63')), findsOneWidget);
    // 데모 연결엔 Pi 4 주소가 없어 입력칸이 뜬다
    expect(find.byKey(const ValueKey('preview-host-input')), findsOneWidget);
    // 피드백 블록
    expect(find.byKey(const ValueKey('preview-send')), findsOneWidget);
    expect(find.byKey(const ValueKey('preview-verdict-correct')), findsOneWidget);

    // 다른 탭으로 갔다 와도 그대로
    await tester.tap(find.byTooltip('상태'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('preview-badge')), findsNothing);
    await tester.tap(find.byTooltip('프리뷰'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('preview-badge')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
