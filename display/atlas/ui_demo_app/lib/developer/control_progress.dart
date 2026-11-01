// 제어 진행 — 명령 하나(command_id)마다 "왜 → 무엇을 → 어떻게 됐나" 한 줄(V30, 2026-10-31, 개발자 화면 전용).
//
// 출처 둘.
//   - hub 기록: Pi 4 `GET http://<host>:8765/api/control-log?limit=50`(hub feat/control-log c1b30ff — control_log.py 레코드를
//     C++ 링버퍼가 오래된 것부터 JSON 배열로). 쿨다운 생략·제안 대기·시간 초과·되돌림까지 hub 가 아는 그대로다.
//   - MQTT 추정: 그 엔드포인트가 없거나(404 — 보드 hub 0453ab6) 닿지 않으면, 이 앱이 받는 `control/cmd`·`control/result`
//     (+ state/phase 의 개인화 arm)로 같은 표를 메모리에서 만든다(devview 와 같은 방식). hub 가 안 보낸 명령(쿨다운 생략 등)은 없다.
// 폴링은 화면이 떠 있을 때만([start]·[stop]) — 보드에 쓸데없는 요청을 보내지 않는다.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../live/live_link.dart';

const controlLogPort = 8765;
const controlProgressLimit = 50;

/// hub dispatcher `result_timeout_sec`(15 s) — MQTT 추정에서 결과가 이만큼 없으면 시간 초과로 본다.
const mqttResultTimeout = Duration(seconds: 15);

/// hub undo_window_sec(60 s) — 이 안에 같은 기기로 온 되돌리기(gate undo) 명령을 직전 자동 실행의 되돌림으로 묶는다.
const mqttUndoWindow = Duration(seconds: 60);

const hubLogMissing = 'hub 에 제어 로그 없음(IPK 갱신 필요)';

enum ControlProgressSource { hub, mqtt }

@immutable
class ControlRecord {
  const ControlRecord({
    required this.commandId,
    required this.fields,
    this.ts,
  });

  final String commandId;

  /// 이 레코드의 모든 필드(hub 레코드 그대로, MQTT 추정이면 같은 이름으로 채운 것). 펼치기에 그대로 보인다.
  final Map<String, Object?> fields;
  final DateTime? ts;

  static DateTime? _time(Object? seconds) => seconds is num
      ? DateTime.fromMillisecondsSinceEpoch((seconds * 1000).round())
      : null;

  /// hub control_log.py 레코드 한 건. command_id 가 없으면 null.
  static ControlRecord? fromHub(Object? json) {
    if (json is! Map) return null;
    final fields = json.map((k, v) => MapEntry(k.toString(), v));
    final id = fields['command_id'];
    if (id is! String || id.isEmpty) return null;
    return ControlRecord(
        commandId: id,
        fields: fields,
        ts: _time(fields['created_ts'] ?? fields['sent_ts'] ?? fields['ts']));
  }

  String? _s(String key) {
    final v = fields[key];
    return v == null || (v is String && v.isEmpty) ? null : v.toString();
  }

  String get status => _s('status') ?? '-';
  String? get errorCode => _s('error_code');
  bool get isUndo => fields['undo'] == true;

  int? get latencyMs {
    final v = fields['latency_ms'];
    return v is num ? v.round() : null;
  }

  /// 왜: state · cause · gate · arm.
  String get why => [
        _s('state'),
        _s('cause'),
        _s('gate'),
        _s('arm'),
      ].whereType<String>().join(' · ');

  /// 무엇을: 기기 동작 값.
  String get what {
    final value = fields['value'];
    final shown = value == null
        ? ''
        : value is String
            ? ' $value'
            : ' ${jsonEncode(value)}';
    return '${_s('target_id') ?? '?'} ${_s('operation') ?? '?'}$shown';
  }

  /// 결과 한 마디(배지).
  String get statusKo => switch (status) {
        'succeeded' => '성공',
        'failed' => '실패${errorCode == null ? '' : ' · $errorCode'}',
        'timeout' => '시간 초과',
        'skipped' => '생략${_s('reason') == null ? '' : ' · ${_s('reason')}'}',
        'cancelled' => '취소',
        'awaiting_user' => '제안 대기',
        'accepted' || 'executing' || 'sent' => '진행 중',
        _ => status,
      };

  /// 되돌림 한 마디. 없으면 null.
  String? get undoKo {
    final by = _s('undone_by');
    final of = _s('undo_of');
    if (by != null) return '↩ 되돌려짐 ($by)';
    if (of != null) return '↩ 되돌리기 ($of)';
    if (isUndo) return '↩ 되돌리기';
    return null;
  }

