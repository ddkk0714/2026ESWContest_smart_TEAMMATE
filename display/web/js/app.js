/* DESKMATE display — 데이터 소스 → 화면 갱신.
 *
 * 소스는 세 가지이고 store 에 넣는 페이로드 모양은 모두 같다.
 *   mqtt  : 허브 브로커 구독. 실제 운영 경로.
 *   local : 보드에 꽂힌 키보드를 web-app 이 직접 캡처(js/keycap.js). 브로커·collector 불필요.
 *   demo  : 가짜 데이터. 레이아웃 확인용.
 *
 * ATLAS 보드에는 Python 이 없으므로 브라우저가 직접 브로커에 붙는다.
 * 브라우저는 원시 TCP 를 못 쓰니 1883 이 아니라 WebSocket 리스너(9001)로 간다.
 * 차트는 외부 라이브러리 없이 canvas 로 직접 그린다. (벤더링은 mqtt.js 하나뿐)
 */
"use strict";

var $ = function (id) { return document.getElementById(id); };
var fmt = function (v, d) {
  return (typeof v === "number" && isFinite(v)) ? v.toFixed(d || 0) : "--";
};

/* ---------------- 설정 (URL 쿼리로 덮어쓰기 가능) ---------------- */
var CFG = window.DESKMATE || {};
var Q = new URLSearchParams(location.search);
var BROKER = Q.get("broker") || CFG.brokerUrl;
var DEMO = Q.get("demo") === "1";
var LOCAL = !DEMO && (Q.get("src") || CFG.source || "mqtt").toLowerCase() === "local";

var store = window.DeskmateStore.create({
  historyN: CFG.historyN,
  staleAfterMs: CFG.staleAfterMs
});

/* ---------------- 탭 ---------------- */
document.querySelectorAll(".tab").forEach(function (btn) {
  btn.addEventListener("click", function () {
    document.querySelectorAll(".tab").forEach(function (b) { b.classList.remove("is-on"); });
    document.querySelectorAll(".view").forEach(function (v) { v.classList.remove("is-on"); });
    btn.classList.add("is-on");
    $("view-" + btn.dataset.view).classList.add("is-on");
    if (btn.dataset.view === "trend") drawAll();   // 숨겨진 동안 크기가 0이라 다시 그린다
    // 로컬 캡처 중에 space/enter 가 이 버튼을 다시 누르지 않도록 포커스를 뗀다
    btn.blur();
  });
});

/* ---------------- 시계 ---------------- */
var two = function (n) { return String(n).padStart(2, "0"); };
setInterval(function () {
  var d = new Date();
  $("clock").textContent = two(d.getHours()) + ":" + two(d.getMinutes()) + ":" + two(d.getSeconds());
}, 1000);

/* ---------------- 연결 상태 ---------------- */
var link = { state: "off", text: "브로커 연결 중" };
function setLink(state, text) { link = { state: state, text: text }; }

/* ---------------- 렌더 ---------------- */
var last = null;

function meterClass(el, ratio, warnAt, badAt) {
  el.classList.toggle("warn", ratio >= warnAt && ratio < badAt);
  el.classList.toggle("bad", ratio >= badAt);
}

