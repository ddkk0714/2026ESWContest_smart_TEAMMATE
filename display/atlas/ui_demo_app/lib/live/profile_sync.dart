// V34: 다른 화면에서 고친 첫 설정 → 이 앱의 같은 사람 프로필.
//
// devview 는 hub 가 지금 쓰는 사람(active_profile_id)의 프로필을 같은 profile_id 로 고쳐 `deskmate/profile/user` 로 보낸다
// (node laptop-devview, retain). 앱은 그 id 의 저장된 프로필을 고쳐 profiles.json 에 남긴다. 모르는 id 는 무시한다.
// 고친 뒤 앱이 보낼 값이 받은 값과 같으면 다시 보내지 않는다(서로 주고받기만 반복하지 않게).
// 다르면(앱에 없는 항목을 버렸다든가) 지금 고른 사람일 때만 앱의 값으로 다시 보낸다 — 다른 사람 것을 보내면 hub 의 지금 사람이 바뀐다.
library;

import '../demo_model.dart';
import 'live_link.dart';
import 'profile_payload.dart';

/// 반영했으면 true. [onPlacement] 는 지금 사람의 장치 위치가 바뀌었을 때 개발자 설정에도 알리려고.
bool syncRemoteProfile(DemoModel model, LiveLink live, Map<String, dynamic> data,
    {void Function(String placement)? onPlacement}) {
  final before = model.persona.placement;
  final target = model.applyRemoteProfile(data);
  if (target == null) return false;
  final current = identical(target, model.persona);
  if (current && target.placement != before) onPlacement?.call(target.placement);
  if (current && !profileMatches(target, data)) live.publishProfile(target);
  return true;
}
