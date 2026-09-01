import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:dbus/dbus.dart';

abstract interface class MusicPlayback {
  bool get isPlaying;
  Future<bool> toggle();
  Future<void> dispose();
}

class AtlasMusicPlayback implements MusicPlayback {
  final AudioPlayer _player = AudioPlayer();
  final AtlasMediaPermission _permission = AtlasMediaPermission();
  bool _prepared = false;
  bool _playing = false;

  @override
  bool get isPlaying => _playing;

  @override
  Future<bool> toggle() async {
    if (_playing) {
      await _player.pause();
      _playing = false;
      return false;
    }

    if (!_prepared) {
      if (!await _permission.ensureGranted()) {
        throw StateError('미디어 재생 권한을 허용할 수 없습니다.');
      }
      await _player.setReleaseMode(ReleaseMode.loop);
      await _player.play(AssetSource('audio/deskmate_test_music.mp3'));
      _prepared = true;
    } else {
      await _player.resume();
    }
    _playing = true;
    return true;
  }

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
