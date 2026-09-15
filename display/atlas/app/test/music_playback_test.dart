import 'dart:async';

import 'package:deskmate_display/music_playback.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('all playlist assets and credits are bundled', () async {
    for (final asset in classicalPlaylist) {
      final bytes = await rootBundle.load('assets/$asset');
      expect(bytes.lengthInBytes, greaterThan(1000));
    }
    expect(await rootBundle.loadString('assets/audio/CREDITS.md'),
        contains('CC BY-SA 2.0'));
  });

  test('completion advances through all tracks and wraps to Debussy', () async {
    final player = _Player();
    final music = AtlasMusicPlayback(playerFactory: player.create);
    addTearDown(music.dispose);

    expect(await music.toggle(), isTrue);
    for (var i = 0; i < classicalPlaylist.length; i++) {
      player.finish();
      await _flush();
    }
    expect(player.played, [...classicalPlaylist, classicalPlaylist.first]);
    expect(music.isPlaying, isTrue);
  });

  test('OFF pauses and ON resumes without restarting the track', () async {
    final player = _Player();
    final music = AtlasMusicPlayback(playerFactory: player.create);
    addTearDown(music.dispose);
    await music.toggle();
    expect(await music.toggle(), isFalse);
    expect(await music.toggle(), isTrue);
    expect(player.played, [classicalPlaylist.first]);
    expect(player.pauses, 1);
    expect(player.resumes, 1);
  });

  test('OFF during an in-flight track switch pauses the new track', () async {
    final player = _Player();
    final music = AtlasMusicPlayback(playerFactory: player.create);
    addTearDown(music.dispose);
    await music.toggle();
    final gate = Completer<void>();
    player.nextPlay = gate.future;
    player.finish();
    await _flush();
    final pause = music.toggle();
    gate.complete();
    expect(await pause, isFalse);
    expect(player.played, classicalPlaylist.take(2).toList());
    expect(player.audible, isFalse);
  });

  test('completion arriving after OFF advances without starting playback',
      () async {
    final player = _Player();
    final music = AtlasMusicPlayback(playerFactory: player.create);
    addTearDown(music.dispose);
    await music.toggle();
    await music.toggle();
    player.finish();
    await _flush();
    expect(player.played, [classicalPlaylist.first]);
    expect(music.isPlaying, isFalse);
    await music.toggle();
    expect(player.played.last, classicalPlaylist[1]);
  });

  test('failed automatic switch reports OFF and can retry that track',
      () async {
    final player = _Player();
    final music = AtlasMusicPlayback(playerFactory: player.create);
    final states = <bool>[];
    final errors = <Object>[];
    final subscription = music.playingChanges
        .listen(states.add, onError: (Object error) => errors.add(error));
    addTearDown(() async {
      await subscription.cancel();
      await music.dispose();
    });
    await music.toggle();
    player.failNext = true;
    player.finish();
    await _flush();
    expect(music.isPlaying, isFalse);
    expect(states, [true, false]);
    expect(errors, hasLength(1));
    expect(await music.toggle(), isTrue);
    expect(player.played.last, classicalPlaylist[1]);
  });

  test('dispose cancels completion handling', () async {
    final player = _Player();
    final music = AtlasMusicPlayback(playerFactory: player.create);
    await music.toggle();
    await music.dispose();
    expect(player.disposed, isTrue);
    expect(player.events.hasListener, isFalse);
  });

  test('select while OFF stays silent and starts the selected track on ON',
      () async {
    final player = _Player();
    final music = AtlasMusicPlayback(playerFactory: player.create);
    addTearDown(music.dispose);
    await music.selectTrack(2);
    expect(music.selectedTrack, 2);
    expect(player.played, isEmpty);
    expect(music.isPlaying, isFalse);
    await music.toggle();
    expect(player.played, [classicalPlaylist[2]]);
  });

  test('select while ON replaces the native player and cycles from selection',
      () async {
    final player = _Player();
    final music = AtlasMusicPlayback(playerFactory: player.create);
    addTearDown(music.dispose);
    await music.toggle();
    await music.selectTrack(2);
    expect(music.selectedTrack, 2);
    expect(player.played, [classicalPlaylist[0], classicalPlaylist[2]]);
    expect(music.isPlaying, isTrue);
    player.finish();
    await _flush();
    expect(music.selectedTrack, 3);
    expect(player.played.last, classicalPlaylist[3]);
  });

  test('invalid selection leaves playback unchanged', () async {
    final player = _Player();
    final music = AtlasMusicPlayback(playerFactory: player.create);
    addTearDown(music.dispose);
    await music.toggle();
    await expectLater(
        music.selectTrack(classicalPlaylist.length), throwsRangeError);
    expect(music.selectedTrack, 0);
    expect(music.isPlaying, isTrue);
  });

  test('volume is applied to the first track and retained across switches',
      () async {
    final player = _Player();
    final music = AtlasMusicPlayback(playerFactory: player.create);
    addTearDown(music.dispose);

    await music.setVolume(.35);
    expect(music.volume, .35);
    expect(player.setVolumes, isEmpty);

    await music.toggle();
    expect(player.playedVolumes, [.35]);

    await music.setVolume(.6);
    expect(player.setVolumes, [.6]);
    await music.selectTrack(2);
    expect(player.playedVolumes, [.35, .6]);
    expect(music.volume, .6);
  });

  test('invalid volume leaves the current volume unchanged', () async {
    final music = AtlasMusicPlayback(playerFactory: _Player().create);
    addTearDown(music.dispose);

    await expectLater(music.setVolume(1.1), throwsRangeError);
    await expectLater(music.setVolume(double.nan), throwsRangeError);
    expect(music.volume, 1);
  });

  test('external output changes update the exposed volume', () async {
    final output = _OutputVolume(.4);
    final music = AtlasMusicPlayback(
      playerFactory: _Player().create,
      outputVolume: output,
    );
    addTearDown(music.dispose);
    final changes = <double>[];
    final subscription = music.volumeChanges.listen(changes.add);
    addTearDown(subscription.cancel);

    await _flush();
    expect(music.volume, .4);
    output.setExternal(.25);
    await _flush();
    expect(music.volume, .25);
    expect(changes, [.4, .25]);
  });
}

