import 'package:flutter/material.dart';
import 'workspace_store.dart';

class HomeWidgetPicker extends StatefulWidget {
  const HomeWidgetPicker(
      {super.key, required this.selected, required this.onToggle});
  final List<String> selected;
  final bool Function(String id) onToggle;
  @override
  State<HomeWidgetPicker> createState() => _HomeWidgetPickerState();
}

class _HomeWidgetPickerState extends State<HomeWidgetPicker> {
  late final List<String> _selected = [...widget.selected];
  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final available =
        widgetNames.keys.where((id) => !_selected.contains(id)).toList();
    const rowHeight = 48.0;
    const duration = Duration(milliseconds: 360);
    final dividerY = 26 + available.length * rowHeight;
    return SizedBox(
      width: 360,
      height: widgetNames.length * rowHeight + 68,
      child: Stack(children: [
        Text('추가 가능한 위젯',
            style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant)),
        AnimatedPositioned(
            duration: duration,
            curve: Curves.easeInOutCubic,
            left: 0,
            right: 0,
            top: dividerY,
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Divider(height: 15, color: scheme.primary.withValues(alpha: .12)),
              Text('사용 중인 위젯',
                  style:
                      TextStyle(fontSize: 12, color: scheme.onSurfaceVariant)),
            ])),
        for (final id in widgetNames.keys)
          AnimatedPositioned(
            key: ValueKey('picker-row-$id'),
            duration: duration,
            curve: Curves.easeInOutCubic,
            left: 0,
            right: 0,
            top: _selected.contains(id)
                ? dividerY + 38 + _selected.indexOf(id) * rowHeight
                : 22 + available.indexOf(id) * rowHeight,
            height: rowHeight,
            child: Material(
                color: Colors.transparent,
                child: InkWell(
                  key: ValueKey('picker-toggle-$id'),
                  borderRadius: BorderRadius.circular(12),
                  onTap: () {
                    if (!widget.onToggle(id)) return;
                    setState(() {
                      if (!_selected.remove(id)) _selected.add(id);
                    });
                  },
                  child: Row(children: [
                    const SizedBox(width: 5),
                    Semantics(
                        button: true,
                        label:
                            '${widgetNames[id]} ${_selected.contains(id) ? '빼기' : '추가'}',
                        child: AnimatedSwitcher(
                            duration: const Duration(milliseconds: 200),
                            transitionBuilder: (child, animation) =>
                                ScaleTransition(scale: animation, child: child),
                            child: Icon(
                                _selected.contains(id)
                                    ? Icons.remove_circle
                                    : Icons.add_circle,
                                key: ValueKey(_selected.contains(id)),
                                size: 25,
                                color: _selected.contains(id)
                                    ? const Color(0xffcf5858)
                                    : const Color(0xff279660)))),
                    const SizedBox(width: 14),
                    Expanded(
                        child: Text(widgetNames[id]!,
                            style: const TextStyle(fontSize: 15))),
                    Icon(_icons[id], size: 20, color: scheme.onSurfaceVariant),
                    const SizedBox(width: 8),
                  ]),
                )),
          ),
      ]),
    );
  }

  static const _icons = {
    'clock': Icons.schedule_rounded,
    'calendar': Icons.calendar_month_rounded,
    'environment': Icons.thermostat_rounded,
    'focus': Icons.local_fire_department_rounded,
    'music': Icons.music_note_rounded,
    'weather': Icons.wb_cloudy_rounded,
  };
}
