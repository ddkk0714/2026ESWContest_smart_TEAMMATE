import 'package:flutter/material.dart';

import 'workspace_store.dart';
import 'popup_tap_region.dart';

/// A month and its selected day's agenda share one bounded, responsive panel.
class DeskCalendarPanel extends StatelessWidget {
  const DeskCalendarPanel(
      {super.key,
      required this.month,
      required this.selectedDay,
      required this.today,
      required this.events,
      required this.onMonth,
      required this.onDay,
      required this.onAdd,
      required this.onEdit,
      required this.onDelete,
      this.onClose});

  final DateTime month;
  final DateTime selectedDay;
  final DateTime today;
  final List<DeskEvent> events;
  final ValueChanged<DateTime> onMonth;
  final ValueChanged<DateTime> onDay;
  final VoidCallback onAdd;
  final ValueChanged<DeskEvent> onEdit;
  final ValueChanged<DeskEvent> onDelete;
  final VoidCallback? onClose;

  static String _time(int hour, int minute) =>
      '${hour.toString().padLeft(2, '0')}:${minute.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, box) {
        final narrow = box.maxWidth < 620 || box.maxHeight < 380;
        final add = FilledButton.icon(
            key: const ValueKey('workspace-add-event'),
            onPressed: onAdd,
            style: FilledButton.styleFrom(
                minimumSize: const Size(128, 44),
                padding:
                    const EdgeInsets.symmetric(horizontal: 15, vertical: 9),
                textStyle: const TextStyle(
                    fontFamily: 'High1',
                    fontSize: 18,
                    fontWeight: FontWeight.w600),
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(14))),
            icon: const Icon(Icons.add_rounded, size: 23),
            label: const Text('일정 추가'));
        final dayEvents = events
            .where((e) => DateUtils.isSameDay(e.date, selectedDay))
            .toList()
          ..sort((a, b) {
            final start = a.startMinutes.compareTo(b.startMinutes);
            return start != 0 ? start : a.title.compareTo(b.title);
          });
        final calendar = _monthCard(context);
        return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(children: [
                const Expanded(
                    child: Text('캘린더',
                        style: TextStyle(
                            fontSize: 21, fontWeight: FontWeight.w600))),
                if (box.maxWidth >= 380) add,
                if (onClose != null) ...[
                  const SizedBox(width: 8),
                  IconButton(
                      tooltip: '팝업 닫기',
                      onPressed: onClose,
                      icon: const Icon(Icons.close_rounded)),
                ],
              ]),
              if (box.maxWidth < 380)
                Align(alignment: Alignment.centerRight, child: add),
              const SizedBox(height: 12),
              Expanded(
                  child: narrow
                      ? ListView(children: [
                          SizedBox(height: 350, child: calendar),
                          const SizedBox(height: 14),
                          _agenda(context, dayEvents, scroll: false),
                        ])
                      : Row(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                              Expanded(flex: 3, child: calendar),
                              const SizedBox(width: 16),
                              Expanded(
                                  flex: 2,
                                  child: _agenda(context, dayEvents,
                                      scroll: true)),
                            ])),
            ]);
      });

  Widget _surface(BuildContext context, Widget child, {EdgeInsets? padding}) {
    final scheme = Theme.of(context).colorScheme;
    return PopupTapRegion(
        child: Container(
            padding: padding ?? const EdgeInsets.all(12),
            decoration: BoxDecoration(
                color: scheme.surface,
                borderRadius: BorderRadius.circular(22),
                border:
                    Border.all(color: scheme.primary.withValues(alpha: .14))),
            child: child));
  }

  Widget _monthCard(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final offset = DateTime(month.year, month.month).weekday % 7;
    final count = DateTime(month.year, month.month + 1, 0).day;
    final weeks = ((offset + count) / 7).ceil();
    return _surface(
        context,
        Column(children: [
          Row(children: [
            IconButton(
                tooltip: '이전 달',
                onPressed: () => onMonth(DateTime(month.year, month.month - 1)),
                icon: const Icon(Icons.chevron_left_rounded)),
            Expanded(
                child: Center(
                    child: Text('${month.year}년 ${month.month}월',
                        key: const ValueKey('calendar-month-title'),
                        style: const TextStyle(
                            fontSize: 21, fontWeight: FontWeight.w600)))),
            IconButton(
                tooltip: '다음 달',
                onPressed: () => onMonth(DateTime(month.year, month.month + 1)),
                icon: const Icon(Icons.chevron_right_rounded)),
          ]),
          const SizedBox(height: 3),
          Row(children: [
            for (var d = 0; d < 7; d++)
              Expanded(
                  child: Center(
                      child: Text(['일', '월', '화', '수', '목', '금', '토'][d],
                          style: TextStyle(
                              fontSize: 11,
                              color: d == 0
                                  ? const Color(0xffcb715f)
                                  : scheme.onSurfaceVariant))))
          ]),
          const SizedBox(height: 7),
          Expanded(
              child: Column(children: [
            for (var week = 0; week < weeks; week++)
              Expanded(
                  child: Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                    for (var weekday = 0; weekday < 7; weekday++)
                      Expanded(child: Builder(builder: (context) {
                        final day = week * 7 + weekday - offset + 1;
                        if (day < 1 || day > count) {
                          return const SizedBox.shrink();
                        }
                        final date = DateTime(month.year, month.month, day);
                        final selected = DateUtils.isSameDay(date, selectedDay);
                        final isToday = DateUtils.isSameDay(date, today);
                        final scheduled = events
                            .where((e) => DateUtils.isSameDay(e.date, date))
                            .toList()
                          ..sort((a, b) =>
                              a.startMinutes.compareTo(b.startMinutes));
                        return Padding(
                            padding: const EdgeInsets.all(2),
                            child: Tooltip(
                              message: scheduled.map((e) => e.title).join('\n'),
                              child: Material(
                                color: selected
                                    ? scheme.primary.withValues(alpha: .12)
                                    : Colors.transparent,
                                borderRadius: BorderRadius.circular(10),
                                child: InkWell(
                                  key: ValueKey(
                                      'calendar-day-${date.year}-${date.month}-${date.day}'),
                                  borderRadius: BorderRadius.circular(10),
                                  onTap: () => onDay(date),
                                  child:
                                      LayoutBuilder(builder: (context, cell) {
                                    final compact = cell.maxHeight < 38;
                                    final daySize = compact ? 18.0 : 22.0;
                                    final eventHeight = compact ? 12.0 : 14.0;
                                    final visible =
                                        ((cell.maxHeight - daySize) /
                                                (eventHeight + 1))
                                            .floor()
                                            .clamp(1, 2);
                                    return Padding(
                                        padding: const EdgeInsets.symmetric(
                                            horizontal: 2),
                                        child: Column(
                                            crossAxisAlignment:
                                                CrossAxisAlignment.stretch,
                                            children: [
                                              Row(
                                                  mainAxisAlignment:
                                                      MainAxisAlignment.center,
                                                  children: [
                                                    Container(
                                                        width: daySize,
                                                        height: daySize,
                                                        alignment:
                                                            Alignment.center,
                                                        decoration: BoxDecoration(
                                                            color: isToday
                                                                ? scheme.primary
                                                                : Colors
                                                                    .transparent,
                                                            shape: BoxShape
                                                                .circle),
                                                        child: Text('$day',
                                                            style: TextStyle(
                                                                fontSize:
                                                                    compact
                                                                        ? 12
                                                                        : 13,
                                                                height: 1,
                                                                color: isToday
                                                                    ? scheme
                                                                        .onPrimary
                                                                    : weekday ==
                                                                            0
                                                                        ? const Color(
                                                                            0xffcb715f)
                                                                        : scheme
                                                                            .onSurface))),
                                                    if (scheduled.length >
                                                        visible)
                                                      Flexible(
                                                          child: Text(
                                                              '+${scheduled.length - visible}',
                                                              maxLines: 1,
                                                              style: TextStyle(
                                                                  fontSize: 8,
                                                                  color: scheme
                                                                      .primary))),
                                                  ]),
                                              for (final event
                                                  in scheduled.take(visible))
                                                Container(
                                                    margin:
                                                        const EdgeInsets.only(
                                                            top: 1),
                                                    padding:
                                                        const EdgeInsets.symmetric(
                                                            horizontal: 3),
                                                    height: eventHeight,
                                                    decoration: BoxDecoration(
                                                        color: scheme.primary
                                                            .withValues(
                                                                alpha: .09),
                                                        borderRadius:
                                                            BorderRadius.circular(
                                                                4)),
                                                    child: Text(event.title,
                                                        maxLines: 1,
                                                        overflow: TextOverflow
                                                            .ellipsis,
                                                        style: TextStyle(
                                                            fontSize: compact ? 9 : 10,
                                                            height: 1.2,
                                                            color: scheme.primary))),
                                            ]));
                                  }),
                                ),
                              ),
                            ));
                      }))
                  ]))
          ])),
        ]));
  }

  Widget _agenda(BuildContext context, List<DeskEvent> dayEvents,
      {required bool scroll}) {
    final scheme = Theme.of(context).colorScheme;
    final title = Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: Row(children: [
          Expanded(
              child: Text(
                  '${selectedDay.month}월 ${selectedDay.day}일 ${[
                    '월',
                    '화',
                    '수',
                    '목',
                    '금',
                    '토',
                    '일'
                  ][selectedDay.weekday - 1]}요일',
                  style: const TextStyle(
                      fontSize: 17, fontWeight: FontWeight.w600))),
          Text('${dayEvents.length}개',
              style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 12)),
        ]));
    final items = [
      for (var i = 0; i < dayEvents.length; i++)
        _timelineItem(context, dayEvents[i],
            first: i == 0, last: i == dayEvents.length - 1)
    ];
    final empty = Padding(
        padding: const EdgeInsets.symmetric(vertical: 28),
        child: Text('등록된 일정이 없어요.',
            style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 13)));
    return _surface(
        context,
        Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: scroll ? MainAxisSize.max : MainAxisSize.min,
            children: [
              title,
              if (scroll)
                Expanded(
                    child: dayEvents.isEmpty
                        ? empty
                        : ListView(
                            key: const ValueKey('calendar-timeline'),
                            children: items))
              else if (dayEvents.isEmpty)
                empty
              else
                ...items,
            ]));
  }

  Widget _timelineItem(BuildContext context, DeskEvent event,
      {required bool first, required bool last}) {
    final scheme = Theme.of(context).colorScheme;
    return IntrinsicHeight(
        child: Row(
            key: ValueKey('calendar-timeline-${event.id}'),
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
          SizedBox(
              width: 45,
              child: Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(_time(event.hour, event.minute),
                            style:
                                TextStyle(fontSize: 12, color: scheme.primary)),
                        if (event.endMinutes != null) ...[
                          const SizedBox(height: 4),
                          Text(_time(event.endHour!, event.endMinute!),
                              style: TextStyle(
                                  fontSize: 10,
                                  color: scheme.onSurfaceVariant)),
                        ],
                      ]))),
          SizedBox(
              width: 18,
              child: Stack(alignment: Alignment.topCenter, children: [
                Positioned(
                    top: first ? 13 : 0,
                    bottom: last ? null : 0,
                    width: 1.5,
                    height: last ? (first ? 0 : 13) : null,
                    child: ColoredBox(
                        color: scheme.primary.withValues(alpha: .22))),
                Positioned(
                    top: 10,
                    child: Container(
                        width: 9,
                        height: 9,
                        decoration: BoxDecoration(
                            color: scheme.primary, shape: BoxShape.circle))),
              ])),
          Expanded(
              child: Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Container(
                    padding: const EdgeInsets.fromLTRB(10, 9, 7, 5),
                    decoration: BoxDecoration(
                        color: scheme.primary.withValues(alpha: .06),
                        borderRadius: BorderRadius.circular(14)),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(event.title,
                              style: const TextStyle(
                                  fontSize: 15, fontWeight: FontWeight.w600)),
                          if (event.location.isNotEmpty)
                            Padding(
                                padding: const EdgeInsets.only(top: 5),
                                child: Row(children: [
                                  Icon(Icons.location_on_outlined,
                                      size: 13, color: scheme.onSurfaceVariant),
                                  const SizedBox(width: 3),
                                  Expanded(
                                      child: Text(event.location,
                                          style: TextStyle(
                                              fontSize: 11,
                                              color: scheme.onSurfaceVariant))),
                                ])),
                          Row(
                              mainAxisAlignment: MainAxisAlignment.end,
                              children: [
                                IconButton(
                                    tooltip: '일정 수정',
                                    onPressed: () => onEdit(event),
                                    icon: const Icon(Icons.edit_outlined,
                                        size: 17),
                                    visualDensity: VisualDensity.compact,
                                    constraints: const BoxConstraints.tightFor(
                                        width: 32, height: 30)),
                                IconButton(
                                    tooltip: '일정 삭제',
                                    onPressed: () => onDelete(event),
                                    icon: const Icon(Icons.delete_outline,
                                        size: 17),
                                    visualDensity: VisualDensity.compact,
                                    constraints: const BoxConstraints.tightFor(
                                        width: 32, height: 30)),
                              ]),
                        ]),
                  ))),
        ]));
  }
}
