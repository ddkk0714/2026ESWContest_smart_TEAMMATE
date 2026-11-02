import 'package:flutter/material.dart';

/// The outer region closes the current popup on unused space. Content regions
/// consume taps themselves, keeping reading areas and their controls open.
class PopupTapRegion extends StatelessWidget {
  const PopupTapRegion({super.key, required this.child, this.dismiss = false});
  final Widget child;
  final bool dismiss;

  @override
  Widget build(BuildContext context) => GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: dismiss ? () => Navigator.of(context).pop() : () {},
      child: child);
}
