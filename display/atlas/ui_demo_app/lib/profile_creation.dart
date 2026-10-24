import 'dart:math';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'demo_model.dart';
import 'profile_avatars.dart';
import 'name_keyboard.dart';
import 'placement_sketch.dart';

class ProfileCreation extends StatefulWidget {
  const ProfileCreation({super.key, required this.model, required this.onBack});
  final DemoModel model;
  final VoidCallback onBack;

  @override
  State<ProfileCreation> createState() => _ProfileCreationState();
}

class _ProfileCreationState extends State<ProfileCreation> {
  static const green = Color(0xFF356C59);
  static const muted = Color(0xFF7A8580);
  final name = TextEditingController();
  final random = Random();
  final nameFocus = FocusNode();
  static const nicknames = [
    '느긋한 새싹',
    '포근한 구름',
    '빛나는 별',
    '다정한 고양이',
    '잔잔한 호수',
    '따뜻한 햇살',
    '꿈꾸는 토끼',
    '고요한 달빛'
  ];
  @override
  void initState() {
    super.initState();
    name.text = nicknames[random.nextInt(nicknames.length)];
  }

  void shuffleName() {
    final choices = nicknames.where((v) => v != name.text).toList();
    setState(() {
      name.text = choices[random.nextInt(choices.length)];
    });
  }

  bool typing = false;
  int step = 0;
  String avatar = 'sprout';
  String job = '수험생';
  int age = 20;
  int sleep = 7;
  String sound = '백색 소음';
  String mood = '보통';
  String placement = 'center';

  /// 장치 위치를 보드에 보낸 결과 한 줄(환영 화면에 잠깐). 보낼 곳이 없으면 null.
  String? placementNote;
  final order = ['조명', '선풍기', '알림'];
  static const questions = [
    ('어떤 이름으로 불러드릴까요?', '이 책상에서 편하게 불리고 싶은 이름을 알려주세요.'),
    ('당신을 닮은 친구를 골라주세요.', '마음이 가는 모습이면 충분해요.'),
    ('이 책상에서 무엇에 몰두하나요?', '당신의 하루를 조금 더 알고 싶어요.'),
    ('지금, 몇 살의 당신인가요?', '당신의 속도에 한 걸음 더 가까워질게요.'),
    ('하루에 얼마나 잠을 자나요?', '열심히 하는 만큼, 쉬는 시간도 소중하니까요.'),
    ('졸릴 때, 무엇부터 도와드릴까요?', '화살표로 먼저 받고 싶은 도움을 위에 놓아주세요.'),
    ('어떤 소리와 함께 집중하나요?', '고요함도, 작은 소리도 당신의 취향이에요.'),
    ('오늘의 당신은 어떤가요?', '잘하려고 애쓰지 않아도 괜찮아요. 지금 그대로 알려주세요.'),
    ('장치를 보통 어디에 두나요?', '앉은 자리에서 봤을 때'),
  ];

  @override
  void dispose() {
    nameFocus.dispose();
    name.dispose();
    super.dispose();
  }

  void next() {
    FocusScope.of(context).unfocus();
    typing = false;
    if (step == questions.length - 1) {
      widget.model.saveProfile(Persona(
        name: name.text.trim(),
        job: job,
        rounds: 0,
        age: age,
        sleep: sleep,
        sound: sound,
        mood: mood,
        avatar: avatar,
        order: List.of(order),
        placement: placement,
      ));
      final send = widget.model.onPlacement;
      if (send != null) {
        // 보드에 장치 위치를 알린다. 결과는 환영 화면에 잠깐 보이고, 실패해도 등록은 끝난다.
        send(placement).then((note) {
          if (!mounted || note == null) return;
          setState(() => placementNote = note);
          Future<void>.delayed(const Duration(seconds: 4), () {
            if (mounted) setState(() => placementNote = null);
          });
        });
      }
    }
    setState(() => step++);
  }

