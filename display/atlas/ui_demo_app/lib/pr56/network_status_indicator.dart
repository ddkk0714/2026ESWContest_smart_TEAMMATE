import 'package:flutter/material.dart';

class NetworkStatusIndicator extends StatelessWidget {
  const NetworkStatusIndicator({super.key});
  @override
  Widget build(BuildContext context) => const Tooltip(
        message: '연결',
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(Icons.settings_ethernet_rounded, size: 18),
          SizedBox(width: 6),
          Text('연결', style: TextStyle(fontSize: 11)),
        ]),
      );
}
