/* Responsibilities: Independently verify unified risk/navigation scheduling and driver ownership.
 * Implementation: Separate epoch/monotonic clocks, explicit source snapshots and synchronous or
 * deferred fake-driver events exercise the production manager without sensors or native output;
 * slow synthesis remains preparation through fresh-frame semantic changes, requires matching
 * eligible highest-priority evidence at first PCM, completes started same-target sentences, and retains
 * distinct-target priority/lifecycle preemption without altering original request provenance.
 * Related Modules: integration/assist-guidance.js, integration/video-alerts.js,
 * integration/navigation-speech.js. No algorithm implementation or threshold is substituted.
 * Declaration Index: Clock; Clock.constructor; Clock.now; Clock.epochNow; Clock.setTimer;
 * Clock.clearTimer; Clock.advance; Speech; Speech.constructor; Speech.subscribe; Speech.emit;
 * Speech.describe; Speech.voiceList; Speech.speak; Speech.start; Speech.end; Speech.error;
 * Speech.manual; Speech.stopOwned; Vibration; Vibration.constructor; Vibration.subscribe;
 * Vibration.emit; Vibration.describe; Vibration.testPattern; Vibration.stopOwned;
 * box; signalBox; navigationSnapshot; fixture; fixture.risk; fixture.navigation;
 * testDisabled; testNavigationOnly; testAssistantReplyQueue; testAssistantReplyPriority;
 * testRiskOnly; testPreemption; testLatestNavigation;
 * testCompletedNavigation; testRiskCompletion; testEscalation; testSameTargetRepeats;
 * testSameTargetEscalation; testEscalationUsesLatestRisk; testConfirmedTargetEscalation; testConfirmedTargetPreemption;
 * testOtherTargetEscalation; testEscalationEpochIdentity; testCooldown; testVibrationCooldown; testSourceClock;
 * testQueuedRisk; testSlowRiskSynthesis; testRiskReranking; testQueuedEscalation;
 * testRiskCrossTrackConfirmation; testQueuedRiskTargetIneligible; testChangedRiskSynthesis;
 * testPreparationSemanticRecovery; testRiskConfirmationCooldown; testNoAlertPreparation;
 * testPreparationYields; testPreparationBoundaries; testExpiredBeforePlayback;
 * testLanguageChangedBeforePlayback; testQueuedNavigation; testNavigationExpiry; testIndependentStops;
 * testIndependentSwitches; testForeignSpeech; testForeignVibration; testSynchronous;
 * testChannelFailure; testCancellationFailure; testExternalFailure; testUserCancel;
 * testVoice; testEpochReset; testSessionIsolation; testGlobalStop; testPort; testMetadata;
 * testRiskSpeechReasons; testRiskSpeechDeadlines; testRiskSpeechOwnership; testRiskSpeechTransition;
 * testTerminalNavigation; testVibrationFailure.
 * testInitialSignal; testSignalColourChanges; testSignalInvalidation; testSignalBeforeStart;
 * testSignalPriority; testSignalManualOwnership; testSignalLifecycle; testSignalFailure;
 * testSignalEncounter; testSignalInstances; testSignalVibration.
 * Variable Index: None. Driver utterance bodies remain only in test memory; all sources are synthetic.
 * Constraints: Passing tests does not verify actual cameras, GPS, GPU, browser TTS or physical motors.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {AssistGuidanceManager} from "../integration/assist-guidance.js";
import {MAX_TEXT_LENGTH} from "../speech-output.js";
import {videoAlertText} from "../integration/video-alerts.js";
import {navigationSpeechText} from "../integration/navigation-speech.js";

/* Functionality: Control two clocks with deliberately different origins.
 * Logic: Advance both by equal elapsed time and execute due monotonic timers.
 * Constraints: No real waiting, scheduler performance or browser-clock accuracy assertion. */
