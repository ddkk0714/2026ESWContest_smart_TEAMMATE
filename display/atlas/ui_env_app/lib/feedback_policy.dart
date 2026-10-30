import 'dart:math' show min;

import 'bluetooth_service.dart';
import 'display_state.dart';
import 'fan_control.dart';
import 'lamp_defaults.dart';
import 'music_playback.dart';
import 'voice_alert.dart';

const ilinkServiceUuid = '0000a032-0000-1000-8000-00805f9b34fb';
const ilinkWriteUuid = '0000a040-0000-1000-8000-00805f9b34fb';

List<int> ilinkFrame(List<int> body) {
  if (body.isEmpty || body.any((value) => value < 0 || value > 255)) {
    throw ArgumentError.value(body, 'body', 'iLink bytes must be 0..255');
  }
  // Checksum = 0xFF - sum(every byte incl. 55 AA and the mode byte): the rule the
  // lamp uses in its own status frames and in donandren/ilink_light. For 0x01
  // frames the 55+AA+01 prefix cancels out (0x100); for 0x03 RGB frames it does not.
  final head = [0x55, 0xaa, ...body];
  final checksum = (0xff - head.fold<int>(0, (sum, v) => sum + v)) & 0xff;
  return [...head, checksum];
}

List<List<int>> ilinkFramesForPhase(String phase) {
  switch (phase) {
    case 'idle':
    case 'end':
      return [
        ilinkFrame([0x01, 0x08, 0x05, 0x00])
      ];
    case 'start':
      return [
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, 48]),
        ilinkFrame([0x03, 0x08, 0x02, 255, 190, 120]),
      ];
    case 'focus':
      return [
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, 110]),
        ilinkFrame([0x01, 0x08, 0x09, 0x02]),
      ];
    case 'fatigue':
      return [
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, 180]),
        ilinkFrame([0x03, 0x08, 0x02, 255, 120, 40]),
      ];
    case 'recovery':
      return [
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, 90]),
        ilinkFrame([0x03, 0x08, 0x02, 80, 180, 255]),
      ];
    default:
      return const [];
  }
}

class IlinkLampFeedback {
  IlinkLampFeedback(this._bluetooth,
      {this.rescanFor = const Duration(seconds: 6),
      this.settleDelay = const Duration(seconds: 3),
      this.rescanEvery = const Duration(seconds: 30)});
  final AtlasBluetoothService _bluetooth;

  /// 연결 실패 뒤 검색 전에 기다리는 시간. Atlas 가 실패를 먼저 돌려준 뒤에도 BlueZ 가 링크를 맺고
  /// 있을 수 있어, 곧바로 검색하면 그 링크가 끊긴다(선풍기 10-24 확인).
  final Duration settleDelay;

  /// 검색은 이 간격에 한 번까지. 10-26 0.1.8 은 국면 조명이 매초 검색·연결해 램프 링크가 1→0 을
  /// 되풀이했고, 시스템 "Bluetooth device disconnected" 창이 96번 뜬 뒤 앱이 죽었다(SEGV).
  final Duration rescanEvery;

  DateTime? _lastRescanAt;

  // 백그라운드 연결 규칙([connectInBackground])에 쓰는 이번 실행의 기록.
  bool _everTried = false;
  bool _everConnected = false;
  bool _dropRetryUsed = false;

  /// 램프를 다시 찾는 검색 시간(찾으면 바로 멈춘다). 10-26 Pi 5: 페어링하지 않은 BLE 램프는
  /// 끊긴 지 30초 뒤 BlueZ 가 잊고, 그 뒤 Gatt.Connect 는 BlueZ 를 부르지도 않고 바로 실패했다
  /// (hub 명령이 56 ms 만에 bluetooth_write_failed).
  final Duration rescanFor;
  String? _address;
  GattConnection? _connection;
  DateTime? _lastOkAt;
  Object? _lastError;
  Future<void> _writes = Future.value();
  int _revision = 0;
  int _selectionGeneration = 0;
  int _connectionGeneration = 0;
  int get selectionGeneration => _selectionGeneration;
  int get connectionGeneration => _connectionGeneration;
  bool _manualOverride = false;
  bool _environmentOwnsLamp = false;
  bool get manualOverride => _manualOverride;
  void resumeAutomaticControl() {
    _revision++;
    _selectionGeneration++;
    _manualOverride = false;
    _environmentOwnsLamp = false;
  }

