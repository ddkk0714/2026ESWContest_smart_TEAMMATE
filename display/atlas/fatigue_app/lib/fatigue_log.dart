/// 피로 데이터 수집: 프로필, 작업 세션, 15분 KSS 질문.
///
/// 화면 키보드가 없는 보드라 프로필은 이름 대신 A~F 슬롯으로 고른다. 이름·슬롯은
/// 이 보드에만 두고 hub 로는 `profile_id` 와 프로필 답변만 보낸다
/// (`deskmate/fatigue/label`, docs/mqtt-topics.md).
library;

import 'dart:convert';
import 'dart:io';
import 'dart:math';

/// KSS(Karolinska Sleepiness Scale) 1~9 중 화면에 글로 붙이는 칸.
const kssAnchors = {
  1: '매우 또렷함',
  3: '또렷함',
  5: '보통',
  7: '졸리지만 버틸 만함',
  9: '매우 졸림',
};

const profileSlots = ['A', 'B', 'C', 'D', 'E', 'F'];
const chronotypes = {'morning': '아침형', 'neither': '중간', 'evening': '저녁형'};
const workTypes = {
  'coding': '코딩',
  'document': '문서',
  'study': '공부',
  'other': '기타'
};

class FatigueProfile {
  const FatigueProfile({
    required this.id,
    required this.slot,
    required this.chronotype,
    required this.usualSleepH,
    required this.workType,
  });

  /// 슬롯을 초기화하면 새 id 를 받는다 — 다른 사람의 기록이 한 프로필로 섞이지 않게.
  final String id;
  final String slot;
  final String chronotype;
  final double usualSleepH;
  final String workType;

  static String newId(String slot, [Random? random]) {
    final r = random ?? Random.secure();
    return '$slot-${r.nextInt(1 << 20).toRadixString(36).padLeft(4, '0')}';
  }

  /// hub 로 보내는 프로필 답변. 슬롯 이름은 보내지 않는다.
  Map<String, Object?> answers() => {
        'chronotype': chronotype,
        'usual_sleep_h': usualSleepH,
        'work_type': workType,
      };

  Map<String, Object?> toJson() => {'id': id, 'slot': slot, ...answers()};

  static FatigueProfile? fromJson(Object? value) {
    if (value is! Map) return null;
    final id = value['id'], slot = value['slot'];
    final chrono = value['chronotype'], sleep = value['usual_sleep_h'];
    final work = value['work_type'];
    if (id is! String ||
        slot is! String ||
        !profileSlots.contains(slot) ||
        !chronotypes.containsKey(chrono) ||
        sleep is! num ||
        !workTypes.containsKey(work)) {
      return null;
    }
    return FatigueProfile(
        id: id,
        slot: slot,
        chronotype: chrono as String,
        usualSleepH: sleep.toDouble(),
        workType: work as String);
  }
}

/// 프로필 목록을 보드 설정 폴더에 저장한다. 위치 후보는 SettingsStore 와 같다.
class FatigueProfileStore {
  FatigueProfileStore({List<String>? candidates})
      : candidates = candidates ?? defaultCandidates();

  final List<String> candidates;

  static List<String> defaultCandidates() {
    final env = Platform.environment;
    final paths = <String>[];
    void add(String? base, String tail) {
      final root = base?.trim();
      if (root == null || root.isEmpty) return;
      final path = '$root/$tail';
      if (!paths.contains(path)) paths.add(path);
    }

    add(env['DESKMATE_CONFIG_DIR'], 'fatigue_profiles.json');
    add(env['XDG_CONFIG_HOME'], 'deskmate/fatigue_profiles.json');
    add(env['HOME'], '.config/deskmate/fatigue_profiles.json');
    add('/tmp', 'deskmate/fatigue_profiles.json');
    return paths;
  }

  List<FatigueProfile> load() {
    for (final path in candidates) {
      try {
        final file = File(path);
        if (!file.existsSync()) continue;
        final decoded = jsonDecode(file.readAsStringSync());
        if (decoded is! Map || decoded['profiles'] is! List) continue;
        return [
          for (final item in decoded['profiles'] as List)
            if (FatigueProfile.fromJson(item) case final profile?) profile
        ];
      } on Object {
        // 깨진 파일은 다음 후보로 넘어간다.
      }
    }
    return const [];
  }