class Clock {
  /* Functionality: Initialize separated origins. Inputs: None. Outputs: Isolated clock.
   * Logic: Epoch offset is one million milliseconds. Constraints: Never interchangeable origins. */
  constructor() { this.time = 100; this.offset = 1000000; this.sequence = 0; this.timers = new Map(); }
  /* Functionality: Read monotonic time. Inputs: State. Outputs: Milliseconds.
   * Logic: Direct lookup. Constraints: Changes only via advance. */
  now = () => this.time;
  /* Functionality: Read epoch time. Inputs: State. Outputs: Milliseconds.
   * Logic: Preserve an explicit origin offset. Constraints: No system clock dependency. */
  epochNow = () => this.offset + this.time;
  /* Functionality: Register deadline. Inputs: Callback/delay. Outputs: ID.
   * Logic: Absolute monotonic due time. Constraints: No repeat or retry. */
  setTimer = (callback, delay) => { const id = ++this.sequence; this.timers.set(id, {callback, at: this.time + delay}); return id; };
  /* Functionality: Clear deadline. Inputs: ID. Outputs: Deletion result.
   * Logic: Map deletion. Constraints: No other timer changes. */
  clearTimer = id => this.timers.delete(id);
  /* Functionality: Advance elapsed time. Inputs: Nonnegative duration. Outputs: Due effects.
   * Logic: Execute timers once in order before final jump. Constraints: No backward movement. */
  advance(duration) {
    assert.ok(duration >= 0); const target = this.time + duration;
    while (true) {
      const due = [...this.timers.entries()].filter(([, item]) => item.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.timers.delete(due[0]); this.time = due[1].at; due[1].callback();
    }
    this.time = target;
  }
}

/* Functionality: Model one shared backend speech driver with independent PCM events.
 * Logic: Preserve request IDs, explicit voice and manual ownership; cancellation can remain uncertain.
 * Constraints: No synthesis, permission grant, actual engine or audible output. */
class Speech {
  /* Functionality: Initialize driver state. Inputs: Clock. Outputs: Enabled fixture.
   * Logic: Settings and outcomes are independently controllable. Constraints: Manager stays opt-in. */
  constructor(clock) {
    Object.assign(this, {clock, current: null, lastRequest: null, sequence: 0, listeners: new Set(), requests: [],
      attempts: [], enabled: true, cancelFailure: false, refusal: null, synchronous: null,
      rate: {value: "2"}, volume: {value: "1"},
      backendOnly: true, voices: [{id: "Cherry", language: "multi", languages: ["zh-CN", "en-US"], service: "backend"}]});
  }
  /* Functionality: Subscribe. Inputs: Listener. Outputs: Unsubscribe.
   * Logic: Local set. Constraints: No implicit replay. */
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  /* Functionality: Publish lifecycle. Inputs: Phase/ID/reason. Outputs: Observer calls.
   * Logic: Snapshot listeners. Constraints: Fixture events are not physical output evidence. */
  emit(phase, id = this.current?.id, reason = null) {
    for (const listener of [...this.listeners]) listener({channel: "speech", phase, request_id: id, reason, at: this.clock.now()});
  }
  /* Functionality: Describe admission. Inputs: Current state. Outputs: Shared driver contract.
   * Logic: Exact current ownership. Constraints: No text in metadata. */
  describe() { return {supported: true, enabled: this.enabled, visible: true, user_activated: true,
    active_request: Boolean(this.current), last_request: this.lastRequest,
    engine_speaking: this.current?.started === true, engine_pending: Boolean(this.current && !this.current.started)}; }
  /* Functionality: Read explicit voice identities. Inputs: Inventory. Outputs: Copied list.
   * Logic: Stable fixture fields. Constraints: No default/remote replacement. */
  voiceList() { return {accepted: true, voices: [...this.voices]}; }
  /* Functionality: Submit once. Inputs: Text/options. Outputs: Acknowledgement.
   * Logic: Synchronous submitted/start/end/error exposes ownership reentrancy.
   * Constraints: Overlap rejected by assertion; no hidden queue. */
  speak(text, options) {
    assert.equal(this.current, null);
    if (this.refusal) return {accepted: false, reason: this.refusal};
    const id = ++this.sequence;
    this.current = {id, text, options, started: false}; this.lastRequest = {id, ...options, service: "backend"};
    this.requests.push(this.current); this.emit("submitted");
    if (this.synchronous) this.start();
    if (this.synchronous === "end") this.end();
    if (this.synchronous === "error") this.error();
    return {accepted: true, last_request: {id}};
  }
  /* Functionality: Start a current utterance only after its synchronous pre-playback guard.
   * Inputs: Current request. Outputs: PCM-start event unless cancelled before scheduling.
   * Logic: Preserve request identity across before_start observers. Constraints: Not audible proof. */
  start() {
    const request = this.current; this.emit("before_start");
    if (this.current !== request) return;
    request.started = true; this.emit("start");
  }
  /* Functionality: Complete current utterance. Inputs: Current. Outputs: End event.
   * Logic: Release before dispatch. Constraints: No automatic successor. */
  end() { const id = this.current.id; this.current = null; this.emit("end", id); }
  /* Functionality: Fail current output. Inputs: Current. Outputs: Error/free driver.
   * Logic: Ordinary engine failure releases ownership. Constraints: No retry. */
  error() { const id = this.current.id; this.current = null; this.emit("error", id, "native_error"); }
  /* Functionality: Occupy driver independently. Inputs: None. Outputs: Foreign ID.
   * Logic: Shared ID sequence. Constraints: No manager submission. */
  manual() { assert.equal(this.current, null); const id = ++this.sequence;
    this.current = {id, manual: true}; this.lastRequest = {id}; this.emit("submitted"); return id; }
  /* Functionality: Cancel only caller ownership. Inputs: ID. Outputs: Accepted/reason.
   * Logic: Failure retains current and records each attempt. Constraints: No implicit retries. */
  stopOwned(id) {
    this.attempts.push(id);
    if (this.current?.id !== id) return {accepted: false, reason: "not_owned"};
    if (this.cancelFailure) return {accepted: false, reason: "cancel_failed"};
    this.current = null; this.emit("cancel", id); return {accepted: true};
  }
}

/* Functionality: Model pending vibration identity separately from speech.
 * Logic: API acceptance emits start, never fabricated physical completion.
 * Constraints: No native vibration or device support proof. */
class Vibration {
  /* Functionality: Initialize explicit driver. Inputs: Clock. Outputs: Enabled fixture.
   * Logic: Retain pending identity until confirmed stop. Constraints: No automatic expiry in driver. */
  constructor(clock) { Object.assign(this, {clock, current: null, sequence: 0, listeners: new Set(),
    requests: [], attempts: [], enabled: true, cancelFailure: false, refusal: null}); }
  /* Functionality: Subscribe. Inputs: Listener. Outputs: Unsubscribe.
   * Logic: Local set. Constraints: No initial event. */
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  /* Functionality: Publish acceptance/cancel events. Inputs: Phase/reason. Outputs: Observer calls.
   * Logic: Current native-compatible ID. Constraints: No motor-state claim. */
  emit(phase, reason = null) { for (const listener of [...this.listeners]) listener({channel: "vibration", phase,
    request_id: this.current?.id ?? this.sequence, reason, at: this.clock.now()}); }
  /* Functionality: Describe pending ownership. Inputs: State. Outputs: Driver contract.
   * Logic: Pending is explicit. Constraints: Not physical activity inference. */
  describe() { return {supported: true, enabled: this.enabled, visible: true, user_activated: true,
    request_pending: Boolean(this.current), request_id: this.current?.id ?? this.sequence}; }
  /* Functionality: Submit fixed pattern. Inputs: Name. Outputs: API-style acknowledgement.
   * Logic: Acceptance emits synchronous start. Constraints: No hidden loop or replacement. */
  testPattern(name) {
    assert.equal(this.current, null);
    if (this.refusal) return {accepted: false, reason: this.refusal};
    this.current = {id: ++this.sequence, name}; this.requests.push(this.current); this.emit("start");
    return {accepted: true, request_id: this.current.id};
  }
  /* Functionality: Cancel matching pending ownership. Inputs: ID. Outputs: Boolean.
   * Logic: Failed stop keeps pending record. Constraints: Never cancels a foreign ID. */
  stopOwned(id) {
    this.attempts.push(id);
    if (this.current?.id !== id || this.cancelFailure) return false;
    this.current = null; this.emit("cancel"); return true;
  }
}

/* Functionality: Supply canonical algorithm-result fields. Inputs: Overrides. Outputs: Fresh box.
 * Logic: Required high-risk person by default. Constraints: No risk recomputation in fixture. */
function box(overrides = {}) {
  return {track_id: 7, label: "person", direction: "front_left", risk_level: "high", alert_priority: 3,
    alert_required: true, ttc_sec: 1, distance_m: 8, motion_state: "approaching", relative_speed_mps: -3,
    risk_reasons: ["approaching", "closing_mps_ge_2"], ...overrides};
}

/* Functionality: Supply the algorithm's merged front pedestrian signal, independent of TTC.
 * Inputs: Colour and explicit field overrides. Outputs: New source fixture.
 * Logic: Include existing class and colour-score schema with no confirmed tracking/depth.
 * Constraints: These fixtures test integration only and never replace the classifier. */
function signalBox(state = "red", overrides = {}) {
  return {label: "pedestrian light", class_id: 84, front_light: true, light_state: state,
    light_scores: {red: state === "red" ? 0.8 : 0, green: state === "green" ? 0.8 : 0, off: state === "unknown" ? 0.8 : 0},
    score: 0.8, track_id: null, direction: "front", risk_level: "unknown", alert_priority: 0,
    alert_required: false, ttc_sec: null, distance_m: null, motion_state: "unknown",
    relative_speed_mps: null, risk_reasons: [], ...overrides};
}

/* Functionality: Supply the existing navigation snapshot contract. Inputs: Epoch/overrides.
 * Outputs: Fresh navigating snapshot. Logic: Departure then left turn.
 * Constraints: Synthetic coordinates and instructions are not real route/GPS evidence. */
function navigationSnapshot(epoch, overrides = {}) {
  return {state: "navigating", active: true, reason: null, ...overrides,
    position: overrides.position === null ? null : {timestamp: epoch, accuracy_m: 5, longitude: 103.776543, latitude: 1.297654, ...overrides.position},
    progress: overrides.progress === null ? null : {step_index: 0, current_step: {type: 11, instruction: "Head east"},
      next_step: {type: 0, instruction: "Turn left"}, distance_to_next_m: 42, ...overrides.progress}};
}

/* Functionality: Bind independently controlled clocks/drivers to the actual unified owner.
 * Inputs: Optional source-enable flag. Outputs: Manager and explicit source helpers.
 * Logic: Voice setup is explicit; helpers never imply a native device source.
 * Constraints: Default-off behavior is tested with enable=false. */
function fixture(enable = true, riskMaxAgeMs = 1000, riskSpeechStyle = "detailed") {
  const clock = new Clock(), speech = new Speech(clock), vibration = new Vibration(clock), events = [];
  const manager = new AssistGuidanceManager({speech, vibration, now: clock.now, epochNow: clock.epochNow,
    setTimer: clock.setTimer, clearTimer: clock.clearTimer, riskMaxAgeMs, riskSpeechStyle});
  manager.subscribe(event => events.push(event)); manager.configure({voiceId: "Cherry", language: "zh-CN"});
  if (enable) { manager.setRiskEnabled(true); manager.setNavigationEnabled(true); }
  let frame = 0;
  return {clock, speech, vibration, manager, events,
    // Functionality: Ingest explicit risk source. Inputs: Boxes/capture. Outputs: Manager state.
    // Logic: Start a named source only when requested. Constraints: No camera or inference.
    risk(boxes = [box()], capturedAt = clock.now()) {
      if (!manager.riskSessionId) manager.beginRisk("risk-session");
      return manager.ingestRisk({sessionId: manager.riskSessionId, capturedAt,
        metadata: {index: ++frame, boxes, tracking_epoch: 1}});
    },
    // Functionality: Ingest explicit core snapshot. Inputs: Overrides. Outputs: Manager state.
    // Logic: Start a named navigation run only when requested. Constraints: No GPS/watch/route action.
    navigation(overrides = {}) {
      if (!manager.navigationRunId) manager.beginNavigation("navigation-run");
      return manager.ingestNavigation(navigationSnapshot(clock.epochNow(), overrides));
    }};
}

test("brief warnings cover uncommon classes and preserve the two-second and alert gates", () => {
  const f = fixture(true, 2000, "brief");
  f.clock.advance(3000);
  f.risk([box({label: "chair", risk_level: "high", alert_priority: 3})], f.clock.now()-1500);
  assert.equal(f.speech.requests[0].text, "警告，");
  f.speech.start(); f.speech.end();
  f.manager.stopRisk("test");
  f.risk([box({label: "stairs", risk_level: "critical", alert_priority: 4})], f.clock.now()-2001);
  assert.equal(f.speech.requests.length, 1);
  f.risk([box({label: "stairs", alert_required: false, alert_priority: 0})]);
  assert.equal(f.speech.requests.length, 1);
  f.risk([box({label: "stairs", risk_level: "critical", alert_priority: 4})]);
  assert.equal(f.speech.requests[1].text, "危险，");
  f.manager.dispose();
});

// Functionality: Check explicit admission; Inputs: Both sources; Outputs: No output; Logic: Opt in separately; Constraints: No device grant.
test("both automatic sources default disabled", function testDisabled() {
  const f = fixture(false); f.risk(); f.navigation(); assert.equal(f.speech.requests.length, 0);
  assert.equal(f.vibration.requests.length, 0); assert.deepEqual(f.manager.describe().paused, {speech: false, vibration: false}); f.manager.dispose();
});

// Functionality: Check navigation-only scheduling; Inputs: Same-step GPS; Outputs: One policy utterance; Logic: Dedup completion; Constraints: No camera requirement.
test("navigation works alone and completed same-step GPS never repeats", function testNavigationOnly() {
  const f = fixture(); f.navigation(); assert.equal(f.speech.requests[0].text, navigationSpeechText(f.manager.latestNavigation, "zh-CN"));
  assert.equal(Object.hasOwn(f.speech.requests[0].options, "purpose"), false);
  f.speech.start(); f.speech.end(); f.clock.advance(200); f.navigation(); assert.equal(f.speech.requests.length, 1);
  assert.equal(f.vibration.requests.length, 0); f.manager.dispose();
});

/* Functionality: Verify complete FIFO assistant speech and driver-sized text segmentation.
 * Inputs: Two queued replies, one longer than the native utterance limit. Outputs: Ordered owned requests.
 * Logic: Complete each simulated native utterance, then concatenate the long reply's submitted parts.
 * Constraints: Fake driver events verify scheduling only; they do not prove browser audio is audible. */
test("live assistant replies queue in order and long text is spoken without truncation", function testAssistantReplyQueue() {
  const f = fixture(), longReply = "甲".repeat(MAX_TEXT_LENGTH - 5) + "。后半段也必须完整播报。";
  f.manager.enqueueAssistantReply(longReply); f.manager.enqueueAssistantReply("第二条助手回复。");
  const parts = [];
  while (f.manager.describe().queued_assistant_replies) {
    const request = f.speech.current;
    assert.ok(request, "the next queued assistant part should own the speech driver");
    assert.equal(request.options.voice_id, "Cherry");
    assert.equal(request.options.language, "auto");
    assert.equal(Object.hasOwn(request.options, "purpose"), false);
    parts.push(request.text); f.speech.start(); f.speech.end();
  }
  assert.ok(parts.slice(0, -1).every(part => [...part].length <= MAX_TEXT_LENGTH));
  assert.equal(parts.slice(0, -1).join(""), longReply);
  assert.equal(parts.at(-1), "第二条助手回复。");
  assert.equal(f.manager.describe().queued_assistant_replies, 0);
  f.manager.dispose();
});

/* Functionality: Verify assistant priority against route guidance and risk output.
 * Inputs: Synthetic active route, assistant reply and risk source. Outputs: Owned priority transitions.
 * Logic: Assistant preempts navigation; required risk preempts the reply; route guidance resumes last.
 * Constraints: No GPS, camera, algorithm execution, physical output or device permission is exercised. */
test("risk preempts a live assistant reply and route guidance resumes after both", function testAssistantReplyPriority() {
  const f = fixture(); f.navigation(); f.manager.enqueueAssistantReply("正在测试的助手回复。");
  assert.equal(f.speech.current.text, "正在测试的助手回复。");
  f.speech.start(); f.risk();
  assert.equal(f.speech.current.text, videoAlertText(box(), "zh-CN"));
  f.speech.start(); f.risk([]); f.speech.end();
  assert.equal(f.speech.current.text, "正在测试的助手回复。");
  f.speech.start(); f.speech.end();
  assert.equal(f.speech.current.text, navigationSpeechText(f.manager.latestNavigation, "zh-CN"));
  f.manager.dispose();
});

// Functionality: Check risk-only output; Inputs: Required box; Outputs: Existing text/pattern; Logic: No nav prerequisite; Constraints: No scoring change.
test("perception works alone with existing short speech and vibration policy", function testRiskOnly() {
  const f = fixture(); f.risk(); assert.equal(f.speech.requests[0].text, videoAlertText(box(), "zh-CN"));
  assert.equal(f.vibration.requests[0].name, "double"); assert.equal(f.speech.requests[0].options.rate, 2);
  assert.equal(f.speech.requests[0].options.purpose, "risk"); f.manager.dispose();
});

// Functionality: Check unified priority; Inputs: Nav then medium risk; Outputs: Owned preemption/resume; Logic: Every risk tier wins; Constraints: Fresh current step only.
test("even medium required risk preempts navigation and unfinished current guidance resumes", function testPreemption() {
  const f = fixture(); f.navigation(); f.speech.start(); f.risk([box({risk_level: "medium", alert_priority: 2})]);
  assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.manager.active.speech.kind, "risk");
  f.speech.start(); f.risk([]); f.speech.end(); assert.equal(f.manager.active.speech.kind, "navigation");
  assert.equal(f.speech.requests.length, 3); f.manager.dispose();
});

