import 'package:deskmate_display/bluetooth_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  // Records captured from Atlas Bluetooth1.GetDiscoverableDevices on the Pi 5
  // (2026-09-18) for the KLZS-L1 speaker/lamp, which advertises two addresses.
  const speaker = BluetoothDeviceInfo(
    address: '00:18:d5:08:84:6c',
    name: 'KLZS-L1',
    paired: false,
    connected: false,
    deviceClass: 0x760408, // major class 0x04 Audio/Video, minor loudspeaker
  );
  const lampBle = BluetoothDeviceInfo(
    address: 'c0:18:d5:08:e4:6c',
    name: 'KLZS-L1 app',
    paired: false,
    connected: false,
    deviceClass: 0,
  );

  test('classic speaker record is the only A2DP candidate', () {
    expect(speaker.majorDeviceClass, 0x04);
    expect(speaker.isAudioSink, isTrue);
    expect(speaker.isBleOnly, isFalse);
  });

  test('BLE-only record is a lamp candidate, never a speaker', () {
    expect(lampBle.isAudioSink, isFalse);
    expect(lampBle.isBleOnly, isTrue);
  });

  test('class-0 BLE record remains a lamp candidate with advertised services', () {
    const lampWithGatt = BluetoothDeviceInfo(
      address: 'c0:18:d5:08:e4:6c',
      name: 'KLZS-L1 app',
      paired: false,
      connected: false,
      profiles: ['0000a032-0000-1000-8000-00805f9b34fb'],
    );
    expect(lampWithGatt.isBleOnly, isTrue);
    expect(lampWithGatt.isAudioSink, isFalse);
  });
  test('A2DP sink UUID also marks a device as audio sink', () {
    const byUuid = BluetoothDeviceInfo(
      address: '11:22:33:44:55:66',
      name: 'x',
      paired: false,
      connected: false,
      profiles: ['0000110B-0000-1000-8000-00805F9B34FB'],
    );
    expect(byUuid.isAudioSink, isTrue);
  });
}
