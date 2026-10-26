import 'package:flutter/foundation.dart';
import 'live/payload.dart' show newProfileId;
import 'pr56/display_state.dart';
import 'profile_store.dart';

enum DemoPage { home, focus, details, widgets, report }

class DemoConfig {
  DemoConfig(this.values);
  final Map<String, dynamic> values;
  int seconds(String key) => (values[key] as num).toInt();
  double number(String key) => (values[key] as num).toDouble();
}

class DeskPreferences {
  DeskPreferences({this.brightness = .85, this.musicEnabled = true});
  bool lightEnabled = true;
  double brightness;
  double warmth = .45;
  bool fanEnabled = false;
  int fanSpeed = 1;
  bool musicEnabled;
  double volume = .3;
  int focusMinutes = 25;
  bool autoStart = true;
  bool autoAdjust = true;
  bool startNotice = true;
  double fatigueBrightness = .95;
  double fatigueWarmth = .25;
  int fatigueFanSpeed = 3;
  DeskPreferences copy() =>
      DeskPreferences(brightness: brightness, musicEnabled: musicEnabled)
        ..lightEnabled = lightEnabled
        ..warmth = warmth
        ..fanEnabled = fanEnabled
        ..fanSpeed = fanSpeed
        ..volume = volume
        ..focusMinutes = focusMinutes
        ..autoStart = autoStart
        ..autoAdjust = autoAdjust
        ..startNotice = startNotice
        ..fatigueBrightness = fatigueBrightness
        ..fatigueWarmth = fatigueWarmth
        ..fatigueFanSpeed = fatigueFanSpeed;
}

class Persona {
  Persona(
      {required this.name,
      required this.job,
      required this.rounds,
      this.age = 19,
      this.sleep = 7,
      this.sound = '백색 소음',
      this.order = const ['조명', '선풍기', '알림'],
      this.mood = '보통',
      this.avatar = 'cat',
      String? profileId,
      this.placement = 'center',
      this.bedtimeMinutes = defaultBedtime,
      DeskPreferences? preferences})
      : profileId = profileId ?? newProfileId(),
        preferences = preferences ??
            DeskPreferences(
                brightness: job == '수험생' ? .85 : .45,
                musicEnabled: sound != '무음');
  String name;
  String job;
  int rounds;
  int age;
  int sleep;
  String sound;
  List<String> order;
  String mood;
  String avatar;

  /// hub 에 보내는 식별자. 이름과 무관한 난수이며 복사본도 같은 값을 쓴다.
  final String profileId;

  /// 장치를 보통 두는 곳(앉은 자리 기준): left | center | right.
  String placement;

  /// 잠드는 시각(자정부터 분). 일어나는 시각 = 이 값 + 수면 시간.
  int bedtimeMinutes;
  static const defaultBedtime =
      23 * 60 + 30; // hub personalize.yaml sleep.default_bedtime
  final DeskPreferences preferences;
  Persona copy() => Persona(
      name: name,
      job: job,
      rounds: rounds,
      age: age,
      sleep: sleep,
      sound: sound,
      order: List.of(order),
      mood: mood,
      avatar: avatar,
      profileId: profileId,
      placement: placement,
      bedtimeMinutes: bedtimeMinutes,
      preferences: preferences.copy());
}

class DemoModel extends ChangeNotifier {
  DemoModel(this.config, {this.store, this.presentationEnabled = false})
      : profiles = presentationEnabled
            ? defaultProfiles()
            : [Persona(name: '나', job: '기타', rounds: 0)] {
    if (presentationEnabled) {
      timeScale = config.seconds('simulatedSecondsPerTick');
    }
    final saved = store?.load();
    if (saved != null) {
      profiles
        ..clear()
        ..addAll(saved.profiles);
      profileIndex = saved.selected;
    } else {
      // 첫 실행: 기본 프로필의 profile_id 도 다음 실행에 같게 남긴다.
      _persist();
    }
  }
  final DemoConfig config;
  bool presentationEnabled;
  int timeScale = 1;
  bool showFocusGoal = false;

  void configurePresentation(
      {required bool enabled, int speed = 1, bool goal = false}) {
    presentationEnabled = enabled;
    timeScale = enabled && [1, 10, 60].contains(speed) ? speed : 1;
    showFocusGoal = enabled && goal;
    scenario = false;
    proposal = false;
    notifyListeners();
  }

