/// 램프 밝기 조정의 기본값. 코드 곳곳에 숫자를 박지 않고 여기 한곳에 둔다.
/// 하한은 설정 `lamp_brightness_floor` 로 바꿀 수 있고, 없으면 이 값.
library;

/// adjust_brightness 의 하한(iLink 원시 밝기 0~255). 30 ≈ 12 %.
const kLampBrightnessFloorDefault = 30;

/// 올리는데 반올림 결과가 기준과 같을 때 최소로 올리는 단계.
const kLampAdjustMinStep = 10;