  int? _lastBrightness;
  bool? _poweredOn;

  bool get configured => _address?.isNotEmpty ?? false;
  String? get address => _address;
  bool get connected => _connection != null;
  DateTime? get lastOkAt => _lastOkAt;
  Object? get lastError => _lastError;

  /// 램프에 마지막으로 **성공적으로 쓴** 원시 밝기(0~255). 국면 조명(ilinkFramesForPhase)·hub set_brightness·
  /// adjust_brightness 모두 포함. 아직 안 썼거나 마지막 전원 프레임이 끔(0x05 0x00)이면 null — "꺼졌거나 모름".
  int? get currentBrightness => _poweredOn == false ? null : _lastBrightness;
  bool? get poweredOn => _poweredOn;

  /// control/result 의 `previous` — **실제 이전 상태**. 꺼져 있었으면 0(되돌리기 set_brightness 0 이 끔으로 돌아가게),
  /// 이전 값을 모르면(앱 시작 뒤 한 번도 안 씀 등) null → 필드를 넣지 않아 hub 가 되돌리기를 생략한다.
  int? get previousBrightness => _poweredOn == false ? 0 : _lastBrightness;

  void select(String address) {
    if (_address == address) return;
    _revision++;
    _selectionGeneration++;
    _address = address;
    _everTried = false;
    _everConnected = false;
    _dropRetryUsed = false;
    _connection = null;
    _lastOkAt = null;
    _lastError = null;
    _lastBrightness = null;
    _poweredOn = null;
  }

  /// iLink 0x01 프레임에서 전원·밝기를 읽어 기준값을 갱신한다(쓰기가 성공한 프레임만).
  void _track(List<int> frame) {
    if (frame.length < 6 || frame[0] != 0x55 || frame[1] != 0xaa) return;
    if (frame[2] != 0x01 || frame[3] != 0x08) return;
    switch (frame[4]) {
      case 0x05:
        _poweredOn = frame[5] != 0;
      case 0x01:
        _lastBrightness = frame[5];
    }
  }

  /// Invalidate authorization even when the same physical device is rebound.
  void invalidateUndo() {
    _revision++;
    _selectionGeneration++;
  }

  void disconnect() {
    invalidateUndo();
    _connectionGeneration++;
    _connection = null;
  }

  void forget() {
    disconnect();
    _address = null;
    _lastOkAt = null;
    _lastError = null;
  }

  /// Concurrent callers (restore, selection, phase retries) share one attempt:
  /// a second GATT Connect to the same lamp fails with "Already Connected".
  Future<bool> connect() {
    if (!configured) return Future.value(false);
    if (_connection != null) return Future.value(true);
    return _connecting ??= _connect().whenComplete(() => _connecting = null);
  }

  Future<bool>? _connecting;

  Future<bool> _connect() async {
    final address = _address!;
    final generation = _selectionGeneration;
    _everTried = true;
    try {
      final connection = await _bluetooth.connectLamp(address);
      if (_address != address || generation != _selectionGeneration) {
        return false;
      }
      _connection = connection;
      _lastError = null;
      _everConnected = true;
      _dropRetryUsed = false;
      return true;
    } catch (error) {
      if (generation != _selectionGeneration) return false;
      _connectionGeneration++;
      _connection = null;
      _lastError = error;
      return false;
    }
  }

