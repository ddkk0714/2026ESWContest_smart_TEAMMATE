# UI demo versions

UI를 변경할 때마다 V 번호를 올리고, 이전 앱은 별도 ID와 소스로 보존한다.
`ui_demo_versions/` 보관본은 용량 때문에 레포에 올리지 않고 개발 PC에만 둔다. 확정 사항은 [ui_demo_app/DECISIONS.md](ui_demo_app/DECISIONS.md)에 있다.
기존 버전의 패키지를 새 UI로 덮어쓰지 않는다.

| 버전 | 화면 | 소스 | 앱 ID |
|---|---|---|---|
| V1 | 상단 2줄, 시계 아래 문구, 집중 시작·내 책상 버튼 | `ui_demo_versions/v1` | `com.atlas.app.deskmate_ui_demo_v1` |
| V2 | 상단 1줄, 시계 터치로 시작, 하단 상태 항목으로 위젯 진입 | `ui_demo_versions/v2` | `com.atlas.app.deskmate_ui_demo_v2` |
| V3 | 버전 배지 제거, 시계 중앙·날짜 함께 상향 | `ui_demo_versions/v3` | `com.atlas.app.deskmate_ui_demo_v3` |
| V4 | 문구·시계·날짜 재배치, 프로필 선택·짝수 슬롯·이모지 설정 | `ui_demo_versions/v4` | `com.atlas.app.deskmate_ui_demo_v4` |
| V5 | 시계 위 프로필 팝업, 이미지 아래 이름, 프로필 관리 | `ui_demo_versions/v5` | `com.atlas.app.deskmate_ui_demo_v5` |
| V6 | 발표용 시연 조작창 명칭 통일, 피로·졸음·부재 항상 활성 | `ui_demo_versions/v6` | `com.atlas.app.deskmate_ui_demo_v6` |
| V7 | 하단 상태 항목에서 PR56 폰트 수정 멀티 위젯으로 전환 | `ui_demo_versions/v7` | `com.atlas.app.deskmate_ui_demo_v7` |
| V8 | 멀티 위젯 색상·폰트 통일, 밝은 중앙 제안 카드 | `ui_demo_versions/v8` | `com.atlas.app.deskmate_ui_demo_v8` |
| V9 | 집중 상세 카드 정렬·아이콘, 멀티 위젯 선택바 제거 | `ui_demo_versions/v9` | `com.atlas.app.deskmate_ui_demo_v9` |
| V10 | 팝업 안에서 한 질문씩 프로필 생성, 취향을 담은 환영 화면 | `ui_demo_versions/v10` | `com.atlas.app.deskmate_ui_demo_v10` |
| V11 | 랜덤 기본 별명, 팝업 내 한글·영문 이름 키보드 | `ui_demo_versions/v11` | `com.atlas.app.deskmate_ui_demo_v11` |
| V12 | 프로필별 개인화 세부 설정 4개 탭, 로고 유지 | `ui_demo_versions/v12` | `com.atlas.app.deskmate_ui_demo_v12` |
| V13 | V12 개인화 설정 유지, 앱 전체 좌측 위 DESKMATE 로고 제거 | `ui_demo_versions/v13` | `com.atlas.app.deskmate_ui_demo_v13` |
| V14 | 제품 문구 정리, 자연스러운 화면·탭·팝업 전환 | `ui_demo_versions/v14` | `com.atlas.app.deskmate_ui_demo_v14` |
| V15 | 페이드 스루 화면 전환, 피로 제안 카드 응답 후 바로 닫힘 | `ui_demo_versions/v15` | `com.atlas.app.deskmate_ui_demo_v15` |
| V16 | 앞/뒤 방향 가로 슬라이드 화면 전환, V15 카드 수정 유지 | `ui_demo_versions/v16` | `com.atlas.app.deskmate_ui_demo_v16` |
| V17 | 움직임 없는 500ms 밝기 디졸브 화면 전환, V15 카드 수정 유지 | `ui_demo_app` | `com.atlas.app.deskmate_ui_demo_v17` |

모든 버전은 우측 상단 X 버튼으로 앱을 종료한다. 어두운 집중 시계에도 같은 버튼이 있다.
종료하면 메모리에만 있던 프로필·시연 기록은 초기화된다.

## 다음 변경

1. 현재 V17 `ui_demo_app`을 생성물 없이 `ui_demo_versions/v17`에 보관한다.
   `build/`, `.dart_tool/`, ephemeral, SDK, 로그는 복사하지 않는다.
2. 새 UI는 `ui_demo_app`에서 개발하고 표시 이름을 `DESKMATE V18`로 올린다.
3. `appinfo.json`의 id·entry·version, CMake의 ATLAS_APP_ID·BINARY_NAME,
   pubspec version을 함께 올린다. 화면에는 버전 배지를 표시하지 않는다. V18 ID는 `com.atlas.app.deskmate_ui_demo_v18`다.
4. 각 버전을 분석·테스트·release 빌드하고 Pi에 별도 설치한다.
5. 이전 버전은 명시적인 삭제 요청이 있을 때만 제거한다. 화면 비교 시 현재 앱을 Stop 하고 비교할 앱을 Start 한다.

기존 `DESKMATE`와 `DESKMATE UI Test`는 별도의 제품·과거 앱으로 유지한다.
번호 없는 최초 데모도 보존한다. 삭제는 사용자의 명시적 요청이 있을 때만 한다.

Pi 앱 목록에는 번호 버전 V8~V17이 남아 있다. V1~V7은 사용자 요청으로 기기에서 삭제했으며 소스만 보존한다.
CLI에서는 `abusctl call com.atlas.AppManager1 Start <앱 ID>`를 사용한다.

V14부터 앱 표시 이름은 DESKMATE V<N>이다. 이전 설치 앱의 이름과 소스는 당시 버전 그대로 보존한다.
