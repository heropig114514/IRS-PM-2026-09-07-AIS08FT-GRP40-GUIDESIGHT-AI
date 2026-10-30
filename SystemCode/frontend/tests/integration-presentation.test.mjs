/* Responsibilities: Verify interface-language status and retained detection presentation.
 * Implementation: Real presentation adapter with explicit DOM and scalar metadata fixtures;
 * language changes remain independent of queued speech and stopped source state.
 * Related Modules: integration/catalog.js, integration/assist-mvp.js.
 * Declaration Index: fixture; fixture.element.
 * Variable Index: None. No camera, browser playback, network or algorithm substitution. */
import test from "node:test";
import assert from "node:assert/strict";
import {AssistPresentation} from "../integration/catalog.js";

/** Functionality: Bind a text-only document to the real presentation adapter.
 * Inputs: Initial interface language. Outputs: Document, nodes and display owner.
 * Logic: Track changed writes and child rows without implementing browser event scheduling.
 * Constraints: No speech or source controller is available to the adapter. */
function fixture(language = "zh-CN") {
  /** Functionality: Supply the DOM operations used by presentation. Inputs: None.
   * Outputs: Mutable text/attribute/list fixture. Logic: Count text writes, preserve safe strings.
   * Constraints: No HTML parsing, MutationObserver or device behavior is simulated. */
  function element() {
    let value = "";
    return {attributes: new Map(), children: [], writes: 0,
      get textContent() { return value; },
      set textContent(text) { value = text; this.writes++; },
      setAttribute(key, text) { this.attributes.set(key, text); },
      removeAttribute(key) { this.attributes.delete(key); },
      replaceChildren() { this.children = []; },
      append(child) { this.children.push(child); }};
  }
  const nodes = new Map([["status", element()], ["results", element()]]);
  const doc = {documentElement: {lang: language}, getElementById: id => nodes.get(id), createElement: element};
  return {doc, nodes, display: new AssistPresentation(doc)};
}

test("stopped status and retained risks switch both ways without touching speech or source data", () => {
  const {doc, nodes, display} = fixture();
  const speech = Object.freeze({state: "queued", language: "zh-CN"});
  const boxes = [Object.freeze({label: "person", track_id: 8, distance_m: 2.3,
    direction: "front", risk_level: "critical", ttc_sec: 0.7}),
  Object.freeze({label: "car", track_id: 9, distance_m: 4, direction: "front_left", risk_level: "high", ttc_sec: null})];
  display.setResults("results", boxes);
  display.setText("status", "导航与环境检测已停止。", "Navigation and obstacle detection stopped.");
  assert.match(nodes.get("results").children[0].textContent, /前方 · 极高风险/);
  doc.documentElement.lang = "en"; display.refresh();
  assert.equal(nodes.get("status").textContent, "Navigation and obstacle detection stopped.");
  assert.equal(nodes.get("results").children[0].textContent, "person · ID 8 · 2.3 m · Ahead · Critical risk · TTC 0.7 s");
  assert.match(nodes.get("results").children[1].textContent, /Ahead left · High risk/);
  assert.equal(speech.language, "zh-CN"); assert.equal(speech.state, "queued");
  assert.equal(boxes[0].direction, "front"); assert.equal(boxes[0].risk_level, "critical");
  doc.documentElement.lang = "zh-CN"; display.refresh();
  assert.equal(nodes.get("status").textContent, "导航与环境检测已停止。");
  assert.match(nodes.get("results").children[0].textContent, /前方 · 极高风险/);
});

test("English-first dynamic statuses and unknown result values return to Chinese without stale defaults", () => {
  const {doc, nodes, display} = fixture("en-US");
  nodes.get("status").attributes.set("data-product-copy", "waitingResults");
  display.setText("status", "检测已停止；保留最后画面和识别结果。", "Detection stopped. The last frame and results remain visible.");
  display.setResults("results", [{label: "stairs", track_id: null, distance_m: null,
    direction: "unknown", risk_level: "unknown", ttc_sec: null}]);
  assert.equal(nodes.get("status").attributes.has("data-product-copy"), false);
  assert.match(nodes.get("results").children[0].textContent, /Distance unknown · Direction unknown · Unknown risk/);
  doc.documentElement.lang = "zh-TW"; display.refresh();
  assert.equal(nodes.get("status").textContent, "检测已停止；保留最后画面和识别结果。");
  assert.match(nodes.get("results").children[0].textContent, /距离未知 · 方向未知 · 风险未知/);
  display.setText("status", "警报语音已准备，可以开始检测。", "Risk alert audio is ready. You can start detection.");
  doc.documentElement.lang = "en"; display.refresh();
  assert.equal(nodes.get("status").textContent, "Risk alert audio is ready. You can start detection.");
});

test("display snapshots are bounded and a new session can clear retained rows", () => {
  const {nodes, display} = fixture("en");
  const boxes = Array.from({length: 65}, (_, index) => ({label: "chair", track_id: index + 1,
    distance_m: 3, direction: "front_right", risk_level: "medium", ttc_sec: null}));
  display.setResults("results", boxes); boxes[0].label = "changed";
  display.refresh();
  assert.equal(nodes.get("results").children.length, 50);
  assert.match(nodes.get("results").children[0].textContent, /^chair .*Ahead right · Medium risk$/);
  display.setResults("results", []); display.refresh();
  assert.equal(nodes.get("results").children.length, 0);
});

test("status refresh avoids repeated announcements and never overwrites another controller", () => {
  const {doc, nodes, display} = fixture();
  display.setText("status", "环境检测已停止。", "Obstacle detection stopped.");
  const status = nodes.get("status"), writes = status.writes;
  display.refresh(); display.setText("status", "环境检测已停止。", "Obstacle detection stopped.");
  assert.equal(status.writes, writes);
  status.textContent = "New controller state";
  doc.documentElement.lang = "en"; display.refresh();
  assert.equal(status.textContent, "New controller state");
  assert.throws(() => display.setText("status", null, "invalid"), TypeError);
});
