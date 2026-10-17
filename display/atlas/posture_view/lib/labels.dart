/// 라벨 이름·색. `pc/posture_view.html` 의 KO / COLOR 표와 같은 값이다 —
/// 노트북 뷰와 Pi 5 화면이 같은 자세를 다르게 부르면 시연 중에 못 맞춘다.
library;

import 'package:flutter/material.dart';

const kBg = Color(0xFF0F1115);
const kFg = Color(0xFFE8EAF0);
const kDim = Color(0xFF8A90A0);
const kCard = Color(0xFF181B22);
const kCardDeep = Color(0xFF13161D);
const kLine = Color(0xFF2A2F3A);
const kBarTrack = Color(0xFF242936);
const kOk = Color(0xFF4CC38A);
const kWarn = Color(0xFFF5A524);
const kBad = Color(0xFFF06A6A);
const kBar = Color(0xFF3B82F6);

const kLabelKo = <String, String>{
  'upright': '바른자세',
  'lean_forward': '상체 앞으로',
  'recline': '젖힘',
  'face_down': '엎드림',
  'chin_rest': '턱괴기',
  'drowsy': '졸음',
  'away': '자리비움',
  'misplaced': '위치 재조정',
  'forward_head': '거북목',
  'unknown': '모름',
};

const kLabelColor = <String, Color>{
  'upright': kOk,
  'lean_forward': kWarn,
  'recline': kWarn,
  'face_down': kBad,
  'chin_rest': kWarn,
  'drowsy': kBad,
  'away': kDim,
  'misplaced': kDim,
  'forward_head': kBad,
  'unknown': kLine,
};

String labelKo(String label) => kLabelKo[label] ?? label;
Color labelColor(String label) => kLabelColor[label] ?? kFg;

/// 자세 서비스의 기본 HTTP 포트.
const kTofPort = 8771;

/// 화면에서 한 번에 고를 수 있는 Pi 4 주소. 유선 직결이 기본이고, 핫스팟은 두 보드가
/// 같은 휴대폰 핫스팟에 붙었을 때의 Pi 4 wlan0 주소다(2026-10-17 기준).
class AddressPreset {
  const AddressPreset(this.name, this.url);
  final String name;
  final String url;
}

const kAddressPresets = <AddressPreset>[
  AddressPreset('유선 직결', 'http://172.16.34.199:$kTofPort'),
  AddressPreset('핫스팟 So', 'http://172.20.10.3:$kTofPort'),
];

const kDefaultBaseUrl = 'http://172.16.34.199:$kTofPort';
