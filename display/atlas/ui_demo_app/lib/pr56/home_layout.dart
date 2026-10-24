/// Default footprints; resizable cards snap to one or two cells.
const homeWidgetSpans = {
  'clock': 2,
  'calendar': 2,
  'environment': 1,
  'focus': 1,
  'music': 2,
  'weather': 1,
};

const resizableHomeWidgets = {'clock', 'music', 'weather'};

bool homeWidgetsFit(List<String> order, {Map<String, int> sizes = const {}}) =>
    [2, 4].every((columns) => packHomeWidgets(order, columns, sizes: sizes)
        .every((slot) => slot.row < (columns == 4 ? 2 : 4)));

class HomeSlot {
  const HomeSlot(this.id, this.row, this.column, this.span);
  final String id;
  final int row;
  final int column;
  final int span;
}

/// Fill the earliest available slot, letting small cards fill wide-card gaps.
List<HomeSlot> packHomeWidgets(List<String> order, int columns,
    {Map<String, int> sizes = const {}}) {
  final occupied = <List<bool>>[];
  final slots = <HomeSlot>[];
  for (final id in order.toSet()) {
    final custom = sizes[id];
    final preferred =
        resizableHomeWidgets.contains(id) && (custom == 1 || custom == 2)
            ? custom!
            : homeWidgetSpans[id] ?? 1;
    final span = preferred.clamp(1, columns);
    var placed = false;
    for (var row = 0; !placed; row++) {
      if (occupied.length <= row) occupied.add(List.filled(columns, false));
      for (var col = 0; col <= columns - span; col++) {
        if (occupied[row].skip(col).take(span).any((v) => v)) continue;
        for (var c = col; c < col + span; c++) {
          occupied[row][c] = true;
        }
        slots.add(HomeSlot(id, row, col, span));
        placed = true;
        break;
      }
    }
  }
  return slots;
}

List<String> moveHomeWidget(List<String> order, String from, String to) {
  if (from == to || !order.contains(from) || !order.contains(to)) {
    return [...order];
  }
  final result = [...order];
  final target = result.indexOf(to);
  result.remove(from);
  result.insert(target, from);
  return result;
}
