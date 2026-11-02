// V39: 볼륨이 "지금 소리 나는 기기"(AudioManager1.ActiveOutputDevice)를 따라간다.
// 11-02 Pi 5: 시작 때 hdmi_speaker 에 묶인 채 스피커가 붙어 bt_speaker 로 바뀌어도 슬라이더가 hdmi 만 바꿨다.
import 'dart:async';

import 'package:deskmate_ui_demo/developer/pr74/music_playback.dart';
import 'package:flutter_test/flutter_test.dart';

const _hdmi = '/com/atlas/AudioManager1/Device/hdmi_speaker';
const _bt = '/com/atlas/AudioManager1/Device/bt_speaker';

class _FakeBus implements AudioDeviceBus {
  String active = _hdmi;
  final volumes = <String, int>{_hdmi: 20, _bt: 100};
  final sets = <(String, int)>[];
  final _signals = <String, StreamController<void>>{};
  bool closed = false;

  void signal(String device) => _signals[device]?.add(null);

  @override
  Future<void> neutralizeMediaStream() async {}
  @override
  Future<String> activeOutputDevice() async => active;
  @override
  Future<({int min, int max})> volumeRange(String device) async =>
      (min: 0, max: 100);
  @override
  Future<int> volume(String device) async => volumes[device]!;
  @override
  Future<void> setVolume(String device, int raw) async {
    sets.add((device, raw));
    volumes[device] = raw;
  }

  @override
  Stream<void> volumeChanges(String device) =>
      (_signals[device] ??= StreamController<void>.broadcast()).stream;
  @override
  Future<void> close() async => closed = true;
}

void main() {
  test('the slider follows the speaker once it becomes the active output',
      () async {
    final bus = _FakeBus();
    final volume =
        AtlasOutputDeviceVolume(bus: bus, pollEvery: const Duration(milliseconds: 10));
    addTearDown(volume.dispose);
    final seen = <double>[];
    volume.changes.listen(seen.add);
    expect(await volume.initialize(), .2);
    expect(volume.device, _hdmi);

    bus.active = _bt; // 스피커가 붙어 소리가 bt_speaker 로 나간다
    await Future<void>.delayed(const Duration(milliseconds: 40));
    expect(volume.device, _bt);
    expect(seen.last, 1.0); // 새 기기의 지금 볼륨을 보여 준다(덮어쓰지 않음)
    expect(bus.sets, isEmpty);

    await volume.setVolume(.3);
    expect(bus.sets, [(_bt, 30)]); // 소리 나는 기기를 줄인다
    expect(bus.volumes[_hdmi], 20); // hdmi 는 그대로
  });

  test('setting the volume right after a switch still hits the new device',
      () async {
    final bus = _FakeBus();
    // 확인 주기가 길어 아직 못 알아챈 상태
    final volume = AtlasOutputDeviceVolume(bus: bus, pollEvery: const Duration(hours: 1));
    addTearDown(volume.dispose);
    await volume.initialize();
    bus.active = _bt;
    await volume.setVolume(.5);
    expect(bus.sets, [(_bt, 50)]);
    expect(volume.device, _bt);
  });

  test('only the bound device signals are listened to', () async {
    final bus = _FakeBus();
    final volume = AtlasOutputDeviceVolume(bus: bus, pollEvery: const Duration(hours: 1));
    addTearDown(volume.dispose);
    final seen = <double>[];
    volume.changes.listen(seen.add);
    await volume.initialize();
    bus.volumes[_hdmi] = 40;
    bus.signal(_hdmi); // 묶인 기기 신호 → 다시 읽는다
    await Future<void>.delayed(Duration.zero);
    await Future<void>.delayed(Duration.zero);
    expect(seen.last, .4);
  });

  test('the same device stays bound and close releases the bus', () async {
    final bus = _FakeBus();
    final volume =
        AtlasOutputDeviceVolume(bus: bus, pollEvery: const Duration(milliseconds: 10));
    await volume.initialize();
    await Future<void>.delayed(const Duration(milliseconds: 30));
    expect(volume.device, _hdmi);
    await volume.setVolume(.25);
    expect(bus.sets, [(_hdmi, 25)]);
    await volume.dispose();
    expect(bus.closed, isTrue);
  });
}
