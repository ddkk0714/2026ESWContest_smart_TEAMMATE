import 'dart:io';

import 'package:deskmate_display/placement_client.dart';
import 'package:deskmate_display/placement_controller.dart';
import 'package:deskmate_display/settings_store.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeClient implements PlacementClient {
  _FakeClient(this.host);

  @override
  final String host;
  String remote = 'center';
  PlacementException? fetchError;
  PlacementException? applyError;
  final applied = <String>[];
  int fetches = 0;
  bool closed = false;

  PlacementInfo _info() =>
      PlacementInfo(placement: remote, model: 'opt_l8_c256_$remote.json');

  @override
  Future<PlacementInfo> fetch() async {
    fetches++;
    if (fetchError != null) throw fetchError!;
    return _info();
  }

  @override
  Future<PlacementInfo> apply(String placement) async {
    applied.add(placement);
    if (applyError != null) throw applyError!;
    remote = placement;
    return _info();
  }

  @override
  void close() => closed = true;
}

void main() {
  late Directory temp;
  late SettingsStore store;
  late _FakeClient? client;
  late PlacementController controller;
  final changes = <DeskmateSettings>[];

  setUp(() {
    temp = Directory.systemTemp.createTempSync('placement-ctl-');
    store = SettingsStore(
        candidates: ['${temp.path}/settings.json'],
        legacyPostureCandidates: const []);
    client = null;
    changes.clear();
    controller = PlacementController(
      store: store,
      clientFactory: (host) => client = _FakeClient(host),
      onSettingsChanged: changes.add,
    );
  });

  tearDown(() {
    controller.dispose();
    temp.deleteSync(recursive: true);
  });

  test('connect reads the remote value; nothing saved means no POST and ask on start',
      () async {
    await controller.connect('172.20.10.3');
    expect(client!.fetches, 1);
    expect(client!.applied, isEmpty);
    expect(controller.supported, isTrue);
    expect(controller.remote?.placement, 'center');
    expect(controller.appliedLabel, '가운데 · opt_l8_c256_center.json');
    expect(controller.shouldAskOnStart, isTrue);
  });

  test('saved value differs from remote → one POST with the saved value', () async {
    store.save(const DeskmateSettings(placement: 'left'));
    await controller.connect('172.20.10.3');
    expect(client!.applied, ['left']);
    expect(controller.remote?.placement, 'left');
    expect(controller.shouldAskOnStart, isFalse);
  });

  test('saved value equal to remote → no POST', () async {
    store.save(const DeskmateSettings(placement: 'center'));
    await controller.connect('172.20.10.3');
    expect(client!.applied, isEmpty);
  });

  test('404 marks the service unsupported and never asks', () async {
    client = null;
    controller = PlacementController(
      store: store,
      clientFactory: (host) => client = _FakeClient(host)
        ..fetchError =
            const PlacementException(PlacementFailure.unsupported, ''),
    );
    await controller.connect('172.20.10.3');
    expect(controller.supported, isFalse);
    expect(controller.shouldAskOnStart, isFalse);
    expect(controller.appliedLabel, '옛 서비스 (위치 선택 없음)');
  });

  test('unreachable keeps supported unknown and does not ask', () async {
    controller = PlacementController(
      store: store,
      clientFactory: (host) => client = _FakeClient(host)
        ..fetchError =
            const PlacementException(PlacementFailure.unreachable, 'refused'),
    );
    await controller.connect('172.20.10.3');
    expect(controller.supported, isNull);
    expect(controller.shouldAskOnStart, isFalse);
    expect(controller.appliedLabel, contains('연결되지 않아요'));
  });

  test('apply saves locally first, then POSTs; success text names the model',
      () async {
    await controller.connect('172.20.10.3');
    final result = await controller.apply('right');
    expect(result.ok, isTrue);
    expect(result.text, '적용됨: opt_l8_c256_right.json');
    expect(store.load().placement, 'right');
    expect(store.load().placementAsked, isTrue);
    expect(changes.last.placement, 'right');
    expect(client!.applied, ['right']);
    expect(controller.shouldAskOnStart, isFalse);
  });

  test('apply failure keeps the local value and reports the reason', () async {
    await controller.connect('172.20.10.3');
    client!.applyError =
        const PlacementException(PlacementFailure.serverError, 'bad model');
    final result = await controller.apply('left');
    expect(result.ok, isFalse);
    expect(result.text, startsWith('저장했어요.'));
    expect(result.text, contains('500'));
    expect(store.load().placement, 'left');
    expect(controller.lastError?.failure, PlacementFailure.serverError);
  });

  test('apply without a host saves only', () async {
    final result = await controller.apply('center');
    expect(result.ok, isFalse);
    expect(result.failure?.failure, PlacementFailure.unreachable);
    expect(store.load().placement, 'center');
  });

  test('apply rejects an unknown key without saving', () async {
    final result = await controller.apply('top');
    expect(result.ok, isFalse);
    expect(store.load().placement, isNull);
  });

  test('skip records placement_asked so the question is not repeated', () async {
    await controller.connect('172.20.10.3');
    expect(controller.shouldAskOnStart, isTrue);
    controller.skip();
    expect(store.load().placementAsked, isTrue);
    expect(store.load().placement, isNull);
    expect(controller.shouldAskOnStart, isFalse);
    // 다음 실행(새 컨트롤러)에서도 묻지 않는다.
    final again = PlacementController(
        store: store, clientFactory: (host) => _FakeClient(host));
    await again.connect('172.20.10.3');
    expect(again.shouldAskOnStart, isFalse);
    again.dispose();
  });

  test('connect with a new host closes the old client', () async {
    await controller.connect('172.20.10.3');
    final first = client!;
    await controller.connect('172.20.10.9');
    expect(first.closed, isTrue);
    expect(client!.host, '172.20.10.9');
    await controller.connect(null);
    expect(controller.remote, isNull);
    expect(controller.host, isNull);
  });
}