function render(s) {
  last = s;

  var el = $("link");
  if (DEMO) { el.className = "pill pill-demo"; el.textContent = "DEMO"; }
  else if (LOCAL) { el.className = "pill pill-local"; el.textContent = link.text; }
  else if (link.state === "on") { el.className = "pill pill-on"; el.textContent = link.text; }
  else { el.className = "pill pill-off"; el.textContent = link.text; }

  document.body.classList.toggle("stale", !!s.stale);

  var k = s.keystroke;
  var act = $("activity");
  act.className = "activity";
  if (!k || s.stale) {
    act.classList.add(k ? "act-stale" : "act-idle");
    $("act-label").textContent = k ? "수신 끊김" : "대기 중";
    if (LOCAL) {
      $("act-sub").textContent = k
        ? "캡처가 " + fmt(s.ageS, 0) + "초째 멈춰 있습니다 — 앱을 다시 실행하세요"
        : "보드에 꽂힌 키보드를 눌러 보세요";
    } else {
      $("act-sub").textContent = k
        ? "마지막 수신 " + fmt(s.ageS, 0) + "초 전 — collector 가 켜져 있는지 확인"
        : "collector 를 실행하면 여기에 표시됩니다";
    }
  } else if (k.typing_active) {
    act.classList.add("act-typing");
    $("act-label").textContent = "타이핑 중";
    $("act-sub").textContent = "분당 약 "
      + (k.flight_mean_ms ? fmt(60000 / k.flight_mean_ms, 0) : "--") + " 타";
  } else if (k.mouse_active) {
    act.classList.add("act-mouse");
    $("act-label").textContent = "마우스 작업";
    $("act-sub").textContent = "키 입력 없음 — 읽기·탐색 구간으로 보임";
  } else {
    act.classList.add("act-idle");
    $("act-label").textContent = "자리 비움 / 무입력";
    $("act-sub").textContent = LOCAL
      ? "최근 " + fmt(k.window_s, 0) + "초 윈도에 키 입력이 없습니다"
      : "최근 윈도에 키·마우스 입력이 없습니다";
  }

  $("chip-key").classList.toggle("on", !!(k && k.typing_active && !s.stale));
  $("chip-mouse").classList.toggle("on", !!(k && k.mouse_active && !s.stale));

  if (k) {
    $("m-dwell").textContent = fmt(k.dwell_mean_ms, 0);
    $("m-dwell-sd").textContent = fmt(k.dwell_std_ms, 0);
    $("m-flight").textContent = fmt(k.flight_mean_ms, 0);
    $("m-flight-sd").textContent = fmt(k.flight_std_ms, 0);
    $("m-cv").textContent = fmt(k.flight_cv, 2);
    $("m-idle").textContent = fmt((k.idle_ratio || 0) * 100, 0);
    $("m-corr").textContent = fmt((k.correction_rate || 0) * 100, 1);
    $("m-mouse").textContent = fmt(k.mouse_event_rate, 0);
    $("m-window").textContent = fmt(k.window_s, 0);

    // cv 는 1.0 을 상한으로 본다 (그 이상은 리듬이 거의 무너진 상태)
    var cv = Math.min(1, k.flight_cv || 0);
    $("bar-cv").style.width = (cv * 100) + "%";
    meterClass($("bar-cv"), cv, 0.5, 0.8);

    var idle = Math.min(1, k.idle_ratio || 0);
    $("bar-idle").style.width = (idle * 100) + "%";
    meterClass($("bar-idle"), idle, 0.4, 0.7);

    // 정정률은 0.2(20%)만 되어도 높은 편이라 스케일을 5배로 확대해 보여준다
    var corr = Math.min(1, (k.correction_rate || 0) * 5);
    $("bar-corr").style.width = (corr * 100) + "%";
    meterClass($("bar-corr"), corr, 0.5, 0.8);

    $("src").textContent = "노드 " + (k.node || "--");
    $("raw").textContent = JSON.stringify(k, null, 2);
  }

  $("age").textContent = s.ageS === null ? "수신 --" : "수신 " + fmt(s.ageS, 1) + "초 전";
  $("cnt").textContent = s.msgCount + " 건";
  $("trend-span").textContent = "최근 " + s.history.ts.length + " 개 (1Hz)";

  if ($("view-trend").classList.contains("is-on")) drawAll();
}

