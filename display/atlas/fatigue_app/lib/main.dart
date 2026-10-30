/// DESKMATE 피로 기록 — 공용 피로 모델 학습 데이터를 모으는 Pi 5 앱.
///
/// 기본 DESKMATE 앱과 따로 둔다. 수집 중에는 이 앱을 띄워 두고, 15분마다 뜨는 KSS 질문에 답한다.
/// 기록은 MQTT `deskmate/fatigue/label` 로 나가고 PC 의 tools/fatigue_logger.py 가 hub 상태와 함께 저장한다.
/// 절차: docs/fatigue-dataset.md.
library;

import 'dart:async';

import 'package:camtest/hub_setup.dart' show showHubSetup;
import 'package:flutter/material.dart';

import 'atlas_home.dart';
import 'fatigue_log.dart';
import 'fatigue_page.dart';
import 'mqtt_sink.dart';

const _buildHost = String.fromEnvironment('DESKMATE_MQTT_HOST');
const _buildPort = int.fromEnvironment('DESKMATE_MQTT_PORT', defaultValue: 1883);

void main() => runApp(const FatigueApp());

class FatigueApp extends StatelessWidget {
  const FatigueApp({super.key, this.brokers, this.profiles});

  final BrokerStore? brokers;
  final FatigueProfileStore? profiles;

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'DESKMATE 피로 기록',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(colorSchemeSeed: const Color(0xFF3B6E8F), useMaterial3: true),
        home: FatigueHome(
            brokers: brokers ?? BrokerStore(),
            profiles: profiles ?? FatigueProfileStore()),
      );
}

class FatigueHome extends StatefulWidget {
  const FatigueHome({super.key, required this.brokers, required this.profiles});

  final BrokerStore brokers;
  final FatigueProfileStore profiles;

  @override
  State<FatigueHome> createState() => _FatigueHomeState();
}

class _FatigueHomeState extends State<FatigueHome> {
  final _session = FatigueSession();
  MqttFatigueSink? _sink;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    final saved = widget.brokers.load();
    final address = saved ??
        (_buildHost.isEmpty ? null : BrokerAddress(_buildHost, _buildPort));
    if (address != null) _useBroker(address);
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      _session.tick();
      if (mounted) setState(() {});
    });
  }

  void _useBroker(BrokerAddress address) {
    _sink?.close();
    final sink = MqttFatigueSink(address);
    _sink = sink;
    _session.sink = sink;
    sink.connect().catchError((_) {});
  }

  Future<void> _changeBroker() async {
    final ip = await showHubSetup(context,
        currentIp: _sink?.address.host,
        title: 'MQTT 브로커 주소',
        hint: 'Pi 4 의 IP · 포트는 ${_sink?.address.port ?? _buildPort}');
    if (ip == null || ip.isEmpty || !mounted) return;
    final address = BrokerAddress(ip, _sink?.address.port ?? _buildPort);
    final saved = widget.brokers.save(address);
    _useBroker(address);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(saved ? '$address 로 저장했습니다.' : '$address — 저장할 곳이 없어 이번 실행에만 씁니다.')));
  }

  Future<void> _exit() async {
    final ok = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('피로 기록 종료'),
            content: Text(_session.active
                ? '작업 중입니다. 종료하면 이 세션은 끝 응답 없이 끊깁니다. 종료할까요?'
                : '앱을 종료할까요?'),
            actions: [
              TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('취소')),
              FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('종료')),
            ],
          ),
        ) ??
        false;
    if (ok) await returnToAtlasHome();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _sink?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final sink = _sink;
    final pending = _session.outbox.length;
    final link = sink == null
        ? '브로커 주소 없음 — 눌러서 입력'
        : '${sink.connected ? '연결됨' : '연결 안 됨'} · ${sink.address}'
            '${pending > 0 ? ' · 미전송 $pending건' : ''}';
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(32, 16, 32, 16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Row(children: [
              const Text('DESKMATE 피로 기록', style: TextStyle(fontWeight: FontWeight.w700)),
              const Spacer(),
              TextButton.icon(
                  key: const ValueKey('broker'),
                  onPressed: _changeBroker,
                  icon: Icon(sink?.connected == true ? Icons.link : Icons.link_off),
                  label: Text(link)),
              IconButton(
                  tooltip: '앱 종료',
                  onPressed: _exit,
                  icon: const Icon(Icons.power_settings_new)),
            ]),
            const SizedBox(height: 12),
            Expanded(
              child: FatiguePage(
                session: _session,
                store: widget.profiles,
                canSend: sink != null,
                onChanged: () => setState(() {}),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}
