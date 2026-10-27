/* Responsibilities: Verify synthetic navigation bundle validation and source-owned GPS replay.
 * Implementation: Deterministic timers drive the production WalkingNavigation through the bundled
 * route, low accuracy, deviation, recovery and arrival; late/canceled timers remain callable.
 * Related Modules: ../integration/navigation-replay.js, ../integration/navigation-sample.json,
 * ../navigation.js, ../maps-client.js.
 * Declaration Index: clockFixture; clockFixture.now; clockFixture.setTimer; clockFixture.clearTimer;
 * clockFixture.runTo; clockFixture.deliverLate; replayFixture; replayFixture.onEvent;
 * testBundle; testBundleInvalid; testFixesInvalid; testBounds; testReplayTiming; testDelayedSource;
 * testStopGeneration; testStopObserver; testReplace; testDispose; testNavigation;
 * testStaleCore; testEarlyStop.
 * Variable Index: SAMPLE — bundled synthetic JSON, cloned per test; isolated fixtures own every
 * clock, timer and callback. No browser, GPS, ORS, network or actual speech is exercised.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {ReplayGeolocation, validateNavigationReplay} from "../integration/navigation-replay.js";
import {WalkingNavigation} from "../navigation.js";

const SAMPLE = JSON.parse(await readFile(new URL("../integration/navigation-sample.json", import.meta.url), "utf8"));

/* Functionality: Retain timer ownership and expose deterministic delivery, including late callbacks.
 * Inputs: None. Outputs: clock and native-style scheduling methods.
 * Logic: Ordered due times share one clock; canceled callbacks remain in history for explicit tests.
 * Constraints: No wall-clock scheduler or real hardware timing is verified. */
function clockFixture() {
  let time = 100000, sequence = 0;
  const pending = new Map(), history = new Map();
  return {pending, history,
    // Functionality: Read source time; Inputs: None; Outputs: milliseconds.
    // Logic: Fixture-owned value; Constraints: no system clock access.
    now() { return time; },
    // Functionality: Schedule controlled callback; Inputs: callback/delay; Outputs: handle.
    // Logic: Save original deadline; Constraints: zero-delay callbacks are still asynchronous.
    setTimer(callback, delay) {
      const id = ++sequence, entry = {id, callback, due: time + delay};
      pending.set(id, entry); history.set(id, entry); return id;
    },
    // Functionality: Cancel active timer; Inputs: handle; Outputs: removal boolean.
    // Logic: Keep historical callback; Constraints: history is never replayed automatically.
    clearTimer(id) { return pending.delete(id); },
    // Functionality: Deliver all due callbacks in time order; Inputs: target absolute time.
    // Outputs: callback effects. Logic: Re-evaluate pending map after each callback.
    // Constraints: A canceled timer cannot fire unless a test explicitly requests late delivery.
    runTo(target) {
      assert.ok(target >= time);
      while (true) {
        const next = [...pending.values()].filter(item => item.due <= target)
          .sort((a, b) => a.due - b.due || a.id - b.id)[0];
        if (!next) break;
        pending.delete(next.id); time = next.due; next.callback();
      }
      time = target;
    },
    // Functionality: Deliver one retained callback late; Inputs: timer handle and absolute time.
    // Outputs: callback effects. Logic: Preserve original callback/deadline while advancing now.
    // Constraints: Explicit synthetic scheduler fault injection, never a replay retry.
    deliverLate(id, target) { time = target; pending.delete(id); history.get(id).callback(); },
  };
}

/* Functionality: Create an idle replay with private scheduler and metadata event capture.
 * Inputs: Optional source fixes. Outputs: source adapter, clock and recorded events.
 * Logic: Inject every platform dependency; source copying uses the production setter.
 * Constraints: No global browser API, timers or network are touched. */
