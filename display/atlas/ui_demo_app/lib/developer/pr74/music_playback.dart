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
  Future<bool> toggle();
  Future<void> selectTrack(int index);
  Future<void> setVolume(double value);
  Future<void> dispose();
}

/// Local assets. 클래식 여섯 곡은 차례로 돌고(바흐 뒤 드뷔시), V55 의 카페 음악·백색 소음은 한 곡을 되풀이한다
/// ([musicGroups]). hub 의 track 번호와 맞물려 있으니 순서를 바꾸지 말고 뒤에만 더한다.
const classicalPlaylist = [
  'audio/deskmate_test_music.mp3',
  'audio/chopin_nocturne_op9_no2.mp3',
  'audio/satie_gymnopedie_no1.mp3',
  'audio/beethoven_fur_elise.mp3',
  'audio/mozart_sonata_k545_allegro.mp3',
  'audio/bach_prelude_bwv846.mp3',
  'audio/cozy_coffee_jazz.mp3',
  'audio/river_birds.mp3',
];

const classicalTrackTitles = [
  '드뷔시 · 달빛',
  '쇼팽 · 녹턴 Op.9 No.2',
  '사티 · 짐노페디 1번',
  '베토벤 · 엘리제를 위하여',
  '모차르트 · 소나타 K.545 1악장',
  '바흐 · 프렐류드 BWV 846',
  '카페 재즈',
  '냇물과 새소리',
];

/// 설정 '소리' 선택(Persona.sound) → 곡 묶음. 곡이 끝나면 같은 묶음 안에서 다음 곡(한 곡이면 그 곡을 다시).
const musicGroups = {
  '클래식': [0, 1, 2, 3, 4, 5],
  '카페 음악': [6],
  '백색 소음': [7],
};

/// [index] 가 든 묶음. 어느 묶음에도 없으면 그 곡 하나.
List<int> musicGroupOf(int index) => musicGroups.values
    .firstWhere((g) => g.contains(index), orElse: () => [index]);

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
        final group = musicGroupOf(_index);
        _index = group[(group.indexOf(_index) + 1) % group.length];
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

/// V39: AudioManager 의 출력 기기·볼륨을 다루는 D-Bus 경계. 기기 경로는 객체 경로 문자열이다.
/// 시험은 가짜로 바꿔 끼운다([AtlasOutputDeviceVolume.new] 의 `bus`).
abstract interface class AudioDeviceBus {
  /// 예전 판이 media 스트림 게인을 건드렸다 — 출력 기기 볼륨 하나만 기준이 되게 100 으로 둔다.
  Future<void> neutralizeMediaStream();

  /// 지금 소리가 나가는 기기(AudioManager1.ActiveOutputDevice).
  Future<String> activeOutputDevice();
  Future<({int min, int max})> volumeRange(String device);
  Future<int> volume(String device);
  Future<void> setVolume(String device, int raw);

  /// 그 기기의 Volume 이 바뀌었다는 신호(받으면 다시 읽는다). 신호가 없는 환경이면 빈 스트림.
  Stream<void> volumeChanges(String device);
  Future<void> close();
}

/// Uses Atlas AudioManager so the UI and Bluetooth speaker buttons share the
/// active output device volume. No Bluetooth address or PulseAudio sink ID is
/// stored in the application.
///
/// V39(11-02 Pi 5): 앱 시작 때의 출력 기기(hdmi_speaker)에 묶인 채, 나중에 스피커가 붙어
/// ActiveOutputDevice 가 bt_speaker 로 바뀌어도 슬라이더가 hdmi_speaker 만 바꿔 소리가 줄지 않았다
/// (hdmi 20 · bt 100). 이제 0.5 초 확인과 볼륨 설정 때마다 지금 기기를 다시 읽고, 바뀌었으면 그 기기로
/// 갈아 끼운 뒤 **그 기기의 현재 볼륨을 슬라이더에 보여 준다**(사용자가 정한 값을 새 기기에 덮어쓰지 않는다 —
/// 스피커 버튼으로 맞춘 볼륨이 갑자기 바뀌지 않게).
class AtlasOutputDeviceVolume implements MusicOutputVolume {
  AtlasOutputDeviceVolume(
      {AudioDeviceBus? bus,
      this.pollEvery = const Duration(milliseconds: 500)})
      : _bus = bus ?? DbusAudioDeviceBus();

  final AudioDeviceBus _bus;

  /// Bluetooth AVRCP changes are reflected in Device.Volume, but this Atlas
  /// release does not emit PropertiesChanged for every speaker-button press.
  final Duration pollEvery;
  final _changes = StreamController<double>.broadcast();
  String? _device;
  StreamSubscription<void>? _deviceSubscription;
  Timer? _pollTimer;
  double? _lastVolume;
  bool _reading = false;
  bool _closed = false;
  int _minimum = 0;
  int _maximum = 100;