// Functionality: Keep only current navigation; Inputs: Several steps during risk; Outputs: Latest step; Logic: Replace pending; Constraints: No FIFO.
test("risk completion consumes the latest step rather than interrupted or intermediate steps", function testLatestNavigation() {
  const f = fixture(); f.navigation(); f.speech.start(); f.risk(); f.speech.start();
  f.navigation({progress: {step_index: 1, current_step: {type: 0}}});
  f.navigation({progress: {step_index: 2, current_step: {type: 1}}});
  f.risk([]); f.speech.end(); assert.match(f.speech.current.text, /^右转/); assert.equal(f.speech.requests.length, 3); f.manager.dispose();
});

// Functionality: Preserve completed-step dedup; Inputs: Completed nav then risk; Outputs: No duplicate; Logic: Only interruption reopens step; Constraints: No chatter.
test("clearing risk does not replay a navigation step that already completed", function testCompletedNavigation() {
  const f = fixture(); f.navigation(); f.speech.start(); f.speech.end(); f.risk(); f.speech.start(); f.risk([]); f.speech.end();
  assert.equal(f.speech.current, null); assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});

// Functionality: Permit short started warning completion; Inputs: Empty/stale perception; Outputs: No cut-off; Logic: Preserve existing behavior; Constraints: Explicit stop still cancels.
test("started risk finishes after empty and stale frames while queued risk expires", function testRiskCompletion() {
  const f = fixture(); f.risk(); f.speech.start(); f.risk([]); f.clock.advance(1200);
  assert.deepEqual(f.speech.attempts, []); assert.equal(f.manager.active.speech.kind, "risk"); f.speech.end(); f.manager.dispose();
});

// Functionality: Check distinct-target escalation; Inputs: Equal and upgraded competing risks; Outputs: Upgrade preemption; Logic: Server priority; Constraints: No TTC urgency override.
test("equal risk does not interrupt but a distinct target with higher algorithm priority does", function testEscalation() {
  const f = fixture(); f.risk(); f.speech.start(); f.risk([box({track_id: 8})]);
  assert.deepEqual(f.speech.attempts, []); f.risk([box({track_id: 8, risk_level: "critical", alert_priority: 4})]);
  assert.deepEqual(f.speech.attempts, [1]); assert.match(f.speech.current.text, /^危险/); f.manager.dispose();
});

/* Functionality: Coalesce repeated frames of the same currently announced target.
 * Inputs: Twelve fresh same-ID/equal-grade updates during one started sentence and its cooldown boundary.
 * Outputs: One complete sentence without cancellation, then one reminder after3000ms from start.
 * Logic: Repeated detections do not create extra driver requests or reset the actual-start cooldown.
 * Constraints: Synthetic events verify scheduling only; reminders are retained rather than disabled. */
test("repeated same-target frames never interrupt a sentence or reset its speech cooldown", function testSameTargetRepeats() {
  const f = fixture(); f.risk(); f.speech.start();
  for (let index = 0; index < 12; index++) { f.clock.advance(100); f.risk([box({ttc_sec: 2 - index / 10})]); }
  assert.equal(f.speech.requests.length, 1); assert.deepEqual(f.speech.attempts, []);
  assert.deepEqual(f.manager.histories.speech.get(7), {priority: 3, at: 100});
  f.speech.end(); assert.equal(f.speech.current, null);
  f.clock.advance(1799); f.risk(); assert.equal(f.speech.requests.length, 1);
  f.clock.advance(1); f.risk(); assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});

/* Functionality: Complete a same-target sentence before considering a current severity upgrade.
 * Inputs: Started high-risk speech followed by repeated critical frames for the same track.
 * Outputs: No truncated sentence, one latest critical follow-up and independent vibration escalation.
 * Logic: Retain actual speech-start identity; completion schedules only the latest still-valid risk.
 * Constraints: Algorithm grades remain unchanged and vibration acceptance cannot consume speech cooldown. */
test("same-target escalation finishes its current sentence then submits one current higher warning", function testSameTargetEscalation() {
  const f = fixture(); f.risk(); f.speech.start();
  const original = structuredClone(f.manager.active.speech.source);
  for (let index = 0; index < 12; index++) {
    f.clock.advance(50); f.risk([box({risk_level: "critical", alert_priority: 4})]);
  }
  assert.equal(f.speech.requests.length, 1); assert.deepEqual(f.speech.attempts, []);
  assert.deepEqual(f.manager.active.speech.source, original);
  assert.equal(f.manager.active.speech.confirmedTarget.track_id, 7);
  assert.equal(f.manager.active.speech.confirmedTarget.tracking_epoch, 1);
  assert.equal(Object.isFrozen(f.manager.active.speech.confirmedTarget), true);
  assert.equal(f.manager.histories.vibration.get(7).priority, 4);
  assert.equal(f.manager.histories.speech.get(7).priority, 3);
  f.speech.end(); assert.equal(f.speech.requests.length, 2);
  assert.match(f.speech.current.text, /^危险/);
  f.risk([box({risk_level: "critical", alert_priority: 4})]); assert.equal(f.speech.requests.length, 2);
  f.speech.start(); assert.equal(f.manager.histories.speech.get(7).priority, 4); f.speech.end();
  f.risk([box({risk_level: "critical", alert_priority: 4})]);
  assert.equal(f.speech.current, null); assert.equal(f.speech.requests.length, 2);
  assert.equal(f.events.filter(event => event.channel === "speech" && event.phase === "output_end").length, 2);
  f.manager.dispose();
});

/* Functionality: Avoid replaying an earlier same-target upgrade after its evidence changes.
 * Inputs: A transient critical update followed by current high risk, an empty frame or source expiry.
 * Outputs: The started high sentence completes with no queued critical follow-up.
 * Logic: Completion evaluates only the latest valid algorithm result, never a remembered peak grade.
 * Constraints: Same-grade cooldown and capture expiry remain unchanged; no risk history is fabricated. */
test("a transient same-target upgrade is not replayed after its latest risk recovers clears or expires", function testEscalationUsesLatestRisk() {
  for (const latest of [[box()], [], null]) {
    const f = fixture(); f.risk(); f.speech.start(); f.clock.advance(50);
    f.risk([box({risk_level: "critical", alert_priority: 4})]);
    if (latest === null) f.clock.advance(1200);
    else { f.clock.advance(50); f.risk(latest); }
    assert.deepEqual(f.speech.attempts, []); f.speech.end();
    assert.equal(f.speech.requests.length, 1); assert.equal(f.speech.current, null); f.manager.dispose();
  }
});

/* Functionality: Protect the actual confirming target when synthesis began for another track.
 * Inputs: Original track7 preparation, track8 semantic confirmation, then track8 critical escalation.
 * Outputs: Track8's sentence completes before one current escalation, preserving original source identity.
 * Logic: Completion ownership follows confirmed track/epoch rather than the synthesis descriptor's box.
 * Constraints: No source/track relabelling, obsolete severity replay or changed per-track cooldown. */
test("same-target completion follows the actual confirming track rather than the original synthesis target", function testConfirmedTargetEscalation() {
  const f = fixture(); f.risk(); const original = structuredClone(f.manager.active.speech.source);
  f.risk([box({track_id: 8})]); f.speech.start();
  assert.equal(f.manager.active.speech.box.track_id, 7);
  assert.equal(f.manager.active.speech.confirmedTarget.track_id, 8);
  f.risk([box({track_id: 8, risk_level: "critical", alert_priority: 4})]);
  assert.deepEqual(f.speech.attempts, []); assert.equal(f.speech.requests.length, 1);
  assert.deepEqual(f.manager.active.speech.source, original);
  assert.equal(f.manager.describe().active.speech.confirmed_track_id, 8);
  assert.equal(f.manager.describe().active.speech.confirmed_tracking_epoch, 1);
  f.speech.end(); assert.equal(f.speech.requests.length, 2);
  assert.equal(f.manager.active.speech.box.track_id, 8); assert.match(f.speech.current.text, /^危险/);
  f.speech.start(); f.speech.end(); assert.equal(f.speech.current, null); f.manager.dispose();
});

