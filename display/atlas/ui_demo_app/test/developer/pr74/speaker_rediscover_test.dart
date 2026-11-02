// V38: 저장된 스피커를 검색으로 다시 찾고, paired=false 면 Adapter.Pair 를 부른다(11-02 Pi 5: KLZS-L1 paired=false).
import 'package:dbus/dbus.dart';
import 'package:deskmate_ui_demo/developer/pr74/bluetooth_service.dart';
import 'package:flutter_test/flutter_test.dart';

class _Client extends Fake implements DBusClient {
  final calls = <String>[];
  bool pairFails = false;

  @override
  Future<DBusMethodSuccessResponse> callMethod(
      {String? destination,
      required DBusObjectPath path,
      String? interface,
      required String name,
      Iterable<DBusValue> values = const [],
      DBusSignature? replySignature,
      bool noReplyExpected = false,
      bool noAutoStart = false,
      bool allowInteractiveAuthorization = false}) async {
    calls.add('$name ${values.map((v) => v.asString()).join(' ')}');
    if (name == 'Pair' && pairFails) throw StateError('pair refused');
    return DBusMethodSuccessResponse(const []);
  }
}

class _Service extends AtlasBluetoothService {
  _Service(_Client client, this.devices) : super(client: client);
  final List<BluetoothDeviceInfo> devices;

  @override
  Future<String> adapterAddress() async => '88:A2:9E:3C:CC:B5';

  @override
  Stream<List<BluetoothDeviceInfo>> scanProgressive(
      {Duration duration = const Duration(seconds: 8),
      Duration interval = const Duration(seconds: 1)}) async* {
    yield devices;
  }
}

BluetoothDeviceInfo _speaker({required bool paired}) => BluetoothDeviceInfo(
    address: '00:18:D5:08:84:6C', name: 'KLZS-L1', paired: paired, connected: false);

void main() {
  test('an unpaired saved speaker is paired after it is found', () async {
    final client = _Client();
    final service = _Service(client, [_speaker(paired: false)]);
    expect(
        await service.rediscoverSpeaker('00:18:d5:08:84:6c', atLeast: Duration.zero),
        isTrue);
    expect(client.calls, ['Pair 88:A2:9E:3C:CC:B5 00:18:D5:08:84:6C']);
  });

  test('a paired speaker is not paired again', () async {
    final client = _Client();
    final service = _Service(client, [_speaker(paired: true)]);
    expect(await service.rediscoverSpeaker('00:18:D5:08:84:6C', atLeast: Duration.zero),
        isTrue);
    expect(client.calls, isEmpty);
  });

  test('a speaker no scan finds is reported and nothing is paired', () async {
    final client = _Client();
    final service = _Service(client, const []);
    expect(await service.rediscoverSpeaker('00:18:D5:08:84:6C', atLeast: Duration.zero),
        isFalse);
    expect(client.calls, isEmpty);
  });

  test('a refused pairing does not throw — the next connect decides', () async {
    final client = _Client()..pairFails = true;
    final service = _Service(client, [_speaker(paired: false)]);
    expect(await service.rediscoverSpeaker('00:18:D5:08:84:6C', atLeast: Duration.zero),
        isTrue);
    expect(client.calls.single, startsWith('Pair'));
  });

  test('connectWithRetry: 3 tries, rediscover only after the 2nd failure',
      () async {
    final log = <String>[];
    var n = 0;
    final ok = await connectWithRetry(() async {
      log.add('try ${++n}');
      return n == 3;
    },
        attempts: 3,
        retryDelay: Duration.zero,
        rediscover: () async => log.add('rediscover'));
    expect(ok, isTrue);
    expect(log, ['try 1', 'try 2', 'rediscover', 'try 3']);
    log.clear();
    n = 0;
    expect(
        await connectWithRetry(() async {
          log.add('try ${++n}');
          return false;
        },
            attempts: 3,
            retryDelay: Duration.zero,
            rediscover: () async => log.add('rediscover'),
            stop: () => n >= 1),
        isFalse);
    expect(log, ['try 1']); // 멈춤 조건이면 더 시도하지 않는다
  });
}
