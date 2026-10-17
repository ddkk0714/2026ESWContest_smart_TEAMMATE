/* 보드 키보드 캡처 로직 검증 — 실제 키 입력 없이 합성 이벤트로만 돌린다.
 * collector/tests/test_features.py 와 같은 시나리오를 써서 두 노드가 같은 값을 내는지 본다.
 *   node --test tests/keycap.test.js
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const Keycap = require("../web/js/keycap.js");

const KIND = Keycap.KIND;

/** n 타를 flight 간격, dwell 유지로 밀어넣는다. */
function typing(cap, { start = 0, n = 20, flight = 0.15, dwell = 0.09, kind = KIND.CHAR } = {}) {
  let t = start;
  for (let i = 0; i < n; i++) {
    cap.press(kind, t);
    cap.release(kind, t + dwell);
    t += flight;
  }
  return cap;
}

const fresh = (opts) => Keycap.create(Object.assign({ windowS: 60 }, opts));

test("입력이 없으면 idle 로 본다", () => {
  const p = fresh().extract(100);
  assert.strictEqual(p.typing_active, false);
  assert.strictEqual(p.input_active, false);
  assert.strictEqual(p.idle_ratio, 1.0);
});

test("일정한 타이핑의 dwell·flight·cv", () => {
  const p = typing(fresh(), { start: 41, n: 30, flight: 0.15, dwell: 0.09 }).extract(60);
  assert.strictEqual(p.typing_active, true);
  assert.ok(p.dwell_mean_ms >= 80 && p.dwell_mean_ms <= 100, `dwell=${p.dwell_mean_ms}`);
  assert.ok(p.flight_mean_ms >= 140 && p.flight_mean_ms <= 160, `flight=${p.flight_mean_ms}`);
  assert.ok(p.flight_cv < 0.1, `cv=${p.flight_cv}`);
});

test("불규칙한 타이핑은 cv 가 더 크다", () => {
  const irr = fresh();
  let t = 41;
  for (let r = 0; r < 3; r++) {
    for (const fl of [0.1, 0.4, 0.12, 0.5, 0.09, 0.6, 0.11, 0.45]) {
      irr.press(KIND.CHAR, t);
      irr.release(KIND.CHAR, t + 0.08);
      t += fl;
    }
  }
  const steady = typing(fresh(), { start: 41, n: 24, flight: 0.15, dwell: 0.08 });
  assert.ok(irr.extract(60).flight_cv > steady.extract(60).flight_cv);
});

test("백스페이스 비율이 correction_rate 가 된다", () => {
  const cap = fresh();
  typing(cap, { start: 41, n: 10, dwell: 0.08 });
  typing(cap, { start: 43, n: 5, dwell: 0.08, kind: KIND.BACKSPACE });
  assert.ok(Math.abs(cap.extract(60).correction_rate - 5 / 15) < 0.01);
});

test("윈도 끝에 몰린 짧은 입력은 idle_ratio 가 높다", () => {
  const p = typing(fresh(), { start: 55, n: 5, dwell: 0.08 }).extract(60);
  assert.ok(p.idle_ratio > 0.8, `idle=${p.idle_ratio}`);
});

test("키 입력 없이 마우스만 있어도 활동으로 잡는다", () => {
  const cap = fresh();
  for (let i = 0; i < 30; i++) cap.mouse("move", 41 + i * 0.5);
  cap.mouse("click", 42);
  const p = cap.extract(60);
  assert.strictEqual(p.typing_active, false);
  assert.strictEqual(p.mouse_active, true);
  assert.strictEqual(p.input_active, true);
  assert.ok(p.mouse_event_rate > 0);
});

test("키를 꾹 눌러도 한 타로 센다", () => {
  const cap = fresh();
  for (let i = 0; i < 50; i++) cap.press(KIND.OTHER, 10 + i * 0.03);
  assert.strictEqual(cap.stats(12).pressTotal, 1);
  cap.release(KIND.OTHER, 12);
  cap.press(KIND.OTHER, 12.5);
  assert.strictEqual(cap.stats(13).pressTotal, 2);
});

