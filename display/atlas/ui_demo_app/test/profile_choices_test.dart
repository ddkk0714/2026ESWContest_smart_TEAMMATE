import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/profile_avatars.dart';
import 'package:deskmate_ui_demo/profile_creation.dart';
import 'package:deskmate_ui_demo/personal_settings.dart';
import 'package:deskmate_ui_demo/profile_store.dart';

DemoModel model() => DemoModel(
    DemoConfig(jsonDecode(File('assets/config/demo.json').readAsStringSync())
        as Map<String, dynamic>),
    presentationEnabled: true);

void main() {
  Future<void> open(WidgetTester tester, Widget child) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(
        home: Builder(
            builder: (context) => TextButton(
                onPressed: () => showDialog<void>(
                    context: context,
                    builder: (_) => child is PersonalSettings
                        ? child
                        : Dialog(
                            child: SizedBox(
                                width: 920, height: 560, child: child))),
                child: const Text('open')))));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  testWidgets('new profile selects a categorized avatar and a new occupation',
      (tester) async {
    final m = model();
    await open(tester, ProfileCreation(model: m, onBack: () {}));
    await tester.tap(find.byKey(const ValueKey('profile-next')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ChoiceChip, '사람'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('creation-avatar-cat')), findsNothing);
    await tester
        .ensureVisible(find.byKey(const ValueKey('creation-avatar-astronaut')));
    await tester.tap(find.byKey(const ValueKey('creation-avatar-astronaut')));
    await tester.tap(find.byKey(const ValueKey('profile-next')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('연구원'));
    await tester.tap(find.text('연구원'));
    for (var step = 2; step < 9; step++) {
      await tester.tap(find.byKey(const ValueKey('profile-next')));
      await tester.pumpAndSettle();
    }
    expect(m.persona.avatar, 'astronaut');
    expect(m.persona.job, '연구원');
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
      'existing profile edits new choices and retains them after reload',
      (tester) async {
    final m = model();
    await open(tester, PersonalSettings(model: m, initialTab: 3));
    await tester.tap(find.text('나를 닮은 이미지 바꾸기'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('동물'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const ValueKey('avatar-penguin')));
    await tester.tap(find.byKey(const ValueKey('avatar-penguin')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('개발자'));
    await tester.tap(find.text('개발자'));
    await tester.tap(find.byKey(const ValueKey('save-personal-settings')));
    await tester.pumpAndSettle();
    final dir = Directory.systemTemp.createTempSync('profile-choices-');
    try {
      final store = ProfileStore(candidates: ['${dir.path}/profiles.json']);
      expect(store.save(m.profiles, m.profileIndex), isTrue);
      final loaded = store.load()!;
      expect(loaded.profiles[loaded.selected].avatar, 'penguin');
      expect(loaded.profiles[loaded.selected].job, '개발자');
    } finally {
      dir.deleteSync(recursive: true);
    }
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  test('all avatar choices have bundled PNG artwork', () {
    for (final id in avatarChoices.keys) {
      final bytes = File('assets/avatars/$id.png').readAsBytesSync();
      expect(bytes.take(8), [137, 80, 78, 71, 13, 10, 26, 10], reason: id);
    }
    expect(avatarGroups.values.expand((ids) => ids).toSet(),
        avatarChoices.keys.toSet());
  });
}
