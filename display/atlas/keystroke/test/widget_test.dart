import 'package:flutter_test/flutter_test.dart';
import 'package:keystroke/main.dart';

void main() {
  testWidgets('DeskmateApp renders dashboard', (tester) async {
    await tester.pumpWidget(const DeskmateApp());
    expect(find.text('DESKMATE'), findsOneWidget);
  });
}