  ControlRecord copyWith(Map<String, Object?> changes) => ControlRecord(
      commandId: commandId, fields: {...fields, ...changes}, ts: ts);
}

class ControlLogUnavailable implements Exception {
  const ControlLogUnavailable(this.reason, {this.missing = false});
  final String reason;

  /// true = hub 가 엔드포인트를 모른다(404, 옛 hub). false = 닿지 않음·응답 형식.
  final bool missing;
  @override
  String toString() => reason;
}

/// hub 제어 로그를 가져오는 쪽. 테스트는 가짜로 바꾼다.
abstract class ControlLogFetcher {
  /// 오래된 것 먼저인 레코드 배열. 못 가져오면 [ControlLogUnavailable].
  Future<List<Object?>> fetch(int limit);
  void close() {}
}

class HttpControlLogFetcher implements ControlLogFetcher {
  HttpControlLogFetcher(this.host,
      {this.port = controlLogPort,
      Duration timeout = const Duration(seconds: 3)})
      : _timeout = timeout,
        _client = HttpClient()..connectionTimeout = timeout;

  final String host;
  final int port;
  final Duration _timeout;
  final HttpClient _client;

  @override
  Future<List<Object?>> fetch(int limit) async {
    final uri = Uri(
        scheme: 'http',
        host: host,
        port: port,
        path: '/api/control-log',
        queryParameters: {'limit': '$limit'});
    final HttpClientResponse response;
    final String body;
    try {
      final request = await _client.getUrl(uri).timeout(_timeout);
      request.headers.set(HttpHeaders.cacheControlHeader, 'no-store');
      response = await request.close().timeout(_timeout);
      body = await utf8.decodeStream(response).timeout(_timeout);
    } on TimeoutException {
      throw const ControlLogUnavailable('시간 초과');
    } on SocketException catch (e) {
      throw ControlLogUnavailable(e.osError?.message ?? e.message);
    } on HttpException catch (e) {
      throw ControlLogUnavailable(e.message);
    }
    if (response.statusCode == HttpStatus.notFound) {
      throw const ControlLogUnavailable('404', missing: true);
    }
    if (response.statusCode != HttpStatus.ok) {
      throw ControlLogUnavailable('HTTP ${response.statusCode}');
    }
    try {
      final decoded = jsonDecode(body);
      if (decoded is List) return decoded;
    } on FormatException {
      // 아래에서
    }
    throw const ControlLogUnavailable('응답 형식');
  }

  @override
  void close() => _client.close(force: true);
}

/// 제어 진행 표. hub 기록이 있으면 그것을, 없으면 MQTT 추정을 보여 준다.
class ControlProgress extends ChangeNotifier {
  ControlProgress({
    ControlLogFetcher Function(String host)? fetcherFactory,
    this.interval = const Duration(seconds: 4),
    this.limit = controlProgressLimit,
    DateTime Function()? clock,
  })  : _fetcherFactory = fetcherFactory ?? HttpControlLogFetcher.new,
        _clock = clock ?? DateTime.now;

  final ControlLogFetcher Function(String host) _fetcherFactory;
  final Duration interval;
  final int limit;
  final DateTime Function() _clock;

  ControlProgressSource source = ControlProgressSource.mqtt;

  /// hub 기록을 못 쓰는 이유 한 줄([hubLogMissing] …). hub 기록을 쓰는 중이거나 아직 안 물어봤으면 null.
  String? hubProblem;
  DateTime? hubAt;
  List<ControlRecord> _hub = const [];
  final Map<String, ControlRecord> _mqtt = {};
  String? _arm;
  String? _armSource;

  String? _host;
  ControlLogFetcher? _fetcher;
  Timer? _timer;
  bool _polling = false;
  bool _disposed = false;

  /// 최신이 위, [limit] 건.
  List<ControlRecord> get records {
    if (source == ControlProgressSource.hub) return _hub;
    final now = _clock();
    final list = _mqtt.values.map((r) => _withTimeout(r, now)).toList()
      ..sort((a, b) => (b.ts ?? now).compareTo(a.ts ?? now));
    return list.take(limit).toList(growable: false);
  }

  ControlRecord _withTimeout(ControlRecord r, DateTime now) {
    final sent = r.ts;
    final open = const {'sent', 'executing', 'accepted'}.contains(r.status);
    if (!open || sent == null || now.difference(sent) < mqttResultTimeout) {
      return r;
    }
    return r.copyWith({'status': 'timeout'});
  }

