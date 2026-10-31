import 'dart:convert';
import 'package:flutter/services.dart';

class Co2Band {
  const Co2Band(this.upper, this.label, this.face);
  final int? upper;
  final String label;
  final int face;
}

/// Display-only grading. It never changes the hub's FSM or automation rules.
class Co2Grades {
  Co2Grades(this.bands);
  final List<Co2Band> bands;
  static Future<Co2Grades> load() async {
    final raw =
        jsonDecode(await rootBundle.loadString('assets/config/co2_bands.json'))
            as Map;
    return Co2Grades.fromJson(raw);
  }

  factory Co2Grades.fromJson(Map raw) => Co2Grades((raw['bands'] as List)
      .map((b) => Co2Band(
          b['upper_ppm'] as int?, b['label'] as String, b['face'] as int))
      .toList());

  Co2Band? forValue(int? ppm) {
    if (ppm == null || ppm <= 0) return null;
    for (final band in bands) {
      if (band.upper == null || ppm < band.upper!) return band;
    }
    return null;
  }
}