Future<void> _flush() => Future<void>.delayed(Duration.zero);

class _OutputVolume implements MusicOutputVolume {
  _OutputVolume(this.value);

  double value;
  final controller = StreamController<double>.broadcast(sync: true);

  @override
  Stream<double> get changes => controller.stream;

  @override
  Future<double> initialize() async => value;

  @override
  Future<void> setVolume(double value) async {
    this.value = value;
    controller.add(value);
  }

  void setExternal(double value) {
    this.value = value;
    controller.add(value);
  }

  @override
  Future<void> dispose() => controller.close();
}

class _Player implements MusicTrackPlayer {
  var events = StreamController<void>.broadcast(sync: true);
  bool _created = false;

  MusicTrackPlayer create() {
    if (_created) {
      if (!disposed) throw StateError("Previous native player still loaded");
      events = StreamController<void>.broadcast(sync: true);
    }
    _created = true;
    disposed = false;
    return _SingleLoadPlayer(this);
  }

  final played = <String>[];
  final playedVolumes = <double>[];
  final setVolumes = <double>[];
  int pauses = 0;
  int resumes = 0;
  bool audible = false;
  bool disposed = false;
  bool failNext = false;
  Future<void>? nextPlay;

  void finish() {
    audible = false;
    events.add(null);
  }

  @override
  Stream<void> get completed => events.stream;

  @override
  Future<void> play(String asset, {required double volume}) async {
    if (failNext) {
      failNext = false;
      throw StateError('test load failure');
    }
    played.add(asset);
    playedVolumes.add(volume);
    final pending = nextPlay;
    nextPlay = null;
    if (pending != null) await pending;
    audible = true;
  }

  @override
  Future<void> pause() async {
    pauses++;
    audible = false;
  }

  @override
  Future<void> resume() async {
    resumes++;
    audible = true;
  }

  @override
  Future<void> setVolume(double value) async => setVolumes.add(value);

  @override
  Future<void> dispose() async {
    disposed = true;
    audible = false;
    await events.close();
  }
}

// Models Atlas PlayerClient: a second source on the same player is rejected.
class _SingleLoadPlayer implements MusicTrackPlayer {
  _SingleLoadPlayer(this.owner);
  final _Player owner;
  bool loaded = false;
  @override
  Stream<void> get completed => owner.completed;
  @override
  Future<void> play(String asset, {required double volume}) async {
    if (loaded) throw StateError('already loaded');
    loaded = true;
    await owner.play(asset, volume: volume);
  }

  @override
  Future<void> pause() => owner.pause();
  @override
  Future<void> resume() => owner.resume();
  @override
  Future<void> setVolume(double value) => owner.setVolume(value);
  @override
  Future<void> dispose() => owner.dispose();
}