function replayFixture(fixes = SAMPLE.fixes) {
  const clock = clockFixture(), events = [];
  const replay = new ReplayGeolocation({...clock,
    // Functionality: Capture event metadata; Inputs: event; Outputs: array append.
    // Logic: Observer stores emitted snapshot; Constraints: no coordinate fields are added.
    onEvent(event) { events.push(event); },
  });
  replay.setReplay(fixes);
  return {clock, replay, events};
}

/* Functionality: Assert valid imports are detached from all caller-owned nested values.
 * Inputs: Bundled route/fixes. Outputs: validation and mutation isolation assertions.
 * Logic: Mutate both source and returned objects independently. Constraints: No real route claim. */
function testBundle() {
  const input = structuredClone(SAMPLE), actual = validateNavigationReplay(input);
  assert.deepEqual(actual, input);
  input.route.geometry.coordinates[0][0] = 42;
  input.route.steps[0].instruction = "Changed caller";
  input.fixes[0].longitude = 41;
  assert.equal(actual.route.geometry.coordinates[0][0], 0);
  assert.equal(actual.route.steps[0].instruction, SAMPLE.route.steps[0].instruction);
  assert.equal(actual.fixes[0].longitude, 0);
  actual.fixes[1].accuracy_m = 99;
  assert.equal(input.fixes[1].accuracy_m, 5);
  assert.match(actual.description, /非真实道路/);
}
test("navigation bundle preserves explicit synthetic provenance and deep copies", testBundle);

/* Functionality: Reject ambiguous provenance and invalid existing route contracts.
 * Inputs: Deliberately malformed bundles. Outputs: explicit rejection assertions.
 * Logic: Vary each independent boundary. Constraints: No automatic metadata or route repair. */
function testBundleInvalid() {
  for (const patch of [{schema_version: 2}, {source: "gps"}, {label: " "}, {description: ""}])
    assert.throws(() => validateNavigationReplay({...structuredClone(SAMPLE), ...patch}), /合成导航/);
  assert.throws(() => validateNavigationReplay(null), /合成导航/);
  const invalid = structuredClone(SAMPLE); invalid.route.provider = "unknown";
  assert.throws(() => validateNavigationReplay(invalid), /路线格式/);
  for (const key of ["request_id", "attribution"]) {
    const bundle = structuredClone(SAMPLE); bundle.route[key] = "";
    assert.throws(() => validateNavigationReplay(bundle), /来源说明/);
  }
}
test("navigation bundle rejects invalid provenance and route metadata", testBundleInvalid);

/* Functionality: Reject time, coordinate and accuracy corruption before scheduling.
 * Inputs: Independent invalid fix fields. Outputs: explicit validation failures.
 * Logic: Check finite native domains and first/strictly-increasing offsets.
 * Constraints: Numeric strings are never coerced and missing values are never zero-filled. */
function testFixesInvalid() {
  for (const patch of [{offset_ms: 1}, {offset_ms: -1}, {offset_ms: 0.1}, {offset_ms: Infinity},
    {offset_ms: 2147483648}, {longitude: 181}, {longitude: NaN}, {longitude: "0"},
    {latitude: -91}, {latitude: Infinity}, {accuracy_m: -1}, {accuracy_m: "5"}, {accuracy_m: NaN}]) {
    const input = structuredClone(SAMPLE); Object.assign(input.fixes[0], patch);
    assert.throws(() => validateNavigationReplay(input), /定位点/);
  }
  for (const value of [0, -1, 1000]) {
    const input = structuredClone(SAMPLE);
    input.fixes[1].offset_ms = value; input.fixes[2].offset_ms = value;
    assert.throws(() => validateNavigationReplay(input), /定位点/);
  }
}
test("navigation replay rejects invalid source times and positioning values", testFixesInvalid);

/* Functionality: Enforce source cardinality limits without changing valid coordinate bounds.
 * Inputs: Empty/oversized sources and exact longitude/latitude bounds.
 * Outputs: bounded import assertions. Logic: Exercise each independent capacity boundary.
 * Constraints: File bytes remain the page file-loader responsibility. */
