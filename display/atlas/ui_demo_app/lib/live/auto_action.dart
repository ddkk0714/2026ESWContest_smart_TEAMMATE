// hub 자동 실행(`deskmate/control/cmd` gate auto) → 화면 카드 "무엇을 왜 했는지" + 되돌리기(2026-10-31, 시연용).
//
// 왜: 자동 실행은 확인 질문(interaction/request)이 없어 화면에 아무것도 안 떴다 — 무드등이 밝아져도 사람이 이유를 모르고,
// 되돌릴 길도 없었다. 되돌리기는 hub 개인화의 실제 신호다(feedback/user reject → 디스패처가 이전 밝기로 되돌리고,
// 정책은 그 방법에 undo −2 를 준다).
//
// 겹치지 않게: 기기 없는 arm(알림음·음성·자세 카드)은 hub 가 이미 자동 정보 카드(auto: true)를 내므로 여기서 안 띄운다.
// 휴식 안내(rest_notice)·창문 환기(vent_suggest)도 각자의 카드가 있다. 이 카드는 기기 명령(무드등·선풍기·공기청정기)만.
//
// 문구표 (기기 × 원인) — [autoActionTitle]
// | 기기·동작                      | 자세(ACTION_POSTURE)                | 환경(ACTION_ENV)                 | 인지(ACTION_BREAK)                   |
// | desk_lamp 밝게                 | 졸음이 느껴져 무드등을 밝혔어요       | 방이 어두워 무드등을 밝혔어요       | 집중이 흐려져 조명을 밝게 바꿨어요      |
// | desk_lamp 어둡게               | 조명을 낮춰 눈을 쉬게 했어요          | (같음)                            | (같음)                               |
// | vent_fan 켜기                  | 졸음을 깨우려고 선풍기를 켰어요       | 공기가 답답해 선풍기를 켰어요       | 머리를 식히려고 선풍기를 켰어요          |
// | air_purifier 세게              | 공기를 맑게 하려고 공기청정기를 세게 돌렸어요 (원인 무관)                                                  |
// | speaker 알림음·음성·휴식 안내   | 이 카드 없음 — hub 자동 정보 카드·rest_notice 배너가 맡는다                                            |
library;

import 'package:flutter/foundation.dart';

import '../pr56/display_state.dart';

/// hub control.yaml `undo_window_sec`(60 s)과 같은 값 — 이 안에서만 되돌리기 버튼을 켠다.
const autoUndoWindow = Duration(seconds: 60);

/// 카드를 띄워 두는 시간(되돌리기 창 + 여유).
const autoCardShowFor = Duration(seconds: 90);

/// 되돌리기를 보낸 뒤 hub 의 되돌리기 명령(gate undo)을 기다리는 시간. 안 오면 기기는 그대로이고 학습만 남은 것이다
/// (hub 가 이전 밝기를 모르거나 되돌리기 창을 넘겼을 때 — dispatcher 는 명령 없이 정책에만 undo 를 준다).
const autoUndoWait = Duration(seconds: 10);

enum AutoActionStatus { sent, done, failed, undoing, undone }

@immutable
class AutoActionCard {
  const AutoActionCard({
    required this.commandId,
    required this.targetId,
    required this.title,
    required this.executedAt,
    this.reason,
    this.personalNote,
    this.status = AutoActionStatus.sent,
    this.failure,
    this.undoSentAt,
  });

  final String commandId;
  final String targetId;
  final String title;

  /// 왜 했는지 한 줄(자세·피로·환경). 모르면 null.
  final String? reason;

  /// 개인화 정책이 고른 방법이면 "처음 설정한 취향(빛)으로 골랐어요" 같은 한 줄. FSM 기본 명령이면 null.
  final String? personalNote;
  final DateTime executedAt;
  final AutoActionStatus status;
  final String? failure;

  /// 되돌리기를 보낸 시각. 보내지 않았으면 null.
  final DateTime? undoSentAt;

  DateTime get undoUntil => executedAt.add(autoUndoWindow);

  bool canUndo(DateTime now) =>
      (status == AutoActionStatus.sent || status == AutoActionStatus.done) &&
      now.isBefore(undoUntil);

