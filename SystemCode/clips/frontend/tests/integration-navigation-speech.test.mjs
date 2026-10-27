/** Responsibilities: Independently verify navigation speech content, freshness, ownership and lifecycle.
 * Implementation: Controlled clock, GPS and speech boundaries exercise public integration APIs without sensors or audio.
 * Related Modules: integration/navigation-speech.js, navigation.js, speech-output.js.
 * Declaration Index: Clock; Clock.constructor; Clock.now; Clock.setTimer; Clock.clearTimer; Clock.advance;
 * Speech; Speech.constructor; Speech.subscribe; Speech.emit; Speech.describe; Speech.voiceList; Speech.speak;
 * Speech.start; Speech.end; Speech.error; Speech.manual; Speech.stopOwned; snapshot; fixture; fixture.ingest;
 * testTextSemantics; testLabels; testEnglish; testEndpoint; testTextStates; testDefaultOff; testStepDedup;
 * testStepRollback; testStatusTransitions; testArrival; testInvalidFix; testQueuedExpiry; testRunningExpiry;
 * testManualLatest; testManualExpiry; testVoiceAdmission; testRate; testSynchronousStart; testSynchronousError;
 * testRefusalResume; testAsynchronousError; testUserCancellation; testStopRestart; testDisable; testDispose;
 * testRealNavigation; testRealNavigation.geo.watchPosition; testRealNavigation.geo.clearWatch; testDiagnostics;
 * testErrorNotice; testStartedLatestFreshness; testCancellationFailure; testExternalCancellationFailure;
 * testNativePrerequisites; testSilentLifecycle.
 * Variable Index: VOICES is an explicitly synthetic local/remote inventory; ZH_LABELS is an independent product expectation;
 * fixture request text stays in memory for assertions and is never written to production logs.
 * Constraints: No actual GPS, permissions, ORS, browser speech engine, sound hardware or network is validated.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {NavigationSpeechAdapter, navigationSpeechText} from "../integration/navigation-speech.js";
import {WalkingNavigation, NAVIGATION_POLICY} from "../navigation.js";

const VOICES = [{id: "local-zh", language: "zh-CN", local_service: true},
  {id: "local-en", language: "en-US", local_service: true},
  {id: "remote-zh", language: "zh-CN", local_service: false}];
const ZH_LABELS = ["左转", "右转", "急左转", "急右转", "稍向左", "稍向右", "直行", "进入环岛",
  "离开环岛", "掉头", "到达终点", "出发", "靠左", "靠右"];

/** Functionality: Model source deadlines independently of real time. Logic: Run due callbacks in order.
 * Constraints: Does not measure browser timing or latency. */
class Clock {
  /** Functionality: Initialize deterministic epoch. Inputs: None. Outputs: Isolated state.
   * Logic: Use positive wall-clock-style milliseconds; Constraints: advance is explicit. */
  constructor() { this.time = 100000; this.sequence = 0; this.timers = new Map(); }
  /** Functionality: Read time. Inputs: State. Outputs: Milliseconds.
   * Logic: Direct lookup; Constraints: no system clock dependency. */
  now = () => this.time;
  /** Functionality: Schedule a deadline. Inputs: Callback/delay. Outputs: Timer ID.
   * Logic: Store absolute time; Constraints: no interval or implicit retry. */
  setTimer = (callback, delay) => {
    const id = ++this.sequence; this.timers.set(id, {callback, at: this.time + delay}); return id;
  };
  /** Functionality: Remove a deadline. Inputs: ID. Outputs: Deletion result.
   * Logic: Map deletion; Constraints: absent IDs are harmless. */
  clearTimer = id => this.timers.delete(id);
  /** Functionality: Move time forward. Inputs: Target milliseconds. Outputs: Due effects.
   * Logic: Delete each callback before execution; Constraints: no backward time. */
  advance(target) {
    assert.ok(target >= this.time);
    while (true) {
      const due = [...this.timers.entries()].filter(([, value]) => value.at <= target)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.timers.delete(due[0]); this.time = due[1].at; due[1].callback();
    }
    this.time = target;
  }
}

/** Functionality: Model one speech controller and independent native events.
 * Logic: Submitted, started and finished are separate observations; Constraints: no voice synthesis. */
