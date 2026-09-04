import 'package:flutter/material.dart';

abstract final class DeskmateColors {
  static const background = Color(0xFFD8D6D1);
  static const surface = Color(0xFFE0DED9);
  static const surfaceRaised = Color(0xFFE7E5E0);
  static const surfaceMuted = Color(0xFFCFCDC8);
  static const ink = Color(0xFF292A28);
  static const inkMuted = Color(0xFF777873);
  static const inkFaint = Color(0xFFA3A39E);
  static const accent = Color(0xFF9BC4BC);
  static const accentStrong = Color(0xFF729F97);
  static const line = Color(0xFFCBC9C4);
  static const warning = Color(0xFFA77B5D);
  static const offline = Color(0xFFA6665F);
}

abstract final class DeskmateRadius {
  static const panel = 20.0;
  static const control = 13.0;
  static const pill = 999.0;
}

ThemeData buildDeskmateTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: DeskmateColors.accentStrong,
    brightness: Brightness.light,
    surface: DeskmateColors.surface,
  );
  return ThemeData(
    brightness: Brightness.light,
    useMaterial3: true,
    scaffoldBackgroundColor: DeskmateColors.background,
    colorScheme: scheme.copyWith(
      primary: DeskmateColors.accentStrong,
      onPrimary: DeskmateColors.ink,
      surface: DeskmateColors.surface,
      onSurface: DeskmateColors.ink,
      outline: DeskmateColors.line,
    ),
    textTheme: const TextTheme(
      displayLarge: TextStyle(
        color: DeskmateColors.ink,
        fontSize: 72,
        height: .98,
        fontWeight: FontWeight.w300,
        letterSpacing: -3,
      ),
      headlineLarge: TextStyle(
        color: DeskmateColors.ink,
        fontSize: 30,
        height: 1.12,
        fontWeight: FontWeight.w600,
        letterSpacing: -1,
      ),
      headlineMedium: TextStyle(
        color: DeskmateColors.ink,
        fontSize: 24,
        height: 1.16,
        fontWeight: FontWeight.w600,
        letterSpacing: -.7,
      ),
      titleLarge: TextStyle(
        color: DeskmateColors.ink,
        fontSize: 20,
        height: 1.2,
        fontWeight: FontWeight.w600,
        letterSpacing: -.4,
      ),
      titleMedium: TextStyle(
        color: DeskmateColors.ink,
        fontSize: 15,
        fontWeight: FontWeight.w600,
      ),
      bodyLarge: TextStyle(
        color: DeskmateColors.ink,
        fontSize: 15,
        height: 1.45,
      ),
      bodyMedium: TextStyle(
        color: DeskmateColors.inkMuted,
        fontSize: 13,
        height: 1.4,
      ),
      labelMedium: TextStyle(
        color: DeskmateColors.inkMuted,
        fontSize: 11,
        fontWeight: FontWeight.w600,
        letterSpacing: .2,
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: DeskmateColors.accent,
        foregroundColor: DeskmateColors.ink,
        minimumSize: const Size(96, 44),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(DeskmateRadius.control),
        ),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: DeskmateColors.ink,
        minimumSize: const Size(96, 44),
        side: const BorderSide(color: DeskmateColors.line),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(DeskmateRadius.control),
        ),
      ),
    ),
    snackBarTheme: const SnackBarThemeData(
      backgroundColor: DeskmateColors.ink,
      contentTextStyle: TextStyle(color: Colors.white),
      behavior: SnackBarBehavior.floating,
    ),
  );
}
