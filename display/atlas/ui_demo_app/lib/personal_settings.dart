import 'package:flutter/material.dart';
import 'demo_model.dart';
import 'profile_avatars.dart';
import 'page_transition.dart';
import 'placement_sketch.dart';
import 'live/connection_panel.dart';
import 'live/live_link.dart';
import 'live/profile_payload.dart' show clockText;

class PersonalSettings extends StatefulWidget {
  const PersonalSettings({super.key, required this.model, this.live});
  final DemoModel model;

  /// 보드 연결. 있으면 '연결' 탭이 보인다.
  final LiveLink? live;
  @override
  State<PersonalSettings> createState() => _PersonalSettingsState();
}

class _PersonalSettingsState extends State<PersonalSettings> {
  static const green = Color(0xFF356C59);
  static const muted = Color(0xFF7A8580);
  late final Persona draft = widget.model.persona.copy();
  DeskPreferences get p => draft.preferences;
  int tab = 0;
  @override
  Widget build(BuildContext context) => Dialog(
      backgroundColor: const Color(0xFFF4F2EB),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(28)),
      child: SizedBox(
        width: 920,
        height: 540,
        child: Listener(
            onPointerDown: (_) => widget.model.touch(),
            onPointerMove: (_) => widget.model.touch(),
            child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(children: [
                  Row(children: [
                    ProfileAvatar(id: draft.avatar, size: 52),
                    const SizedBox(width: 16),
                    Expanded(
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                          Text('${draft.name}님에게 편안한 책상',
                              style: const TextStyle(fontSize: 25)),
                          const SizedBox(height: 4),
                          const Text('빛도, 바람도, 당신에게 좋은 만큼.',
                              style: TextStyle(color: muted, fontSize: 14)),
                        ])),
                    IconButton(
                        tooltip: '설정 닫기',
                        onPressed: () => Navigator.pop(context),
                        icon: const Icon(Icons.close_rounded)),
                  ]),
                  const SizedBox(height: 20),
                  Expanded(
                      child: Row(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                        SizedBox(
                            width: 170,
                            child: Column(children: [
                              for (final item in [
                                (Icons.wb_sunny_outlined, '빛과 바람'),
                                (Icons.music_note_outlined, '소리'),
                                (Icons.spa_outlined, '몰입'),
                                (Icons.favorite_outline, '나의 리듬'),
                                if (widget.live != null)
                                  (Icons.settings_ethernet_rounded, '연결'),
                              ].indexed)
                                Padding(
                                    // 연결 탭까지 5개면 같은 높이에 들어가게 간격만 조금 줄인다.
                                    padding: EdgeInsets.only(
                                        bottom: widget.live == null ? 8 : 4),
                                    child: ListTile(
                                        key:
                                            ValueKey('settings-tab-${item.$1}'),
                                        visualDensity: widget.live == null
                                            ? null
                                            : VisualDensity.compact,
                                        shape: RoundedRectangleBorder(
                                            borderRadius:
                                                BorderRadius.circular(18)),
                                        selected: tab == item.$1,
                                        selectedColor: green,
                                        selectedTileColor:
                                            const Color(0xFFE3EBDD),
                                        leading: Icon(item.$2.$1),
                                        title: Text(item.$2.$2),
                                        onTap: () =>
                                            setState(() => tab = item.$1))),
                              const Spacer(),
                              Text('당신의 작은 취향이\n편안한 몰입의 시작이에요.',
                                  style: const TextStyle(
                                      color: muted, height: 1.7, fontSize: 13)),
                            ])),
                        const SizedBox(width: 20),
                        Expanded(
                            child: PageTransitionView(
                                pageKey: tab,
                                child: SingleChildScrollView(
                                    key: ValueKey('settings-scroll-$tab'),
                                    child: switch (tab) {
                                      0 => _environment(),
                                      1 => _sound(),
                                      2 => _focus(),
                                      3 => _rhythm(),
                                      _ => ConnectionPanel(live: widget.live!)
                                    }))),
                      ])),
                  const SizedBox(height: 16),
                  Row(children: [
                    const Expanded(
                        child: Text('프로필별로 기억하며, 앱을 닫으면 초기화돼요.',
                            style: TextStyle(color: muted, fontSize: 12))),
                    TextButton(
                        onPressed: () => Navigator.pop(context),
                        child: const Text('취소')),
                    const SizedBox(width: 8),
                    FilledButton(
                        key: const ValueKey('save-personal-settings'),
                        onPressed: () {
                          final placementChanged =
                              draft.placement != widget.model.persona.placement;
                          final messenger = ScaffoldMessenger.maybeOf(context);
                          widget.model.updateProfile(draft);
                          Navigator.pop(context);
                          final send = widget.model.onPlacement;
                          if (placementChanged && send != null) {
                            // 보내기 실패는 잠깐 알리기만 한다. 저장은 이미 끝났다.
                            send(draft.placement).then((note) {
                              if (note != null) {
                                messenger?.showSnackBar(SnackBar(
                                    content: Text(note),
                                    duration: const Duration(seconds: 3)));
                              }
                            });
                          }
                        },
                        child: const Text('이대로 맞춰둘게요')),
                  ]),
                ]))),
      ));

  Widget _card(String title, String subtitle, List<Widget> children) =>
      Container(
          width: double.infinity,
          margin: const EdgeInsets.only(bottom: 14),
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
              color: const Color(0xFFFFFEF8),
              borderRadius: BorderRadius.circular(22)),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title, style: const TextStyle(fontSize: 21)),
            const SizedBox(height: 5),
            Text(subtitle,
                style:
                    const TextStyle(color: muted, fontSize: 13, height: 1.5)),
            const SizedBox(height: 12),
            ...children,
          ]));

  Widget _toggle(
          String id, String title, bool value, ValueChanged<bool> change) =>
      SwitchListTile(
          key: ValueKey('preference-$id'),
          contentPadding: EdgeInsets.zero,
          title: Text(title, style: const TextStyle(fontSize: 16)),
          value: value,
          onChanged: (v) => setState(() => change(v)));

  Widget _slider(String id, String label, String value, double current,
          ValueChanged<double> change,
          {double min = 0, double max = 1, int divisions = 20}) =>
      Column(children: [
        Row(children: [
          Text(label),
          const Spacer(),
          Text(value, style: const TextStyle(color: green, fontSize: 17))
        ]),
        Slider(
            key: ValueKey('preference-$id'),
            value: current,
            min: min,
            max: max,
            divisions: divisions,
            label: value,
            onChanged: (v) => setState(() => change(v))),
      ]);

  Widget _choices(
          List<String> choices, String current, ValueChanged<String> change) =>
      Wrap(spacing: 8, runSpacing: 8, children: [
        for (final option in choices)
          ChoiceChip(
              label: Text(option),
              selected: current == option,
              onSelected: (_) => setState(() => change(option)))
      ]);

  Widget _environment() => Column(children: [
        _card('하루를 감싸는 빛', '눈이 편안한 밝기와 온도로 집중을 시작해요.', [
          Center(
              child: Container(
                  width: 76,
                  height: 76,
                  decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: Color.lerp(const Color(0xFFF6D99E),
                          const Color(0xFFEAF1ED), p.warmth)),
                  child: Icon(Icons.light_mode_outlined,
                      size: 38,
                      color:
                          green.withValues(alpha: .35 + p.brightness * .65)))),
          _toggle('light-enabled', '집중 시작할 때 조명 켜기', p.lightEnabled,
              (v) => p.lightEnabled = v),
          _slider('brightness', '원하는 밝기', '${(p.brightness * 100).round()}%',
              p.brightness, (v) => p.brightness = v),
          _slider(
              'warmth',
              '빛의 온도',
              p.warmth < .35
                  ? '따뜻하게'
                  : p.warmth > .65
                      ? '맑고 시원하게'
                      : '부드럽게',
              p.warmth,
              (v) => p.warmth = v),
        ]),
        _card('곁을 스치는 바람', '가벼운 산들바람부터, 기분 좋은 환기까지.', [
          _toggle('fan-enabled', '집중 시작할 때 바람 켜기', p.fanEnabled,
              (v) => p.fanEnabled = v),
          _slider(
              'fan-speed',
              '원하는 바람 세기',
              ['산들바람', '기분 좋은 바람', '시원한 바람'][p.fanSpeed - 1],
              p.fanSpeed.toDouble(),
              (v) => p.fanSpeed = v.round(),
              min: 1,
              max: 3,
              divisions: 2),
        ]),
        _card('잠시 지칠 때의 환기', '피로할 때 도움받고 싶은 빛과 바람의 정도예요.', [
          _slider(
              'fatigue-brightness',
              '환기할 때 밝기',
              '${(p.fatigueBrightness * 100).round()}%',
              p.fatigueBrightness,
              (v) => p.fatigueBrightness = v),
          _slider(
              'fatigue-warmth',
              '환기할 때 빛의 온도',
              p.fatigueWarmth < .5 ? '따뜻한 쪽' : '시원한 쪽',
              p.fatigueWarmth,
              (v) => p.fatigueWarmth = v),
          _slider(
              'fatigue-fan',
              '환기할 때 바람',
              '${p.fatigueFanSpeed}단계',
              p.fatigueFanSpeed.toDouble(),
              (v) => p.fatigueFanSpeed = v.round(),
              min: 1,
              max: 3,
              divisions: 2),
        ]),
      ]);

  Widget _sound() =>
      _card('당신의 몰입에 어울리는 소리', '좋아하는 소리를 작게 곁에 두어도, 고요하게 머물러도 좋아요.', [
        _toggle('music-enabled', '집중할 때 소리 함께 듣기', p.musicEnabled, (v) {
          p.musicEnabled = v;
          if (v && draft.sound == '무음') draft.sound = '백색 소음';
        }),
        const SizedBox(height: 8),
        _choices(['백색 소음', '카페 음악', '클래식', '무음'], draft.sound, (v) {
          draft.sound = v;
          p.musicEnabled = v != '무음';
        }),
        const SizedBox(height: 22),
        _slider('volume', '편안한 볼륨', '${(p.volume * 100).round()}%', p.volume,
            (v) => p.volume = v),
        const Text('집중을 시작하면 이 선택을 화면 속 재생 상태에 반영해요.',
            style: TextStyle(color: muted, fontSize: 13, height: 1.6)),
      ]);

  Widget _focus() => Column(children: [
        _card('당신의 속도로, 한 번의 몰입', '부담 없이 채울 수 있는 시간을 골라주세요.', [
          _slider('focus-minutes', '한 번의 집중 목표', '${p.focusMinutes}분',
              p.focusMinutes.toDouble(), (v) => p.focusMinutes = v.round(),
              min: 5, max: 120, divisions: 23),
          _toggle('auto-start', '익숙해지면 집중을 자동으로 시작', p.autoStart,
              (v) => p.autoStart = v),
          _toggle('auto-adjust', '익숙해지면 환경을 자동으로 맞춤', p.autoAdjust,
              (v) => p.autoAdjust = v),
          _toggle('start-notice', '집중 시작 안내 보여주기', p.startNotice,
              (v) => p.startNotice = v),
          const Text('아직 알아가는 프로필에는 먼저 물어봐요. 자동 맞춤을 꺼두면 익숙해진 뒤에도 제안부터 해요.',
              style: TextStyle(color: muted, height: 1.6, fontSize: 13)),
        ]),
        _card('도움도, 당신이 좋아하는 순서로', '먼저 받고 싶은 도움을 위로 올려주세요.', [
          for (final item in draft.order.toList().indexed)
            Row(children: [
              Text('${item.$1 + 1}', style: const TextStyle(color: muted)),
              const SizedBox(width: 20),
              Text(item.$2, style: const TextStyle(fontSize: 18)),
              const Spacer(),
              IconButton(
                  tooltip: '설정 ${item.$2} 우선순위 올리기',
                  onPressed: item.$1 == 0
                      ? null
                      : () => setState(() {
                            final value = draft.order.removeAt(item.$1);
                            draft.order.insert(item.$1 - 1, value);
                          }),
                  icon: const Icon(Icons.arrow_upward_rounded)),
            ]),
        ]),
      ]);

  Widget _rhythm() => Column(children: [
        _card('지금의 나를, 조금 더', '처음 답했던 선택도 언제든 바꿀 수 있어요.', [
          Row(children: [
            ProfileAvatar(id: draft.avatar, size: 54),
            const SizedBox(width: 14),
            TextButton(
                onPressed: () async {
                  final avatar = await pickProfileAvatar(context, draft.avatar);
                  if (avatar != null && mounted) {
                    setState(() => draft.avatar = avatar);
                  }
                },
                child: const Text('나를 닮은 이미지 바꾸기'))
          ]),
          const SizedBox(height: 16),
          _choices(['수험생', '재택 직장인', '창작·독서'], draft.job, (v) => draft.job = v),
          const SizedBox(height: 22),
          _slider('age', '지금의 나이', '${draft.age}세', draft.age.toDouble(),
              (v) => draft.age = v.round(),
              min: 1, max: 100, divisions: 99),
          _slider('sleep', '평균 수면 시간', '${draft.sleep}시간',
              draft.sleep.toDouble(), (v) => draft.sleep = v.round(),
              min: 1, max: 12, divisions: 11),
          // 21:00 ~ 다음 날 03:00, 30분 단위. 일어나는 시각은 수면 시간으로 계산한다.
          _slider(
              'bedtime',
              '보통 잠드는 시각',
              '${clockText(draft.bedtimeMinutes)} → ${clockText(draft.bedtimeMinutes + draft.sleep * 60)} 기상',
              (((draft.bedtimeMinutes - 21 * 60) % (24 * 60)) / 30)
                  .clamp(0, 12)
                  .toDouble(),
              (v) => draft.bedtimeMinutes = (21 * 60 + v.round() * 30) % (24 * 60),
              min: 0,
              max: 12,
              divisions: 12),
          const SizedBox(height: 8),
          _choices(['좋음', '보통', '피곤'], draft.mood, (v) => draft.mood = v),
        ]),
        _card('장치를 두는 곳', '앉은 자리에서 봤을 때 장치가 있는 쪽이에요. 자세를 읽는 기준이 돼요.', [
          PlacementChoice(
              value: draft.placement,
              compact: true,
              onChanged: (v) => setState(() => draft.placement = v)),
        ]),
      ]);
}
