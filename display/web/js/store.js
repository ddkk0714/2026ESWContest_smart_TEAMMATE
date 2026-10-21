/* DESKMATE display — 수신 상태 저장소.
 *
 * 파이썬(Flask+SSE) 쪽에 있던 링버퍼·신선도 판정을 브라우저로 옮긴 것이다.
 * ATLAS 보드에는 Python 런타임이 없으므로 web-app 이 이 역할을 직접 진다.
 *
 * 브라우저에서는 <script> 로 읽어 window.DeskmateStore 를 쓰고,
 * node --test 에서는 require() 로 같은 코드를 검증한다. (UMD)
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.DeskmateStore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // 화면에 그릴 추이 계열. docs/mqtt-topics.md 규약 필드 + collector additive 필드.
  var SERIES = ["dwell_mean_ms", "flight_mean_ms", "flight_cv",
                "idle_ratio", "correction_rate", "mouse_event_rate"];

  function createStore(opts) {
    opts = opts || {};
    var historyN = opts.historyN || 300;          // 1Hz → 5분
    var staleAfterMs = opts.staleAfterMs || 5000;

    var keystroke = null;
    var rxAtMs = 0;
    var phase = null;
    var health = {};
    var msgCount = 0;
    var hist = { ts: [] };
    SERIES.forEach(function (k) { hist[k] = []; });

    function push(arr, v) {
      arr.push(v);
      if (arr.length > historyN) arr.shift();
    }

    function num(v) {
      // 없는 필드를 0 으로 채우면 '리듬 완벽 / 마우스 정지' 로 잘못 읽힌다. null 로 둔다.
      return typeof v === "number" && isFinite(v) ? v : null;
    }

    // 배열도 typeof 는 "object" 라 따로 걸러야 한다. JSON 최상위가 배열인 메시지 방어.
    function isPlainObject(v) {
      return !!v && typeof v === "object" && !Array.isArray(v);
    }

    return {
      SERIES: SERIES,

      putKeystroke: function (payload, nowMs) {
        if (!isPlainObject(payload)) return;
        keystroke = payload;
        rxAtMs = nowMs;
        msgCount += 1;
        push(hist.ts, num(payload.ts));
        SERIES.forEach(function (k) { push(hist[k], num(payload[k])); });
      },

      putPhase: function (payload) { if (isPlainObject(payload)) phase = payload; },

      putHealth: function (node, payload) {
        if (node && isPlainObject(payload)) health[node] = payload;
      },

      snapshot: function (nowMs) {
        var ageMs = rxAtMs ? nowMs - rxAtMs : null;
        var out = {
          keystroke: keystroke,
          phase: phase,
          health: health,
          msgCount: msgCount,
          ageS: ageMs === null ? null : ageMs / 1000,
          // 값이 아예 없을 때를 '신선함' 으로 오인하면 안 된다
          stale: ageMs === null || ageMs > staleAfterMs,
          history: { ts: hist.ts.slice() }
        };
        SERIES.forEach(function (k) { out.history[k] = hist[k].slice(); });
        return out;
      }
    };
  }

  return { create: createStore, SERIES: SERIES };
});