  bool expired(DateTime now) => !now.isBefore(executedAt.add(autoCardShowFor));

  /// 되돌리기 버튼 옆에 셀 남은 초. 창이 지났으면 0.
  int undoSecondsLeft(DateTime now) {
    final left = undoUntil.difference(now).inSeconds;
    return left < 0 ? 0 : left;
  }

  /// 제목 아래 상태 한 줄. 실행 중·성공이면 null(제목이 이미 말한다).
  /// 되돌리기는 두 갈래: hub 가 기기를 되돌렸으면(gate undo 명령) "되돌렸어요", [autoUndoWait] 안에 그 명령이 없으면
  /// 기기는 그대로이고 학습(다음엔 덜 고름)만 남았다.
  String? statusTextAt(DateTime now) => switch (status) {
        AutoActionStatus.failed => '기기에 닿지 못했어요${failure == null ? '' : ' ($failure)'}',
        AutoActionStatus.undoing
            when undoSentAt != null &&
                !now.isBefore(undoSentAt!.add(autoUndoWait)) =>
          '기기는 그대로예요 · 다음엔 이 방법을 덜 고를게요',
        AutoActionStatus.undoing => '되돌리는 중이에요…',
        AutoActionStatus.undone => '되돌렸어요 · 다음엔 이 방법을 덜 고를게요',
        _ => null,
      };

  AutoActionCard copyWith(
          {AutoActionStatus? status, String? failure, DateTime? undoSentAt}) =>
      AutoActionCard(
        commandId: commandId,
        targetId: targetId,
        title: title,
        reason: reason,
        personalNote: personalNote,
        executedAt: executedAt,
        status: status ?? this.status,
        failure: failure ?? this.failure,
        undoSentAt: undoSentAt ?? this.undoSentAt,
      );
}

/// 기기·동작·원인 → 카드 제목. 이 카드를 띄우지 않는 명령이면 null.
String? autoActionTitle(
    String target, String operation, Object? value, String? cause) {
  switch (target) {
    case 'desk_lamp':
      final brighter = _lampBrighter(operation, value);
      if (brighter == null) return null;
      if (!brighter) return '조명을 낮춰 눈을 쉬게 했어요';
      return switch (cause) {
        'posture' => '졸음이 느껴져 무드등을 밝혔어요',
        'environment' => '방이 어두워 무드등을 밝혔어요',
        'cognitive' => '집중이 흐려져 조명을 밝게 바꿨어요',
        _ => '무드등을 밝혔어요',
      };
    case 'vent_fan':
    case 'fan':
      if (operation == 'set_power' && value.toString() != 'on') return null;
      return switch (cause) {
        'posture' => '졸음을 깨우려고 선풍기를 켰어요',
        'environment' => '공기가 답답해 선풍기를 켰어요',
        'cognitive' => '머리를 식히려고 선풍기를 켰어요',
        _ => '선풍기를 켰어요',
      };
    case 'air_purifier':
      return '공기를 맑게 하려고 공기청정기를 세게 돌렸어요';
  }
  return null; // speaker(알림음·음성·휴식 안내)는 hub 정보 카드·rest_notice 가 맡는다
}

/// 밝게(true)·어둡게(false)·모름(null).
bool? _lampBrighter(String operation, Object? value) {
  if (operation == 'adjust_brightness') {
    final percent = value is Map ? value['percent'] : null;
    return percent is num ? percent > 0 : null;
  }
  if (operation == 'set_brightness') {
    return value is num ? value >= 128 : null;
  }
  return null;
}

/// 왜 했는지 한 줄 — 지금 state/phase 에서.
String? autoActionReason(String? cause, DisplayState? state) {
  if (state == null) return null;
  final fatigue = '피로 ${(state.fatigue * 100).round()}%';
  switch (cause) {
    case 'posture':
      final posture = switch (state.tofPosture) {
        'face_down' => '엎드린 자세가 이어졌어요',
        'drowsy' => '꾸벅이는 자세가 이어졌어요',
        'chin_rest' => '턱을 괸 자세가 이어졌어요',
        _ => '자세가 무너진 채 이어졌어요',
      };
      return '$posture · $fatigue';
    case 'environment':
      final flags = state.envFlags;
      final env = flags.contains('too_dark')
          ? '방이 어두워요'
          : flags.contains('co2_high') || flags.contains('co2_rising')
              ? '공기가 답답해졌어요'
              : flags.contains('too_hot')
                  ? '방이 조금 더워요'
                  : '주변 환경이 바뀌었어요';
      return '$env · $fatigue';
    case 'cognitive':
      return '오래 이어서 일했어요 · $fatigue';
  }
  return fatigue;
}

