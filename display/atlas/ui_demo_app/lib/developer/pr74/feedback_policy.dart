import 'dart:async';
import 'dart:math' show min;

import 'bluetooth_service.dart';
import 'diag.dart';
import 'display_state.dart';
import 'fan_control.dart';
import 'lamp_defaults.dart';
import 'music_playback.dart';
import 'music_state.dart';
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

/// 피곤할 때 조명 색: 앱의 '빛의 온도'(0 따뜻하게 … 1 시원하게) → iLink RGB.
/// 따뜻한 주황(255,120,40) → 하얀빛(255,230,200) → 시원한 푸른빛(170,205,255) 사이를 잇는다.
List<int> ilinkRgbForWarmth(double warmth) {
  const warm = [255, 120, 40], neutral = [255, 230, 200], cool = [170, 205, 255];
  final w = warmth.isFinite ? warmth.clamp(0.0, 1.0) : 0.5;
  final (a, b, t) = w < .5 ? (warm, neutral, w / .5) : (neutral, cool, (w - .5) / .5);
  return [for (var i = 0; i < 3; i++) (a[i] + (b[i] - a[i]) * t).round()];
}

class IlinkLampFeedback {
  IlinkLampFeedback(this._bluetooth,
      {this.rescanFor = const Duration(seconds: 6),
      this.rescanAtLeast = const Duration(seconds: 3),
      this.settleDelay = const Duration(seconds: 3),
      this.rescanEvery = const Duration(seconds: 30),
      this.connectAttempts = 3,
      this.writeRetryDelay = const Duration(seconds: 1)});
  final AtlasBluetoothService _bluetooth;

  /// V41: 쓰기가 실패했는데 링크가 살아 있을 때, 같은 링크로 다시 쓰기 전에 기다리는 시간.
  final Duration writeRetryDelay;

  /// V38: 선풍기([BleFanControl.connectAttempts])와 같은 시도 수 — [ensureConnected] 가 최대 이만큼 붙어 본다.
  final int connectAttempts;

  /// 다시 찾을 때 주소가 보여도 이만큼은 검색한다([rediscoverDevice], V35).
  final Duration rescanAtLeast;

  /// 연결 실패 뒤 검색 전에 기다리는 시간. Atlas 가 실패를 먼저 돌려준 뒤에도 BlueZ 가 링크를 맺고
  /// 있을 수 있어, 곧바로 검색하면 그 링크가 끊긴다(선풍기 10-24 확인).
  final Duration settleDelay;

  /// 검색은 이 간격에 한 번까지. 10-26 0.1.8 은 국면 조명이 매초 검색·연결해 램프 링크가 1→0 을
  /// 되풀이했고, 시스템 "Bluetooth device disconnected" 창이 96번 뜬 뒤 앱이 죽었다(SEGV).
  final Duration rescanEvery;

  DateTime? _lastRescanAt;

  /// V44: 특성을 몰라 링크를 끊고 새로 붙은 때. 국면 조명은 [rescanEvery] 안에 다시 끊지 않는다.
  DateTime? _unknownReconnectAt;

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
  int? _lastBrightness;
  bool? _poweredOn;

  int _generation = 0;

  Future<void> _release(GattConnection? connection) async {
    if (connection == null) return;
    try {
      await _bluetooth.disconnectGatt(connection);
    } catch (_) {}
  }

  void forget() {
    final old = _connection;
    _generation++;
    _address = null;
    _connection = null;
    _connecting = null;
    _lastBrightness = null;
    _poweredOn = null;
    _everTried = _everConnected = _dropRetryUsed = false;
    unawaited(_release(old));
  }

  bool get configured => _address?.isNotEmpty ?? false;
  String? get address => _address;
  bool get connected => _connection != null;

  /// 지금 맺은 GATT 링크(clientId 를 앱 데이터에 남겨 다음 실행이 정리하게, V38). 없으면 null.
  GattConnection? get connection => _connection;
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
    final old = _connection;
    _generation++;
    _connecting = null;
    _address = address;
    _connection = null;
    _lastOkAt = null;
    _lastError = null;
    _lastBrightness = null;
    _poweredOn = null;
    _lastRescanAt = null;
    _unknownReconnectAt = null;
    _everTried = _everConnected = _dropRetryUsed = false;
    unawaited(_release(old));
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

