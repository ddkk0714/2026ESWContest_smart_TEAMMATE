import 'dart:async';
import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:dbus/dbus.dart';

abstract interface class MusicPlayback {
  bool get isPlaying;
  int get selectedTrack;
  Stream<bool> get playingChanges;
  Future<bool> toggle();
  Future<void> selectTrack(int index);
  Future<void> dispose();
}

/// Local assets, played in order and wrapped back to Debussy after Satie.
const classicalPlaylist = [
  'audio/deskmate_test_music.mp3',
  'audio/chopin_nocturne_op9_no2.mp3',
  'audio/satie_gymnopedie_no1.mp3',
];

const classicalTrackTitles = [
  '드뷔시 · 달빛',
  '쇼팽 · 녹턴 Op.9 No.2',
  '사티 · 짐노페디 1번',
];

/// Small audio boundary so completion/pause races can be tested without a board.
abstract interface class MusicTrackPlayer {
  Stream<void> get completed;
  Future<void> play(String asset);
  Future<void> pause();
  Future<void> resume();
  Future<void> dispose();
}

class AtlasMusicPlayback implements MusicPlayback {
  AtlasMusicPlayback({MusicTrackPlayer Function()? playerFactory})
      : _createPlayer = playerFactory ?? _AtlasTrackPlayer.new;

  void _listenForCompletion(MusicTrackPlayer player) {
    _completion = player.completed.listen((_) {
      unawaited(_enqueue(() async {
        if (!_prepared || !identical(player, _player)) return;
        _index = (_index + 1) % classicalPlaylist.length;
        _prepared = false;
        if (_playing) await _startTrack();
        _setPlaying(_playing);
      }).catchError((Object error, StackTrace stack) {
        _setPlaying(false);
        if (!_disposed) _changes.addError(error, stack);
      }));
    });
  }

  final MusicTrackPlayer Function() _createPlayer;
  MusicTrackPlayer? _player;
  final _changes = StreamController<bool>.broadcast();
  StreamSubscription<void>? _completion;
  Future<void> _pending = Future<void>.value();
  int _index = 0;
  bool _prepared = false;
  bool _playing = false;
  bool _disposed = false;

  @override
  bool get isPlaying => _playing;

  @override
  int get selectedTrack => _index;

  @override
  Stream<bool> get playingChanges => _changes.stream;

  // Finish a track switch before applying OFF, so a late play cannot undo pause.
  Future<void> _enqueue(Future<void> Function() action) {
    final operation = _pending.then((_) async {
      if (!_disposed) await action();
    });
    _pending = operation.catchError((Object _) {});
    return operation;
  }

  void _setPlaying(bool value) {
    _playing = value;
    if (!_disposed) _changes.add(value);
  }

  Future<void> _startTrack() async {
    // Atlas PlayerClient rejects load() while another source is loaded.
    // Fully dispose the old native player before loading a different track.
    await _completion?.cancel();
    await _player?.dispose();
    final player = _createPlayer();
    _player = player;
    _listenForCompletion(player);
    await player.play(classicalPlaylist[_index]);
    _prepared = true;
  }

  @override
  Future<void> selectTrack(int index) async {
    RangeError.checkValidIndex(index, classicalPlaylist, 'index');
    await _enqueue(() async {
      if (index == _index) return;
      try {
        await _player?.pause();
        _index = index;
        _prepared = false;
        if (_playing) await _startTrack();
        _setPlaying(_playing);
      } catch (_) {
        _setPlaying(false);
        rethrow;
      }
    });
  }

  @override
  Future<bool> toggle() async {
    await _enqueue(() async {
      if (_playing) {
        await _player!.pause();
        _setPlaying(false);
      } else {
        if (_prepared) {
          await _player!.resume();
        } else {
          await _startTrack();
        }
        _setPlaying(true);
      }
    });
    return _playing;
  }

  @override
  Future<void> dispose() async {
    _disposed = true;
    await _completion?.cancel();
    await _pending;
    await _completion?.cancel();
    await _player?.dispose();
    await _changes.close();
  }
}

class _AtlasTrackPlayer implements MusicTrackPlayer {
  final AudioPlayer _player = AudioPlayer();
  final AtlasMediaPermission _permission = AtlasMediaPermission();
  bool _permissionGranted = false;

  @override
  Stream<void> get completed => _player.onPlayerComplete;

  @override
  Future<void> play(String asset) async {
    if (!_permissionGranted) {
      if (!await _permission.ensureGranted()) {
        throw StateError('미디어 재생 권한을 허용할 수 없습니다.');
      }
      _permissionGranted = true;
    }
    await _player.setReleaseMode(ReleaseMode.stop);
    await _player.play(AssetSource(asset));
  }

  @override
  Future<void> pause() => _player.pause();

  @override
  Future<void> resume() => _player.resume();

  @override
  Future<void> dispose() async {
    await _player.dispose();
    await _permission.dispose();
  }
}

/// Requests only the Atlas user-consent permission needed by the local MP3
/// player. The ON action is the user's explicit request to grant it.
class AtlasMediaPermission {
  static const _permissionName =
      'com.atlas.permission.user_consent.mediaplayer';
  static const _busName = 'com.atlas.PermissionAgent1';
  static const _objectPath = '/com/atlas/PermissionAgent1';
  static const _interface = 'com.atlas.PermissionAgent1';

  factory AtlasMediaPermission() => AtlasMediaPermission.sharedClient();

  // The remote object and permission calls share one system-bus connection.
  AtlasMediaPermission._(this._client, this._agent);

  factory AtlasMediaPermission.sharedClient() {
    final client = DBusClient.system();
    return AtlasMediaPermission._(
      client,
      DBusRemoteObject(
        client,
        name: _busName,
        path: DBusObjectPath(_objectPath),
      ),
    );
  }

  final DBusClient _client;
  final DBusRemoteObject _agent;

  Future<bool> ensureGranted() async {
    if (await _checkSelf()) return true;
    final appUid = await _currentAppUid();
    if (appUid.isEmpty) return false;
    try {
      await _agent.callMethod(
        _interface,
        'UpdateUserConsentPermission',
        [DBusString(appUid), DBusString(_permissionName), DBusInt32(1)],
      );
    } catch (_) {
      return false;
    }
    return _checkSelf();
  }

  Future<bool> _checkSelf() async {
    try {
      await _agent.callMethod(
        _interface,
        'CheckSelfUserConsentPermission',
        [const DBusString(_permissionName)],
      );
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<String> _currentAppUid() async {
    try {
      for (final line in await File('/proc/self/status').readAsLines()) {
        if (!line.startsWith('Uid:')) continue;
        final fields = line.trim().split(RegExp(r'\s+'));
        if (fields.length > 1) return 'u0_a${fields[1]}';
      }
    } catch (_) {
      // Reported to the caller as a denied permission.
    }
    return '';
  }

  Future<void> dispose() => _client.close();
}
