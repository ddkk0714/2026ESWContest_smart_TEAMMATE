enum WeatherKind { clear, cloudy, rain, snow, storm, fog }

const weatherLabels = {
  WeatherKind.clear: '맑음',
  WeatherKind.cloudy: '흐림',
  WeatherKind.rain: '비',
  WeatherKind.snow: '눈',
  WeatherKind.storm: '뇌우',
  WeatherKind.fog: '안개',
};

class WeatherHour {
  const WeatherHour(this.temperature, this.kind);
  final int temperature;
  final WeatherKind kind;
}

class WeatherSnapshot {
  const WeatherSnapshot(
      {required this.kind,
      required this.isNight,
      required this.temperature,
      required this.high,
      required this.low,
      required this.precipitationChance,
      required this.hours,
      this.city = '서울특별시'});
  final WeatherKind kind;
  final bool isNight;
  final int temperature;
  final int high;
  final int low;
  final int precipitationChance;
  final List<WeatherHour> hours;
  final String city;

  factory WeatherSnapshot.demo(WeatherKind kind, {bool isNight = false}) {
    final temperature = switch (kind) {
      WeatherKind.clear => isNight ? 14 : 22,
      WeatherKind.cloudy => 17,
      WeatherKind.rain => 16,
      WeatherKind.snow => -2,
      WeatherKind.storm => 18,
      WeatherKind.fog => 14,
    };
    final chance = switch (kind) {
      WeatherKind.clear => 10,
      WeatherKind.cloudy => 70,
      WeatherKind.rain => 90,
      WeatherKind.snow => 80,
      WeatherKind.storm => 95,
      WeatherKind.fog => 20,
    };
    return WeatherSnapshot(
        kind: kind,
        isNight: isNight,
        temperature: temperature,
        high: kind == WeatherKind.snow ? 1 : 22,
        low: kind == WeatherKind.snow ? -5 : (isNight ? 13 : 15),
        precipitationChance: chance,
        hours: List.generate(
            6,
            (i) => WeatherHour(
                temperature + [0, 1, 2, 2, 2, 3][i] * (isNight ? -1 : 1),
                kind == WeatherKind.cloudy ? WeatherKind.rain : kind)));
  }
}
