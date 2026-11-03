import 'package:flutter/material.dart';
import 'demo_model.dart';
import 'profile_avatars.dart';
import 'profile_creation.dart';
import 'page_transition.dart';

class ProfilePicker extends StatefulWidget {
  const ProfilePicker({super.key, required this.model});
  final DemoModel model;
  @override
  State<ProfilePicker> createState() => _ProfilePickerState();
}

class _ProfilePickerState extends State<ProfilePicker> {
  bool managing = false;
  bool creating = false;
  static const green = Color(0xFF356C59);
  static const muted = Color(0xFF7A8580);

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: widget.model,
        builder: (context, _) {
          return PageTransitionView(pageKey: creating, child: _content());
        },
      );

  Future<void> _confirmDelete(int index) async {
    final name = widget.model.profiles[index].name;
    final ok = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              title: Text('$name 프로필을 지울까요?'),
              content: const Text('지운 프로필은 되돌릴 수 없어요.'),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('취소')),
                TextButton(
                    key: const ValueKey('confirm-delete-profile'),
                    style: TextButton.styleFrom(
                        foregroundColor: const Color(0xFFB3261E)),
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('지우기')),
              ],
            ));
    if (ok == true && mounted) widget.model.deleteProfile(index);
  }

  Widget _content() {
    final model = widget.model;
    if (creating) {
      return Dialog(
        backgroundColor: const Color(0xFFF4F2EB),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(28)),
        child: SizedBox(
            width: 740,
            height: 540,
            child: ProfileCreation(
                model: model, onBack: () => setState(() => creating = false))),
      );
    }
    final compact = model.profileSlotCount > 4;
    final avatarSize = compact ? 64.0 : 94.0;
    return Dialog(
      backgroundColor: const Color(0xFFF4F2EB),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(28)),
      child: SizedBox(
        key: const ValueKey('profile-picker'),
        width: 620,
        height: compact ? 540 : 500,
        child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(children: [
              SizedBox(
                  height: 40,
                  child: Stack(alignment: Alignment.center, children: [
                    Center(
                        child: Text(managing ? '프로필 이미지 바꾸기' : '누구의 책상인가요?',
                            style: const TextStyle(fontSize: 25))),
                    Align(
                        alignment: Alignment.centerRight,
                        child: IconButton(
                            tooltip: '프로필 선택 닫기',
                            onPressed: () => Navigator.pop(context),
                            icon: const Icon(Icons.close_rounded, size: 22))),
                  ])),
              const SizedBox(height: 18),
              Expanded(
                  child: GridView.builder(
                key: const ValueKey('profile-slots'),
                gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                    crossAxisCount: 2,
                    mainAxisExtent: compact ? 112 : 148,
                    mainAxisSpacing: 10,
                    crossAxisSpacing: 24),
                itemCount: model.profileSlotCount,
                itemBuilder: (context, index) {
                  if (index == model.profileSlotCount - 1) {
                    return InkWell(
                        key: const ValueKey('new-profile-slot'),
                        borderRadius: BorderRadius.circular(18),
                        onTap: () => setState(() => creating = true),
                        child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Container(
                                  width: avatarSize + 8,
                                  height: avatarSize + 8,
                                  decoration: BoxDecoration(
                                      color: const Color(0xFFE2E7DC),
                                      borderRadius: BorderRadius.circular(22)),
                                  child: Icon(Icons.add_rounded,
                                      size: avatarSize * .5, color: green)),
                              const SizedBox(height: 8),
                              const Text('새 프로필',
                                  style: TextStyle(fontSize: 16, color: muted)),
                            ]));
                  }
                  if (index >= model.profiles.length) {
                    return SizedBox(key: ValueKey('empty-profile-$index'));
                  }
                  final profile = model.profiles[index];
                  final selected = model.profileIndex == index;
                  // 마지막 하나와 집중 중인 지금 프로필은 지우지 않는다
                  final deletable =
                      model.profiles.length > 1 && !(selected && model.active);
                  return Tooltip(
                      message: managing
                          ? '${profile.name} 이미지 변경'
                          : '${profile.name} 선택',
                      child: InkWell(
                          key: ValueKey('profile-slot-$index'),
                          borderRadius: BorderRadius.circular(18),
                          onTap: () async {
                            if (managing) {
                              final avatar = await pickProfileAvatar(
                                  context, profile.avatar,
                                  onDelete: deletable
                                      ? () => _confirmDelete(index)
                                      : null);
                              if (avatar != null && mounted) {
                                model.setAvatar(index, avatar);
                              }
                            } else {
                              model.selectProfile(index);
                              if (context.mounted) {
                                Navigator.pop(context);
                              }
                            }
                          },
                          child: Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                // 관리 모드의 삭제 버튼은 이미지 밖 오른쪽 위에 둔다
                                Stack(children: [
                                  Padding(
                                      padding: const EdgeInsets.fromLTRB(
                                          22, 8, 22, 0),
                                      child: Container(
                                          padding: const EdgeInsets.all(3),
                                          decoration: BoxDecoration(
                                              borderRadius:
                                                  BorderRadius.circular(
                                                      avatarSize * .34 + 4),
                                              border: Border.all(
                                                  color: selected
                                                      ? green
                                                      : Colors.transparent,
                                                  width: 1)),
                                          child: Stack(
                                              alignment: Alignment.center,
                                              children: [
                                                ProfileAvatar(
                                                    id: profile.avatar,
                                                    size: avatarSize),
                                                if (managing)
                                                  Container(
                                                      width: avatarSize,
                                                      height: avatarSize,
                                                      decoration: BoxDecoration(
                                                          color: Colors.black
                                                              .withValues(
                                                                  alpha: .25),
                                                          borderRadius:
                                                              BorderRadius
                                                                  .circular(
                                                                      avatarSize *
                                                                          .34)),
                                                      child: const Icon(
                                                          Icons.edit_outlined,
                                                          color: Colors.white,
                                                          size: 28)),
                                              ]))),
                                  if (managing && deletable)
                                    Positioned(
                                        top: 0,
                                        right: 0,
                                        child: Tooltip(
                                            message: '${profile.name} 프로필 삭제',
                                            // 보이는 원은 22, 누르는 범위는 36(터치 화면)
                                            child: GestureDetector(
                                                key: ValueKey(
                                                    'profile-delete-$index'),
                                                behavior:
                                                    HitTestBehavior.opaque,
                                                onTap: () =>
                                                    _confirmDelete(index),
                                                child: SizedBox(
                                                    width: 36,
                                                    height: 36,
                                                    child: Align(
                                                        alignment:
                                                            Alignment.topRight,
                                                        child: Container(
                                                            width: 22,
                                                            height: 22,
                                                            decoration: BoxDecoration(
                                                                color: Colors
                                                                    .white,
                                                                shape: BoxShape
                                                                    .circle,
                                                                border: Border.all(
                                                                    color: const Color(
                                                                        0xFFE1E5DC))),
                                                            child: const Icon(
                                                                Icons
                                                                    .delete_outline_rounded,
                                                                color: Color(0xFFB3261E),
                                                                size: 15))))))),
                                ]),
                                const SizedBox(height: 8),
                                Text(profile.name,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: TextStyle(
                                        fontSize: 16,
                                        color: selected
                                            ? green
                                            : const Color(0xFF263B37))),
                              ])));
                },
              )),
              const SizedBox(height: 12),
              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                OutlinedButton(
                    onPressed: () => setState(() => managing = !managing),
                    child: Text(managing ? '완료' : '프로필 관리')),
                const SizedBox(width: 12),
                TextButton(
                    onPressed: () => Navigator.pop(context, 'preferences'),
                    child: const Text('나의 책상 설정')),
              ]),
            ])),
      ),
    );
  }
}
