/// Pi 4 주소를 보드에서 직접 찍는 화면.
///
/// **키보드를 쓰지 않는다.** 보드에 USB 키보드가 꽂혀 있으리라 가정할 수 없고,
/// Flutter eLinux 에는 화면 키보드가 없다. 그래서 `TextField` 대신 숫자 키패드를
/// 직접 그린다 — 터치만으로 끝난다.
library;

import 'package:flutter/material.dart';

import 'hub_config.dart';
import 'palette.dart';

/// 취소하면 null, `데모로` 를 누르면 빈 문자열, 저장하면 정규화된 IP.
Future<String?> showHubSetup(BuildContext context, {String? currentIp}) {
  return showDialog<String>(
    context: context,
    builder: (context) => _HubSetupDialog(initial: currentIp ?? ''),
  );
}

class _HubSetupDialog extends StatefulWidget {
  const _HubSetupDialog({required this.initial});
  final String initial;

  @override
  State<_HubSetupDialog> createState() => _HubSetupDialogState();
}

class _HubSetupDialogState extends State<_HubSetupDialog> {
  late String _text = widget.initial;

  String? get _valid => normalizeIp(_text);

  void _tap(String key) {
    setState(() {
      if (key == '<') {
        if (_text.isNotEmpty) _text = _text.substring(0, _text.length - 1);
        return;
      }
      // 점 세 개를 넘기거나 점을 연달아 찍는 건 주소가 될 수 없다.
      if (key == '.') {
        if (_text.isEmpty || _text.endsWith('.') || '.'.allMatches(_text).length >= 3) {
          return;
        }
        _text += key;
        return;
      }
      // 주소가 될 수 없는 숫자는 아예 안 들어가게 막는다. 다 찍고 나서
      // "아직 주소가 아닙니다" 를 보는 것보다 눌러도 안 들어가는 편이 빠르다.
      final octet = _text.split('.').last;
      if (octet.length >= 3) return;
      final next = int.tryParse('$octet$key');
      if (next != null && next > 255) return;
      _text += key;
    });
  }

  @override
  Widget build(BuildContext context) {
    final valid = _valid;
    return AlertDialog(
      backgroundColor: kSurface,
      // 기본 여백으로는 1024x600 보드에서 content 높이가 384 로 잘려 키패드가
      // 넘친다. 여백을 줄여 자리를 벌고, 그래도 모자라면 스크롤되게 둔다.
      insetPadding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
      contentPadding: const EdgeInsets.fromLTRB(20, 16, 20, 8),
      title: const Text('자세 노드 주소'),
      content: SizedBox(
        width: 330,
        child: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            decoration: BoxDecoration(
              color: kBg,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: valid == null ? kLine : kGreen),
            ),
            child: Text(
              _text.isEmpty ? '예: 172.16.34.198' : _text,
              key: const ValueKey('hub-input'),
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 23,
                fontWeight: FontWeight.w700,
                letterSpacing: 1.5,
                color: _text.isEmpty ? kDim : kInk,
              ),
            ),
          ),
          const SizedBox(height: 6),
          Text(
            valid == null && _text.isNotEmpty
                ? '아직 주소가 아닙니다'
                : 'Pi 4 자세 노드의 IP · 포트는 $kHubPort 고정',
            style: TextStyle(
                fontSize: 12, color: valid == null && _text.isNotEmpty ? kAmber : kDim),
          ),
          const SizedBox(height: 10),
          for (final row in const [
            ['1', '2', '3'],
            ['4', '5', '6'],
            ['7', '8', '9'],
            ['.', '0', '<'],
          ])
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [for (final key in row) _key(key)],
            ),
        ])),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('취소'),
        ),
        TextButton(
          key: const ValueKey('hub-demo'),
          onPressed: () => Navigator.pop(context, ''),
          style: TextButton.styleFrom(foregroundColor: kMuted),
          child: const Text('데모로'),
        ),
        FilledButton(
          key: const ValueKey('hub-save'),
          onPressed: valid == null ? null : () => Navigator.pop(context, valid),
          child: const Text('저장'),
        ),
      ],
    );
  }

  Widget _key(String label) => Padding(
        padding: const EdgeInsets.all(3),
        child: SizedBox(
          width: 92,
          height: 46,
          child: OutlinedButton(
            key: ValueKey('hub-key-$label'),
            onPressed: () => _tap(label),
            style: OutlinedButton.styleFrom(
              foregroundColor: kInk,
              side: const BorderSide(color: kLine),
              padding: EdgeInsets.zero,
            ),
            child: label == '<'
                ? const Icon(Icons.backspace_outlined, size: 20)
                : Text(label,
                    style: const TextStyle(
                        fontSize: 20, fontWeight: FontWeight.w700)),
          ),
        ),
      );
}