/* ---------------- 그래프 ---------------- */
function line(ctx, xs, w, h, color, lo, hi, fill) {
  var pts = [];
  xs.forEach(function (v, i) { if (typeof v === "number" && isFinite(v)) pts.push([i, v]); });
  if (pts.length < 2) return;
  var span = Math.max(1, xs.length - 1);
  var rng = (hi - lo) || 1;
  var X = function (i) { return (i / span) * w; };
  var Y = function (v) { return h - ((Math.min(hi, Math.max(lo, v)) - lo) / rng) * h; };

  var trace = function () {
    ctx.beginPath();
    pts.forEach(function (p, n) {
      if (n) ctx.lineTo(X(p[0]), Y(p[1])); else ctx.moveTo(X(p[0]), Y(p[1]));
    });
  };

  if (fill) {
    trace();
    ctx.lineTo(X(pts[pts.length - 1][0]), h);
    ctx.lineTo(X(pts[0][0]), h);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
  }
  trace();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.stroke();
}

function prep(canvas) {
  var dpr = window.devicePixelRatio || 1;
  var w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return null;
  canvas.width = w * dpr; canvas.height = h * dpr;
  var ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  // 가로 눈금 4등분
  ctx.strokeStyle = "#26313d"; ctx.lineWidth = 1;
  for (var i = 1; i < 4; i++) {
    var y = (h / 4) * i;
    ctx.beginPath(); ctx.moveTo(0, y + 0.5); ctx.lineTo(w, y + 0.5); ctx.stroke();
  }
  return { ctx: ctx, w: w, h: h };
}

function drawAll() {
  if (!last || !last.history) return;
  var H = last.history;

  var a = prep($("c-flight"));
  if (a) {
    var vals = H.flight_mean_ms.filter(function (v) { return typeof v === "number" && v > 0; });
    var hi = Math.max.apply(null, [300].concat(vals)) * 1.1;
    line(a.ctx, H.flight_mean_ms, a.w, a.h, "#4dd6a8", 0, hi, "rgba(77,214,168,.12)");
  }

  var b = prep($("c-idle"));
  if (b) {
    line(b.ctx, H.idle_ratio, b.w, b.h, "#5aa9ff", 0, 1, "rgba(90,169,255,.10)");
    // 정정률은 0~0.2 구간을 화면 전체로 펴서 idle 과 같이 읽히게 한다
    line(b.ctx, H.correction_rate, b.w, b.h, "#f2b544", 0, 0.2, null);
  }
}

window.addEventListener("resize", function () { if (last) drawAll(); });

/* ---------------- 데이터 소스 ---------------- */
function parse(buf) {
  try {
    var text = (typeof buf === "string") ? buf : new TextDecoder("utf-8").decode(buf);
    var v = JSON.parse(text);
    return (v && typeof v === "object" && !Array.isArray(v)) ? v : null;
  } catch (e) {
    return null;
  }
}

function connectMqtt() {
  $("raw-topic").textContent = CFG.topics.keystroke + "   @ " + BROKER;

  var client = mqtt.connect(BROKER, {
    clientId: "pi-display-" + Math.random().toString(16).slice(2, 8),
    reconnectPeriod: 2000,     // 상시 표시 단말이라 지수 백오프보다 고정 2초가 낫다
    connectTimeout: 8000,
    clean: true
  });

  client.on("connect", function () {
    setLink("on", "브로커 연결");
    client.subscribe([CFG.topics.keystroke, CFG.topics.phase, CFG.topics.health]);
  });
  client.on("reconnect", function () { setLink("off", "재연결 중"); });
  client.on("close", function () { setLink("off", "브로커 끊김"); });
  client.on("error", function (e) {
    setLink("off", "브로커 오류");
    console.error("[mqtt]", e && e.message);
  });

  client.on("message", function (topic, buf) {
    var p = parse(buf);
    if (!p) return;
    if (topic === CFG.topics.keystroke) store.putKeystroke(p, Date.now());
    else if (topic === CFG.topics.phase) store.putPhase(p);
    else if (topic.indexOf("deskmate/health/") === 0) store.putHealth(topic.split("/").pop(), p);
  });
}

