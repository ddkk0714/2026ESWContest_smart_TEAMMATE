import 'package:deskmate_display/main.dart';
import 'package:deskmate_display/music_playback.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('music selector offers three tracks and preserves OFF',
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
}

class _FakeMusicPlayback implements MusicPlayback {
  bool _playing = false;
  int _selectedTrack = 0;

  @override
  int get selectedTrack => _selectedTrack;

  @override
  Future<void> selectTrack(int index) async => _selectedTrack = index;

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
  Future<void> dispose() async {}
}