test("move 이벤트는 다운샘플한다", () => {
  const cap = fresh({ moveThrottleS: 0.05 });
  for (let i = 0; i < 100; i++) cap.mouse("move", 30 + i * 0.001);   // 1ms 간격
  const rate = cap.extract(60).mouse_event_rate;
  assert.ok(rate > 0 && rate < 100 / 60 * 60, `rate=${rate}`);
});

test("페이로드가 MQTT 규약 필드를 지킨다", () => {
  const p = typing(fresh({ node: "pi-keyboard" }), { start: 41, n: 20 }).extract(60);
  for (const key of ["ts", "node", "window_s", "dwell_mean_ms", "dwell_std_ms",
                     "flight_mean_ms", "flight_std_ms", "idle_ratio", "correction_rate"]) {
    assert.ok(key in p, `규약 필드 누락: ${key}`);
  }
  assert.strictEqual(p.node, "pi-keyboard");
  assert.strictEqual(p.window_s, 60);
});

test("classifyCode 는 종류만 돌려주고 문자 값을 흘리지 않는다", () => {
  assert.strictEqual(Keycap.classifyCode("KeyA"), KIND.CHAR);
  assert.strictEqual(Keycap.classifyCode("Digit1"), KIND.CHAR);
  assert.strictEqual(Keycap.classifyCode("Comma"), KIND.CHAR);
  assert.strictEqual(Keycap.classifyCode("Space"), KIND.SPACE);
  assert.strictEqual(Keycap.classifyCode("Backspace"), KIND.BACKSPACE);
  assert.strictEqual(Keycap.classifyCode("NumpadEnter"), KIND.ENTER);
  assert.strictEqual(Keycap.classifyCode("ShiftLeft"), KIND.OTHER);
  assert.strictEqual(Keycap.classifyCode("F5"), KIND.OTHER);
  // 다섯 종류 밖의 값이 나오면 안 된다
  const kinds = Object.values(KIND);
  ["KeyZ", "Escape", "ArrowUp", "MetaLeft", "이상한값"].forEach((c) => {
    assert.ok(kinds.includes(Keycap.classifyCode(c)), c);
  });
});

test("code 가 비어 오면 key 로 종류를 정한다", () => {
  // 실물 USB 키보드는 code 를 채우지만, 가상 키보드·원격 입력은 key 만 보내기도 한다.
  // 그때 전부 other 로 떨어지면 correction_rate 가 통째로 0 이 되어 버린다.
  const ev = (code, key) => Keycap.classifyEvent({ code, key });
  assert.strictEqual(ev("", "a"), KIND.CHAR);
  assert.strictEqual(ev("", "가"), KIND.CHAR);
  assert.strictEqual(ev("", " "), KIND.SPACE);
  assert.strictEqual(ev("", "Backspace"), KIND.BACKSPACE);
  assert.strictEqual(ev("", "Enter"), KIND.ENTER);
  assert.strictEqual(ev("", "Shift"), KIND.OTHER);
  assert.strictEqual(ev("", ""), KIND.OTHER);
  assert.strictEqual(ev(undefined, undefined), KIND.OTHER);
  // code 가 있으면 code 가 이긴다 — key 가 IME 때문에 "Process" 로 와도 흔들리지 않는다
  assert.strictEqual(ev("KeyA", "Process"), KIND.CHAR);
  assert.strictEqual(ev("ShiftLeft", "Shift"), KIND.OTHER);
});

test("오래된 이벤트는 윈도 밖으로 빠진다", () => {
  const cap = typing(fresh(), { start: 5, n: 20, dwell: 0.08 });
  assert.strictEqual(cap.extract(10).typing_active, true);
  assert.strictEqual(cap.extract(200).typing_active, false);   // 60초 윈도 밖
});