/* Functionality: Preserve preemption when the old synthesis target becomes a distinct urgent risk.
 * Inputs: Track7 synthesis confirmed by track8, then a critical track7 and still-high track8.
 * Outputs: Immediate interruption for track7's current higher priority.
 * Logic: Original synthesis identity is not treated as the actual playing sentence's target.
 * Constraints: Current algorithm priority is used unchanged; only manager-owned speech is cancelled. */
test("the old synthesis target can preempt a sentence actually confirmed by another track", function testConfirmedTargetPreemption() {
  const f = fixture(); f.risk(); f.risk([box({track_id: 8})]); f.speech.start();
  f.risk([box({risk_level: "critical", alert_priority: 4}), box({track_id: 8})]);
  assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.speech.requests.length, 2);
  assert.equal(f.manager.active.speech.box.track_id, 7); assert.match(f.speech.current.text, /^危险/);
  f.manager.dispose();
});

/* Functionality: Retain a distinct target's urgent interruption even behind a same-target upgrade.
 * Inputs: Playing high track7, then critical track7 ranked ahead of another critical track8.
 * Outputs: Owned high speech is interrupted so a current critical warning can begin preparation.
 * Logic: Same-target completion protection cannot hide a different higher-priority required risk.
 * Constraints: Selection and grading are unchanged; the new selected critical target may still be track7. */
test("a same-target upgrade cannot mask another target with higher priority than the playing sentence", function testOtherTargetEscalation() {
  const f = fixture(); f.risk(); f.speech.start();
  f.risk([box({risk_level: "critical", alert_priority: 4, ttc_sec: 0.5}),
    box({track_id: 8, risk_level: "critical", alert_priority: 4, ttc_sec: 1})]);
  assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.speech.requests.length, 2);
  assert.match(f.speech.current.text, /^危险/); f.manager.dispose();
});

/* Functionality: Separate reused track IDs across tracker epochs during started speech.
 * Inputs: Track7 high speech in epoch1, then track7 critical risk in epoch2.
 * Outputs: Immediate interruption and a new epoch2 request.
 * Logic: Actual target identity includes tracker epoch and cannot be inferred from a numeric ID alone.
 * Constraints: Tracker resets remain algorithm-owned; original speech provenance is not rewritten. */
test("a reused numeric track ID in a new tracking epoch is a distinct target for preemption", function testEscalationEpochIdentity() {
  const f = fixture(); f.risk(); f.speech.start();
  assert.equal(f.manager.active.speech.confirmedTarget.tracking_epoch, 1);
  f.manager.ingestRisk({sessionId: f.manager.riskSessionId, capturedAt: f.clock.now(),
    metadata: {index: 2, tracking_epoch: 2, boxes: [box({risk_level: "critical", alert_priority: 4})]}});
  assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.speech.requests.length, 2);
  assert.equal(f.manager.active.speech.source.tracking_epoch, 2);
  f.speech.start(); assert.equal(f.manager.active.speech.confirmedTarget.tracking_epoch, 2); f.manager.dispose();
});

// Functionality: Separate output cooldowns; Inputs: Foreign speech plus vibration start; Outputs: Later risk speech; Logic: Actual channel starts; Constraints: 3s unchanged.
test("vibration acceptance cannot consume speech cooldown and speech cooldown begins at actual start", function testCooldown() {
  const f = fixture(); f.speech.manual(); f.risk(); assert.equal(f.vibration.requests.length, 1);
  f.clock.advance(100); f.speech.end(); assert.equal(f.speech.requests.length, 1); f.speech.start(); f.speech.end();
  f.clock.advance(2900); f.risk(); assert.equal(f.speech.requests.length, 1);
  f.clock.advance(100); f.risk(); assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});

/* Functionality: Preserve independent vibration cooldown while speech uses semantic confirmation.
 * Inputs: Accepted risk vibration, expired source, and new fresh evidence around its3000ms cooldown.
 * Outputs: Original vibration track/time retained and a second pattern only after the original boundary.
 * Logic: Vibration descriptors do not carry speech language/voice and keep their original history path.
 * Constraints: API acceptance remains distinct from physical vibration or speech playback. */
test("semantic speech confirmation leaves original vibration start cooldown unchanged", function testVibrationCooldown() {
  const f = fixture(); f.risk();
  assert.deepEqual(f.manager.histories.vibration.get(7), {priority: 3, at: 100});
  assert.equal(f.manager.histories.speech.has(7), false);
  f.clock.advance(2900); f.risk(); assert.equal(f.vibration.requests.length, 1);
  f.clock.advance(100); f.risk(); assert.equal(f.vibration.requests.length, 2);
  assert.deepEqual(f.manager.histories.vibration.get(7), {priority: 3, at: 3100}); f.manager.dispose();
});

// Functionality: Verify independent clock domains; Inputs: Epoch GPS/monotonic capture; Outputs: Both accepted; Logic: True source TTL; Constraints: No receipt-age reset.
test("GPS epoch and capture monotonic origins stay separate and future captures are rejected", function testSourceClock() {
  const f = fixture(); f.navigation(); assert.equal(f.speech.requests.length, 1); f.speech.start(); f.speech.end();
  f.risk([box()], f.clock.now() + 1); assert.equal(f.speech.requests.length, 1);
  f.risk(); assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});

// Functionality: Check missing source expiry; Inputs: Unstarted speech and no new frame; Outputs: Cancelled; Logic: Capture TTL; Constraints: No extra synthesis.
test("queued risk is cancelled when the last original source frame expires", function testQueuedRisk() {
  const f = fixture(); f.risk(); f.clock.advance(1000);
  assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.speech.current, null);
  assert.equal(f.speech.requests.length, 1); f.manager.dispose();
});

/* Functionality: Prevent slow backend synthesis from starving a continuously confirmed risk warning.
 * Inputs: Matching original detections every 700ms and a delayed PCM start after 2100ms.
 * Outputs: One synthesis request and separate original/confirmed frame latency evidence.
 * Logic: Keep all source clocks unchanged while the latest detection confirms exactly the same text.
 * Constraints: Synthetic frames and driver events do not verify actual TTS or hardware audio. */
test("slow risk synthesis survives matching fresh frames without rewriting original provenance", function testSlowRiskSynthesis() {
  const f = fixture(); f.risk();
  const original = structuredClone(f.manager.active.speech.source);
  for (let index = 0; index < 3; index++) { f.clock.advance(700); f.risk(); }
  assert.equal(f.speech.requests.length, 1); assert.deepEqual(f.speech.attempts, []);
  assert.deepEqual(f.manager.active.speech.source, original);
  f.speech.start();
  const started = f.events.find(event => event.phase === "output_start" && event.channel === "speech");
  assert.equal(started.frame_index, original.frame_index);
  assert.equal(started.captured_at_ms, original.captured_at_ms);
  assert.equal(started.capture_to_start_ms, 2100);
  assert.equal(started.confirmed_frame_index, 4); assert.equal(started.confirmed_capture_to_start_ms, 0);
  f.manager.dispose();
});

/* Functionality: Prevent equal-priority TTC ranking changes from starving a still-current warning.
 * Inputs: Two original tracks retained in fresh frames every100ms, alternating TTC rank for2300ms.
 * Outputs: One synthesis followed by a start for the original track, with separate confirmation provenance.
 * Logic: The other track has a different direction and leads on the final frame, so neither its phrase
 * nor the selected-target identity may replace the still-valid queued warning.
 * Constraints: No target scoring changes, source clock reset or physical audio claim. */
test("slow risk synthesis survives equal-priority target reranking while its own track remains current", function testRiskReranking() {
  const f = fixture(); f.risk([box(), box({track_id: 8, direction: "front_right", ttc_sec: 2})]);
  const original = structuredClone(f.manager.active.speech.source), originalText = f.speech.current.text;
  for (let index = 1; index <= 23; index++) {
    f.clock.advance(100);
    f.risk([box({ttc_sec: index % 2 ? 2 : 1}),
      box({track_id: 8, direction: "front_right", ttc_sec: index % 2 ? 1 : 2})]);
  }
  assert.equal(f.speech.requests.length, 1); assert.deepEqual(f.speech.attempts, []);
  assert.equal(f.speech.current.text, originalText); assert.deepEqual(f.manager.active.speech.source, original);
  f.speech.start();
  const started = f.events.find(event => event.phase === "output_start" && event.channel === "speech");
  assert.equal(started.track_id, 7); assert.equal(started.frame_index, original.frame_index);
  assert.equal(started.captured_at_ms, original.captured_at_ms); assert.equal(started.capture_to_start_ms, 2300);
  assert.equal(started.confirmed_frame_index, 24); assert.equal(started.confirmed_capture_to_start_ms, 0);
  assert.equal(f.manager.histories.speech.has(7), true); assert.equal(f.manager.histories.speech.has(8), false);
  f.manager.dispose();
});

/* Functionality: Preserve immediate higher-priority preemption during slow synthesis.
 * Inputs: Still-valid original high-risk target and a fresh critical target in the same frame.
 * Outputs: Original request cancelled and critical warning submitted before playback.
 * Logic: Stable target confirmation never overrides global algorithm priority.
 * Constraints: The test supplies algorithm grades without recomputing risk or waiting for hardware. */
test("a higher global priority preempts queued speech even while its original target remains valid", function testQueuedEscalation() {
  const f = fixture(); f.risk(); f.clock.advance(100);
  f.risk([box(), box({track_id: 8, direction: "front_right", risk_level: "critical", alert_priority: 4})]);
  assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.speech.current.id, 2);
  assert.equal(f.manager.active.speech.box.track_id, 8); assert.match(f.speech.current.text, /^危险，右前方/);
  assert.equal(f.manager.active.speech.started, false);
  f.speech.start();
  assert.equal(f.events.filter(event => event.phase === "output_start" && event.channel === "speech").length, 1);
  f.manager.dispose();
});