class Speech {
  /** Functionality: Initialize enabled output fixture. Inputs: Clock. Outputs: State.
   * Logic: Native prerequisites are independently mutable; Constraints: adapter still defaults disabled. */
  constructor(clock) {
    Object.assign(this, {clock, current: null, sequence: 0, requests: [], cancels: [], listeners: new Set(),
      voices: [...VOICES], enabled: true, supported: true, visible: true, voiceAccepted: true,
      rate: {value: "2"}, volume: {value: "1"}, synchronous: null, refusal: null, lastRequest: null,
      cancelFailure: false, cancelAttempts: []});
  }
  /** Functionality: Subscribe. Inputs: Listener. Outputs: Unsubscribe.
   * Logic: Local set; Constraints: no implicit event delivery. */
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  /** Functionality: Publish a synthetic native event. Inputs: Phase/ID/reason. Outputs: Observer calls.
   * Logic: Snapshot observers supports cleanup; Constraints: old IDs may deliberately be delivered. */
  emit(phase, requestId = this.current?.id, reason = null) {
    for (const listener of [...this.listeners]) listener({channel: "speech", phase,
      request_id: requestId, reason, at: this.clock.now()});
  }
  /** Functionality: Expose admission metadata. Inputs: Fixture state. Outputs: Shared controller contract.
   * Logic: No body in status; Constraints: hardware support is synthetic. */
  describe() { return {supported: this.supported, enabled: this.enabled, visible: this.visible,
    user_activated: true, active_request: !!this.current, engine_speaking: !!this.current?.started,
    engine_pending: !!this.current && !this.current.started, engine_paused: false,
    last_request: this.lastRequest && {...this.lastRequest}}; }
  /** Functionality: Return voice identities. Inputs: Fixture inventory. Outputs: Fresh inventory.
   * Logic: Query failure is explicit; Constraints: no default or remote substitution. */
  voiceList() { return {accepted: this.voiceAccepted, voices: [...this.voices]}; }
  /** Functionality: Submit one request. Inputs: Text/options. Outputs: Shared acknowledgement.
   * Logic: Synchronous submitted/start/error delivery exposes reentrancy errors.
   * Constraints: Overlap fails immediately; no queue or automatic finish. */
  speak(text, options) {
    assert.equal(this.current, null, "adapter must respect the shared output's ownership");
    if (this.refusal) return {accepted: false, reason: this.refusal};
    this.current = {id: ++this.sequence, text, options}; this.requests.push(this.current);
    this.lastRequest = {id: this.current.id, ...options};
    this.emit("submitted");
    if (this.synchronous === "start") this.start();
    if (this.synchronous === "error") this.error();
    return {accepted: true, last_request: {...this.lastRequest}};
  }
  /** Functionality: Start current request. Inputs: Queued request. Outputs: Start event.
   * Logic: Preserve ID and mark busy phase; Constraints: not audible sound evidence. */
  start() { this.current.started = true; this.emit("start"); }
  /** Functionality: Complete request. Inputs: Current request. Outputs: Free controller/end.
   * Logic: Release before notification; Constraints: no automatic successor. */
  end() { const id = this.current.id; this.current = null; this.emit("end", id); }
  /** Functionality: Fail request. Inputs: Current request. Outputs: Free controller/error.
   * Logic: Explicit native error; Constraints: adapter must not retry automatically. */
  error() { const id = this.current.id; this.current = null; this.emit("error", id, "native_error"); }
  /** Functionality: Occupy driver with external speech. Inputs: None. Outputs: Foreign request.
   * Logic: Share native ID space without recording an adapter submission; Constraints: no speech text needed. */
  manual() { assert.equal(this.current, null); this.current = {id: ++this.sequence, manual: true};
    this.lastRequest = {id: this.current.id}; this.emit("submitted"); return this.current.id; }
  /** Functionality: Cancel only matching ownership. Inputs: ID. Outputs: Acknowledgement.
   * Logic: Emit cancel after freeing driver; Constraints: foreign requests cannot be canceled. */
  stopOwned(id) {
    this.cancelAttempts.push(id);
    if (this.current?.id !== id) return {accepted: false, reason: "not_owned"};
    if (this.cancelFailure) return {accepted: false, reason: "cancel_failed"};
    this.cancels.push(id); this.current = null; this.emit("cancel", id); return {accepted: true};
  }
}

/** Functionality: Build a navigation snapshot from the existing core contract.
 * Inputs: Timestamp and top-level/position/progress overrides. Outputs: Fresh independent object.
 * Logic: Depart then left turn at the end of a segment; Constraints: synthetic route and position. */
function snapshot(timestamp = 100000, overrides = {}) {
  return {available: true, supported: true, visible: true, state: "navigating", active: true, reason: null,
    policy: {...NAVIGATION_POLICY}, route: {provider: "openrouteservice", profile: "foot-walking", request_id: null},
    ...overrides,
    position: {longitude: 0, latitude: 0, accuracy_m: 5, timestamp, age_ms: 0, ...overrides.position},
    progress: {step_index: 0, current_step: {type: 11, instruction: "Head east on Example Road"},
      next_step: {type: 0, instruction: "Turn left onto Example Street"}, distance_to_next_m: 42,
      remaining_distance_m: 142, ...overrides.progress}};
}

/** Functionality: Create a configured independent adapter fixture. Inputs: Enabled toggle.
 * Outputs: Adapter/driver/clock/events/ingest. Logic: Explicit begin separates navigation runs.
 * Constraints: No DOM, browser engine, GPS or risk manager is involved. */