function testBounds() {
  for (const fixes of [[], Array.from({length: 2001}, (_, index) => ({...SAMPLE.fixes[0], offset_ms: index}))])
    assert.throws(() => validateNavigationReplay({...structuredClone(SAMPLE), fixes}), /2000/);
  const oversized = structuredClone(SAMPLE);
  oversized.route.geometry.coordinates = Array.from({length: 5001}, () => [0, 0]);
  assert.throws(() => validateNavigationReplay(oversized), /5000/);
  const boundary = structuredClone(SAMPLE);
  Object.assign(boundary.fixes[0], {longitude: 180, latitude: -90, accuracy_m: 0});
  assert.equal(validateNavigationReplay(boundary).fixes[0].longitude, 180);
}
test("navigation replay enforces route and fix capacities", testBounds);

/* Functionality: Verify asynchronous start, exact ordering and one-pass exhaustion.
 * Inputs: Two source fixes with separate offsets. Outputs: source timestamps and lifecycle assertions.
 * Logic: Advance deterministic deadlines and retain active watch until owner stops it.
 * Constraints: Exhaustion is not declared as arrival and never loops. */
function testReplayTiming() {
  const f = replayFixture(SAMPLE.fixes.slice(0, 2)), delivered = [];
  const id = f.replay.watchPosition(fix => delivered.push(fix));
  assert.equal(delivered.length, 0);
  assert.equal(f.replay.describe().active, true);
  f.clock.runTo(100000);
  f.clock.runTo(104000);
  assert.deepEqual(delivered.map(fix => fix.timestamp), [100000, 104000]);
  assert.deepEqual(delivered.map(fix => fix.coords.longitude), [0, 0.00045]);
  f.clock.runTo(200000);
  assert.equal(delivered.length, 2);
  assert.equal(f.events.filter(event => event.phase === "exhausted").length, 1);
  assert.equal(f.replay.describe().index, 2);
  assert.equal(f.replay.describe().active, true);
  assert.equal(JSON.stringify(f.events).includes("longitude"), false);
  assert.equal(JSON.stringify(f.replay.describe()).includes("latitude"), false);
  f.replay.clearWatch(id);
  assert.equal(f.replay.describe().active, false);
}
test("GPS replay starts asynchronously and exhausts once without looping", testReplayTiming);

/* Functionality: Preserve captured source time when a scheduler callback is delayed.
 * Inputs: A deliberately late first fix. Outputs: old source timestamp and actual delivery metadata.
 * Logic: Move clock without changing scheduled source timestamp.
 * Constraints: A slow timer cannot make stale GPS appear newly acquired. */
function testDelayedSource() {
  const f = replayFixture(SAMPLE.fixes.slice(0, 1)), fixes = [];
  f.replay.watchPosition(fix => fixes.push(fix));
  const [timer] = f.clock.pending.keys();
  f.clock.deliverLate(timer, 120000);
  assert.equal(fixes[0].timestamp, 100000);
  assert.deepEqual(f.events.find(event => event.phase === "fix"), {
    phase: "fix", watch_id: 1, index: 1, scheduled_at: 100000, delivered_at: 120000,
  });
}
test("delayed timers retain source timestamps rather than refreshing GPS age", testDelayedSource);

/* Functionality: Keep old or cleared callbacks from affecting a restarted watch.
 * Inputs: Old retained timers, new unique handle and old cancellation handle.
 * Outputs: isolation and complete owned-timer cleanup assertions.
 * Logic: Explicitly deliver old callbacks after restarting. Constraints: No real native GPS calls. */
