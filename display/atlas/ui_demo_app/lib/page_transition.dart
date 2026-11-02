import 'package:flutter/material.dart';

// Gentle dissolve: no movement or scaling, the old page dims away while the
// new one brightens in over the same slow curve.
class PageTransitionView extends StatelessWidget {
  const PageTransitionView(
      {super.key, required this.pageKey, required this.child});
  final Object pageKey;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final duration = MediaQuery.disableAnimationsOf(context)
        ? Duration.zero
        : const Duration(milliseconds: 500);
    return AnimatedSwitcher(
      duration: duration,
      reverseDuration: duration,
      switchInCurve: Curves.easeInOut,
      switchOutCurve: Curves.easeInOut,
      layoutBuilder: (current, previous) => Stack(
          fit: StackFit.passthrough,
          alignment: Alignment.center,
          children: [...previous, if (current != null) current]),
      transitionBuilder: (child, animation) => AnimatedBuilder(
          animation: animation,
          child: child,
          builder: (_, child) {
            final leaving = animation.status == AnimationStatus.reverse;
            return IgnorePointer(
                ignoring: leaving,
                child: ExcludeSemantics(
                    excluding: leaving,
                    child: FadeTransition(opacity: animation, child: child)));
          }),
      child: KeyedSubtree(key: ValueKey(pageKey), child: child),
    );
  }
}