  /// V18: 프로필을 Pi 5 에 저장하는 곳. null 이면 V17 처럼 메모리에만 둔다(시험).
  final ProfileStore? store;

  void _persist() => store?.save(profiles, profileIndex);

  /// 프로필이 만들어지거나 바뀌거나 골라질 때(보드에 선호를 알린다). 연결이 없으면 null.
  void Function(Persona persona)? onProfileChanged;

  /// 장치 위치를 보드에 보낸다. 화면에 잠깐 보일 한 줄을 돌려준다(보낼 곳이 없으면 null).
  Future<String?> Function(String placement)? onPlacement;

  /// 보드의 state/phase 를 따라가는 중인가. 끊기면 V17 과 같은 메모리 흐름으로 돌아간다.
  bool liveDriven = false;

  /// 이번 집중이 보드를 따라 시작됐는가. 그러면 경과 시간은 실제 시간(1초=1초)으로 센다.
  bool liveSession = false;
  bool sessionObserved = false;

  /// hub 가 REST(휴식) 상태다. 집중 상세가 쉬는 중 문구로 바뀌고 경과 시간을 멈춘다.
  bool resting = false;
  String? _liveFsm;
  final List<Persona> profiles;
  static List<Persona> defaultProfiles() => [
        Persona(name: '지우', job: '수험생', rounds: 12),
        Persona(
            name: '민준',
            job: '재택 직장인',
            rounds: 12,
            age: 29,
            sound: '카페 음악',
            avatar: 'sprout',
            order: ['알림', '조명', '선풍기']),
        Persona(name: '새 사용자', job: '수험생', rounds: 0, avatar: 'star'),
      ];
  int profileIndex = 0;
  Persona get persona => profiles[profileIndex];
  int get profileSlotCount => ((profiles.length + 2) ~/ 2) * 2;

  void setAvatar(int index, String avatar) {
    profiles[index].avatar = avatar;
    _persist();
    notifyListeners();
  }

  bool get trained => persona.rounds >= config.seconds('feedbackRounds');
  DemoPage page = DemoPage.home;
  DemoPage returnPage = DemoPage.home;
  DateTime now = DateTime.now();
  int sessionSeconds = 0;
  int completedSeconds = 0;
  int completedSessions = 0;
  int workTicks = 0;
  int pageTicks = 0;
  int noticeTicks = 0;
  int absentTicks = 0;
  int fatigueEvents = 0;
  int interventionStep = 0;
  bool preferencesOpen = false;
  bool active = false;
  bool scenario = false;
  bool absent = false;
  bool proposal = false;
  bool ledOn = true;
  bool fanOn = false;
  int fanSpeed = 1;
  double brightness = .65;
  double warmth = .45;
  String notice = '';
  String feeling = '';
  String posture = '연결 대기';
  String? pendingAction;
  // The fatigue card stays only until the user answers it.
  bool askingFeedback = false;
  final events = <String>[];

