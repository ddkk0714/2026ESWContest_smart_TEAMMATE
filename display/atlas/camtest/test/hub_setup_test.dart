import 'dart:io';

import 'package:camtest/hub_config.dart';
import 'package:camtest/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// 보드에는 키보드가 없다고 본다. 이 화면이 터치만으로 끝나는지 본다.
void main() {
  late Directory dir;
  late HubStore store;

  setUp(() {
    dir = Directory.systemTemp.createTempSync('camtest-setup');
    store = HubStore(candidates: ['${dir.path}/cfg/hub.txt']);
  });
  tearDown(() => dir.deleteSync(recursive: true));

  Future<void> show(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(PostureApp(store: store));
    await tester.pump();
    await tester.pump();
  }

  Future<void> openPad(WidgetTester tester) async {
    await tester.tap(find.byKey(const ValueKey('hub-setup')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
  }

  Future<void> type(WidgetTester tester, String keys) async {
    for (final key in keys.split('')) {
      await tester.tap(find.byKey(ValueKey('hub-key-$key')));
      await tester.pump();
    }
  }

  testWidgets('키패드로 찍은 주소가 보드에 저장된다', (tester) async {
    await show(tester);
    // 주소가 없으면 데모로 뜬다.
    expect(find.text('DEMO · 실데이터 아님'), findsOneWidget);

    await openPad(tester);
    await type(tester, '172.16.34.198');
    await tester.tap(find.byKey(const ValueKey('hub-save')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));

    expect(store.read(), 'http://172.16.34.198:8765');
    // 데모 배지가 사라지고 실제 주소를 보러 간다.
    expect(find.text('DEMO · 실데이터 아님'), findsNothing);
  });

  testWidgets('주소가 덜 찍혔으면 저장이 안 눌린다', (tester) async {
    await show(tester);
    await openPad(tester);
    await type(tester, '172.16');
    await tester.pump();

    final save = tester.widget<FilledButton>(find.byKey(const ValueKey('hub-save')));
    expect(save.onPressed, isNull);
    expect(find.text('아직 주소가 아닙니다'), findsOneWidget);
  });

  String shownText(WidgetTester tester) =>
      tester.widget<Text>(find.byKey(const ValueKey('hub-input'))).data!;

  testWidgets('점은 연달아 · 네 번째로 안 찍힌다', (tester) async {
    await show(tester);
    await openPad(tester);
    await type(tester, '1..2.3.4');
    expect(shownText(tester), '1.2.3.4');

    // 네 번째 점은 무시된다. 숫자는 계속 받는다 — 1.2.3.45 도 주소다.
    await type(tester, '.');
    expect(shownText(tester), '1.2.3.4');
    await type(tester, '5');
    expect(shownText(tester), '1.2.3.45');
  });

  testWidgets('주소가 못 되는 숫자는 눌러도 안 들어간다', (tester) async {
    await show(tester);
    await openPad(tester);

    // 256 은 옥텟이 될 수 없다. 6 을 안 받는다.
    await type(tester, '256');
    expect(shownText(tester), '25');
    // 옥텟은 세 자리까지.
    await type(tester, '55');
    expect(shownText(tester), '255');
    await type(tester, '.10');
    expect(shownText(tester), '255.10');
  });

  testWidgets('지우기로 되돌린다', (tester) async {
    await show(tester);
    await openPad(tester);
    await type(tester, '10.0.0.15');
    await tester.tap(find.byKey(const ValueKey('hub-key-<')));
    await tester.pump();

    final shown = tester.widget<Text>(find.byKey(const ValueKey('hub-input')));
    expect(shown.data, '10.0.0.1');
  });

  testWidgets('데모로 누르면 저장된 주소를 지운다', (tester) async {
    store.save('http://172.16.34.198:8765');
    await show(tester);
    expect(find.text('DEMO · 실데이터 아님'), findsNothing);

    await openPad(tester);
    await tester.tap(find.byKey(const ValueKey('hub-demo')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));

    expect(store.read(), isNull);
    expect(find.text('DEMO · 실데이터 아님'), findsOneWidget);
  });

  testWidgets('저장된 주소는 다시 켤 때 그대로 쓰인다', (tester) async {
    store.save('http://10.0.0.5:8765');
    await show(tester);

    await openPad(tester);
    // 현재 값이 채워져 있어야 한 자리만 고칠 수 있다.
    final shown = tester.widget<Text>(find.byKey(const ValueKey('hub-input')));
    expect(shown.data, '10.0.0.5');
  });
}