/* 보드에 직접 꽂힌 키보드를 캡처한다.
 * 브로커도 PC collector 도 없이 "키 입력이 앱까지 오는가 + 특징이 제대로 뽑히는가" 를 본다.
 * 계산은 js/keycap.js 가 하고 여기서는 DOM 이벤트를 '종류' 로 바꿔 넘기기만 한다.
 * event.key(실제 문자)는 읽지 않는다. — display/README.md 프라이버시 항목
 */
function startLocalCapture() {
  var CAP = CFG.capture || {};
  var cap = window.DeskmateKeycap.create({
    node: CAP.node,
    windowS: CAP.windowS,
    flightGapMaxS: CAP.flightGapMaxS,
    idleGapS: CAP.idleGapS
  });

  // 벽시계(Date.now)는 NTP 보정으로 튈 수 있다. 간격 계산은 단조 시계로 한다.
  var nowS = function () { return performance.now() / 1000; };

  $("capbar").hidden = false;
  $("raw-topic").textContent = CFG.topics.keystroke + "   (로컬 캡처 — 브로커 미사용)";
  setLink("on", "보드 키보드 " + (CAP.node || ""));

  var KIND_KO = { char: "문자", space: "스페이스", backspace: "백스페이스",
                  enter: "엔터", other: "기타" };

  function hit(kind) {
    var dot = $("cap-dot");
    dot.classList.remove("beat");
    void dot.offsetWidth;            // 리플로우를 강제해야 연타에서도 애니메이션이 다시 돈다
    dot.classList.add("beat");
    $("cap-last").textContent = "마지막 입력 " + (KIND_KO[kind] || kind);
  }

  // capture 단계에서 받는다. 버튼 같은 요소가 이벤트를 먼저 삼켜도 놓치지 않는다.
  window.addEventListener("keydown", function (e) {
    if (e.repeat) return;                              // 꾹 누름은 한 타
    var kind = window.DeskmateKeycap.classifyEvent(e);
    cap.press(kind, nowS());
    hit(kind);
    // 보드는 키오스크라 뒤로가기·새로고침·스크롤로 화면이 날아가면 복구가 번거롭다
    if (e.code === "Space" || e.code === "Backspace" || e.code === "Tab" ||
        e.code === "F5" || e.code.indexOf("Arrow") === 0) e.preventDefault();
  }, true);

  window.addEventListener("keyup", function (e) {
    cap.release(window.DeskmateKeycap.classifyEvent(e), nowS());
  }, true);

  // 마우스가 없는 보드도 있으므로 있으면 쓰고 없으면 그냥 0 이다. 좌표·버튼은 보지 않는다.
  window.addEventListener("pointermove", function () { cap.mouse("move", nowS()); }, true);
  window.addEventListener("pointerdown", function () { cap.mouse("click", nowS()); }, true);
  window.addEventListener("wheel", function () { cap.mouse("scroll", nowS()); }, { passive: true, capture: true });

  // 창이 포커스를 잃으면 눌린 키의 keyup 을 못 받는다. 눌림 상태를 정리해 dwell 오염을 막는다.
  window.addEventListener("blur", function () {
    var t = nowS();
    ["char", "space", "backspace", "enter", "other"].forEach(function (k) { cap.release(k, t); });
  });

  setInterval(function () {
    store.putKeystroke(cap.extract(nowS()), Date.now());
    var st = cap.stats(nowS());
    $("cap-count").textContent = st.pressTotal;
  }, CAP.periodMs || 1000);
}

if (DEMO) {
  $("raw-topic").textContent = CFG.topics.keystroke + "   (DEMO — 브로커 미사용)";
  window.DeskmateDemo.start(function (p) { store.putKeystroke(p, Date.now()); }, 1000);
} else if (LOCAL) {
  startLocalCapture();
} else {
  connectMqtt();
}

// 메시지가 안 와도 '수신 n초 전' 이 흘러야 하므로 화면 갱신은 독립적으로 돈다
setInterval(function () { render(store.snapshot(Date.now())); }, 200);
render(store.snapshot(Date.now()));
