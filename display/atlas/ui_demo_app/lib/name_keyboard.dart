import 'package:flutter/material.dart';

const _initials = 'ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ';
const _vowels = 'ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ';
const _finals = ' ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ';
const _pairs = {
  'ㅗㅏ': 'ㅘ',
  'ㅗㅐ': 'ㅙ',
  'ㅗㅣ': 'ㅚ',
  'ㅜㅓ': 'ㅝ',
  'ㅜㅔ': 'ㅞ',
  'ㅜㅣ': 'ㅟ',
  'ㅡㅣ': 'ㅢ',
  'ㄱㅅ': 'ㄳ',
  'ㄴㅈ': 'ㄵ',
  'ㄴㅎ': 'ㄶ',
  'ㄹㄱ': 'ㄺ',
  'ㄹㅁ': 'ㄻ',
  'ㄹㅂ': 'ㄼ',
  'ㄹㅅ': 'ㄽ',
  'ㄹㅌ': 'ㄾ',
  'ㄹㅍ': 'ㄿ',
  'ㄹㅎ': 'ㅀ',
  'ㅂㅅ': 'ㅄ',
};

String _syllable(int initial, int vowel, int finalIndex) =>
    String.fromCharCode(0xAC00 + (initial * 21 + vowel) * 28 + finalIndex);

String? _split(String value) {
  for (final entry in _pairs.entries) {
    if (entry.value == value) return entry.key;
  }
  return null;
}

// Compose only the syllable immediately before the cursor. Spaces and Latin
// letters terminate composition, as on a standard two-set Korean keyboard.
String composeName(String text, String key) {
  if (text.isEmpty) return key == 'backspace' ? '' : key;
  final last = text.substring(text.length - 1);
  final prefix = text.substring(0, text.length - 1);
  final code = last.codeUnitAt(0) - 0xAC00;
  if (code >= 0 && code < 11172) {
    final initial = code ~/ 588;
    final vowel = (code % 588) ~/ 28;
    final finalIndex = code % 28;
    if (key == 'backspace') {
      if (finalIndex > 0) {
        final pair = _split(_finals[finalIndex]);
        return prefix +
            _syllable(
                initial, vowel, pair == null ? 0 : _finals.indexOf(pair[0]));
      }
      final pair = _split(_vowels[vowel]);
      return prefix +
          (pair == null
              ? _initials[initial]
              : _syllable(initial, _vowels.indexOf(pair[0]), 0));
    }
    final nextVowel = _vowels.indexOf(key);
    if (nextVowel >= 0) {
      if (finalIndex > 0) {
        final tail = _finals[finalIndex];
        final pair = _split(tail);
        final nextInitial = _initials.indexOf(pair == null ? tail : pair[1]);
        return prefix +
            _syllable(
                initial, vowel, pair == null ? 0 : _finals.indexOf(pair[0])) +
            _syllable(nextInitial, nextVowel, 0);
      }
      final combined = _pairs[_vowels[vowel] + key];
      if (combined != null) {
        return prefix + _syllable(initial, _vowels.indexOf(combined), 0);
      }
    }
    final nextFinal = _finals.indexOf(key);
    if (nextFinal > 0) {
      if (finalIndex == 0) return prefix + _syllable(initial, vowel, nextFinal);
      final combined = _pairs[_finals[finalIndex] + key];
      if (combined != null) {
        return prefix + _syllable(initial, vowel, _finals.indexOf(combined));
      }
    }
  } else {
    if (key == 'backspace') {
      return prefix + (_split(last)?[0] ?? '');
    }
    final initial = _initials.indexOf(last);
    final vowel = _vowels.indexOf(key);
    if (initial >= 0 && vowel >= 0) {
      return prefix + _syllable(initial, vowel, 0);
    }
    final combined = _pairs[last + key];
    if (combined != null) return prefix + combined;
  }
  return text + key;
}

void editName(TextEditingController controller, String key) {
  final value = controller.value;
  final selection = value.selection.isValid
      ? value.selection
      : TextSelection.collapsed(offset: value.text.length);
  final before = value.text.substring(0, selection.start);
  final after = value.text.substring(selection.end);
  final edited = key == 'backspace' && !selection.isCollapsed
      ? before
      : composeName(before, key);
  final result = edited + after;
  if (result.length > 16) return;
  controller.value = TextEditingValue(
      text: result, selection: TextSelection.collapsed(offset: edited.length));
}

class NameKeyboard extends StatefulWidget {
  const NameKeyboard({super.key, required this.onKey, required this.onDone});
  final ValueChanged<String> onKey;
  final VoidCallback onDone;
  @override
  State<NameKeyboard> createState() => _NameKeyboardState();
}

