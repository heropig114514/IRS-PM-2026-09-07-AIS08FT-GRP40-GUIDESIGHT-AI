/* Responsibilities: Independently verify unified risk/navigation scheduling and driver ownership.
 * Implementation: Separate epoch/monotonic clocks, explicit source snapshots and synchronous or
 * deferred fake-driver events exercise the production manager without sensors or native output.
 * Related Modules: integration/assist-guidance.js, integration/video-alerts.js,
 * integration/navigation-speech.js. No algorithm implementation or threshold is substituted.
 * Declaration Index: Clock; Clock.constructor; Clock.now; Clock.epochNow; Clock.setTimer;
 * Clock.clearTimer; Clock.advance; Speech; Speech.constructor; Speech.subscribe; Speech.emit;
 * Speech.describe; Speech.voiceList; Speech.speak; Speech.start; Speech.end; Speech.error;
 * Speech.manual; Speech.stopOwned; Vibration; Vibration.constructor; Vibration.subscribe;
 * Vibration.emit; Vibration.describe; Vibration.testPattern; Vibration.stopOwned;
 * box; navigationSnapshot; fixture; fixture.risk; fixture.navigation;
 * testDisabled; testNavigationOnly; testAssistantReplyQueue; testAssistantReplyPriority;
 * testRiskOnly; testPreemption; testLatestNavigation;
 * testCompletedNavigation; testRiskCompletion; testEscalation; testCooldown; testSourceClock;
 * testQueuedRisk; testQueuedNavigation; testNavigationExpiry; testIndependentStops;
 * testIndependentSwitches; testForeignSpeech; testForeignVibration; testSynchronous;
 * testChannelFailure; testCancellationFailure; testExternalFailure; testUserCancel;
 * testVoice; testEpochReset; testSessionIsolation; testGlobalStop; testPort; testMetadata;
 * testTerminalNavigation; testVibrationFailure.
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

/* Functionality: Model one shared speech driver with independent native events.
 * Logic: Preserve request IDs, explicit voice and manual ownership; cancellation can remain uncertain.
 * Constraints: No synthesis, permission grant, actual engine or audible output. */