function fixture(enabled = true) {
  const clock = new Clock(), speech = new Speech(clock), events = [];
  const adapter = new NavigationSpeechAdapter({speech, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer});
  adapter.subscribe(event => events.push(event)); adapter.configure({voiceId: "local-zh", language: "zh-CN"});
  adapter.begin("navigation-test"); if (enabled) adapter.setEnabled(true);
  /** Functionality: Deliver latest snapshot. Inputs: Overrides. Outputs: Adapter effects.
   * Logic: Default source timestamp follows test clock; Constraints: no simulated sensor acquisition. */
  function ingest(overrides = {}) { return adapter.ingest(snapshot(clock.now(), overrides)); }
  return {clock, speech, adapter, events, ingest};
}

/** Functionality: Verify current/next semantics. Inputs: Independent route snapshot. Outputs: Exact product text.
 * Logic: The countdown belongs to next step; Constraints: no English street-name translation. */
test("navigation speech counts down to the next maneuver rather than the current one", function testTextSemantics() {
  assert.equal(navigationSpeechText(snapshot(), "zh-CN"), "开始沿规划路线行走。沿当前路段行进，约42米后左转。");
  const value = snapshot(100000, {progress: {current_step: {type: 1, instruction: "Turn right"},
    next_step: {type: 0, instruction: "Turn left"}}});
  assert.equal(navigationSpeechText(value, "zh-CN"), "右转。沿当前路段行进，约42米后左转。");
});

/** Functionality: Cover all valid turn types. Inputs: ORS type enums. Outputs: Product Chinese labels.
 * Logic: Assert expected text independently; Constraints: arrival remains a controller state. */
test("Chinese navigation uses type labels rather than English original instructions", function testLabels() {
  for (const type of [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13]) {
    const text = navigationSpeechText(snapshot(100000, {progress: {
      current_step: {type, instruction: "English original must not be read"}, next_step: null}}), "zh-CN");
    assert.equal(text, ZH_LABELS[type] + "。");
  }
});

/** Functionality: Verify supported English speech. Inputs: Depart/left snapshot. Outputs: Type-derived instruction.
 * Logic: Check current and upcoming maneuver separately; Constraints: no invented street names. */
test("English navigation preserves the next maneuver and endpoint semantics", function testEnglish() {
  const text = navigationSpeechText(snapshot(), "en-US");
  assert.match(text, /^Start walking along the planned route\./);
  assert.match(text, /In about 42 meters, turn left\./);
  assert.doesNotMatch(text, /Example/);
});

/** Functionality: Prevent false arrival and rounded zero. Inputs: Upcoming endpoint/short distance.
 * Outputs: Future endpoint statement; Logic: Only controller arrived may announce arrival.
 * Constraints: no distance or arrival threshold changes. */
test("upcoming destination is never announced as an already completed arrival", function testEndpoint() {
  const value = snapshot(100000, {progress: {current_step: {type: 6}, next_step: {type: 10}, distance_to_next_m: 20}});
  assert.equal(navigationSpeechText(value, "zh-CN"), "直行。前方约20米为路线终点。");
  assert.doesNotMatch(navigationSpeechText(value, "en-US"), /arrived|have reached/i);
  value.progress.distance_to_next_m = 0.2;
  assert.match(navigationSpeechText(value, "zh-CN"), /不足1米/);
  assert.doesNotMatch(navigationSpeechText(value, "zh-CN"), /约0米/);
});

/** Functionality: Verify states without route guidance. Inputs: Lifecycle snapshots. Outputs: Silence/notices.
 * Logic: No stale turn in unavailable states; Constraints: reason wording can remain a product choice. */
test("state notices never reuse a turn and inactive ready states remain silent", function testTextStates() {
  for (const state of ["idle", "ready", "stopped", "locating"])
    assert.equal(navigationSpeechText(snapshot(100000, {state, active: false}), "zh-CN"), "");
  for (const [state, reason] of [["off_route", "off_route"], ["locating", "accuracy_insufficient"], ["error", "position_stale"]]) {
    const text = navigationSpeechText(snapshot(100000, {state, reason}), "zh-CN");
    assert.ok(text.length > 0); assert.doesNotMatch(text, /42米|左转|Head east/);
  }
});

/** Functionality: Verify explicit opt in and run boundary. Inputs: Disabled/unbound adapter.
 * Outputs: No speech; Logic: Ingestion cannot create a navigation run or enable sound.
 * Constraints: native output switch remains independent. */
test("navigation speech defaults off and requires an explicit run", function testDefaultOff() {
  const f = fixture(false); f.ingest(); assert.equal(f.speech.requests.length, 0);
  assert.equal(f.adapter.describe().enabled, false);
  f.adapter.stop(); f.adapter.setEnabled(true); f.ingest(); assert.equal(f.speech.requests.length, 0);
  f.adapter.begin("explicit-run"); f.ingest(); assert.equal(f.speech.requests.length, 1); f.adapter.dispose();
});