/// 개인화 정책이 이 기기를 골랐으면 그 이유 한 줄. FSM 기본 명령(정책 결정 없음)이면 null.
String? autoActionPersonalNote(String target, DisplayState? state) {
  if (state == null || state.interventionSource != 'bandit') return null;
  final arm = state.interventionArm;
  final channel = switch (arm) {
    'wake_light' || 'light_cool_bright' || 'light_warm_dim' => 'desk_lamp',
    'fan_on' => 'vent_fan',
    'air_fan_up' => 'air_purifier',
    _ => null,
  };
  if (channel == null ||
      (channel != target && !(channel == 'vent_fan' && target == 'fan'))) {
    return null;
  }
  final count = state.interventionPersonalCount ?? 0;
  if (count > 0) return '지금까지의 반응을 보고 이 방법을 골랐어요';
  final pref = switch (channel) {
    'desk_lamp' => '빛',
    'vent_fan' => '바람',
    _ => '공기',
  };
  return '처음 설정한 취향($pref)으로 골랐어요';
}

/// 자동 실행 카드 하나를 들고 있는다. 새 자동 명령이 오면 바꾼다.
class AutoActionBoard extends ChangeNotifier {
  AutoActionCard? _current;

  AutoActionCard? current(DateTime now) {
    final card = _current;
    return card == null || card.expired(now) ? null : card;
  }

  /// `control/cmd` data. 자동(gate auto) 기기 명령이면 카드를 띄우고, 되돌리기 명령(gate undo)이면 되돌림으로 표시한다.
  void onCommand(Map<String, dynamic> data, DisplayState? state, DateTime now) {
    final target = data['target_id']?.toString() ?? '';
    final operation = data['operation']?.toString() ?? '';
    final gate = data['gate']?.toString();
    final card = _current;
    if (gate == 'undo') {
      if (card != null && card.targetId == target) {
        _current = card.copyWith(status: AutoActionStatus.undone);
        notifyListeners();
      }
      return;
    }
    if (gate != 'auto') return;
    final origin = data['origin_state']?.toString() ?? '';
    if (!origin.startsWith('ACTION_')) return;
    final id = data['command_id']?.toString() ?? '';
    final cause = data['cause']?.toString();
    final title = autoActionTitle(target, operation, data['value'], cause);
    if (id.isEmpty || title == null) return;
    _current = AutoActionCard(
      commandId: id,
      targetId: target,
      title: title,
      reason: autoActionReason(cause, state),
      personalNote: autoActionPersonalNote(target, state),
      executedAt: now,
    );
    notifyListeners();
  }

  /// `control/result` data — 같은 command_id 면 성공·실패를 붙인다.
  void onResult(Map<String, dynamic> data) {
    final card = _current;
    if (card == null || data['command_id']?.toString() != card.commandId) {
      return;
    }
    if (card.status == AutoActionStatus.undoing ||
        card.status == AutoActionStatus.undone) {
      return;
    }
    final status = data['status']?.toString();
    if (status == 'succeeded') {
      _current = card.copyWith(status: AutoActionStatus.done);
    } else if (status == 'failed' || status == 'timeout') {
      final code = data['error_code']?.toString();
      _current = card.copyWith(
          status: AutoActionStatus.failed,
          failure: code == null || code.isEmpty ? status : code);
    } else {
      return;
    }
    notifyListeners();
  }

  /// 되돌리기를 보냈다 — hub 의 되돌리기 명령(gate undo)이 오면 [AutoActionStatus.undone].
  void markUndoing(DateTime now) {
    final card = _current;
    if (card == null) return;
    _current = card.copyWith(status: AutoActionStatus.undoing, undoSentAt: now);
    notifyListeners();
  }

  void dismiss() {
    _current = null;
    notifyListeners();
  }
}
