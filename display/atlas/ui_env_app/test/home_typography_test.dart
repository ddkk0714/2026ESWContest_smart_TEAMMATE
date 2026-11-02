import 'package:deskmate_display/home_widgets.dart';
import 'package:deskmate_display/home_weather_tile.dart';
import 'package:deskmate_display/irumae.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    final loader = FontLoader('HakgyoansimGeurimilgi');
    loader.addFont(
        rootBundle.load('assets/fonts/HakgyoansimGeurimilgi-Regular.ttf'));
    await loader.load();
  });
  for (final size in [
    const Size(210, 230),
    const Size(438, 230),
    const Size(272, 142),
    const Size(128, 142)
  ]) {
    testWidgets('Home typography fits $size and focus shows elapsed time',
        (tester) async {
      Widget frame(Widget child) => MaterialApp(
          theme: ThemeData(fontFamily: 'High1Wonchuri'),
          home: Center(child: SizedBox.fromSize(size: size, child: child)));
      await tester.pumpWidget(frame(FocusCountdownTile(
          seconds: 1335,
          minutes: 25,
          running: true,
          onOpen: () {},
          mood: IrumaeMood.working)));
      expect(
          tester
              .widget<Text>(find.byKey(const ValueKey('home-focus-countdown')))
              .data,
          '02:45');
      expect(find.text('목표 25 min'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(frame(HomeWeatherTile(
          wide: size.width > 250, demo: true, now: DateTime(2026, 10, 18))));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(frame(HomeMusicTile(
          title: '드뷔시 · 달빛',
          playing: false,
          position: Duration.zero,
          duration: const Duration(minutes: 5),
          onPlaylist: () {},
          onToggle: () {},
          onPrevious: () {},
          onNext: () {},
          favorite: false,
          onFavorite: () {},
          onVolume: () {})));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    });
  }
}