function testStopGeneration() {
  const f = replayFixture(SAMPLE.fixes.slice(0, 2)), fixes = [];
  const first = f.replay.watchPosition(fix => fixes.push(fix));
  const oldTimers = [...f.clock.pending.keys()];
  f.replay.clearWatch(first);
  assert.equal(f.clock.pending.size, 0);
  const second = f.replay.watchPosition(fix => fixes.push(fix));
  assert.notEqual(first, second);
  f.replay.clearWatch(first);
  for (const timer of oldTimers) f.clock.history.get(timer).callback();
  assert.equal(fixes.length, 0);
  assert.equal(f.replay.describe().watch_id, second);
  f.clock.runTo(104000);
  assert.equal(fixes.length, 2);
  f.replay.clearWatch(second);
  assert.equal(f.clock.pending.size, 0);
}
test("cleared callbacks and older handles cannot affect a new GPS replay", testStopGeneration);

/* Functionality: Respect cancellation performed synchronously by a delivery observer.
 * Inputs: Event observer that stops its own watch. Outputs: no subsequent success callback.
 * Logic: Ownership is rechecked after event dispatch. Constraints: Observer never acquires GPS. */
function testStopObserver() {
  const clock = clockFixture(); let count = 0, replay;
  replay = new ReplayGeolocation({...clock, onEvent: event => {
    if (event.phase === "fix") replay.clearWatch(event.watch_id);
  }});
  replay.setReplay(SAMPLE.fixes);
  replay.watchPosition(() => count++);
  clock.runTo(100000);
  assert.equal(count, 0);
  assert.equal(clock.pending.size, 0);
}
test("delivery observer cancellation cannot leak a pending location callback", testStopObserver);

/* Functionality: Preserve configured source on rejected replacement and copy accepted inputs.
 * Inputs: Mutated caller fixes and active replacement attempt. Outputs: immutable source assertions.
 * Logic: Caller changes after setReplay cannot alter scheduled positions.
 * Constraints: No implicit replacement of an active source. */
function testReplace() {
  const input = structuredClone(SAMPLE.fixes.slice(0, 1)), f = replayFixture(input), delivered = [];
  input[0].longitude = 33;
  const id = f.replay.watchPosition(fix => delivered.push(fix));
  assert.throws(() => f.replay.setReplay(SAMPLE.fixes), /先停止/);
  assert.throws(() => f.replay.watchPosition(() => {}), /已运行/);
  f.clock.runTo(100000);
  assert.equal(delivered[0].coords.longitude, 0);
  f.replay.clearWatch(id);
  assert.throws(() => f.replay.setReplay([]), /2000/);
  assert.equal(f.replay.describe().count, 1);
  assert.throws(() => f.replay.watchPosition(null), /回调/);
}
test("GPS replay owns copied source and requires explicit stop before replacement", testReplace);

/* Functionality: Verify terminal cleanup and absence of implicit restart after disposal.
 * Inputs: Active configured replay and retained late callbacks.
 * Outputs: disposed state, zero timers and explicit start/setter rejection.
 * Logic: Dispose twice then invoke all retained callbacks. Constraints: No external resources. */
function testDispose() {
  const f = replayFixture(); let delivered = 0;
  f.replay.watchPosition(() => delivered++);
  f.replay.dispose(); f.replay.dispose();
  for (const entry of f.clock.history.values()) entry.callback();
  assert.equal(delivered, 0);
  assert.equal(f.clock.pending.size, 0);
  assert.deepEqual(f.replay.describe(), {mode: "replay", configured: false, count: 0,
    index: 0, active: false, watch_id: null, disposed: true});
  assert.throws(() => f.replay.watchPosition(() => {}), /已释放/);
  assert.throws(() => f.replay.setReplay(SAMPLE.fixes), /已释放/);
}
test("disposing GPS replay is terminal and cancels all source timers", testDispose);

/* Functionality: Exercise bundled replay against the unchanged navigation algorithm.
 * Inputs: Production route/controller and deterministic synthetic positioning.
 * Outputs: exact quality, turn, deviation, recovery, arrival and cleanup state assertions.
 * Logic: Drive source times and read public controller snapshots.
 * Constraints: Synthetic source is not real GPS, street navigation, ORS or audible output evidence. */