/* Functionality: Confirm an identical prepared phrase against a different current target.
 * Inputs: Both tracks present, then only the competing track remains with the same spoken phrase.
 * Outputs: One original synthesis, separate actual confirmation identity and cooldown for that target.
 * Logic: The phrase describes the current eligible highest-priority target without claiming old track continuity.
 * Constraints: Original track/source/capture latency remain immutable; no obsolete phrase is played. */
test("a current matching target confirms prepared speech without relabelling its original provenance", function testRiskCrossTrackConfirmation() {
  const f = fixture(); f.risk([box(), box({track_id: 8, ttc_sec: 2})]); f.clock.advance(100);
  const original = structuredClone(f.manager.active.speech.source);
  f.risk([box({ttc_sec: 2}), box({track_id: 8})]); assert.deepEqual(f.speech.attempts, []);
  f.clock.advance(100); f.risk([box({track_id: 8})]);
  assert.deepEqual(f.speech.attempts, []); assert.equal(f.speech.current.id, 1);
  f.speech.start();
  const started = f.events.find(event => event.phase === "output_start" && event.channel === "speech");
  assert.equal(started.track_id, 7); assert.equal(started.confirmed_track_id, 8);
  assert.equal(started.frame_index, original.frame_index); assert.equal(started.capture_to_start_ms, 200);
  assert.equal(started.confirmed_frame_index, 3); assert.equal(started.confirmed_capture_to_start_ms, 0);
  assert.deepEqual(f.manager.active.speech.source, original);
  assert.equal(f.manager.histories.speech.has(7), false); assert.equal(f.manager.histories.speech.has(8), true);
  f.speech.end(); f.risk([box({track_id: 8})]); assert.equal(f.speech.requests.length, 1);
  f.clock.advance(2999); f.risk([box({track_id: 8})]); assert.equal(f.speech.requests.length, 1);
  f.clock.advance(1); f.risk([box({track_id: 8})]); assert.equal(f.speech.requests.length, 2);
  f.manager.dispose();
});

/* Functionality: Reject confirmation from a track that no longer qualifies for an algorithm alert.
 * Inputs: Original target with false alert_required, zero or absent priority, plus a differently phrased competitor.
 * Outputs: Preparation retained until first PCM, then cancelled with the valid target replacing it.
 * Logic: Exact text from an ineligible target cannot authorize physical audio scheduling.
 * Constraints: Caller contract validation remains upstream; these inputs cannot invent a new alert. */
test("an ineligible original target cannot retain queued speech when another valid alert remains", function testQueuedRiskTargetIneligible() {
  for (const change of [{alert_required: false}, {alert_priority: 0}, {alert_priority: undefined}]) {
    const f = fixture(); f.risk(); f.clock.advance(100);
    f.risk([box(change), box({track_id: 8, direction: "front_right"})]);
    assert.deepEqual(f.speech.attempts, []); f.speech.start();
    assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.speech.current.id, 2);
    assert.equal(f.manager.active.speech.box.track_id, 8); f.manager.dispose();
  }
});

/* Functionality: Reject prepared audio whose phrase or priority lacks current confirmation.
 * Inputs: Fresh changed priority, direction, category or approach suffix before first PCM.
 * Outputs: Preparation retained until audio arrives, then cancelled and replaced without an old start.
 * Logic: Target fluctuations are not playback approval; exact current semantic evidence is mandatory.
 * Constraints: No source timestamp mutation, risk scoring or retry after an output failure. */
test("changed risk meaning stays preparation until first PCM rejects the obsolete phrase", function testChangedRiskSynthesis() {
  for (const change of [
    {alert_priority: 2, risk_level: "medium"}, {direction: "front_right"}, {label: "car"}, {risk_reasons: []},
  ]) {
    const f = fixture(); f.risk(); f.clock.advance(100);
    f.risk([box(change)]);
    assert.deepEqual(f.speech.attempts, [], JSON.stringify(change)); assert.equal(f.speech.current.id, 1);
    f.speech.start();
    assert.deepEqual(f.speech.attempts, [1], JSON.stringify(change));
    assert.equal(f.speech.current.id, 2); assert.equal(f.manager.active.speech.source.frame_index, 2);
    assert.equal(f.events.some(event => event.phase === "output_start" && event.channel === "speech"), false);
    f.manager.dispose();
  }
});

/* Functionality: Permit slow synthesis to outlive transient per-frame target/meaning changes.
 * Inputs: Three fresh frames700ms apart with intervening direction/category changes and a final matching new track.
 * Outputs: One request starting after2100ms with original provenance and separate current confirmation.
 * Logic: Preparation is reusable only when the current phrase/priority is supported at first PCM.
 * Constraints: No risk cache, changed clock, old-risk playback or guarantee of provider timing. */
test("preparation survives transient semantic changes only if the current risk confirms it before playback", function testPreparationSemanticRecovery() {
  const f = fixture(); f.risk();
  f.clock.advance(700); f.risk([box({track_id: 8, direction: "front_right"})]);
  f.clock.advance(700); f.risk([box({track_id: 9, label: "car"})]);
  f.clock.advance(700); f.risk([box({track_id: 10})]);
  assert.equal(f.speech.requests.length, 1); assert.deepEqual(f.speech.attempts, []);
  f.speech.start();
  const started = f.events.find(event => event.phase === "output_start" && event.channel === "speech");
  assert.equal(started.track_id, 7); assert.equal(started.confirmed_track_id, 10);
  assert.equal(started.capture_to_start_ms, 2100); assert.equal(started.confirmed_frame_index, 4);
  assert.equal(started.confirmed_capture_to_start_ms, 0); f.manager.dispose();
});

/* Functionality: Recheck actual confirming-target cooldown before prepared audio can start.
 * Inputs: A recently spoken target, a different pending target, then only the cooled-down target remains.
 * Outputs: Pending audio cancelled with no second start and no cooldown attributed to the absent target.
 * Logic: Cross-track semantic confirmation cannot bypass start-based per-track cooldown.
 * Constraints: Cooldown duration and algorithm grades remain unchanged. */
test("an already cooled-down target cannot authorize a different target's prepared phrase", function testRiskConfirmationCooldown() {
  const f = fixture(); f.risk([box({track_id: 8})]); f.speech.start(); f.speech.end();
  f.risk(); assert.equal(f.speech.current.id, 2); f.clock.advance(100); f.risk([box({track_id: 8})]);
  assert.deepEqual(f.speech.attempts, []); f.speech.start();
  assert.deepEqual(f.speech.attempts, [2]); assert.equal(f.speech.current, null);
  assert.equal(f.speech.requests.length, 2); assert.equal(f.manager.histories.speech.has(7), false);
  assert.equal(f.events.filter(event => event.phase === "output_start" && event.channel === "speech").length, 1);
  f.manager.dispose();
});

/* Functionality: Reject prepared risk audio when fresh evidence no longer contains an alert.
 * Inputs: A queued warning followed by an empty fresh frame and first PCM.
 * Outputs: No eager synthesis churn, but cancellation before any physical audio start.
 * Logic: Freshness permits preparation while semantic confirmation remains independently mandatory.
 * Constraints: No lower-priority work is waiting in this case. */
test("fresh no-alert frames retain preparation but never authorize obsolete risk playback", function testNoAlertPreparation() {
  const f = fixture(); f.risk(); f.clock.advance(100); f.risk([]);
  assert.deepEqual(f.speech.attempts, []); assert.equal(f.speech.current.id, 1);
  f.speech.start(); assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.speech.current, null);
  assert.equal(f.events.some(event => event.phase === "output_start" && event.channel === "speech"), false);
  f.manager.dispose();
});

/* Functionality: Avoid letting no-alert preparation block available assistant/navigation speech.
 * Inputs: Risk preparation, pending lower-priority work and a fresh frame without alerts.
 * Outputs: Preparation cancelled and the existing queued reply or current navigation candidate submitted.
 * Logic: Yield only before risk playback when there is no current alert to retain priority.
 * Constraints: No provider timeout, automatic retry or new scheduling priority is introduced. */
test("no-alert risk preparation yields immediately to waiting assistant or navigation work", function testPreparationYields() {
  for (const kind of ["assistant", "navigation"]) {
    const f = fixture(); f.risk();
    if (kind === "assistant") f.manager.enqueueAssistantReply("已为你准备好路线。");
    else f.navigation();
    assert.deepEqual(f.speech.attempts, []); f.clock.advance(100); f.risk([]);
    assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.manager.active.speech.kind, kind);
    assert.equal(f.speech.current.id, 2);
    assert.equal(f.events.some(event => event.phase === "output_cancel" && event.reason === "risk_preparation_yield"), true);
    f.speech.start(); assert.equal(f.manager.active.speech.started, true); f.manager.dispose();
  }
});

/* Functionality: Cancel queued preparation immediately at epoch and voice boundaries.
 * Inputs: Fresh source from a new tracking epoch or an explicit unavailable voice selection.
 * Outputs: Old request cancelled before PCM, with a new epoch request or diagnosed voice failure.
 * Logic: Reusable phrases cannot cross source identity or submitted voice configuration.
 * Constraints: No fallback voice, channel recovery or changed target risk policy. */
test("tracking epoch and voice changes immediately invalidate queued risk preparation", function testPreparationBoundaries() {
  const f = fixture(); f.risk(); f.clock.advance(100);
  f.manager.ingestRisk({sessionId: f.manager.riskSessionId, capturedAt: f.clock.now(),
    metadata: {index: 2, tracking_epoch: 2, boxes: [box()]}});
  assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.speech.current.id, 2); f.manager.dispose();
  const g = fixture(); g.risk(); g.manager.configure({voiceId: "unavailable", language: "zh-CN"});
  assert.deepEqual(g.speech.attempts, [1]); assert.equal(g.speech.current, null);
  assert.equal(g.manager.describe().reasons.speech, "backend_voice_unavailable"); g.manager.dispose();
});

/* Functionality: Recheck source expiry before PCM playback even when a scheduled timer is delayed.
 * Inputs: Expired source and an explicit driver before_start event without timer execution.
 * Outputs: Cancelled request with no start event. Logic: The production manager pumps synchronously.
 * Constraints: Original one-second freshness remains strict at its boundary. */