/** Functionality: Bound repetitive GPS speech. Inputs: Same-step distance updates. Outputs: One uninterrupted utterance.
 * Logic: Semantic step identity rather than whole snapshot; Constraints: no distance reminder policy added. */
test("same-step GPS distance updates neither replay nor interrupt speech", function testStepDedup() {
  const f = fixture(); f.ingest(); f.speech.start();
  f.clock.advance(100100); f.ingest({progress: {distance_to_next_m: 18}});
  assert.equal(f.speech.requests.length, 1); assert.deepEqual(f.speech.cancels, []);
  f.speech.end(); f.ingest({progress: {distance_to_next_m: 12}});
  assert.equal(f.speech.requests.length, 1); f.adapter.dispose();
});

/** Functionality: Handle genuine step changes and GPS rollback. Inputs: Step zero/one/zero.
 * Outputs: Three current announcements; Logic: Last semantic step does not enforce monotonicity.
 * Constraints: no rerouting or artificial progress. */
test("new and backward steps replace only the adapter's previous instruction", function testStepRollback() {
  const f = fixture(); f.ingest(); f.speech.start();
  f.ingest({progress: {step_index: 1, current_step: {type: 0}, next_step: {type: 1}}});
  assert.equal(f.speech.requests.length, 2); assert.deepEqual(f.speech.cancels, [1]);
  assert.match(f.speech.requests[1].text, /^左转。/);
  f.ingest(); assert.equal(f.speech.requests.length, 3); assert.match(f.speech.requests[2].text, /^开始/);
  f.adapter.dispose();
});

/** Functionality: Withhold turns during unreliable states. Inputs: Off-route/low accuracy/recovery.
 * Outputs: Single state notice then fresh turn; Logic: State transitions invalidate old turn.
 * Constraints: no automatic route request or GPS retry. */
test("off-route and poor accuracy replace turns once and recover only on fresh guidance", function testStatusTransitions() {
  for (const status of [{state: "off_route", reason: "off_route"},
    {state: "locating", reason: "accuracy_insufficient", position: {accuracy_m: 80}}]) {
    const f = fixture(); f.ingest(); f.speech.start(); f.ingest(status);
    assert.equal(f.speech.requests.length, 2); assert.deepEqual(f.speech.cancels, [1]);
    f.speech.end(); f.ingest(status); assert.equal(f.speech.requests.length, 2);
    f.ingest(); assert.equal(f.speech.requests.length, 3); f.adapter.dispose();
  }
});

/** Functionality: Respect arrived inactive snapshot. Inputs: Controller-style arrival. Outputs: One arrival notice.
 * Logic: Arrival is terminal but trusted; Constraints: missing/stale/poor fixes cannot arrive by speech. */
test("fresh controller arrival is spoken once despite active false", function testArrival() {
  const f = fixture(); f.ingest({state: "arrived", active: false, progress: {step_index: 2,
    current_step: {type: 10}, next_step: null, distance_to_next_m: 0}});
  assert.equal(f.speech.requests.length, 1); assert.match(f.speech.requests[0].text, /到达/);
  f.speech.end(); f.ingest({state: "arrived", active: false});
  assert.equal(f.speech.requests.length, 1); f.adapter.dispose();
});

/** Functionality: Reject unreliable guidance. Inputs: Stale/future/nonfinite/low quality snapshots.
 * Outputs: No turn; Logic: Original quality gates remain upper bounds.
 * Constraints: no fabricated fallback location. */
test("stale, future and poor-quality fixes cannot produce navigation turns", function testInvalidFix() {
  const cases = [{position: {timestamp: 85000}}, {position: {timestamp: 100001}},
    {position: {timestamp: NaN}}, {position: {accuracy_m: 31}}, {active: false}];
  for (const overrides of cases) {
    const f = fixture(); f.ingest(overrides);
    assert.equal(f.speech.requests.length, 0, JSON.stringify(overrides)); f.adapter.dispose();
  }
});

/** Functionality: Preserve queued source expiry. Inputs: Queued speech then newer same-step fix.
 * Outputs: Old request canceled at original deadline; Logic: New GPS cannot refresh utterance provenance.
 * Constraints: no native start/finish inferred. */
test("queued turn expires by its own GPS timestamp rather than later same-step fixes", function testQueuedExpiry() {
  const f = fixture(); f.ingest(); f.clock.advance(114000); f.ingest();
  assert.equal(f.speech.requests.length, 1); f.clock.advance(115000);
  assert.ok(f.speech.cancels.includes(1)); f.adapter.stop(); assert.equal(f.clock.timers.size, 0);
});

/** Functionality: Prevent persistent obsolete directions. Inputs: Started instruction with GPS silence.
 * Outputs: Own speech canceled by core freshness deadline; Logic: no new positions preserve guidance.
 * Constraints: adapter does not alter sensor timeout. */
