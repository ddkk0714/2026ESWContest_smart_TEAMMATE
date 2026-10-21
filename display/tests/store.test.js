/* web/js/store.js 검증 — 브라우저 없이 node --test 로 돌린다.
 *   cd display && node --test tests/
 * store.js 는 UMD 라 브라우저(<script>)와 node(require) 양쪽에서 같은 코드가 돈다.
 */
const test = require("node:test");
const assert = require("node:assert");
const { create, SERIES } = require("../web/js/store.js");

function sample(over) {
  return Object.assign({
    ts: 1769000001.0, node: "pc-collector", window_s: 60,
    dwell_mean_ms: 92.4, dwell_std_ms: 21.8,
    flight_mean_ms: 148.2, flight_std_ms: 63.5,
    idle_ratio: 0.18, correction_rate: 0.07,
  }, over || {});
}

const T0 = 1769000000000;   // 고정 시각 — Date.now() 에 의존하지 않는다

test("첫 메시지 전에는 stale 이다", () => {
  const s = create({ historyN: 10, staleAfterMs: 5000 }).snapshot(T0);
  assert.strictEqual(s.keystroke, null);
  assert.strictEqual(s.stale, true);        // 값이 없을 때 '신선함'으로 오인하면 안 된다
  assert.strictEqual(s.ageS, null);
  assert.deepStrictEqual(s.history.ts, []);
});

test("keystroke 를 넣으면 스냅샷과 추이에 반영된다", () => {
  const st = create({ historyN: 10, staleAfterMs: 5000 });
  st.putKeystroke(sample(), T0);
  const s = st.snapshot(T0 + 500);
  assert.strictEqual(s.stale, false);
  assert.strictEqual(s.msgCount, 1);
  assert.strictEqual(s.ageS, 0.5);
  assert.strictEqual(s.keystroke.flight_mean_ms, 148.2);
  assert.deepStrictEqual(s.history.flight_mean_ms, [148.2]);
});

test("stale 경계를 넘기면 끊김으로 표시된다", () => {
  const st = create({ historyN: 10, staleAfterMs: 5000 });
  st.putKeystroke(sample(), T0);
  assert.strictEqual(st.snapshot(T0 + 4999).stale, false);
  assert.strictEqual(st.snapshot(T0 + 5001).stale, true);
});

test("additive 필드가 없으면 0 이 아니라 null 이다", () => {
  // 규약 확정 필드만 오는 페이로드도 그래프를 깨뜨리지 않아야 한다
  const st = create({ historyN: 10, staleAfterMs: 5000 });
  st.putKeystroke(sample(), T0);            // flight_cv · mouse_event_rate 없음
  const h = st.snapshot(T0).history;
  assert.deepStrictEqual(h.flight_cv, [null]);        // 0 으로 채우면 '리듬 완벽'으로 잘못 보인다
  assert.deepStrictEqual(h.mouse_event_rate, [null]);
  assert.deepStrictEqual(Object.keys(h).sort(), ["ts", ...SERIES].sort());
});

test("추이 링버퍼는 historyN 을 넘지 않는다", () => {
  const st = create({ historyN: 3, staleAfterMs: 5000 });
  for (let i = 0; i < 10; i++) st.putKeystroke(sample({ flight_mean_ms: i }), T0 + i);
  const h = st.snapshot(T0 + 10).history;
  assert.deepStrictEqual(h.flight_mean_ms, [7, 8, 9]);
  assert.strictEqual(h.ts.length, 3);
});

test("잘못된 페이로드는 무시한다", () => {
  const st = create({ historyN: 10, staleAfterMs: 5000 });
  [null, undefined, "문자열", 42, [1, 2, 3]].forEach((bad) => st.putKeystroke(bad, T0));
  const s = st.snapshot(T0);
  assert.strictEqual(s.keystroke, null);    // 죽지도, 쓰레기를 담지도 않는다
  assert.strictEqual(s.msgCount, 0);
});

test("phase 와 health 는 각자 자리에 담긴다", () => {
  const st = create({ historyN: 10, staleAfterMs: 5000 });
  st.putPhase({ phase: "fatigue", confidence: 0.81 });
  st.putHealth("esp32-a", { status: "online" });
  const s = st.snapshot(T0);
  assert.strictEqual(s.phase.phase, "fatigue");
  assert.strictEqual(s.health["esp32-a"].status, "online");
});