test("risk cannot begin playback after source expiry even before the expiry timer runs", function testExpiredBeforePlayback() {
  const f = fixture(); f.risk(); f.clock.time += 1000; f.speech.start();
  assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.speech.current, null);
  assert.equal(f.events.some(event => event.phase === "output_start" && event.channel === "speech"), false);
  f.manager.dispose();
});

/* Functionality: Keep synthesized text tied to the interface language at actual playback.
 * Inputs: Fresh waiting Chinese warning and an English configuration update before first audio.
 * Outputs: Chinese request cancelled and a new English request. Logic: Exact phrase matching.
 * Constraints: Replacing source semantics does not claim that either driver request was audible. */
test("risk playback revalidates the exact phrase after an interface-language change", function testLanguageChangedBeforePlayback() {
  const f = fixture(); f.risk(); f.manager.configure({voiceId: "Cherry", language: "en-US"});
  assert.deepEqual(f.speech.attempts, [1]);
  assert.equal(f.speech.current.id, 2); assert.equal(f.speech.current.options.language, "en-US");
  assert.equal(f.manager.active.speech.started, false); f.speech.start();
  assert.equal(f.manager.active.speech.started, true); f.manager.dispose();
});

// Functionality: Preserve queued navigation provenance; Inputs: Newer same-step fix; Outputs: Old expires once; Logic: Original GPS deadline; Constraints: No same-step replacement loop.
test("queued navigation expires at original GPS time despite later same-step fixes", function testQueuedNavigation() {
  const f = fixture(); f.navigation(); f.clock.advance(14000); f.navigation(); f.clock.advance(1000);
  assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.speech.current, null); assert.equal(f.speech.requests.length, 1); f.manager.dispose();
});

// Functionality: Drop stale resume candidates; Inputs: Risk longer than nav validity; Outputs: No stale turn; Logic: Epoch gate; Constraints: No replay of old step.
test("navigation that expires during risk cannot resume after risk finishes", function testNavigationExpiry() {
  const f = fixture(); f.navigation(); f.speech.start(); f.risk(); f.speech.start(); f.clock.advance(15000); f.speech.end();
  assert.equal(f.speech.current, null); assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});

// Functionality: Check source-local termination; Inputs: Separate stop calls; Outputs: Other source retained; Logic: Own kind only; Constraints: No cross-source cleanup.
test("stopping camera preserves navigation and stopping navigation preserves risk", function testIndependentStops() {
  const f = fixture(); f.navigation(); f.speech.start(); f.manager.stopRisk(); assert.equal(f.manager.active.speech.kind, "navigation");
  f.risk(); f.speech.start(); f.manager.stopNavigation(); assert.equal(f.manager.active.speech.kind, "risk");
  assert.notEqual(f.vibration.current, null); f.manager.stopRisk(); assert.equal(f.speech.current, null); f.manager.dispose();
});

// Functionality: Check independent enablement; Inputs: Source toggles; Outputs: Other ownership retained; Logic: No global switch; Constraints: No driver enablement.
test("disabling one source cannot clear the other source or its driver switch", function testIndependentSwitches() {
  const f = fixture(); f.navigation(); f.speech.start(); f.manager.setRiskEnabled(false);
  assert.equal(f.manager.active.speech.kind, "navigation"); assert.equal(f.speech.enabled, true);
  f.manager.setRiskEnabled(true); f.risk(); f.speech.start(); f.manager.setNavigationEnabled(false);
  assert.equal(f.manager.active.speech.kind, "risk"); f.manager.dispose();
});

// Functionality: Protect foreign output; Inputs: Manual request plus sources; Outputs: No cancellation; Logic: Driver admission; Constraints: Latest-only after end.
test("manual and scene speech are never preempted by either automatic source", function testForeignSpeech() {
  const f = fixture(); const manual = f.speech.manual(); f.navigation(); f.risk();
  assert.equal(f.speech.current.id, manual); assert.deepEqual(f.speech.attempts, []);
  f.clock.advance(1100); f.speech.end(); assert.equal(f.manager.active.speech.kind, "navigation"); f.manager.dispose();
});

// Functionality: Protect pending foreign patterns; Inputs: Manual vibration and risk; Outputs: No replacement; Logic: Explicit ownership; Constraints: No automatic foreign stop.
test("foreign pending vibration is never replaced or cancelled", function testForeignVibration() {
  const f = fixture(); f.vibration.current = {id: ++f.vibration.sequence, name: "manual"};
  f.risk(); assert.equal(f.vibration.requests.length, 0); f.manager.stopRisk();
  assert.equal(f.vibration.current.name, "manual"); assert.deepEqual(f.vibration.attempts, []); f.manager.dispose();
});

// Functionality: Verify synchronous native lifecycle; Inputs: start/end in speak; Outputs: No phantom owner; Logic: Pending descriptor binding; Constraints: No fabricated timestamps.
test("synchronous submitted start and end bind correctly and deduplicate completed navigation", function testSynchronous() {
  const f = fixture(); f.speech.synchronous = "end"; f.navigation();
  assert.equal(f.manager.active.speech, null); f.navigation(); assert.equal(f.speech.requests.length, 1);
  assert.equal(f.events.filter(event => event.phase === "output_start").length, 1); f.manager.dispose();
});

// Functionality: Latch real output errors; Inputs: Synchronous error then data; Outputs: No retry; Logic: Explicit resume; Constraints: No channel fallback.
test("speech errors remain paused across both data streams until explicit resume", function testChannelFailure() {
  const f = fixture(); f.speech.synchronous = "error"; f.navigation(); assert.equal(f.manager.paused.speech, true);
  f.risk(); f.navigation(); assert.equal(f.speech.requests.length, 1);
  f.speech.synchronous = null; f.manager.resume("speech"); assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});

// Functionality: Preserve uncertain ownership; Inputs: Failed risk preemption and later data; Outputs: One attempt; Logic: Manual cleanup only; Constraints: No implicit retries.
test("failed preemption retains speech ID and cannot be cleared by resume or new sources", function testCancellationFailure() {
  const f = fixture(); f.navigation(); f.speech.start(); f.speech.cancelFailure = true; f.risk();
  assert.equal(f.manager.active.speech.requestId, 1); assert.equal(f.manager.paused.speech, true);
  f.clock.advance(1200); f.risk(); f.navigation({progress: {step_index: 1}}); f.manager.resume("speech");
  assert.deepEqual(f.speech.attempts, [1]); assert.equal(f.manager.paused.speech, true);
  f.speech.cancelFailure = false; f.manager.stopNavigation(); f.manager.resume("speech");
  assert.equal(f.manager.active.speech.kind, "risk"); f.manager.dispose();
});

// Functionality: Handle shared manual stop failure; Inputs: Error with native request retained; Outputs: Ownership/pause retained; Logic: Driver contract; Constraints: No false free state.
test("external cancel_failed events retain request ownership until explicit successful stop", function testExternalFailure() {
  const f = fixture(); f.risk(); f.speech.start(); f.speech.emit("error", 1, "cancel_failed");
  f.manager.resume("speech"); assert.equal(f.manager.active.speech.requestId, 1); assert.equal(f.manager.paused.speech, true);
  f.risk(); assert.deepEqual(f.speech.attempts, []); f.manager.stopRisk();
  assert.equal(f.manager.active.speech, null); assert.equal(f.manager.paused.speech, true); f.manager.resume("speech"); f.manager.dispose();
});

// Functionality: Distinguish manual cancellation; Inputs: Driver cancel event; Outputs: Paused source-independent channel; Logic: No implicit replay; Constraints: User resume required.
test("manual cancellation pauses speech without silently replaying either source", function testUserCancel() {
  const f = fixture(); f.navigation(); f.speech.stopOwned(1); f.risk(); f.navigation();
  assert.equal(f.manager.paused.speech, true); assert.equal(f.speech.requests.length, 1);
  f.manager.resume("speech"); assert.equal(f.manager.active.speech.kind, "risk"); f.manager.dispose();
});

// Functionality: Enforce the backend-only assist voice; Inputs: Missing/non-backend/wrong-language inventory; Outputs: No speech; Logic: Exact backend identity; Constraints: No local fallback.
test("missing and invalid backend voices cannot trigger local speech", function testVoice() {
  for (const voices of [[],
    [{id: "Cherry", language: "multi", languages: ["zh-CN", "en-US"], service: "native"}],
    [{id: "Cherry", language: "multi", languages: ["en-US"], service: "backend"}]]) {
    const f = fixture(); f.speech.voices = voices; f.navigation(); f.risk();
    assert.equal(f.speech.requests.length, 0); assert.equal(f.manager.paused.speech, true); f.manager.dispose();
  }
  const disabledBackend = fixture(); disabledBackend.speech.backendOnly = false;
  disabledBackend.navigation(); disabledBackend.risk();
  assert.equal(disabledBackend.speech.requests.length, 0); assert.equal(disabledBackend.manager.paused.speech, true);
  disabledBackend.manager.dispose();
});

// Functionality: Isolate tracker identities; Inputs: Same track after epoch reset; Outputs: Immediate new alert; Logic: History reset; Constraints: No algorithm mutation.
test("tracker epoch changes reset cooldown without affecting navigation identity", function testEpochReset() {
  const f = fixture(); f.navigation(); f.speech.start(); f.speech.end(); const run = f.manager.navigationRunId;
  f.risk(); f.speech.start(); f.speech.end();
  f.manager.ingestRisk({sessionId: f.manager.riskSessionId, capturedAt: f.clock.now(), metadata: {index: 9, tracking_epoch: 2, boxes: [box()]}});
  assert.equal(f.speech.requests.length, 3); assert.equal(f.manager.navigationRunId, run); f.manager.dispose();
});

