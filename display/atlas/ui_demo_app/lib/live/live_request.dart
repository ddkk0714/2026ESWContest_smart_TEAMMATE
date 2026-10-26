// hub 의 확인 질문(`deskmate/interaction/request`) → 화면 중앙 제안 카드 문구.
//
// kind 와 문구는 ui_env_app dashboard_view.dart(suggestionTitle·ventWhyText)와 같은 뜻으로 맞추되
// 이 앱의 차분한 말투로 옮겼다. 응답은 accept/reject 두 가지뿐이다.
library;

import '../pr56/display_state.dart';
import 'payload.dart';
import 'home_items.dart' show postureKo;

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
    this.adjustments = const [],
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
  final List<SuggestionAdjustment> adjustments;

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
      adjustments: [
        for (final item in data['adjustments'] is List
            ? data['adjustments'] as List
            : const [])
          if (item is Map)
            if (SuggestionAdjustment.fromData(item) case final adjustment?)
              adjustment,
      ],
    );
  }

  /// FSM 상태와 상관없이 응답·만료까지 두는 질문. 나머지는 hub 가 ACTION_* 를 벗어나면 내린다
  /// (ui_env_app state_source.dart 와 같은 규칙).
  bool get stateIndependent =>
      kind == 'checkin_suggest' || kind == 'vent_suggest';

  /// 창문 환기 카드인가. env_suggest 라도 정책이 창문 환기(arm)를 골랐으면 같은 문구를 쓴다.
  bool get isVent =>
      kind == 'vent_suggest' ||
      (kind == 'env_suggest' && arm == 'ventilate_suggest');

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
      _ =>
        const LiveCard(title: '잠시 주변을 맞춰 볼까요?', accept: '좋아요', reject: '괜찮아요'),
    };
  }

  SuggestionDetails details(DisplayState? state) {
    final reasons = [
      for (final code in why)
        if (ventWhyText(code) case final text when text.isNotEmpty) text,
    ];
    final reason = explanation ??
        state?.interventionExplanation ??
        (reasons.isNotEmpty ? reasons.toSet().join(' · ') : null) ??
        (kind == 'env_suggest' || isVent
            ? (state == null ? null : envReason(state))
            : null) ??
        switch (kind) {
          'posture_suggest' => '책상에서 자세를 잠깐 돌아보자는 신호가 왔어요.',
          'break_suggest' => '지금의 흐름을 오래 이어갈 수 있도록, 잠깐의 쉼을 제안받았어요.',
          'checkin_suggest' => '센서만으로 알 수 없는 지금의 기분을, 당신에게 직접 듣고 싶어요.',
          _ => '책상에서 조금 더 편안한 환경을 위한 제안이 도착했어요.',
        };
    final observed = <String>[
      if (state?.co2Ppm case final value? when value > 0) 'CO₂ $value ppm',
      if (state?.temperatureC case final value? when value.isFinite)
        '온도 ${value.toStringAsFixed(1)}°C',
      if (state?.humidityPct case final value? when value.isFinite)
        '습도 ${value.round()}%',
      if (state?.lux case final value? when value >= 0) '조도 $value lx',
      if (kind == 'posture_suggest' && postureKo(state?.tofPosture) != null)
        '지금 자세 · ${postureKo(state?.tofPosture)}',
    ];
    final changes = adjustments.isNotEmpty
        ? adjustments
        : <SuggestionAdjustment>[
            if (isVent)
              SuggestionAdjustment(
                  '창문 환기', minutes == null ? '환기 시간 정보가 없어요' : '$minutes분 동안',
                  manual: true)
            else if (kind == 'posture_suggest')
              SuggestionAdjustment(
                  armLabel ?? '허리와 어깨, 편안한 자세로', '직접 천천히 자세를 바꿔주세요',
                  manual: true)
            else if (kind == 'break_suggest')
              SuggestionAdjustment(armLabel ?? '잠깐의 휴식',
                  minutes == null ? '휴식 시간 정보가 없어요' : '$minutes분 동안',
                  manual: true)
            else if (kind == 'checkin_suggest')
              const SuggestionAdjustment('지금의 컨디션', '답변만 기록해요 · 기기 변경 없음',
                  manual: true)
            else
              SuggestionAdjustment(armLabel ?? '책상 환경', '정확한 조절량 정보가 없어요'),
          ];
    return SuggestionDetails(
        reason: reason,
        observations: observed,
        changes: changes,
        reassurance: isVent
            ? '창문은 직접 열어주세요. 답변으로 환기 의사를 전할게요.'
            : changes.every((change) => change.manual)
                ? '당신의 속도에 맞춰 선택해 주세요. 지금은 그대로여도 괜찮아요.'
                : '동의하면 조절을 요청할게요. 기기에서 반영된 결과는 연결 상태에서 확인할 수 있어요.');
  }
}

class SuggestionDetails {
  const SuggestionDetails(
      {required this.reason,
      this.observations = const [],
      required this.changes,
      required this.reassurance});
  final String reason;
  final List<String> observations;
  final List<SuggestionAdjustment> changes;
  final String reassurance;
}

class SuggestionAdjustment {
  const SuggestionAdjustment(this.target, this.amount, {this.manual = false});
  final String target;
  final String amount;
  final bool manual;

  static SuggestionAdjustment? fromData(Map data) {
    final target = switch (data['target_id']) {
      'desk_lamp' => '조명',
      'vent_fan' => '선풍기',
      'air_purifier' => '공기청정기',
      'plug_1' => '플러그',
      _ => null,
    };
    if (target == null) return null;
    final value = data['value'];
    String? number(Object? v) => v is num && v.isFinite
        ? (v == v.roundToDouble() ? v.toInt().toString() : v.toStringAsFixed(1))
        : null;
    final n = number(value);
    final unit =
        switch (data['unit']) { '%' => '%', 'K' => 'K', 'min' => '분', _ => '' };
    final amount = switch (data['operation']) {
      'set_brightness' when n != null =>
        '밝기 ${unit.isEmpty ? '설정값 ' : ''}$n$unit',
      'adjust_brightness' when n != null =>
        '밝기 설정값 ${value is num && value > 0 ? '+' : ''}$n만큼',
      'set_color_temperature' when n != null =>
        '빛 온도 ${unit.isEmpty ? '설정값 ' : ''}$n$unit',
      'set_level' when n != null => '$n단으로',
      'set_speed' when n != null => '바람 세기 $n%',
      'set_power' when value == true || value == 'on' => '켜기',
      'set_power' when value == false || value == 'off' => '끄기',
      'set_mode' => switch (value) {
          'auto' || 'AUTO' => '자동 모드로',
          'LOW' => '약풍으로',
          'natural' => '자연풍으로',
          'normal' => '일반풍으로',
          _ => null,
        },
      _ => null,
    };
    if (amount == null) return null;
    final before = number(data['previous_value']);
    return SuggestionAdjustment(
        target, before == null ? amount : '현재 $before$unit → $amount');
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
      }
          case final phrase?)
        phrase,
  ];
  return parts.isEmpty ? null : parts.join(' · ');
}
