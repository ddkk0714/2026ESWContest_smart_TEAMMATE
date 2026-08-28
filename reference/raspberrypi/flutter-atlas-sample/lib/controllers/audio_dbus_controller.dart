import 'dart:async';
import 'package:dbus/dbus.dart';
import 'dbus_config.dart';
import 'audio_controller.dart';

export 'audio_controller.dart' show AudioMetadata;

/// D-Bus 직접 호출 기반 오디오 컨트롤러.
/// MediaPlayer1 D-Bus 서비스에 직접 접근해 재생을 제어한다.
class AudioDbusController implements AudioController {
  late DBusClient _controllerClient;
  late DBusClient _playerClient;

  late DBusRemoteObject _controller;
  late DBusRemoteObject _player;
  DBusRemoteObject? _session;

  static const String _appId = 'com.atlas.app.smartkitchen';
  static const String _sessionId = 'kitchen_session';

  final _playbackStatusController = StreamController<String>.broadcast();
  final _positionController = StreamController<int>.broadcast();
  final _metadataController = StreamController<AudioMetadata>.broadcast();
  final _bufferingController = StreamController<int>.broadcast();

  Stream<String> get playbackStatus => _playbackStatusController.stream;
  Stream<int> get position => _positionController.stream;
  Stream<AudioMetadata> get metadata => _metadataController.stream;
  Stream<int> get bufferingPercent => _bufferingController.stream;

  int? _playerId;
  StreamSubscription? _playerSignalSub;
  StreamSubscription? _controllerSignalSub;

  // 세션에 기록 중인 현재 메타데이터 상태 (title/artist는 앱이 setInitialMetadata로 제공)
  String _sessionTitle = '';
  String _sessionArtist = '';
  String _sessionAlbum = '';
  int _sessionDurationMs = 0;

  // PositionChanged가 10Hz로 오므로 Session 기록은 1초에 1회로 제한
  int _lastSessionPositionWriteMs = 0;

  AudioDbusController() {
    _controllerClient = DBusClient.system();
    _playerClient = DBusClient.system();

    _controller = DBusRemoteObject(
      _controllerClient,
      name: DBusConfig.mediaControllerBusName,
      path: DBusObjectPath(DBusConfig.mediaControllerObjectPath),
    );
    _player = DBusRemoteObject(
      _playerClient,
      name: DBusConfig.mediaPlayerBusName,
      path: DBusObjectPath(DBusConfig.mediaPlayerObjectPath),
    );
  }

  Future<void> initialize() async {
    await _registerSession();
    await _activateSession();
    _subscribePlayerSignals();
    _subscribeControllerSignals();
  }

  Future<void> _registerSession() async {
    try {
      await _controller.callMethod(
        DBusConfig.mediaControllerInterface,
        'RegisterSession',
        [DBusString(_appId), DBusString(_sessionId)],
      );
    } catch (_) {
      // 세션이 이미 존재해도 무시
    }

    _session = DBusRemoteObject(
      _controllerClient,
      name: DBusConfig.mediaControllerBusName,
      path: DBusObjectPath(DBusConfig.mediaControllerSessionPath(_sessionId)),
    );
  }

  Future<void> _activateSession() async {
    try {
      await _session?.callMethod(
        DBusConfig.mediaControllerSessionInterface,
        'Activate',
        [],
      );
    } catch (e) {
      print('MediaController: Activate failed: $e');
    }
  }

  // ── Session 쓰기 (MediaPlayer1 시그널 → Session 동기화) ──────

  Future<void> setInitialMetadata(AudioMetadata meta) async {
    _sessionTitle = meta.title;
    _sessionArtist = meta.artist;
    _sessionAlbum = meta.album;
    _sessionDurationMs = meta.durationMs;
    await Future.wait([
      _writeSessionStatus('stopped'),
      _writeSessionPosition(0),
      _writeSessionMetadata(),
    ]);
  }

  Future<void> _writeSessionStatus(String status) async {
    try {
      await _session?.setProperty(
        DBusConfig.mediaControllerSessionInterface,
        'PlaybackStatus',
        DBusString(status),
      );
    } catch (e) {
      print('Session: setStatus failed: $e');
    }
  }

