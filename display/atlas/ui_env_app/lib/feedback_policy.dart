import 'bluetooth_service.dart';
import 'display_state.dart';
import 'music_playback.dart';

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
  IlinkLampFeedback(this._bluetooth);
  final AtlasBluetoothService _bluetooth;
  String? _address;
  GattConnection? _connection;
  DateTime? _lastOkAt;
  Object? _lastError;
  Future<void> _writes = Future.value();
  int _revision = 0;
  bool _manualOverride = false;
  bool _environmentOwnsLamp = false;
  bool get manualOverride => _manualOverride;
  void resumeAutomaticControl() {
    _revision++;
    _manualOverride = false;
    _environmentOwnsLamp = false;
  }

  bool get configured => _address?.isNotEmpty ?? false;
  String? get address => _address;
  bool get connected => _connection != null;
  DateTime? get lastOkAt => _lastOkAt;
  Object? get lastError => _lastError;

  void select(String address) {
    if (_address == address) return;
    _revision++;
    _address = address;
    _connection = null;
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
    try {
      final address = _address!;
      final connection = await _bluetooth.connectLamp(address);
      if (_address != address) return false;
      _connection = connection;
      _lastError = null;
      return true;
    } catch (error) {
      _connection = null;
      _lastError = error;
      return false;
    }
  }

  Future<void> apply(String phase) async {
    await writeFrames(ilinkFramesForPhase(phase));
  }

  Future<bool> setBrightness(int value) {
    _revision++;
    _manualOverride = true;
    final revision = _revision;
    return _queueFrames([
      ilinkFrame([0x01, 0x08, 0x05, 0x01]),
      ilinkFrame([0x01, 0x08, 0x01, value]),
    ], null, () => revision == _revision);
  }

  /// Phase feedback and explicit commands must not interleave BLE frames.
  Future<bool> writeFrames(List<List<int>> frames,
      {num? expiresTsMs,
      bool control = false,
      bool Function()? stillAuthorized}) {
    if (_manualOverride || (!control && _environmentOwnsLamp)) {
      return Future.value(false);
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
            (stillAuthorized?.call() ?? true));
  }

  Future<bool> _queueFrames(
      List<List<int>> frames, num? expiresTsMs, bool Function() valid) {
    final next = _writes.then((_) => _writeFrames(frames, expiresTsMs, valid));
    _writes = next.then<void>((_) {});
    return next;
  }

  Future<bool> _writeFrames(
      List<List<int>> frames, num? expiresTsMs, bool Function() valid) async {
    if (!configured || frames.isEmpty) return false;
    bool expired() =>
        expiresTsMs != null &&
        DateTime.now().millisecondsSinceEpoch >= expiresTsMs;
    // 끊긴 GATT 세션은 재사용하지 않고 한 번만 새 세션으로 되풀이한다.
    for (var attempt = 0; attempt < 2; attempt++) {
      if (expired() || !valid()) return false;
      if (!await connect()) continue;
      try {
        for (final frame in frames) {
          if (expired() || !valid()) return false;
          await _bluetooth.writeCharacteristic(_connection!,
              serviceId: ilinkServiceUuid,
              characteristicId: ilinkWriteUuid,
              value: frame);
        }
        _lastOkAt = DateTime.now();
        _lastError = null;
        return true;
      } catch (error) {
        _connection = null;
        _lastError = error;
      }
    }
    return false;
  }
}

/// The FSM authorizes interventions through its gate and control commands.
/// Suggestions require acceptance; raw fatigue phases only update the UI.
class FeedbackCoordinator {
  FeedbackCoordinator({
    required this.music,
    required this.lamp,
    required this.bluetooth,
  });
  final MusicPlayback music;
  final IlinkLampFeedback lamp;
  final AtlasBluetoothService bluetooth;
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
  }

  String? _lastLampKey;
  String? _lastMusicKey;
  String? _acceptedAction;
  bool _applying = false;
  DisplayState? _latest;
  final _commandResults = <String, Future<Map<String, dynamic>>>{};
  String? _lastControlId;
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
  Future<Map<String, dynamic>?> handleControlCommand(
      Map<String, dynamic> command) async {
    if (command['target_id'] != 'desk_lamp') return null;
    final id = command['command_id'];
    if (id is! String || id.isEmpty) return null;
    final cached = _commandResults[id];
    if (cached != null) return cached;
    final result = _executeLampCommand(command);
    _commandResults[id] = result;
    if (_commandResults.length > 128) {
      _commandResults.remove(_commandResults.keys.first);
    }
    return result;
  }

  Future<Map<String, dynamic>> _executeLampCommand(
      Map<String, dynamic> command) async {
    Map<String, dynamic> result(String status, [String? error]) {
      lastControlError = error;
      return {
        'command_id': command['command_id'],
        'status': status,
        // A GATT write acknowledges transport, not the physical lamp state.
        if (status == 'executing') 'confirmation': 'transport_only',
        if (error != null) 'error_code': error,
        'completed_ts_ms': status == 'executing'
            ? null
            : DateTime.now().millisecondsSinceEpoch,
      };
    }

    if (command['execution_mode'] == 'mock') {
      return result('cancelled', 'mock_command_blocked');
    }
    if (!enabled) return result('failed', 'feedback_disabled');
    if (lamp.manualOverride) return result('cancelled', 'manual_override');
    if (command['gate'] == 'undo' &&
        (command['undo_of_command_id'] == null ||
            command['undo_of_command_id'] != _lastControlId)) {
      return result('cancelled', 'superseded_command');
    }
    final expires = command['expires_ts_ms'];
    if (expires is! num ||
        !expires.isFinite ||
        !['auto', 'suggest', 'undo'].contains(command['gate']) ||
        command['requires_confirmation'] != false) {
      return result('failed', 'invalid_command');
    }
    if (expires <= DateTime.now().millisecondsSinceEpoch) {
      return result('failed', 'expired');
    }
    if (!lamp.configured) return result('failed', 'lamp_not_configured');
    final value = command['value'];
    final List<List<int>> frames;
    if (command['operation'] == 'set_brightness' &&
        value is int &&
        value >= 0 &&
        value <= 255) {
      frames = [
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, value])
      ];
    } else if (command['operation'] == 'set_power' &&
        (value == 'on' || value == 'off')) {
      frames = [
        ilinkFrame([0x01, 0x08, 0x05, value == 'on' ? 1 : 0])
      ];
    } else {
      return result('failed', 'unsupported_operation_or_value');
    }
    if (!await lamp.writeFrames(frames,
        expiresTsMs: expires, control: true, stillAuthorized: () => enabled)) {
      if (lamp.manualOverride) return result('cancelled', 'manual_override');
      if (!enabled) return result('cancelled', 'feedback_disabled');
      return result(
          'failed',
          expires <= DateTime.now().millisecondsSinceEpoch
              ? 'expired'
              : 'bluetooth_write_failed');
    }
    // Sound failure must not turn a successful lamp command into a failure.
    if (command['gate'] != 'undo' && _latest?.phase == 'fatigue') {
      try {
        await _playFatigueMusic();
      } catch (error) {
        lastAudioError = error;
      }
    }
    _lastControlId = command['command_id'] as String;
    return result('executing', 'device_state_unconfirmed');
  }
}
