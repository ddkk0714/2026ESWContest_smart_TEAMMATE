/// Optional control contract. No network or Flutter dependency; also exercised
/// against envelopes exported by the real Hub/FSM + Mock MQTT scenario.
library;

import 'dart:convert';

Map<String, dynamic> controlMap(Object? value) => value is Map
    ? value.map((key, value) => MapEntry(key.toString(), value))
    : const {};

class ControlCommandState {
  ControlCommandState(Map<String, dynamic> data)
      : id = data['command_id'] is String ? data['command_id'] as String : '',
        target = data['target'] is String ? data['target'] as String : '',
        operation = data['op'] is String ? data['op'] as String : '',
        value = data['value'],
        status = data['status']?.toString() ?? 'unknown',
        stateConfirmed = data['state_confirmed'] == true,
        error = data['error_code']?.toString(),
        reason = data['reason']?.toString() ?? '';
  final String id, target, operation, status, reason;
  final Object? value;
  final String? error;
  final bool stateConfirmed;
  bool get valid =>
      id.isNotEmpty &&
      target.isNotEmpty &&
      operation.isNotEmpty &&
      (value is String ||
          value is bool ||
          (value is num && (value as num).isFinite));
  List<Object?> get identity => [id, target, operation, value, reason];
  String get description => '$target · $operation = ${jsonEncode(value)}';
  String get statusLabel => switch (status) {
        'accepted' => '요청 접수 · 동작 미확인',
        'executing' => '실행 중 · 동작 미확인',
        'succeeded' => stateConfirmed ? '상태 확인 완료' : '처리 응답 · 기기 상태 미확인',
        'failed' => '실패',
        'timeout' => '시간 초과 · 결과 미확인',
        'cancelled' => '취소',
        'skipped' => '실행하지 않음',
        _ => '결과 미확인',
      };
}

class ControlState {
  ControlState._(this.active, this.mode, this.episode, this.commands,
      this.advice, this.valid);
  final bool active;
  final String mode;
  final Map<String, dynamic> episode;
  final List<ControlCommandState> commands;
  final List<Map<String, dynamic>> advice;
  final bool valid;
  bool get isMock => mode == 'mock';
  bool get awaitingUser => active && episode['awaiting_user'] == true;
  bool get adviceOnly => commands.isEmpty && advice.isNotEmpty;
  bool get ventilationOnly =>
      adviceOnly &&
      advice.every((item) => item['kind'] == 'manual_ventilation');
  String get cause => episode['cause']?.toString() ?? '';
  String get outcome => episode['outcome']?.toString() ?? '';
  String get signature => jsonEncode([
        mode,
        episode['state'],
        cause,
        episode['gate'],
        for (final command in commands) command.identity,
        for (final item in advice)
          [
            item['kind'],
            item['rule'],
            item['sensor'],
            item['role'],
            item['reason']
          ],
      ]);
  String get label => isMock ? 'MOCK · 모의 제어 (실제 가전 아님)' : '가전 제어';
  String get outcomeLabel => switch (outcome) {
        'rejected' => '사용자 거절 · 실행하지 않음',
        'expired' => '제안 만료 · 실행하지 않음',
        'advised' => '안내 확인 · 가전 제어 없음',
        'skipped' => '실행하지 않음',
        _ => '서버 제어 결과',
      };
  static ControlState? parse(Object? value) {
    final root = controlMap(value);
    if (root.isEmpty) return null;
    final active = root['active'] == true;
    final ep = active ? root : controlMap(root['last_episode']);
    final rawCommands = ep['commands'];
    final rawAdvice = ep['advice'] ?? const [];
    final commands = rawCommands is List
        ? rawCommands
            .map((v) => ControlCommandState(controlMap(v)))
            .toList(growable: false)
        : <ControlCommandState>[];
    final advice = rawAdvice is List
        ? rawAdvice
            .map(
                (value) => Map<String, dynamic>.unmodifiable(controlMap(value)))
            .toList(growable: false)
        : <Map<String, dynamic>>[];
    final valid = ep['state'] == 'ACTION_ENV' &&
        ep['cause'] is String &&
        ['mock', 'live'].contains(root['execution_mode']) &&
        rawCommands is List &&
        rawAdvice is List &&
        commands.every((c) => c.valid) &&
        commands.map((c) => c.id).toSet().length == commands.length &&
        advice.every((a) =>
            ['manual_ventilation', 'manual_environment_adjustment']
                .contains(a['kind']) &&
            ['rule', 'sensor', 'role', 'reason']
                .every((key) => a[key] is String)) &&
        (commands.isNotEmpty || advice.isNotEmpty);
    return ControlState._(
        active,
        root['execution_mode']?.toString() ?? 'unknown',
        Map.unmodifiable(ep),
        List.unmodifiable(commands),
        List.unmodifiable(advice),
        valid);
  }
}

class ControlProposal {
  ControlProposal._(
      this.requestId, this.bootId, this.createdAt, this.expiresAt, this.plan);
  final String requestId, bootId;
  final DateTime createdAt, expiresAt;
  final ControlState plan;
  static ControlProposal? parse(Map<String, dynamic> envelope) {
    final data = controlMap(envelope['data']);
    final plan = ControlState.parse(data['control_plan']);
    final ts = envelope['ts'];
    final expiry = data['expires_in_s'];
    if (envelope['schema_version'] != '1.0' ||
        data['kind'] != 'env_suggest' ||
        data['request_id'] is! String ||
        (data['request_id'] as String).isEmpty ||
        envelope['boot_id'] is! String ||
        (envelope['boot_id'] as String).isEmpty ||
        ts is! num ||
        !ts.isFinite ||
        ts <= 0 ||
        expiry is! num ||
        !expiry.isFinite ||
        expiry <= 0 ||
        expiry > 3600 ||
        plan == null ||
        !plan.valid ||
        !plan.awaitingUser ||
        plan.episode['gate'] != 'suggest') {
      return null;
    }
    final created = DateTime.fromMillisecondsSinceEpoch((ts * 1000).round());
    return ControlProposal._(
        data['request_id'] as String,
        envelope['boot_id'] as String,
        created,
        created.add(Duration(milliseconds: (expiry * 1000).round())),
        plan);
  }

  bool matches(
          {required ControlState? current,
          required String? hubBootId,
          required DateTime stateTime,
          required DateTime now}) =>
      now.isBefore(expiresAt) &&
      !createdAt.isAfter(now.add(const Duration(seconds: 2))) &&
      now.difference(stateTime).inMilliseconds >= -2000 &&
      now.difference(stateTime) <= const Duration(seconds: 30) &&
      hubBootId == bootId &&
      current != null &&
      current.valid &&
      current.awaitingUser &&
      current.signature == plan.signature;
}
