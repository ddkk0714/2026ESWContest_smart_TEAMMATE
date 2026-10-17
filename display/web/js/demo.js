/* --demo 모드: 브로커 없이 가짜 키스트로크를 1Hz 로 밀어넣는다.
 * 레이아웃·터치를 보드나 브로커 없이 확인하려고 둔 것. 실제 배선과 무관하다.
 * 페이로드 스키마는 collector/features.py 의 to_payload() 와 같다.
 */
(function (root) {
  "use strict";

  function payload(t) {
    var cycle = (Math.sin(t / 40) + 1) / 2;          // 0=집중, 1=피로
    var typing = Math.random() > 0.15 * (1 + cycle);
    var flightMean = 140 + 130 * cycle + (Math.random() - 0.5) * 24;
    var flightStd = 45 + 70 * cycle + (Math.random() - 0.5) * 16;
    var r = function (v, d) { var m = Math.pow(10, d); return Math.round(v * m) / m; };
    return {
      ts: Date.now() / 1000,
      node: "pc-collector",
      window_s: 60,
      dwell_mean_ms: r(88 + 22 * cycle + (Math.random() - 0.5) * 8, 2),
      dwell_std_ms: r(18 + 14 * cycle, 2),
      flight_mean_ms: typing ? r(flightMean, 2) : 0,
      flight_std_ms: typing ? r(flightStd, 2) : 0,
      idle_ratio: typing ? r(Math.min(1, 0.10 + 0.55 * cycle + Math.random() * 0.08), 4) : 1,
      correction_rate: typing ? r(0.03 + 0.11 * cycle + Math.random() * 0.02, 4) : 0,
      typing_active: typing,
      mouse_active: Math.random() > 0.4,
      input_active: true,
      flight_cv: typing && flightMean ? r(flightStd / flightMean, 3) : 0,
      mouse_event_rate: r(Math.random() * 90, 1)
    };
  }

  root.DeskmateDemo = {
    start: function (onPayload, periodMs) {
      var t0 = Date.now();
      var tick = function () { onPayload(payload((Date.now() - t0) / 1000)); };
      tick();
      return setInterval(tick, periodMs || 1000);
    }
  };
})(window);