test("started turn cannot remain active after its latest GPS source becomes stale", function testRunningExpiry() {
  const f = fixture(); f.ingest(); f.speech.start(); f.clock.advance(115000);
  assert.ok(f.speech.cancels.includes(1)); assert.equal(f.adapter.describe().active_request_id, null); f.adapter.dispose();
});

/** Functionality: Respect shared manual ownership and latest-only pending. Inputs: Foreign speech and two steps.
 * Outputs: Only newest fresh maneuver after foreign end; Logic: no FIFO or preemption.
 * Constraints: an external event is not permission to retry a failed request. */
test("manual speech is never interrupted and only the latest pending step is submitted", function testManualLatest() {
  const f = fixture(), foreign = f.speech.manual(); f.ingest();
  f.ingest({progress: {step_index: 1, current_step: {type: 0}, next_step: {type: 1}}});
  assert.equal(f.speech.requests.length, 0); assert.deepEqual(f.speech.cancels, []);
  f.speech.end(); assert.equal(f.speech.requests.length, 1); assert.match(f.speech.requests[0].text, /^左转。/);
  assert.notEqual(f.speech.current.id, foreign); f.adapter.dispose();
});

/** Functionality: Reject delayed pending guidance. Inputs: Busy foreign speech beyond freshness.
 * Outputs: No later navigation submission; Logic: pending source retains original timestamp.
 * Constraints: no deferred stale replay. */
test("foreign completion never resurrects an expired pending maneuver", function testManualExpiry() {
  const f = fixture(); f.speech.manual(); f.ingest(); f.clock.advance(115000); f.speech.end();
  assert.equal(f.speech.requests.length, 0); f.adapter.dispose();
});

/** Functionality: Enforce explicit local language-matching voice. Inputs: Missing/remote/mismatched/ambiguous/query failures.
 * Outputs: No speech; Logic: invalid inventory cannot fall back.
 * Constraints: voice IDs are synthetic. */
test("default, remote, wrong-language, missing and ambiguous voices are rejected", function testVoiceAdmission() {
  for (const voiceId of ["", "remote-zh", "local-en", "missing"]) {
    const f = fixture(); f.adapter.configure({voiceId, language: "zh-CN"}); f.ingest();
    assert.equal(f.speech.requests.length, 0, voiceId); f.adapter.dispose();
  }
  for (const fail of ["ambiguous", "query"]) {
    const f = fixture();
    if (fail === "ambiguous") f.speech.voices.push({...VOICES[0]}); else f.speech.voiceAccepted = false;
    f.ingest(); assert.equal(f.speech.requests.length, 0, fail); f.adapter.dispose();
  }
});

/** Functionality: Respect user speech controls. Inputs: Default 2x then explicit override.
 * Outputs: Requested rates/volumes; Logic: adapter reads existing controls per submission.
 * Constraints: does not force rate after user changes it. */
test("speech follows 2x controls and later user rate and volume overrides", function testRate() {
  const f = fixture(); f.ingest(); assert.equal(f.speech.requests[0].options.rate, 2); f.speech.end();
  f.speech.rate.value = "1.4"; f.speech.volume.value = "0.4";
  f.ingest({progress: {step_index: 1}});
  assert.equal(f.speech.requests[1].options.rate, 1.4); assert.equal(f.speech.requests[1].options.volume, 0.4);
  f.adapter.dispose();
});

/** Functionality: Bind synchronous lifecycle to correct request. Inputs: Synchronous native start.
 * Outputs: Single owned request/start; Logic: callbacks may arrive before speak returns.
 * Constraints: not a real browser timing claim. */
test("synchronous submitted and start are bound to one owned request", function testSynchronousStart() {
  const f = fixture(); f.speech.synchronous = "start"; f.ingest();
  assert.equal(f.adapter.describe().active_request_id, 1); assert.equal(f.adapter.describe().active_started, true);
  assert.equal(f.events.filter(event => event.phase === "output_start").length, 1);
  f.speech.end(); assert.equal(f.adapter.describe().active_request_id, null); f.adapter.dispose();
});

/** Functionality: Latch synchronous errors. Inputs: Synchronous engine error then new fix.
 * Outputs: Paused/no duplicate; Logic: accepted return cannot overwrite observed failure.
 * Constraints: recovery is explicit. */
test("synchronous native errors cannot leave phantom active output or auto retry", function testSynchronousError() {
  const f = fixture(); f.speech.synchronous = "error"; f.ingest();
  assert.equal(f.adapter.describe().paused, true); assert.equal(f.adapter.describe().active_request_id, null);
  f.ingest({progress: {step_index: 1}}); assert.equal(f.speech.requests.length, 1);
  f.speech.synchronous = null; f.adapter.resume(); assert.equal(f.speech.requests.length, 2); f.adapter.dispose();
});