class Speech {
  /* Functionality: Initialize driver state. Inputs: Clock. Outputs: Enabled fixture.
   * Logic: Settings and outcomes are independently controllable. Constraints: Manager stays opt-in. */
  constructor(clock) {
    Object.assign(this, {clock, current: null, lastRequest: null, sequence: 0, listeners: new Set(), requests: [],
      attempts: [], enabled: true, cancelFailure: false, refusal: null, synchronous: null,
      rate: {value: "2"}, volume: {value: "1"},
      voices: [{id: "local-zh", language: "zh-CN", local_service: true}]});
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
    this.current = {id, text, options, started: false}; this.lastRequest = {id, ...options};
    this.requests.push(this.current); this.emit("submitted");
    if (this.synchronous) this.start();
    if (this.synchronous === "end") this.end();
    if (this.synchronous === "error") this.error();
    return {accepted: true, last_request: {id}};
  }
  /* Functionality: Start current utterance. Inputs: Current. Outputs: Native-style event.
   * Logic: Mark before dispatch. Constraints: Not audible proof. */
  start() { this.current.started = true; this.emit("start"); }
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
function fixture(enable = true) {
  const clock = new Clock(), speech = new Speech(clock), vibration = new Vibration(clock), events = [];
  const manager = new AssistGuidanceManager({speech, vibration, now: clock.now, epochNow: clock.epochNow,
    setTimer: clock.setTimer, clearTimer: clock.clearTimer});
  manager.subscribe(event => events.push(event)); manager.configure({voiceId: "local-zh", language: "zh-CN"});
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

// Functionality: Check explicit admission; Inputs: Both sources; Outputs: No output; Logic: Opt in separately; Constraints: No device grant.
test("both automatic sources default disabled", function testDisabled() {
  const f = fixture(false); f.risk(); f.navigation(); assert.equal(f.speech.requests.length, 0);
  assert.equal(f.vibration.requests.length, 0); assert.deepEqual(f.manager.describe().paused, {speech: false, vibration: false}); f.manager.dispose();
});

// Functionality: Check navigation-only scheduling; Inputs: Same-step GPS; Outputs: One policy utterance; Logic: Dedup completion; Constraints: No camera requirement.
test("navigation works alone and completed same-step GPS never repeats", function testNavigationOnly() {
  const f = fixture(); f.navigation(); assert.equal(f.speech.requests[0].text, navigationSpeechText(f.manager.latestNavigation, "zh-CN"));
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
    assert.equal(request.options.voice_id, "local-zh");
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
  assert.equal(f.vibration.requests[0].name, "double"); assert.equal(f.speech.requests[0].options.rate, 2); f.manager.dispose();
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

// Functionality: Check escalation only; Inputs: Equal/downgraded/upgraded risks; Outputs: Upgrade preemption; Logic: Server priority; Constraints: No TTC urgency override.
test("equal risk does not interrupt but a higher algorithm priority does", function testEscalation() {
  const f = fixture(); f.risk(); f.speech.start(); f.risk([box({track_id: 8})]);
  assert.deepEqual(f.speech.attempts, []); f.risk([box({risk_level: "critical", alert_priority: 4})]);
  assert.deepEqual(f.speech.attempts, [1]); assert.match(f.speech.current.text, /^危险/); f.manager.dispose();
});

// Functionality: Separate output cooldowns; Inputs: Foreign speech plus vibration start; Outputs: Later risk speech; Logic: Actual channel starts; Constraints: 3s unchanged.
test("vibration acceptance cannot consume speech cooldown and speech cooldown begins at actual start", function testCooldown() {
  const f = fixture(); f.speech.manual(); f.risk(); assert.equal(f.vibration.requests.length, 1);
  f.clock.advance(100); f.speech.end(); assert.equal(f.speech.requests.length, 1); f.speech.start(); f.speech.end();
  f.clock.advance(2900); f.risk(); assert.equal(f.speech.requests.length, 1);
  f.clock.advance(100); f.risk(); assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});

// Functionality: Verify independent clock domains; Inputs: Epoch GPS/monotonic capture; Outputs: Both accepted; Logic: True source TTL; Constraints: No receipt-age reset.
test("GPS epoch and capture monotonic origins stay separate and future captures are rejected", function testSourceClock() {
  const f = fixture(); f.navigation(); assert.equal(f.speech.requests.length, 1); f.speech.start(); f.speech.end();
  f.risk([box()], f.clock.now() + 1); assert.equal(f.speech.requests.length, 1);
  f.risk(); assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});

// Functionality: Check original risk deadline; Inputs: Unstarted speech and newer frame; Outputs: Old cancelled; Logic: Capture TTL; Constraints: Fresh new frame may submit once.
test("queued risk is cancelled at its original capture deadline", function testQueuedRisk() {
  const f = fixture(); f.risk(); f.clock.advance(900); f.risk(); f.clock.advance(100);
  assert.ok(f.speech.attempts.includes(1)); assert.notEqual(f.manager.active.speech?.requestId, 1); f.manager.dispose();
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

// Functionality: Enforce explicit local voice; Inputs: Missing/remote/wrong language; Outputs: No speech; Logic: Native identity validation; Constraints: No replacement.
test("unavailable remote and wrong-language voices cannot be substituted", function testVoice() {
  for (const voices of [[], [{id: "local-zh", language: "zh-CN", local_service: false}], [{id: "local-zh", language: "en-US", local_service: true}]]) {
    const f = fixture(); f.speech.voices = voices; f.navigation(); f.risk();
    assert.equal(f.speech.requests.length, 0); assert.equal(f.manager.paused.speech, true); f.manager.dispose();
  }
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
