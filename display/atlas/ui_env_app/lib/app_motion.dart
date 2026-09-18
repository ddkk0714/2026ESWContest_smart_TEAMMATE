import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

abstract final class AppMotion {
  static const fast = Duration(milliseconds: 180);
  static const content = Duration(milliseconds: 220);
  static const indicator = Duration(milliseconds: 300);
  static const normal = Duration(milliseconds: 300);
  static const page = Duration(milliseconds: 360);
  static const slow = Duration(milliseconds: 360);

  static const standardCurve = Curves.easeOutCubic;
  static const reverseCurve = Curves.easeInCubic;

  static const pageEnterOffset = .22;
  static const pageExitOffset = .10;
  static const pageEnterScale = .965;
  static const pageExitOpacity = .18;

  static Widget fadeTransition(
    Widget child,
    Animation<double> animation,
  ) {
    final curved = CurvedAnimation(
      parent: animation,
      curve: standardCurve,
      reverseCurve: reverseCurve,
    );
    return FadeTransition(opacity: curved, child: child);
  }

  static Widget fadeScaleTransition(
    Widget child,
    Animation<double> animation,
  ) {
    final curved = CurvedAnimation(
      parent: animation,
      curve: standardCurve,
      reverseCurve: reverseCurve,
    );
    return FadeTransition(
      opacity: curved,
      child: ScaleTransition(
        scale: Tween<double>(begin: pageEnterScale, end: 1).animate(curved),
        child: child,
      ),
    );
  }
}

class DirectionalSharedAxisTransition extends StatelessWidget {
  const DirectionalSharedAxisTransition({
    super.key,
    required this.animation,
    required this.direction,
    required this.child,
  });

  final Animation<double> animation;
  final ValueListenable<double> direction;
  final Widget child;

  @override
  Widget build(BuildContext context) => ValueListenableBuilder<double>(
        valueListenable: direction,
        child: child,
        builder: (context, direction, child) => AnimatedBuilder(
          animation: animation,
          child: child,
          builder: (context, child) {
            final outgoing = animation.status == AnimationStatus.reverse;
            final rawProgress =
                outgoing ? 1 - animation.value : animation.value;
            final progress =
                (outgoing ? AppMotion.reverseCurve : AppMotion.standardCurve)
                    .transform(rawProgress.clamp(0, 1));
            final opacity = outgoing
                ? 1 - (1 - AppMotion.pageExitOpacity) * progress
                : progress;
            final dx = outgoing
                ? -direction * AppMotion.pageExitOffset * progress
                : direction * AppMotion.pageEnterOffset * (1 - progress);
            final scale = outgoing
                ? 1.0
                : AppMotion.pageEnterScale +
                    (1 - AppMotion.pageEnterScale) * progress;

            return Opacity(
              opacity: opacity,
              child: FractionalTranslation(
                translation: Offset(dx, 0),
                child: Transform.scale(scale: scale, child: child),
              ),
            );
          },
        ),
      );
}