/** Functionality: Require deliberate recovery after submission refusal. Inputs: Rejected speak and fresh sources.
 * Outputs: No automatic request until resume; Logic: failure latch survives begin.
 * Constraints: does not enable native output or replace voice. */
test("submission failure stays paused across fixes and new runs until manual resume", function testRefusalResume() {
  const f = fixture(); f.speech.refusal = "native_error"; f.ingest();
  assert.equal(f.adapter.describe().paused, true); f.speech.refusal = null;
  f.adapter.begin("another-run"); f.ingest(); assert.equal(f.speech.requests.length, 0);
  f.adapter.resume(); assert.equal(f.speech.requests.length, 1); f.adapter.dispose();
});

/** Functionality: Latch asynchronous errors. Inputs: Failed own request then later fixes/events.
 * Outputs: No retry until explicit resume; Logic: foreign events do not clear error state.
 * Constraints: no native error fallback. */
test("asynchronous own error pauses and foreign completion cannot recover it", function testAsynchronousError() {
  const f = fixture(); f.ingest(); f.speech.error(); f.ingest({progress: {step_index: 1}});
  f.speech.emit("end", 999); assert.equal(f.speech.requests.length, 1);
  assert.equal(f.adapter.describe().paused, true); f.adapter.resume(); assert.equal(f.speech.requests.length, 2);
  f.adapter.dispose();
});

/** Functionality: Respect manual stop of owned output. Inputs: External cancel event.
 * Outputs: Paused rather than replayed; Logic: user intent differs from adapter transition cancellation.
 * Constraints: explicit resume is required. */
test("manual cancellation of owned speech never automatically replays the same instruction", function testUserCancellation() {
  const f = fixture(); f.ingest(); f.speech.stopOwned(f.speech.current.id); f.ingest();
  assert.equal(f.speech.requests.length, 1); assert.equal(f.adapter.describe().paused, true); f.adapter.dispose();
});

/** Functionality: Isolate stopped generations. Inputs: Stop/restart plus late native events.
 * Outputs: New owned request survives old error/end; Logic: old IDs cannot affect current run.
 * Constraints: route request IDs need not exist. */
test("stop and begin isolate late events and allow the same route step in a new run", function testStopRestart() {
  const f = fixture(); f.ingest(); const old = f.speech.current.id; f.adapter.stop("page_hidden");
  f.ingest(); assert.equal(f.speech.requests.length, 1); assert.equal(f.clock.timers.size, 0);
  f.adapter.begin("new-run"); f.ingest(); const current = f.speech.current.id;
  f.speech.emit("error", old, "native_error"); f.speech.emit("end", old);
  assert.equal(f.adapter.describe().active_request_id, current); assert.equal(f.adapter.describe().paused, false);
  assert.equal(f.adapter.describe().run_id, "new-run"); f.adapter.dispose();
});

/** Functionality: Clean up on opt out. Inputs: Disable during own output then foreign output/stop.
 * Outputs: Own cancellation only; Logic: disabling does not invalidate unrelated output.
 * Constraints: no implicit replay on reenable without a source. */
test("disable clears own pending work and stop never cancels later manual speech", function testDisable() {
  const f = fixture(); f.ingest(); f.adapter.setEnabled(false); assert.deepEqual(f.speech.cancels, [1]);
  const manual = f.speech.manual(); f.adapter.stop(); assert.equal(f.speech.current.id, manual);
  f.adapter.setEnabled(true); f.speech.end(); assert.equal(f.speech.requests.length, 1); f.adapter.dispose();
});

/** Functionality: Release subscriptions and deadlines. Inputs: Active adapter/dispose.
 * Outputs: Zero listeners/timers with no late replay; Logic: disposal is idempotent.
 * Constraints: no native output proof. */
test("dispose releases all adapter subscriptions and timers", function testDispose() {
  const f = fixture(); f.ingest(); f.adapter.dispose(); f.adapter.dispose();
  assert.equal(f.speech.listeners.size, 0); assert.equal(f.clock.timers.size, 0);
  f.clock.advance(120000); f.speech.emit("end", 1); assert.equal(f.speech.requests.length, 1);
});

/** Functionality: Exercise real controller snapshots through independent integration.
 * Inputs: Explicit synthetic L-route and GPS callbacks. Outputs: Correct start/turn/arrival speech.
 * Logic: Production WalkingNavigation performs geometry/step/arrival decisions unchanged.
 * Constraints: synthetic GPS, route and output are not actual walking navigation validation. */