  Future<void> _writeSessionPosition(int positionMs) async {
    try {
      await _session?.setProperty(
        DBusConfig.mediaControllerSessionInterface,
        'PlaybackPosition',
        DBusInt64(positionMs),
      );
    } catch (e) {
      print('Session: setPosition failed: $e');
    }
  }

  Future<void> _writeSessionMetadata() async {
    try {
      await _session?.setProperty(
        DBusConfig.mediaControllerSessionInterface,
        'MetaData',
        DBusDict(
          DBusSignature('s'),
          DBusSignature('v'),
          {
            DBusString('title'): DBusVariant(DBusString(_sessionTitle)),
            DBusString('artist'): DBusVariant(DBusString(_sessionArtist)),
            DBusString('album'): DBusVariant(DBusString(_sessionAlbum)),
            DBusString('duration'): DBusVariant(DBusInt64(_sessionDurationMs)),
          },
        ),
      );
    } catch (e) {
      print('Session: setMetaData failed: $e');
    }
  }

  // ── 시그널 구독 ───────────────────────────────────────────────

  void _subscribePlayerSignals() {
    _playerSignalSub = DBusSignalStream(
      _playerClient,
      sender: DBusConfig.mediaPlayerBusName,
      interface: DBusConfig.mediaPlayerInterface,
    ).listen((signal) {
      if (signal.values.isEmpty) return;
      final pid = signal.values[0].asInt32();
      if (pid != _playerId) return;

      switch (signal.name) {
        case 'Playing':
          _playbackStatusController.add('playing');
          _writeSessionStatus('playing');
        case 'Paused':
          _playbackStatusController.add('paused');
          _writeSessionStatus('paused');
        case 'EndOfStream':
          _playbackStatusController.add('stopped');
          _writeSessionStatus('stopped');
        case 'PositionChanged':
          if (signal.values.length >= 2) {
            final pos = signal.values[1].asInt64();
            _positionController.add(pos);
            // Session 기록은 1초 간격으로만 — 10Hz 신호를 그대로 쓰면 D-Bus 과부하
            final now = DateTime.now().millisecondsSinceEpoch;
            if (now - _lastSessionPositionWriteMs >= 1000) {
              _lastSessionPositionWriteMs = now;
              _writeSessionPosition(pos);
            }
          }
        case 'SourceInfo':
          if (signal.values.length >= 2) {
            final info = signal.values[1] as DBusDict;
            final dur = _parseDurationMs(info);
            if (dur > 0) {
              _sessionDurationMs = dur;
              _metadataController.add(AudioMetadata(durationMs: dur));
              _writeSessionMetadata();
            }
          }
        case 'BufferingStart':
          _bufferingController.add(0);
        case 'BufferingProgress':
          if (signal.values.length >= 2) {
            _bufferingController.add(signal.values[1].asInt32());
          }
        case 'BufferingEnd':
          _bufferingController.add(100);
        case 'AsyncError':
          if (signal.values.length >= 2) {
            print('MediaPlayer AsyncError: ${signal.values[1].asString()}');
            _playbackStatusController.add('error');
          }
      }
    });
  }

  void _subscribeControllerSignals() {
    // PlaybackInfoChanged는 우리가 Session에 쓴 값의 에코이므로 구독하지 않는다.
    // 상태/위치/메타데이터는 MediaPlayer1 시그널(더 직접적)에서 이미 처리됨.
    // 구독 유지 이유: 향후 외부 AVRCP 이벤트나 다른 앱이 Session을 변경할 경우 대응.
    _controllerSignalSub = DBusSignalStream(
      _controllerClient,
      sender: DBusConfig.mediaControllerBusName,
      interface: DBusConfig.mediaControllerInterface,
      name: 'PlaybackInfoChanged',
    ).listen((_) {
      // 에코 이벤트는 무시 — MediaPlayer1 시그널이 primary source
    });
  }

  int _parseDurationMs(DBusDict dict) {
    int tryInt(String key) {
      try { return dict.children[DBusString(key)]?.asVariant().asInt64() ?? 0; }
      catch (_) { return 0; }
    }
    // 실제 키는 "duration_ms" (snake_case) — busctl로 확인
    return tryInt('duration_ms') > 0 ? tryInt('duration_ms') : tryInt('durationMs');
  }