class _NameKeyboardState extends State<NameKeyboard> {
  bool korean = true;
  bool shift = false;
  @override
  Widget build(BuildContext context) {
    final rows = korean
        ? [shift ? 'ㅃㅉㄸㄲㅆㅛㅕㅑㅒㅖ' : 'ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔ', 'ㅁㄴㅇㄹㅎㅗㅓㅏㅣ', 'ㅋㅌㅊㅍㅠㅜㅡ']
        : [
            shift ? 'QWERTYUIOP' : 'qwertyuiop',
            shift ? 'ASDFGHJKL' : 'asdfghjkl',
            shift ? 'ZXCVBNM' : 'zxcvbnm'
          ];
    Widget button(String label, VoidCallback action, {int flex = 1}) =>
        Expanded(
            flex: flex,
            child: Padding(
                padding: const EdgeInsets.all(3),
                child: TextButton(
                    style: TextButton.styleFrom(
                        backgroundColor: const Color(0xFFFFFEF8),
                        foregroundColor: const Color(0xFF263B37),
                        padding: EdgeInsets.zero,
                        minimumSize: const Size(0, 36),
                        shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(10))),
                    onPressed: action,
                    child: Text(label, style: const TextStyle(fontSize: 21)))));
    return Focus(
        canRequestFocus: false,
        descendantsAreFocusable: false,
        child: Column(
            key: const ValueKey('name-keyboard'),
            mainAxisSize: MainAxisSize.min,
            children: [
              for (int i = 0; i < rows.length; i++)
                SizedBox(
                    height: 42,
                    child: Row(children: [
                      if (i == 2)
                        button(shift ? '⇧ 켜짐' : '⇧',
                            () => setState(() => shift = !shift),
                            flex: 2),
                      for (final key in rows[i].split(''))
                        button(key, () {
                          widget.onKey(key);
                          if (shift) setState(() => shift = false);
                        }),
                      if (i == 2)
                        button('⌫', () => widget.onKey('backspace'), flex: 2),
                    ])),
              SizedBox(
                  height: 42,
                  child: Row(children: [
                    button(
                        korean ? '한 → EN' : 'EN → 한',
                        () => setState(() {
                              korean = !korean;
                              shift = false;
                            }),
                        flex: 2),
                    button('띄어쓰기', () => widget.onKey(' '), flex: 5),
                    button('입력 완료', widget.onDone, flex: 2),
                  ])),
            ]));
  }
}

/// 보드 주소 한 글자 편집. 주소에 쓰는 글자(영문 소문자·숫자·점·하이픈)만 받고 64자까지.
String editHostText(String text, String key, {int maxLength = 64}) {
  if (key == 'backspace') {
    return text.isEmpty ? text : text.substring(0, text.length - 1);
  }
  if (text.length >= maxLength || !RegExp(r'^[a-z0-9.\-]$').hasMatch(key)) {
    return text;
  }
  return text + key;
}

/// 보드 주소·포트 입력용 앱 내부 키보드. 이름 키보드와 같은 단추 모양이며,
/// Pi 5 에서 시스템 키보드가 열리자마자 닫히는 문제(V11)를 같은 방식으로 피한다.
class HostKeyboard extends StatefulWidget {
  const HostKeyboard(
      {super.key,
      required this.onKey,
      required this.onDone,
      this.digitsOnly = false});
  final ValueChanged<String> onKey;
  final VoidCallback onDone;

  /// 포트처럼 숫자만 받는 칸.
  final bool digitsOnly;
  @override
  State<HostKeyboard> createState() => _HostKeyboardState();
}

class _HostKeyboardState extends State<HostKeyboard> {
  bool letters = false;
  @override
  Widget build(BuildContext context) {
    final showLetters = letters && !widget.digitsOnly;
    final rows = showLetters
        ? ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']
        : ['1234567890'];
    Widget button(String label, VoidCallback action, {int flex = 1}) =>
        Expanded(
            flex: flex,
            child: Padding(
                padding: const EdgeInsets.all(3),
                child: TextButton(
                    style: TextButton.styleFrom(
                        backgroundColor: const Color(0xFFFFFEF8),
                        foregroundColor: const Color(0xFF263B37),
                        padding: EdgeInsets.zero,
                        minimumSize: const Size(0, 36),
                        shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(10))),
                    onPressed: action,
                    child: Text(label, style: const TextStyle(fontSize: 21)))));
    return Focus(
        canRequestFocus: false,
        descendantsAreFocusable: false,
        child: Column(
            key: const ValueKey('host-keyboard'),
            mainAxisSize: MainAxisSize.min,
            children: [
              for (final row in rows)
                SizedBox(
                    height: 42,
                    child: Row(children: [
                      for (final key in row.split(''))
                        button(key, () => widget.onKey(key)),
                    ])),
              SizedBox(
                  height: 42,
                  child: Row(children: [
                    if (!widget.digitsOnly)
                      button(showLetters ? '123' : 'abc',
                          () => setState(() => letters = !letters),
                          flex: 2),
                    if (!widget.digitsOnly) button('.', () => widget.onKey('.')),
                    if (!widget.digitsOnly) button('-', () => widget.onKey('-')),
                    button('⌫', () => widget.onKey('backspace'), flex: 2),
                    button('입력 완료', widget.onDone, flex: 3),
                  ])),
            ]));
  }
}
