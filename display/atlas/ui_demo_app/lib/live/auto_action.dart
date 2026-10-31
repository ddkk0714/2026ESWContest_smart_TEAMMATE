// hub 자동 실행(`deskmate/control/cmd` gate auto) → 화면 카드 "무엇을 왜 했는지" + 되돌리기(2026-10-31, 시연용).
//
// 왜: 자동 실행은 확인 질문(interaction/request)이 없어 화면에 아무것도 안 떴다 — 무드등이 밝아져도 사람이 이유를 모르고,
// 되돌릴 길도 없었다. 되돌리기는 hub 개인화의 실제 신호다(feedback/user reject → 디스패처가 이전 밝기로 되돌리고,
// 정책은 그 방법에 undo −2 를 준다).
//
// 겹치지 않게: 기기 없는 arm(알림음·음성·자세 카드)은 hub 가 이미 자동 정보 카드(auto: true)를 내므로 여기서 안 띄운다.
// 휴식 안내(rest_notice)·창문 환기(vent_suggest)도 각자의 카드가 있다. 이 카드는 기기 명령(무드등·선풍기·공기청정기)만.
//
// 문구 — 기계가 보고하는 말투가 아니라 곁에서 챙겨 주는 말투로(V29, 10-31 사용자). 숫자(피로 %)·오류 코드는 제품 화면에
// 내지 않는다(개발자 화면·제어 로그에 있다).
// 제목 (기기 × 원인) — [autoActionTitle]
// | 기기·동작        | 자세(ACTION_POSTURE)                 | 환경(ACTION_ENV)                  | 인지(ACTION_BREAK)                  |
// | desk_lamp 밝게   | 졸음이 내려앉기 전에, 불빛을 밝혀 둘게요 | 주변이 어둑해져 불빛을 조금 더했어요   | 머리가 무거워 보여 환한 빛으로 바꿨어요 |
// | desk_lamp 어둡게 | 눈이 쉴 수 있게 불빛을 낮췄어요        | (같음)                              | (같음)                              |
// | vent_fan 켜기    | 선선한 바람으로 잠을 깨워 볼게요        | 공기가 무거워 바람을 조금 들였어요     | 잠깐 바람으로 머리를 식혀요            |
// | air_purifier 세게 | 숨 쉬기 편하게 공기를 맑게 하고 있어요 (원인 무관)                                                     |
// | speaker 알림음·음성·휴식 안내 | 이 카드 없음 — hub 자동 정보 카드·rest_notice 배너가 맡는다                                  |
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

/// 되돌리기 명령(gate undo)이 온 뒤 그 명령의 결과를 기다리는 시간. 안 오면 되돌리지 못한 것으로 본다
/// (제어 진행 탭의 시간 초과와 같은 15 s).
const autoUndoResultWait = Duration(seconds: 15);

/// 이 카드가 되돌리기를 보낸 뒤, undo_of 없는 되돌리기 명령을 이 카드 것으로 받아 주는 시간(hub undo_window_sec 와 같다).
const autoUndoLinkWindow = Duration(seconds: 60);

/// [undone] 은 되돌리기 명령의 결과가 succeeded 일 때만. 실패·시간 초과면 [undoFailed].
enum AutoActionStatus { sent, done, failed, undoing, undone, undoFailed }

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
    this.undoCommandId,
    this.undoCommandAt,
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

  /// 이 카드에 묶은 hub 되돌리기 명령(gate undo)의 command_id 와 받은 시각. 그 id 의 결과로만 되돌림을 판정한다.
  final String? undoCommandId;
  final DateTime? undoCommandAt;

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
  /// 되돌리기는 세 갈래: hub 되돌리기 명령의 결과가 succeeded 면 "돌려 뒀어요", 실패·시간 초과([autoUndoResultWait])면
  /// "돌리지 못했어요", [autoUndoWait] 안에 되돌리기 명령 자체가 없으면 기기는 그대로. 어느 쪽이든 reject 는 hub 에 갔으니
  /// 학습(다음엔 덜 고름)은 남는다.
  String? statusTextAt(DateTime now) => switch (status) {
        AutoActionStatus.failed => '잠시 기기에 닿지 못했어요',
        AutoActionStatus.undoing
            when undoCommandAt != null &&
                !now.isBefore(undoCommandAt!.add(autoUndoResultWait)) =>
          _undoFailedText,
        AutoActionStatus.undoing
            when undoCommandId == null &&
                undoSentAt != null &&
                !now.isBefore(undoSentAt!.add(autoUndoWait)) =>
          '알겠어요. 다음엔 다른 방법으로 다가갈게요',
        AutoActionStatus.undoing => '원래대로 돌려 두는 중이에요…',
        AutoActionStatus.undone => '원래대로 돌려 뒀어요. 다음엔 다른 방법을 찾아볼게요',
        AutoActionStatus.undoFailed => _undoFailedText,
        _ => null,
      };

  static const _undoFailedText = '원래대로 돌리지 못했어요. 그래도 다음엔 다른 방법을 찾아볼게요';

  AutoActionCard copyWith(
          {AutoActionStatus? status,
          String? failure,
          DateTime? undoSentAt,
          String? undoCommandId,
          DateTime? undoCommandAt}) =>
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
        undoCommandId: undoCommandId ?? this.undoCommandId,
        undoCommandAt: undoCommandAt ?? this.undoCommandAt,
      );
}

