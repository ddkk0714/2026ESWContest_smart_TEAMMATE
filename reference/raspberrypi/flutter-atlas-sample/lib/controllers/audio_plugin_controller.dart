import 'dart:async';
import 'package:audioplayers/audioplayers.dart';
import 'package:dbus/dbus.dart';
import 'dbus_config.dart';
import 'audio_controller.dart';

export 'audio_controller.dart' show AudioMetadata;

/// audioplayers_atlas 플러그인 기반 오디오 컨트롤러.
/// 재생 엔진: audioplayers_atlas → libmediaplayerclient → MediaPlayer1 D-Bus
/// 세션 관리: MediaController1 D-Bus 직접 (AVRCP write-back용)
class AudioPluginController implements AudioController {
  // ── 재생 엔진 ─────────────────────────────────────────────
  final AudioPlayer _player = AudioPlayer()
    ..setReleaseMode(ReleaseMode.stop);  // 재생 완료 시 stop 상태로 유지

  // 현재 로드된 소스 (play() 호출 시 재사용)
  Source? _currentSource;

  // ── MediaController1 세션 (AVRCP) ────────────────────────
  late DBusClient _controllerClient;
  late DBusRemoteObject _controller;
  DBusRemoteObject? _session;

  static const String _appId = 'com.atlas.app.smartkitchen';
  static const String _sessionId = 'kitchen_session';

  // ── 외부 노출 스트림 ──────────────────────────────────────
  final _playbackStatusController = StreamController<String>.broadcast();
  final _positionController = StreamController<int>.broadcast();
  final _metadataController = StreamController<AudioMetadata>.broadcast();
  final _bufferingController = StreamController<int>.broadcast();

  Stream<String> get playbackStatus => _playbackStatusController.stream;
  Stream<int> get position => _positionController.stream;
  Stream<AudioMetadata> get metadata => _metadataController.stream;
  Stream<int> get bufferingPercent => _bufferingController.stream;

  // ── 내부 상태 ─────────────────────────────────────────────
  final List<StreamSubscription> _subs = [];
  String _sessionTitle = '';
  String _sessionArtist = '';
  int _sessionDurationMs = 0;
  int _lastSessionPositionWriteMs = 0;

  AudioPluginController() {
    _controllerClient = DBusClient.system();
    _controller = DBusRemoteObject(
      _controllerClient,
      name: DBusConfig.mediaControllerBusName,
      path: DBusObjectPath(DBusConfig.mediaControllerObjectPath),
    );
  }

  // ── 초기화 ────────────────────────────────────────────────

  Future<void> initialize() async {
    await _registerSession();
    await _activateSession();
    _subscribePlayerEvents();
  }

