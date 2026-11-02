# 이루매 UI 애니메이션

- 앱 자산: `irumae-atlas-soft.png` (4열 × 5상태, 1402×1122 투명 PNG). 원본 `irumae-atlas.png`는 보존한다.
- 사용자가 제공한 서울시립대학교 이루매 그림 4장을 참고해 내장 `image_gen` 도구로 생성했다. 대학 공식 배포 애니메이션 원본이 아니다.
- 원래 그림의 `안녕`, `ㅊㅋㅊㅋ`, `털썩` 자막은 넣지 않았다.
- 행 순서: 인사 / 노트북 작업 / 축하 / 책상에서 잠든 모습 / 털썩.
- `lib/irumae.dart`와 `lib/irumae_motion.dart`가 실측 행 경계와 상태별 포즈를 사용한다. 운영체제의 동작 줄이기 설정을 따른다.

## 생성 문구

Use case: identity-preserve. Create one production animation sprite atlas for this exact Korean university owl mascot 이루매 from the four supplied references. Preserve round white face inside brown owl hood, small yellow beak and feet, blue university clothes, thick hand-drawn brown outlines. References: image1 work at laptop with flames, image2 waving greeting in blue cap, image3 happy celebration, image4 sleeping at desk and discouraged sitting. Remove all Korean captions, handwriting, music notes and motion-text labels (안녕, ㅊㅋㅊㅋ, 털썩 etc), no text except tiny UOS on cap if desired. REQUIRED layout: perfectly regular FOUR COLUMNS by FIVE ROWS (20 sprites total), square equally-sized cells, landscape ratio 4:5 canvas, actual transparent background, no grid, no labels, no extra decorative background. Each cell shows the whole character at matching size centered with at least 8% transparent margins, no part extends to adjacent cells. Rows are five states; each row's 4 columns are 4 successive subtly different keyframes of the same looping movement. Row1: idle hello waving (arm low, middle, high, middle), blue cap. Row2: focused working at laptop desk (typing left, typing right, typing left, typing right), gentle warm orange flames behind owl, no writing. Row3: success celebrating smiling and bouncing hands up (lower, middle, high, middle), cap, no captions/music notes. Row4: asleep face resting on desk eyes closed (breathing down, up, down, up), no Z letters. Row5: discouraged/slumped sitting on floor (head slightly down, lower, down, lower), no captions. Flat clean sticker illustration, consistent brown/blue/gold colors and original character identity. All 20 cells must stay exactly on the 4x5 uniform grid and same scale. This is a game sprite sheet consumed via row/column cropping in a Flutter app. No shadows outside the sprite.

## 상태 연결

- 기본: 인사. 집중 타이머 진행: 작업.
- 타이머 완료: 12초 축하 후 인사. 일시정지는 실패로 보지 않는다.
- 명시적 이번 집중 종료: 10초 털썩 후 인사.
- 쉬기 선택 또는 기존 허브 `REST`: 잠든 포즈. 현재 입력 계약에는 확정 수면 판정이 없으므로, 무입력·피로 점수로 수면을 추정하지 않는다. 향후 명시적 `phase=sleep`가 들어오면 같은 포즈를 쓸 수 있다.
- 데모의 집중 팝업에서 다섯 상태를 미리 볼 수 있다. 실제 센서·제어 상태를 변경하지 않는다.

추가 후보: 잠깐 쉬기에는 물 마시기/스트레칭, 3회 완료에는 별 모으기, 장기 미사용 후 돌아오면 반갑게 맞이하기. 입력 내용이나 카메라를 추가 수집할 필요가 없는 UI 이벤트 기반 후보다.


## 2026-10-18 느린 연속 동작 적용

- 내장 `image_gen` 도구의 편집 모드로 기존 시트의 두 번째 행에서 불꽃만 제거했다.
  편집본 `irumae-atlas-soft.png`를 앱에 포함하며 원본 크기·투명도·행 경계를 유지한다.
- 불꽃은 앱에서 별도로 그린다. 기존보다 작은 폭·높이로, 20% 불투명도의 옅은 주황색을 사용한다.
  캐릭터와 노트북의 선명도는 낮추지 않는다.
- 불규칙한 네 포즈를 빠르게 교체하거나 서로 겹치면 얼굴·손 잔상이 생겨,
  상태별 선명한 포즈 하나에 연속적인 위치·기울기·호흡 배율을 적용한다.
  인사: 작은 흔들림 / 작업: 미세한 호흡 / 축하: 느린 바운스 / 잠듦: 호흡 / 털썩: 작은 처짐.
- 한 주기: 인사 4.8초, 작업 5.8초, 축하 3초, 잠듦 7.2초, 털썩 4초.
  속도 피드백에 따라 축하·털썩은 약 두 배 빠르게, 나머지는 약 20% 짧은 주기로 조정했다.
  최초 프레임 교체 방식의 재생 주기는 1.36초였다. 각 루프의 시작·끝 위치와 속도는 연결된다.
- 상태가 바뀔 때만 650ms 전환을 적용한다. 연속 동작은 화면 전체를 매번 다시 만들지 않고
  캐릭터 영역만 다시 그린다. 숨긴 화면·동작 줄이기에서는 재생을 멈춘다.

### 이번 편집 문구 (내장 image_gen)

Use case: precise-object-edit. Edit target is the supplied transparent animation sprite atlas of the brown-and-blue university owl mascot. Make ONE change only: REMOVE ALL orange/red flame shapes from behind the four laptop-working owl sprites in the SECOND ROW. Replace only those flames with transparent pixels. Preserve the laptop, brown desktop, owl body, hair/hood outline, white face, hands, eyes, beak, clothes and every character detail. Preserve ALL OTHER FOUR ROWS exactly (greeting first row, celebration third row, sleeping fourth row, discouraged fifth row). Preserve the FOUR COLUMNS and all existing sprite placements, sizes, row boundaries and transparent spacing exactly, with the same canvas and composition as the source. This is a sprite atlas consumed using existing cropping coordinates, so do NOT recenter, rescale, crop, add margins or rearrange any sprite. Real transparent background, no new text, no grid, no new flames, no background, no checkerboard pixels. Keep the original visual identity unchanged. Return the complete edited atlas.
