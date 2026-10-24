import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'package:deskmate_ui_demo/pr56/home_widgets.dart';
import 'package:deskmate_ui_demo/pr56/home_weather_tile.dart';
import 'package:deskmate_ui_demo/pr56/workspace_shell.dart';

Future<void> settle(WidgetTester tester) async {
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 700));
  await tester.pump();
}

void main() {
  testWidgets(
      'bottom sensor opens the font-corrected PR56 workspace and returns',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final model = DemoModel(DemoConfig(
        jsonDecode(File('assets/config/demo.json').readAsStringSync())
            as Map<String, dynamic>));
    await tester.pumpWidget(DemoApp(model: model, ticking: false));
    await settle(tester);
    await tester.tap(find.text('720 ppm'));
    await settle(tester);
    expect(find.byType(DeskmateWorkspace), findsOneWidget);
    expect(find.byKey(const ValueKey('home-fixed-grid')), findsOneWidget);
    for (final type in [
      StandbyClockTile,
      MonthCalendarTile,
      EnvironmentTile,
      FocusCountdownTile,
      HomeMusicTile
    ]) {
      expect(find.byType(type), findsOneWidget);
      final rect = tester.getRect(find.byType(type));
      expect(rect.bottom, lessThanOrEqualTo(600));
      expect(rect.right, lessThanOrEqualTo(1024));
    }
    expect(find.byKey(const ValueKey('workspace-brand')), findsNothing);
    expect(find.text('DESKMATE'), findsNothing);
    expect(find.text('자세 히트맵'), findsNothing);
    expect(find.byKey(const ValueKey('workspace-navigation-scroll')),
        findsNothing);
    expect(find.byKey(const ValueKey('workspace-return-home')), findsNothing);
    await tester.tap(find.byType(MonthCalendarTile));
    await settle(tester);
    expect(find.byKey(const ValueKey('home-fixed-grid')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('workspace-return-home')));
    await settle(tester);
    expect(find.byKey(const ValueKey('home-fixed-grid')), findsOneWidget);
    final space =
        tester.getTopLeft(find.byKey(const ValueKey('home-edit-space'))) +
            const Offset(8, 40);
    await tester.longPressAt(space);
    await settle(tester);
    expect(find.text('홈 편집'), findsOneWidget);
    await tester.tap(find.text('위젯'));
    await settle(tester);
    expect(find.text('사용 중인 위젯'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('picker-row-music')));
    await settle(tester);
    await tester.tap(find.byKey(const ValueKey('picker-row-weather')));
    await settle(tester);
    await tester.tap(find.text('닫기'));
    await settle(tester);
    await tester.tap(find.text('완료'));
    await settle(tester);
    expect(find.byType(HomeWeatherTile), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('workspace-return-clock')));
    await settle(tester);
    expect(model.page, DemoPage.home);
    expect(find.byKey(const ValueKey('home-clock-start')), findsOneWidget);
    await tester.tap(find.text('720 ppm'));
    await settle(tester);
    expect(find.byType(HomeWeatherTile), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
}