  /// 사람이 시키지 않은 연결(국면 조명·시작 복원·유지). 이번 실행에서 아직 한 번도 시도하지 않았거나,
  /// 붙어 있다가 끊긴 뒤 첫 재시도일 때만 실제로 붙는다. 그 밖엔(연결이 실패한 뒤) BlueZ 를 건드리지 않는다.
  /// 10-26 Pi 5: 실패한 램프 연결마다 시스템 "Bluetooth device disconnected" 창이 떴고, 그 창이
  /// 쌓이면서 화면이 ATLAS 로고에 멈췄다(vc4_plane 경고 36 → 72). 다시 붙는 길은 hub 명령과 사람이
  /// 누르는 버튼뿐이다.
  Future<bool> connectInBackground() {
    if (_connection != null) return Future.value(true);
    if (!configured) return Future.value(false);
    final firstTry = !_everTried;
    final dropRetry = _everConnected && !_dropRetryUsed;
    if (!firstTry && !dropRetry) return Future.value(false);
    if (!firstTry) _dropRetryUsed = true;
    return connect();
  }

  /// 붙어 있지 않으면 붙고, 실패하면 주소를 한 번 검색해 다시 보이게 한 뒤 한 번 더 붙는다.
  /// [rediscovered]: null = 검색하지 않음, true = 찾음, false = 못 찾음.
  /// 검색은 [rescanEvery] 에 한 번까지, 실패 뒤 [settleDelay] 를 기다린 다음에만 한다.
  Future<({bool ok, bool? rediscovered})> ensureConnected(
      {num? expiresTsMs}) async {
    if (await connect()) return (ok: true, rediscovered: null);
    final last = _lastRescanAt;
    if (last != null && DateTime.now().difference(last) < rescanEvery) {
      return (ok: false, rediscovered: null);
    }
    if (!await _settle(expiresTsMs)) return (ok: false, rediscovered: null);
    _lastRescanAt = DateTime.now();
    final found = await _rediscover(expiresTsMs);
    return (ok: await connect(), rediscovered: found);
  }

  /// [settleDelay] 를 기다린다. 명령 만료 전에 검색·연결할 시간(4 s)이 안 남으면 기다리지 않고 false.
  Future<bool> _settle(num? expiresTsMs) async {
    if (expiresTsMs != null) {
      final left = expiresTsMs - DateTime.now().millisecondsSinceEpoch;
      if (left < settleDelay.inMilliseconds + 4000) return false;
    }
    if (settleDelay > Duration.zero) await Future<void>.delayed(settleDelay);
    return true;
  }

  /// 램프가 보일 때까지 최대 [rescanFor](명령이면 만료 2초 전까지) 검색한다.
  Future<bool> _rediscover(num? expiresTsMs) async {
    if (!configured) return false;
    var limit = rescanFor;
    if (expiresTsMs != null) {
      final left = Duration(
              milliseconds:
                  (expiresTsMs - DateTime.now().millisecondsSinceEpoch)
                      .round()) -
          const Duration(seconds: 2);
      if (left < limit) limit = left;
    }
    if (limit <= Duration.zero) return false;
    final wanted = _address!.toLowerCase();
    try {
      await for (final devices in _bluetooth.scanProgressive(duration: limit)) {
        if (devices.any((d) => d.address.toLowerCase() == wanted)) return true;
      }
    } catch (_) {
      // 검색이 안 되면 못 찾은 것으로 본다.
    }
    return false;
  }

  Future<void> apply(String phase) async {
    await writeFrames(ilinkFramesForPhase(phase));
  }

  Future<bool> setBrightness(int value) {
    _revision++;
    _selectionGeneration++;
    _manualOverride = true;
    final revision = _revision;
    return _queueFrames([
      ilinkFrame([0x01, 0x08, 0x05, 0x01]),
      ilinkFrame([0x01, 0x08, 0x01, value]),
    ], null, () => revision == _revision, true)
        .then((r) => r.ok);
  }

  Future<bool> writeFrames(List<List<int>> frames,
          {num? expiresTsMs,
          bool control = false,
          bool rediscover = false,
          bool Function()? stillAuthorized}) =>
      writeFramesReport(frames,
              expiresTsMs: expiresTsMs,
              control: control,
              rediscover: rediscover,
              stillAuthorized: stillAuthorized)
          .then((r) => r.ok);

