import 'dart:io';

import 'package:camtest/posture_source.dart' as camera;
import 'package:camtest/posture_state.dart';
import 'package:deskmate_display/link_status.dart';
import 'package:deskmate_display/posture_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('retries source discovery every 30 seconds while showing demo',
      (tester) async {
    var discoveries = 0;
    final source = camera.DemoPostureSource();
    await tester.pumpWidget(MaterialApp(
      home: PostureScreen(
        source: source,
        sourceDiscovery: () async => discoveries++,
      ),
    ));
    await tester.pump();
    expect(discoveries, 1); // initial local discovery

    await tester.pump(const Duration(seconds: 29));
    expect(discoveries, 1);
    await tester.pump(const Duration(seconds: 1));
    await tester.pump();
    expect(discoveries, 2);
  });

  testWidgets('reconnect button starts source discovery', (tester) async {
    var discoveries = 0;
    await tester.pumpWidget(MaterialApp(
      home: PostureScreen(
        source: camera.DemoPostureSource(),
        sourceDiscovery: () async => discoveries++,
      ),
    ));
    await tester.pump();
    expect(find.byKey(const ValueKey('posture-reconnect')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('posture-reconnect')));
    await tester.pump();
    expect(discoveries, 2);
  });

  testWidgets('a changed reconnect signal starts discovery', (tester) async {
    var discoveries = 0;
    final source = camera.DemoPostureSource();
    Widget screen(int signal) => MaterialApp(
          home: PostureScreen(
            source: source,
            sourceDiscovery: () async => discoveries++,
            reconnectSignal: signal,
          ),
        );

    await tester.pumpWidget(screen(0));
    await tester.pump();
    expect(discoveries, 1);

    await tester.pumpWidget(screen(1));
    await tester.pump();
    expect(discoveries, 2);
  });

  testWidgets('demo reports deviceNotFound once and does not repeat',
      (tester) async {
    final reports = <LinkStatus>[];
    await tester.pumpWidget(MaterialApp(
      home: PostureScreen(
        source: camera.DemoPostureSource(),
        sourceDiscovery: () async {},
        onLinkStatus: reports.add,
      ),
    ));
    await tester.pump();
    await tester.pump(const Duration(seconds: 35)); // 재탐색 한 번 포함
    await tester.pump();

    expect(reports, hasLength(1));
    expect(reports.single.id, LinkId.posture);
    expect(reports.single.cause, LinkCause.deviceNotFound);
  });

  testWidgets('a live source reports ok, then down after 5 s of failures',
      (tester) async {
    final reports = <LinkStatus>[];
    final source = _FlakySource();
    await tester.pumpWidget(MaterialApp(
      home: PostureScreen(
        source: source,
        sourceDiscovery: () async {},
        onLinkStatus: reports.add,
      ),
    ));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    expect(reports.last.health, LinkHealth.ok);

    source.fail = true;
    for (var i = 0; i < 7; i++) {
      await tester.pump(const Duration(seconds: 1));
    }
    expect(reports.last.health, LinkHealth.down);
    expect(reports.last.cause, LinkCause.disconnected);
    expect(reports.where((r) => r.health == LinkHealth.ok), hasLength(1));
  });
}

/// 데모가 아닌 소스처럼 보이는 가짜. fail 이면 연결 실패를 던진다.
class _FlakySource implements camera.PostureSource {
  final _demo = camera.DemoPostureSource();
  bool fail = false;

  @override
  String get label => 'fake camsvc';

  @override
  bool get canCalibrate => false;

  @override
  Future<PostureState> fetch() {
    if (fail) throw const SocketException('camsvc down');
    return _demo.fetch();
  }

  @override
  Future<camera.LinkHealth?> health() async => null;

  @override
  Future<void> calibrate() async {}

  @override
  void close() {}
}
