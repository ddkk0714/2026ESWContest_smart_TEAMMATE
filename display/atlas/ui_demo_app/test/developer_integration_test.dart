import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/developer/developer_controller.dart';
import 'package:deskmate_ui_demo/developer/developer_views.dart';
import 'package:deskmate_ui_demo/developer/pr74/bluetooth_service.dart';
import 'package:deskmate_ui_demo/developer/pr74/bluetooth_control_page.dart';
import 'package:deskmate_ui_demo/developer/pr74/developer_page.dart';
import 'package:deskmate_ui_demo/developer/pr74/preview_page.dart';
import 'package:deskmate_ui_demo/developer/pr74/dev_mode_page.dart';
import 'package:deskmate_ui_demo/developer/pr74/settings_store.dart';
import 'package:deskmate_ui_demo/developer/pr74/music_playback.dart';
import 'package:deskmate_ui_demo/live/live_link.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'demo_test.dart' show model;
import 'live_test.dart' show FakeTransport, phase;

class NoBluetooth implements AtlasBluetoothService {
  @override
  Future<void> close() async {}
  @override
  Stream<List<BluetoothDeviceInfo>> scanProgressive(
          {Duration duration = const Duration(seconds: 8),
          Duration interval = const Duration(seconds: 1)}) =>
      Stream.value([]);
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class NoMusic implements MusicPlayback {
  @override
  double get volume => .4;
  @override
  bool get isPlaying => false;
  @override
  Future<void> dispose() async {}
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    for (final entry in {
      'High1': 'High1WonchuriBodyB.ttf',
      'LGText': 'LGEITextTTF-Regular.ttf',
      'LGClock': 'LGEIHeadlineVF.ttf'
    }.entries) {
      await (FontLoader(entry.key)
            ..addFont(rootBundle.load('assets/fonts/${entry.value}')))
          .load();
    }
  });
  late Directory dir;
  late FakeTransport transport;
  late LiveLink live;
  late DeveloperController tools;
  setUp(() {
    dir = Directory.systemTemp.createTempSync('developer-tools-');
    transport = FakeTransport();
    live = LiveLink(transportFactory: (_, __) => transport);
    tools = DeveloperController(
        live: live,
        settings: SettingsStore(candidates: ['${dir.path}/settings.json']),
        bluetooth: NoBluetooth(),
        music: NoMusic(),
        startHardware: false);
  });
  tearDown(() {
    live.dispose();
    dir.deleteSync(recursive: true);
  });

  test(
      'shared MQTT receives independent PC metrics and logs without fabricating sensors',
      () async {
    live.configure('127.0.0.1', 1883);
    await Future<void>.delayed(Duration.zero);
    expect(transport.subscribed, contains(LiveLink.keystrokeTopic));
    expect(tools.state, isNull);
    transport.deliver(LiveLink.stateTopic,
        phase('FOCUS_PC', summary: {'co2_ppm': 915, 'temp_c': 25.3}));
    transport.deliver(LiveLink.keystrokeTopic, {
      'schema_version': '1.0',
      'ts': DateTime.now().millisecondsSinceEpoch / 1000,
      'node': 'pc',
      'data': {'dwell_mean_ms': 87.0, 'event_count': 19, 'valid': true}
    });
    await Future<void>.delayed(Duration.zero);
    expect(tools.state!.co2Ppm, 915);
    expect(tools.state!.humidityPct, isNull);
    expect(tools.keystroke!.eventCount, 19);
    tools.applyPreset(devSuggestions.first);
    final published = transport.published.length;
    await tools.sendFeedback(const PreviewFeedback('accept'));
    expect(transport.published.length, published);
    expect(tools.feedback.enabled, isFalse);
    tools.clearOverride();
    await tools.sendFeedback(const PreviewFeedback('correct',
        correctedState: 'REST', note: '잠깐 쉬었어요'));
    expect(
        transport.sent(LiveLink.feedbackTopic).last['data']['corrected_state'],
        'REST');
    live.configure('127.0.0.2', 1883);
    expect(tools.state, isNull);
    expect(tools.keystrokeEnvelope, isNull);
    tools.dispose();
  });

  testWidgets(
      'entry, all six tabs, raw dialog, isolated UI18 preview and Bluetooth popup fit Pi',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final m = model();
    final capture = GlobalKey();
    await tester.pumpWidget(RepaintBoundary(
        key: capture,
        child: DemoApp(
            model: m,
            live: live,
            tools: tools,
            ticking: false,
            onExit: () {})));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    final btX =
        tester.getCenter(find.byKey(const ValueKey('bluetooth-open'))).dx;
    expect(btX, lessThan(tester.getCenter(find.byTooltip('나의 책상 설정')).dx));
    await tester.tap(find.byKey(const ValueKey('bluetooth-open')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    expect(find.byType(Dialog), findsOneWidget);
    expect(find.byType(BluetoothControlPage), findsOneWidget);
    expect(find.text('오늘도, 당신의 속도로.'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.runAsync(() => _capture(capture, 'bluetooth'));
    await tester.tap(find.byKey(const ValueKey('bluetooth-popup-close')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    await tester.tap(find.byTooltip('발표 조작창'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    await tester.tap(find.byKey(const ValueKey('developer-open')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    expect(find.byType(DeveloperScreen), findsOneWidget);
    expect(m.preferencesOpen, isTrue);
    await tester.runAsync(() => _capture(capture, 'sensors'));
    await tester.tap(find.text('Raw 값'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    expect(find.text('아직 받은 값이 없어요.'), findsOneWidget);
    await tester.tap(find
        .descendant(of: find.byType(Dialog), matching: find.byIcon(Icons.close))
        .first);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    for (final tab in DeveloperTab.values) {
      await tester.tap(find.byKey(ValueKey('developer-tab-${tab.name}')));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 700));
      expect(tester.takeException(), isNull, reason: tab.name);
      if (tab == DeveloperTab.developer) {
        await tester.tap(find.byKey(const ValueKey('dev-focus')));
        await tester.pump();
        await tester.pump(const Duration(milliseconds: 700));
        expect(find.byType(UI18DeveloperPreview), findsOneWidget);
        expect(m.active, isFalse);
        expect(transport.published, isEmpty);
      }
      if (tab == DeveloperTab.preview) {
        expect(find.byType(PreviewPage), findsOneWidget);
      }
      if (tab == DeveloperTab.mode) {
        expect(find.byType(DevModePage), findsOneWidget);
      }
      await tester.runAsync(() => _capture(capture, tab.name));
    }
    await tester.tap(find.text('주소 변경'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    expect(tester.takeException(), isNull);
    Navigator.of(tester.element(find.byType(Dialog))).pop();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    await tester.tap(find.byKey(const ValueKey('developer-close')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    expect(m.preferencesOpen, isFalse);
    expect(find.text('오늘도, 당신의 속도로.'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
}

Future<void> _capture(GlobalKey key, String name) async {
  final boundary =
      key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  final image = await boundary.toImage(pixelRatio: 1);
  final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
  Directory('build/qa').createSync(recursive: true);
  File('build/qa/developer-$name.png')
      .writeAsBytesSync(bytes!.buffer.asUint8List());
  image.dispose();
}