  Future<({bool ok, bool? rediscovered})> writeFramesReport(
      List<List<int>> frames,
      {num? expiresTsMs,
      bool control = false,
      bool rediscover = false,
      bool Function()? stillAuthorized}) {
    if (_manualOverride || (!control && _environmentOwnsLamp)) {
      return Future.value((ok: false, rediscovered: null));
    }
    if (control) {
      _environmentOwnsLamp = true;
      _revision++;
    }
    final revision = _revision;
    return _queueFrames(
        frames,
        expiresTsMs,
        () =>
            revision == _revision &&
            !_manualOverride &&
            (stillAuthorized?.call() ?? true),
        rediscover);
  }

  Future<({bool ok, bool? rediscovered})> _queueFrames(List<List<int>> frames,
      num? expiresTsMs, bool Function() valid, bool rediscover) {
    final next = _writes
        .then((_) => _writeFrames(frames, expiresTsMs, valid, rediscover));
    _writes = next.then<void>((_) {});
    return next;
  }

  Future<({bool ok, bool? rediscovered})> _writeFrames(List<List<int>> frames,
      num? expiresTsMs, bool Function() valid, bool rediscover) async {
    bool? rediscovered;
    ({bool ok, bool? rediscovered}) failed() =>
        (ok: false, rediscovered: rediscovered);
    if (!configured || frames.isEmpty) return failed();
    bool expired() =>
        expiresTsMs != null &&
        DateTime.now().millisecondsSinceEpoch >= expiresTsMs;
    // 끊긴 GATT 세션은 재사용하지 않고 한 번만 새 세션으로 되풀이한다. 검색은 처음 한 번만.
    for (var attempt = 0; attempt < 2; attempt++) {
      if (expired() || !valid()) return failed();
      if (rediscover && attempt == 0) {
        final link = await ensureConnected(expiresTsMs: expiresTsMs);
        rediscovered = link.rediscovered;
        if (!link.ok) return failed();
      } else if (!await (rediscover ? connect() : connectInBackground())) {
        if (!rediscover) return failed();
        continue;
      }
      try {
        for (final frame in frames) {
          if (expired() || !valid()) return failed();
          await _bluetooth.writeCharacteristic(_connection!,
              serviceId: ilinkServiceUuid,
              characteristicId: ilinkWriteUuid,
              value: frame);
          _track(frame);
        }
        _lastOkAt = DateTime.now();
        _lastError = null;
        return (ok: true, rediscovered: rediscovered);
      } catch (error) {
        _connectionGeneration++;
        _connection = null;
        _lastError = error;
      }
    }
    return failed();
  }
}

/// hub 의 `adjust_brightness {"percent": p}`: 지금 밝기 대비 p % 만큼. [current] 가 null(꺼짐·모름)이면 계산 기준은 [floor].
/// 새 값 = clamp(round(기준 × (1 + p/100)), floor, 255). 올리는데 결과가 기준과 같으면(반올림·상한 전) 최소 [minStep] 만큼 올려
/// 체감되게 한다(255 면 더 못 올림). 계산 기준(base)과 새 값(actual)을 돌려준다 — 결과의 `previous` 는 이것이 아니라
/// 실제 이전 상태([IlinkLampFeedback.previousBrightness])다.
/// [minRaw] (선택 키 `min_raw`, 0~255): 계산 결과가 그보다 낮으면 거기까지 올린다 — 깨우기(percent 60, min_raw 153)처럼
/// "적어도 이만큼은 밝게" 가 필요한 명령용.
({int base, int actual}) adjustBrightness({
  required int? current,
  required int percent,
  required int floor,
  int minStep = kLampAdjustMinStep,
  int? minRaw,
}) {
  final lo = floor.clamp(0, 255);
  final base = (current ?? lo).clamp(lo, 255);
  var next = (base * (1 + percent / 100)).round().clamp(lo, 255);
  if (percent > 0 && next <= base && base < 255) {
    next = min(255, base + minStep);
  }
  if (minRaw != null && next < minRaw) next = minRaw.clamp(lo, 255);
  return (base: base, actual: next);
}

/// The FSM authorizes interventions through its gate and control commands.
/// Suggestions require acceptance; raw fatigue phases only update the UI.
class FeedbackCoordinator {
  FeedbackCoordinator({
    required this.music,
    required this.lamp,
    required this.bluetooth,
    this.fan,
    this.alerts,
    this.speech,
    this.notices,
  });
  final MusicPlayback music;
  final IlinkLampFeedback lamp;
  final AtlasBluetoothService bluetooth;