test("existing navigation controller feeds start, step change and inactive arrival without risk integration", function testRealNavigation() {
  const f = fixture(); let success; const cleared = [];
  const geo = {
    // Functionality: Register synthetic GPS callback. Inputs: Success callback. Outputs: Zero watch ID.
    // Logic: Retain callback for explicit delivery; Constraints: no device or permission request.
    watchPosition(callback) { success = callback; return 0; },
    // Functionality: Record cleanup. Inputs: Watch ID. Outputs: Cancellation record.
    // Logic: Append ID once per core cancellation; Constraints: no hardware resource exists.
    clearWatch(id) { cleared.push(id); },
  };
  const nav = new WalkingNavigation({geo, now: f.clock.now, schedule: f.clock.setTimer, cancel: f.clock.clearTimer,
    isVisible: () => true, onChange: value => f.adapter.ingest(value)});
  nav.setRoute({provider: "openrouteservice", profile: "foot-walking", geometry: {type: "LineString",
    coordinates: [[0, 0], [0.001, 0], [0.001, 0.001]]}, summary: {distance_m: 222, duration_s: 180}, steps: [
    {type: 11, instruction: "Head east", way_points: [0, 1], distance_m: 111, duration_s: 90},
    {type: 0, instruction: "Turn left", way_points: [1, 2], distance_m: 111, duration_s: 90},
    {type: 10, instruction: "Arrive", way_points: [2, 2], distance_m: 0, duration_s: 0}]});
  nav.start(); success({coords: {longitude: 0.0005, latitude: 0, accuracy: 5}, timestamp: f.clock.now()});
  assert.equal(f.speech.requests.length, 1); assert.match(f.speech.requests[0].text, /米后左转/);
  f.clock.advance(101000); success({coords: {longitude: 0.001, latitude: 0.0002, accuracy: 5}, timestamp: f.clock.now()});
  assert.equal(f.speech.requests.length, 2); assert.match(f.speech.requests[1].text, /^左转。/);
  f.clock.advance(102000); success({coords: {longitude: 0.001, latitude: 0.001, accuracy: 5}, timestamp: f.clock.now()});
  assert.equal(nav.describe().state, "arrived"); assert.equal(nav.describe().active, false);
  assert.equal(f.speech.requests.length, 3); assert.match(f.speech.requests[2].text, /到达/);
  assert.deepEqual(cleared, [0]); assert.deepEqual(NAVIGATION_POLICY,
    {max_accuracy_m: 30, off_route_m: 40, arrival_m: 15, max_fix_age_ms: 15000}); f.adapter.dispose();
});

/** Functionality: Keep diagnostics private and honest. Inputs: Actual adapter events/state.
 * Outputs: No speech body/location and no hardware claim; Logic: compare against distinctive source strings.
 * Constraints: fixture requests intentionally retain text only in test memory. */
test("navigation diagnostics omit spoken bodies and coordinates and never claim verified audio", function testDiagnostics() {
  const f = fixture(); f.ingest({position: {longitude: 103.776543, latitude: 1.297654}}); f.speech.start(); f.speech.end();
  const diagnostics = JSON.stringify({events: f.events, status: f.adapter.describe()});
  assert.doesNotMatch(diagnostics, /103\.776543|1\.297654|Head east|开始沿规划路线行走|约42米/);
  assert.equal(f.adapter.describe().audio_output_verified, false); f.adapter.dispose();
});

/** Functionality: Permit truthful failure notice without a usable fix. Inputs: Error with null position/progress.
 * Outputs: One error notice that remains queued; Logic: Failure is an existing core state, not GPS guidance.
 * Constraints: no turn, restart or stale-location claim. */
test("terminal location errors are announced once without requiring a GPS fix", function testErrorNotice() {
  const f = fixture();
  const value = {state: "error", reason: "permission_denied", active: false, position: null, progress: null};
  f.adapter.ingest(value); assert.equal(f.speech.requests.length, 1);
  assert.match(f.speech.requests[0].text, /导航已停止/);
  f.adapter.ingest(value); assert.equal(f.speech.requests.length, 1); assert.deepEqual(f.speech.cancels, []);
  f.clock.advance(120000); assert.deepEqual(f.speech.cancels, []);
  f.speech.end(); f.adapter.ingest(value); assert.equal(f.speech.requests.length, 1); f.adapter.dispose();
});

/** Functionality: Distinguish started guidance from queued source provenance.
 * Inputs: Started speech and newer same-step GPS fix. Outputs: Freshness follows latest source.
 * Logic: Same-step updates sustain a current turn without interrupting or repeating it.
 * Constraints: original queued expiry is covered separately. */
test("started same-step guidance follows latest GPS freshness without replay", function testStartedLatestFreshness() {
  const f = fixture(); f.ingest(); f.speech.start(); f.clock.advance(114000); f.ingest();
  f.clock.advance(115000); assert.deepEqual(f.speech.cancels, []); assert.equal(f.speech.requests.length, 1);
  f.clock.advance(128999); assert.deepEqual(f.speech.cancels, []);
  f.clock.advance(129000); assert.deepEqual(f.speech.cancels, [1]); f.adapter.dispose();
});

