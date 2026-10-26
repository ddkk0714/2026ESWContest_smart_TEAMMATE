import 'package:flutter/material.dart';

const avatarChoices = <String, (String, Color)>{
  'cat': ('고양이', Color(0xFFF6E5D5)),
  'rabbit': ('토끼', Color(0xFFF3E0E7)),
  'sprout': ('새싹', Color(0xFFE2EBD8)),
  'flower': ('벚꽃', Color(0xFFF4DEE4)),
  'star': ('별', Color(0xFFF3EDCE)),
  'moon': ('달', Color(0xFFE5E1F2)),
  'person': ('사람', Color(0xFFE0E9F1)),
  'robot': ('로봇', Color(0xFFDFEAE6)),
  'dog': ('강아지', Color(0xFFF6E5D5)),
  'fox': ('여우', Color(0xFFF8E2D2)),
  'bear': ('곰', Color(0xFFEDE2D7)),
  'panda': ('판다', Color(0xFFE2EBD8)),
  'koala': ('코알라', Color(0xFFE0E9F1)),
  'tiger': ('호랑이', Color(0xFFF3EDCE)),
  'penguin': ('펭귄', Color(0xFFDFEAE6)),
  'owl': ('부엉이', Color(0xFFEDE2D7)),
  'whale': ('고래', Color(0xFFDDEBF3)),
  'butterfly': ('나비', Color(0xFFE5E1F2)),
  'sunflower': ('해바라기', Color(0xFFF3EDCE)),
  'tulip': ('튤립', Color(0xFFF4DEE4)),
  'clover': ('네잎클로버', Color(0xFFE2EBD8)),
  'cactus': ('선인장', Color(0xFFDFEAE6)),
  'tree': ('나무', Color(0xFFE2EBD8)),
  'sun': ('햇살', Color(0xFFF3EDCE)),
  'cloud': ('구름', Color(0xFFE0E9F1)),
  'rainbow': ('무지개', Color(0xFFF3E0E7)),
  'heart': ('마음', Color(0xFFE5E1F2)),
  'planet': ('행성', Color(0xFFE5E1F2)),
  'artist': ('예술가', Color(0xFFF4DEE4)),
  'astronaut': ('우주인', Color(0xFFE0E9F1)),
  'scientist': ('과학자', Color(0xFFDFEAE6)),
  'technologist': ('개발자', Color(0xFFF3EDCE)),
};

const avatarGroups = <String, List<String>>{
  '동물': [
    'cat',
    'rabbit',
    'dog',
    'fox',
    'bear',
    'panda',
    'koala',
    'tiger',
    'penguin',
    'owl',
    'whale',
    'butterfly'
  ],
  '식물': ['sprout', 'flower', 'sunflower', 'tulip', 'clover', 'cactus', 'tree'],
  '하늘·사물': ['star', 'moon', 'sun', 'cloud', 'rainbow', 'heart', 'planet'],
  '사람': ['person', 'robot', 'artist', 'astronaut', 'scientist', 'technologist'],
};

class ProfileAvatar extends StatelessWidget {
  const ProfileAvatar({super.key, required this.id, this.size = 72});
  final String id;
  final double size;
  @override
  Widget build(BuildContext context) {
    final safeId = avatarChoices.containsKey(id) ? id : 'cat';
    final choice = avatarChoices[safeId]!;
    return Container(
        width: size,
        height: size,
        padding: EdgeInsets.all(size * .19),
        decoration: BoxDecoration(
            color: choice.$2, borderRadius: BorderRadius.circular(size * .34)),
        child: Image.asset('assets/avatars/$safeId.png',
            semanticLabel: choice.$1));
  }
}

class ProfileAvatarChoices extends StatefulWidget {
  const ProfileAvatarChoices(
      {super.key,
      required this.selected,
      required this.onChanged,
      this.keyPrefix = 'avatar'});
  final String selected;
  final ValueChanged<String> onChanged;
  final String keyPrefix;

  @override
  State<ProfileAvatarChoices> createState() => _ProfileAvatarChoicesState();
}

class _ProfileAvatarChoicesState extends State<ProfileAvatarChoices> {
  String group = '전체';

  @override
  Widget build(BuildContext context) => Column(children: [
        Wrap(
            alignment: WrapAlignment.center,
            spacing: 8,
            runSpacing: 6,
            children: [
              for (final name in ['전체', ...avatarGroups.keys])
                ChoiceChip(
                    label: Text(name),
                    selected: group == name,
                    onSelected: (_) => setState(() => group = name)),
            ]),
        const SizedBox(height: 16),
        Wrap(
            alignment: WrapAlignment.center,
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final id
                  in group == '전체' ? avatarChoices.keys : avatarGroups[group]!)
                Semantics(
                    selected: widget.selected == id,
                    button: true,
                    label: avatarChoices[id]!.$1,
                    child: InkWell(
                        key: ValueKey('${widget.keyPrefix}-$id'),
                        borderRadius: BorderRadius.circular(18),
                        onTap: () => widget.onChanged(id),
                        child: Container(
                            width: 82,
                            height: 94,
                            decoration: BoxDecoration(
                                color: widget.selected == id
                                    ? const Color(0xFFE2EBD8)
                                    : null,
                                border: Border.all(
                                    width: 2,
                                    color: widget.selected == id
                                        ? const Color(0xFF356C59)
                                        : Colors.transparent),
                                borderRadius: BorderRadius.circular(18)),
                            child: Column(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  ProfileAvatar(id: id, size: 56),
                                  const SizedBox(height: 5),
                                  Text(avatarChoices[id]!.$1,
                                      style: const TextStyle(fontSize: 12)),
                                ])))),
            ]),
      ]);
}

Future<String?> pickProfileAvatar(BuildContext context, String current) =>
    showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('나를 닮은 이미지'),
        content: SizedBox(
            width: 640,
            height: 340,
            child: SingleChildScrollView(
                child: ProfileAvatarChoices(
                    selected: current,
                    onChanged: (id) => Navigator.pop(context, id)))),
        actions: [
          const Text('Twemoji · CC BY 4.0',
              style: TextStyle(fontSize: 10, color: Color(0xFF7A8580))),
          TextButton(
              onPressed: () => Navigator.pop(context), child: const Text('닫기'))
        ],
      ),
    );