  /// 지금 볼륨을 맞추는 기기(객체 경로). 시작 전이면 null.
  String? get device => _device;

  @override
  Stream<double> get changes => _changes.stream;

  @override
  Future<double> initialize() async {
    await _bus.neutralizeMediaStream();
    await _bind(await _bus.activeOutputDevice());
    final volume = await _readVolume();
    _lastVolume = volume;
    _pollTimer = Timer.periodic(pollEvery, (_) => unawaited(_refreshVolume()));
    return volume;
  }

  /// [device] 로 갈아 끼운다: 범위를 읽고 그 기기의 변경 신호만 듣는다.
  Future<void> _bind(String device) async {
    final range = await _bus.volumeRange(device);
    await _deviceSubscription?.cancel();
    _device = device;
    _minimum = range.min;
    _maximum = range.max;
    _deviceSubscription = _bus.volumeChanges(device).listen(
        (_) => unawaited(_refreshVolume()),
        onError: (Object _, StackTrace __) {});
  }

  /// 지금 소리가 나가는 기기가 바뀌었으면 갈아 끼운다. 바뀌었으면 true.
  Future<bool> _followActiveDevice() async {
    final active = await _bus.activeOutputDevice();
    if (active == _device) return false;
    await _bind(active);
    return true;
  }

  double _normalize(int value) {
    if (_maximum <= _minimum) return 1;
    return ((value - _minimum) / (_maximum - _minimum)).clamp(0.0, 1.0);
  }

  Future<double> _readVolume() async => _normalize(await _bus.volume(_device!));

  Future<void> _refreshVolume() async {
    if (_reading || _closed || _device == null) return;
    _reading = true;
    try {
      await _followActiveDevice();
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
    // 마지막 확인 뒤에 스피커가 붙었을 수 있다 — 바꾸기 전에 지금 기기를 따른다
    try {
      await _followActiveDevice();
    } catch (_) {}
    final raw = (_minimum + ((_maximum - _minimum) * value)).round();
    await _bus.setVolume(_device!, raw);
    _publish(value);
  }

  @override
  Future<void> dispose() async {
    _closed = true;
    _pollTimer?.cancel();
    await _deviceSubscription?.cancel();
    await _changes.close();
    await _bus.close();
  }
}

/// [AudioDeviceBus] 의 실제 Atlas D-Bus 구현(com.atlas.AudioManager1).
class DbusAudioDeviceBus implements AudioDeviceBus {
  DbusAudioDeviceBus({DBusClient? client})
      : _client = client ?? DBusClient.system();

  static const _busName = 'com.atlas.AudioManager1';
  static const _managerPath = '/com/atlas/AudioManager1';
  static const _managerInterface = 'com.atlas.AudioManager1';
  static const _deviceInterface = 'com.atlas.AudioManager1.Device';
  static const _streamInterface = 'com.atlas.AudioManager1.Stream';
  static const _mediaPath = '/com/atlas/AudioManager1/Stream/media';

  final DBusClient _client;

  DBusRemoteObject _remote(String path) =>
      DBusRemoteObject(_client, name: _busName, path: DBusObjectPath(path));

  Future<int> _uint(String path, String name) async => (await _remote(path)
          .getProperty(_deviceInterface, name, signature: DBusSignature('u')))
      .asUint32();

  @override
  Future<void> neutralizeMediaStream() => _remote(_mediaPath)
      .setProperty(_streamInterface, 'Volume', const DBusUint32(100));

  @override
  Future<String> activeOutputDevice() async => (await _remote(_managerPath)
          .getProperty(_managerInterface, 'ActiveOutputDevice',
              signature: DBusSignature('o')))
      .asObjectPath()
      .value;

  @override
  Future<({int min, int max})> volumeRange(String device) async =>
      (min: await _uint(device, 'VolumeMin'), max: await _uint(device, 'VolumeMax'));

  @override
  Future<int> volume(String device) => _uint(device, 'Volume');

  @override
  Future<void> setVolume(String device, int raw) => _remote(device)
      .callMethod(_deviceInterface, 'SetVolume', [DBusUint32(raw)]);

  @override
  Stream<void> volumeChanges(String device) => _remote(device)
      .propertiesChanged
      .where((s) =>
          s.propertiesInterface == _deviceInterface &&
          (s.changedProperties.containsKey('Volume') ||
              s.invalidatedProperties.contains('Volume')))
      .map((_) {});

  @override
  Future<void> close() => _client.close();
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
