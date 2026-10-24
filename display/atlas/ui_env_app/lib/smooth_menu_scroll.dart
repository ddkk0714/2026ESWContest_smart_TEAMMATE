import 'dart:async';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

/// Continuous dragging and eased, accumulated wheel motion, without item snaps.
class SmoothMenuScroll extends StatefulWidget {
  const SmoothMenuScroll(
      {super.key, required this.child, this.axis = Axis.vertical});
  final Widget child;
  final Axis axis;
  @override
  State<SmoothMenuScroll> createState() => _SmoothMenuScrollState();
}

class _SmoothMenuScrollState extends State<SmoothMenuScroll> {
  final _controller = ScrollController();
  double? _wheelTarget;
  double _lastDelta = 0;
  int _wheelSerial = 0;

  void _signal(PointerSignalEvent event) {
    if (event is! PointerScrollEvent || !_controller.hasClients) return;
    final delta = widget.axis == Axis.vertical
        ? event.scrollDelta.dy
        : event.scrollDelta.dx != 0
            ? event.scrollDelta.dx
            : event.scrollDelta.dy;
    if (delta == 0) return;
    GestureBinding.instance.pointerSignalResolver.register(event, (_) {
      if (!mounted || !_controller.hasClients) return;
      final position = _controller.position;
      final reverse = delta.sign != _lastDelta.sign;
      final base = reverse ? position.pixels : _wheelTarget ?? position.pixels;
      final target = (base + delta)
          .clamp(position.minScrollExtent, position.maxScrollExtent)
          .toDouble();
      _wheelTarget = target;
      _lastDelta = delta;
      final serial = ++_wheelSerial;
      unawaited(_controller
          .animateTo(target,
              duration: const Duration(milliseconds: 230),
              curve: Curves.easeOutCubic)
          .then((_) {
        if (mounted && serial == _wheelSerial) _wheelTarget = null;
      }));
    });
  }

  void _touch(PointerDownEvent _) {
    _wheelTarget = null;
    _lastDelta = 0;
    _wheelSerial++;
    // A direct drag takes over immediately from a wheel animation.
    if (_controller.hasClients &&
        _controller.position.isScrollingNotifier.value) {
      _controller.jumpTo(_controller.offset);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ScrollConfiguration(
      behavior: ScrollConfiguration.of(context).copyWith(
          scrollbars: false,
          overscroll: false,
          dragDevices: {
            ...ScrollConfiguration.of(context).dragDevices,
            PointerDeviceKind.mouse
          }),
      child: Stack(fit: StackFit.expand, children: [
        SingleChildScrollView(
            controller: _controller,
            scrollDirection: widget.axis,
            physics: const BouncingScrollPhysics(
                parent: AlwaysScrollableScrollPhysics()),
            clipBehavior: Clip.hardEdge,
            child: widget.child),
        // Stay ahead of Scrollable's wheel handler even while its animated
        // viewport ignores content taps. Translucency preserves item gestures.
        Positioned.fill(
            child: Listener(
                onPointerSignal: _signal,
                onPointerDown: _touch,
                behavior: HitTestBehavior.translucent)),
      ]));
}
