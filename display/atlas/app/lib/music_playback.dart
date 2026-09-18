import 'dart:async';
import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:dbus/dbus.dart';

abstract interface class MusicPlayback {
  bool get isPlaying;
  int get selectedTrack;
  double get volume;
  Stream<bool> get playingChanges;
  Stream<double> get volumeChanges;
  Future<bool> play();
  Future<void> pause();
  Future<bool> toggle();
  Future<void> selectTrack(int index);
  Future<void> setVolume(double value);
  Future<void> dispose();
}

/// Local assets, played in order and wrapped back to Debussy after Bach.
const classicalPlaylist = [
  'audio/deskmate_test_music.mp3',
  'audio/chopin_nocturne_op9_no2.mp3',
  'audio/satie_gymnopedie_no1.mp3',
  'audio/beethoven_fur_elise.mp3',
  'audio/mozart_sonata_k545_allegro.mp3',
  'audio/bach_prelude_bwv846.mp3',
];

const classicalTrackTitles = [
  '드뷔시 · 달빛',
  '쇼팽 · 녹턴 Op.9 No.2',
  '사티 · 짐노페디 1번',
  '베토벤 · 엘리제를 위하여',
  '모차르트 · 소나타 K.545 1악장',
  '바흐 · 프렐류드 BWV 846',
];

/// Small audio boundary so completion/pause races can be tested without a board.
abstract interface class MusicTrackPlayer {
  Stream<void> get completed;
  Future<void> play(String asset, {required double volume});
  Future<void> pause();
  Future<void> resume();
  Future<void> setVolume(double value);
  Future<void> dispose();
}

abstract interface class MusicOutputVolume {
  Stream<double> get changes;
  Future<double> initialize();
  Future<void> setVolume(double value);
  Future<void> dispose();
}

class AtlasMusicPlayback implements MusicPlayback {
  AtlasMusicPlayback({
    MusicTrackPlayer Function()? playerFactory,
    MusicOutputVolume? outputVolume,
  })  : _createPlayer = playerFactory ?? _AtlasTrackPlayer.new,
        _outputVolume = outputVolume ??
            (playerFactory == null
                ? AtlasOutputDeviceVolume()
                : _MemoryOutputVolume()) {
    _volumeSubscription = _outputVolume.changes.listen(
      _setVolumeFromOutput,
      onError: (Object _, StackTrace __) {},
    );
    _volumeReady = _initializeVolume();
  }

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
  final MusicOutputVolume _outputVolume;
  MusicTrackPlayer? _player;
  final _changes = StreamController<bool>.broadcast();
  final _volumeChanges = StreamController<double>.broadcast();
  StreamSubscription<void>? _completion;
  late final StreamSubscription<double> _volumeSubscription;
  late final Future<void> _volumeReady;
  Future<void> _pending = Future<void>.value();
  int _index = 0;
  double _volume = 1;
  bool _prepared = false;
  bool _playing = false;
  bool _disposed = false;

  @override
  bool get isPlaying => _playing;

  @override
  int get selectedTrack => _index;

  @override
  double get volume => _volume;

  @override
  Stream<bool> get playingChanges => _changes.stream;

  @override
  Stream<double> get volumeChanges => _volumeChanges.stream;

  Future<void> _initializeVolume() async {
    try {
      _setVolumeFromOutput(await _outputVolume.initialize());
    } catch (_) {}
  }

  void _setVolumeFromOutput(double value) {
    final next = value.clamp(0.0, 1.0);
    if ((_volume - next).abs() < .001) return;
    _volume = next;
    if (!_disposed) _volumeChanges.add(next);
  }

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
    await _volumeReady;
    await player.play(classicalPlaylist[_index], volume: _volume);
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
  Future<bool> play() async {
    await _enqueue(() async {
      if (_playing) return;
      if (_prepared) {
        await _player!.resume();
      } else {
        await _startTrack();
      }
      _setPlaying(true);
    });
    return _playing;
  }

  @override
  Future<void> pause() async {
    await _enqueue(() async {
      if (!_playing) return;
      await _player!.pause();
      _setPlaying(false);
    });
  }

  @override
  Future<bool> toggle() async {
    if (_playing) {
      await pause();
      return _playing;
    }
    return play();
  }

  @override
  Future<void> setVolume(double value) async {
    if (!value.isFinite || value < 0 || value > 1) {
      throw RangeError.range(value, 0, 1, 'value');
    }
    await _enqueue(() async {
      await _volumeReady;
      await _outputVolume.setVolume(value);
      if (_prepared) await _player!.setVolume(value);
      _setVolumeFromOutput(value);
    });
  }

  @override
  Future<void> dispose() async {
    _disposed = true;
    await _completion?.cancel();
    await _pending;
    await _volumeReady;
    await _volumeSubscription.cancel();
    await _completion?.cancel();
    await _player?.dispose();
    await _outputVolume.dispose();
    await _changes.close();
    await _volumeChanges.close();
  }
}

class _AtlasTrackPlayer implements MusicTrackPlayer {
  final AudioPlayer _player = AudioPlayer();
  final AtlasMediaPermission _permission = AtlasMediaPermission();
  bool _permissionGranted = false;

  @override
  Stream<void> get completed => _player.onPlayerComplete;

