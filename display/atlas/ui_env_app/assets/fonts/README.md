# UI 서체 구성

하다산스 적용을 되돌려, 현재 한국어 본문은 하이원 원추리체 B, 홈의 지정 제목·날씨·집중 숫자는 학교안심 그림일기를 사용한다. 디지털 시계의 LG EI Headline과 DESKMATE 로고 이미지는 유지한다. Hada Sans 원본과 라이선스는 이전 시안 자산으로 보관하며 현재 화면에는 사용하지 않는다.

- `HakgyoansimGeurimilgi-Regular.ttf`: 사용자가 제공한 `학교안심 그림일기.zip`의
  `Hakgyoansim Geurimilgi TTF R.ttf` 원본이다. Regular 400으로 등록한다.
  폰트 내부 이름은 `학교안심 그림일기 TTF R`이며, 한글 완성형 11,172자와 요청 문구의 글리프를 확인했다.
  홈 캘린더의 월 제목, 실내 제목, 공기질 상태, 집중 제목, 음악 제목·작곡가에 적용한다.
  날씨 위젯 전체(숫자 포함)와 날씨 미리보기 팝업에도 적용한다.
  원본 파일 데이터·메타데이터는 수정하지 않았다. 폰트 내부 안내 URL: https://www.keris.or.kr/

- DESKMATE 로고는 `../brand/deskmate-wordmark.png`의 고정 문구 이미지로 보존한다.
  Windows에 설치된 Comic Sans MS로 문구 전체를 렌더링했으며 폰트 파일은 앱에 동봉하지 않는다.
  개별 글자 이미지나 비트맵 폰트가 아닌 단일 로고다.
  근거: https://learn.microsoft.com/en-us/typography/fonts/font-faq#redistribution-and-extended-rights

- `High1WonchuriBodyB.ttf`: 사용자가 제공한 `하이원_원추리체_B.zip` 안의
  `high1 Wonchuri Body B.ttf` 원본이다. 앱의 한글과 시계 외 UI의 기본 서체로 사용한다.
  글꼴 이름은 pubspec의 별칭이며 폰트 내부 데이터는 수정하지 않았다.
  별도 지정하지 않은 홈 본문은 High1Wonchuri / w600을 사용한다.
  사용자가 지정한 제목·공기질 상태·음악 문구·날씨는 위의 학교안심 그림일기로 재정의한다.
  시계 숫자의 전용 서체와 DESKMATE 로고 이미지는 유지한다.
  저작자: 강원랜드. 작품명: 하이원 원추리체 B.
  동봉된 출처·이용표시: 공공누리 출처표시(상업적·비상업적 이용 및 변형 가능).
  출처: https://gongu.copyright.or.kr/gongu/wrt/wrt/view.do?wrtSn=13373089&menuNo=200195

- `LGEIHeadlineVF.ttf`: 사용자가 제공한 `LG-EI-Headline_all.zip`의 가변 폰트. 시계 숫자에 사용한다.
  압축본의 OTF·TTF 11종 cmap을 확인했으며 한글 완성형 글리프는 없다.
- `LGEITextTTF-Regular.ttf`, `LGEITextTTF-SemiBold.ttf`, `LGEITextTTF-Bold.ttf`:
  LG 공식 브랜드 Asset의 TTF 배포본(2026-10-18 확인)에서 가져왔다. 각각 400·600·700 굵기로 등록했다.
  한글 완성형 U+AC00~U+D7A3을 포함한다. 하이원 서체에 없는 글자의 대체 서체로 둔다.

공식 배포 페이지: https://www.lge.co.kr/company/info/brandAsset

공식 원본: https://www.lge.co.kr/kr/company/upload/info/asset/font/LG-EI-text-ttf.zip

LG의 서체 소개: https://www.lge.co.kr/company/info/visualIdentity

폰트 파일과 저작권 메타데이터는 원본을 유지한다. 폰트를 다른 이름의 서체로 변경하거나 수정하지 않는다.