function testNavigation() {
  const bundle = validateNavigationReplay(SAMPLE), f = replayFixture(bundle.fixes), snapshots = [];
  const nav = new WalkingNavigation({geo: f.replay, now: f.clock.now, schedule: f.clock.setTimer,
    cancel: f.clock.clearTimer, isVisible: () => true, onChange: state => snapshots.push(state)});
  nav.setRoute(bundle.route);
  assert.equal(nav.start().state, "locating");
  assert.notEqual(nav.watchId, null);
  const actual = [];
  for (const fix of bundle.fixes) {
    f.clock.runTo(100000 + fix.offset_ms);
    const state = nav.describe();
    actual.push([fix.offset_ms, state.state, state.reason, state.progress?.step_index ?? null]);
  }
  assert.deepEqual(actual, [
    [0, "navigating", null, 0], [4000, "navigating", null, 0], [8000, "navigating", null, 1],
    [12000, "locating", "accuracy_insufficient", null], [16000, "navigating", null, 1],
    [20000, "off_route", "off_route", null], [24000, "navigating", null, 1],
    [28000, "navigating", null, 2], [32000, "navigating", null, 2], [36000, "arrived", null, 3],
  ]);
  assert.ok(snapshots.find(state => state.state === "off_route").progress.cross_track_m > 40);
  assert.equal(nav.active, false);
  assert.equal(nav.watchId, null);
  assert.equal(f.replay.describe().active, false);
  assert.equal(f.clock.pending.size, 0);
  f.clock.runTo(200000);
  assert.equal(nav.describe().state, "arrived");
}
test("local replay drives navigation turns, quality loss, deviation, recovery and arrival", testNavigation);

/* Functionality: Keep the unchanged navigation freshness watchdog authoritative after exhaustion.
 * Inputs: Replay ending at route start. Outputs: position_stale failure and source cleanup.
 * Logic: Advance15s after the only source fix. Constraints: No last-position refresh or completion fallback. */
function testStaleCore() {
  const f = replayFixture(SAMPLE.fixes.slice(0, 1));
  const nav = new WalkingNavigation({geo: f.replay, now: f.clock.now, schedule: f.clock.setTimer,
    cancel: f.clock.clearTimer, isVisible: () => true});
  nav.setRoute(SAMPLE.route); nav.start(); f.clock.runTo(100000);
  assert.equal(nav.describe().state, "navigating");
  f.clock.runTo(115000);
  assert.equal(nav.describe().reason, "position_stale");
  assert.equal(nav.describe().state, "error");
  assert.equal(f.clock.pending.size, 0);
  assert.equal(f.replay.describe().active, false);
}
test("exhausted replay cannot bypass the existing stale GPS watchdog", testStaleCore);

/* Functionality: Verify explicit navigation stop cancels every replay input before later offsets.
 * Inputs: Real controller start/stop and old retained source callbacks.
 * Outputs: stopped state with no new accepted positions or timers.
 * Logic: Stop after first fix then forcibly deliver old callbacks.
 * Constraints: No hidden loop, seek, physical GPS or automatic restart. */
function testEarlyStop() {
  const f = replayFixture();
  const nav = new WalkingNavigation({geo: f.replay, now: f.clock.now, schedule: f.clock.setTimer,
    cancel: f.clock.clearTimer, isVisible: () => true});
  nav.setRoute(SAMPLE.route); nav.start(); f.clock.runTo(100000);
  const timestamp = nav.describe().position.timestamp;
  assert.equal(nav.stop().accepted, true);
  for (const entry of f.clock.history.values()) entry.callback();
  assert.equal(nav.describe().state, "stopped");
  assert.equal(nav.describe().position.timestamp, timestamp);
  assert.equal(f.clock.pending.size, 0);
}
test("explicit navigation stop prevents all later synthetic GPS fixes", testEarlyStop);
