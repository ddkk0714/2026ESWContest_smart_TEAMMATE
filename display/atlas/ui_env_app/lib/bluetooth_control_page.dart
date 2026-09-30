import 'dart:async';

import 'package:flutter/material.dart';

import 'bluetooth_link.dart';
import 'bluetooth_service.dart';
import 'feedback_policy.dart';
import 'link_status.dart';
import 'music_playback.dart';

class BluetoothControlPage extends StatefulWidget {
  const BluetoothControlPage({
    super.key,
    required this.bluetooth,
    required this.feedback,
    required this.music,
    required this.links,
  });
  final AtlasBluetoothService bluetooth;
  final FeedbackCoordinator feedback;
  final MusicPlayback music;

  /// 저장·자동 재연결을 맡는다. 연결은 전부 이것을 거쳐야 저장된다.
  final BluetoothLinkManager links;

  @override
  State<BluetoothControlPage> createState() => _BluetoothControlPageState();
}

class _BluetoothControlPageState extends State<BluetoothControlPage> {
  List<BluetoothDeviceInfo> _devices = const [];
  bool _scanning = false;
  StreamSubscription<List<BluetoothDeviceInfo>>? _scan;
  late double _volume;

  @override
  void initState() {
    super.initState();
    _volume = widget.music.volume;
  }

  @override
  void dispose() {
    unawaited(_scan?.cancel());
    super.dispose();
  }

  /// 결과가 나오는 대로 목록을 채운다. 8초를 다 기다린 뒤 한꺼번에 보여 주면
  /// 기기가 이미 보이는데도 사람이 기다려야 한다.
  void _startScan() {
    if (_scanning) return;
    setState(() {
      _scanning = true;
      _devices = const [];
    });
    _scan = widget.bluetooth.scanProgressive().listen(
      (devices) {
        if (mounted) setState(() => _devices = devices);
      },
      // cancelOnError 면 오류 뒤에 onDone 이 오지 않는다. 여기서 풀지 않으면
      // 권한 거부 같은 실패 뒤에 버튼이 '검색 중…' 에서 영영 멈춘다.
      onError: (Object error) {
        if (mounted) setState(() => _scanning = false);
        _notice('Bluetooth 검색 실패: $error');
      },
      onDone: () {
        if (mounted) setState(() => _scanning = false);
      },
      cancelOnError: true,
    );
  }

  Future<void> _connectSpeaker(BluetoothDeviceInfo device) async {
    try {
      await widget.links.connectSpeaker(device);
      _notice('${device.name}을(를) 오디오 출력으로 연결하고 저장했습니다.');
    } on Object catch (error) {
      _notice('스피커 연결 실패: $error');
    }
  }

  Future<void> _selectLamp(BluetoothDeviceInfo device) async {
    if (!device.isBleOnly) {
      // The speaker address is classic Bluetooth; GATT lamp control lives on
      // the separate BLE address (e.g. "KLZS-L1 app").
      _notice(
          '${device.name}은(는) 오디오 기기입니다. 램프는 BLE 항목(예: "KLZS-L1 app")을 선택하세요.');
      return;
    }
    final ok = await widget.links.selectLamp(device);
    _notice(ok
        ? '${device.name}을(를) 램프로 연결하고 저장했습니다.'
        : '${device.name}을(를) 램프로 저장했습니다. 연결은 다음 상태 전환 때 다시 시도합니다.');
  }

  Future<void> _connectPair(
      BluetoothDeviceInfo speaker, BluetoothDeviceInfo lamp) async {
    try {
      await widget.links.connectPair(speaker, lamp);
      _notice('${speaker.name} 스피커와 램프를 한 번에 연결했습니다.');
    } on Object catch (error) {
      _notice('일체형 연결 실패: $error');
    }
  }

  String _roleLabel(BluetoothDeviceInfo device) {
    if (device.isAudioSink) return '오디오 기기 · A2DP 스피커';
    if (device.isBleOnly) return 'BLE · 램프 제어(GATT)';
    return '기타';
  }

