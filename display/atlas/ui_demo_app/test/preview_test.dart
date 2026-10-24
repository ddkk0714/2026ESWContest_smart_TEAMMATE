import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'package:deskmate_ui_demo/profile_avatars.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    for (final font in {
      'High1': 'High1WonchuriBodyB.ttf',
      'LGText': 'LGEITextTTF-Regular.ttf',
      'LGClock': 'LGEIHeadlineVF.ttf',
      'High1Wonchuri': 'High1WonchuriBodyB.ttf',
      'ComicNeue': 'ComicNeue-Regular.ttf',
      'HakgyoansimGeurimilgi': 'HakgyoansimGeurimilgi-Regular.ttf',
      'LGEIText': 'LGEITextTTF-Regular.ttf',
      'LGEIHeadline': 'LGEIHeadlineVF.ttf'
    }.entries) {
      await (FontLoader(font.key)
            ..addFont(rootBundle.load('assets/fonts/${font.value}')))
          .load();
    }
  });
  testWidgets('render six screens with production fonts for visual review',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final model = DemoModel(DemoConfig(
        jsonDecode(File('assets/config/demo.json').readAsStringSync())
            as Map<String, dynamic>));
    model.now = DateTime(2026, 10, 24, 10, 24);
    final key = GlobalKey();
    await tester.pumpWidget(RepaintBoundary(
        key: key, child: DemoApp(model: model, ticking: false)));
    await tester.runAsync(() async {
      for (final id in avatarChoices.keys) {
        await precacheImage(
            AssetImage('assets/avatars/$id.png'), key.currentContext!);
      }
    });
    Future<void> capture(String name) async {
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(tester.takeException(), isNull, reason: name);
      await tester.runAsync(() async {
        final boundary =
            key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        final file = File('build/qa/$name.png');
        await file.parent.create(recursive: true);
        await file.writeAsBytes(bytes!.buffer.asUint8List());
        image.dispose();
      });
    }

    await capture('home');
    await tester.tap(find.byTooltip('나의 책상 설정'));
    await tester.pumpAndSettle();
    await capture('settings-environment');
    await tester
        .ensureVisible(find.byKey(const ValueKey('preference-fatigue-fan')));
    await capture('settings-environment-more');
    for (int i = 1; i < 4; i++) {
      await tester.tap(find.byKey(ValueKey('settings-tab-$i')));
      await tester.pumpAndSettle();
      await capture('settings-$i');
    }
    await tester.tap(find.byTooltip('설정 닫기'));
    await tester.pumpAndSettle();
    model.proposal = true;
    model.show(DemoPage.home);
    await capture('proposal');
    expect(tester.getCenter(find.byKey(const ValueKey('suggestion-card'))).dx,
        512);
    model.decline();
    model.start();
    for (int i = 0; i < 5; i++) {
      model.tick(DateTime(2026, 10, 24, 10, 24));
    }
    await capture('focus');
    model.fatigue();
    await capture('fatigue-proposal');
    expect(tester.getCenter(find.byKey(const ValueKey('suggestion-card'))),
        const Offset(512, 300));
    await tester.tap(find.text('도움 됐어요'));
    await tester.pump();
    expect(model.events.last, contains('도움 됐어요'));
    model.show(DemoPage.details);
    await capture('details');
    model.show(DemoPage.widgets);
    await capture('widgets');
    model.finish();
    await capture('report');
    model.show(DemoPage.home);
    await tester.pumpAndSettle();
    await tester.tap(find.text('지우 · 수험생'));
    await capture('profiles');
    await tester.tap(find.text('프로필 관리'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('지우 이미지 변경'));
    await capture('avatar-picker');
    await tester.tap(find.text('닫기'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('프로필 선택 닫기'));
    await tester.pumpAndSettle();
    model.saveProfile(
        Persona(name: '추가 프로필', job: '창작·독서', rounds: 0, avatar: 'flower'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('추가 프로필 · 창작·독서'));
    await capture('profiles-six-slots');
    await tester.tap(find.byTooltip('프로필 선택 닫기'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('추가 프로필 · 창작·독서'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('new-profile-slot')));
    await tester.pumpAndSettle();
    await capture('profile');
    await tester.tap(find.byKey(const ValueKey('profile-name')));
    await tester.pumpAndSettle();
    await capture('profile-keyboard');
    await tester.tap(find.text('입력 완료'));
    await tester.pumpAndSettle();
    tester
        .widget<TextField>(find.byKey(const ValueKey('profile-name')))
        .controller!
        .text = '하늘';
    await tester.tap(find.byKey(const ValueKey('profile-random-name')));
    tester
        .widget<TextField>(find.byKey(const ValueKey('profile-name')))
        .controller!
        .text = '하늘';
    await tester.pump();
    for (int i = 0; i < 8; i++) {
      await tester.tap(find.byKey(const ValueKey('profile-next')));
      await tester.pumpAndSettle();
      if (i == 1) await capture('profile-question');
    }
    await capture('profile-welcome');
    await tester.pumpWidget(const SizedBox());
  });
}
