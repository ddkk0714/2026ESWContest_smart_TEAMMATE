import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:deskmate_display/bluetooth_service.dart';
import 'package:deskmate_display/calendar_panel.dart';
import 'package:deskmate_display/clock_tools.dart';
import 'package:deskmate_display/music_device_page.dart';
import 'package:deskmate_display/feedback_policy.dart';
import 'package:deskmate_display/home_widgets.dart';
import 'package:deskmate_display/home_layout.dart';
import 'package:deskmate_display/home_weather_tile.dart';
import 'package:deskmate_display/home_widget_picker.dart';
import 'package:deskmate_display/weather_sky.dart';
import 'package:deskmate_display/weather_snapshot.dart';
import 'package:deskmate_display/co2_grade.dart';
import 'package:deskmate_display/irumae.dart';
import 'package:deskmate_display/music_playback.dart';
import 'package:deskmate_display/workspace_shell.dart';
import 'package:deskmate_display/workspace_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets(
      'home fits without dragging; widgets navigate to their matching pages',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final store = _MemoryStore();
    final music = _Music();
    final bluetooth = _Bluetooth();
    final feedback = FeedbackCoordinator(
        music: music, lamp: IlinkLampFeedback(bluetooth), bluetooth: bluetooth);
    var now = DateTime(2026, 10, 18, 9);
    await tester.pumpWidget(MaterialApp(
        home: DeskmateWorkspace(
            state: null,
            demo: true,
            now: () => now,
            music: music,
            feedback: feedback,
            store: store,
            developerBuilder: (_) => const Text('Diagnostics'),
            onConnections: () {},
            onAccept: () {},
            onReject: () {},
            onUndo: () {})));
    await tester.pump();
    expect(find.byKey(const ValueKey('workspace-scroll')), findsNothing);
    for (final type in [
      StandbyClockTile,
      MonthCalendarTile,
      EnvironmentTile,
      FocusCountdownTile,
      HomeMusicTile
    ]) {
      final rect = tester.getRect(find.byType(type));
      expect(rect.top, greaterThanOrEqualTo(0));
      expect(rect.bottom, lessThanOrEqualTo(600));
      expect(rect.right, lessThanOrEqualTo(1024));
    }
    for (var day = 1; day <= 31; day++) {
      expect(find.byKey(ValueKey('home-calendar-day-$day')), findsOneWidget);
    }
    expect(find.text('YOUR TIME'), findsNothing);
    expect(find.text('SOUND'), findsNothing);
    expect(find.text('사운드 선택'), findsNothing);
    expect(
        find.descendant(
            of: find.byType(MonthCalendarTile),
            matching: find.byIcon(Icons.open_in_full_rounded)),
        findsNothing);
    expect(
        find.descendant(
            of: find.byType(FocusCountdownTile),
            matching: find.byIcon(Icons.tune_rounded)),
        findsNothing);
    expect(
        find.descendant(
            of: find.byType(HomeMusicTile),
            matching: find.byIcon(Icons.more_horiz_rounded)),
        findsNothing);
    expect(find.text('예시 센서 값'), findsNothing);
    expect(find.text('24.6 °C'), findsOneWidget);
    expect(tester.getSize(find.byType(IrumaeAnimation)).width, greaterThan(20));
    expect(
        tester.getSize(find.byType(IrumaeAnimation)).height, greaterThan(20));
    expect(find.text('조도'), findsNothing);
    expect(find.textContaining('720'), findsNothing);
    expect(find.text('10월 18일 일요일'), findsNothing);
    final gridBefore =
        tester.getRect(find.byKey(const ValueKey('home-fixed-grid')));
    await tester.drag(find.byType(FocusCountdownTile), const Offset(0, -120));
    await tester.pump();
    expect(tester.getRect(find.byKey(const ValueKey('home-fixed-grid'))),
        gridBefore);
    now = now.add(const Duration(seconds: 3));
    await tester.pump(const Duration(seconds: 3));
    expect(find.byKey(const ValueKey('home-focus-countdown')), findsOneWidget);
    expect(
        tester
            .widget<Text>(find.byKey(const ValueKey('home-focus-countdown')))
            .data,
        '00:03');
    expect(find.text('목표 25 min'), findsOneWidget);

    await tester.tap(find.byType(MonthCalendarTile));
    await _settle(tester);
    expect(find.byType(DeskCalendarPanel), findsOneWidget);
    expect(find.byType(Dialog), findsNothing);
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-home')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-home')));
    await _settle(tester);
    await tester.tap(find.byType(StandbyClockTile));
    await _settle(tester);
    expect(find.byType(DeskClockPage), findsOneWidget);
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-home')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-home')));
    await _settle(tester);
    await tester.tap(find.byType(EnvironmentTile));
    await _settle(tester);
    expect(find.text('실내'), findsNWidgets(3));
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-home')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-home')));
    await _settle(tester);
    await tester.tap(find.byType(FocusCountdownTile));
    await _settle(tester);
    await tester.tap(find.text('50분'));
    await tester.pump();
    expect(store.preferences.focusMinutes, 50);
    expect(find.byType(Dialog), findsNothing);
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-home')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-home')));
    await _settle(tester);
    await tester.tap(find.byType(HomeMusicTile));
    await _settle(tester);
    expect(find.byType(MusicDevicePage), findsOneWidget);
    expect(find.byType(Dialog), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await feedback.dispose();
  });

  testWidgets('home stays pinned while the sidebar scrolls', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final music = _Music();
    final bluetooth = _Bluetooth();
    final feedback = FeedbackCoordinator(
        music: music, lamp: IlinkLampFeedback(bluetooth), bluetooth: bluetooth);
    await tester.pumpWidget(MaterialApp(
        home: DeskmateWorkspace(
            state: null,
            demo: true,
            music: music,
            feedback: feedback,
            store: _MemoryStore(),
            developerBuilder: (_) => const Text('Diagnostics'),
            onConnections: () {},
            onAccept: () {},
            onReject: () {},
            onUndo: () {})));
    await _settle(tester);
    final home = find.byKey(const ValueKey('workspace-nav-home'));
    final before = tester.getRect(home);
    await tester.drag(find.byKey(const ValueKey('workspace-navigation-scroll')),
        const Offset(0, -400));
    await _settle(tester);
    expect(tester.getRect(home), before);
    expect(before.bottom, lessThanOrEqualTo(600));
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-settings')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-settings')));
    await _settle(tester);
    await tester.tap(home);
    await _settle(tester);
    expect(find.byType(StandbyClockTile), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await feedback.dispose();
  });

  for (final screen in [
    const Size(1024, 600),
    const Size(800, 600),
    const Size(320, 800)
  ]) {
    testWidgets('clock, device detail and music pages work and fit $screen',
        (tester) async {
      tester.view.physicalSize = screen;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      var now = DateTime(2026, 10, 18, 16, 25);
      final store = _MemoryStore();
      final music = _Music();
      final bluetooth = _Bluetooth();
      final feedback = FeedbackCoordinator(
          music: music,
          lamp: IlinkLampFeedback(bluetooth),
          bluetooth: bluetooth);
      await tester.pumpWidget(MaterialApp(
          home: DeskmateWorkspace(
              state: null,
              demo: true,
              now: () => now,
              music: music,
              feedback: feedback,
              store: store,
              developerBuilder: (_) => const Text('Diagnostics'),
              onConnections: () {},
              onAccept: () {},
              onReject: () {},
              onUndo: () {})));
      Future<void> nav(String id) async {
        final item = find.byKey(ValueKey('workspace-nav-$id'));
        await tester.ensureVisible(item);
        await tester.tap(item);
        await _settle(tester);
      }

      await nav('clock');
      await tester.ensureVisible(find.text('1분'));
      await tester.tap(find.text('1분'));
      await tester
          .ensureVisible(find.byKey(const ValueKey('clock-timer-toggle')));
      await tester.tap(find.byKey(const ValueKey('clock-timer-toggle')));
      await _settle(tester);
      final tools =
          tester.widget<DeskClockPage>(find.byType(DeskClockPage)).tools;
      await nav('home');
      now = now.add(const Duration(seconds: 3));
      await tester.pump(const Duration(seconds: 3));
      await nav('clock');
      expect(tools.remaining, 57);
      expect(tools.timerRunning, isTrue);
      await tester.tap(find.text('스톱워치'));
      await _settle(tester);
      await tester
          .ensureVisible(find.byKey(const ValueKey('clock-watch-toggle')));
      await tester.tap(find.byKey(const ValueKey('clock-watch-toggle')));
      await _settle(tester);
      expect(tools.watch.isRunning, isTrue);
      await nav('home');
      await nav('clock');
      expect(tools.watch.isRunning, isTrue);
      await tester.tap(find.text('알람'));
      await _settle(tester);
      await tester.tap(find.byKey(const ValueKey('clock-add-alarm')));
      await _settle(tester);
      await tester.enterText(
          find.byKey(const ValueKey('clock-alarm-label')), '스트레칭');
      await tester.tap(find.byKey(const ValueKey('clock-save-alarm')));
      await _settle(tester);
      expect(store.preferences.alarms.single.label, '스트레칭');
      await tester.tap(find.byType(Switch));
      await _settle(tester);
      expect(store.preferences.alarms.single.enabled, isFalse);
      await nav('devices');
      final lamp =
          tester.getSize(find.byKey(const ValueKey('device-card-lamp')));
      expect(lamp.width, closeTo(lamp.height, .1));
      await tester.tap(find.byKey(const ValueKey('device-card-lamp')));
      await _settle(tester);
      await tester
          .ensureVisible(find.byKey(const ValueKey('lamp-tone-slider')));
      await tester.drag(
          find.byKey(const ValueKey('lamp-tone-slider')),
          Offset(
              tester
                  .getSize(find.byKey(const ValueKey('lamp-tone-slider')))
                  .width,
              0));
      await _settle(tester);
      expect(
          tester
              .widget<Slider>(find.byKey(const ValueKey('lamp-tone-slider')))
              .value,
          6);
      expect(tester.takeException(), isNull);
      await nav('home');
      await tester.tap(find.byType(HomeMusicTile));
      await _settle(tester);
      expect(find.byType(MusicDevicePage), findsOneWidget);
      final musicTabs = find.byType(NavigationBar);
      await tester
          .tap(find.descendant(of: musicTabs, matching: find.text('플레이리스트')));
      await _settle(tester);
      await tester.tap(find.byTooltip('플레이리스트 추가'));
      await _settle(tester);
      await tester.enterText(
          find.byKey(const ValueKey('playlist-name')), '작업 음악');
      FocusManager.instance.primaryFocus?.unfocus();
      await _settle(tester);
      for (final i in [0, 2]) {
        final item = find.byType(CheckboxListTile).at(i);
        await tester.ensureVisible(item);
        await _settle(tester);
        await tester.tap(item);
        await _settle(tester);
      }
      await tester.tap(find.byKey(const ValueKey('playlist-save')));
      await _settle(tester);
      expect(store.preferences.playlists.single.tracks, [0, 2]);
      expect(tester.widget<NavigationBar>(musicTabs).selectedIndex, 2);
      await tester.scrollUntilVisible(find.text('작업 음악'), 80,
          scrollable: find.descendant(
              of: find.byKey(const PageStorageKey('music-playlists-list')),
              matching: find.byType(Scrollable)));
      await _settle(tester);
      await tester.tap(find.text('작업 음악'));
      await _settle(tester);
      expect(music.queue, [0, 2]);
      expect(music.playing, isTrue);
      await tester
          .tap(find.descendant(of: musicTabs, matching: find.text('라디오')));
      await _settle(tester);
      await tester.scrollUntilVisible(find.text('작업실 라디오'), 80,
          scrollable: find.descendant(
              of: find.byKey(const PageStorageKey('music-radio-list')),
              matching: find.byType(Scrollable)));
      await _settle(tester);
      await tester.tap(find.text('작업실 라디오'));
      await _settle(tester);
      expect(music.queue, [4, 5]);
      expect(music.selectedTrack, 4);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      await feedback.dispose();
    });
  }

  testWidgets(
      'seven-step lamp controls send documented off, brightness and CCT frames',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final store = _MemoryStore();
    final music = _Music();
    final bluetooth = _RecordBluetooth();
    final feedback = FeedbackCoordinator(
        music: music,
        lamp: IlinkLampFeedback(bluetooth)..select('AA:BB:CC:DD:EE:01'),
        bluetooth: bluetooth);
    await tester.pumpWidget(MaterialApp(
        home: DeskmateWorkspace(
            state: null,
            demo: false,
            music: music,
            feedback: feedback,
            store: store,
            developerBuilder: (_) => const Text('Diagnostics'),
            onConnections: () {},
            onAccept: () {},
            onReject: () {},
            onUndo: () {})));
    final nav = find.byKey(const ValueKey('workspace-nav-devices'));
    await tester.ensureVisible(nav);
    await tester.tap(nav);
    await _settle(tester);
    await tester.tap(find.byKey(const ValueKey('device-card-lamp')));
    await _settle(tester);
    for (final command in [
      ('lamp-level-slider', 500.0, [1, 8, 1, 255]),
      ('lamp-tone-slider', -500.0, [1, 8, 9, 5]),
      ('lamp-tone-slider', 500.0, [1, 8, 9, 1]),
      ('lamp-level-slider', -650.0, [1, 8, 5, 0])
    ]) {
      final slider = find.byKey(ValueKey(command.$1));
      await tester.ensureVisible(slider);
      await tester.drag(slider, Offset(command.$2, 0));
      await _settle(tester);
      expect(bluetooth.frames.last, ilinkFrame(command.$3));
    }
    await tester.tap(find.byTooltip('별명 변경'));
    await _settle(tester);
    await tester.enterText(find.byType(TextField), '나의 스탠드');
    await tester.tap(find.text('저장'));
    await _settle(tester);
    expect(store.preferences.lampNickname, '나의 스탠드');
    final restored = WorkspacePreferences.fromJson(store.preferences.toJson());
    expect(restored.lampNickname, '나의 스탠드');
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await feedback.dispose();
  });

  test('all widget permutations fit two rows with fixed footprints', () {
    Iterable<List<String>> permutations(List<String> items) sync* {
      if (items.isEmpty) {
        yield [];
        return;
      }
      for (final id in items) {
        for (final rest in permutations(items.where((s) => s != id).toList())) {
          yield [id, ...rest];
        }
      }
    }

    for (final clockSpan in [1, 2]) {
      for (final musicSpan in [1, 2]) {
        final sizes = {'clock': clockSpan, 'music': musicSpan};
        for (final order in permutations(
            homeWidgetSpans.keys.where((id) => id != 'weather').toList())) {
          final occupied = <String>{};
          for (final slot in packHomeWidgets(order, 4, sizes: sizes)) {
            expect(slot.row, lessThan(2));
            expect(slot.span, sizes[slot.id] ?? homeWidgetSpans[slot.id]);
            for (var col = slot.column; col < slot.column + slot.span; col++) {
              expect(occupied.add('${slot.row}:$col'), isTrue);
            }
          }
          expect(occupied.length, 4 + clockSpan + musicSpan);
        }
      }
    }
  });

  test('six widgets fit with weather; expansion cannot overflow the home', () {
    final order = widgetNames.keys.toList();
    expect(homeWidgetsFit(order), isFalse);
    expect(homeWidgetsFit(order, sizes: {'clock': 1, 'music': 1, 'weather': 2}),
        isTrue);
    expect(homeWidgetsFit(order, sizes: {'clock': 2, 'music': 2, 'weather': 2}),
        isFalse);
  });

  testWidgets(
      'weather is added, resized by grip, removed and added with animated rows',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final store = _MemoryStore();
    final music = _Music();
    final bluetooth = _Bluetooth();
    final feedback = FeedbackCoordinator(
        music: music, lamp: IlinkLampFeedback(bluetooth), bluetooth: bluetooth);
    await tester.pumpWidget(MaterialApp(
        home: DeskmateWorkspace(
            state: null,
            demo: true,
            music: music,
            feedback: feedback,
            store: store,
            now: () => DateTime(2026, 10, 18, 9),
            developerBuilder: (_) => const Text('Diagnostics'),
            onConnections: () {},
            onAccept: () {},
            onReject: () {},
            onUndo: () {})));
    await tester.pump();
    final mascotState = tester.state(find.byType(IrumaeAnimation));
    final calendarRect =
        tester.getRect(find.byKey(const ValueKey('home-position-calendar')));
    await _editHome(tester);
    expect(tester.state(find.byType(IrumaeAnimation)), same(mascotState));
    // Header geometry stays fixed while controls fade in.
    expect(
        tester
            .getRect(find.byKey(const ValueKey('home-position-calendar')))
            .top,
        calendarRect.top);
    await tester.tap(find.text('위젯'));
    await _settle(tester);
    expect(find.byType(CheckboxListTile), findsNothing);
    final weatherToggle = find.byKey(const ValueKey('picker-toggle-weather'));
    await tester.tap(weatherToggle);
    await _settle(tester);
    expect(store.preferences.widgets.contains('weather'), isFalse);
    expect(find.textContaining('홈이 가득'), findsOneWidget);
    await tester.tap(find.text('닫기'));
    await _settle(tester);
    for (final id in ['clock', 'music']) {
      await tester.drag(
          find.byKey(ValueKey('home-resize-$id')), const Offset(-80, 0));
      await _settle(tester);
    }
    expect(find.byKey(const ValueKey('home-analog-clock')), findsOneWidget);
    await tester.tap(find.text('위젯'));
    await _settle(tester);
    final before = tester.getTopLeft(weatherToggle).dy;
    await tester.tap(weatherToggle);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 120));
    final during = tester.getTopLeft(weatherToggle).dy;
    expect(during, greaterThan(before));
    await _settle(tester);
    expect(tester.getTopLeft(weatherToggle).dy, greaterThan(during));
    expect(store.preferences.widgets.contains('weather'), isTrue);
    final bottom = tester.getTopLeft(weatherToggle).dy;
    await tester.tap(weatherToggle);
    await _settle(tester);
    expect(tester.getTopLeft(weatherToggle).dy, lessThan(bottom));
    await tester.tap(weatherToggle);
    await _settle(tester);
    await tester.tap(find.text('닫기'));
    await _settle(tester);
    expect(tester.widget<HomeWeatherTile>(find.byType(HomeWeatherTile)).wide,
        isFalse);
    await tester.drag(
        find.byKey(const ValueKey('home-resize-weather')), const Offset(80, 0));
    await _settle(tester);
    expect(store.preferences.sizes['weather'], 2);
    expect(tester.widget<HomeWeatherTile>(find.byType(HomeWeatherTile)).wide,
        isTrue);
    await tester.drag(
        find.byKey(const ValueKey('home-resize-clock')), const Offset(80, 0));
    await _settle(tester);
    expect(store.preferences.sizes['clock'], 1);
    for (final id in store.preferences.widgets) {
      expect(tester.getRect(find.byKey(ValueKey('home-position-$id'))).bottom,
          lessThanOrEqualTo(600));
    }
    await tester.tap(find.text('취소'));
    await _settle(tester);
    expect(store.preferences.widgets.contains('weather'), isFalse);
    expect(store.preferences.sizes, {'clock': 2, 'music': 2, 'weather': 1});
    expect(tester.state(find.byType(IrumaeAnimation)), same(mascotState));
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await feedback.dispose();
  });

  testWidgets(
      'weather sizes and picker fit compact screens and never imply live data',
      (tester) async {
    addTearDown(tester.view.reset);
    tester.view.devicePixelRatio = 1;
    for (final size in [
      const Size(130, 120),
      const Size(280, 120),
      const Size(210, 232),
      const Size(438, 232)
    ]) {
      for (final wide in [false, true]) {
        tester.view.physicalSize = size;
        await tester.pumpWidget(MaterialApp(
            home: Scaffold(
                body: HomeWeatherTile(
                    now: DateTime(2026, 10, 18, 9), demo: true, wide: wide))));
        await tester.pump();
        expect(find.text('데모'), findsOneWidget);
        expect(tester.takeException(), isNull);
      }
    }
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: HomeWeatherTile(
                now: DateTime(2026, 10, 18), demo: false, wide: false))));
    expect(find.text('19°'), findsNothing);
    expect(find.text('날씨 연결 대기'), findsOneWidget);
    tester.view.physicalSize = const Size(320, 600);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: Center(
                child: HomeWidgetPicker(
                    selected: widgetNames.keys.toList(),
                    onToggle: (_) => true)))));
    await _settle(tester);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('weather preview changes sky, current icon and forecast together',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final store = _MemoryStore();
    store.preferences.widgets = ['weather'];
    final music = _Music();
    final bluetooth = _Bluetooth();
    final feedback = FeedbackCoordinator(
        music: music, lamp: IlinkLampFeedback(bluetooth), bluetooth: bluetooth);
    await tester.pumpWidget(MaterialApp(
        home: DeskmateWorkspace(
            state: null,
            demo: true,
            music: music,
            feedback: feedback,
            store: store,
            now: () => DateTime(2026, 10, 18, 9),
            developerBuilder: (_) => const Text('Diagnostics'),
            onConnections: () {},
            onAccept: () {},
            onReject: () {},
            onUndo: () {})));
    await tester.pump();
    expect(tester.widget<WeatherSky>(find.byType(WeatherSky)).kind,
        WeatherKind.cloudy);
    await tester.tap(find.byType(HomeWeatherTile));
    await _settle(tester);
    expect(find.byType(Dialog), findsNothing);
    expect(find.text('데모 날씨'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('weather-demo-rain')));
    await _settle(tester);
    for (final sky in tester.widgetList<WeatherSky>(find.byType(WeatherSky))) {
      expect(sky.kind, WeatherKind.rain);
    }
    await tester.tap(find.byType(SwitchListTile));
    await _settle(tester);
    for (final sky in tester.widgetList<WeatherSky>(find.byType(WeatherSky))) {
      expect(sky.isNight, isTrue);
    }
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-home')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-home')));
    await _settle(tester);
    expect(find.byType(AlertDialog), findsNothing);
    expect(tester.widget<WeatherSky>(find.byType(WeatherSky)).isNight, isTrue);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await feedback.dispose();
  });

  testWidgets(
      'every weather sky fits both footprints, including compact resize intermediates',
      (tester) async {
    addTearDown(tester.view.reset);
    tester.view.devicePixelRatio = 1;
    for (final kind in WeatherKind.values) {
      for (final night in [false, true]) {
        for (final size in [
          const Size(130, 120),
          const Size(210, 232),
          const Size(438, 232)
        ]) {
          for (final wide in [false, true]) {
            tester.view.physicalSize = size;
            final weather = WeatherSnapshot.demo(kind, isNight: night);
            await tester.pumpWidget(MaterialApp(
                home: Scaffold(
                    body: HomeWeatherTile(
                        wide: wide,
                        demo: true,
                        now: DateTime(2026, 10, 18, 9),
                        weather: weather))));
            await _settle(tester);
            expect(
                tester.widget<WeatherSky>(find.byType(WeatherSky)).kind, kind);
            expect(find.text('${weather.temperature}°'), findsWidgets);
            expect(tester.takeException(), isNull,
                reason: '$kind night=$night $size wide=$wide');
          }
        }
      }
    }
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('weather changes theme without retaining an opposite-color sky',
      (tester) async {
    tester.view.physicalSize = const Size(438, 232);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    for (final dark in [false, true, false]) {
      await tester.pumpWidget(MaterialApp(
          theme:
              ThemeData(brightness: dark ? Brightness.dark : Brightness.light),
          home: Scaffold(
              body: HomeWeatherTile(
                  wide: true,
                  demo: true,
                  now: DateTime(2026, 10, 18, 9),
                  weather: WeatherSnapshot.demo(WeatherKind.rain)))));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.byType(WeatherSky), findsOneWidget);
      expect(tester.widget<WeatherSky>(find.byType(WeatherSky)).dark, dark);
      expect(tester.takeException(), isNull);
    }
    await tester.pumpWidget(const SizedBox());
  });

  test('CO2 display has five grades and does not invent missing readings', () {
    final grades = Co2Grades.fromJson(
        jsonDecode(File('assets/config/co2_bands.json').readAsStringSync())
            as Map);
    expect([500, 720, 900, 1400, 2200].map((v) => grades.forValue(v)?.label),
        ['매우 좋음', '좋음', '보통', '나쁨', '매우 나쁨']);
    expect(grades.forValue(null), isNull);
    expect(grades.forValue(0), isNull);
  });

  testWidgets(
      'home editor drags fixed widgets, cancels changes, and saves layout',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final store = _MemoryStore();
    final music = _Music();
    final bluetooth = _Bluetooth();
    final feedback = FeedbackCoordinator(
        music: music, lamp: IlinkLampFeedback(bluetooth), bluetooth: bluetooth);
    await tester.pumpWidget(MaterialApp(
        home: DeskmateWorkspace(
            state: null,
            demo: true,
            music: music,
            feedback: feedback,
            store: store,
            developerBuilder: (_) => const Text('Diagnostics'),
            onConnections: () {},
            onAccept: () {},
            onReject: () {},
            onUndo: () {})));
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-settings')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-settings')));
    await tester.pump();
    expect(find.byKey(const ValueKey('workspace-edit-home')), findsNothing);
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-home')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-home')));
    await tester.pump();
    await _editHome(tester);
    expect(find.byType(Draggable<String>), findsNWidgets(5));
    final clockSize =
        tester.getSize(find.byKey(const ValueKey('home-drop-clock')));
    final calendar =
        tester.getCenter(find.byKey(const ValueKey('home-drop-calendar')));
    final gesture = await tester.startGesture(
        tester.getCenter(find.byKey(const ValueKey('home-drop-clock'))));
    await gesture.moveBy(const Offset(18, 0));
    await tester.pump();
    await gesture.moveTo(calendar);
    await tester.pump(const Duration(milliseconds: 250));
    await gesture.up();
    await _settle(tester);
    expect(store.preferences.widgets.take(2), ['calendar', 'clock']);
    expect(tester.getSize(find.byKey(const ValueKey('home-drop-clock'))),
        clockSize);
    await tester.tap(find.text('취소'));
    await tester.pump();
    expect(store.preferences.widgets.take(2), ['clock', 'calendar']);
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-settings')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-settings')));
    await tester.pump();
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-home')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-home')));
    await tester.pump();
    await _editHome(tester);
    await tester.drag(
        find.byKey(const ValueKey('home-drop-music')),
        tester.getCenter(find.byKey(const ValueKey('home-drop-clock'))) -
            tester.getCenter(find.byKey(const ValueKey('home-drop-music'))));
    await _settle(tester);
    await tester.tap(find.text('완료'));
    await _settle(tester);
    expect(store.preferences.widgets.first, 'music');
    expect(find.byType(Draggable<String>), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await feedback.dispose();
  });

  testWidgets(
      'blank long press enters editor; clock and music resize, cancel and save',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final store = _MemoryStore();
    final music = _Music();
    final bluetooth = _Bluetooth();
    final feedback = FeedbackCoordinator(
        music: music, lamp: IlinkLampFeedback(bluetooth), bluetooth: bluetooth);
    await tester.pumpWidget(MaterialApp(
        home: DeskmateWorkspace(
            state: null,
            demo: true,
            music: music,
            feedback: feedback,
            store: store,
            developerBuilder: (_) => const Text('Diagnostics'),
            onConnections: () {},
            onAccept: () {},
            onReject: () {},
            onUndo: () {})));
    await tester.longPress(find.byType(StandbyClockTile));
    await tester.pump();
    expect(find.byType(Draggable<String>), findsNothing);
    await _editHome(tester);
    expect(find.byType(Draggable<String>), findsNWidgets(5));
    final wide = tester.getSize(find.byType(StandbyClockTile));
    final order = [...store.preferences.widgets];
    await tester.drag(
        find.byKey(const ValueKey('home-resize-clock')), const Offset(-80, 0));
    await _settle(tester);
    expect(find.byKey(const ValueKey('home-analog-clock')), findsOneWidget);
    expect(store.preferences.sizes['clock'], 1);
    expect(tester.getSize(find.byType(StandbyClockTile)).width,
        lessThan(wide.width * .6));
    await tester.drag(
        find.byKey(const ValueKey('home-resize-music')), const Offset(-70, 0));
    await _settle(tester);
    expect(store.preferences.sizes['music'], 1);
    expect(store.preferences.widgets, order);
    for (final type in [
      StandbyClockTile,
      MonthCalendarTile,
      EnvironmentTile,
      FocusCountdownTile,
      HomeMusicTile
    ]) {
      final rect = tester.getRect(find.byType(type));
      expect(rect.bottom, lessThanOrEqualTo(600));
      expect(rect.right, lessThanOrEqualTo(1024));
    }
    await tester.tap(find.text('취소'));
    await _settle(tester);
    expect(store.preferences.sizes, {'clock': 2, 'music': 2, 'weather': 1});
    await tester.longPress(find.byKey(const ValueKey('home-header-space')));
    await _settle(tester);
    for (final id in ['clock', 'music']) {
      await tester.drag(
          find.byKey(ValueKey('home-resize-$id')), const Offset(-80, 0));
      await _settle(tester);
    }
    await tester.tap(find.text('완료'));
    await _settle(tester);
    expect(store.preferences.sizes, {'clock': 1, 'music': 1, 'weather': 1});
    expect(find.byType(Draggable<String>), findsNothing);
    await tester.tap(find.byTooltip('재생'));
    await tester.pump();
    expect(music.playing, isTrue);
    await tester.tap(find.byType(HomeMusicTile));
    await _settle(tester);
    await tester.tap(find.byTooltip('즐겨찾기').first);
    await _settle(tester);
    expect(store.preferences.favoriteTracks, contains(music.selectedTrack));
    expect(find.byTooltip('즐겨찾기 해제'), findsWidgets);
    await tester.tap(find.byTooltip('볼륨').first);
    await _settle(tester);
    expect(find.text('볼륨'), findsOneWidget);
    final volumeBefore = music.volume;
    await tester.drag(find.byType(Slider).last, const Offset(-60, 0));
    await _settle(tester);
    expect(music.volume, lessThan(volumeBefore));
    await tester.tapAt(const Offset(5, 5));
    await _settle(tester);
    expect(find.text('플레이리스트'), findsOneWidget);
    await tester.tapAt(const Offset(5, 5));
    await _settle(tester);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await feedback.dispose();
  });

  testWidgets(
      'mascot celebrates completion, rests, and slumps only on giving up',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final store = _MemoryStore();
    final music = _Music();
    final bluetooth = _Bluetooth();
    final feedback = FeedbackCoordinator(
        music: music, lamp: IlinkLampFeedback(bluetooth), bluetooth: bluetooth);
    var now = DateTime(2026, 10, 18);
    await tester.pumpWidget(MaterialApp(
        home: DeskmateWorkspace(
            state: null,
            demo: true,
            now: () => now,
            music: music,
            feedback: feedback,
            store: store,
            developerBuilder: (_) => const Text('Diagnostics'),
            onConnections: () {},
            onAccept: () {},
            onReject: () {},
            onUndo: () {})));
    expect(tester.widget<IrumaeAnimation>(find.byType(IrumaeAnimation)).mood,
        IrumaeMood.working);
    now = now.add(const Duration(minutes: 25));
    await tester.pump(const Duration(seconds: 1));
    await _settle(tester);
    expect(find.byKey(const ValueKey('focus-report-dialog')), findsOneWidget);
    expect(store.preferences.focusReports.length, 1);
    await tester.tap(find.byKey(const ValueKey('focus-report-today')));
    await _settle(tester);
    expect(find.byKey(const ValueKey('focus-history-dialog')), findsOneWidget);
    await tester.tap(find.byType(ListTile).last);
    await _settle(tester);
    expect(find.byKey(const ValueKey('focus-report-dialog')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('focus-report-close')));
    await _settle(tester);
    expect(find.text('오늘 25 min · 1회 완료'), findsOneWidget);
    expect(tester.widget<IrumaeAnimation>(find.byType(IrumaeAnimation)).mood,
        IrumaeMood.celebrating);
    now = now.add(const Duration(seconds: 13));
    await tester.pump(const Duration(seconds: 1));
    expect(store.preferences.focusReports.length, 1);
    expect(tester.widget<IrumaeAnimation>(find.byType(IrumaeAnimation)).mood,
        IrumaeMood.greeting);
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-focus')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-focus')));
    await _settle(tester);
    await tester
        .ensureVisible(find.byKey(const ValueKey('focus-preview-report')));
    await tester.tap(find.byKey(const ValueKey('focus-preview-report')));
    await _settle(tester);
    expect(find.text('집중 리포트 미리보기'), findsOneWidget);
    expect(store.preferences.focusReports.length, 1);
    await tester.tap(find.byKey(const ValueKey('focus-report-close')));
    await _settle(tester);
    await tester.ensureVisible(find.text('쉬기'));
    await tester.tap(find.text('쉬기'));
    await tester.pump();
    expect(tester.widget<IrumaeAnimation>(find.byType(IrumaeAnimation)).mood,
        IrumaeMood.sleeping);
    await tester.tap(find.text('이번 집중 종료'));
    await tester.pump();
    expect(tester.widget<IrumaeAnimation>(find.byType(IrumaeAnimation)).mood,
        IrumaeMood.slumped);
    await tester.pumpWidget(const SizedBox());
    await feedback.dispose();
  });
  testWidgets(
      'completed automatic control closes after eight seconds and stays in history',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final store = _MemoryStore();
    store.preferences.automatic = true;
    final music = _Music();
    final bluetooth = _RecordBluetooth();
    final feedback = FeedbackCoordinator(
        music: music,
        lamp: IlinkLampFeedback(bluetooth)..select('AA:BB:CC:DD:EE:01'),
        bluetooth: bluetooth);
    await tester.pumpWidget(MaterialApp(
        home: DeskmateWorkspace(
            state: null,
            demo: true,
            music: music,
            feedback: feedback,
            store: store,
            developerBuilder: (_) => const Text('Diagnostics'),
            onConnections: () {},
            onAccept: () {},
            onReject: () {},
            onUndo: () {})));
    await feedback.handleControlCommand({
      'command_id': 'popup',
      'target_id': 'desk_lamp',
      'operation': 'set_brightness',
      'value': 160,
      'gate': 'auto',
      'requires_confirmation': false,
      'expires_ts_ms':
          DateTime.now().add(const Duration(minutes: 1)).millisecondsSinceEpoch
    });
    await tester.pump();
    expect(find.text('자동 제어 완료'), findsOneWidget);
    await tester.pump(const Duration(seconds: 9));
    expect(find.text('자동 제어 완료'), findsNothing);
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-devices')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-devices')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('workspace-history')));
    await _settle(tester);
    expect(find.text('책상 조명을 조절했어요'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await feedback.dispose();
  });
  test(
      'calendar and automatic permissions survive restart without changing connection settings',
      () {
    final dir = Directory.systemTemp.createTempSync('deskmate-ui-');
    addTearDown(() => dir.deleteSync(recursive: true));
    final store = WorkspaceStore(paths: ['${dir.path}/workspace.json']);
    final prefs = WorkspacePreferences()
      ..appearance = 'dark'
      ..automatic = true
      ..lampAutomatic = false
      ..speakerAutomatic = true
      ..sizes = {'clock': 1, 'music': 1}
      ..events = [
        DeskEvent(
            id: 'e',
            title: '시연',
            date: DateTime(2026, 10, 21),
            hour: 14,
            minute: 30,
            endHour: 16,
            endMinute: 0,
            location: '공학관 201호')
      ];
    expect(store.save(prefs), isTrue);
    final loaded = store.load();
    expect(loaded.appearance, 'dark');
    expect(loaded.events.single.title, '시연');
    expect(loaded.events.single.endMinutes, 16 * 60);
    expect(loaded.events.single.location, '공학관 201호');
    expect(loaded.lampAutomatic, isFalse);
    expect(loaded.speakerAutomatic, isTrue);
    expect(loaded.sizes, {'clock': 1, 'music': 1, 'weather': 1});
  });

  for (final size in [
    const Size(1024, 600),
    const Size(800, 600),
    const Size(320, 800)
  ]) {
    for (final compact in [false, true]) {
      testWidgets(
          'daily pages fit $size compact=$compact; permissions exist only in settings',
          (tester) async {
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.reset);
        final store = _MemoryStore();
        if (compact) store.preferences.sizes = {'clock': 1, 'music': 1};
        final music = _Music()..track = compact ? 4 : 0;
        final bluetooth = _Bluetooth();
        final feedback = FeedbackCoordinator(
            music: music,
            lamp: IlinkLampFeedback(bluetooth),
            bluetooth: bluetooth);
        await tester.pumpWidget(MaterialApp(
            home: DeskmateWorkspace(
                state: null,
                demo: true,
                music: music,
                feedback: feedback,
                store: store,
                developerBuilder: (_) => const Text('Diagnostics'),
                onConnections: () {},
                onAccept: () {},
                onReject: () {},
                onUndo: () {})));
        await tester.pump();
        expect(find.byKey(const ValueKey('workspace-clock')), findsOneWidget);
        expect(tester.takeException(), isNull);
        await tester
            .ensureVisible(find.byKey(const ValueKey('workspace-nav-devices')));
        await tester.tap(find.byKey(const ValueKey('workspace-nav-devices')));
        await tester.pump();
        expect(
            find.byKey(const ValueKey('workspace-auto-master')), findsNothing);
        expect(tester.takeException(), isNull);
        await tester.tap(find.byKey(const ValueKey('workspace-history')));
        await _settle(tester);
        expect(find.text('아직 자동 제어 내역이 없어요.'), findsOneWidget);
        await tester.tap(find.byTooltip('닫기'));
        await _settle(tester);
        await tester.ensureVisible(
            find.byKey(const ValueKey('workspace-nav-settings')));
        await tester.tap(find.byKey(const ValueKey('workspace-nav-settings')));
        await tester.pump();
        await tester
            .ensureVisible(find.byKey(const ValueKey('workspace-auto-master')));
        final master = tester.widget<SwitchListTile>(
            find.byKey(const ValueKey('workspace-auto-master')));
        master.onChanged!(true);
        await tester.pump();
        final lamp = tester.widget<SwitchListTile>(
            find.byKey(const ValueKey('workspace-auto-lamp')));
        lamp.onChanged!(false);
        await tester.pump();
        expect(feedback.enabled, isTrue);
        expect(feedback.lampAutomatic, isFalse);
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox());
        await feedback.dispose();
      });
    }
  }

  testWidgets(
      'calendar page moves months, clamps selected dates and returns home',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final music = _Music();
    final bluetooth = _Bluetooth();
    final feedback = FeedbackCoordinator(
        music: music, lamp: IlinkLampFeedback(bluetooth), bluetooth: bluetooth);
    await tester.pumpWidget(MaterialApp(
        home: DeskmateWorkspace(
            state: null,
            demo: true,
            now: () => DateTime(2026, 12, 31),
            music: music,
            feedback: feedback,
            store: _MemoryStore(),
            developerBuilder: (_) => const Text('Diagnostics'),
            onConnections: () {},
            onAccept: () {},
            onReject: () {},
            onUndo: () {})));
    await tester.tap(find.byType(MonthCalendarTile));
    await _settle(tester);
    expect(find.byType(DeskCalendarPanel), findsOneWidget);
    expect(find.byType(Dialog), findsNothing);
    expect(find.text('오늘'), findsNothing);
    expect(find.textContaining('D-day'), findsNothing);
    await tester.tap(find.byTooltip('다음 달'));
    await _settle(tester);
    expect(find.text('2027년 1월'), findsOneWidget);
    await tester.tap(find.byTooltip('다음 달'));
    await _settle(tester);
    expect(find.text('2027년 2월'), findsOneWidget);
    expect(find.text('2월 28일 일요일'), findsOneWidget);
    await tester.tap(find.byTooltip('이전 달'));
    await _settle(tester);
    expect(find.text('2027년 1월'), findsOneWidget);
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-home')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-home')));
    await _settle(tester);
    expect(find.byType(Dialog), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await feedback.dispose();
  });

  testWidgets(
      'events store start, end and place; timeline sorts and home counts; edit and delete work',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final store = _MemoryStore();
    store.preferences.events = [
      DeskEvent(
          id: 'late',
          title: '팀 회의',
          date: DateTime(2026, 10, 18),
          hour: 15,
          minute: 0,
          endHour: 16,
          endMinute: 0),
      DeskEvent(
          id: 'early',
          title: '자료 검토',
          date: DateTime(2026, 10, 18),
          hour: 9,
          minute: 0,
          endHour: 10,
          endMinute: 0),
    ];
    final music = _Music();
    final bluetooth = _Bluetooth();
    final feedback = FeedbackCoordinator(
        music: music, lamp: IlinkLampFeedback(bluetooth), bluetooth: bluetooth);
    await tester.pumpWidget(MaterialApp(
        home: DeskmateWorkspace(
            state: null,
            demo: true,
            now: () => DateTime(2026, 10, 18),
            music: music,
            feedback: feedback,
            store: store,
            developerBuilder: (_) => const Text('Diagnostics'),
            onConnections: () {},
            onAccept: () {},
            onReject: () {},
            onUndo: () {})));
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-calendar')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-calendar')));
    await _settle(tester);
    await tester.tap(find.byKey(const ValueKey('workspace-add-event')));
    await _settle(tester);
    await tester.tap(find.byKey(const ValueKey('workspace-event-save')));
    await _settle(tester);
    expect(find.text('일정 이름을 입력해주세요.'), findsOneWidget);
    await tester.enterText(
        find.byKey(const ValueKey('workspace-event-title')), '작품 시연');
    await tester.enterText(
        find.byKey(const ValueKey('workspace-event-location')), '공학관 201호');
    Future<void> setTime(String field, String hour, String minute) async {
      await tester
          .ensureVisible(find.byKey(ValueKey('workspace-event-$field')));
      await tester.tap(find.byKey(ValueKey('workspace-event-$field')));
      await _settle(tester);
      final inputs = find.descendant(
          of: find.byType(TimePickerDialog), matching: find.byType(TextField));
      await tester.enterText(inputs.at(0), hour);
      await tester.enterText(inputs.at(1), minute);
      final label = MaterialLocalizations.of(
              tester.element(find.byType(TimePickerDialog)))
          .okButtonLabel;
      await tester.tap(find.text(label));
      await _settle(tester);
    }

    await setTime('end', '13', '00');
    await tester.tap(find.byKey(const ValueKey('workspace-event-save')));
    await _settle(tester);
    expect(find.text('끝 시간은 시작 시간보다 늦게 정해주세요.'), findsOneWidget);
    await setTime('start', '13', '30');
    await setTime('end', '14', '45');
    await tester.tap(find.byKey(const ValueKey('workspace-event-save')));
    await _settle(tester);
    final saved =
        store.preferences.events.singleWhere((e) => e.title == '작품 시연');
    expect(saved.startMinutes, 13 * 60 + 30);
    expect(saved.endMinutes, 14 * 60 + 45);
    expect(saved.location, '공학관 201호');
    final newItem = find.byKey(ValueKey('calendar-timeline-${saved.id}'));
    final early = tester
        .getTopLeft(find.byKey(const ValueKey('calendar-timeline-early')))
        .dy;
    final middle = tester.getTopLeft(newItem).dy;
    final late = tester
        .getTopLeft(find.byKey(const ValueKey('calendar-timeline-late')))
        .dy;
    expect(early, lessThan(middle));
    expect(middle, lessThan(late));
    expect(find.text('작품 시연'), findsNWidgets(2));
    expect(find.textContaining('D-day'), findsNothing);
    await tester
        .ensureVisible(find.byKey(const ValueKey('workspace-nav-home')));
    await tester.tap(find.byKey(const ValueKey('workspace-nav-home')));
    await _settle(tester);
    final dots = tester
        .widget<Row>(find.byKey(const ValueKey('home-calendar-events-18')));
    expect(dots.children.length, 3);
    expect(find.text('작품 시연'), findsNothing);
    await tester.tap(find.byType(MonthCalendarTile));
    await _settle(tester);
    final edit =
        find.descendant(of: newItem, matching: find.byTooltip('일정 수정'));
    await tester.ensureVisible(edit);
    await tester.tap(edit);
    await _settle(tester);
    expect(
        find.descendant(
            of: find.byKey(const ValueKey('workspace-event-end')),
            matching: find.text('14:45')),
        findsOneWidget);
    await tester.enterText(
        find.byKey(const ValueKey('workspace-event-location')), '중앙도서관');
    await tester.tap(find.byKey(const ValueKey('workspace-event-save')));
    await _settle(tester);
    expect(
        store.preferences.events.singleWhere((e) => e.id == saved.id).location,
        '중앙도서관');
    final delete =
        find.descendant(of: newItem, matching: find.byTooltip('일정 삭제'));
    await tester.ensureVisible(delete);
    await tester.tap(delete);
    await _settle(tester);
    expect(store.preferences.events.length, 2);
    expect(find.text('작품 시연'), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await feedback.dispose();
  });
}