  void _notice(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.links,
        builder: (context, _) {
          final pair = findSpeakerLampPair(_devices);
          return ListView(
            children: [
              Text('Bluetooth 피드백',
                  style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 8),
              const Text(
                  '스피커는 Atlas A2DP 출력으로 연결됩니다. 램프와 음악의 자동 피드백은 아래 스위치를 켠 뒤 FSM phase가 바뀔 때만 실행됩니다. '
                  '연결한 기기는 저장되어 다음에 앱을 켜면 검색 없이 다시 연결됩니다.'),
              const SizedBox(height: 12),
              _SavedDevices(links: widget.links),
              const SizedBox(height: 12),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(children: [
                    SwitchListTile.adaptive(
                      key: const ValueKey('feedback-enabled'),
                      contentPadding: EdgeInsets.zero,
                      title: const Text('상태 기반 조명·음악 피드백'),
                      subtitle: const Text(
                          'fatigue에서 음악 재생, recovery/idle/end에서 일시정지'),
                      value: widget.feedback.enabled,
                      onChanged: (value) =>
                          setState(() => widget.feedback.enabled = value),
                    ),
                    Row(children: [
                      const Icon(Icons.volume_up_rounded),
                      const SizedBox(width: 10),
                      const Text('음악 볼륨'),
                      Expanded(
                        child: Slider(
                          value: _volume,
                          onChanged: (value) => setState(() => _volume = value),
                          onChangeEnd: (value) => widget.music.setVolume(value),
                        ),
                      ),
                      Text('${(_volume * 100).round()}%'),
                    ]),
                  ]),
                ),
              ),
              const SizedBox(height: 12),
              Row(children: [
                FilledButton.icon(
                  key: const ValueKey('bluetooth-scan'),
                  onPressed: _scanning ? null : _startScan,
                  icon: _scanning
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.bluetooth_searching),
                  label: Text(_scanning ? '검색 중…' : '기기 검색'),
                ),
                const SizedBox(width: 12),
                if (pair != null)
                  // KLZS-L1 같은 일체형은 오디오·BLE 두 항목을 따로 고르면 헷갈린다.
                  OutlinedButton.icon(
                    key: const ValueKey('bluetooth-connect-pair'),
                    onPressed: widget.links.speakerBusy
                        ? null
                        : () => _connectPair(pair.speaker, pair.lamp),
                    icon: const Icon(Icons.link_rounded),
                    label: Text('${pair.speaker.name} 한 번에 연결'),
                  ),
              ]),
              const SizedBox(height: 12),
              if (_devices.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 24),
                  child: Center(
                    child: Text(_scanning
                        ? '기기를 찾는 중입니다…'
                        : '검색을 눌러 Bluetooth 스피커 또는 iLink 램프를 찾으세요. '
                            '스피커가 목록에 없으면 페어링 모드로 두고 다시 검색하세요.'),
                  ),
                ),
              for (final device in _devices)
                Card(
                  child: ListTile(
                    leading: Icon(device.isAudioSink
                        ? Icons.speaker_rounded
                        : Icons.lightbulb_outline_rounded),
                    title: Text(device.name),
                    subtitle: Text('${device.address} · ${_roleLabel(device)}'),
                    trailing: Wrap(spacing: 6, children: [
                      OutlinedButton(
                        onPressed:
                            device.isBleOnly ? () => _selectLamp(device) : null,
                        child: const Text('램프 선택'),
                      ),
                      FilledButton(
                        onPressed: device.isAudioSink
                            ? () => _connectSpeaker(device)
                            : null,
                        child: Text(device.paired ? '스피커 연결' : '페어링·연결'),
                      ),
                    ]),
                  ),
                ),
            ],
          );
        },
      );
}

/// 저장된 스피커·램프와 지금 상태, 다시 연결·잊기.
class _SavedDevices extends StatelessWidget {
  const _SavedDevices({required this.links});

  final BluetoothLinkManager links;

  @override
  Widget build(BuildContext context) {
    final rows = [
      (
        id: LinkId.speaker,
        icon: Icons.speaker_rounded,
        saved: links.savedSpeaker,
        status: links.speakerStatus,
        busy: links.speakerBusy,
        reconnect: links.reconnectSpeaker,
      ),
      (
        id: LinkId.lamp,
        icon: Icons.lightbulb_outline_rounded,
        saved: links.savedLamp,
        status: links.lampStatus,
        busy: links.lampBusy,
        reconnect: links.reconnectLamp,
      ),
    ];
    return Card(
      key: const ValueKey('bluetooth-saved'),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        child: Column(children: [
          for (final row in rows)
            ListTile(
              key: ValueKey('bluetooth-saved-${row.id.name}'),
              contentPadding: EdgeInsets.zero,
              leading: Icon(row.icon),
              title: Text(row.saved == null
                  ? '${row.status.label} — 저장된 기기 없음'
                  : '${row.status.label} — ${row.saved!.name}'),
              subtitle: Text(row.saved == null
                  ? '아래에서 검색해 연결하면 저장됩니다.'
                  : '${row.saved!.address} · ${_statusText(row.status)}'),
              trailing: row.saved == null
                  ? null
                  : Wrap(spacing: 6, children: [
                      TextButton(
                        key: ValueKey('bluetooth-forget-${row.id.name}'),
                        onPressed: row.busy ? null : () => links.forget(row.id),
                        child: const Text('잊기'),
                      ),
                      FilledButton.tonal(
                        key: ValueKey('bluetooth-reconnect-${row.id.name}'),
                        onPressed: row.busy ? null : row.reconnect,
                        child: Text(row.busy ? '연결 중…' : '다시 연결'),
                      ),
                    ]),
            ),
        ]),
      ),
    );
  }

  static String _statusText(LinkStatus status) => switch (status.health) {
        LinkHealth.ok => '연결됨',
        LinkHealth.checking => '연결 확인 중',
        LinkHealth.down => '연결 끊김 — 전원과 거리를 확인하세요',
        LinkHealth.degraded => '불안정',
        LinkHealth.unconfigured => '미사용',
      };
}