  /// `vent_fan` 실물(블루투스 선풍기). 고르지 않았으면 vent_fan 명령에 대답하지 않는다.
  final BleFanControl? fan;

  /// `speaker` 명령(speak·chime)의 소리·말·화면 문구. 없으면 speaker 명령은 speaker_unavailable 로 답한다.
  final AlertSound? alerts;
  final SpeechOutput? speech;
  final NoticeBoard? notices;

  /// TTS 가 안 될 때 화면에 문구를 띄워 두는 시간.
  static const speakFallbackShowFor = Duration(seconds: 20);

  /// adjust_brightness 의 하한(원시 0~255). 설정 `lamp_brightness_floor`, 없으면 [kLampBrightnessFloorDefault].
  int brightnessFloor = kLampBrightnessFloorDefault;
  String? _speakerAddress;
  String? get speakerAddress => _speakerAddress;
  bool _enabled = false;
  bool get enabled => _enabled;
  set enabled(bool value) {
    if (_enabled == value) return;
    _enabled = value;
    if (value) lamp.resumeAutomaticControl();
    _lastLampKey = null;
    _lastMusicKey = null;
    _acceptedAction = null;
    _lastControls.clear();
  }

  String? _lastLampKey;
  String? _lastMusicKey;
  String? _acceptedAction;
  bool _applying = false;
  DisplayState? _latest;
  final _commandResults = <String, Future<Map<String, dynamic>>>{};
  // Internal epochs only: no BLE identity is added to MQTT or logs.
  final Map<String, ({String id, int selection, int connection})>
      _lastControls = {};

  int _selection(String target) => target == 'desk_lamp'
      ? lamp.selectionGeneration
      : fan!.selectionGeneration;
  int _connection(String target) => target == 'desk_lamp'
      ? lamp.connectionGeneration
      : fan!.connectionGeneration;
  bool _connected(String target) =>
      target == 'desk_lamp' ? lamp.connected : fan!.connected;

  void _remember(Map<String, dynamic> command, int selection, int connection) {
    final target = command['target_id'] as String;
    if (command['gate'] == 'undo') {
      _lastControls.remove(target); // A completed undo cannot itself be undone.
    } else if (_selection(target) == selection &&
        _connection(target) == connection &&
        _connected(target)) {
      _lastControls[target] = (
        id: command['command_id'] as String,
        selection: selection,
        connection: connection
      );
    }
  }

  String? lastControlError;
  Object? lastAudioError;

  void selectSpeaker(String address) => _speakerAddress = address;

  /// [_speakerAddress] is the classic-Bluetooth (A2DP) address, distinct from
  /// the lamp's BLE address on combined speaker/lamp products.
  Future<void> reconcileAudioOutput() async {
    final address = _speakerAddress;
    if (address == null || !music.isPlaying) return;
    try {
      if (!await bluetooth.isA2dpConnected(address)) {
        if (music.isPlaying) {
          await music.toggle();
          _lastMusicKey = null;
        }
      }
    } catch (_) {
      // A lost Bluetooth service is treated like a disconnected output.
      if (music.isPlaying) await music.toggle();
      _lastMusicKey = null;
    }
  }

  Future<void> apply(DisplayState state) async {
    _latest = state;
    if (state.fsmState != _acceptedAction) _acceptedAction = null;
    if (!enabled || _applying) return;
    // ACTION_ENV is executed by the hub's explicit control command. Suspected
    // fatigue and unanswered suggestions must not change light or sound.
    if (state.phase == 'fatigue' &&
        (state.fsmState == 'ACTION_ENV' ||
            !state.fsmState.startsWith('ACTION_') ||
            (state.gate != 'auto' && state.fsmState != _acceptedAction))) {
      return;
    }
    await _applyOutputs(state);
  }