  /// Concurrent callers (restore, selection, phase retries) share one attempt:
  /// a second GATT Connect to the same lamp fails with "Already Connected".
  Future<bool> connect() {
    if (!configured) return Future.value(false);
    if (_connection != null) return Future.value(true);
    final generation = _generation;
    return _connecting ??= _connect().whenComplete(() {
      if (generation == _generation) _connecting = null;
    });
  }

  Future<bool>? _connecting;

  Future<bool> _connect() async {
    final generation = _generation;
    final address = _address!;
    _everTried = true;
    try {
      final connection = await _bluetooth.connectLamp(address, role: '램프');
      if (generation != _generation) {
        await _release(connection);
        return false;
      }
      _connection = connection;
      _lastError = null;
      _everConnected = true;
      _dropRetryUsed = false;
      return true;
    } catch (error) {
      if (generation != _generation) return false;
      _connection = null;
      _lastError = error;
      return false;
    }
  }

  /// 사람이 시키지 않은 연결(국면 조명·시작 복원·유지). 이번 실행에서 아직 한 번도 시도하지 않았거나,
  /// 붙어 있다가 끊긴 뒤 첫 재시도일 때만 실제로 붙는다. 그 밖엔(연결이 실패한 뒤) BlueZ 를 건드리지 않는다.
  /// 10-26 Pi 5: 실패한 램프 연결마다 시스템 "Bluetooth device disconnected" 창이 떴고, 그 창이
  /// 쌓이면서 화면이 ATLAS 로고에 멈췄다(vc4_plane 경고 36 → 72). 다시 붙는 길은 hub 명령과 사람이
  /// 누르는 버튼뿐이다(ui_env_app 0.1.12, 6b8d4ac).
  Future<bool> connectInBackground() {
    if (_connection != null) return Future.value(true);
    if (!configured) return Future.value(false);
    final firstTry = !_everTried;
    final dropRetry = _everConnected && !_dropRetryUsed;
    if (!firstTry && !dropRetry) return Future.value(false);
    if (!firstTry) _dropRetryUsed = true;
    return connect();
  }

  /// 붙어 있지 않으면 선풍기와 같은 틀([connectWithRetry], V38)로 붙는다 — 최대 [connectAttempts] 번,
  /// 실패 사이 [settleDelay], 두 번째 실패 뒤엔 주소를 검색해 다시 보이게 한 다음.
  /// [rediscovered]: null = 검색하지 않음, true = 찾음, false = 못 찾음.
  /// 검색은 [rescanEvery] 에 한 번까지(그 안이면 검색 없이 다음 시도), 명령 만료 전에 기다리고 다시 붙을
  /// 시간([settleDelay] + 4 s)이 안 남으면 더 시도하지 않는다.
  Future<({bool ok, bool? rediscovered})> ensureConnected(
      {num? expiresTsMs}) async {
    final generation = _generation;
    bool? rediscovered;
    bool noTimeLeft() {
      if (expiresTsMs == null) return false;
      final left = expiresTsMs - DateTime.now().millisecondsSinceEpoch;
      return left < settleDelay.inMilliseconds + 4000;
    }

    final ok = await connectWithRetry(connect,
        attempts: connectAttempts,
        retryDelay: settleDelay,
        rediscover: () async {
          final last = _lastRescanAt;
          if (last != null && DateTime.now().difference(last) < rescanEvery) {
            return;
          }
          _lastRescanAt = DateTime.now();
          final found = await _rediscover(expiresTsMs);
          rediscovered = (rediscovered ?? false) || found;
        },
        // 이전 실행의 링크가 남아 거절되면(Already connected) 검색·재시도로는 안 풀린다
        stop: () =>
            generation != _generation ||
            noTimeLeft() ||
            isAlreadyConnectedError(_lastError));
    return (ok: ok && generation == _generation, rediscovered: rediscovered);
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
    return rediscoverDevice(_bluetooth, _address!,
        limit: limit, atLeast: rescanAtLeast);
  }

  Future<void> apply(String phase) async {
    await writeFrames(ilinkFramesForPhase(phase));
  }

