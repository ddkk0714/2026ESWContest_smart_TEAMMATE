import 'package:flutter/material.dart';

abstract final class DeskmateColors {
  static const background = Color(0xFFEFF6FC);
  static const surface = Color(0xFFFFFFFF);
  static const surfaceRaised = Color(0xFFF8FCFF);
  static const surfaceMuted = Color(0xFFE5F0F9);
  static const ink = Color(0xFF1E3B5A);
  static const inkMuted = Color(0xFF627A91);
  static const inkFaint = Color(0xFF93AABD);
  static const accent = Color(0xFFD7EAFB);
  static const accentStrong = Color(0xFF2C67A0);
  static const line = Color(0xFFD2E3F1);
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
    fontFamily: 'High1Wonchuri',
    fontFamilyFallback: const [
      'LGEIText',
      'LGEIHeadline',
      'Noto Sans KR',
      'sans-serif'
    ],
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