  Future<void> _registerSession() async {
    try {
      await _controller.callMethod(
        DBusConfig.mediaControllerInterface,
        'RegisterSession',
        [DBusString(_appId), DBusString(_sessionId)],
      );
    } catch (_) {}

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
        'Activate', [],
      );
    } catch (e) {
      print('Session: Activate failed: $e');
    }
  }

  // ── audioplayers 이벤트 → 스트림 + Session 동기화 ─────────

  void _subscribePlayerEvents() {
    _subs.add(_player.onPlayerStateChanged.listen((state) {
      print('[Plugin] onPlayerStateChanged: $state');
      final status = _stateToString(state);
      _playbackStatusController.add(status);
      _writeSessionStatus(status);
    }));

    _subs.add(_player.onPositionChanged.listen((duration) {
      final ms = duration.inMilliseconds;
      _positionController.add(ms);
      final now = DateTime.now().millisecondsSinceEpoch;
      if (now - _lastSessionPositionWriteMs >= 1000) {
        _lastSessionPositionWriteMs = now;
        _writeSessionPosition(ms);
      }
    }));

    _subs.add(_player.onDurationChanged.listen((duration) {
      print('[Plugin] onDurationChanged: ${duration?.inMilliseconds}ms');
      if (duration != null && duration.inMilliseconds > 0) {
        _sessionDurationMs = duration.inMilliseconds;
        // title/artist 포함해야 metadata 이벤트가 덮어쓰지 않음
        _metadataController.add(AudioMetadata(
          title: _sessionTitle,
          artist: _sessionArtist,
          durationMs: _sessionDurationMs,
        ));
        _writeSessionMetadata();
      }
    }));

    // 재생 완료 — C++ OnEndOfStream은 Stop() 후 stream handler 알림 없음.
    // onPlayerStateChanged가 fired되지 않으므로 여기서 명시적으로 처리.
    _subs.add(_player.onPlayerComplete.listen((_) {
      print('[Plugin] onPlayerComplete');
      _positionController.add(0);
      _playbackStatusController.add('stopped');
      _writeSessionStatus('stopped');
      _writeSessionPosition(0);
    }));

    _subs.add(_player.onSeekComplete.listen((_) {
      print('[Plugin] onSeekComplete');
    }));

    _subs.add(_player.onLog.listen(
      (msg) => print('[Plugin] log: $msg'),
      onError: (Object e, [StackTrace? st]) => print('[Plugin] log error: $e'),
    ));

    _subs.add(AudioPlayer.global.onLog.listen(
      (msg) => print('[Plugin][global] log: $msg'),
      onError: (Object e, [StackTrace? st]) => print('[Plugin][global] log error: $e'),
    ));
  }

  static String _stateToString(PlayerState state) => switch (state) {
    PlayerState.playing => 'playing',
    PlayerState.paused => 'paused',
    PlayerState.completed => 'stopped',
    _ => 'stopped',
  };

  // ── 공개 API (AudioDbusController와 동일한 인터페이스) ────

  Future<void> setInitialMetadata(AudioMetadata meta) async {
    _sessionTitle = meta.title;
    _sessionArtist = meta.artist;
    _sessionDurationMs = meta.durationMs;
    await Future.wait([
      _writeSessionStatus('stopped'),
      _writeSessionPosition(0),
      _writeSessionMetadata(),
    ]);
  }

  Future<bool> load(String uri) async {
    try {
      print('[Plugin] load: $uri');
      await _player.release(); // unload 후 새 소스 로드 (stop은 unload 안 함)
      if (uri.startsWith('http')) {
        _currentSource = UrlSource(uri);
      } else {
        final path = Uri.parse(uri).toFilePath();
        print('[Plugin] DeviceFileSource path: $path');
        _currentSource = DeviceFileSource(path);
      }
      // D-Bus NoPermission 등 native 에러 시 Future가 완료되지 않는 경우를 방어
      await _player.setSource(_currentSource!).timeout(
        const Duration(seconds: 10),
        onTimeout: () => throw Exception('setSource timeout — native plugin not responding'),
      );
      print('[Plugin] load done, playerState=${_player.state}');
      return true;
    } catch (e) {
      print('[Plugin] load failed: $e');
      _currentSource = null;
      return false;
    }
  }

  Future<bool> play() async {
    try {
      print('[Plugin] play called, playerState=${_player.state}, source=$_currentSource');
      if (_currentSource == null) {
        print('[Plugin] play: no source loaded');
        return false;
      }
      // pause 상태면 현재 위치에서 resume; 그 외(stopped/completed)는 처음부터 재생
      if (_player.state == PlayerState.paused) {
        await _player.resume();
      } else {
        await _player.play(_currentSource!);
      }
      print('[Plugin] play done, playerState=${_player.state}');
      return true;
    } catch (e) {
      print('[Plugin] play failed: $e');
      return false;
    }
  }

  Future<void> pause() async {
    try { await _player.pause(); }
    catch (e) { print('[Plugin] pause failed: $e'); }
  }

  Future<void> seek(int positionMs) async {
    try { await _player.seek(Duration(milliseconds: positionMs)); }
    catch (e) { print('[Plugin] seek failed: $e'); }
  }

  Future<void> setVolume(double volume) async {
    try { await _player.setVolume(volume); }
    catch (e) { print('[Plugin] setVolume failed: $e'); }
  }

  Future<double> getVolume() async => _player.volume;

  Future<void> stop() async {
    _currentSource = null;
    await _writeSessionStatus('stopped');
    try { await _player.stop(); }
    catch (e) { print('[Plugin] stop failed: $e'); }
  }

  Future<void> sendKeyEvent(String event) async {
    try {
      await _controller.callMethod(
        DBusConfig.mediaControllerInterface,
        'SendKeyEvent', [DBusString(event)],
      );
    } catch (e) {
      print('MediaController: SendKeyEvent failed: $e');
    }
  }

  // ── Session write-back (AVRCP) ────────────────────────────

  Future<void> _writeSessionStatus(String status) async {
    try {
      await _session?.setProperty(
        DBusConfig.mediaControllerSessionInterface,
        'PlaybackStatus', DBusString(status),
      );
    } catch (e) { print('Session: setStatus failed: $e'); }
  }

  Future<void> _writeSessionPosition(int positionMs) async {
    try {
      await _session?.setProperty(
        DBusConfig.mediaControllerSessionInterface,
        'PlaybackPosition', DBusInt64(positionMs),
      );
    } catch (e) { print('Session: setPosition failed: $e'); }
  }

  Future<void> _writeSessionMetadata() async {
    try {
      await _session?.setProperty(
        DBusConfig.mediaControllerSessionInterface,
        'MetaData',
        DBusDict(
          DBusSignature('s'), DBusSignature('v'),
          {
            DBusString('title'): DBusVariant(DBusString(_sessionTitle)),
            DBusString('artist'): DBusVariant(DBusString(_sessionArtist)),
            DBusString('duration'): DBusVariant(DBusInt64(_sessionDurationMs)),
          },
        ),
      );
    } catch (e) { print('Session: setMetaData failed: $e'); }
  }

  // ── 종료 ─────────────────────────────────────────────────

  Future<void> dispose() async {
    for (final sub in _subs) await sub.cancel();
    await _player.dispose();
    try {
      await _session?.callMethod(
        DBusConfig.mediaControllerSessionInterface, 'Deactivate', []);
      await _controller.callMethod(
        DBusConfig.mediaControllerInterface,
        'UnregisterSession', [DBusString(_sessionId)],
      );
    } catch (e) { print('Session: cleanup failed: $e'); }
    await _playbackStatusController.close();
    await _positionController.close();
    await _metadataController.close();
    await _bufferingController.close();
    await _controllerClient.close();
  }
}