  // ── 재생 제어 ─────────────────────────────────────────────────

  Future<bool> load(String uri) async {
    try {
      // useVideoFrameCb=true switches the vsink from waylandsink to appsink
      // (Iceoryx). Without this the autoplay pipeline sets up waylandsink and
      // audio-only HTTPS streams fail with GStreamer not-linked (-1).
      // This mirrors what the video plugin sets in umplayer::LoadParams.
      final result = await _player.callMethod(
        DBusConfig.mediaPlayerInterface,
        'Load',
        [
          DBusString(uri),
          DBusDict(DBusSignature('s'), DBusSignature('v'), {
            DBusString('useVideoFrameCb'): DBusVariant(DBusBoolean(true)),
          }),
        ],
        replySignature: DBusSignature('i'),
      );
      _playerId = result.returnValues[0].asInt32();
      return true;
    } catch (e) {
      print('MediaPlayer: Load failed: $e');
      return false;
    }
  }

  Future<bool> play() async {
    if (_playerId == null) return false;
    try {
      await _player.callMethod(
        DBusConfig.mediaPlayerInterface,
        'Play',
        [DBusInt32(_playerId!)],
      );
      return true;
    } catch (e) {
      print('MediaPlayer: Play failed: $e');
      return false;
    }
  }

  Future<void> pause() async {
    if (_playerId == null) return;
    try {
      await _player.callMethod(
        DBusConfig.mediaPlayerInterface,
        'Pause',
        [DBusInt32(_playerId!)],
      );
    } catch (e) {
      print('MediaPlayer: Pause failed: $e');
    }
  }

  Future<void> seek(int positionMs) async {
    if (_playerId == null) return;
    try {
      await _player.callMethod(
        DBusConfig.mediaPlayerInterface,
        'Seek',
        [DBusInt32(_playerId!), DBusInt64(positionMs)],
      );
    } catch (e) {
      print('MediaPlayer: Seek failed: $e');
    }
  }

  Future<void> setVolume(double volume) async {
    if (_playerId == null) return;
    try {
      await _player.callMethod(
        DBusConfig.mediaPlayerInterface,
        'SetVolume',
        [DBusInt32(_playerId!), DBusDouble(volume)],
      );
    } catch (e) {
      print('MediaPlayer: SetVolume failed: $e');
    }
  }

  Future<double> getVolume() async {
    if (_playerId == null) return 1.0;
    try {
      final result = await _player.callMethod(
        DBusConfig.mediaPlayerInterface,
        'GetVolume',
        [DBusInt32(_playerId!)],
        replySignature: DBusSignature('d'),
      );
      return result.returnValues[0].asDouble();
    } catch (e) {
      print('MediaPlayer: GetVolume failed: $e');
      return 1.0;
    }
  }

  Future<void> stop() async {
    await _writeSessionStatus('stopped');
    await _unload();
  }

  Future<void> sendKeyEvent(String event) async {
    try {
      await _controller.callMethod(
        DBusConfig.mediaControllerInterface,
        'SendKeyEvent',
        [DBusString(event)],
      );
    } catch (e) {
      print('MediaController: SendKeyEvent failed: $e');
    }
  }

  Future<void> _unload() async {
    if (_playerId == null) return;
    try {
      await _player.callMethod(
        DBusConfig.mediaPlayerInterface,
        'Unload',
        [DBusInt32(_playerId!)],
      );
      _playerId = null;
    } catch (e) {
      print('MediaPlayer: Unload failed: $e');
    }
  }

  Future<void> _deactivateSession() async {
    try {
      await _session?.callMethod(
        DBusConfig.mediaControllerSessionInterface,
        'Deactivate',
        [],
      );
      await _controller.callMethod(
        DBusConfig.mediaControllerInterface,
        'UnregisterSession',
        [DBusString(_sessionId)],
      );
    } catch (e) {
      print('MediaController: Deactivate/Unregister failed: $e');
    }
  }

  Future<void> dispose() async {
    await _playerSignalSub?.cancel();
    await _controllerSignalSub?.cancel();
    await _unload();
    await _deactivateSession();
    await _playbackStatusController.close();
    await _positionController.close();
    await _metadataController.close();
    await _bufferingController.close();
    await _playerClient.close();
    await _controllerClient.close();
  }
}