  Future<void> accept(DisplayState state) async {
    if (!enabled ||
        state.gate != 'suggest' ||
        !state.fsmState.startsWith('ACTION_') ||
        state.fsmState == 'ACTION_ENV') {
      return;
    }
    if (_latest != null && _latest!.fsmState != state.fsmState) return;
    _acceptedAction = state.fsmState;
    await _applyOutputs(state);
  }

  Future<void> _applyOutputs(DisplayState state) async {
    if (_applying) return;
    _applying = true;
    try {
      final key = '${state.phase}:${lamp.address}';
      if (key != _lastLampKey &&
          await lamp.writeFrames(ilinkFramesForPhase(state.phase))) {
        _lastLampKey = key;
      }
      if (state.phase == 'fatigue') {
        await _playFatigueMusic();
      } else if (state.phase == 'recovery' ||
          state.phase == 'idle' ||
          state.phase == 'end') {
        if (_lastMusicKey != state.phase) {
          if (music.isPlaying) await music.toggle();
          _lastMusicKey = state.phase;
        }
      } else {
        _lastMusicKey = null;
      }
    } catch (error) {
      lastAudioError = error;
    } finally {
      _applying = false;
    }
  }

  Future<void> _playFatigueMusic() async {
    if (_lastMusicKey == 'fatigue') return;
    final address = _speakerAddress;
    if (address != null && !await bluetooth.isA2dpConnected(address)) return;
    if (!music.isPlaying) await music.toggle();
    lastAudioError = null;
    if (music.isPlaying) _lastMusicKey = 'fatigue';
  }

  /// Hub already applied its gate and received acceptance before publishing.
  /// Cache in-flight results too, so QoS 1 duplicates never actuate twice.
  /// A vent_fan command is left unanswered while no fan is selected, so another
  /// adapter (tools/ble_fan.py bridge) can own it instead.
  Future<Map<String, dynamic>?> handleControlCommand(
      Map<String, dynamic> command) async {
    final target = command['target_id'];
    final isFan = target == 'vent_fan' &&
        fan != null &&
        (fan!.configured || command['gate'] == 'undo');
    final isSpeaker = target == 'speaker';
    if (target != 'desk_lamp' && !isFan && !isSpeaker) return null;
    final id = command['command_id'];
    if (id is! String || id.isEmpty) return null;
    final cached = _commandResults[id];
    if (cached != null) return cached;
    final result = isSpeaker
        ? _executeSpeakerCommand(command)
        : isFan
            ? _executeFanCommand(command)
            : _executeLampCommand(command);
    _commandResults[id] = result;
    if (_commandResults.length > 128) {
      _commandResults.remove(_commandResults.keys.first);
    }
    return result;
  }

  /// control/result 의 `recovery`: 기기를 검색해 다시 찾았으면 rediscovered, 찾다 못 찾았으면 not_found.
  /// 검색하지 않았으면 넣지 않는다. status 는 그대로 succeeded|failed 다.
  static Map<String, Object?> _recovery(bool? rediscovered) =>
      switch (rediscovered) {
        true => const {'recovery': 'rediscovered'},
        false => const {'recovery': 'not_found'},
        null => const {},
      };

  Map<String, dynamic> _result(Map<String, dynamic> command, String status,
      {Object? actual,
      String? error,
      String? confirmation,
      Map<String, Object?> extra = const {}}) {
    lastControlError = error;
    return {
      'command_id': command['command_id'],
      'status': status,
      if (actual != null) 'actual_value': actual,
      if (status == 'executing') 'confirmation': 'transport_only',
      if (confirmation != null) 'confirmation': confirmation,
      if (error != null) 'error_code': error,
      ...extra,
      'completed_ts_ms':
          status == 'executing' ? null : DateTime.now().millisecondsSinceEpoch,
    };
  }

