import 'dart:convert';

import 'package:flutter/material.dart';

import '../page_transition.dart';
import '../live/connection_panel.dart';
import 'developer_controller.dart';
import 'pr74/bluetooth_control_page.dart';
import 'pr74/connection_page.dart';
import 'pr74/developer_page.dart';
import 'pr74/dev_mode_page.dart';
import 'pr74/keystroke_page.dart';
import 'pr74/link_status.dart';
import 'pr74/preview_page.dart';
import 'pr74/sensor_overview_page.dart';

enum DeveloperTab { sensors, keystroke, developer, preview, mode, connection }

class DeveloperScreen extends StatefulWidget {
  const DeveloperScreen(
      {super.key,
      required this.controller,
      required this.preview,
      required this.onClose,
      required this.onBluetooth,
      required this.onPlacement});
  final DeveloperController controller;
  final Widget Function() preview;
  final VoidCallback onClose, onBluetooth, onPlacement;
  @override
  State<DeveloperScreen> createState() => _DeveloperScreenState();
}

class _DeveloperScreenState extends State<DeveloperScreen> {
  late DeveloperTab tab = widget.controller.defaultOnStart
      ? DeveloperTab.mode
      : DeveloperTab.sensors;
  static const labels = ['센서 전체', '키스트로크', '개발자', '프리뷰', '개발자 모드', '연결 상태'];

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        return ColoredBox(
            color: const Color(0xFFF4F2EB),
            child: Padding(
                padding: const EdgeInsets.fromLTRB(24, 14, 24, 16),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Row(children: [
                        const Text('개발자 화면', style: TextStyle(fontSize: 22)),
                        const Spacer(),
                        Text(
                            c.live.configured
                                ? '${c.live.host}:${c.live.port} · ${c.freshState ? '수신 중' : c.stateAt == null ? '수신 대기' : '수신 끊김'}'
                                : '책상 주소 미설정',
                            style: const TextStyle(fontSize: 12)),
                        const SizedBox(width: 12),
                        TextButton.icon(
                            key: const ValueKey('developer-close'),
                            onPressed: widget.onClose,
                            icon: const Icon(Icons.arrow_back),
                            label: const Text('시계로')),
                      ]),
                      const SizedBox(height: 4),
                      Row(children: [
                        for (final value in DeveloperTab.values)
                          Padding(
                              padding: const EdgeInsets.only(right: 6),
                              child: ChoiceChip(
                                  key: ValueKey('developer-tab-${value.name}'),
                                  label: Text(labels[value.index]),
                                  selected: tab == value,
                                  onSelected: (_) =>
                                      setState(() => tab = value))),
                        const Spacer(),
                        if (tab == DeveloperTab.sensors ||
                            tab == DeveloperTab.keystroke)
                          TextButton(
                              onPressed: () => _raw(context),
                              child: const Text('Raw 값')),
                      ]),
                      const SizedBox(height: 10),
                      Expanded(
                          child: PageTransitionView(
                              pageKey: tab,
                              child: switch (tab) {
                                DeveloperTab.sensors => SensorOverviewPage(
                                    state: c.state ??
                                        DeveloperController.emptyState()),
                                DeveloperTab.keystroke => KeystrokePage(
                                    metrics: c.keystroke ?? c.state?.keystroke,
                                    now: DateTime.now(),
                                    demo: false,
                                    sourceLabel: c.live.configured
                                        ? 'PC 수집기 · MQTT'
                                        : '수신 대기',
                                    onConnection: () => setState(
                                        () => tab = DeveloperTab.connection)),
                                DeveloperTab.developer => DeveloperPage(
                                    state: c.previewState,
                                    overridden: c.overrideState != null,
                                    activePreset: c.preset?.key,
                                    envFlags: c.envFlags,
                                    onPreset: c.applyPreset,
                                    onEnvFlags: c.setFlags,
                                    onClear: c.clearOverride,
                                    preview: widget.preview()),
                                DeveloperTab.preview => PreviewPage(
                                    state: c.state,
                                    host: c.host,
                                    onHostChanged: c.setHost,
                                    onFeedback: c.sendFeedback,
                                    feedbackEnabled: c.overrideState == null &&
                                        c.freshState),
                                DeveloperTab.mode => DevModePage(
                                    state: c.state,
                                    host: c.host,
                                    onHostChanged: c.setHost,
                                    log: c.log,
                                    board: c.board,
                                    deviceAvailable: {
                                      'desk_lamp': c.feedback.lamp.configured,
                                      'vent_fan':
                                          c.feedback.fan?.configured ?? false
                                    },
                                    bluetoothLine:
                                        '램프 ${linkHealthLabel(c.btLinks.lampStatus)} · '
                                        '선풍기 ${linkHealthLabel(c.btLinks.fanStatus)} · '
                                        '스피커 ${linkHealthLabel(c.btLinks.speakerStatus)} · '
                                        '자동피드백 ${c.feedback.enabled ? 'ON' : 'OFF'}',
                                    placement: c.settings.load().placement,
                                    defaultOnStart: c.defaultOnStart,
                                    onDefaultOnStartChanged: c.setDefault,
                                    connectionLabel: c.live.status.name),
                                DeveloperTab.connection => ConnectionPage(
                                    snapshot: c.snapshot,
                                    now: DateTime.now(),
                                    sourceLabel: c.live.configured
                                        ? 'MQTT · ${c.live.host}:${c.live.port}'
                                        : '책상 주소 미설정',
                                    onAction: (id) {
                                      if ([
                                        LinkId.speaker,
                                        LinkId.lamp,
                                        LinkId.fan
                                      ].contains(id)) {
                                        widget.onBluetooth();
                                      } else {
                                        c.reconnect();
                                      }
                                    },
                                    onReloadAll: c.reloadAll,
                                    onChangeAddress: () => _address(context),
                                    placementLabel: c.settings.load().placement,
                                    onChangePlacement: widget.onPlacement),
                              })),
                    ])));
      });

  Future<void> _address(BuildContext context) => showDialog<void>(
      context: context,
      builder: (_) => Dialog(
          child: SizedBox(
              width: 650,
              child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Row(children: [
                      const Text('책상 연결', style: TextStyle(fontSize: 24)),
                      const Spacer(),
                      IconButton(
                          onPressed: () => Navigator.pop(context),
                          icon: const Icon(Icons.close))
                    ]),
                    ConnectionPanel(live: widget.controller.live),
                  ])))));

  Future<void> _raw(BuildContext context) => showDialog<void>(
      context: context,
      builder: (context) => Dialog(
          child: SizedBox(
              width: 900,
              height: 490,
              child: Padding(
                  padding: const EdgeInsets.all(20),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Row(children: [
                          const Text('수신 Raw 값',
                              style: TextStyle(fontSize: 22)),
                          const Spacer(),
                          IconButton(
                              onPressed: () => Navigator.pop(context),
                              icon: const Icon(Icons.close))
                        ]),
                        const Text(
                            '센서 측정값과 키 입력의 집계 특징만 표시해요. 입력한 글자는 수집하지 않아요.'),
                        const SizedBox(height: 12),
                        Expanded(
                            child: AnimatedBuilder(
                                animation: widget.controller,
                                builder: (_, __) {
                                  final c = widget.controller;
                                  final data = tab == DeveloperTab.sensors
                                      ? c.stateEnvelope
                                      : c.keystrokeEnvelope;
                                  return SingleChildScrollView(
                                      child: SelectableText(
                                          data == null
                                              ? '아직 받은 값이 없어요.'
                                              : const JsonEncoder.withIndent(
                                                      '  ')
                                                  .convert(data),
                                          style: const TextStyle(
                                              fontFamily: 'LGText',
                                              fontSize: 13)));
                                })),
                      ])))));
}

