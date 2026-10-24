// hub 의 확인 질문(`deskmate/interaction/request`) → 화면 중앙 제안 카드 문구.
//
// kind 와 문구는 ui_env_app dashboard_view.dart(suggestionTitle·ventWhyText)와 같은 뜻으로 맞추되
// 이 앱의 차분한 말투로 옮겼다. 응답은 accept/reject 두 가지뿐이다.
library;

import '../pr56/display_state.dart';
import 'payload.dart';

class LiveRequest {
  const LiveRequest({
    required this.requestId,
    required this.kind,
    required this.receivedAt,
    this.promptCode,
    this.explanation,
    this.minutes,
    this.why = const [],
    this.expiresInS,
    this.arm,
    this.armLabel,
  });

  final String requestId;

  /// posture_suggest · env_suggest · break_suggest · checkin_suggest · vent_suggest …
  final String kind;
  final DateTime receivedAt;
  final String? promptCode;

  /// 왜 이 제안인지 한 줄(`data.explanation`, 개인화 정책·CO₂ 창문 규칙). 있으면 카드 본문이 된다.
  final String? explanation;

  /// vent_suggest 의 `data.minutes`(옛 hub 는 `ventilate_minutes`).
  final int? minutes;
  final List<String> why;
  final int? expiresInS;
  final String? arm;
  final String? armLabel;

  static LiveRequest? fromData(Map<String, dynamic> data, DateTime now) {
    final id = data['request_id'];
    if (id is! String || id.isEmpty) return null;
    final minutes = data['minutes'] ?? data['ventilate_minutes'];
    final expires = data['expires_in_s'];
    String? text(Object? v) =>
        v is String && v.trim().isNotEmpty ? v.trim() : null;
    return LiveRequest(
      requestId: id,
      kind: data['kind']?.toString() ?? 'unknown',
      receivedAt: now,
      promptCode: text(data['prompt_code']),
      explanation: text(data['explanation']),
      minutes: minutes is num && minutes > 0 ? minutes.round() : null,
      why: (data['why'] as List? ?? const [])
          .map((e) => e.toString())
          .toList(growable: false),
      expiresInS: expires is num && expires > 0 ? expires.round() : null,
      arm: text(data['arm']),
      armLabel: text(data['arm_label']),
    );
  }

  /// FSM 상태와 상관없이 응답·만료까지 두는 질문. 나머지는 hub 가 ACTION_* 를 벗어나면 내린다
  /// (ui_env_app state_source.dart 와 같은 규칙).
  bool get stateIndependent =>
      kind == 'checkin_suggest' || kind == 'vent_suggest';

  /// 창문 환기 카드인가. env_suggest 라도 정책이 창문 환기(arm)를 골랐으면 같은 문구를 쓴다.
  bool get isVent =>
      kind == 'vent_suggest' || (kind == 'env_suggest' && arm == 'ventilate_suggest');

  DateTime? get expiresAt => expiresInS == null
      ? null
      : receivedAt.add(Duration(seconds: expiresInS!));

  Map<String, Object> feedback(String verdict, DateTime now) {
    final ms = now.difference(receivedAt).inMilliseconds;
    return buildFeedbackData(
        requestId: requestId,
        verdict: verdict,
        responseMs: ms < 0 ? 0 : ms,
        kind: kind);
  }

  /// 카드 문구. hub 가 `explanation`(왜 이 제안인지 한 줄)을 주면 그것을 본문으로 쓰고,
  /// 없을 때만 kind·환경 플래그로 만든 문구를 쓴다(10-25 총괄 지시).
  LiveCard card(DisplayState? state) {
    final base = _baseCard(state);
    final note = explanation;
    return note == null
        ? base
        : LiveCard(
            title: base.title,
            body: note,
            accept: base.accept,
            reject: base.reject);
  }