  String get elapsed {
    final h = sessionSeconds ~/ 3600;
    final m = sessionSeconds ~/ 60 % 60;
    final s = sessionSeconds % 60;
    return '${h.toString().padLeft(2, '0')}:${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }

  void tick([DateTime? time]) {
    now = time ?? DateTime.now();
    pageTicks++;
    if (noticeTicks > 0 && --noticeTicks == 0) {
      notice = '';
      askingFeedback = false;
    }
    if (active && !absent && !resting) {
      sessionSeconds += presentationEnabled ? timeScale : 1;
    }
    // 보드를 따라갈 때 자리 비움의 종료는 hub(END)가 정한다. 화면이 따로 끝내지 않는다.
    if (active &&
        absent &&
        presentationEnabled &&
        !liveDriven &&
        ++absentTicks >= config.seconds('absenceGraceSeconds')) {
      finish();
      return;
    }
    if (!preferencesOpen &&
        scenario &&
        !active &&
        !proposal &&
        page == DemoPage.home) {
      if (++workTicks >= config.seconds('workCueSeconds')) {
        if (trained &&
            persona.preferences.autoStart &&
            config.number('trainedConfidence') >=
                config.number('autoConfidence')) {
          start(automatic: true);
          return;
        }
        proposal = true;
        scenario = false;
      }
    }
    if (!preferencesOpen &&
        page == DemoPage.details &&
        pageTicks >= config.seconds('detailSeconds') &&
        !absent) {
      page = DemoPage.focus;
      pageTicks = 0;
    }
    if (!preferencesOpen &&
        page == DemoPage.widgets &&
        pageTicks >= config.seconds('widgetIdleSeconds')) {
      page = returnPage;
      pageTicks = 0;
    }
    notifyListeners();
  }

  void touch() => pageTicks = 0;

  void show(DemoPage next) {
    if (next == DemoPage.widgets) {
      returnPage = active ? DemoPage.details : DemoPage.home;
    }
    page = next;
    pageTicks = 0;
    notifyListeners();
  }

  void selectProfile(int index) {
    if (active) return;
    profileIndex = index;
    proposal = false;
    scenario = false;
    workTicks = 0;
    _persist();
    onProfileChanged?.call(persona);
    notifyListeners();
  }

  void beginScenario() {
    if (active || !presentationEnabled) return;
    page = DemoPage.home;
    scenario = true;
    proposal = false;
    workTicks = 0;
    notifyListeners();
  }

  void start({bool automatic = false}) {
    if (active) return;
    active = true;
    liveSession = liveDriven;
    sessionObserved = liveDriven;
    resting = false;
    scenario = false;
    absent = false;
    absentTicks = 0;
    sessionSeconds = 0;
    fatigueEvents = 0;
    interventionStep = 0;
    if (!liveDriven) posture = presentationEnabled ? '바른 자세' : '연결 대기';
    pendingAction = null;
    askingFeedback = false;
    feeling = '';
    events.clear();
    events.add(automatic ? '집중 자동 진입' : '집중 시작 · 사용자 확인');
    if (proposal) {
      persona.rounds++;
      _persist();
    }
    proposal = false;
    _applyPreferences();
    page = DemoPage.focus;
    pageTicks = 0;
    notice = persona.preferences.startNotice ? '집중 모드 시작됨' : '';
    noticeTicks = config.seconds('focusNoticeSeconds');
    notifyListeners();
  }

  void decline() {
    proposal = false;
    scenario = false;
    notice = '괜찮아요. 편할 때 시작해요.';
    noticeTicks = config.seconds('focusNoticeSeconds');
    notifyListeners();
  }

  void fatigue() {
    if (!active || !presentationEnabled) return;
    fatigueEvents++;
    posture = '앞으로 기울어짐';
    final action = persona.order[interventionStep % persona.order.length];
    interventionStep++;
    final automatic = trained && persona.preferences.autoAdjust;
    if (automatic) {
      _applyAction(action);
    } else {
      pendingAction = action;
    }
    events.add('피로 → $action ${automatic ? '자동 조정' : '제안'}');
    notice = automatic ? '$action 조정으로 잠깐 환기해요' : '$action 조정이 도움이 될까요?';
    askingFeedback = true;
    noticeTicks = config.seconds('detailSeconds');
    notifyListeners();
  }

  void feedback(bool helpful) {
    if (pendingAction != null && helpful) _applyAction(pendingAction!);
    pendingAction = null;
    askingFeedback = false;
    events.add(helpful ? '개입 피드백 · 도움 됐어요' : '개입 피드백 · 다른 방법');
    if (!trained) {
      persona.rounds++;
      _persist();
    }
    notice = helpful ? '고마워요. 선호에 반영할게요' : '다음 선호 방법을 제안할게요';
    noticeTicks = config.seconds('focusNoticeSeconds');
    notifyListeners();
  }

  void _applyAction(String action) {
    if (action == '조명') {
      ledOn = true;
      brightness = persona.preferences.fatigueBrightness;
      warmth = persona.preferences.fatigueWarmth;
    }
    if (action == '선풍기') {
      fanOn = true;
      fanSpeed = persona.preferences.fatigueFanSpeed;
    }
  }

  void away() {
    if (!active || !presentationEnabled) return;
    absent = true;
    absentTicks = 0;
    page = DemoPage.details;
    pageTicks = 0;
    events.add('부재 · 일시정지');
    notifyListeners();
  }

  void resume() {
    absent = false;
    absentTicks = 0;
    show(DemoPage.focus);
  }

  void finish() {
    if (!active) return;
    active = false;
    liveSession = false;
    resting = false;
    scenario = false;
    completedSessions++;
    completedSeconds += sessionSeconds;
    ledOn = false;
    fanOn = false;
    notice = '';
    events.add('작업 종료');
    page = DemoPage.report;
    pageTicks = 0;
    notifyListeners();
  }

  void recordFeeling(String value) {
    feeling = value;
    notifyListeners();
  }

  void manual({double? light, double? color, bool? fan, bool? led}) {
    if (light != null) brightness = light;
    if (color != null) warmth = color;
    if (fan != null) fanOn = fan;
    if (led != null) ledOn = led;
    events.add('수동 조정 · 선호 피드백');
    if (events.length > 30) events.removeAt(0);
    touch();
    notifyListeners();
  }

  void _applyPreferences() {
    final p = persona.preferences;
    ledOn = p.lightEnabled;
    brightness = p.brightness;
    warmth = p.warmth;
    fanOn = p.fanEnabled;
    fanSpeed = p.fanSpeed;
  }

  void updateProfile(Persona draft) {
    profiles[profileIndex] = draft;
    _applyPreferences();
    _persist();
    onProfileChanged?.call(draft);
    touch();
    notifyListeners();
  }

  void saveProfile(Persona draft) {
    profiles.add(draft);
    profileIndex = profiles.length - 1;
    _persist();
    onProfileChanged?.call(draft);
    scenario = false;
    workTicks = 0;
    proposal = false;
    show(DemoPage.home);
  }

  /// 보드의 state/phase 한 건을 화면 흐름에 반영한다. 화면에 이미 있는 흐름만 쓴다:
  /// 몰입(FOCUS_*) 진입 → 집중 시작(자동 시작을 껐으면 시작 제안 카드), REST → 쉬는 중,
  /// 자리 비움 → 일시정지, END·IDLE → 작업 종료 요약. 피로(FATIGUE) 진입은 리포트의 횟수로 센다.
  void followLive(DisplayState s, {String? postureLabel}) {
    liveDriven = true;
    final previous = _liveFsm;
    _liveFsm = s.fsmState;
    if (postureLabel != null) posture = postureLabel;
    final focus = s.fsmState.startsWith('FOCUS_');
    final enteredFocus =
        focus && (previous == null || !previous.startsWith('FOCUS_'));
    if (!active) {
      if (enteredFocus && !preferencesOpen) {
        if (persona.preferences.autoStart) {
          start(automatic: true);
          return;
        }
        proposal = true;
      }
      if (!focus && proposal && s.fsmState == 'IDLE') proposal = false;
      notifyListeners();
      return;
    }
    sessionObserved = true;
    if (s.fsmState == 'END' || s.fsmState == 'IDLE') {
      finish();
      return;
    }
    if (s.fsmState == 'FATIGUE' && previous != 'FATIGUE') {
      fatigueEvents++;
      events.add('피로 감지 · 보드');
    }
    final rest = s.fsmState == 'REST';
    if (rest != resting) {
      resting = rest;
      events.add(rest ? '휴식 · 보드' : '휴식 끝 · 보드');
      if (rest && page == DemoPage.focus) {
        page = DemoPage.details;
        pageTicks = 0;
      }
    }
    final away = s.present == false;
    if (away != absent) {
      absent = away;
      absentTicks = 0;
      events.add(away ? '부재 · 일시정지' : '복귀');
      if (away && page == DemoPage.focus) {
        page = DemoPage.details;
        pageTicks = 0;
      }
    }
    notifyListeners();
  }

  /// 보드 연결이 끊겼거나 상태가 오래됐다. 진행 중인 집중은 그대로 두고 메모리 흐름으로 돌아간다.
  void leaveLive() {
    if (!liveDriven) return;
    liveDriven = false;
    _liveFsm = null;
    resting = false;
    absent = false;
    posture = '연결 대기';
    notifyListeners();
  }

  /// 보드에서 온 제안 카드에 답했다. 답변 문구는 기존 피로 카드와 같다.
  void liveAnswered(bool accepted) {
    events.add(accepted ? '제안 피드백 · 좋아요' : '제안 피드백 · 다른 방법');
    if (events.length > 30) events.removeAt(0);
    if (!trained) {
      persona.rounds++;
      _persist();
    }
    notice = accepted ? '고마워요. 선호에 반영할게요' : '다음 선호 방법을 제안할게요';
    noticeTicks = config.seconds('focusNoticeSeconds');
    touch();
    notifyListeners();
  }
}