  /// Shared gates never infer device acceptance from a successful GATT write.
  String? _rejection(Map<String, dynamic> command) {
    if (command['execution_mode'] == 'mock') return 'mock_command_blocked';
    if (!enabled) return 'feedback_disabled';
    if (command['gate'] == 'undo') {
      final target = command['target_id'] as String;
      final previous = _lastControls[target];
      if (previous == null || command['undo_of_command_id'] != previous.id) {
        return 'superseded_command';
      }
      if (previous.selection != _selection(target) ||
          previous.connection != _connection(target) ||
          !_connected(target)) {
        _lastControls.remove(target);
        return 'superseded_command';
      }
    }
    final expires = command['expires_ts_ms'];
    if (expires is! num ||
        !expires.isFinite ||
        !['auto', 'suggest', 'undo', 'notice'].contains(command['gate']) ||
        command['requires_confirmation'] != false) {
      return 'invalid_command';
    }
    if (expires <= DateTime.now().millisecondsSinceEpoch) return 'expired';
    return null;
  }

  Map<String, dynamic> _refused(Map<String, dynamic> command, String reason) =>
      _result(
          command,
          ['mock_command_blocked', 'superseded_command'].contains(reason)
              ? 'cancelled'
              : 'failed',
          error: reason);

  Future<Map<String, dynamic>> _executeLampCommand(
      Map<String, dynamic> command) async {
    final rejected = _rejection(command);
    if (rejected != null) return _refused(command, rejected);
    if (lamp.manualOverride) {
      return _result(command, 'cancelled', error: 'manual_override');
    }
    final expires = command['expires_ts_ms'] as num;
    if (!lamp.configured) {
      return _result(command, 'failed', error: 'lamp_not_configured');
    }
    final value = command['value'];
    final List<List<int>> frames;
    // adjust_brightness: 결과에 previous(기준)·actual(새 값)을 싣는다. actual_value 도 새 값.
    var extra = const <String, Object?>{};
    if (command['operation'] == 'set_brightness' &&
        value is int &&
        value >= 0 &&
        value <= 255) {
      frames = [
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, value])
      ];
    } else if (command['operation'] == 'adjust_brightness' &&
        value is Map &&
        value['percent'] is num &&
        (value['percent'] as num).isFinite) {
      final minRaw = value['min_raw'];
      final adjusted = adjustBrightness(
        current: lamp.currentBrightness,
        percent: (value['percent'] as num).round(),
        floor: brightnessFloor,
        minRaw: minRaw is int && minRaw >= 0 && minRaw <= 255 ? minRaw : null,
      );
      // previous = 실제 이전 상태(꺼짐 0 · 모름이면 생략). 계산 기준(하한)과 다르다.
      final previous = lamp.previousBrightness;
      extra = {
        if (previous != null) 'previous': previous,
        'actual': adjusted.actual,
      };
      frames = [
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, adjusted.actual])
      ];
    } else if (command['operation'] == 'set_power' &&
        (value == 'on' || value == 'off')) {
      frames = [
        ilinkFrame([0x01, 0x08, 0x05, value == 'on' ? 1 : 0])
      ];
    } else {
      return _result(command, 'failed',
          error: 'unsupported_operation_or_value');
    }
    final selection = lamp.selectionGeneration;
    final connection = lamp.connectionGeneration;
    final write = await lamp.writeFramesReport(frames,
        rediscover: true,
        expiresTsMs: expires,
        control: true,
        stillAuthorized: () =>
            lamp.selectionGeneration == selection &&
            _rejection(command) == null);
    final recovery = _recovery(write.rediscovered);
    if (!write.ok) {
      final rejection = _rejection(command);
      if (rejection != null) return _refused(command, rejection);
      if (lamp.manualOverride) {
        return _result(command, 'cancelled', error: 'manual_override');
      }
      if (!enabled) {
        return _result(command, 'cancelled', error: 'feedback_disabled');
      }
      return _result(command, 'failed',
          error: expires <= DateTime.now().millisecondsSinceEpoch
              ? 'expired'
              : write.rediscovered == false
                  ? 'device_not_found'
                  : 'bluetooth_write_failed',
          extra: recovery);
    }
    _remember(command, selection, connection);
    extra = {...extra, ...recovery};
    // Sound failure must not turn a successful lamp command into a failure.
    if (command['gate'] != 'undo' && _latest?.phase == 'fatigue') {
      try {
        await _playFatigueMusic();
      } catch (error) {
        lastAudioError = error;
      }
    }
    return _result(command, 'executing',
        error: 'device_state_unconfirmed', extra: extra);
  }

  Future<Map<String, dynamic>> _executeFanCommand(
      Map<String, dynamic> command) async {
    final rejected = _rejection(command);
    if (rejected != null) return _refused(command, rejected);
    final expires = command['expires_ts_ms'] as num;
    final operation = command['operation'];
    final value = command['value'];
    final frame = fanFrame(operation, value);
    if (frame == null) {
      return _result(command, 'failed',
          error: 'unsupported_operation_or_value');
    }
    final selection = fan!.selectionGeneration;
    final connection = fan!.connectionGeneration;
    final sent = await fan!.send(frame,
        control: true,
        expiresTsMs: expires,
        stillAuthorized: () => _rejection(command) == null);
    final recovery = _recovery(fan!.lastRediscovered);
    if (!sent.written) {
      final rejection = _rejection(command);
      if (rejection != null) return _refused(command, rejection);
      return _result(command, 'failed',
          error: 'bluetooth_write_failed', extra: recovery);
    }
    _remember(command, selection, connection);
    final state = sent.state;
    // The firmware has no independent per-command acceptance ACK. Readback
    // confirms reported state, never motor motion or a fabricated requested value.
    if (state == null) {
      return _result(command, 'executing',
          error: 'device_state_unconfirmed', extra: recovery);
    }
    if (!state.matches(operation, value)) {
      return _result(command, 'failed',
          actual: state.actualFor(operation),
          error: 'state_mismatch',
          confirmation: 'state_readback',
          extra: recovery);
    }
    return _result(command, 'succeeded',
        actual: state.actualFor(operation),
        confirmation: 'state_readback',
        extra: recovery);
  }

  /// speak: TTS 로 읽고, 안 되면 알림음 + 화면 문구로 대신한 뒤 failed·tts_unavailable 과 함께
  /// 무엇으로 대신했는지(fallback: chime_text | text) 답한다. chime {pattern, volume}: 알림음 하나.
  Future<Map<String, dynamic>> _executeSpeakerCommand(
      Map<String, dynamic> command) async {
    final rejected = _rejection(command);
    if (rejected != null) return _result(command, 'failed', error: rejected);
    final operation = command['operation'];
    final value = command['value'];
    final alerts = this.alerts;
    if (operation == 'chime') {
      if (alerts == null) {
        return _result(command, 'failed', error: 'speaker_unavailable');
      }
      final pattern = value is Map ? value['pattern'] : null;
      final volume = value is Map ? value['volume'] : null;
      try {
        await alerts.play(chimeAssetFor(pattern),
            volume: chimeVolumeFor(volume));
      } on Object {
        return _result(command, 'failed', error: 'audio_failed');
      }
      return _result(command, 'succeeded', actual: {
        'pattern': pattern?.toString() ?? 'double',
        if (volume != null) 'volume': volume.toString(),
      });
    }
    if (operation != 'speak') {
      return _result(command, 'failed',
          error: 'unsupported_operation_or_value');
    }
    final text = value is Map ? value['text'] : null;
    if (text is! String || text.trim().isEmpty) {
      return _result(command, 'failed',
          error: 'unsupported_operation_or_value');
    }
    final lang = value is Map && value['lang'] is String
        ? value['lang'] as String
        : 'ko';
    final speech = this.speech;
    if (speech != null) {
      try {
        await speech.speak(text.trim(), lang: lang);
        return _result(command, 'succeeded', actual: {'mode': 'tts'});
      } on Object {
        // 이 보드에는 TTS 가 없다 — 아래로 넘어가 소리와 글로 알린다.
      }
    }
    notices?.show(ScreenNotice(
        text: text.trim(),
        shownAt: DateTime.now(),
        showFor: speakFallbackShowFor,
        kind: 'speak'));
    var chimed = false;
    if (alerts != null) {
      try {
        await alerts.play(kChimeSoft);
        chimed = true;
      } on Object {
        // 소리도 안 나면 화면 문구만 남는다.
      }
    }
    return _result(command, 'failed',
        error: 'tts_unavailable',
        extra: {'fallback': chimed ? 'chime_text' : 'text'});
  }
}