  /// 화면이 뜨면 — host 로 바로 한 번 묻고 [interval] 마다 다시 묻는다.
  void start(String? host) {
    if (host != _host) {
      _fetcher?.close();
      _fetcher = host == null || host.isEmpty ? null : _fetcherFactory(host);
      _host = host;
      _hub = const [];
      hubProblem = null;
    }
    _timer?.cancel();
    if (_fetcher == null) {
      _useMqtt('책상 주소 미설정');
      return;
    }
    unawaited(pollOnce());
    _timer = Timer.periodic(interval, (_) => unawaited(pollOnce()));
  }

  /// 화면이 닫히면.
  void stop() {
    _timer?.cancel();
    _timer = null;
  }

  Future<void> pollOnce() async {
    final fetcher = _fetcher;
    if (fetcher == null || _polling || _disposed) return;
    _polling = true;
    try {
      final raw = await fetcher.fetch(limit);
      if (_disposed || !identical(fetcher, _fetcher)) return;
      _hub = raw
          .map(ControlRecord.fromHub)
          .whereType<ControlRecord>()
          .toList()
          .reversed
          .take(limit)
          .toList(growable: false);
      source = ControlProgressSource.hub;
      hubProblem = null;
      hubAt = _clock();
    } on ControlLogUnavailable catch (e) {
      if (_disposed || !identical(fetcher, _fetcher)) return;
      _useMqtt(e.missing ? null : e.reason);
    } finally {
      _polling = false;
    }
    _notify();
  }

  void _useMqtt(String? detail) {
    source = ControlProgressSource.mqtt;
    hubProblem = detail == null ? hubLogMissing : '$hubLogMissing · $detail';
    _notify();
  }

  /// 이 앱이 받은 MQTT 메시지(retain 제외). MQTT 추정 표를 늘 같이 만들어 두어 hub 가 끊겨도 바로 넘어간다.
  void addMessage(String topic, Map<String, dynamic> envelope) {
    final data = envelope['data'];
    if (data is! Map) return;
    final now = _clock();
    switch (topic) {
      case LiveLink.stateTopic:
        final summary = data['sensor_summary'];
        final pers = summary is Map ? summary['personalize'] : null;
        _arm = pers is Map ? pers['arm']?.toString() : null;
        _armSource = pers is Map ? pers['source']?.toString() : null;
        return;
      case LiveLink.commandTopic:
        final id = data['command_id']?.toString() ?? '';
        if (id.isEmpty || _mqtt.containsKey(id)) return; // QoS 1 중복
        final gate = data['gate']?.toString();
        final target = data['target_id']?.toString();
        final fields = <String, Object?>{
          'command_id': id,
          'source': 'mqtt',
          'status': 'sent',
          'state': data['origin_state'],
          'cause': data['cause'],
          'gate': gate,
          if (_armSource == 'bandit' && gate != 'undo' && _arm != null)
            'arm': _arm,
          'target_id': target,
          'operation': data['operation'],
          'value': data['value'],
          'sent_ts': now.millisecondsSinceEpoch / 1000,
          'undo': gate == 'undo',
        };
        if (gate == 'undo') {
          // 같은 기기의 직전 자동 실행(60 s 안)을 되돌린 것으로 묶는다
          final undone = _mqtt.values
              .where((r) =>
                  !r.isUndo &&
                  r.fields['target_id'] == target &&
                  r.fields['undone_by'] == null &&
                  r.ts != null &&
                  now.difference(r.ts!) <= mqttUndoWindow)
              .fold<ControlRecord?>(
                  null,
                  (best, r) =>
                      best == null || r.ts!.isAfter(best.ts!) ? r : best);
          if (undone != null) {
            fields['undo_of'] = undone.commandId;
            _mqtt[undone.commandId] = undone.copyWith({'undone_by': id});
          }
        }
        _mqtt[id] = ControlRecord(commandId: id, fields: fields, ts: now);
        _trim();
      case LiveLink.resultTopic:
        final id = data['command_id']?.toString() ?? '';
        final record = _mqtt[id];
        if (record == null) return;
        final sent = record.ts;
        _mqtt[id] = record.copyWith({
          'status': data['status'] ?? record.status,
          'completed_ts': now.millisecondsSinceEpoch / 1000,
          if (sent != null) 'latency_ms': now.difference(sent).inMilliseconds,
          for (final k in const [
            'error_code',
            'node',
            'previous',
            'actual',
            'actual_value',
            'recovery'
          ])
            if (data[k] != null) k: data[k],
        });
      default:
        return;
    }
    if (source == ControlProgressSource.mqtt) _notify();
  }

  void _trim() {
    while (_mqtt.length > limit * 2) {
      _mqtt.remove(_mqtt.keys.first);
    }
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _timer?.cancel();
    _fetcher?.close();
    super.dispose();
  }
}
