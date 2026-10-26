import 'dart:async';

import 'package:flutter/material.dart';

import 'bluetooth_link.dart';
import 'bluetooth_service.dart';
import 'fan_control.dart';
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
    this.onManualAdjust,
  });
  final AtlasBluetoothService bluetooth;
  final FeedbackCoordinator feedback;
  final MusicPlayback music;

  /// 저장·자동 재연결을 맡는다. 연결은 전부 이것을 거쳐야 저장된다.
  final BluetoothLinkManager links;

  /// 사람이 직접 램프 밝기·선풍기 단계를 바꿨을 때(target_id, 이전 값(모르면 null), 새 값). hub 에 manual_adjust 로 간다.
  final void Function(String targetId, int? from, int to)? onManualAdjust;

  @override
  State<BluetoothControlPage> createState() => _BluetoothControlPageState();
}

class _BluetoothControlPageState extends State<BluetoothControlPage> {
  List<BluetoothDeviceInfo> _devices = const [];
  bool _scanning = false;
  StreamSubscription<List<BluetoothDeviceInfo>>? _scan;
  late double _volume;
  bool _testing = false;

  Future<void> _testBrightness(int value) async {
    setState(() => _testing = true);
    try {
      // 사람이 직접 바꾼 값만 hub 에 manual_adjust 로 알린다(hub 명령·국면 조명은 제외). from 은 실제 이전 상태.
      final from = widget.feedback.lamp.previousBrightness;
      final ok = await widget.feedback.lamp.setBrightness(value);
      if (ok) widget.onManualAdjust?.call('desk_lamp', from, value);
      _notice(ok
          ? '조명 밝기를 요청했어요.'
          : '조명 쓰기 실패: ${widget.feedback.lamp.lastError ?? '램프를 먼저 연결하세요'}');
    } catch (error) {
      _notice('조명 조절 실패: $error');
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  /// 허브 명령과 같은 쓰기 경로(BleFanControl.send)로 보낸다. 자동 피드백 스위치와 무관하다.
  Future<void> _testFan(List<int> frame, String label) async {
    final fan = widget.feedback.fan;
    if (fan == null) return;
    setState(() => _testing = true);
    try {
      final before = fan.lastState;
      final from = before == null ? null : (before.power ? before.level : 0);
      final sent = await fan.send(frame);
      final state = sent.state;
      if (sent.written) {
        // [0x03, n] = 단계 n · [0x01, 0] = 끔(단계 0). 읽은 상태가 있으면 그 단계가 더 정확하다.
        final to = state == null
            ? (frame.first == 0x03 ? frame[1] : 0)
            : (state.power ? state.level : 0);
        widget.onManualAdjust?.call('vent_fan', from, to);
      }
      _notice(!sent.written
          ? '선풍기 쓰기 실패: ${fan.lastError ?? '선풍기를 먼저 연결하세요'}'
          : state == null
              ? '선풍기 $label 명령을 보냈습니다. 실제 바람을 확인하세요.'
              : '선풍기 $label — 지금 ${state.summary}');
    } catch (error) {
      _notice('선풍기 조절 실패: $error');
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  Future<void> _testMusic() async {
    try {
      final playing = await widget.music.toggle();
      _notice(playing ? '음악 재생을 요청했어요.' : '음악을 일시정지했습니다.');
    } catch (error) {
      _notice('음악 재생 실패: $error');
    }
  }

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
      _notice('${device.name} 연결됨');
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
    _notice(
        ok ? '${device.name} 조명 연결됨' : '${device.name} 조명 연결 실패. 다시 연결해 주세요.');
  }

  Future<void> _selectFan(BluetoothDeviceInfo device) async {
    final ok = await widget.links.selectFan(device);
    _notice(ok
        ? '${device.name} 선풍기 연결됨'
        : '${device.name} 선풍기 연결 실패. 다시 연결해 주세요.');
  }

  Future<void> _connectPair(
      BluetoothDeviceInfo speaker, BluetoothDeviceInfo lamp) async {
    try {
      final lampOk = await widget.links.connectPair(speaker, lamp);
      _notice(lampOk
          ? '${speaker.name} 스피커와 램프를 한 번에 연결했습니다.'
          : '스피커 연결됨 · 조명은 다시 연결해 주세요.');
    } on Object catch (error) {
      _notice('일체형 연결 실패: $error');
    }
  }

  String _roleLabel(BluetoothDeviceInfo device) {
    if (device.isAudioSink) return '스피커';
    if (isFanDevice(device)) return '선풍기';
    if (device.isBleOnly) return '조명';
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
              _SavedDevices(links: widget.links),
              const SizedBox(height: 12),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(children: [
                    SwitchListTile.adaptive(
                      key: const ValueKey('feedback-enabled'),
                      contentPadding: EdgeInsets.zero,
                      title: const Text('나에게 맞춰 자동 조절'),
                      value: widget.feedback.enabled,
                      onChanged: (value) => setState(
                          () => widget.links.setFeedbackEnabled(value)),
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
                    Wrap(spacing: 8, runSpacing: 8, children: [
                      OutlinedButton(
                        key: const ValueKey('lamp-test-dim'),
                        onPressed: _testing || !widget.feedback.lamp.configured
                            ? null
                            : () => _testBrightness(64),
                        child: const Text('조명 25%'),
                      ),
                      OutlinedButton(
                        key: const ValueKey('lamp-test-bright'),
                        onPressed: _testing || !widget.feedback.lamp.configured
                            ? null
                            : () => _testBrightness(255),
                        child: const Text('조명 100%'),
                      ),
                      if (widget.feedback.fan case final fan?)
                        for (final (key, label, frame) in [
                          ('fan-test-1', '1단', [0x03, 1]),
                          ('fan-test-3', '3단', [0x03, 3]),
                          ('fan-test-off', '끄기', [0x01, 0]),
                        ])
                          OutlinedButton(
                            key: ValueKey(key),
                            onPressed: _testing || !fan.configured
                                ? null
                                : () => _testFan(frame, label),
                            child: Text('선풍기 $label'),
                          ),
                      OutlinedButton(
                        key: const ValueKey('music-test'),
                        onPressed: _testMusic,
                        child: const Text('음악 재생·일시정지'),
                      ),
                    ]),
                    if (widget.feedback.lastControlError != null)
                      const Text('조명을 조절하지 못했어요. 연결을 확인해 주세요.'),
                    if (widget.feedback.lastAudioError != null)
                      const Text('음악을 재생하지 못했어요. 스피커 연결을 확인해 주세요.'),
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
                    child: Text(
                        _scanning ? '기기를 찾는 중입니다…' : '기기를 연결 모드로 두고 검색해 주세요.'),
                  ),
                ),
              for (final device in _devices)
                Card(
                  child: ListTile(
                    leading: Icon(device.isAudioSink
                        ? Icons.speaker_rounded
                        : isFanDevice(device)
                            ? Icons.air_rounded
                            : Icons.lightbulb_outline_rounded),
                    title: Text(device.name),
                    subtitle: Text(_roleLabel(device)),
                    trailing: Wrap(spacing: 6, children: [
                      if (isFanDevice(device))
                        OutlinedButton(
                          onPressed: widget.feedback.fan == null
                              ? null
                              : () => _selectFan(device),
                          child: const Text('선풍기 선택'),
                        )
                      else if (device.isBleOnly)
                        OutlinedButton(
                          onPressed: device.isBleOnly
                              ? () => _selectLamp(device)
                              : null,
                          child: const Text('램프 선택'),
                        ),
                      if (device.isAudioSink)
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

/// 저장된 스피커·램프·선풍기와 지금 상태, 다시 연결·잊기.
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
      if (links.feedback.fan != null)
        (
          id: LinkId.fan,
          icon: Icons.air_rounded,
          saved: links.savedFan,
          status: links.fanStatus,
          busy: links.fanBusy,
          reconnect: links.reconnectFan,
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
              title:
                  Text(row.saved == null ? row.status.label : row.saved!.name),
              subtitle: Text(
                  row.saved == null ? '연결된 기기 없음' : _statusText(row.status)),
              trailing: row.saved == null
                  ? null
                  : Wrap(spacing: 6, children: [
                      TextButton(
                        key: ValueKey('bluetooth-forget-${row.id.name}'),
                        onPressed: row.busy ? null : () => links.forget(row.id),
                        child: const Text('기기 삭제'),
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
        LinkHealth.down => '연결 끊김',
        LinkHealth.degraded => '불안정',
        LinkHealth.unconfigured => '미사용',
      };
}
