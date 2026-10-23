import 'bluetooth_service.dart';
import 'display_state.dart';
import 'fan_control.dart';
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

  bool get configured => _address?.isNotEmpty ?? false;
  String? get address => _address;
  bool get connected => _connection != null;
  DateTime? get lastOkAt => _lastOkAt;
  Object? get lastError => _lastError;

  void select(String address) {
    if (_address == address) return;
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
      _connection = await _bluetooth.connectLamp(_address!);
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

  Future<bool> setBrightness(int value) => writeFrames([
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, value]),
      ]);

  /// Phase feedback and explicit commands must not interleave BLE frames.
  Future<bool> writeFrames(List<List<int>> frames, {num? expiresTsMs}) {
    final next = _writes.then((_) => _writeFrames(frames, expiresTsMs));
    _writes = next.then<void>((_) {});
    return next;
  }

  Future<bool> _writeFrames(List<List<int>> frames, num? expiresTsMs) async {
    if (!configured || frames.isEmpty) return false;
    bool expired() =>
        expiresTsMs != null &&
        DateTime.now().millisecondsSinceEpoch >= expiresTsMs;
    // 끊긴 GATT 세션은 재사용하지 않고 한 번만 새 세션으로 되풀이한다.
    for (var attempt = 0; attempt < 2; attempt++) {
      if (expired()) return false;
      if (!await connect()) continue;
      try {
        for (final frame in frames) {
          if (expired()) return false;
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
    this.fan,
  });
  final MusicPlayback music;
  final IlinkLampFeedback lamp;
  final AtlasBluetoothService bluetooth;

  /// `vent_fan` 실물(블루투스 선풍기). 고르지 않았으면 vent_fan 명령에 대답하지 않는다.
  final BleFanControl? fan;
  String? _speakerAddress;
  String? get speakerAddress => _speakerAddress;
  bool _enabled = false;
  bool get enabled => _enabled;
  set enabled(bool value) {
    if (_enabled == value) return;
    _enabled = value;
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
    final isFan = target == 'vent_fan' && (fan?.configured ?? false);
    if (target != 'desk_lamp' && !isFan) return null;
    final id = command['command_id'];
    if (id is! String || id.isEmpty) return null;
    final cached = _commandResults[id];
    if (cached != null) return cached;
    final result =
        isFan ? _executeFanCommand(command) : _executeLampCommand(command);
    _commandResults[id] = result;
    if (_commandResults.length > 128) {
      _commandResults.remove(_commandResults.keys.first);
    }
    return result;
  }

  Map<String, dynamic> _result(Map<String, dynamic> command, String status,
      {Object? actual, String? error}) {
    lastControlError = error;
    return {
      'command_id': command['command_id'],
      'status': status,
      if (status == 'succeeded' || actual != null) 'actual_value': actual,
      if (error != null) 'error_code': error,
      'completed_ts_ms': DateTime.now().millisecondsSinceEpoch,
    };
  }

  /// Checks every target shares: the feedback switch, gate, confirmation, expiry.
  String? _rejection(Map<String, dynamic> command) {
    if (!enabled) return 'feedback_disabled';
    final expires = command['expires_ts_ms'];
    if (expires is! num ||
        !expires.isFinite ||
        !['auto', 'suggest', 'undo'].contains(command['gate']) ||
        command['requires_confirmation'] != false) {
      return 'invalid_command';
    }
    if (expires <= DateTime.now().millisecondsSinceEpoch) return 'expired';
    return null;
  }

  Future<Map<String, dynamic>> _executeLampCommand(
      Map<String, dynamic> command) async {
    final rejected = _rejection(command);
    if (rejected != null) return _result(command, 'failed', error: rejected);
    final expires = command['expires_ts_ms'] as num;
    if (!lamp.configured) {
      return _result(command, 'failed', error: 'lamp_not_configured');
    }
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
      return _result(command, 'failed', error: 'unsupported_operation_or_value');
    }
    if (!await lamp.writeFrames(frames, expiresTsMs: expires)) {
      return _result(command, 'failed',
          error: expires <= DateTime.now().millisecondsSinceEpoch
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
    return _result(command, 'succeeded', actual: value);
  }

  /// set_power on|off · set_level 0~3 · set_speed 0~100 · set_mode normal|natural · set_timer 초.
  Future<Map<String, dynamic>> _executeFanCommand(
      Map<String, dynamic> command) async {
    final rejected = _rejection(command);
    if (rejected != null) return _result(command, 'failed', error: rejected);
    final expires = command['expires_ts_ms'] as num;
    final operation = command['operation'];
    final value = command['value'];
    final frame = fanFrame(operation, value);
    if (frame == null) {
      return _result(command, 'failed', error: 'unsupported_operation_or_value');
    }
    final sent = await fan!.send(frame, expiresTsMs: expires);
    if (!sent.written) {
      return _result(command, 'failed',
          error: expires <= DateTime.now().millisecondsSinceEpoch
              ? 'expired'
              : 'bluetooth_write_failed');
    }
    final state = sent.state;
    // 상태를 못 읽었으면 램프처럼 쓰기 완료를 성공으로 본다.
    if (state == null) return _result(command, 'succeeded', actual: value);
    if (!state.matches(operation, value)) {
      return _result(command, 'failed',
          actual: state.actualFor(operation), error: 'state_mismatch');
    }
    return _result(command, 'succeeded', actual: state.actualFor(operation));
  }
}
