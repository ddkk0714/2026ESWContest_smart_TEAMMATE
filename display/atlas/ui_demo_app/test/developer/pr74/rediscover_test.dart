// V35: 다시 찾기는 주소가 첫 결과(Atlas 가 예전에 알던 목록)에 보여도 최소 시간만큼 검색을 이어 간다.
import 'package:deskmate_ui_demo/developer/pr74/bluetooth_service.dart';
import 'package:flutter_test/flutter_test.dart';

const _lamp = BluetoothDeviceInfo(
    address: 'C0:18:D5:08:E4:6C',
    name: 'KLZS-L1 app',
    paired: false,
    connected: false);

/// [scanProgressive] 가 [step] 마다 결과를 내고 [duration] 이 지나면 끝난다. [appearAfter] 번째 묶음부터 램프가 보인다.
class _ScanningBluetooth implements AtlasBluetoothService {
  _ScanningBluetooth({this.appearAfter = 0, this.visible = true});
  final int appearAfter;
  final bool visible;
  final Duration step = const Duration(milliseconds: 20);
  int batches = 0;

  @override
  Stream<List<BluetoothDeviceInfo>> scanProgressive(
      {Duration duration = const Duration(seconds: 8),
      Duration interval = const Duration(seconds: 1)}) async* {
    final clock = Stopwatch()..start();
    var i = 0;
    while (true) {
      batches++;
      yield [if (visible && i >= appearAfter) _lamp];
      i++;
      if (clock.elapsed >= duration) return;
      await Future<void>.delayed(step);
    }
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} was not expected');
}

void main() {
  test('a device in the very first (cached) batch is not taken at once',
      () async {
    final bt = _ScanningBluetooth();
    final clock = Stopwatch()..start();
    final found = await rediscoverDevice(bt, 'c0:18:d5:08:e4:6c',
        limit: const Duration(seconds: 2),
        atLeast: const Duration(milliseconds: 100));
    expect(found, isTrue);
    expect(clock.elapsed, greaterThanOrEqualTo(const Duration(milliseconds: 100)));
    expect(bt.batches, greaterThan(1)); // 예전엔 첫 묶음에서 바로 끝났다
    expect(clock.elapsed, lessThan(const Duration(seconds: 2))); // 최소 시간이 지나면 끝까지 기다리지 않는다
  });

  test('a device that appears later is found after the minimum scan', () async {
    final bt = _ScanningBluetooth(appearAfter: 3);
    expect(
        await rediscoverDevice(bt, _lamp.address,
            limit: const Duration(seconds: 2),
            atLeast: const Duration(milliseconds: 50)),
        isTrue);
  });

  test('a device never seen is not found within the limit', () async {
    final bt = _ScanningBluetooth(visible: false);
    expect(
        await rediscoverDevice(bt, _lamp.address,
            limit: const Duration(milliseconds: 120),
            atLeast: const Duration(milliseconds: 50)),
        isFalse);
  });

  test('near expiry (limit shorter than the minimum) the seen result counts',
      () async {
    final bt = _ScanningBluetooth();
    expect(
        await rediscoverDevice(bt, _lamp.address,
            limit: const Duration(milliseconds: 60),
            atLeast: const Duration(seconds: 3)),
        isTrue);
  });
}