  /// 블루투스 탭의 밝기 시험 버튼 — 사람이 누른 것이라 필요하면 램프를 다시 찾는다.
  Future<bool> setBrightness(int value) => writeFrames([
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, value]),
      ], rediscover: true);

  /// Phase feedback and explicit commands must not interleave BLE frames.
  /// [rediscover] 는 hub 명령·사람이 누른 버튼만 켠다. 국면 조명은 검색하지 않고, 연결도
  /// [connectInBackground] 규칙을 따른다.
  Future<bool> writeFrames(List<List<int>> frames,
          {num? expiresTsMs, bool rediscover = false}) =>
      writeFramesReport(frames,
              expiresTsMs: expiresTsMs, rediscover: rediscover)
          .then((r) => r.ok);

  /// [writeFrames] 와 같고, 램프를 다시 찾았는지도 돌려준다([ensureConnected] 의 rediscovered).
  Future<({bool ok, bool? rediscovered})> writeFramesReport(
      List<List<int>> frames,
      {num? expiresTsMs,
      bool rediscover = false}) {
    final generation = _generation;
    final next = _writes
        .then((_) => _writeFrames(frames, expiresTsMs, rediscover, generation));
    _writes = next.then<void>((_) {});
    return next;
  }

  Future<({bool ok, bool? rediscovered})> _writeFrames(List<List<int>> frames,
      num? expiresTsMs, bool rediscover, int generation) async {
    bool? rediscovered;
    ({bool ok, bool? rediscovered}) failed() =>
        (ok: false, rediscovered: rediscovered);
    if (!configured || frames.isEmpty || generation != _generation) {
      return failed();
    }
    bool expired() =>
        expiresTsMs != null &&
        DateTime.now().millisecondsSinceEpoch >= expiresTsMs;
    // false = 만료·기기 바뀜으로 그만둠. 실패는 던진다.
    Future<bool> send() async {
      for (final frame in frames) {
        if (expired() || generation != _generation) return false;
        await _bluetooth.writeCharacteristic(_connection!,
            serviceId: ilinkServiceUuid,
            characteristicId: ilinkWriteUuid,
            value: frame);
        if (generation != _generation) return false;
        _track(frame);
      }
      _lastOkAt = DateTime.now();
      _lastError = null;
      return true;
    }

    // V44: 이 호출에서 같은 링크로 서비스를 다시 검색했나, 우리가 링크를 끊어 새로 붙여야 하나.
    var servicesRefreshed = false;
    var reconnect = false;
    // 끊긴 GATT 세션은 재사용하지 않고 한 번만 새 세션으로 되풀이한다. 검색은 처음 한 번만.
    var attempts = 2;
    for (var attempt = 0; attempt < attempts; attempt++) {
      if (expired() || generation != _generation) return failed();
      if (rediscover && attempt == 0) {
        final link = await ensureConnected(expiresTsMs: expiresTsMs);
        rediscovered = link.rediscovered;
        if (!link.ok) return failed();
      } else if (!await (rediscover || reconnect
          ? connect()
          : connectInBackground())) {
        if (!rediscover) return failed();
        continue;
      }
      if (generation != _generation) return failed();
      Object error;
      try {
        if (!await send()) return failed();
        return (ok: true, rediscovered: rediscovered);
      } catch (e) {
        error = e;
      }
      if (generation != _generation) return failed();
      _lastError = error;
      final address = _address!;
      // V44: 링크는 살아 있는데 BlueZ 가 특성을 모른다(11-02 14:41 Pi 5, V43). V41 처럼 같은 링크로 다시 써도
      // 10 s 마다 같은 InvalidArgs 만 되풀이했다. 같은 링크로 서비스를 다시 검색해 한 번 더 쓰고, 그래도 모르면 이때만
      // 링크를 끊고 새로 붙는다(새 Connect 가 서비스 검색부터 다시 한다).
      if (isUnknownCharacteristicError(error) && !servicesRefreshed) {
        servicesRefreshed = true;
        diag.write('램프 쓰기 실패 $address (특성 모름): $error → 같은 링크로 서비스 다시 검색');
        try {
          final refreshed = await _bluetooth.refreshGattServices(address);
          if (generation != _generation) return failed();
          _connection = refreshed;
          if (!await send()) return failed();
          return (ok: true, rediscovered: rediscovered);
        } catch (e) {
          if (generation != _generation) return failed();
          error = e;
          _lastError = e;
        }
      }
      if (isUnknownCharacteristicError(error)) {
        if (reconnect) {
          // 새 링크도 모르면 더 끊지 않는다 — 끊을 때마다 시스템 창이 뜬다(10-26).
          diag.write('램프 쓰기 실패 $address (새로 붙은 링크도 특성 모름): $error');
          return failed();
        }
        final now = DateTime.now();
        final last = _unknownReconnectAt;
        if (!rediscover && last != null && now.difference(last) < rescanEvery) {
          diag.write('램프 쓰기 실패 $address (다시 검색해도 특성 모름): $error → '
              '방금 새로 붙었으니 이번엔 그만둠');
          return failed();
        }
        _unknownReconnectAt = now;
        diag.write('램프 쓰기 실패 $address (다시 검색해도 특성 모름): $error → 링크 끊고 새로 붙기');
        reconnect = true;
        attempts = attempt + 2; // 새로 붙어 쓰기 한 번은 남긴다
        final old = _connection;
        _connection = null;
        await _release(old);
        continue;
      }
      // V41: 쓰기가 실패해도 링크가 살아 있으면 끊지 않는다 — 잠깐 뒤 같은 링크로 한 번 더 쓴다.
      // 11-02 Pi 5(V40): 시작 복원 직후 국면 조명 쓰기가 같은 칩 스피커(A2DP)가 붙는 순간 실패했고, 그때 앱이
      // 방금 맺은 램프 링크를 Disconnect 해 버렸다. A2DP 가 붙은 뒤 램프는 광고를 멈춰 다시 붙지 못했다.
      // 13:10·13:27 처럼 링크를 유지하면 그 뒤 쓰기는 됐다. 링크가 정말 사라졌을 때만 정리하고 새로 붙는다.
      final linked = await _bluetooth.isGattLinked(address);
      if (generation != _generation) return failed();
      diag.write('램프 쓰기 실패 $address (${linked ? '링크 유지' : '링크 없음'}): $error');
      if (linked) {
        if (writeRetryDelay > Duration.zero) {
          await Future<void>.delayed(writeRetryDelay);
        }
        continue;
      }
      final old = _connection;
      _connection = null;
      await _release(old);
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

  /// 지금 사람의 '피곤할 때 조명 색'(RGB). hub 가 피로 단계에서 무드등을 밝힐 때 이 색을 같이 쓴다. null 이면 색은 그대로.
  List<int>? fatigueRgb;
  void setFatigueColor(double warmth) => fatigueRgb = ilinkRgbForWarmth(warmth);
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
    _lampFailKey = null;
  }

  /// V44: 같은 국면 조명이 이만큼 잇달아 실패하면 국면이 바뀌거나 램프에 다른 쓰기가 성공할 때까지 쉰다.
  /// 11-02 14:41 Pi 5(V43): 상태가 올 때마다(10 s) 같은 쓰기를 되풀이해 실패 로그가 끝없이 쌓였다.
  static const phaseLampAttempts = 3;

  String? _lastLampKey;
  String? _lampFailKey;
  int _lampFailures = 0;
  DateTime? _lampGaveUpAt;

  bool _phaseLampAllowed(String key) {
    if (key != _lampFailKey || _lampFailures < phaseLampAttempts) {
      return true;
    }
    final ok = lamp.lastOkAt;
    if (ok != null && ok.isAfter(_lampGaveUpAt!)) {
      _lampFailKey = null;
      return true;
    }
    return false;
  }

  void _phaseLampFailed(String key) {
    if (key != _lampFailKey) {
      _lampFailKey = key;
      _lampFailures = 0;
    }
    if (++_lampFailures == phaseLampAttempts) {
      _lampGaveUpAt = DateTime.now();
      diag.write('국면 조명 램프 쓰기 $phaseLampAttempts번 실패 — 국면이 바뀌거나 램프에 다시 써질 때까지 쉼');
    }
  }
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
    // 피로 단계의 기기는 hub 명령(control/cmd)만 바꾼다. 앱이 따로 국면 조명·음악을 켜면
    // 사람마다 다른 도움(A 음악 · B 조명)이 섞이고 자세 안내 카드에도 기기가 켜진다(11-04 결선 대본 점검).
    if (state.phase == 'fatigue') return;
    if (_applying) return;
    _applying = true;
    try {
      final key = '${state.phase}:${lamp.address}';
      if (key != _lastLampKey && _phaseLampAllowed(key)) {
        if (await lamp.writeFrames(ilinkFramesForPhase(state.phase))) {
          _lastLampKey = key;
          _lampFailKey = null;
        } else {
          _phaseLampFailed(key);
        }
      }
      if (state.phase == 'recovery' ||
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

  /// Hub already applied its gate and received acceptance before publishing.
  /// Cache in-flight results too, so QoS 1 duplicates never actuate twice.
  /// A vent_fan command is left unanswered while no fan is selected, so another
  /// adapter (tools/ble_fan.py bridge) can own it instead.
  Future<Map<String, dynamic>?> handleControlCommand(
      Map<String, dynamic> command) async {
    final target = command['target_id'];
    final isFan = target == 'vent_fan' && (fan?.configured ?? false);
    final isSpeaker = target == 'speaker';
    final isMusic = target == 'music';
    if (target != 'desk_lamp' && !isFan && !isSpeaker && !isMusic) {
      return null;
    }
    final id = command['command_id'];
    if (id is! String || id.isEmpty) return null;
    final cached = _commandResults[id];
    if (cached != null) return cached;
    final result = isSpeaker
        ? _executeSpeakerCommand(command)
        : isMusic
            ? _executeMusicCommand(command)
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
      {Object? actual, String? error, Map<String, Object?> extra = const {}}) {
    lastControlError = error;
    return {
      'command_id': command['command_id'],
      'status': status,
      if (status == 'succeeded' || actual != null) 'actual_value': actual,
      if (error != null) 'error_code': error,
      ...extra,
      'completed_ts_ms': DateTime.now().millisecondsSinceEpoch,
    };
  }

  /// Checks every target shares: the feedback switch, gate, confirmation, expiry.
  String? _rejection(Map<String, dynamic> command) {
    if (!enabled) return 'feedback_disabled';
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

  Future<Map<String, dynamic>> _executeLampCommand(
      Map<String, dynamic> command) async {
    final rejected = _rejection(command);
    if (rejected != null) return _result(command, 'failed', error: rejected);
    final expires = command['expires_ts_ms'] as num;
    if (!lamp.configured) {
      return _result(command, 'failed', error: 'lamp_not_configured');
    }
    final value = command['value'];
    List<List<int>> frames;
    // adjust_brightness: 결과에 previous(기준)·actual(새 값)을 싣는다. actual_value 도 새 값.
    var actual = value;
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
      actual = adjusted.actual;
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
      // V43: 끄기 직전 밝기(꺼져 있었으면 0, 모르면 생략) — hub 가 자리 비움에 끈 무드등을 다시 앉았을 때
      // set_brightness previous 로 되돌린다. adjust_brightness 의 previous 와 같은 뜻.
      final previous = lamp.previousBrightness;
      if (previous != null) extra = {'previous': previous};
      frames = [
        ilinkFrame([0x01, 0x08, 0x05, value == 'on' ? 1 : 0])
      ];
    } else {
      return _result(command, 'failed',
          error: 'unsupported_operation_or_value');
    }
    // 피로 단계에 hub 가 무드등을 켜거나 밝히면 이 사람의 '피곤할 때 색'으로 칠한다(되돌리기·끄기는 제외).
    final rgb = fatigueRgb;
    if (rgb != null &&
        command['gate'] != 'undo' &&
        _latest?.phase == 'fatigue' &&
        !(command['operation'] == 'set_power' && value == 'off')) {
      frames = [...frames, ilinkFrame([0x03, 0x08, 0x02, ...rgb])];
    }
    final write = await lamp.writeFramesReport(frames,
        expiresTsMs: expires, rediscover: true);
    final recovery = _recovery(write.rediscovered);
    if (!write.ok) {
      return _result(command, 'failed',
          error: expires <= DateTime.now().millisecondsSinceEpoch
              ? 'expired'
              : write.rediscovered == false
                  ? 'device_not_found'
                  : 'bluetooth_write_failed',
          extra: recovery);
    }
    extra = {...extra, ...recovery};
    return _result(command, 'succeeded', actual: actual, extra: extra);
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
      return _result(command, 'failed',
          error: 'unsupported_operation_or_value');
    }
    final sent = await fan!.send(frame, expiresTsMs: expires);
    // V43: 쓰기 전 상태 {power, speed, level} — hub 가 자리 비움에 끈 선풍기를 다시 앉았을 때 되돌린다.
    // send 가 같은 연결로 쓰기 직전에 본 것(마지막 상태가 없으면 한 번 읽음). 못 읽었으면 싣지 않는다.
    final before = fan!.stateBeforeSend;
    final recovery = {
      ..._recovery(fan!.lastRediscovered),
      if (before != null) 'previous': before.asPrevious,
    };
    if (!sent.written) {
      return _result(command, 'failed',
          error: expires <= DateTime.now().millisecondsSinceEpoch
              ? 'expired'
              : fan!.lastRediscovered == false
                  ? 'device_not_found'
                  : 'bluetooth_write_failed',
          extra: recovery);
    }
    final state = sent.state;
    // 상태를 못 읽었으면 램프처럼 쓰기 완료를 성공으로 본다.
    if (state == null) {
      return _result(command, 'succeeded', actual: value, extra: recovery);
    }
    if (!state.matches(operation, value)) {
      return _result(command, 'failed',
          actual: state.actualFor(operation),
          error: 'state_mismatch',
          extra: recovery);
    }
    return _result(command, 'succeeded',
        actual: state.actualFor(operation), extra: recovery);
  }

  /// 마지막으로 실행한 hub 음악 명령(V32). `display/audio` 에 실어 hub 가 그 뒤의 변화를 사용자 조작으로 가린다.
  String? lastMusicCommandId;

  /// V32 target `music` · operation `set_music` {on, volume?, track?}. 결과 actual_value·actual = 실행 뒤 상태,
  /// previous = 실행 전 상태({on, volume, track, track_id}) — hub 는 되돌리기 때 previous 를 그대로 value 로 보낸다(gate undo).
  /// 스피커를 골랐는데 A2DP 가 끊겼으면 켜지 않고 speaker_not_connected. 실행 뒤 이 국면 동안은 앱 자체 음악
  /// 켜기·끄기(_applyOutputs)가 hub 의 결정을 덮지 않는다.
  Future<Map<String, dynamic>> _executeMusicCommand(
      Map<String, dynamic> command) async {
    final rejected = _rejection(command);
    if (rejected != null) return _result(command, 'failed', error: rejected);
    final value = command['value'];
    final on = value is Map ? value['on'] : null;
    final volume = value is Map ? value['volume'] : null;
    final track = value is Map ? value['track'] : null;
    if (command['operation'] != 'set_music' ||
        on is! bool ||
        (volume != null &&
            (volume is! num ||
                !volume.isFinite ||
                volume < 0 ||
                volume > 1)) ||
        (track != null &&
            (track is! int || track < 0 || track >= classicalPlaylist.length))) {
      return _result(command, 'failed',
          error: 'unsupported_operation_or_value');
    }
    final previous = musicStateOf(music);
    final address = _speakerAddress;
    if (on && address != null && !await bluetooth.isA2dpConnected(address)) {
      return _result(command, 'failed',
          error: 'speaker_not_connected', extra: {'previous': previous});
    }
    try {
      if (volume != null) await music.setVolume(volume.toDouble());
      if (track != null && track != music.selectedTrack) {
        await music.selectTrack(track);
      }
      if (on != music.isPlaying) await music.toggle();
    } on Object catch (error) {
      lastAudioError = error;
      return _result(command, 'failed',
          actual: musicStateOf(music),
          error: 'audio_failed',
          extra: {'previous': previous});
    }
    lastMusicCommandId = command['command_id'] as String;
    _lastMusicKey = _latest?.phase;
    final actual = musicStateOf(music);
    if (actual['on'] != on) {
      return _result(command, 'failed',
          actual: actual,
          error: 'state_mismatch',
          extra: {'previous': previous, 'actual': actual});
    }
    lastAudioError = null;
    return _result(command, 'succeeded',
        actual: actual, extra: {'previous': previous, 'actual': actual});
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
