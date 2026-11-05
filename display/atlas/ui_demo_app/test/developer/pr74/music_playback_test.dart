import 'dart:async';

import 'package:deskmate_ui_demo/developer/pr74/music_playback.dart';
import 'package:flutter_test/flutter_test.dart';

/// 재생한 음원을 적어 두고, 곡 끝을 시험에서 알린다.
class _Player implements MusicTrackPlayer {
  _Player(this.log);
  final List<String> log;
  final _done = StreamController<void>.broadcast();
  void finish() => _done.add(null);

  @override
  Stream<void> get completed => _done.stream;
  @override
  Future<void> play(String asset, {required double volume}) async =>
      log.add(asset);
  @override
  Future<void> pause() async {}
  @override
  Future<void> resume() async {}
  @override
  Future<void> setVolume(double value) async {}
  @override
  Future<void> dispose() => _done.close();
}

/// 플레이어에 건 소리 크기를 적는다.
class _GainPlayer extends _Player {
  _GainPlayer(this.volumes) : super([]);
  final List<double> volumes;
  @override
  Future<void> play(String asset, {required double volume}) async =>
      volumes.add(volume);
  @override
  Future<void> setVolume(double value) async => volumes.add(value);
}

void main() {
  test('V55: sound choices map to bundled tracks', () {
    expect(classicalPlaylist[musicGroups['카페 음악']!.single],
        'audio/cozy_coffee_jazz.mp3');
    expect(classicalPlaylist[musicGroups['백색 소음']!.single],
        'audio/river_birds.mp3');
    expect(classicalTrackTitles.length, classicalPlaylist.length);
  });

  test('V57: gain lowers only this player, volume stays', () async {
    final volumes = <double>[];
    final music = AtlasMusicPlayback(playerFactory: () => _GainPlayer(volumes));
    await music.setGain(.45);
    await music.toggle();
    expect(volumes.last, closeTo(.45, 1e-9)); // 볼륨 1 × 배율 .45
    await music.setGain(1);
    expect(volumes.last, 1);
    expect(music.volume, 1);
    await music.dispose();
  });

  test('V55: a one-track sound repeats, classical still goes round', () async {
    final log = <String>[];
    late _Player current;
    final music =
        AtlasMusicPlayback(playerFactory: () => current = _Player(log));
    await music.selectTrack(7);
    await music.toggle();
    current.finish();
    await pumpEventQueue();
    expect(log, ['audio/river_birds.mp3', 'audio/river_birds.mp3']);
    expect(music.selectedTrack, 7);

    await music.selectTrack(5); // 바흐 → 다음은 드뷔시(카페 재즈가 아니다)
    log.clear();
    current.finish();
    await pumpEventQueue();
    expect(music.selectedTrack, 0);
    expect(log.last, 'audio/deskmate_test_music.mp3');
    await music.dispose();
  });
}