// Functionality: Reject old source/callback generations; Inputs: Stop/newsession then old ID events; Outputs: Current ownership intact; Logic: Request/session match; Constraints: No resurrection.
test("late perception sessions and old speech events cannot affect a restarted owner", function testSessionIsolation() {
  const f = fixture(); f.risk(); f.manager.stopRisk(); f.manager.beginRisk("next");
  f.manager.ingestRisk({sessionId: "risk-session", capturedAt: f.clock.now(), metadata: {boxes: [box()], index: 3}});
  assert.equal(f.speech.requests.length, 1); f.risk(); const id = f.manager.active.speech.requestId;
  f.speech.emit("error", 1, "native_error"); f.speech.emit("end", 1);
  assert.equal(f.manager.active.speech.requestId, id); assert.equal(f.manager.paused.speech, false); f.manager.dispose();
});

// Functionality: Clear global ownership atomically; Inputs: Both active sources and stop; Outputs: No intermediate new request; Logic: Suspension; Constraints: Manual driver remains reusable.
test("global stop and dispose clear both sources without intermediate resubmission", function testGlobalStop() {
  const f = fixture(); f.navigation(); f.speech.start(); f.risk(); const count = f.speech.requests.length;
  f.manager.stop("page_hidden"); f.clock.advance(20000);
  assert.equal(f.speech.requests.length, count); assert.equal(f.manager.navigationRunId, null);
  assert.equal(f.manager.riskSessionId, null); assert.equal(f.clock.timers.size, 0);
  f.manager.dispose(); assert.equal(f.speech.listeners.size, 0); assert.equal(f.vibration.listeners.size, 0);
});

// Functionality: Exercise bridge-compatible interface; Inputs: navPort calls; Outputs: Independent state; Logic: Delegation; Constraints: Does not consume risk lifecycle.
test("navigation port exposes bridge lifecycle without clearing perception", function testPort() {
  const f = fixture(); f.risk(); f.manager.navPort.begin("bridge-run");
  f.manager.navPort.ingest(navigationSnapshot(f.clock.epochNow())); f.manager.navPort.stop("bridge_stop");
  assert.equal(f.manager.riskSessionId, "risk-session"); assert.equal(f.manager.active.speech.kind, "risk"); f.manager.dispose();
});

// Functionality: Keep diagnostics source-bound and private; Inputs: Risk/nav events; Outputs: Identity metadata only; Logic: Inspect actual reports; Constraints: No hardware claim.
test("events bind actual start to original source and omit speech bodies and coordinates", function testMetadata() {
  const f = fixture(); f.navigation(); f.speech.start(); f.risk(); const sourceIndex = f.manager.active.speech.source.frame_index;
  f.risk(); f.speech.start(); const event = f.events.findLast(item => item.phase === "output_start" && item.kind === "risk" && item.channel === "speech");
  assert.equal(event.frame_index, sourceIndex); const text = JSON.stringify({events: f.events, state: f.manager.describe()});
  assert.doesNotMatch(text, /103\.776543|1\.297654|Head east|危险，|警告，|开始沿规划路线/);
  assert.equal(f.manager.describe().hardware_outputs_verified, false); f.manager.dispose();
});

// Functionality: Accept original terminal notices; Inputs: Arrived inactive/error no fix; Outputs: Exact notice once; Logic: Existing formatter; Constraints: No false arrival inference.
test("arrival active false and terminal error without GPS use existing navigation notices", function testTerminalNavigation() {
  const f = fixture(); f.navigation({state: "arrived", active: false, progress: {current_step: {type: 10}}});
  assert.equal(f.speech.current.text, "已到达路线终点。"); f.speech.start(); f.speech.end();
  f.navigation({state: "arrived", active: false}); assert.equal(f.speech.requests.length, 1);
  f.navigation({state: "error", active: false, position: null, progress: null, reason: "position_stale"});
  assert.match(f.speech.current.text, /导航已停止/); f.manager.dispose();
});

// Functionality: Keep vibration failure independent and owned; Inputs: Failed cancel plus data; Outputs: Pause/no retry while speech works; Logic: Channel separation; Constraints: No motor proof.
test("vibration cancellation failure retains ownership and cannot pause or replay speech", function testVibrationFailure() {
  const f = fixture(); f.risk(); f.speech.start(); f.vibration.cancelFailure = true; f.risk([]);
  assert.equal(f.manager.paused.vibration, true); assert.equal(f.manager.paused.speech, false);
  f.clock.advance(1200); f.risk(); assert.deepEqual(f.vibration.attempts, [1]);
  f.manager.resume("vibration"); assert.equal(f.manager.paused.vibration, true);
  f.vibration.cancelFailure = false; f.manager.stopRisk(); f.manager.resume("vibration"); f.manager.dispose();
});


// Functionality: Distinguish grade labels from required output. Inputs: Existing flags. Outputs: Honest reason.
// Logic: Inspect without scheduling. Constraints: No invented medium-risk announcement.
test("risk speech diagnostics distinguish grade, alert flag and session", function testRiskSpeechReasons() {
  const f=fixture(); assert.equal(f.manager.riskSpeechStatus().state,"idle");
  f.manager.beginRisk("risk-session"); assert.equal(f.manager.riskSpeechStatus().state,"waiting");
  f.risk([box({risk_level:"medium",alert_required:false})]);
  assert.equal(f.manager.riskSpeechStatus().state,"no_alert");
  assert.equal(f.manager.riskSpeechStatus().alert_count,0); assert.equal(f.speech.requests.length,0);
  f.manager.setRiskEnabled(false); assert.equal(f.manager.riskSpeechStatus().state,"disabled"); f.manager.dispose();
});

// Functionality: Preserve exact capture freshness. Inputs: Expired/future/fresh timestamps. Outputs: Reasons.
// Logic: No receipt-time refresh. Constraints: Diagnostics never submit or grant playback.
test("risk speech diagnostics preserve one-second and future capture rejection", function testRiskSpeechDeadlines() {
  const f=fixture(); f.risk([box()],f.clock.now()-1000);
  assert.equal(f.manager.riskSpeechStatus().state,"stale"); assert.equal(f.manager.riskSpeechStatus().alert_count,1);
  assert.equal(f.manager.riskSpeechStatus().capture_age_ms,1000); assert.equal(f.speech.requests.length,0);
  f.risk([box()],f.clock.now()+1); assert.equal(f.manager.riskSpeechStatus().state,"stale");
  f.risk([box()],f.clock.now()-999); assert.equal(f.manager.riskSpeechStatus().state,"preparing");
  f.clock.advance(1); assert.equal(f.manager.riskSpeechStatus().state,"stale"); f.manager.dispose();
});

// Functionality: Distinguish scheduled/completed/paused speech. Inputs: Driver lifecycle. Outputs: Current state.
// Logic: Respect sentence completion and start-based cooldown. Constraints: No audible-device claim.
test("risk speech diagnostics track playback, cooldown and explicit pause", function testRiskSpeechOwnership() {
  const f=fixture(); f.risk(); f.speech.start(); assert.equal(f.manager.riskSpeechStatus().state,"playing");
  f.clock.advance(1100); assert.equal(f.manager.riskSpeechStatus().state,"playing");
  f.risk(); f.speech.end(); assert.equal(f.manager.riskSpeechStatus().state,"cooldown");
  f.manager.pause("speech","audio_context_interrupted");
  assert.equal(f.manager.riskSpeechStatus().state,"paused");
  assert.equal(f.manager.riskSpeechStatus().pause_reason,"audio_context_interrupted"); f.manager.dispose();
});

// Functionality: Publish expiry even when frames stop arriving. Inputs: One source and timer. Outputs: One transition.
// Logic: Deduplicate by finite reason. Constraints: No repeated per-frame diagnostic event or capture rewrite.
test("risk speech reason updates on expiry without flooding repeated frames", function testRiskSpeechTransition() {
  const f=fixture(); f.risk([box({alert_required:false})]); f.risk([box({alert_required:false})]);
  const notices=()=>f.events.filter(e=>e.phase==="risk_speech_state");
  assert.equal(notices().filter(e=>e.reason==="no_alert").length,1);
  f.clock.advance(1000); assert.equal(notices().at(-1).reason,"stale");
  const prior=f.speech.requests.length; f.manager.describe(); f.manager.riskSpeechStatus();
  assert.equal(f.speech.requests.length,prior); f.manager.dispose();
});

test("explicit two-second demo policy accepts delayed risk and expires before playback", () => {
  const f = fixture(true, 2000);
  f.clock.advance(2000);
  f.risk([box()], f.clock.now() - 1500);
  assert.equal(f.manager.riskSpeechStatus().state, "preparing");
  assert.equal(f.speech.requests.length, 1);
  f.speech.start();
  assert.equal(f.manager.riskSpeechStatus().state, "playing");
  f.manager.dispose();
  const expired = fixture(true, 2000);
  expired.clock.advance(2000);
  expired.risk([box()], expired.clock.now() - 1950);
  expired.clock.advance(50);
  assert.equal(expired.manager.riskSpeechStatus().state, "stale");
  assert.equal(expired.events.filter(event => event.phase === "output_start" && event.channel === "speech").length, 0);
  expired.manager.dispose();
});

/* Functionality: Announce an initially green light and avoid repeating unchanged source frames.
 * Inputs: Fresh untracked green signal frames. Outputs: One exact prepared sentence.
 * Logic: Complete once, then refresh beyond ordinary risk cooldown. Constraints: No TTC needed. */
test("initial green signals use prepared speech once despite continuous frames", function testInitialSignal() {
  const f = fixture(); f.risk([signalBox("green")]);
  assert.equal(f.speech.current.text, "绿灯，请通行。");
  assert.equal(f.speech.current.options.purpose, "risk");
  assert.equal(f.manager.active.speech.kind, "signal");
  f.speech.start(); f.speech.end();
  for (let index = 0; index < 25; index++) { f.clock.advance(200); f.risk([signalBox("green")]); }
  assert.equal(f.speech.requests.length, 1); assert.equal(f.vibration.requests.length, 0);
  f.manager.configure({voiceId: "Cherry", language: "en-US"});
  assert.equal(f.speech.current.text, "Green light. Please proceed.");
  f.speech.start(); f.speech.end(); f.risk([signalBox("green")]);
  assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});

