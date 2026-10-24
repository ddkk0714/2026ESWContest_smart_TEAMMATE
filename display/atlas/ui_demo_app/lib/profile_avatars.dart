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
};

class ProfileAvatar extends StatelessWidget {
  const ProfileAvatar({super.key, required this.id, this.size = 72});
  final String id;
  final double size;
  @override
  Widget build(BuildContext context) {
    final choice = avatarChoices[id]!;
    return Container(
        width: size,
        height: size,
        padding: EdgeInsets.all(size * .19),
        decoration: BoxDecoration(
            color: choice.$2, borderRadius: BorderRadius.circular(size * .34)),
        child: Image.asset('assets/avatars/$id.png', semanticLabel: choice.$1));
  }
}

Future<String?> pickProfileAvatar(BuildContext context, String current) =>
    showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('나를 닮은 이미지'),
        content: SizedBox(
            width: 520,
            height: 280,
            child: Column(children: [
              const Text('동물 · 식물 · 추상 · 사람',
                  style: TextStyle(color: Color(0xFF7A8580))),
              const SizedBox(height: 16),
              Expanded(
                  child: GridView.count(
                      crossAxisCount: 4,
                      mainAxisSpacing: 10,
                      crossAxisSpacing: 10,
                      childAspectRatio: 1.15,
                      children: [
                    for (final entry in avatarChoices.entries)
                      InkWell(
                          key: ValueKey('avatar-${entry.key}'),
                          borderRadius: BorderRadius.circular(18),
                          onTap: () => Navigator.pop(context, entry.key),
                          child: Container(
                              decoration: BoxDecoration(
                                  border: Border.all(
                                      color: current == entry.key
                                          ? const Color(0xFF356C59)
                                          : Colors.transparent,
                                      width: 2),
                                  borderRadius: BorderRadius.circular(18)),
                              child: Column(
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    ProfileAvatar(id: entry.key, size: 64),
                                    const SizedBox(height: 5),
                                    Text(entry.value.$1,
                                        style: const TextStyle(fontSize: 12)),
                                  ]))),
                  ])),
            ])),
        actions: [
          const Text('Twemoji · CC BY 4.0',
              style: TextStyle(fontSize: 10, color: Color(0xFF7A8580))),
          TextButton(
              onPressed: () => Navigator.pop(context), child: const Text('닫기'))
        ],
      ),
    );