  @override
  Future<void> play(String asset, {required double volume}) async {
    if (!_permissionGranted) {
      if (!await _permission.ensureGranted()) {
        throw StateError('미디어 재생 권한을 허용할 수 없습니다.');
      }
      _permissionGranted = true;
    }
    await _player.setReleaseMode(ReleaseMode.stop);
    // audioplayers applies play(volume:) before loading the source. Atlas can
    // reset the native player's gain during load, so set it after load and
    // before playback starts.
    await _player.setSource(AssetSource(asset));
    await _player.setVolume(volume);
    await _player.resume();
  }

  @override
  Future<void> pause() => _player.pause();

  @override
  Future<void> resume() => _player.resume();

  @override
  Future<void> setVolume(double value) => _player.setVolume(value);

  @override
  Future<void> dispose() async {
    await _player.dispose();
    await _permission.dispose();
  }
}

class _MemoryOutputVolume implements MusicOutputVolume {
  double _value = 1;
  final _changes = StreamController<double>.broadcast();

  @override
  Stream<double> get changes => _changes.stream;

  @override
  Future<double> initialize() async => _value;

  @override
  Future<void> setVolume(double value) async {
    _value = value;
    _changes.add(value);
  }

  @override
  Future<void> dispose() => _changes.close();
}

/// Uses Atlas AudioManager so the UI and Bluetooth speaker buttons share the
/// active output device volume. No Bluetooth address or PulseAudio sink ID is
/// stored in the application.
class AtlasOutputDeviceVolume implements MusicOutputVolume {
  static const _busName = 'com.atlas.AudioManager1';
  static const _managerPath = '/com/atlas/AudioManager1';
  static const _managerInterface = 'com.atlas.AudioManager1';
  static const _deviceInterface = 'com.atlas.AudioManager1.Device';
  static const _streamInterface = 'com.atlas.AudioManager1.Stream';
  static const _mediaPath = '/com/atlas/AudioManager1/Stream/media';

  AtlasOutputDeviceVolume() : _client = DBusClient.system();

  final DBusClient _client;
  final _changes = StreamController<double>.broadcast();
  DBusRemoteObject? _device;
  StreamSubscription<DBusPropertiesChangedSignal>? _deviceSubscription;
  Timer? _pollTimer;
  double? _lastVolume;
  bool _reading = false;
  int _minimum = 0;
  int _maximum = 100;

  @override
  Stream<double> get changes => _changes.stream;

  DBusRemoteObject _remote(DBusObjectPath path) => DBusRemoteObject(
        _client,
        name: _busName,
        path: path,
      );

  @override
  Future<double> initialize() async {
    // Earlier builds controlled the media stream gain. Keep it neutral so the
    // active output device is the single source of truth.
    await _remote(DBusObjectPath(_mediaPath)).setProperty(
      _streamInterface,
      'Volume',
      const DBusUint32(100),
    );

    final manager = _remote(DBusObjectPath(_managerPath));
    final path = (await manager.getProperty(
      _managerInterface,
      'ActiveOutputDevice',
      signature: DBusSignature('o'),
    ))
        .asObjectPath();
    final device = _remote(path);
    _device = device;
    _minimum = (await device.getProperty(
      _deviceInterface,
      'VolumeMin',
      signature: DBusSignature('u'),
    ))
        .asUint32();
    _maximum = (await device.getProperty(
      _deviceInterface,
      'VolumeMax',
      signature: DBusSignature('u'),
    ))
        .asUint32();
    _deviceSubscription = device.propertiesChanged.listen(_onDeviceChanged);
    final volume = await _readVolume();
    _lastVolume = volume;
    // Bluetooth AVRCP changes are reflected in Device.Volume, but this Atlas
    // release does not emit PropertiesChanged for every speaker-button press.
    _pollTimer = Timer.periodic(
      const Duration(milliseconds: 500),
      (_) => unawaited(_refreshVolume()),
    );
    return volume;
  }

  double _normalize(int value) {
    if (_maximum <= _minimum) return 1;
    return ((value - _minimum) / (_maximum - _minimum)).clamp(0.0, 1.0);
  }

  Future<double> _readVolume() async {
    final value = (await _device!.getProperty(
      _deviceInterface,
      'Volume',
      signature: DBusSignature('u'),
    ))
        .asUint32();
    return _normalize(value);
  }

  void _onDeviceChanged(DBusPropertiesChangedSignal signal) {
    if (signal.propertiesInterface != _deviceInterface) return;
    final volume = signal.changedProperties['Volume'];
    if (volume != null) {
      _publish(_normalize(volume.asUint32()));
    } else if (signal.invalidatedProperties.contains('Volume')) {
      unawaited(_refreshVolume());
    }
  }

  Future<void> _refreshVolume() async {
    if (_reading) return;
    _reading = true;
    try {
      _publish(await _readVolume());
    } catch (_) {
      // Keep the last known value while the output device is unavailable.
    } finally {
      _reading = false;
    }
  }

  void _publish(double value) {
    if (_lastVolume != null && (_lastVolume! - value).abs() < .001) return;
    _lastVolume = value;
    if (!_changes.isClosed) _changes.add(value);
  }

  @override
  Future<void> setVolume(double value) async {
    final raw = (_minimum + ((_maximum - _minimum) * value)).round();
    await _device!.callMethod(
      _deviceInterface,
      'SetVolume',
      [DBusUint32(raw)],
    );
    _publish(value);
  }

  @override
  Future<void> dispose() async {
    _pollTimer?.cancel();
    await _deviceSubscription?.cancel();
    await _changes.close();
    await _client.close();
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