/* Functionality: Announce red/green transitions once and revoke an obsolete started instruction.
 * Inputs: Same-frame-authority colour changes. Outputs: Current exact sentence without channel pause.
 * Logic: Intentional replacement differs from a driver error. Constraints: No colour calculation. */
test("red to green and green to red replace obsolete signal speech", function testSignalColourChanges() {
  const f = fixture(); f.risk([signalBox("red")]);
  assert.equal(f.speech.current.text, "前方是红灯，请等待。");
  f.speech.start(); const red = f.speech.current.id;
  f.risk([signalBox("green")]);
  assert.deepEqual(f.speech.attempts, [red]); assert.equal(f.speech.current.text, "绿灯，请通行。");
  f.speech.start(); const green = f.speech.current.id;
  f.risk([signalBox("red")]);
  assert.deepEqual(f.speech.attempts, [red, green]); assert.equal(f.speech.current.text, "前方是红灯，请等待。");
  assert.equal(f.manager.paused.speech, false);
  f.speech.start(); f.speech.end(); f.risk([signalBox("red")]);
  assert.equal(f.speech.requests.length, 3); f.manager.dispose();
});

/* Functionality: Cancel green on unknown, ambiguous, nonfront or stale evidence.
 * Inputs: Independent source invalidations. Outputs: No residual green request or automatic error recovery.
 * Logic: Source permission applies during the complete signal sentence. Constraints: No safe-road claim. */
test("green speech never survives lost or expired signal evidence", function testSignalInvalidation() {
  for (const boxes of [[signalBox("unknown")], [signalBox("green", {front_light: false})],
    [signalBox("green"), signalBox("red")], [signalBox("green", {light_scores: null})], []]) {
    const f = fixture(); f.risk([signalBox("green")]); f.speech.start();
    f.risk(boxes); assert.equal(f.speech.current, null); assert.equal(f.speech.requests.length, 1);
    assert.equal(f.manager.paused.speech, false); f.manager.dispose();
  }
  const stale = fixture(); stale.risk([signalBox("green")]); stale.speech.start();
  stale.clock.advance(1000); assert.equal(stale.speech.current, null);
  assert.equal(stale.manager.describe().signal_state, null); stale.manager.dispose();
});

/* Functionality: Recheck fresh signal semantics at the exact first-PCM gate.
 * Inputs: Current source changes without a separate scheduling callback. Outputs: No stale green start.
 * Logic: before_start pumps current evidence. Constraints: Driver events do not establish audibility. */
test("queued green is confirmed again before first PCM", function testSignalBeforeStart() {
  const f = fixture(); f.risk([signalBox("green")]);
  const old = f.speech.current.id;
  f.manager.latestRisk.metadata.boxes = [signalBox("red")];
  f.speech.start();
  assert.equal(f.events.filter(event => event.phase === "output_start" && event.request_id === old).length, 0);
  assert.equal(f.speech.current.text, "前方是红灯，请等待。");
  f.speech.start(); f.speech.end(); f.manager.dispose();
});

/* Functionality: Keep obstacle warnings first while permitting signal speech in their cooldown gap.
 * Inputs: Red light plus independent algorithm-required hazards. Outputs: Risk/signal priority and resume.
 * Logic: Interrupted signal completion stays pending. Constraints: Original warning grade unchanged. */
test("risk preempts signals and an interrupted fresh signal resumes completely", function testSignalPriority() {
  const f = fixture(); f.risk([signalBox(), box()]);
  assert.equal(f.manager.active.speech.kind, "risk"); f.speech.start(); f.speech.end();
  assert.equal(f.manager.active.speech.kind, "signal"); f.speech.start();
  f.risk([signalBox(), box({track_id: 8, risk_level: "critical", alert_priority: 4})]);
  assert.equal(f.manager.active.speech.kind, "risk"); assert.match(f.speech.current.text, /^危险/);
  f.speech.start(); f.speech.end();
  assert.equal(f.manager.active.speech.kind, "signal"); assert.equal(f.speech.current.text, "前方是红灯，请等待。");
  f.speech.start(); f.speech.end();
  assert.equal(f.speech.requests.filter(item => item.text === "前方是红灯，请等待。").length, 2);
  assert.equal(f.speech.current, null); f.manager.dispose();
});

/* Functionality: Protect manual output and insert signals before assistant/navigation speech.
 * Inputs: Explicit foreign request and existing owned sources. Outputs: Independent ownership order.
 * Logic: Only manager-owned output may be replaced. Constraints: No implicit audio activation. */
test("signals preserve manual speech and arbitrate through the existing owner", function testSignalManualOwnership() {
  const f = fixture(); const manual = f.speech.manual(); f.risk([signalBox("green")]);
  assert.equal(f.speech.current.id, manual); assert.equal(f.speech.requests.length, 0);
  f.speech.end(); assert.equal(f.manager.active.speech.kind, "signal");
  f.speech.start(); f.speech.end(); f.manager.dispose();
  const g = fixture(); g.navigation(); g.manager.enqueueAssistantReply("Current assistant reply.");
  g.risk([signalBox("red")]); assert.equal(g.manager.active.speech.kind, "signal");
  g.speech.start(); g.speech.end(); assert.equal(g.manager.active.speech.kind, "assistant");
  g.speech.start(); g.speech.end(); assert.equal(g.manager.active.speech.kind, "navigation");
  g.manager.dispose();
});

/* Functionality: Scope signal encounters to perception generations, tracker epochs and global stop.
 * Inputs: Explicit source and lifecycle boundaries. Outputs: Cancellation and fresh future encounters.
 * Logic: The same colour can announce in a new session. Constraints: No automatic source restart. */
test("signal ownership is cleared on stop, session and epoch boundaries", function testSignalLifecycle() {
  const f = fixture(); f.risk([signalBox("green")]); f.speech.start(); f.speech.end();
  f.manager.ingestRisk({sessionId: f.manager.riskSessionId, capturedAt: f.clock.now(),
    metadata: {index: 2, tracking_epoch: 2, boxes: [signalBox("green")]}});
  assert.equal(f.speech.requests.length, 2); f.manager.stopRisk(); assert.equal(f.speech.current, null);
  f.risk([signalBox("green")]); assert.equal(f.speech.requests.length, 3);
  f.manager.stop("page_hidden"); assert.equal(f.speech.current, null);
  assert.equal(f.manager.riskSessionId, null); assert.equal(f.manager.latestSignal, null);
  f.clock.advance(5000); assert.equal(f.speech.requests.length, 3); f.manager.dispose();
});

/* Functionality: Preserve explicit recovery after signal output or cancellation failure.
 * Inputs: Driver failures and fresh changed colours. Outputs: Paused ownership without retries.
 * Logic: New frames cannot clear failure latches. Constraints: Recovery is a user action. */
test("signal driver and cancellation failures require explicit recovery", function testSignalFailure() {
  const f = fixture(); f.risk([signalBox("green")]); f.speech.error();
  f.risk([signalBox("red")]); assert.equal(f.speech.requests.length, 1); assert.equal(f.manager.paused.speech, true);
  f.manager.resume("speech"); assert.equal(f.speech.current.text, "前方是红灯，请等待。"); f.manager.dispose();
  const g = fixture(); g.risk([signalBox("green")]); g.speech.start(); g.speech.cancelFailure = true;
  g.risk([signalBox("red")]); assert.equal(g.manager.paused.speech, true); assert.equal(g.speech.attempts.length, 1);
  g.risk([signalBox("red")]); g.risk([signalBox("unknown")]); assert.equal(g.speech.attempts.length, 1);
  g.speech.cancelFailure = false; g.manager.stop(); assert.equal(g.speech.current, null); g.manager.dispose();
});

/* Functionality: Ignore short occlusion without forgetting a completed colour, then bound the encounter.
 * Inputs: Unknown frames and the original TTL. Outputs: Same-colour dedup or new encounter after loss.
 * Logic: Reuse capture freshness instead of an additional recognition threshold. Constraints: No stale green. */
test("short signal occlusion deduplicates but a lost encounter can announce again", function testSignalEncounter() {
  const f = fixture(); f.risk([signalBox("green")]); f.speech.start(); f.speech.end();
  f.clock.advance(200); f.risk([signalBox("unknown")]);
  f.clock.advance(200); f.risk([signalBox("green")]); assert.equal(f.speech.requests.length, 1);
  f.clock.advance(200); f.risk([signalBox("unknown")]);
  f.clock.advance(1000); f.risk([signalBox("green")]); assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});

/* Functionality: Isolate signal dedup and colour state for three independent assistance pages.
 * Inputs: Three explicit manager instances. Outputs: Independent initial and changed utterances.
 * Logic: No module-level transition state. Constraints: This is not physical phone/audio testing. */
test("three page managers never share signal colour or completion state", function testSignalInstances() {
  const pages = [fixture(), fixture(), fixture()];
  for (const f of pages) { f.risk([signalBox("red")]); f.speech.start(); f.speech.end(); }
  pages[0].risk([signalBox("green")]);
  assert.equal(pages[0].speech.current.text, "绿灯，请通行。");
  assert.equal(pages[1].speech.current, null); assert.equal(pages[2].speech.current, null);
  for (const f of pages) f.manager.dispose();
});

/* Functionality: Keep algorithm-required vibration while avoiding a generic obstacle name for a signal.
 * Inputs: Signal with existing risk flags. Outputs: Signal speech plus original vibration pattern.
 * Logic: Separate spoken semantics from unchanged risk metadata. Constraints: No risk reclassification. */
test("pedestrian light speech is semantic while risk vibration stays unchanged", function testSignalVibration() {
  const f = fixture(); const light = signalBox("red", {track_id: 5, risk_level: "critical", alert_priority: 4, alert_required: true});
  f.risk([light]); assert.equal(f.speech.current.text, "前方是红灯，请等待。");
  assert.equal(f.vibration.current.name, "long"); assert.equal(light.alert_priority, 4);
  assert.equal(f.manager.latestRisk.metadata.boxes[0].alert_required, true); f.manager.dispose();
});
