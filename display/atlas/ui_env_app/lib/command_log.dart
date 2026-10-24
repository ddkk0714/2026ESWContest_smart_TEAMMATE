/// 개발자 모드의 명령 로그 — hub 가 내리는 제어 명령과 그 결과, 제안 카드, 사용자 피드백을
/// 받은 순서대로 한 줄씩 쌓는다.
///
/// 네 토픽을 본다(`docs/mqtt-topics.md`):
///   - `deskmate/control/cmd`        → `CMD <target> <operation> <value> (<gate>, id <6자리>)`
///   - `deskmate/control/result`     → `RESULT <id> <status> [error_code|actual_value] (<node>)`
///   - `deskmate/interaction/request`→ `REQUEST <kind> <action|prompt_code> "<설명>" <expires_in_s> s`
///   - `deskmate/feedback/user`      → `FEEDBACK <verdict> [→ corrected_state] (req <id>)`
///
/// 블루투스 램프·선풍기가 붙어 있지 않아도 hub 가 **무엇을 켜라고 했는지**는 CMD 줄로 보이고,
/// 앱이 낸 `failed lamp_not_configured` 같은 결과도 RESULT 줄로 같이 보인다 — 그게 이 로그의 목적이다.
/// 메시지의 해석(어떤 명령을 실행할지)은 바꾸지 않는다. 받은 것을 글로 남길 뿐이다.
library;

import 'package:flutter/foundation.dart';

enum CommandLogKind { cmd, result, request, feedback }

class CommandLogEntry {
  const CommandLogEntry(this.at, this.kind, this.text, {this.ok});

  final DateTime at;
  final CommandLogKind kind;
  final String text;

  /// RESULT 줄의 성공 여부. succeeded → true, failed·timeout·cancelled → false, 진행 중·다른 종류 → null.
  final bool? ok;

  String get kindLabel => switch (kind) {
        CommandLogKind.cmd => 'CMD',
        CommandLogKind.result => 'RESULT',
        CommandLogKind.request => 'REQUEST',
        CommandLogKind.feedback => 'FEEDBACK',
      };

  String get time =>
      '${_two(at.hour)}:${_two(at.minute)}:${_two(at.second)}';

  /// 화면·테스트가 보는 한 줄. 예: `12:03:41 CMD desk_lamp set_brightness 230 (auto, id 9f3c1a)`.
  String get line => '$time $kindLabel $text';

  static String _two(int v) => v.toString().padLeft(2, '0');

  @override
  String toString() => line;
}

class CommandLog extends ChangeNotifier {
  CommandLog({this.maxEntries = 200});

  /// 보존하는 줄 수. 넘치면 오래된 줄부터 버린다.
  final int maxEntries;
  final List<CommandLogEntry> _entries = [];

  List<CommandLogEntry> get entries => List.unmodifiable(_entries);
  bool get isEmpty => _entries.isEmpty;
  int get length => _entries.length;

  void add(CommandLogEntry entry) {
    _entries.add(entry);
    if (_entries.length > maxEntries) {
      _entries.removeRange(0, _entries.length - maxEntries);
    }
    notifyListeners();
  }

  void clear() {
    if (_entries.isEmpty) return;
    _entries.clear();
    notifyListeners();
  }

  /// MQTT 로 받은 envelope 한 건을 줄로 바꿔 쌓는다. 모르는 토픽이나 `data` 가 없는 메시지는 무시한다.
  /// [now] 는 테스트용. 시각은 받은 시각(Pi 5 시계) — Pi 4 `ts` 와 섞으면 두 시계 차이가 줄에 섞인다.
  void addMessage(String topic, Map<String, dynamic> envelope, {DateTime? now}) {
    final entry = entryFor(topic, envelope, now: now);
    if (entry != null) add(entry);
  }

  static const topicCmd = 'deskmate/control/cmd';
  static const topicResult = 'deskmate/control/result';
  static const topicRequest = 'deskmate/interaction/request';
  static const topicFeedback = 'deskmate/feedback/user';

  /// 토픽별 한 줄 텍스트. 로그에 넣을 게 아니면 null.
  static CommandLogEntry? entryFor(String topic, Map<String, dynamic> envelope,
      {DateTime? now}) {
    final raw = envelope['data'];
    if (raw is! Map) return null;
    final data = raw.map((k, v) => MapEntry(k.toString(), v));
    final at = now ?? DateTime.now();
    final node = envelope['node']?.toString();
    switch (topic) {
      case topicCmd:
        final target = _s(data['target_id']);
        final op = _s(data['operation']);
        final value = data['value'] == null ? '' : ' ${data['value']}';
        final gate = _s(data['gate']);
        final id = _shortId(data['command_id']);
        final cause = data['cause'];
        final causeText = cause == null ? '' : ' $cause';
        return CommandLogEntry(at, CommandLogKind.cmd,
            '$target $op$value ($gate, id $id)$causeText');
      case topicResult:
        final id = _shortId(data['command_id']);
        final status = _s(data['status']);
        final error = data['error_code'];
        final actual = data['actual_value'];
        final detail = error != null
            ? ' $error'
            : actual != null
                ? ' $actual'
                : '';
        final who = node == null || node.isEmpty ? '' : ' ($node)';
        final bool? ok = switch (status) {
          'succeeded' => true,
          'failed' || 'timeout' || 'cancelled' => false,
          _ => null,
        };
        return CommandLogEntry(
            at, CommandLogKind.result, '$id $status$detail$who',
            ok: ok);
      case topicRequest:
        final kind = _s(data['kind']);
        final action = data['action'] ?? data['prompt_code'];
        final explanation = data['explanation']?.toString().trim();
        final arm = data['arm_label'] ?? data['arm'];
        final minutes = data['ventilate_minutes'];
        final expires = data['expires_in_s'];
        final parts = <String>[
          kind,
          if (action != null) action.toString(),
          if (explanation != null && explanation.isNotEmpty)
            '"${_clip(explanation, 60)}"',
          if (arm != null) 'arm=$arm',
          if (minutes != null) '$minutes 분',
          if (expires != null) '$expires s',
        ];
        return CommandLogEntry(at, CommandLogKind.request, parts.join(' '));
      case topicFeedback:
        if (data['kind'] == 'personalization') {
          return CommandLogEntry(at, CommandLogKind.feedback,
              'personalization ${_s(data['action'])}');
        }
        final verdict = _s(data['verdict']);
        final corrected = data['corrected_state'];
        final note = data['note']?.toString().trim();
        final req = data['request_id'];
        final parts = <String>[
          verdict,
          if (corrected != null) '→ $corrected',
          if (note != null && note.isNotEmpty) '"${_clip(note, 40)}"',
          if (req != null) '(req ${_shortId(req)})',
        ];
        return CommandLogEntry(at, CommandLogKind.feedback, parts.join(' '));
      default:
        return null;
    }
  }

  static String _s(Object? v) => v == null ? '?' : v.toString();

  /// command_id·request_id 는 길어서 앞 6 자만. 짧으면 그대로.
  static String _shortId(Object? v) {
    final text = v?.toString() ?? '?';
    return text.length > 6 ? text.substring(0, 6) : text;
  }

  static String _clip(String text, int max) =>
      text.length <= max ? text : '${text.substring(0, max)}…';
}
