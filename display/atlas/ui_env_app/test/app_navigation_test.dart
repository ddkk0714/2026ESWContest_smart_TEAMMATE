import 'dart:async';

import 'package:deskmate_display/main.dart';
import 'package:deskmate_display/music_playback.dart';
import 'package:deskmate_display/settings_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  _connectionTabTest();
  testWidgets('music selector offers all tracks and preserves OFF',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final music = _FakeMusicPlayback();
    await tester.pumpWidget(DeskmateApp(music: music));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('music-select')));
    await tester.pumpAndSettle();
    for (final title in classicalTrackTitles) {
      expect(find.text(title), findsWidgets);
    }
    await tester.tap(find.byKey(const ValueKey('music-track-1')));
    await tester.pumpAndSettle();
    expect(music.selectedTrack, 1);
    expect(find.text(classicalTrackTitles[1]), findsOneWidget);
    expect(find.text('OFF'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('music button toggles the bundled playlist on and off',
      (tester) async {
    final music = _FakeMusicPlayback();
    await tester.pumpWidget(DeskmateApp(music: music));
    await tester.pump();

    expect(find.byKey(const ValueKey('music-toggle')), findsOneWidget);
    expect(find.text('OFF'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('music-toggle')));
    await tester.pump();
    expect(music.isPlaying, isTrue);
    expect(find.text('ON'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('music-toggle')));
    await tester.pump();
    expect(music.isPlaying, isFalse);
    expect(find.text('OFF'), findsOneWidget);
  });

  testWidgets('volume button opens a slider and updates playback volume',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final music = _FakeMusicPlayback();
    await tester.pumpWidget(DeskmateApp(music: music));
    await tester.pump();

    await tester.tap(find.byKey(const ValueKey('music-volume')));
    await tester.pumpAndSettle();
    expect(find.text('음량 조절'), findsOneWidget);
    expect(find.text('100%'), findsOneWidget);

    final slider = tester.widget<Slider>(
      find.byKey(const ValueKey('music-volume-slider')),
    );
    slider.onChanged!(.35);
    await tester.pump();
    expect(music.volume, .35);
    expect(find.text('35%'), findsOneWidget);

    music.setExternalVolume(.7);
    await tester.pump();
    expect(find.text('70%'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('header exposes exit confirmation and the full FSM graph',
      (tester) async {
    await tester.pumpWidget(const DeskmateApp());
    await tester.pump();

    expect(find.byKey(const ValueKey('app-exit')), findsOneWidget);
    await tester.tap(find.byTooltip('FSM 전체'));
    await tester.pump();
    expect(find.byKey(const ValueKey('fsm-full-graph')), findsOneWidget);
    expect(find.textContaining('현재 START'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('app-exit')));
    await tester.pumpAndSettle();
    expect(find.text('DESKMATE 종료'), findsOneWidget);
    expect(find.text('앱을 종료할까요?'), findsOneWidget);
    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();
  });

  testWidgets('header exposes the sensor overview without hiding other views',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const DeskmateApp());
    await tester.pump();

    expect(find.byTooltip('상태'), findsOneWidget);
    expect(find.byTooltip('센서 전체'), findsOneWidget);
    expect(find.byTooltip('개발자'), findsOneWidget);
    expect(find.byTooltip('FSM 전체'), findsOneWidget);

    await tester.tap(find.byTooltip('센서 전체'));
    await tester.pumpAndSettle();
    expect(find.text('실시간 환경센서 데이터'), findsOneWidget);

    await tester.tap(find.byTooltip('상태'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('demo-cycle-toggle')), findsOneWidget);
  });

  testWidgets('개발자 탭에서 버튼으로 화면을 바꾸면 상태 탭도 그 화면이 된다', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const DeskmateApp());
    await tester.pump();

    await tester.tap(find.byTooltip('개발자'));
    await tester.pumpAndSettle();
    expect(find.text('개발자 화면'), findsOneWidget);
    // 예전 Pi 4 HTTP 연결 폼은 없다
    expect(find.byKey(const ValueKey('hub-url-input')), findsNothing);

    final devList = find.ancestor(
        of: find.text('국면 화면'), matching: find.byType(Scrollable));
    await tester.scrollUntilVisible(
        find.byKey(const ValueKey('dev-suggest-env')), 120,
        scrollable: devList.first);
    await tester.tap(find.byKey(const ValueKey('dev-suggest-env')));
    await tester.pumpAndSettle();
    expect(find.textContaining('ACTION_ENV · gate suggest'), findsOneWidget);
    expect(find.text('적용할게요'), findsOneWidget); // 미리보기 안의 제안 카드

    await tester.tap(find.byTooltip('상태'));
    await tester.pumpAndSettle();
    expect(find.text('적용할게요'), findsOneWidget);
    expect(find.textContaining('뒤 닫혀요'), findsOneWidget);
  });

  testWidgets('연결 배지는 5초 뒤 사라진다', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(const DeskmateApp());
    await tester.pump();

    // 붙었다는 것은 한 번은 보여 줘야 한다.
    expect(find.text('내장 데모'), findsOneWidget);

    await tester.pump(const Duration(seconds: 6));
    await tester.pump();

    // 그 뒤로는 상단을 차지하지 않는다. 시퀀스 번호는 그대로 남는다.
    expect(find.text('내장 데모'), findsNothing);
    expect(find.textContaining('#'), findsWidgets);
  });
}

void _connectionTabTest() {
  testWidgets('connection tab lists every link and demo raises no badge',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(DeskmateApp(
      music: _FakeMusicPlayback(),
      settings: SettingsStore(
          candidates: const [], legacyPostureCandidates: const []),
    ));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1)); // 연결 스냅샷 1회 갱신

    expect(find.byKey(const ValueKey('link-problem-badge')), findsNothing);
    expect(find.byKey(const ValueKey('link-status-bar')), findsNothing);

    await tester.tap(find.byTooltip('연결'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('link-row-mqtt')), findsOneWidget);
    expect(find.byKey(const ValueKey('link-health-mqtt')), findsOneWidget);
    expect(find.text('화면 내장 데모'), findsOneWidget);
    // 목록이 600 px 화면보다 길다. 끝까지 내려 마지막 행(램프)까지 그려지는지 본다.
    await tester.dragUntilVisible(find.byKey(const ValueKey('link-row-lamp')),
        find.byType(ListView), const Offset(0, -200));
    expect(find.byKey(const ValueKey('link-row-lamp')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}

class _FakeMusicPlayback implements MusicPlayback {
  bool _playing = false;
  int _selectedTrack = 0;
  double _volume = 1;
  final _volumeChanges = StreamController<double>.broadcast(sync: true);

  @override
  int get selectedTrack => _selectedTrack;

  @override
  double get volume => _volume;

  @override
  Stream<double> get volumeChanges => _volumeChanges.stream;

  @override
  Future<void> selectTrack(int index) async => _selectedTrack = index;

  @override
  Future<void> setVolume(double value) async {
    _volume = value;
    _volumeChanges.add(value);
  }

  void setExternalVolume(double value) {
    _volume = value;
    _volumeChanges.add(value);
  }

  @override
  bool get isPlaying => _playing;

  @override
  Stream<bool> get playingChanges => const Stream<bool>.empty();

  @override
  Future<bool> toggle() async {
    _playing = !_playing;
    return _playing;
  }

  @override
  Future<void> dispose() => _volumeChanges.close();
}