Future<void> showBluetoothPopup(BuildContext context, DeveloperController c) =>
    showDialog<void>(
        context: context,
        builder: (context) => Theme(
            data: Theme.of(context).copyWith(
              colorScheme: ColorScheme.fromSeed(
                  seedColor: const Color(0xFF356C59),
                  surface: const Color(0xFFFFFEF8)),
              cardTheme: CardThemeData(
                  color: const Color(0xFFFFFEF8),
                  elevation: 0,
                  margin: EdgeInsets.zero,
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(22),
                      side: const BorderSide(color: Color(0xFFE1E5DC)))),
              dividerColor: const Color(0xFFE1E5DC),
            ),
            child: Dialog(
                backgroundColor: const Color(0xFFF4F2EB),
                insetPadding:
                    const EdgeInsets.symmetric(horizontal: 40, vertical: 24),
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(28)),
                child: SizedBox(
                    width: 944,
                    height: 540,
                    child: Padding(
                        padding: const EdgeInsets.fromLTRB(24, 12, 24, 16),
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              Row(children: [
                                const Icon(Icons.bluetooth_rounded),
                                const SizedBox(width: 10),
                                const Text('책상 기기 연결',
                                    style: TextStyle(fontSize: 24)),
                                const Spacer(),
                                IconButton(
                                    key:
                                        const ValueKey('bluetooth-popup-close'),
                                    tooltip: '닫기',
                                    onPressed: () => Navigator.pop(context),
                                    icon: const Icon(Icons.close_rounded))
                              ]),
                              Expanded(
                                  child: BluetoothControlPage(
                                      bluetooth: c.bluetooth,
                                      feedback: c.feedback,
                                      music: c.music,
                                      links: c.btLinks,
                                      onManualAdjust: c.manualAdjust)),
                            ]))))));
