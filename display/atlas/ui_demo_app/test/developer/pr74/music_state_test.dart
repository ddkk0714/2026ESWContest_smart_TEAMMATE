import 'dart:async';

import 'package:deskmate_ui_demo/developer/pr74/music_playback.dart';
import 'package:deskmate_ui_demo/developer/pr74/music_state.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('state carries on, rounded volume, track and file id only', () {
    final music = _Music()
      ..playing = true
      ..vol = .4567
      ..track = 3;
    expect(musicStateOf(music), {
      'on': true,
      'volume': .46,
      'track': 3,
      'track_id': 'beethoven_fur_elise',
    });
    expect(trackIdOf(-1), '');
    expect(trackIdOf(classicalPlaylist.length), '');
  });

  test('reports once at start, settles slider bursts, skips repeats',
      () async {
    final music = _Music()..vol = .2;
    final sent = <Map<String, Object?>>[];
    final reporter =
        MusicStateReporter(music, sent.add, settle: const Duration(milliseconds: 20));
    addTearDown(reporter.dispose);
    expect(sent, hasLength(1));
    expect(sent.single['volume'], .2);
    for (final v in [.3, .4, .5]) {
      music
        ..vol = v
        ..volumes.add(v);
    }
    await Future<void>.delayed(const Duration(milliseconds: 60));
    expect(sent, hasLength(2)); // 세 번 움직였어도 한 번, 마지막 값
    expect(sent.last['volume'], .5);
    reporter.report(); // 바뀐 게 없으면 보내지 않는다
    expect(sent, hasLength(2));
    music
      ..playing = true
      ..plays.add(true);
    await Future<void>.delayed(const Duration(milliseconds: 60));
    expect(sent, hasLength(3));
    expect(sent.last['on'], isTrue);
    music.track = 4; // 곡만 바뀐 것은 스트림이 없다 — report() 가 챙긴다
    reporter.report();
    expect(sent.last['track_id'], 'mozart_sonata_k545_allegro');
  });
}

class _Music implements MusicPlayback {
  bool playing = false;
  double vol = 1;
  int track = 0;
  final plays = StreamController<bool>.broadcast(sync: true);
  final volumes = StreamController<double>.broadcast(sync: true);

  @override
  bool get isPlaying => playing;
  @override
  double get volume => vol;
  @override
  int get selectedTrack => track;
  @override
  Stream<bool> get playingChanges => plays.stream;
  @override
  Stream<double> get volumeChanges => volumes.stream;
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} was not expected');
}