  /// 처음으로 쓰기에 성공한 후보에 저장한다. 전부 실패하면 false.
  bool save(List<FatigueProfile> profiles) {
    final body = jsonEncode({
      'schema_version': 1,
      'profiles': [for (final p in profiles) p.toJson()],
    });
    for (final path in candidates) {
      try {
        final file = File(path);
        file.parent.createSync(recursive: true);
        file.writeAsStringSync(body);
        return true;
      } on Object {
        continue;
      }
    }
    return false;
  }
}

/// 피로 기록을 hub 쪽으로 내보내는 통로. MqttStateSource 가 구현한다.
abstract interface class FatigueSink {
  Future<void> publishFatigue(Map<String, Object?> data);
}

/// 작업 세션 하나의 상태와 질문 시각. 1초마다 [tick] 을 부른다.
class FatigueSession {
  FatigueSession({
    this.promptEvery = const Duration(minutes: 15),
    this.answerWindow = const Duration(minutes: 3),
    DateTime Function()? clock,
  }) : _clock = clock ?? DateTime.now;

  final Duration promptEvery;

  /// 질문을 이 시간 안에 답하지 않으면 '놓침' 으로 남기고 다음 질문을 잡는다.
  final Duration answerWindow;
  final DateTime Function() _clock;

  FatigueSink? sink;
  String? sessionId;
  FatigueProfile? profile;
  DateTime? startedAt;
  DateTime? nextPromptAt;
  DateTime? promptShownAt;

  bool get active => sessionId != null;
  bool get promptDue => promptShownAt != null;

  Future<void> start(FatigueProfile who,
      {required double sleepHours, required int kss}) async {
    _checkKss(kss);
    final now = _clock();
    profile = who;
    startedAt = now;
    sessionId = '${who.id}-${now.millisecondsSinceEpoch}';
    promptShownAt = null;
    nextPromptAt = now.add(promptEvery);
    await _send(now, 'session_start', {
      'kss': kss,
      'sleep_hours_last_night': sleepHours,
      'profile': who.answers(),
    });
  }

  /// 질문 시각이 되면 띄우고, 답 시간이 지나면 놓침으로 남긴다. 상태가 바뀌면 true.
  bool tick() {
    if (outbox.isNotEmpty) flush();
    if (!active) return false;
    final now = _clock();
    final shown = promptShownAt;
    if (shown == null) {
      if (now.isBefore(nextPromptAt!)) return false;
      promptShownAt = now;
      return true;
    }
    if (now.difference(shown) < answerWindow) return false;
    promptShownAt = null;
    nextPromptAt = now.add(promptEvery);
    _send(now, 'prompt_missed', {'prompt_ts': _ts(shown)});
    return true;
  }

  Future<void> answer(int kss) async {
    _checkKss(kss);
    final shown = promptShownAt;
    if (!active || shown == null) return;
    final now = _clock();
    promptShownAt = null;
    nextPromptAt = now.add(promptEvery);
    await _send(now, 'kss', {
      'kss': kss,
      'prompt_ts': _ts(shown),
      'response_ms': now.difference(shown).inMilliseconds,
    });
  }

  /// 질문과 상관없이 사용자가 누르는 '졸려요'.
  Future<void> sleepy() async {
    if (!active) return;
    await _send(_clock(), 'sleepy', const {});
  }

  Future<void> end({int? kss}) async {
    if (!active) return;
    if (kss != null) _checkKss(kss);
    await _send(_clock(), 'session_end', {if (kss != null) 'kss': kss});
    sessionId = null;
    profile = null;
    startedAt = null;
    nextPromptAt = null;
    promptShownAt = null;
  }

  static double _ts(DateTime t) => t.millisecondsSinceEpoch / 1000;

  static void _checkKss(int kss) {
    if (kss < 1 || kss > 9) throw RangeError.range(kss, 1, 9, 'kss');
  }

  /// 보내지 못한 기록. 라벨은 다시 얻을 수 없으니 연결이 돌아오면 순서대로 다시 보낸다.
  final List<Map<String, Object?>> outbox = [];
  bool _flushing = false;

  Future<void> _send(
      DateTime now, String event, Map<String, Object?> extra) async {
    outbox.add({
      'event': event,
      'session_id': sessionId,
      'profile_id': profile?.id,
      'ts': _ts(now),
      ...extra,
    });
    await flush();
  }

  Future<void> flush() async {
    final out = sink;
    if (out == null || _flushing) return;
    _flushing = true;
    try {
      while (outbox.isNotEmpty) {
        await out.publishFatigue(outbox.first);
        outbox.removeAt(0);
      }
    } on Object catch (error) {
      stderr.writeln('[fatigue] 기록 발행 실패(${outbox.length}건 대기): $error');
    } finally {
      _flushing = false;
    }
  }
}