  @override
  Widget build(BuildContext context) {
    final complete = step == questions.length;
    return Padding(
      key: const ValueKey('profile-creation'),
      padding: const EdgeInsets.fromLTRB(36, 18, 36, 24),
      child: Column(children: [
        Row(children: [
          if (!complete)
            IconButton(
                tooltip: '이전 질문',
                onPressed: () {
                  FocusScope.of(context).unfocus();
                  if (step == 0) {
                    widget.onBack();
                  } else {
                    setState(() => step--);
                  }
                },
                icon: const Icon(Icons.arrow_back_rounded)),
          const Spacer(),
          Text(complete ? '반가워요, 새로운 책상 친구' : '조금씩, 당신을 알아가는 중',
              style: const TextStyle(color: muted, fontSize: 14)),
          const Spacer(),
          IconButton(
              tooltip: '프로필 등록 닫기',
              onPressed: () => Navigator.pop(context),
              icon: const Icon(Icons.close_rounded)),
        ]),
        if (!complete) ...[
          const SizedBox(height: 8),
          ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                  value: (step + 1) / questions.length,
                  minHeight: 3,
                  color: green,
                  backgroundColor: const Color(0xFFE1E5DC))),
        ],
        Expanded(
            child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 300),
          transitionBuilder: (child, animation) => FadeTransition(
              opacity: animation,
              child: SlideTransition(
                  position:
                      Tween(begin: const Offset(0, .035), end: Offset.zero)
                          .animate(animation),
                  child: child)),
          child: SingleChildScrollView(
            key: ValueKey(step),
            padding:
                EdgeInsets.symmetric(vertical: typing && step == 0 ? 4 : 22),
            child: complete
                ? _welcome()
                : typing && step == 0
                    ? Column(children: [
                        const Text('편하게 이름을 적어주세요.',
                            style: TextStyle(fontSize: 24)),
                        const SizedBox(height: 8),
                        _answer(),
                        const SizedBox(height: 6),
                        NameKeyboard(
                            onKey: (key) => setState(() => editName(name, key)),
                            onDone: () => setState(() {
                                  typing = false;
                                  nameFocus.unfocus();
                                })),
                      ])
                    : Column(children: [
                        Text('${step + 1} / ${questions.length}',
                            style: const TextStyle(color: green, fontSize: 14)),
                        const SizedBox(height: 16),
                        Text(questions[step].$1,
                            key: const ValueKey('profile-question'),
                            textAlign: TextAlign.center,
                            style: const TextStyle(fontSize: 32, height: 1.4)),
                        const SizedBox(height: 10),
                        Text(questions[step].$2,
                            textAlign: TextAlign.center,
                            style: const TextStyle(
                                color: muted, fontSize: 15, height: 1.5)),
                        const SizedBox(height: 26),
                        _answer(),
                      ]),
          ),
        )),
        if (complete)
          FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('내 책상으로 가기')),
        if (!complete)
          SizedBox(
              width: 200,
              child: FilledButton(
                  key: const ValueKey('profile-next'),
                  onPressed:
                      step == 0 && name.text.trim().isEmpty ? null : next,
                  child:
                      Text(step == questions.length - 1 ? '당신을 기억할게요' : '다음'))),
      ]),
    );
  }

  Widget _answer() => switch (step) {
        0 => SizedBox(
            width: 420,
            child: Focus(
                onKeyEvent: (_, event) {
                  if (event is! KeyDownEvent && event is! KeyRepeatEvent) {
                    return KeyEventResult.ignored;
                  }
                  if (event.logicalKey == LogicalKeyboardKey.backspace) {
                    setState(() => editName(name, 'backspace'));
                    return KeyEventResult.handled;
                  }
                  if (event.logicalKey == LogicalKeyboardKey.enter) {
                    setState(() {
                      typing = false;
                      nameFocus.unfocus();
                    });
                    return KeyEventResult.handled;
                  }
                  if (HardwareKeyboard.instance.isControlPressed ||
                      HardwareKeyboard.instance.isMetaPressed) {
                    return KeyEventResult.ignored;
                  }
                  if (event.character != null && event.character!.isNotEmpty) {
                    setState(() => editName(name, event.character!));
                    return KeyEventResult.handled;
                  }
                  return KeyEventResult.ignored;
                },
                child: Column(children: [
                  TextField(
                    key: const ValueKey('profile-name'),
                    controller: name,
                    focusNode: nameFocus,
                    readOnly: true,
                    showCursor: true,
                    onTap: () => setState(() => typing = true),
                    maxLength: 16,
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: typing ? 24 : 30),
                    textInputAction: TextInputAction.done,
                    onChanged: (_) => setState(() {}),
                    onSubmitted: (_) {
                      if (name.text.trim().isNotEmpty) next();
                    },
                    decoration: InputDecoration(
                        contentPadding: EdgeInsets.symmetric(
                            horizontal: 16, vertical: typing ? 10 : 20),
                        suffixIcon: name.text.isEmpty
                            ? null
                            : IconButton(
                                tooltip: '이름 지우기',
                                onPressed: () => setState(() => name.clear()),
                                icon: const Icon(Icons.clear_rounded)),
                        hintText: '이름 또는 별명',
                        counterText: '',
                        filled: true,
                        fillColor: Color(0xFFFFFEF8),
                        border: const OutlineInputBorder(
                            borderRadius:
                                BorderRadius.all(Radius.circular(20)))),
                  ),
                  SizedBox(height: typing ? 0 : 12),
                  TextButton.icon(
                      key: const ValueKey('profile-random-name'),
                      onPressed: shuffleName,
                      icon: const Icon(Icons.shuffle_rounded, size: 20),
                      label: const Text('다른 별명 만나보기')),
                ]))),
        1 => Wrap(
              spacing: 14,
              runSpacing: 12,
              alignment: WrapAlignment.center,
              children: [
                for (final entry in avatarChoices.entries)
                  Semantics(
                      selected: avatar == entry.key,
                      button: true,
                      label: entry.value.$1,
                      child: InkWell(
                          key: ValueKey('creation-avatar-${entry.key}'),
                          borderRadius: BorderRadius.circular(22),
                          onTap: () => setState(() => avatar = entry.key),
                          child: Container(
                              padding: const EdgeInsets.all(4),
                              decoration: BoxDecoration(
                                  borderRadius: BorderRadius.circular(22),
                                  border: Border.all(
                                      color: avatar == entry.key
                                          ? green
                                          : Colors.transparent,
                                      width: 2)),
                              child: ProfileAvatar(id: entry.key, size: 64)))),
              ]),
        2 => _choices(['수험생', '재택 직장인', '창작·독서'], job, (v) => job = v),
        3 => _number('$age세', () {
            if (age > 1) setState(() => age--);
          }, () {
            if (age < 100) setState(() => age++);
          }),
        4 => _number('$sleep시간', () {
            if (sleep > 1) setState(() => sleep--);
          }, () {
            if (sleep < 12) setState(() => sleep++);
          }),
        5 => SizedBox(
            width: 360,
            child: Column(children: [
              for (int i = 0; i < order.length; i++)
                Row(children: [
                  Text('${i + 1}',
                      style: const TextStyle(color: muted, fontSize: 18)),
                  const SizedBox(width: 24),
                  Text(order[i], style: const TextStyle(fontSize: 24)),
                  const Spacer(),
                  IconButton(
                      tooltip: '${order[i]} 우선순위 올리기',
                      onPressed: i == 0
                          ? null
                          : () => setState(() {
                                final value = order.removeAt(i);
                                order.insert(i - 1, value);
                              }),
                      icon: const Icon(Icons.arrow_upward_rounded)),
                ]),
            ])),
        6 => _choices(['백색 소음', '카페 음악', '무음'], sound, (v) => sound = v),
        7 => _choices(['좋음', '보통', '피곤'], mood, (v) => mood = v),
        _ => PlacementChoice(
            value: placement,
            onChanged: (v) => setState(() => placement = v)),
      };

  Widget _choices(
          List<String> options, String selected, ValueChanged<String> change) =>
      Wrap(
          spacing: 14,
          runSpacing: 12,
          alignment: WrapAlignment.center,
          children: [
            for (final option in options)
              ChoiceChip(
                label: Text(option, style: const TextStyle(fontSize: 22)),
                padding:
                    const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                selected: selected == option,
                selectedColor: const Color(0xFFE0E9D9),
                onSelected: (_) => setState(() => change(option)),
              )
          ]);

  Widget _number(String value, VoidCallback minus, VoidCallback plus) =>
      Row(mainAxisAlignment: MainAxisAlignment.center, children: [
        IconButton(
            tooltip: '줄이기',
            onPressed: minus,
            icon: const Icon(Icons.remove_circle_outline, size: 32)),
        SizedBox(
            width: 180,
            child: Text(value,
                textAlign: TextAlign.center,
                style: const TextStyle(fontFamily: 'LGClock', fontSize: 44))),
        IconButton(
            tooltip: '늘리기',
            onPressed: plus,
            icon: const Icon(Icons.add_circle_outline, size: 32)),
      ]);

  Widget _welcome() => Column(children: [
        ProfileAvatar(id: avatar, size: 72),
        const SizedBox(height: 20),
        Text(
            '${name.text.trim().endsWith('님') ? name.text.trim() : '${name.text.trim()}님'},\n당신은 이런 분이군요.',
            key: const ValueKey('profile-welcome'),
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 30, height: 1.3)),
        const SizedBox(height: 12),
        Text(
            '$job의 하루를 보내며,\n${sound == '무음' ? '고요함' : sound} 속에서 몰입을 찾는 분.\n잠시 지칠 땐 ${order.first}부터 곁에 둘게요.',
            textAlign: TextAlign.center,
            style: const TextStyle(color: green, fontSize: 17, height: 1.5)),
        const SizedBox(height: 12),
        const Text('오늘도, 당신의 속도로.\n우리, 천천히 알아가요.',
            textAlign: TextAlign.center,
            style: TextStyle(color: muted, fontSize: 16, height: 1.6)),
      ]);
}