/// 기기·동작·원인 → 카드 제목. 이 카드를 띄우지 않는 명령이면 null.
String? autoActionTitle(
    String target, String operation, Object? value, String? cause) {
  switch (target) {
    case 'desk_lamp':
      final brighter = _lampBrighter(operation, value);
      if (brighter == null) return null;
      if (!brighter) return '눈이 쉴 수 있게 불빛을 낮췄어요';
      return switch (cause) {
        'posture' => '졸음이 내려앉기 전에, 불빛을 밝혀 둘게요',
        'environment' => '주변이 어둑해져 불빛을 조금 더했어요',
        'cognitive' => '머리가 무거워 보여 환한 빛으로 바꿨어요',
        _ => '불빛을 조금 밝혀 둘게요',
      };
    case 'vent_fan':
    case 'fan':
      if (operation == 'set_power' && value.toString() != 'on') return null;
      return switch (cause) {
        'posture' => '선선한 바람으로 잠을 깨워 볼게요',
        'environment' => '공기가 무거워 바람을 조금 들였어요',
        'cognitive' => '잠깐 바람으로 머리를 식혀요',
        _ => '바람을 조금 들였어요',
      };
    case 'air_purifier':
      return '숨 쉬기 편하게 공기를 맑게 하고 있어요';
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

/// 왜 했는지 한 줄 — 지금 state/phase 에서. 숫자 없이 지켜본 모습만 말한다.
String? autoActionReason(String? cause, DisplayState? state) {
  if (state == null) return null;
  switch (cause) {
    case 'posture':
      return switch (state.tofPosture) {
        'face_down' => '책상에 엎드린 채 한참이 지났어요',
        'drowsy' => '고개가 자꾸 꾸벅이고 있었어요',
        'chin_rest' => '턱을 괸 채 오래 머물러 있었어요',
        _ => '자세가 조금씩 무너지고 있었어요',
      };
    case 'environment':
      final flags = state.envFlags;
      return flags.contains('too_dark')
          ? '책상 위가 어둑해졌어요'
          : flags.contains('co2_high') || flags.contains('co2_rising')
              ? '공기가 조금 답답해졌어요'
              : flags.contains('too_hot')
                  ? '방이 조금 더워졌어요'
                  : '주변이 조금 달라졌어요';
    case 'cognitive':
      return '쉼 없이 오래 집중하고 있었어요';
  }
  return null;
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
  if (count > 0) return '그동안의 반응을 기억해서 골랐어요';
  return switch (channel) {
    'desk_lamp' => '처음에 알려 준 대로, 빛으로 깨워 드려요',
    'vent_fan' => '처음에 알려 준 대로, 바람으로 깨워 드려요',
    _ => '처음에 알려 준 취향을 기억하고 있어요',
  };
}

/// 자동 실행 카드 하나를 들고 있는다. 새 자동 명령이 오면 바꾼다.
class AutoActionBoard extends ChangeNotifier {
  AutoActionCard? _current;

  AutoActionCard? current(DateTime now) {
    final card = _current;
    return card == null || card.expired(now) ? null : card;
  }

  /// `control/cmd` data. 자동(gate auto) 기기 명령이면 카드를 띄우고, 되돌리기 명령(gate undo)이면 이 카드에 묶어
  /// 그 command_id 의 결과를 기다린다(되돌림 판정은 [onResult]).
  void onCommand(Map<String, dynamic> data, DisplayState? state, DateTime now) {
    final target = data['target_id']?.toString() ?? '';
    final operation = data['operation']?.toString() ?? '';
    final gate = data['gate']?.toString();
    final card = _current;
    if (gate == 'undo') {
      final id = data['command_id']?.toString() ?? '';
      if (card != null && id.isNotEmpty && _undoBelongsTo(card, data, target, now)) {
        _current = card.copyWith(
            status: AutoActionStatus.undoing,
            undoCommandId: id,
            undoCommandAt: now);
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

  /// 되돌리기 명령이 이 카드 것인가. undo_of 가 있으면 그것만 본다. 없으면 이 카드가 되돌리기를 보낸 뒤
  /// [autoUndoLinkWindow] 안에 온 같은 기기의 첫 되돌리기만 — 다른 카드(같은 기기라도)의 되돌리기는 묶지 않는다.
  static bool _undoBelongsTo(AutoActionCard card, Map<String, dynamic> data,
      String target, DateTime now) {
    final undoOf = data['undo_of']?.toString();
    if (undoOf != null && undoOf.isNotEmpty) return undoOf == card.commandId;
    final sentAt = card.undoSentAt;
    return card.undoCommandId == null &&
        card.status == AutoActionStatus.undoing &&
        sentAt != null &&
        card.targetId == target &&
        now.isBefore(sentAt.add(autoUndoLinkWindow));
  }

  /// `control/result` data — 원 명령이면 성공·실패를, 묶인 되돌리기 명령이면 되돌림 성공·실패를 붙인다.
  void onResult(Map<String, dynamic> data) {
    final card = _current;
    if (card == null) return;
    final id = data['command_id']?.toString();
    if (id != null && id == card.undoCommandId) {
      if (card.status != AutoActionStatus.undoing) return;
      final status = data['status']?.toString();
      if (status == 'succeeded') {
        _current = card.copyWith(status: AutoActionStatus.undone);
      } else if (status == 'failed' || status == 'timeout') {
        final code = data['error_code']?.toString();
        _current = card.copyWith(
            status: AutoActionStatus.undoFailed,
            failure: code == null || code.isEmpty ? status : code);
      } else {
        return;
      }
      notifyListeners();
      return;
    }
    if (id != card.commandId) return;
    if (card.status == AutoActionStatus.undoing ||
        card.status == AutoActionStatus.undone ||
        card.status == AutoActionStatus.undoFailed) {
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

  /// 되돌리기를 보냈다 — hub 의 되돌리기 명령(gate undo)이 오면 묶고, 그 결과가 succeeded 면 [AutoActionStatus.undone].
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
