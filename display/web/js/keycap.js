/* DESKMATE display — 보드에 직접 꽂힌 키보드를 캡처해 특징을 뽑는다.
 *
 * collector/capture.py + collector/features.py 를 JS 로 옮긴 것이다.
 * 상수(flight_gap_max 2.0s · idle_gap 3.0s · dwell 상한 2000ms)와 반올림 자릿수,
 * to_payload() 의 필드 이름까지 파이썬 쪽과 같게 맞춰 두 노드의 값을 그대로 비교할 수 있다.
 *
 * 왜 브라우저에서 하나:
 *   ATLAS 는 Yocto 최소 이미지라 Python 런타임이 없다. 보드 키보드로 실기 테스트를 하려면
 *   web-app 이 직접 keydown/keyup 을 받아 계산하는 수밖에 없다.
 *
 * 프라이버시 원칙(타협 불가):
 *   - 이 모듈은 KeyboardEvent 를 받지 않는다. 호출자가 classifyCode() 로 '종류' 를 만들어 넘긴다.
 *   - 종류는 char/space/backspace/enter/other 다섯 가지뿐이라 입력 내용을 복원할 수 없다.
 *   - event.key(실제 문자)는 이 파일 어디서도 읽지 않는다.
 *
 * 브라우저에서는 <script> 로 읽어 window.DeskmateKeycap 을 쓰고,
 * node --test 에서는 require() 로 같은 코드를 검증한다. (UMD — store.js 와 같은 방식)
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.DeskmateKeycap = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // collector/capture.py 의 KeyKind 와 같은 집합.
  var KIND = {
    CHAR: "char",
    SPACE: "space",
    BACKSPACE: "backspace",
    ENTER: "enter",
    OTHER: "other"
  };

  // 문자로 입력되는 기호·숫자패드 키. pynput 이 KeyCode 로 주는 것들과 같은 범위다.
  var CHAR_CODE = /^(Key[A-Z]|Digit[0-9]|Numpad[0-9]|Minus|Equal|Bracket(Left|Right)|Backslash|Semicolon|Quote|Backquote|Comma|Period|Slash|IntlBackslash|IntlRo|IntlYen|Numpad(Decimal|Add|Subtract|Multiply|Divide))$/;

  /** KeyboardEvent.code → 종류. 문자 값은 반환하지 않는다. */
  function classifyCode(code) {
    if (code === "Space") return KIND.SPACE;
    if (code === "Backspace") return KIND.BACKSPACE;
    if (code === "Enter" || code === "NumpadEnter") return KIND.ENTER;
    if (CHAR_CODE.test(code)) return KIND.CHAR;
    return KIND.OTHER;
  }

  /**
   * KeyboardEvent.key → 종류. code 가 비어 오는 입력 경로용 폴백이다.
   * 실물 USB 키보드는 code 를 늘 채우지만, 가상 키보드·원격 입력·자동화 도구는
   * key 만 채워 보내는 경우가 있다. 그때 전부 other 로 떨어지면 지표가 통째로 망가진다.
   * 길이 1 인 key = 출력되는 문자 → char. (pynput 이 KeyCode 를 CHAR 로 보는 것과 같은 기준)
   * 문자 자체는 여기서도 저장·반환하지 않는다.
   */
  function classifyKey(key) {
    if (typeof key !== "string" || !key) return KIND.OTHER;
    if (key === " " || key === "Spacebar") return KIND.SPACE;
    if (key === "Backspace") return KIND.BACKSPACE;
    if (key === "Enter") return KIND.ENTER;
    // 서로게이트 페어(이모지 등)까지 한 글자로 세려면 코드 포인트 길이로 본다
    if (Array.from(key).length === 1) return KIND.CHAR;
    return KIND.OTHER;
  }

  /** KeyboardEvent → 종류. code 를 우선하고 비어 있으면 key 로 떨어진다. */
  function classifyEvent(e) {
    var byCode = classifyCode(e && e.code);
    if (byCode !== KIND.OTHER || (e && e.code)) return byCode;
    return classifyKey(e && e.key);
  }

  function round(v, d) {
    var m = Math.pow(10, d);
    return Math.round(v * m) / m;
  }

  // statistics.fmean / statistics.pstdev (모집단 표준편차) 와 같은 정의.
  function stat(xs) {
    if (!xs.length) return { mean: 0, std: 0 };
    if (xs.length === 1) return { mean: round(xs[0], 2), std: 0 };
    var sum = 0, i;
    for (i = 0; i < xs.length; i++) sum += xs[i];
    var m = sum / xs.length;
    var acc = 0;
    for (i = 0; i < xs.length; i++) acc += (xs[i] - m) * (xs[i] - m);
    return { mean: round(m, 2), std: round(Math.sqrt(acc / xs.length), 2) };
  }

  function create(opts) {
    opts = opts || {};
    var node = opts.node || "pi-keyboard";
    var windowS = opts.windowS || 60;
    var flightGapMaxS = opts.flightGapMaxS || 2.0;   // 이보다 큰 간격은 리듬 통계에서 뺀다
    var idleGapS = opts.idleGapS || 3.0;             // 이 이상 공백이면 idle 로 센다
    var maxEvents = opts.maxEvents || 20000;
    var moveThrottleS = opts.moveThrottleS || 0.05;

    var keys = [];        // {t, kind, down}
    var mice = [];        // {t, kind}
    var held = {};        // kind → true. auto-repeat 억제용, 메모리 전용
    var pressTotal = 0;   // 세션 누적 타건 수 (화면 확인용, 페이로드에는 안 들어감)
    var lastPressT = null;
    var lastKind = null;
    var lastMoveT = -Infinity;

    // 윈도의 두 배까지만 남긴다. 그보다 오래된 이벤트는 어떤 계산에도 쓰이지 않는다.
    function trim(arr, nowS) {
      var cut = nowS - windowS * 2;
      while (arr.length && arr[0].t < cut) arr.shift();
      while (arr.length > maxEvents) arr.shift();
    }

    return {
      KIND: KIND,

      /** 키 누름. 같은 종류가 이미 눌려 있으면 auto-repeat 로 보고 무시한다. */
      press: function (kind, tS) {
        if (held[kind]) return false;
        held[kind] = true;
        keys.push({ t: tS, kind: kind, down: true });
        pressTotal += 1;
        lastPressT = tS;
        lastKind = kind;
        trim(keys, tS);
        return true;
      },

      /** 키 뗌. press 없이 들어온 release 는 dwell 매칭에서 자연스럽게 버려진다. */
      release: function (kind, tS) {
        delete held[kind];
        keys.push({ t: tS, kind: kind, down: false });
        trim(keys, tS);
      },

      /** 마우스 활동. 좌표·버튼은 받지 않는다. move 는 다운샘플한다. */
      mouse: function (kind, tS) {
        if (kind === "move") {
          if (tS - lastMoveT < moveThrottleS) return false;
          lastMoveT = tS;
        }
        mice.push({ t: tS, kind: kind });
        trim(mice, tS);
        return true;
      },

      /** 화면 표시용. 페이로드와 별개다. */
      stats: function (nowS) {
        return {
          pressTotal: pressTotal,
          lastKind: lastKind,
          sinceLastS: lastPressT === null ? null : nowS - lastPressT
        };
      },

      /**
       * [nowS - windowS, nowS] 구간의 특징.
       * 반환 형태는 collector/features.py 의 to_payload() + ts 와 같다.
       */
      extract: function (nowS) {
        var startT = nowS - windowS;
        var inWin = function (e) { return e.t >= startT && e.t <= nowS; };
        var evs = keys.filter(inWin);
        var mevs = mice.filter(inWin);

        var p = {
          ts: round(Date.now() / 1000, 3),
          node: node,
          window_s: windowS,
          dwell_mean_ms: 0, dwell_std_ms: 0,
          flight_mean_ms: 0, flight_std_ms: 0,
          idle_ratio: 0, correction_rate: 0,
          typing_active: false, mouse_active: false, input_active: false,
          flight_cv: 0, mouse_event_rate: 0
        };

        // ---------- 마우스 (키보드 유무와 무관하게 계산) ----------
        if (mevs.length) {
          p.mouse_active = true;
          p.mouse_event_rate = round(mevs.length / windowS * 60.0, 1);
        }

        // ---------- 키보드 ----------
        var downs = evs.filter(function (e) { return e.down; });
        if (!downs.length) {
          p.idle_ratio = 1.0;
          p.input_active = p.mouse_active;
          return p;
        }

        p.typing_active = true;
        p.input_active = true;

        var bs = downs.filter(function (e) { return e.kind === KIND.BACKSPACE; }).length;
        p.correction_rate = round(bs / downs.length, 4);

        // dwell: 같은 종류 press → 다음 release 를 FIFO 로 매칭(근사).
        var dwellMs = [];
        var pending = {};
        evs.forEach(function (e) {
          if (e.down) {
            (pending[e.kind] = pending[e.kind] || []).push(e.t);
          } else {
            var q = pending[e.kind];
            if (q && q.length) {
              var dt = (e.t - q.shift()) * 1000.0;
              if (dt > 0 && dt < 2000) dwellMs.push(dt);   // 2초 넘는 건 눌러둔 키로 본다
            }
          }
        });
        var d = stat(dwellMs);
        p.dwell_mean_ms = d.mean;
        p.dwell_std_ms = d.std;

        // flight: 연속 keydown 간격. 멈춤(> flightGapMax)은 리듬 통계에서 뺀다.
        var dts = downs.map(function (e) { return e.t; });
        var flightsMs = [];
        var i, gap;
        for (i = 1; i < dts.length; i++) {
          gap = dts[i] - dts[i - 1];
          if (gap <= flightGapMaxS) flightsMs.push(gap * 1000.0);
        }
        var f = stat(flightsMs);
        p.flight_mean_ms = f.mean;
        p.flight_std_ms = f.std;
        p.flight_cv = f.mean ? round(f.std / f.mean, 3) : 0;

        // idle 비율: 앞뒤 여백 + 입력 사이 idle_gap 초과분
        var idle = Math.max(0, dts[0] - startT) + Math.max(0, nowS - dts[dts.length - 1]);
        for (i = 1; i < dts.length; i++) {
          gap = dts[i] - dts[i - 1];
          if (gap > idleGapS) idle += gap;
        }
        p.idle_ratio = round(Math.min(1.0, idle / windowS), 4);

        return p;
      }
    };
  }

  return { create: create, classifyCode: classifyCode, classifyKey: classifyKey,
           classifyEvent: classifyEvent, KIND: KIND };
});