  LiveCard _baseCard(DisplayState? state) {
    if (isVent) {
      final reasons = [
        for (final w in why)
          if (ventWhyText(w) case final text when text.isNotEmpty) text
      ];
      return LiveCard(
          title: minutes == null ? '창문을 잠깐 열어 주세요' : '창문을 $minutes분 열어 주세요',
          body: reasons.isNotEmpty
              ? reasons.toSet().join(' · ')
              : (state == null ? null : envReason(state)),
          accept: '열게요',
          reject: '괜찮아요');
    }
    return switch (kind) {
      'checkin_suggest' => const LiveCard(
          title: '지금 피곤하세요?',
          body: '알려주시면 다음에 더 잘 도울게요.',
          accept: '예',
          reject: '아니오'),
      'posture_suggest' => LiveCard(
          title: '자세를 가볍게 바로잡아 볼까요?',
          body: armLabel != null ? '추천 · $armLabel' : '허리를 펴고 어깨를 한 번 내려 보세요.',
          accept: '해볼게요',
          reject: '나중에'),
      'break_suggest' => LiveCard(
          title: '잠깐 쉬어 가는 건 어떨까요?',
          body: armLabel != null ? '추천 · $armLabel' : '눈을 감고 잠시 숨을 골라요.',
          accept: '쉬어 갈게요',
          reject: '조금 더 할게요'),
      'env_suggest' => LiveCard(
          title: environmentTitle(state?.envFlags ?? const []),
          body: armLabel != null
              ? '추천 · $armLabel'
              : (state == null ? null : envReason(state)),
          accept: '좋아요',
          reject: '괜찮아요'),
      _ => const LiveCard(
          title: '잠시 주변을 맞춰 볼까요?', accept: '좋아요', reject: '괜찮아요'),
    };
  }
}

class LiveCard {
  const LiveCard(
      {required this.title,
      this.body,
      required this.accept,
      required this.reject});
  final String title;
  final String? body;
  final String accept;
  final String reject;
}

/// vent_suggest 의 why 코드 → 문구. 모르는 코드(`co2_avg=1180` 같은 값 표기 포함)는 빈 문자열 —
/// 영문 코드를 화면에 내지 않는다.
String ventWhyText(String code) => switch (code) {
      'co2_high' || 'co2_rule' => '공기가 답답해졌어요',
      'co2_rising' => '공기가 조금씩 답답해지고 있어요',
      'too_hot' => '방이 조금 더워요',
      'too_humid' => '습도가 높아요',
      'stale_air' => '공기가 답답해졌어요',
      'outdoor_ok' => '바깥 공기가 괜찮아요',
      'outdoor_cold' => '바깥이 추워서 짧게만',
      'wind' => '바람이 있어 금방 바뀌어요',
      _ => '',
    };

String environmentTitle(List<String> flags) {
  if (flags.contains('co2_high') || flags.contains('co2_rising')) {
    return '잠깐 환기해 볼까요?';
  }
  if (flags.contains('too_dark')) return '조명을 조금 밝혀 볼까요?';
  if (flags.contains('too_hot') || flags.contains('too_cold')) {
    return '실내 온도를 맞춰 볼까요?';
  }
  if (flags.contains('too_humid') || flags.contains('too_dry')) {
    return '습도를 맞춰 볼까요?';
  }
  return '주변을 잠시 맞춰 볼까요?';
}

/// 환경 플래그 → 이유 한 줄. 없으면 null.
String? envReason(DisplayState state) {
  final parts = [
    for (final flag in state.envFlags)
      if (switch (flag) {
        'co2_high' => '공기가 답답해졌어요',
        'co2_rising' => '공기가 조금씩 답답해지고 있어요',
        'too_hot' => '방이 조금 더워요',
        'too_cold' => '방이 조금 추워요',
        'too_humid' => '습도가 높아요',
        'too_dry' => '공기가 건조해요',
        'too_dark' => '책상이 어두워요',
        _ => null,
      } case final phrase?)
        phrase,
  ];
  return parts.isEmpty ? null : parts.join(' · ');
}
