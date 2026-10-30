/* Responsibilities: Keep product preview and speech recovery independent of diagnostic controls.
 * Implementation: Execute the shared controller with explicit DOM/media/worker fixtures.
 * Related Modules: integration/live-latency.js; live-camera.html; live-latency.html.
 * Declaration Index: fixture; fixture.Element; fixture.Element.constructor; fixture.Element.addEventListener;
 * fixture.Element.setAttribute; fixture.Element.click; fixture.Element.play; fixture.Element.pause;
 * fixture.Motion; fixture.Motion.constructor; fixture.Motion.start; fixture.Motion.stop;
 * fixture.Motion.ingest; fixture.Motion.describe; fixture.Overlay; fixture.Overlay.show;
 * fixture.Overlay.clear; fixture.Overlay.clearTracking; fixture.Observer; fixture.Observer.constructor;
 * fixture.Observer.observe.
 * Variable Index: source is the real controller with dependency imports supplied by each fixture. */
import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source = readFileSync(new URL("../integration/live-latency.js", import.meta.url), "utf8")
  .replace(/^import .+ from .+;\r?\n/gm, "");

/** Functionality: Exercise shared view ownership with or without diagnostics. Inputs: diagnostic flag.
 * Outputs: DOM, event dispatcher and counters. Logic: isolate browser dependencies while running the
 * production controller. Constraints: no actual media, inference, speech, timers or network. */
function fixture(diagnostics = false) {
  const counts = {starts: 0, stops: 0, seeds: 0, draws: 0, speech: 0, riskReads: 0};
  const listeners = new Map(), observers = [], nodes = new Map();
  /** Functionality: Supply only the DOM operations used by the view. Logic: retain events and values.
   * Constraints: no HTML parser, real audio or camera. */
  class Element {
    /** Functionality: Initialize an element. Inputs: none. Outputs: mutable fixture.
     * Logic: explicit visible/control state. Constraints: no rendering claim. */
    constructor() { this.hidden = false; this.checked = true; this.disabled = false; this.textContent = ""; this.events = {}; }
    /** Functionality: Retain a handler. Inputs: event/callback. Outputs: stored callback.
     * Logic: one handler per event. Constraints: fixture event dispatch only. */
    addEventListener(name, callback) { this.events[name] = callback; }
    /** Functionality: Accept ARIA state. Inputs: key/value. Outputs: fixture attribute.
     * Logic: assign directly. Constraints: no browser accessibility claim. */
    setAttribute(key, value) { this[key] = value; }
    /** Functionality: Invoke a control. Inputs: none. Outputs: callback result.
     * Logic: user-action fixture. Constraints: no hardware output. */
    click() { this.events.click?.(); }
    /** Functionality: Accept preview playback. Inputs: none. Outputs: fulfilled Promise.
     * Logic: deterministic fixture. Constraints: no decoded video. */
    play() { return Promise.resolve(); }
    /** Functionality: Accept preview stop. Inputs: none. Outputs: none.
     * Logic: browser adapter fixture. Constraints: no hardware state. */
    pause() {}
  }
  for (const id of ["echo", "live-speech-controls", "live-camera-preview", "live-camera-figure",
    "live-camera-overlay", "live-camera-age", "live-speech-test", "assist-speech-start",
    "live-speech-feedback", "assist-speech-status", ...(diagnostics ? ["live-latency-values",
      "live-latency-export", "live-motion-enabled", "live-motion-status", "live-box-counts"] : [])]) nodes.set(id, new Element());
  const result = {hidden: false, before() {}, setAttribute() {}};
  nodes.get("echo").closest = () => result;
  nodes.get("assist-speech-start").events.click = () => counts.speech++;
  const root = {lang: "en", dataset: {cameraDisplay: "live-overlay", riskMaxAgeMs: "2000"}};
  /** Functionality: Track worker ownership. Logic: count starts/stops/seeds. Constraints: no optical flow. */
  class Motion {
    /** Functionality: Initialize worker state. Inputs: none. Outputs: stopped tracker.
     * Logic: deterministic counters. Constraints: no worker process. */
    constructor() { this.stats = {state: "stopped"}; this.worker = null; }
    /** Functionality: Start tracking. Inputs: none. Outputs: running state.
     * Logic: record ownership. Constraints: no media. */
    start() { counts.starts++; this.worker = {}; this.stats.state = "running"; }
    /** Functionality: Stop tracking. Inputs: none. Outputs: cleared worker.
     * Logic: record cleanup. Constraints: no process termination. */
    stop() { counts.stops++; this.worker = null; this.stats.state = "stopped"; }
    /** Functionality: Accept a source seed. Inputs: event. Outputs: count.
     * Logic: observe admission. Constraints: no inference substitute. */
    ingest() { counts.seeds++; }
    /** Functionality: Return scalar state. Inputs: none. Outputs: statistics.
     * Logic: current fixture state. Constraints: no target data. */
    describe() { return this.stats; }
  }
  /** Functionality: Observe visual routing. Logic: count draws and clears. Constraints: geometry tested separately. */
  class Overlay {
    /** Functionality: Observe a frame. Inputs: event. Outputs: count.
     * Logic: record display call. Constraints: no canvas rendering. */
    show() { counts.draws++; }
    /** Functionality: Clear visual state. Inputs: none. Outputs: none.
     * Logic: independent fixture. Constraints: no media state. */
    clear() {}
    /** Functionality: Clear estimates. Inputs: none. Outputs: none.
     * Logic: independent fixture. Constraints: no inference state. */
    clearTracking() {}
  }
  /** Functionality: Retain language/control observers. Logic: explicit delivery. Constraints: no native DOM. */
  class Observer {
    /** Functionality: Retain callback. Inputs: callback. Outputs: observer.
     * Logic: no immediate execution. Constraints: no automatic mutation loop. */
    constructor(callback) { this.callback = callback; }
    /** Functionality: Retain observed target. Inputs: target. Outputs: stored observer.
     * Logic: allow language switch delivery. Constraints: no live DOM. */
    observe(target) { observers.push({target, callback: this.callback}); }
  }
  const camera = {stream: {}, image: {}, video: new Element(), subscribe(callback) { listeners.set("camera", callback); }};
  runInNewContext(source, {document: {documentElement: root, getElementById: id => nodes.get(id) ?? null},
    cameraApp: camera, LocalMotion: Motion, LiveCameraOverlay: Overlay, MutationObserver: Observer,
    guidanceManager: {subscribe(callback) { listeners.set("speech", callback); }, riskSpeechStatus() { counts.riskReads++; return {}; }},
    speechActivation: {describe: () => ({ready: true})}, console, performance: {now: () => 100},
    setTimeout: () => 1, clearTimeout() {}, URL, Blob});
  return {counts, nodes, root, observers, emit: event => listeners.get("camera")(event)};
}

