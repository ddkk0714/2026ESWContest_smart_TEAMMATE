// 설정(톱니바퀴)의 '연결' 탭. 보드(MQTT 브로커) 주소와 포트만 다룬다.
library;

import 'package:flutter/material.dart';

import '../name_keyboard.dart';
import 'link_settings.dart';
import 'live_link.dart';

class ConnectionPanel extends StatefulWidget {
  const ConnectionPanel({super.key, required this.live});
  final LiveLink live;
  @override
  State<ConnectionPanel> createState() => _ConnectionPanelState();
}

class _ConnectionPanelState extends State<ConnectionPanel> {
  static const green = Color(0xFF356C59);
  static const muted = Color(0xFF7A8580);
  late String host = widget.live.host;
  late String port = '${widget.live.port}';

  /// 키보드가 고치고 있는 칸: host | port | null.
  String? editing;
  String? saveNote;

  LiveLink get live => widget.live;

  String _statusText() {
    if (!live.configured) return '연결하지 않았어요. 이 화면만으로 움직여요.';
    final from = switch (live.hostSource) {
      LinkSource.settings => ' (개발자 설정의 주소)',
      LinkSource.wiredDefault => ' (기본: Pi 4 유선 주소)',
      _ => '',
    };
    return _linkText() + from;
  }

  String _linkText() {
    if (live.live) {
      final seconds = live.stateAt == null
          ? 0
          : DateTime.now().difference(live.stateAt!).inSeconds;
      final hub = live.hubHealth == 'offline' ? ' · 보드 응답을 기다려요' : '';
      return '보드와 이어졌어요 · $seconds초 전 상태$hub';
    }
    if (live.status == LinkStatus.connected) return '이어졌어요. 책상 상태를 기다리는 중이에요.';
    return '보드를 찾는 중이에요. 그동안은 이 화면만으로 움직여요.';
  }

  Widget _field(String id, String label, String value, String hint) => InkWell(
      key: ValueKey('connection-$id'),
      borderRadius: BorderRadius.circular(16),
      onTap: () => setState(() => editing = id),
      child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          decoration: BoxDecoration(
              color: const Color(0xFFF4F2EB),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(
                  color: editing == id ? green : const Color(0xFFE1E5DC))),
          child: Row(children: [
            Text(label, style: const TextStyle(color: muted, fontSize: 13)),
            const SizedBox(width: 14),
            Expanded(
                child: Text(value.isEmpty ? hint : value,
                    style: TextStyle(
                        fontSize: 18, color: value.isEmpty ? muted : null))),
          ])));

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
      animation: live,
      builder: (context, _) => Container(
          width: double.infinity,
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
              color: const Color(0xFFFFFEF8),
              borderRadius: BorderRadius.circular(22)),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('책상과 이어지기', style: TextStyle(fontSize: 21)),
            const SizedBox(height: 5),
            const Text(
                '보드 주소를 적으면 책상의 지금 상태와 제안을 이 화면에 보여드려요.\n'
                '연결 정보만 이 기기에 저장하고, 프로필은 저장하지 않아요.',
                style: TextStyle(color: muted, fontSize: 13, height: 1.5)),
            const SizedBox(height: 14),
            _field('host', '주소', host, '보드의 IP 주소'),
            const SizedBox(height: 8),
            _field('port', '포트', port, '${LinkSettings.defaultPort}'),
            if (editing != null) ...[
              const SizedBox(height: 8),
              HostKeyboard(
                  key: ValueKey('host-keyboard-$editing'),
                  digitsOnly: editing == 'port',
                  onKey: (key) => setState(() {
                        if (editing == 'port') {
                          port = editHostText(port, key, maxLength: 5);
                        } else {
                          host = editHostText(host, key);
                        }
                      }),
                  onDone: () => setState(() => editing = null)),
            ],
            const SizedBox(height: 12),
            Row(children: [
              Expanded(
                  child: Text(saveNote ?? _statusText(),
                      key: const ValueKey('connection-status'),
                      style: const TextStyle(color: green, fontSize: 13))),
              if (live.configured)
                TextButton(
                    key: const ValueKey('connection-clear'),
                    onPressed: () => setState(() {
                          host = '';
                          editing = null;
                          saveNote =
                              live.configure('', LinkSettings.defaultPort)
                                  ? null
                                  : '저장하지 못해 이번 실행에만 적용돼요.';
                        }),
                    child: const Text('연결 끊기')),
              const SizedBox(width: 6),
              FilledButton(
                  key: const ValueKey('connection-apply'),
                  onPressed: host.isEmpty
                      ? null
                      : () => setState(() {
                            editing = null;
                            saveNote = live.configure(
                                    host,
                                    int.tryParse(port) ??
                                        LinkSettings.defaultPort)
                                ? null
                                : '저장하지 못해 이번 실행에만 적용돼요.';
                          }),
                  child: const Text('연결하기')),
            ]),
            if (live.weather?.text case final weather?) ...[
              const SizedBox(height: 6),
              Text('바깥 $weather',
                  style: const TextStyle(color: muted, fontSize: 12)),
            ],
          ])));
}
