import 'dart:async';

import 'package:deskmate_display/main.dart';
import 'package:deskmate_display/music_playback.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
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

  testWidgets('sensor test explains that the real Hub connection is required',
      (tester) async {
    await tester.pumpWidget(const DeskmateApp());
    await tester.pump();

    await tester.tap(find.byTooltip('센서 테스트'));
    await tester.pump();

    expect(find.textContaining('Pi 4의 실제 FSMEngine'), findsOneWidget);
  });

  // 자세·센서 전체는 예전에 각각 별도 앱(camtest·ui_env_app)이었다.
  // 한 앱으로 합친 뒤에도 헤더에서 바로 갈 수 있어야 한다.
  testWidgets('every merged screen is reachable from the header',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const DeskmateApp());
    await tester.pump();

    await tester.tap(find.byTooltip('센서 전체'));
    await tester.pump();
    expect(find.text('센서 전체'), findsOneWidget);
    expect(find.text('생체 · mmWave'), findsOneWidget);

    await tester.tap(find.byTooltip('자세'));
    await tester.pump();
    expect(find.text('DESKMATE · 자세'), findsOneWidget);

    await tester.tap(find.byTooltip('세션 리포트'));
    await tester.pump();
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
  Future<bool> play() async {
    _playing = true;
    return true;
  }

  @override
  Future<void> pause() async => _playing = false;

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
