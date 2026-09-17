import 'package:flutter/material.dart';

import 'bluetooth_service.dart';
import 'feedback_policy.dart';
import 'music_playback.dart';

class BluetoothControlPage extends StatefulWidget {
  const BluetoothControlPage({
    super.key,
    required this.bluetooth,
    required this.feedback,
    required this.music,
  });
  final AtlasBluetoothService bluetooth;
  final FeedbackCoordinator feedback;
  final MusicPlayback music;

  @override
  State<BluetoothControlPage> createState() => _BluetoothControlPageState();
}

class _BluetoothControlPageState extends State<BluetoothControlPage> {
  List<BluetoothDeviceInfo> _devices = const [];
  bool _scanning = false;
  String? _lampAddress;
  late double _volume;

  @override
  void initState() {
    super.initState();
    _volume = widget.music.volume;
  }

  Future<void> _scan() async {
    if (_scanning) return;
    setState(() => _scanning = true);
    try {
      final devices = await widget.bluetooth.scan();
      if (mounted) setState(() => _devices = devices);
    } catch (error) {
      _notice('Bluetooth 검색 실패: $error');
    } finally {
      if (mounted) setState(() => _scanning = false);
    }
  }

  Future<void> _connectSpeaker(BluetoothDeviceInfo device) async {
    try {
      await widget.bluetooth.connectSpeaker(device);
      widget.feedback.selectSpeaker(device.address);
      _notice('${device.name}을(를) 오디오 출력으로 연결했습니다.');
    } catch (error) {
      _notice('스피커 연결 실패: $error');
    }
  }

  void _selectLamp(BluetoothDeviceInfo device) {
    if (!device.isBleOnly) {
      // The speaker address is classic Bluetooth; GATT lamp control lives on
      // the separate BLE address (e.g. "KLZS-L1 app").
      _notice('${device.name}은(는) 오디오 기기입니다. 램프는 BLE 항목(예: "KLZS-L1 app")을 선택하세요.');
      return;
    }
    widget.feedback.lamp.select(device.address);
    setState(() => _lampAddress = device.address);
    _notice('${device.name}을(를) iLink 램프로 선택했습니다.');
  }

  String _roleLabel(BluetoothDeviceInfo device) {
    if (device.isAudioSink) return '오디오 기기 · A2DP 스피커';
    if (device.isBleOnly) return 'BLE · 램프 제어(GATT)';
    return '기타';
  }

  void _notice(String text) => ScaffoldMessenger.of(context)
      .showSnackBar(SnackBar(content: Text(text)));

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('Bluetooth 피드백', style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          const Text('스피커는 Atlas A2DP 출력으로 연결됩니다. 램프와 음악의 자동 피드백은 아래 스위치를 켠 뒤 FSM phase가 바뀔 때만 실행됩니다.'),
          const SizedBox(height: 4),
          const Text('KLZS-L1 처럼 스피커+램프 일체형은 주소가 두 개입니다: 오디오 기기(스피커)와 BLE(램프). '
              '스피커가 목록에 없으면 스피커를 페어링 모드로 두고 다시 검색하세요.'),
          const SizedBox(height: 18),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(children: [
                SwitchListTile.adaptive(
                  key: const ValueKey('feedback-enabled'),
                  contentPadding: EdgeInsets.zero,
                  title: const Text('상태 기반 조명·음악 피드백'),
                  subtitle: const Text('fatigue에서 음악 재생, recovery/idle/end에서 일시정지'),
                  value: widget.feedback.enabled,
                  onChanged: (value) => setState(() => widget.feedback.enabled = value),
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
              onPressed: _scanning ? null : _scan,
              icon: _scanning
                  ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.bluetooth_searching),
              label: Text(_scanning ? '검색 중…' : '기기 검색'),
            ),
            const SizedBox(width: 12),
            Text(_lampAddress == null ? '램프 미선택' : '램프 선택됨: $_lampAddress'),
          ]),
          const SizedBox(height: 12),
          Expanded(
            child: _devices.isEmpty
                ? const Center(child: Text('검색을 눌러 Bluetooth 스피커 또는 iLink 램프를 찾으세요.'))
                : ListView.separated(
                    itemCount: _devices.length,
                    separatorBuilder: (_, __) => const SizedBox(height: 8),
                    itemBuilder: (context, index) {
                      final device = _devices[index];
                      return Card(
                        child: ListTile(
                          leading: Icon(device.isAudioSink
                              ? Icons.speaker_rounded
                              : Icons.lightbulb_outline_rounded),
                          title: Text(device.name),
                          subtitle: Text('${device.address} · ${_roleLabel(device)}'),
                          trailing: Wrap(spacing: 6, children: [
                            OutlinedButton(
                              onPressed: device.isBleOnly ? () => _selectLamp(device) : null,
                              child: const Text('램프 선택'),
                            ),
                            FilledButton(
                              onPressed: device.isAudioSink ? () => _connectSpeaker(device) : null,
                              child: Text(device.paired ? '스피커 연결' : '페어링·연결'),
                            ),
                          ]),
                        ),
                      );
                    },
                  ),
          ),
        ],
      );
}