// The mascot intentionally animates continuously; settle UI transitions with a
// bounded interval rather than waiting for every animation in the app to stop.
Future<void> _settle(WidgetTester tester) async {
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 700));
  await tester.pump();
}

Future<void> _editHome(WidgetTester tester) async {
  final empty =
      tester.getTopLeft(find.byKey(const ValueKey('home-edit-space'))) +
          const Offset(8, 40);
  await tester.longPressAt(empty);
  await _settle(tester);
}

class _MemoryStore extends WorkspaceStore {
  final preferences = WorkspacePreferences();
  @override
  WorkspacePreferences load() => preferences;
  @override
  bool save(WorkspacePreferences preferences) => true;
}

class _Music implements MusicPlayback, MusicQueue {
  List<int> _queue = List.generate(classicalPlaylist.length, (i) => i);
  @override
  List<int> get queue => _queue;
  @override
  void setQueue(List<int> tracks) => _queue = [...tracks];
  bool playing = false;
  double level = .3;
  int track = 0;
  @override
  bool get isPlaying => playing;
  @override
  int get selectedTrack => track;
  @override
  double get volume => level;
  @override
  Stream<bool> get playingChanges => const Stream.empty();
  @override
  Stream<double> get volumeChanges => const Stream.empty();
  @override
  Future<bool> toggle() async => playing = !playing;
  @override
  Future<void> selectTrack(int index) async {
    track = index;
  }

  @override
  Future<void> setVolume(double value) async {
    level = value;
  }

  @override
  Future<void> dispose() async {}
}

class _Bluetooth implements AtlasBluetoothService {
  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnimplementedError();
}

class _RecordBluetooth extends _Bluetooth {
  final frames = <List<int>>[];
  @override
  Future<GattConnection> connectLamp(String address) async =>
      GattConnection(adapter: 'test', clientId: 'test');
  @override
  Future<void> writeCharacteristic(GattConnection connection,
      {required String serviceId,
      required String characteristicId,
      required List<int> value}) async {
    frames.add([...value]);
  }
}
