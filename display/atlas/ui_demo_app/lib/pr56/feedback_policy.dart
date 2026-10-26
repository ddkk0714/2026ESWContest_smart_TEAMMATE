// UI-only boundary for the imported PR #56 workspace; no Bluetooth or device IO.
class ControlRecord {
  ControlRecord(this.device, this.title, this.detail, this.succeeded)
      : time = DateTime.now();
  final String device;
  final String title;
  final String detail;
  final bool succeeded;
  final DateTime time;
}

List<int> ilinkFrame(List<int> body) => List.of(body);

class DemoBluetooth {
  Future<bool> isA2dpConnected(String address) async => false;
}

class DemoLamp {
  bool get connected => false;
  bool get configured => false;
  Future<bool> writeFrames(List<List<int>> frames) async => false;
}

class FeedbackCoordinator {
  final lamp = DemoLamp();
  final bluetooth = DemoBluetooth();
  String? get speakerAddress => null;
  Stream<ControlRecord> get records => const Stream.empty();
  bool enabled = false;
  void configureTargets({required bool lamp, required bool speaker}) {}
}