test("product preview and recovery work without diagnostic DOM or timing samples", () => {
  const f = fixture();
  f.emit({type: "session", sessionId: "camera"});
  assert.equal(f.counts.starts, 1);
  f.emit({type: "frame", sessionId: "camera", result: {metadata: {boxes: []}}});
  assert.equal(f.counts.draws, 1); assert.equal(f.counts.seeds, 1); assert.equal(f.counts.riskReads, 0);
  f.nodes.get("live-speech-test").click(); assert.equal(f.counts.speech, 1);
  f.emit({type: "stop"});
  assert.equal(f.nodes.get("live-camera-preview").srcObject, null);
  assert.equal(f.nodes.get("live-camera-figure").hidden, true);
  f.emit({type: "frame", sessionId: "camera"}); assert.equal(f.counts.draws, 1);
  f.emit({type: "session", sessionId: "next"}); assert.equal(f.counts.starts, 2);
});

test("diagnostic language changes refresh text without starting another stream", () => {
  const f = fixture(true);
  assert.match(f.nodes.get("live-latency-values").textContent, /frames recorded/);
  f.root.lang = "zh-CN";
  f.observers.filter(item => item.target === f.root).forEach(item => item.callback());
  assert.match(f.nodes.get("live-latency-values").textContent, /已统计/);
  f.root.lang = "en";
  f.observers.filter(item => item.target === f.root).forEach(item => item.callback());
  assert.doesNotMatch(f.nodes.get("live-latency-values").textContent, /[\u3400-\u9fff]/);
  assert.equal(f.counts.starts, 0); assert.equal(f.counts.speech, 0);
});
