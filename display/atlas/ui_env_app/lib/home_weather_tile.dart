import 'package:flutter/material.dart';
import 'home_widgets.dart';
import 'weather_sky.dart';
import 'weather_snapshot.dart';

/// Weather shares the workspace's paper-like cards, blue type and quiet spacing.
class HomeWeatherTile extends StatelessWidget {
  const HomeWeatherTile(
      {super.key,
      required this.wide,
      required this.demo,
      required this.now,
      this.weather,
      this.onTap});
  final bool wide;
  final bool demo;
  final DateTime now;
  final WeatherSnapshot? weather;
  final VoidCallback? onTap;
  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, bounds) {
        final data = weather ??
            (demo
                ? WeatherSnapshot.demo(WeatherKind.cloudy,
                    isNight: now.hour < 6 || now.hour >= 18)
                : null);
        if (data == null) {
          return const HomeTile(
              child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                Icon(Icons.cloud_outlined, size: 38),
                SizedBox(height: 10),
                Text('날씨 연결 대기', style: homeHandwritingTextStyle)
              ]));
        }
        final small = bounds.maxHeight < 180;
        final tight = bounds.maxHeight < 140;
        final dark = Theme.of(context).brightness == Brightness.dark;
        final ink = dark ? const Color(0xff9bcfff) : const Color(0xff2c67a0);
        final muted = dark ? const Color(0xffacc5dd) : const Color(0xff627a91);
        final tint = ink.withValues(alpha: dark ? .10 : .065);
        Widget line(String text, double size, {Color? color, Key? key}) =>
            FittedBox(
                fit: BoxFit.scaleDown,
                alignment: Alignment.centerLeft,
                child: Text(text,
                    key: key,
                    maxLines: 1,
                    style: TextStyle(
                        color: color ?? ink, fontSize: size, height: 1.15)));
        Widget glyph(double size) => WeatherGlyph(
            kind: data.kind, isNight: data.isNight, size: size, color: ink);
        Widget city() => Row(children: [
              Icon(Icons.location_on_outlined,
                  size: tight ? 12 : (small ? 15 : 21), color: muted),
              const SizedBox(width: 4),
              Flexible(
                  child: line(data.city.replaceFirst('특별시', ''),
                      tight ? 12 : (small ? 15 : 21),
                      color: muted)),
              if (demo) ...[
                const SizedBox(width: 6),
                Text('데모',
                    style: TextStyle(
                        fontSize: small ? 8 : 10, color: muted, height: 1))
              ],
            ]);
        Widget temperature() => FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text('${data.temperature}°',
                key: const ValueKey('weather-temperature'),
                style: TextStyle(
                    fontSize: tight ? 32 : (small ? 43 : 76),
                    height: 1.05,
                    fontWeight: FontWeight.w400,
                    color: ink,
                    letterSpacing: -1)));
        Widget range() => line(
            '최고 ${data.high}°  최저 ${data.low}°', tight ? 10 : (small ? 12 : 16),
            color: muted);
        Widget chance() => Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(
                  data.kind == WeatherKind.snow
                      ? Icons.ac_unit_rounded
                      : Icons.water_drop_outlined,
                  size: tight ? 9 : (small ? 11 : 15),
                  color: muted),
              const SizedBox(width: 4),
              Flexible(
                  child: line('강수 ${data.precipitationChance}%',
                      tight ? 10 : (small ? 12 : 16),
                      color: muted, key: const ValueKey('weather-advice'))),
            ]);
        Widget smallCard() =>
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              city(),
              Expanded(
                  child: Row(children: [
                Expanded(child: temperature()),
                const SizedBox(width: 8),
                Container(
                    width: small ? 36 : 68,
                    height: small ? 36 : 68,
                    decoration:
                        BoxDecoration(color: tint, shape: BoxShape.circle),
                    alignment: Alignment.center,
                    child: glyph(small ? 26 : 47)),
              ])),
              line(weatherLabels[data.kind]!, tight ? 12 : (small ? 15 : 21),
                  key: const ValueKey('weather-condition')),
              SizedBox(height: small ? 4 : 8),
              range(),
              SizedBox(height: small ? 4 : 7),
              chance(),
            ]);
        Widget forecast() {
          final count = (bounds.maxWidth >= 360
                  ? 6
                  : bounds.maxWidth >= 250
                      ? 4
                      : 2)
              .clamp(0, data.hours.length);
          return Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                line('시간별 예보', tight ? 10 : (small ? 12 : 16), color: muted),
                SizedBox(height: small ? 6 : 12),
                SizedBox(
                    height: tight ? 40 : (small ? 53 : 110),
                    child: Row(
                        children: List.generate(count, (i) {
                      final hour = data.hours[i];
                      return Expanded(
                          child: Column(
                              mainAxisAlignment: MainAxisAlignment.spaceBetween,
                              children: [
                            FittedBox(
                                fit: BoxFit.scaleDown,
                                child: Text('${(now.hour + i) % 24}시',
                                    style: TextStyle(
                                        fontSize: tight ? 9 : (small ? 11 : 15),
                                        height: 1.1,
                                        color: muted))),
                            WeatherGlyph(
                                kind: hour.kind,
                                isNight: data.isNight,
                                size: tight ? 16 : (small ? 20 : 34),
                                color: ink),
                            FittedBox(
                                fit: BoxFit.scaleDown,
                                child: Text('${hour.temperature}°',
                                    style: TextStyle(
                                        fontSize:
                                            tight ? 11 : (small ? 13 : 19),
                                        height: 1.1,
                                        color: ink))),
                          ]));
                    }))),
              ]);
        }

        Widget wideCard() => Column(children: [
              Row(children: [
                Expanded(child: city()),
                const SizedBox(width: 8),
                Container(
                    padding: EdgeInsets.symmetric(
                        horizontal: small ? 5 : 8, vertical: small ? 3 : 4),
                    decoration: BoxDecoration(
                        color: tint, borderRadius: BorderRadius.circular(20)),
                    child: Row(mainAxisSize: MainAxisSize.min, children: [
                      glyph(small ? 14 : 21),
                      const SizedBox(width: 4),
                      line(weatherLabels[data.kind]!, small ? 13 : 17,
                          key: const ValueKey('weather-condition'))
                    ])),
              ]),
              SizedBox(height: small ? 6 : 12),
              Expanded(
                  child: Row(children: [
                Expanded(
                    flex: 4,
                    child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          temperature(),
                          SizedBox(height: small ? 4 : 9),
                          range(),
                          SizedBox(height: small ? 3 : 7),
                          chance(),
                        ])),
                SizedBox(width: small ? 8 : 15),
                if (bounds.maxWidth >= 280) ...[
                  Container(
                      width: 1,
                      height: small ? 44 : 88,
                      color: ink.withValues(alpha: .09)),
                  SizedBox(width: small ? 8 : 15),
                ],
                Expanded(flex: 7, child: forecast()),
              ])),
            ]);
        return HomeTile(
          color: dark ? const Color(0xff1b2d42) : Colors.white,
          padding: tight ? 12 : (small ? 15 : 21),
          onTap: onTap,
          label:
              '${data.city} ${data.temperature}도 ${weatherLabels[data.kind]}${demo ? ', 데모 날씨' : ''}',
          background: AnimatedSwitcher(
              key: ValueKey('weather-theme-$dark'),
              duration: const Duration(milliseconds: 500),
              child: WeatherSky(
                  key: ValueKey(
                      'weather-sky-${data.kind.name}-${data.isNight ? 'night' : 'day'}-$dark'),
                  kind: data.kind,
                  isNight: data.isNight,
                  dark: dark)),
          child: DefaultTextStyle.merge(
              style: homeHandwritingTextStyle.copyWith(color: ink),
              child: wide && bounds.maxWidth >= 250 ? wideCard() : smallCard()),
        );
      });
}
