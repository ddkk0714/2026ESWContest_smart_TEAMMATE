import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'package:deskmate_ui_demo/name_keyboard.dart';

void main() {
  test('Korean syllables and compound vowels and finals compose', () {
    for (final entry in {
      'ㅎㅏㄴㄱㅡㄹ': '한글',
      'ㄱㅗㅏ': '과',
      'ㅇㅏㄴㅈㅏ': '안자',
      'ㄱㅏㅂㅅㅏ': '갑사',
      'ㄱㅏㅂㅅㅇㅣ': '값이',
      'ㄲㅗㅊ': '꽃'
    }.entries) {
      expect(entry.key.split('').fold('', composeName), entry.value);
    }
    expect(composeName('각', 'backspace'), '가');
    expect(composeName('가', 'backspace'), 'ㄱ');
    expect(composeName('ㄱ', 'backspace'), '');
    expect(composeName('과', 'backspace'), '고');
    expect(composeName('값', 'backspace'), '갑');
    expect(composeName('한', ' '), '한 ');
  });
  test('editing replaces selection and respects name length', () {
    final controller = TextEditingController(text: '새 별명');
    controller.selection = const TextSelection(baseOffset: 0, extentOffset: 4);
    editName(controller, 'ㄴ');
    editName(controller, 'ㅏ');
    expect(controller.text, '나');
    controller.text = 'a' * 16;
    editName(controller, 'b');
    expect(controller.text.length, 16);
    controller.dispose();
  });
  testWidgets(
      'local keyboard stays open through clock ticks and edits Korean names',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final model = DemoModel(DemoConfig(
        jsonDecode(File('assets/config/demo.json').readAsStringSync())
            as Map<String, dynamic>));
    await tester.pumpWidget(DemoApp(model: model, ticking: false));
    await tester.tap(find.text('지우 · 수험생'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('new-profile-slot')));
    await tester.pumpAndSettle();
    final input = find.byKey(const ValueKey('profile-name'));
    await tester.tap(input);
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('name-keyboard')), findsOneWidget);
    expect(tester.testTextInput.isVisible, isFalse);
    await tester.tap(find.byTooltip('이름 지우기'));
    await tester.pump();
    for (final key in ['ㄴ', 'ㅏ', 'ㅁ', 'ㅜ']) {
      await tester.tap(find.widgetWithText(TextButton, key));
      await tester.pump();
      model.tick();
      await tester.pump();
    }
    expect(tester.widget<TextField>(input).controller!.text, '나무');
    expect(find.byKey(const ValueKey('name-keyboard')), findsOneWidget);
    expect(tester.getRect(find.byKey(const ValueKey('name-keyboard'))).bottom,
        lessThan(600));
    await tester.tap(find.text('한 → EN'));
    await tester.pump();
    await tester.tap(find.widgetWithText(TextButton, 'a'));
    await tester.pump();
    expect(tester.widget<TextField>(input).controller!.text, '나무a');
    await tester.tap(input);
    await tester.pump();
    await tester.sendKeyEvent(LogicalKeyboardKey.keyB, character: 'b');
    await tester.pump();
    expect(tester.widget<TextField>(input).controller!.text, contains('b'));
    await tester.tap(find.text('입력 완료'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('name-keyboard')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('profile-next')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('creation-avatar-cat')), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
}
