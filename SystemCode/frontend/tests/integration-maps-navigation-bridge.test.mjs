/* Responsibilities: Verify maps-state speech bridging without creating navigation/device work.
 * Implementation: Independent EventTarget-style surfaces, adapter observations and deterministic
 * real WalkingNavigation/NavigationSpeechAdapter integration over controlled GPS and speech.
 * Related Modules: integration/maps-navigation-bridge.js, integration/navigation-speech.js,
 * navigation.js, navigation-ui.js. Browser capture and physical GPS/audio need separate validation.
 * Declaration Index: Surface; Surface.constructor; Surface.addEventListener; Surface.removeEventListener;
 * Surface.dispatch; Surface.count; AdapterSpy; AdapterSpy.constructor; AdapterSpy.begin;
 * AdapterSpy.ingest; AdapterSpy.stop; Clock; Clock.constructor; Clock.now; Clock.setTimer;
 * Clock.clearTimer; Clock.advance; Speech; Speech.constructor; Speech.subscribe; Speech.emit;
 * Speech.describe; Speech.voiceList; Speech.speak; Speech.end; Speech.stopOwned;
 * snapshot; fixture; fixture.publish; realFixture; realFixture.geo.watchPosition;
 * realFixture.geo.clearWatch; realFixture.fix;
 * testInitial; testEdges; testRestart; testInvalidation; testArrival; testError; testExplicitStop;
 * testVisibility; testPageHide; testInitialActive; testSeparateTargets; testNativeEventTarget; testDispose;
 * testConstructionFailure; testRealController; testCoreStop; testCoreError; testFailedCancellation.
 * Variable Index: None. Each fixture owns synthetic event/device state and finite clocks;
 * no network, permissions, service, actual audio or stored position data is involved.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {MapsNavigationSpeechBridge} from "../integration/maps-navigation-bridge.js";
import {NavigationSpeechAdapter} from "../integration/navigation-speech.js";
import {WalkingNavigation} from "../navigation.js";

/* Functionality: Model explicit EventTarget registration and capture-before-bubble dispatch.
 * Logic: Snapshot listener entries before dispatch, separating capture from ordinary listeners.
 * Constraints: Unit lifecycle fixture, not an operating-system visibility or browser-phase proof. */
class Surface {
  /* Functionality: Initialize a visible event surface. Inputs: None. Outputs: Isolated target.
   * Logic: Store finite listener registrations. Constraints: No native document or permissions. */
  constructor() { this.listeners = []; this.visibilityState = "visible"; }
  /* Functionality: Register an EventTarget-style listener. Inputs: Type/callback/capture.
   * Outputs: Registration. Logic: Keep exact identity and phase. Constraints: No implicit dispatch. */
  addEventListener(type, callback, capture = false) { this.listeners.push({type, callback, capture: capture === true}); }
  /* Functionality: Remove matching registration. Inputs: Type/callback/capture.
   * Outputs: Listener cleanup. Logic: Exact match. Constraints: Other observers remain installed. */
  removeEventListener(type, callback, capture = false) {
    this.listeners = this.listeners.filter(item => item.type !== type || item.callback !== callback || item.capture !== (capture === true));
  }
  /* Functionality: Deliver one explicit fixture event. Inputs: Type and optional detail.
   * Outputs: Listener effects. Logic: Capturing observers precede ordinary observers.
   * Constraints: Does not modify visibility or manufacture any native sensor/output event. */
  dispatch(type, detail) {
    const entries = this.listeners.filter(item => item.type === type);
    for (const item of [...entries.filter(item => item.capture), ...entries.filter(item => !item.capture)])
      item.callback({type, detail});
  }
  /* Functionality: Inspect listener ownership. Inputs: Optional type. Outputs: Count.
   * Logic: Count matching registrations. Constraints: No listener or callback contents exposed. */
  count(type) { return this.listeners.filter(item => !type || item.type === type).length; }
}

/* Functionality: Observe bridge calls independently of native speech behavior.
 * Logic: Retain exact snapshot identities to detect mutation/copying and duplicate transitions.
 * Constraints: Spy does not grant permissions, deduplicate inputs or create audio. */