/** Functionality: Latch cancellation failures without hidden retry loops.
 * Inputs: Failed cancellation, repeated/new steps and expiry. Outputs: One automatic attempt and explicit retained ownership.
 * Logic: Only an explicit stop may try cleanup again; Constraints: failure cannot be reported as confirmed silence. */
test("failed cancellation retains ownership and never retries on GPS or source expiry", function testCancellationFailure() {
  const f = fixture(); f.ingest(); f.speech.start(); f.speech.cancelFailure = true;
  f.ingest({progress: {step_index: 1}}); assert.equal(f.adapter.describe().paused, true);
  assert.equal(f.adapter.describe().active_request_id, 1); assert.deepEqual(f.speech.cancelAttempts, [1]);
  f.ingest({progress: {step_index: 2}}); f.clock.advance(115000);
  assert.deepEqual(f.speech.cancelAttempts, [1]); assert.equal(f.speech.requests.length, 1);
  f.adapter.resume(); assert.equal(f.adapter.describe().paused, true);
  assert.deepEqual(f.speech.cancelAttempts, [1]); assert.equal(f.adapter.describe().active_request_id, 1);
  f.speech.cancelFailure = false; f.adapter.stop("user"); assert.deepEqual(f.speech.cancelAttempts, [1, 1]);
  assert.equal(f.adapter.describe().active_request_id, null); assert.equal(f.clock.timers.size, 0);
  f.adapter.resume(); assert.equal(f.adapter.describe().paused, false); f.adapter.dispose();
});

/** Functionality: Preserve ownership when the shared driver's external stop remains unconfirmed.
 * Inputs: A shared cancel_failed error with its native current request retained, later GPS and expiry.
 * Outputs: Latched pause, no implicit cleanup attempts, and explicit stop/recovery assertions.
 * Logic: Match SpeechOutput.stop's failure event without clearing current as ordinary engine errors do.
 * Constraints: No driver implementation change, automatic cancellation retry or false-silence claim. */
test("failed external shared stop retains ownership until explicit successful cleanup", function testExternalCancellationFailure() {
  const f = fixture(); f.ingest(); f.speech.start();
  const id = f.speech.current.id;
  f.speech.emit("error", id, "cancel_failed");
  assert.equal(f.speech.current.id, id);
  assert.equal(f.adapter.describe().active_request_id, id);
  assert.equal(f.adapter.describe().active_started, true);
  assert.equal(f.adapter.describe().reason, "cancel_failed");
  assert.equal(f.adapter.describe().paused, true);
  f.adapter.resume();
  assert.equal(f.adapter.describe().paused, true);
  assert.equal(f.adapter.describe().reason, "cancel_failed");
  f.ingest({progress: {step_index: 1}}); f.clock.advance(115000);
  assert.equal(f.adapter.describe().active_request_id, id);
  assert.equal(f.adapter.describe().paused, true);
  assert.deepEqual(f.speech.cancelAttempts, []);
  assert.equal(f.speech.requests.length, 1);
  f.adapter.stop("user");
  assert.deepEqual(f.speech.cancelAttempts, [id]);
  assert.equal(f.speech.current, null);
  assert.equal(f.adapter.describe().active_request_id, null);
  assert.equal(f.adapter.describe().paused, true);
  assert.equal(f.clock.timers.size, 0);
  f.adapter.resume();
  assert.equal(f.adapter.describe().paused, false);
  assert.equal(f.adapter.describe().reason, null);
  f.adapter.begin("after-explicit-cleanup"); f.ingest();
  assert.equal(f.speech.requests.length, 2);
  assert.notEqual(f.adapter.describe().active_request_id, id);
  f.adapter.dispose();
});

/** Functionality: Keep native prerequisites independent of adapter enablement.
 * Inputs: Disabled/unsupported/background output driver. Outputs: No speech or implicit enablement.
 * Logic: Existing shared output policy remains authoritative; Constraints: no browser permission proof. */
test("unavailable disabled and hidden shared output cannot be bypassed by navigation", function testNativePrerequisites() {
  for (const field of ["enabled", "supported", "visible"]) {
    const f = fixture(); f.speech[field] = false; f.ingest();
    assert.equal(f.speech.requests.length, 0, field); assert.equal(f.speech[field], false); f.adapter.dispose();
  }
});

/** Functionality: Cancel obsolete instructions on silent lifecycle states.
 * Inputs: Ready/idle/stopped transitions from an active turn. Outputs: Own cancellation and no state speech.
 * Logic: No progress survives lifecycle invalidation; Constraints: no automatic begin or GPS restart. */
test("ready idle and stopped states cancel old turns without speaking stale directions", function testSilentLifecycle() {
  for (const state of ["ready", "idle", "stopped"]) {
    const f = fixture(); f.ingest(); f.speech.start(); f.ingest({state, active: false});
    assert.deepEqual(f.speech.cancels, [1]); assert.equal(f.speech.requests.length, 1);
    assert.equal(f.adapter.describe().active_request_id, null); f.adapter.dispose();
  }
});