class AdapterSpy {
  /* Functionality: Initialize observations. Inputs: None. Outputs: Empty calls.
   * Logic: Separate lifecycle/source records. Constraints: No output engine. */
  constructor() { this.calls = []; }
  /* Functionality: Record new run. Inputs: ID. Outputs: Call entry.
   * Logic: Append once. Constraints: No hidden stop in this independent spy. */
  begin(id) { this.calls.push({operation: "begin", id}); }
  /* Functionality: Record original source. Inputs: Snapshot. Outputs: Identity-preserving entry.
   * Logic: Append unchanged reference. Constraints: No route/state rewriting. */
  ingest(value) { this.calls.push({operation: "ingest", value}); }
  /* Functionality: Record owned stop request. Inputs: Reason. Outputs: Call entry.
   * Logic: Append invocation. Constraints: No automatic completion or retry. */
  stop(reason) { this.calls.push({operation: "stop", reason}); }
}

/* Functionality: Control source freshness and core deadlines without waiting.
 * Logic: Execute due timers chronologically. Constraints: No measured browser timing. */
class Clock {
  /* Functionality: Initialize epoch clock. Inputs: None. Outputs: Isolated scheduler.
   * Logic: Positive epoch-style timestamps. Constraints: No wall-clock access. */
  constructor() { this.time = 100000; this.sequence = 0; this.timers = new Map(); }
  /* Functionality: Read clock. Inputs: State. Outputs: Milliseconds.
   * Logic: Direct read. Constraints: Time changes only through advance. */
  now = () => this.time;
  /* Functionality: Register one deadline. Inputs: Callback/delay. Outputs: ID.
   * Logic: Store absolute due time. Constraints: No recurring task or retry. */
  setTimer = (callback, delay) => { const id = ++this.sequence; this.timers.set(id, {callback, at: this.time + delay}); return id; };
  /* Functionality: Clear one timer. Inputs: ID. Outputs: Deletion result.
   * Logic: Map deletion. Constraints: Other timers remain untouched. */
  clearTimer = id => this.timers.delete(id);
  /* Functionality: Advance deterministic time. Inputs: Target timestamp. Outputs: Due callbacks.
   * Logic: Remove each deadline before execution. Constraints: Cannot move time backwards. */
  advance(target) {
    assert.ok(target >= this.time);
    while (true) {
      const due = [...this.timers.entries()].filter(([, item]) => item.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.timers.delete(due[0]); this.time = due[1].at; due[1].callback();
    }
    this.time = target;
  }
}

/* Functionality: Supply the real adapter's shared-driver contract without physical audio.
 * Logic: One request, synchronous submission and owned cancellation with explicit failure.
 * Constraints: Unit fixture never asserts audible playback or native engine completion. */
class Speech {
  /* Functionality: Initialize independently enabled output. Inputs: None. Outputs: Fake driver.
   * Logic: Exact local voice and2x controls. Constraints: Bridge cannot change enablement. */
  constructor() {
    this.current = null; this.sequence = 0; this.listeners = new Set(); this.requests = [];
    this.cancelAttempts = []; this.cancelFailure = false;
    this.rate = {value: "2"}; this.volume = {value: "1"};
  }
  /* Functionality: Subscribe to lifecycle. Inputs: Listener. Outputs: Unsubscribe.
   * Logic: Local set. Constraints: No initial event replay. */
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  /* Functionality: Deliver lifecycle metadata. Inputs: Phase and request ID. Outputs: Observer calls.
   * Logic: Snapshot listener set. Constraints: Synthetic driver events only. */
  emit(phase, id = this.current?.id) { for (const listener of [...this.listeners]) listener({channel: "speech", phase, request_id: id}); }
  /* Functionality: Describe admission. Inputs: Current request. Outputs: Shared driver metadata.
   * Logic: Enabled/local fixture assumptions. Constraints: No device support proof. */
  describe() { return {supported: true, enabled: true, visible: true, user_activated: true,
    active_request: Boolean(this.current), engine_pending: Boolean(this.current)}; }
  /* Functionality: Return one explicitly local voice. Inputs: None. Outputs: Inventory.
   * Logic: Stable fixture identity. Constraints: No default or remote voice. */
  voiceList() { return {accepted: true, voices: [{id: "local-zh", language: "zh-CN", local_service: true}]}; }
  /* Functionality: Submit without starting. Inputs: Text/settings. Outputs: Request acknowledgement.
   * Logic: Synchronous submitted event tests adapter ownership binding. Constraints: No queue. */
  speak(text, options) {
    assert.equal(this.current, null);
    this.current = {id: ++this.sequence, text, options}; this.requests.push(this.current); this.emit("submitted");
    return {accepted: true, last_request: {id: this.current.id}};
  }
  /* Functionality: Complete one synthetic request. Inputs: Current request. Outputs: End event.
   * Logic: Free the driver before notification. Constraints: Not actual engine/audio evidence. */
  end() { const id = this.current.id; this.current = null; this.emit("end", id); }
  /* Functionality: Attempt only owned cancellation. Inputs: Request ID. Outputs: Accepted/reason.
   * Logic: Record every attempt and preserve current after failure. Constraints: No hidden retry. */
  stopOwned(id) {
    this.cancelAttempts.push(id);
    if (this.current?.id !== id) return {accepted: false, reason: "not_owned"};
    if (this.cancelFailure) return {accepted: false, reason: "cancel_failed"};
    this.current = null; this.emit("cancel", id); return {accepted: true};
  }
}

/* Functionality: Build independent snapshots for lifecycle-boundary assertions.
 * Inputs: State, activity and optional overrides. Outputs: Fresh snapshot.
 * Logic: Locating starts have null position/progress, matching the production controller.
 * Constraints: No simulated coordinate is treated as a real device location. */
function snapshot(state = "ready", active = false, overrides = {}) {
  return {state, active, reason: null, position: null, progress: null,
    route: {request_id: "same-route"}, ...overrides};
}

/* Functionality: Create an independent bridge observation fixture.
 * Inputs: Initial snapshot and optional constructor overrides. Outputs: Targets/spy/bridge/publish.
 * Logic: Navigation methods throw if the bridge attempts device/controller actions.
 * Constraints: Only describe is permitted; no actual GPS, permissions or network. */
function fixture(initial = snapshot(), overrides = {}) {
  const doc = new Surface(), win = new Surface(), adapter = new AdapterSpy(); let reads = 0;
  const navigation = {describe: () => { reads++; return initial; },
    start: () => { throw new Error("Bridge must not start navigation"); },
    stop: () => { throw new Error("Bridge must not stop navigation"); },
    setRoute: () => { throw new Error("Bridge must not set a route"); }};
  const bridge = new MapsNavigationSpeechBridge({navigation, adapter, document: doc, window: win, ...overrides});
  return {doc, win, adapter, navigation, bridge, get reads() { return reads; },
    // Functionality: Publish one original snapshot. Inputs: State. Outputs: Registered observer effects.
    // Logic: Dispatch existing event name. Constraints: No direct bridge invocation or device action.
    publish(value) { doc.dispatch("prs-navigation-change", value); }};
}

/* Functionality: Connect both production navigation and speech adapters through existing events.
 * Inputs: None. Outputs: Controlled real-core fixture.
 * Logic: Explicit test GPS calls drive an L route; only the controller owns watch acquisition.
 * Constraints: Synthetic GPS/speech remain separate from real browser/device validation. */
function realFixture() {
  const doc = new Surface(), win = new Surface(), clock = new Clock(), speech = new Speech(), watches = [], cleared = [];
  const adapter = new NavigationSpeechAdapter({speech, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer});
  adapter.configure({voiceId: "local-zh", language: "zh-CN"}); adapter.setEnabled(true);
  const geo = {
    // Functionality: Retain native-compatible callbacks. Inputs: Success/error/options. Outputs: Unique ID.
    // Logic: Only the production controller may invoke it. Constraints: No automatic location delivery.
    watchPosition(success, error, options) { watches.push({success, error, options}); return watches.length - 1; },
    // Functionality: Record controller cleanup. Inputs: ID. Outputs: Cancellation record.
    // Logic: Keep callbacks to test old-generation rejection. Constraints: No real native resource.
    clearWatch(id) { cleared.push(id); },
  };
  const navigation = new WalkingNavigation({geo, now: clock.now, schedule: clock.setTimer, cancel: clock.clearTimer,
    isVisible: () => doc.visibilityState === "visible", onChange: value => doc.dispatch("prs-navigation-change", value)});
  navigation.setRoute({provider: "openrouteservice", profile: "foot-walking", request_id: "unchanged-route",
    geometry: {type: "LineString", coordinates: [[0, 0], [0.001, 0], [0.001, 0.001]]},
    summary: {distance_m: 222, duration_s: 180}, steps: [
      {type: 11, instruction: "Head east", way_points: [0, 1], distance_m: 111, duration_s: 90},
      {type: 0, instruction: "Turn left", way_points: [1, 2], distance_m: 111, duration_s: 90},
      {type: 10, instruction: "Arrive", way_points: [2, 2], distance_m: 0, duration_s: 0}]});
  const originalStart = navigation.start, originalStop = navigation.stop, originalSetRoute = navigation.setRoute;
  const bridge = new MapsNavigationSpeechBridge({navigation, adapter, document: doc, window: win});
  return {doc, win, clock, speech, adapter, navigation, bridge, watches, cleared, originalStart, originalStop, originalSetRoute,
    // Functionality: Deliver explicit GPS input to the real core. Inputs: Coordinates/watch index.
    // Outputs: Original state events. Logic: Preserve the test clock source timestamp.
    // Constraints: Does not call adapter or bridge directly or adjust core thresholds.
    fix(longitude, latitude, index = watches.length - 1) {
      watches[index].success({coords: {longitude, latitude, accuracy: 5}, timestamp: clock.now()});
    }};
}

/* Functionality: Separate old terminal initial state from an active session.
 * Inputs: Ready/error/arrived initial snapshots. Outputs: No speech or device operation.
 * Logic: Initial read is once; terminal state alone never creates a run.
 * Constraints: No automatic navigation start. */
test("initial inactive or terminal state never starts speech or reads the core repeatedly", function testInitial() {
  for (const initial of [snapshot(), snapshot("error", false, {reason: "position_stale"}), snapshot("arrived")]) {
    const f = fixture(initial); assert.equal(f.reads, 1); assert.deepEqual(f.adapter.calls, []);
    f.publish(initial); assert.equal(f.reads, 1); assert.equal(f.bridge.describe().run_id, null); f.bridge.dispose();
  }
});

/* Functionality: Bind active lifecycle rather than route identity.
 * Inputs: Locating and repeated navigating snapshots with equal/missing route IDs.
 * Outputs: One run and exact input identity. Logic: Active edge starts once.
 * Constraints: Bridge leaves speech selection/deduplication to its adapter. */
test("locating active edge begins one run and forwards original snapshots unchanged", function testEdges() {
  const f = fixture(), locating = snapshot("locating", true), moving = snapshot("navigating", true);
  f.publish(locating); f.publish(moving); f.publish({...moving, route: {request_id: null}});
  const starts = f.adapter.calls.filter(call => call.operation === "begin");
  assert.equal(starts.length, 1); assert.equal(f.adapter.calls[1].value, locating);
  assert.equal(f.adapter.calls[2].value, moving); assert.equal(f.reads, 1); f.bridge.dispose();
});

/* Functionality: Isolate restarts even when the original route is reused.
 * Inputs: Start, stop and same-route restart. Outputs: Distinct run IDs.
 * Logic: Explicit active edges increment generation. Constraints: No request_id session heuristic. */
test("restarting the same route creates a fresh speech generation", function testRestart() {
  const f = fixture(); f.publish(snapshot("locating", true)); f.publish(snapshot("stopped"));
  f.publish(snapshot("locating", true));
  const starts = f.adapter.calls.filter(call => call.operation === "begin");
  assert.equal(starts.length, 2); assert.notEqual(starts[0].id, starts[1].id);
  assert.equal(f.adapter.calls.filter(call => call.operation === "stop").length, 1); f.bridge.dispose();
});

/* Functionality: Consume route-invalidation boundaries once.
 * Inputs: Existing run then ready/idle/stopped and an obsolete moving snapshot.
 * Outputs: One owned stop and no resurrection. Logic: A fresh locating start rearms the bridge.
 * Constraints: Core state is never changed by the observer. */
test("route invalidation and repeated inactive states stop once and reject old movement", function testInvalidation() {
  for (const state of ["ready", "idle", "stopped"]) {
    const f = fixture(); f.publish(snapshot("locating", true)); f.publish(snapshot(state)); f.publish(snapshot(state));
    const count = f.adapter.calls.length; f.publish(snapshot("navigating", true));
    assert.equal(f.adapter.calls.length, count); assert.equal(f.bridge.describe().run_id, null);
    assert.equal(f.adapter.calls.filter(call => call.operation === "stop").length, 1); f.bridge.dispose();
  }
});

/* Functionality: Preserve the original inactive arrival before clearing its final notice.
 * Inputs: Active run then arrived active=false. Outputs: Arrival forwarded and run retained.
 * Logic: Only a later lifecycle boundary/new start clears the terminal run.
 * Constraints: Bridge cannot infer arrival from position or replay completion. */
test("arrival is forwarded despite active false and retained for its final speech", function testArrival() {
  const f = fixture(); f.publish(snapshot("locating", true)); const id = f.bridge.describe().run_id;
  const arrived = snapshot("arrived"); f.publish(arrived);
  assert.equal(f.adapter.calls.at(-1).value, arrived); assert.equal(f.bridge.describe().run_id, id);
  assert.equal(f.adapter.calls.some(call => call.operation === "stop"), false);
  f.publish(snapshot("locating", true)); assert.notEqual(f.bridge.describe().run_id, id); f.bridge.dispose();
});

/* Functionality: Treat an error from a tracked run differently from an old initial error.
 * Inputs: Active run then terminal error/restart. Outputs: Error notice source and new generation.
 * Logic: Error itself does not create another run. Constraints: No retry or navigation restart. */
test("tracked terminal error is forwarded once per event without beginning a new run", function testError() {
  const f = fixture(); f.publish(snapshot("locating", true));
  const error = snapshot("error", false, {reason: "permission_denied"}); f.publish(error); f.publish(error);
  assert.equal(f.adapter.calls.filter(call => call.operation === "begin").length, 1);
  assert.equal(f.adapter.calls.at(-1).value, error); f.publish(snapshot("locating", true));
  assert.equal(f.adapter.calls.filter(call => call.operation === "begin").length, 2); f.bridge.dispose();
});

/* Functionality: Avoid duplicate cleanup after a captured user stop.
 * Inputs: Explicit speech stop followed by the core stopped event and another explicit stop.
 * Outputs: One attempt per explicit user action. Logic: Clear bridge ownership before core events.
 * Constraints: Core controls remain caller-owned. */
test("explicit stop plus the matching core event never retries cancellation implicitly", function testExplicitStop() {
  const f = fixture(); f.publish(snapshot("locating", true)); f.bridge.stop("navigation_stop");
  f.publish(snapshot("stopped")); f.publish(snapshot("stopped"));
  assert.equal(f.adapter.calls.filter(call => call.operation === "stop").length, 1);
  f.bridge.stop("navigation_stop"); assert.equal(f.adapter.calls.filter(call => call.operation === "stop").length, 2);
  f.bridge.dispose(); assert.equal(f.adapter.calls.filter(call => call.operation === "stop").length, 2);
});

/* Functionality: Exercise capture cleanup before already registered ordinary handlers.
 * Inputs: Hidden/visible lifecycle and late old movement. Outputs: Ordered single stop/no restart.
 * Logic: Original core/driver remain ordinary observers; fresh locating is the only rearm.
 * Constraints: Fixture phase ordering does not validate actual OS background behavior. */
test("capture hide stops owned speech first and late events cannot revive it", function testVisibility() {
  const doc = new Surface(), win = new Surface(), adapter = new AdapterSpy(), order = [];
  doc.addEventListener("visibilitychange", () => { order.push("core"); doc.dispatch("prs-navigation-change", snapshot("stopped")); });
  doc.addEventListener("visibilitychange", () => order.push("driver"));
  const bridge = new MapsNavigationSpeechBridge({adapter, document: doc, window: win, initialSnapshot: () => snapshot()});
  doc.dispatch("prs-navigation-change", snapshot("locating", true));
  const originalStop = adapter.stop.bind(adapter);
  adapter.stop = reason => { order.push("bridge"); originalStop(reason); };
  doc.visibilityState = "hidden"; doc.dispatch("visibilitychange"); win.dispatch("pagehide");
  assert.deepEqual(order, ["bridge", "core", "driver"]);
  assert.equal(adapter.calls.filter(call => call.operation === "stop").length, 1);
  const count = adapter.calls.length; doc.dispatch("prs-navigation-change", snapshot("navigating", true));
  doc.visibilityState = "visible"; doc.dispatch("visibilitychange");
  doc.dispatch("prs-navigation-change", snapshot("navigating", true)); assert.equal(adapter.calls.length, count);
  doc.dispatch("prs-navigation-change", snapshot("locating", true));
  assert.equal(adapter.calls.filter(call => call.operation === "begin").length, 2); bridge.dispose();
});

/* Functionality: Cover page leave without a preceding visibility event.
 * Inputs: Active run and repeated pagehide. Outputs: One stop and blocked run.
 * Logic: Lifecycle callbacks share consumed ownership. Constraints: No background resume. */
test("pagehide alone invalidates the run once", function testPageHide() {
  const f = fixture(); f.publish(snapshot("locating", true)); f.win.dispatch("pagehide"); f.win.dispatch("pagehide");
  assert.equal(f.adapter.calls.filter(call => call.operation === "stop").length, 1);
  assert.equal(f.bridge.describe().blocked_until_start, true); f.bridge.dispose();
});

/* Functionality: Attach after navigation already became active without creating another watch.
 * Inputs: Initial active navigating snapshot. Outputs: One new speech run/input.
 * Logic: Initial read acts as the first observed active edge. Constraints: No polling or core start. */
test("an initially active controller attaches directly without waiting for a new route", function testInitialActive() {
  const value = snapshot("navigating", true), f = fixture(value);
  assert.equal(f.adapter.calls.filter(call => call.operation === "begin").length, 1);
  assert.equal(f.adapter.calls.at(-1).value, value); assert.equal(f.reads, 1); f.bridge.dispose();
});

/* Functionality: Permit event/source injection independently of document controls.
 * Inputs: Separate EventTarget and initial snapshot reader. Outputs: Correct subscription boundary.
 * Logic: Events drive source updates; document only drives visibility. Constraints: No DOM assumptions. */
test("separate event and initial snapshot sources are injectable", function testSeparateTargets() {
  const doc = new Surface(), win = new Surface(), events = new Surface(), adapter = new AdapterSpy(); let reads = 0;
  const bridge = new MapsNavigationSpeechBridge({adapter, document: doc, window: win, events,
    initialSnapshot: () => { reads++; return snapshot(); }, runIdFactory: generation => `explicit-${generation}`});
  doc.dispatch("prs-navigation-change", snapshot("locating", true)); assert.deepEqual(adapter.calls, []);
  events.dispatch("prs-navigation-change", snapshot("locating", true));
  assert.equal(adapter.calls[0].id, "explicit-1"); assert.equal(reads, 1); bridge.dispose();
});

/* Functionality: Verify compatibility with the standard EventTarget interface itself.
 * Inputs: Native Node EventTarget and explicitly detailed event. Outputs: One forwarded start.
 * Logic: Register/dispatch/remove through the standard API, independent of the phase fixture.
 * Constraints: Node EventTarget is not a browser DOM capture-order or hardware validation. */
test("standard EventTarget events attach and detach without a document singleton", function testNativeEventTarget() {
  const events = new EventTarget(), adapter = new AdapterSpy();
  const bridge = new MapsNavigationSpeechBridge({adapter, events, initialSnapshot: () => snapshot()});
  const event = new Event("prs-navigation-change");
  Object.defineProperty(event, "detail", {value: snapshot("locating", true)});
  events.dispatchEvent(event); assert.equal(adapter.calls.filter(call => call.operation === "begin").length, 1);
  bridge.dispose(); const count = adapter.calls.length;
  events.dispatchEvent(event); assert.equal(adapter.calls.length, count);
});

/* Functionality: Release exactly this bridge's listeners and own run.
 * Inputs: Active bridge, dispose twice and late events. Outputs: Complete listener cleanup/no new calls.
 * Logic: Caller-owned adapter/core stay reusable. Constraints: No GPS or adapter disposal. */
test("dispose removes listeners and ignores every later event and direct input", function testDispose() {
  const f = fixture(); f.publish(snapshot("locating", true)); f.bridge.dispose(); f.bridge.dispose();
  assert.equal(f.doc.count(), 0); assert.equal(f.win.count(), 0);
  const count = f.adapter.calls.length;
  f.publish(snapshot("locating", true)); f.bridge.ingest(snapshot("locating", true)); f.bridge.stop();
  assert.equal(f.adapter.calls.length, count); assert.equal(f.bridge.describe().disposed, true);
});

/* Functionality: Avoid leaked listeners when initial source acquisition fails.
 * Inputs: Throwing initial read. Outputs: Propagated error and zero bridge registrations.
 * Logic: Constructor removes exact registrations. Constraints: No fallback or read retry. */
test("failed initial read cleans registrations without retrying or fallback", function testConstructionFailure() {
  const doc = new Surface(), win = new Surface(); let reads = 0;
  assert.throws(() => new MapsNavigationSpeechBridge({adapter: new AdapterSpy(), document: doc, window: win,
    initialSnapshot: () => { reads++; throw new Error("source failed"); }}), /source failed/);
  assert.equal(reads, 1); assert.equal(doc.count(), 0); assert.equal(win.count(), 0);
});

/* Functionality: Verify actual core-to-adapter data flow rather than only spy call order.
 * Inputs: User/MCP-equivalent core starts, same-step GPS, turn, arrival and same-route restart.
 * Outputs: Deduplicated speech, inactive arrival notice and exactly one watch per caller start.
 * Logic: Both production classes remain unchanged. Constraints: Synthetic GPS/output, no audible proof. */
test("real navigation and speech adapters share one caller-owned watch across turn and arrival", function testRealController() {
  const f = realFixture(); assert.equal(f.watches.length, 0); assert.equal(f.speech.requests.length, 0);
  assert.equal(f.navigation.start, f.originalStart); assert.equal(f.navigation.stop, f.originalStop);
  assert.equal(f.navigation.setRoute, f.originalSetRoute);
  f.navigation.start(); assert.equal(f.watches.length, 1); f.fix(0.0003, 0);
  assert.equal(f.speech.requests.length, 1); assert.match(f.speech.requests[0].text, /米后左转/);
  f.clock.advance(100100); f.fix(0.0005, 0); assert.equal(f.speech.requests.length, 1);
  f.clock.advance(100200); f.fix(0.001, 0.0002); assert.equal(f.speech.requests.length, 2);
  assert.match(f.speech.requests[1].text, /^左转/);
  f.clock.advance(100300); f.fix(0.001, 0.001);
  assert.equal(f.navigation.describe().state, "arrived"); assert.equal(f.navigation.active, false);
  assert.equal(f.speech.requests.at(-1).text, "已到达路线终点。");
  assert.notEqual(f.bridge.describe().run_id, null); assert.deepEqual(f.cleared, [0]);
  const firstRun = f.bridge.describe().run_id; f.speech.end(); f.navigation.start(); f.fix(0, 0);
  assert.notEqual(f.bridge.describe().run_id, firstRun); assert.equal(f.watches.length, 2);
  assert.equal(f.speech.requests.length, 4);
  f.bridge.stop("navigation_stop"); f.navigation.stop(); f.fix(0.001, 0.001, 0);
  assert.equal(f.speech.requests.length, 4); assert.equal(f.watches.length, 2);
  f.bridge.dispose(); f.adapter.dispose(); assert.equal(f.clock.timers.size, 0);
});

/* Functionality: Cover an agent/core stop without a human click-capture handler.
 * Inputs: Direct original controller stop and late prior-watch callback, followed by explicit restart.
 * Outputs: One owned cancellation, no device restart and fresh next-run speech.
 * Logic: The same core method used by MCP publishes the stopped snapshot to the bridge.
 * Constraints: This unit test does not call an MCP server or native geolocation service. */
test("direct core stop cleans speech once without requiring a UI capture click", function testCoreStop() {
  const f = realFixture(); f.navigation.start(); f.fix(0, 0);
  f.navigation.stop("mcp");
  assert.deepEqual(f.speech.cancelAttempts, [1]); assert.equal(f.bridge.describe().run_id, null);
  assert.equal(f.navigation.active, false); assert.equal(f.adapter.describe().active_request_id, null);
  f.fix(0.001, 0.001, 0); assert.equal(f.speech.requests.length, 1); assert.equal(f.watches.length, 1);
  f.navigation.start(); f.fix(0, 0); assert.equal(f.speech.requests.length, 2); assert.equal(f.watches.length, 2);
  f.bridge.stop(); f.navigation.stop(); f.bridge.dispose(); f.adapter.dispose(); assert.equal(f.clock.timers.size, 0);
});

/* Functionality: Preserve the original core's terminal error for independent speech.
 * Inputs: Core start then native-compatible permission-error callback before any fix.
 * Outputs: One error notice, released GPS and no implicit new watch.
 * Logic: Core failure handling owns termination; bridge retains the run for its final notice.
 * Constraints: No real permission prompt, location acquisition or hardware audio is exercised. */
test("real core location error can speak its final notice without a GPS fix", function testCoreError() {
  const f = realFixture(); f.navigation.start(); f.watches[0].error({code: 1});
  assert.equal(f.navigation.describe().state, "error"); assert.equal(f.navigation.active, false);
  assert.equal(f.watches.length, 1); assert.deepEqual(f.cleared, [0]);
  assert.equal(f.speech.requests.length, 1); assert.match(f.speech.requests[0].text, /导航已停止/);
  assert.notEqual(f.bridge.describe().run_id, null);
  f.speech.end(); assert.equal(f.watches.length, 1); assert.equal(f.speech.requests.length, 1);
  f.bridge.stop(); f.bridge.dispose(); f.adapter.dispose(); assert.equal(f.clock.timers.size, 0);
});

/* Functionality: Preserve cancellation failures across the captured stop/core-state boundary.
 * Inputs: Failed owned cancellation, core stopped, lifecycle events and later explicit retry.
 * Outputs: One attempt until explicit retry and adapter pause preserved.
 * Logic: Real adapter retains uncertain ownership while the bridge consumes its run boundary.
 * Constraints: No automatic retry, voice replacement or GPS manipulation. */
test("failed captured stop is not retried by stopped, hidden or pagehide snapshots", function testFailedCancellation() {
  const f = realFixture(); f.navigation.start(); f.fix(0, 0); f.speech.cancelFailure = true;
  f.bridge.stop("navigation_stop"); f.navigation.stop();
  assert.deepEqual(f.speech.cancelAttempts, [1]); assert.equal(f.adapter.describe().paused, true);
  f.doc.visibilityState = "hidden"; f.doc.dispatch("visibilitychange"); f.win.dispatch("pagehide");
  f.adapter.resume(); assert.equal(f.adapter.describe().paused, true); assert.deepEqual(f.speech.cancelAttempts, [1]);
  f.speech.cancelFailure = false; f.bridge.stop("navigation_stop");
  assert.deepEqual(f.speech.cancelAttempts, [1, 1]); assert.equal(f.adapter.describe().active_request_id, null);
  f.adapter.resume(); assert.equal(f.adapter.describe().paused, false);
  f.doc.visibilityState = "visible"; f.navigation.start(); f.fix(0, 0);
  assert.equal(f.speech.requests.length, 2); assert.equal(f.watches.length, 2);
  f.bridge.stop(); f.navigation.stop(); f.bridge.dispose(); f.adapter.dispose();
});
