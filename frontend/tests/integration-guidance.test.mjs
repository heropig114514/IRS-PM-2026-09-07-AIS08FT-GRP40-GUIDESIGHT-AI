/** Responsibilities: Verify latest-only guidance, source TTL, output-start cooldown and ownership.
 * Implementation: Independent event-driven device fixtures and deterministic monotonic timers.
 * Related Modules: integration/guidance.js/events.js; physical camera/GPU/voice/motor verification is separate.
 * Declaration Index: Clock; Clock.constructor; Clock.now; Clock.setTimer; Clock.clearTimer; Clock.advance;
 * Speech; Speech.constructor; Speech.subscribe; Speech.describe; Speech.voiceList; Speech.speak;
 * Speech.start; Speech.end; Speech.error; Speech.stopOwned;
 * Vibration; Vibration.constructor; Vibration.subscribe; Vibration.describe; Vibration.testPattern; Vibration.stopOwned;
 * fixture; fixture.frame; box; testSelection; testPrerequisites; testCaptureTtl; testCooldown;
 * testNavigationPreemption; testManualOwnership; testLatest; testErrorRecovery; testNavigationExpiry; testCleanup;
 * testUserCancel; testTrackerEpoch; testVoiceDisappeared; testBothChannels; testSourceBinding; testQueuedDeadline; testLateSession;
 * testTimerReceiver; testTimerReceiver.browserSetTimeout; testTimerReceiver.browserClearTimeout;
 * testManualVibrationFirst; testManualVibrationReplacement; testOwnVibrationReplacement;
 * Variable Index: VOICES is explicit local/remote inventory; fixture state is isolated and contains no real sensors/network.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {EventBus} from "../integration/events.js";
import {GuidanceManager, selectRisk} from "../integration/guidance.js";
const VOICES = [{id: "local-zh", language: "zh-CN", local_service: true},
  {id: "remote-zh", language: "zh-CN", local_service: false},
  {id: "local-en", language: "en-US", local_service: true}];

/** Functionality: Control time/timer order. Logic: run due timers in chronological order.
 * Constraints: not a latency benchmark or browser timer accuracy measurement. */
class Clock {
  /** Functionality: Initialize clock. Inputs: None. Outputs: isolated state.
   * Logic: start at100ms; Constraints: no real waiting. */
  constructor() { this.time = 100; this.timers = new Map(); this.sequence = 0; }
  /** Functionality: Read current time. Inputs: state. Outputs: milliseconds.
   * Logic: direct read; Constraints: monotonically advanced only by test. */
  now = () => this.time;
  /** Functionality: Register timer. Inputs: callback/delay. Outputs: id.
   * Logic: absolute due time; Constraints: no implicit interval/retry. */
  setTimer = (callback, delay) => { const id = ++this.sequence; this.timers.set(id, {callback, at: this.time + delay}); return id; };
  /** Functionality: Cancel timer. Inputs: id. Outputs: state removal.
   * Logic: Map delete; Constraints: absent IDs harmless. */
  clearTimer = id => this.timers.delete(id);
  /** Functionality: Advance clock. Inputs: target milliseconds. Outputs: due callback effects.
   * Logic: honor timer time before jumping to target; Constraints: tests cannot run time backwards. */
  advance(target) {
    assert.ok(target >= this.time);
    while (true) {
      const due = [...this.timers.entries()].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.timers.delete(due[0]); this.time = due[1].at; due[1].callback();
    }
    this.time = target;
  }
}
/** Functionality: Model a single busy speech controller with independent start/end events.
 * Logic: submitted is not started; ownership reflects request ID. Constraints: no physical TTS. */
class Speech {
  /** Functionality: Initialize fixture. Inputs: clock. Outputs: driver state.
   * Logic: enabled explicit fixture; Constraints: manager still defaults disabled. */
  constructor(clock) { Object.assign(this, {clock, events: new EventBus(), enabled: true, current: null,
    requests: [], sequence: 0, cancels: [], voices: [...VOICES]}); }
  /** Functionality: Subscribe. Inputs: listener. Outputs: unsubscribe.
   * Logic: fixture event bus; Constraints: no automatic completion. */
  subscribe(listener) { return this.events.subscribe(listener); }
  /** Functionality: Inspect prerequisites/busy state. Inputs: state. Outputs: controller contract.
   * Logic: no text body; Constraints: supported is a fixture assertion. */
  describe() { return {supported: true, enabled: this.enabled, active_request: !!this.current,
    last_request: this.current ? {id: this.current.id} : null}; }
  /** Functionality: Return independent voices. Inputs: fixture state. Outputs: inventory.
   * Logic: copied list; Constraints: identities explicitly synthetic. */
  voiceList() { return {accepted: true, voices: [...this.voices]}; }
  /** Functionality: Submit without starting. Inputs: text/options. Outputs: acknowledgement.
   * Logic: preserve request content to verify navigation resume, emit submitted synchronously.
   * Constraints: fixture rejects overlapping submissions. */
  speak(text, options) {
    assert.equal(this.current, null);
    this.current = {id: ++this.sequence, text, options}; this.requests.push(this.current);
    this.events.publish({channel: "speech", phase: "submitted", request_id: this.current.id, at: this.clock.now()});
    return {accepted: true, last_request: {id: this.current.id}};
  }
  /** Functionality: Trigger actual start event. Inputs: current request. Outputs: event.
   * Logic: deferred start timestamp; Constraints: not speaker verification. */
  start() { this.events.publish({channel: "speech", phase: "start", request_id: this.current.id, at: this.clock.now()}); }
  /** Functionality: Complete current speech. Inputs: request. Outputs: free driver and event.
   * Logic: clear before callback; Constraints: no real audio. */
  end() { const id = this.current.id; this.current = null; this.events.publish({channel: "speech", phase: "end", request_id: id, at: this.clock.now()}); }
  /** Functionality: Emit native error. Inputs: request. Outputs: failed/free state.
   * Logic: finite error code; Constraints: no automatic retry. */
  error() { const id = this.current.id; this.current = null; this.events.publish({channel: "speech", phase: "error", request_id: id, reason: "native_error", at: this.clock.now()}); }
  /** Functionality: Cancel matching ownership. Inputs: id. Outputs: acknowledgement.
   * Logic: refuse another request; Constraints: manual outputs cannot be canceled by guidance. */
  stopOwned(id) {
    if (this.current?.id !== id) return {accepted: false, reason: "not_owned"};
    this.cancels.push(id); this.current = null;
    this.events.publish({channel: "speech", phase: "cancel", request_id: id, at: this.clock.now()});
    return {accepted: true};
  }
}
/** Functionality: Model API-accepted vibration with request identity.
 * Logic: immediate start only means API accepted. Constraints: no motor state inference. */
class Vibration {
  /** Functionality: Initialize fixture. Inputs: clock. Outputs: state.
   * Logic: disabled channel initially; Constraints: test explicitly enables it. */
  constructor(clock) { Object.assign(this, {clock, events: new EventBus(), enabled: false,
    current: null, sequence: 0, requests: [], cancels: [], reject: false}); }
  /** Functionality: Subscribe. Inputs: listener. Outputs: unsubscribe.
   * Logic: local bus; Constraints: no hardware events. */
  subscribe(listener) { return this.events.subscribe(listener); }
  /** Functionality: Inspect controller. Inputs: state. Outputs: pending request and identity contract.
   * Logic: match the real driver's accepted-but-not-cancelled record; Constraints: no inferred motor completion. */
  describe() { return {enabled: this.enabled, supported: true, request_pending: this.current !== null,
    request_id: this.current, hardware_verified: false}; }
  /** Functionality: Submit one fixed pattern. Inputs: pattern. Outputs: acknowledgment/event.
   * Logic: explicit acceptance/refusal; Constraints: no alternative pattern or retry. */
  testPattern(pattern) {
    this.requests.push(pattern);
    if (this.reject) return {accepted: false, reason: "native_rejected"};
    this.current = ++this.sequence;
    this.events.publish({channel: "vibration", phase: "start", request_id: this.current, at: this.clock.now()});
    return {accepted: true, request_id: this.current};
  }
  /** Functionality: Cancel matching request. Inputs: id. Outputs: boolean.
   * Logic: identity protects newer manual requests; Constraints: API cancellation is not physical proof. */
  stopOwned(id) { if (id !== this.current) return false; this.cancels.push(id); this.current = null; return true; }
}
/** Functionality: Create isolated manager/device/clock fixtures. Inputs: None. Outputs: dependencies and frame helper.
 * Logic: session established while default disabled; Constraints: no real algorithm invocation. */
function fixture() {
  const clock = new Clock(), speech = new Speech(clock), vibration = new Vibration(clock), events = [];
  const manager = new GuidanceManager({speech, vibration, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer});
  manager.subscribe(event => events.push(event)); manager.setVoice("local-zh", "zh-CN"); manager.begin("session-one");
  /** Functionality: Publish a synthetic validated snapshot. Inputs: boxes/capturedAt/sessionId.
   * Outputs: manager ingestion; Logic: default capture is current source time; Constraints: no model-accuracy claim. */
  function frame(boxes = [box()], capturedAt = clock.now(), sessionId = "session-one") {
    manager.ingest({sessionId, capturedAt, metadata: {index: 0, boxes}});
  }
  return {clock, speech, vibration, manager, events, frame};
}
/** Functionality: Construct independent canonical risk input. Inputs: overrides. Outputs: object.
 * Logic: server-required alert fixture; Constraints: score/threshold calculation is not duplicated. */
function box(overrides = {}) { return {label: "person", track_id: 1, alert_required: true,
  alert_priority: 3, risk_level: "high", distance_m: 2, direction: "front", ...overrides}; }

/** Functionality: Verify default browser timer calls retain their required global receiver.
 * Inputs: receiver-sensitive native-style global functions and default constructor, without injected timers.
 * Outputs: successful begin/snapshot/navigation/stop with both timer functions called on globalThis.
 * Logic: exposes Illegal invocation masked by arrow-clock fixtures; Constraints: no actual waiting, GPU or output. */
test("default browser timer wrappers retain the native global receiver", function testTimerReceiver() {
  const originalSet = globalThis.setTimeout, originalClear = globalThis.clearTimeout, calls = [];
  /** Functionality: Emulate receiver-sensitive Window.setTimeout. Inputs: callback/delay.
   * Outputs: marker ID. Logic: strict receiver assertion; Constraints: no timer actually runs. */
  function browserSetTimeout(callback, delay) { assert.equal(this, globalThis); calls.push({type: "set", delay}); return calls.length; }
  /** Functionality: Emulate receiver-sensitive Window.clearTimeout. Inputs: timer ID.
   * Outputs: call marker. Logic: strict receiver assertion even for null; Constraints: no retry/default receiver accommodation. */
  function browserClearTimeout(timer) { assert.equal(this, globalThis); calls.push({type: "clear", timer}); }
  try {
    globalThis.setTimeout = browserSetTimeout; globalThis.clearTimeout = browserClearTimeout;
    const manager = new GuidanceManager({}); manager.begin("browser-session");
    manager.ingest({sessionId: "browser-session", capturedAt: performance.now(), metadata: {index: 0, boxes: []}});
    manager.setEnabled(true); manager.navigationEvent({text: "demo", language: "en-US", source: "demo"});
    assert.equal(manager.sessionId, "browser-session"); assert.equal(calls.filter(call => call.type === "set").length, 2);
    assert.ok(calls.some(call => call.type === "clear" && call.timer === null)); manager.dispose();
  } finally { globalThis.setTimeout = originalSet; globalThis.clearTimeout = originalClear; }
});

/** Functionality: Verify stopped sessions ignore late frames while active mismatches remain diagnosed.
 * Inputs: explicit stop and foreign session frame. Outputs: stable stopped phase then active wrong-session event.
 * Logic: no silent rebind to an incoming frame; Constraints: transport ownership is not inferred from DOM/IP. */
test("late frames cannot overwrite stopped guidance but active foreign sessions are reported", function testLateSession() {
  const f = fixture(); f.manager.stop("stop"); const count = f.events.length;
  f.frame(); assert.equal(f.events.length, count); assert.equal(f.events.at(-1).phase, "stop");
  f.manager.begin("session-two"); f.frame(); assert.equal(f.events.at(-1).phase, "wrong_session");
  assert.equal(f.events.at(-1).received_session_id, "session-one"); f.manager.dispose();
});

/** Functionality: Verify request-to-start diagnostics retain the original source under continuous newer frames.
 * Inputs: queued frame10 then newer frame11 followed by delayed engine start.
 * Outputs: original session/index/capturedAt and measured500ms; submitted does not count as actual start.
 * Logic: immutable descriptor prevents tracing against latest frame; Constraints: no real latency/hardware assertion. */
test("late engine start retains queued request's original source frame", function testSourceBinding() {
  const f = fixture(); f.manager.setEnabled(true);
  f.manager.ingest({sessionId: "session-one", capturedAt: 100, metadata: {index: 10, tracking_epoch: 7, boxes: [box()]}});
  const submitted = f.events.find(event => event.phase === "output_submitted");
  assert.equal(submitted.actual_start, false); assert.equal(submitted.frame_index, 10);
  f.clock.advance(500);
  f.manager.ingest({sessionId: "session-one", capturedAt: 450, metadata: {index: 11, tracking_epoch: 7, boxes: [box()]}});
  f.clock.advance(600); f.speech.start();
  const started = f.events.find(event => event.phase === "output_start");
  assert.equal(started.session_id, "session-one"); assert.equal(started.frame_index, 10);
  assert.equal(started.source, "perception");
  assert.equal(started.captured_at_ms, 100); assert.equal(started.tracking_epoch, 7);
  assert.equal(started.capture_to_start_ms, 500); assert.equal(started.started_at_ms, 600);
  assert.equal(f.manager.latest.metadata.index, 11); f.manager.dispose();
});
/** Functionality: Verify fresh incoming frames cannot keep an expired unstarted request alive.
 * Inputs: delayed speech start and new snapshot shortly before old source TTL.
 * Outputs: cancel old queued request, submit only latest source, no fabricated actual-start event/history.
 * Logic: source deadline is independent of latest snapshot expiry; Constraints: no native-error retry. */
test("unstarted risk expires by its own source frame even while new frames arrive", function testQueuedDeadline() {
  const f = fixture(); f.manager.setEnabled(true);
  f.manager.ingest({sessionId: "session-one", capturedAt: 100, metadata: {index: 10, boxes: [box()]}});
  f.clock.advance(900);
  f.manager.ingest({sessionId: "session-one", capturedAt: 850, metadata: {index: 11, boxes: [box()]}});
  f.clock.advance(1100);
  assert.deepEqual(f.speech.cancels, [1]); assert.equal(f.speech.requests.length, 2);
  assert.equal(f.manager.active.speech.source.frame_index, 11); assert.equal(f.manager.history.size, 0);
  assert.equal(f.events.filter(event => event.phase === "output_start").length, 0);
  const cancellation = f.events.find(event => event.phase === "output_cancel");
  assert.equal(cancellation.frame_index, 10); assert.equal(cancellation.captured_at_ms, 100); f.manager.dispose();
});

/** Functionality: Verify a user's stop does not immediately resubmit unstarted guidance speech.
 * Inputs: queued own utterance and externally invoked driver cancellation. Outputs: paused channel/no replay.
 * Logic: only explicit resume allows newest eligible data; Constraints: no automatic output retry. */
test("user cancels queued guidance without an automatic resubmission", function testUserCancel() {
  const f = fixture(); f.manager.setEnabled(true); f.frame(); f.speech.stopOwned(1);
  assert.equal(f.speech.requests.length, 1); assert.equal(f.manager.paused.speech, true);
  f.frame(); assert.equal(f.speech.requests.length, 1); f.manager.resume("speech");
  assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});
/** Functionality: Verify tracker epoch invalidates previously alerted IDs within the same camera session.
 * Inputs: same numeric ID in two tracker generations. Outputs: new track is immediately eligible.
 * Logic: consumes explicit algorithm lifecycle rather than changing tracking rules; Constraints: no ID fabrication. */
test("tracking epoch prevents old cooldown from suppressing a reused ID", function testTrackerEpoch() {
  const f = fixture(); f.manager.setEnabled(true);
  f.manager.ingest({sessionId: "session-one", capturedAt: f.clock.now(), metadata: {index: 0, tracking_epoch: 0, boxes: [box()]}});
  f.speech.start(); f.speech.end();
  f.manager.ingest({sessionId: "session-one", capturedAt: f.clock.now(), metadata: {index: 1, tracking_epoch: 1, boxes: [box()]}});
  assert.equal(f.speech.requests.length, 2); f.manager.dispose();
});
/** Functionality: Verify a selected voice disappearing latches an error without default/remote substitution.
 * Inputs: independent inventory mutation between outputs. Outputs: no new speech until manual recovery.
 * Logic: revalidates live native inventory each submission; Constraints: no automatic voice replacement. */
test("a disappearing explicit local voice pauses rather than falling back", function testVoiceDisappeared() {
  const f = fixture(); f.manager.setEnabled(true); f.frame(); f.speech.start(); f.speech.end();
  f.speech.voices = [VOICES[1]]; f.clock.advance(3100); f.frame();
  assert.equal(f.manager.paused.speech, true); assert.equal(f.speech.requests.length, 1); f.manager.dispose();
});
/** Functionality: Verify both independently enabled channels consume one selected target.
 * Inputs: same snapshot, accepted vibration and delayed native speech start.
 * Outputs: single request per channel and no motor-success claim. Logic: synchronous accepted events preserve ownership.
 * Constraints: pattern elapsed is nominal timing only. */
test("explicit speech and vibration channels share one target without a FIFO", function testBothChannels() {
  const f = fixture(); f.manager.setEnabled(true); f.vibration.enabled = true;
  f.frame([box(), box({track_id: 2, alert_priority: 4, risk_level: "critical"})]);
  assert.equal(f.speech.requests.length, 1); assert.deepEqual(f.vibration.requests, ["long"]);
  assert.equal(f.manager.active.speech.box.track_id, 2); assert.equal(f.manager.active.vibration.box.track_id, 2);
  f.frame(); assert.equal(f.vibration.requests.length, 1); f.manager.dispose();
});

/** Functionality: Verify target ordering. Inputs: independent priorities/distances/IDs. Outputs: one selected ID.
 * Logic: all three tie breakers and alert_required exclusion; Constraints: not a risk-rule test. */
test("select one required risk by priority/distance/track", function testSelection() {
  assert.equal(selectRisk([box({alert_required: false, alert_priority: 4}), box({track_id: 9}),
    box({track_id: 4}), box({track_id: 2, distance_m: 3}), box({alert_priority: 2, distance_m: 1})]).track_id, 4);
  assert.equal(selectRisk([]), null);
});
/** Functionality: Verify explicit switches/voices and paused-channel recovery. Inputs: disabled/remote/mismatched voices.
 * Outputs: zero native calls until prerequisites and explicit resume. Logic: no auto enable/fallback.
 * Constraints: native voice fixture only. */
test("guidance remains opt-in and never substitutes remote or mismatched voices", function testPrerequisites() {
  const f = fixture(); f.frame(); assert.equal(f.speech.requests.length, 0);
  f.manager.setEnabled(true); f.manager.setVoice("remote-zh", "zh-CN"); f.frame();
  assert.equal(f.manager.paused.speech, true); assert.equal(f.speech.requests.length, 0);
  f.manager.setVoice("local-zh", "zh-CN"); f.frame(); assert.equal(f.speech.requests.length, 0);
  f.manager.resume("speech"); assert.equal(f.speech.requests.length, 1);
  f.manager.stop(); assert.equal(f.speech.cancels.length, 1);
  f.manager.begin("session-one"); f.manager.setVoice("local-en", "zh-CN"); f.frame();
  assert.equal(f.manager.paused.speech, true); assert.equal(f.speech.requests.length, 1);
  f.manager.dispose();
});
/** Functionality: Verify source TTL, late reception and wrong session. Inputs: old/future capture timestamps.
 * Outputs: stale results retained with no alerts. Logic: time does not restart at receipt.
 * Constraints: synthetic source clock, no network timing claim. */
test("source capture TTL gates alerts while preserving late results", function testCaptureTtl() {
  const f = fixture(); f.manager.setEnabled(true); f.clock.advance(1600); f.frame([box()], 100);
  assert.equal(f.speech.requests.length, 0); assert.equal(f.manager.latest.metadata.boxes.length, 1);
  assert.ok(f.events.some(event => event.phase === "stale"));
  f.frame([box()], 1700); assert.equal(f.speech.requests.length, 0);
  f.frame([box()], 1600, "different"); assert.equal(f.speech.requests.length, 0);
  f.frame(); assert.equal(f.speech.requests.length, 1); f.manager.dispose();
});
/** Functionality: Verify cooldown begins at engine start and upgrades preempt immediately.
 * Inputs: delayed start, repeated same priority and later upgrade. Outputs: request counts/owned cancellation.
 * Logic: never use submission time as start; Constraints: output policy only. */
test("same-track cooldown starts at engine start, upgrades are immediate", function testCooldown() {
  const f = fixture(); f.manager.setEnabled(true); f.frame(); f.clock.advance(600); f.speech.start(); f.speech.end();
  f.clock.advance(3599); f.frame(); assert.equal(f.speech.requests.length, 1);
  f.clock.advance(3600); f.frame(); assert.equal(f.speech.requests.length, 2); f.speech.start();
  f.frame([box({alert_priority: 4, risk_level: "critical"})]); assert.equal(f.speech.requests.length, 3);
  assert.deepEqual(f.speech.cancels, [2]); f.manager.dispose();
});
/** Functionality: Verify risk interrupts owned navigation and restores complete unexpired instruction.
 * Inputs: demo text then risk/clear snapshots. Outputs: ordered full texts and cancellation IDs.
 * Logic: one navigation record survives risk interruption; Constraints: no GPS route claim. */
test("risk preempts own navigation and resumes its whole text", function testNavigationPreemption() {
  const f = fixture(); f.manager.setEnabled(true);
  f.manager.navigationEvent({text: "完整的导航文本", language: "zh-CN", source: "demo"}); f.speech.start();
  f.frame(); assert.equal(f.speech.requests.length, 2); f.speech.start();
  f.frame([]); assert.equal(f.speech.requests.length, 3);
  assert.equal(f.speech.requests[2].text, "完整的导航文本"); assert.deepEqual(f.speech.cancels, [1, 2]);
  f.speech.end(); assert.equal(f.manager.navigation, null); f.manager.dispose();
});
/** Functionality: Protect unrelated output ownership. Inputs: manually owned speech/vibration.
 * Outputs: no unrelated cancellation on risk/stop. Logic: current request IDs remain authoritative.
 * Constraints: explicit driver replacement is a test scenario, not a production bypass. */
test("guidance never cancels manual/scene or newer manual vibration", function testManualOwnership() {
  const f = fixture(); f.manager.setEnabled(true); f.speech.speak("manual", {}); f.frame();
  assert.equal(f.speech.requests.length, 1); assert.equal(f.speech.cancels.length, 0);
  f.manager.stop(); assert.ok(f.speech.current); assert.equal(f.speech.cancels.length, 0);
  f.speech.end(); f.manager.begin("session-one"); f.vibration.enabled = true; f.speech.enabled = false; f.frame();
  f.vibration.current = 999; f.manager.stop(); assert.equal(f.vibration.current, 999); assert.equal(f.vibration.cancels.length, 0);
  f.manager.dispose();
});
/** Functionality: Protect a manually accepted request before any guidance request exists.
 * Inputs: manual long pattern followed by fresh server-required risks. Outputs: no replacement or cancellation.
 * Logic: pending identity remains authoritative after nominal duration; Constraints: no guessed motor end or automatic stop/retry. */
test("manual vibration already pending cannot be replaced by a new risk", function testManualVibrationFirst() {
  const f = fixture(); f.manager.setEnabled(true); f.speech.enabled = false; f.vibration.enabled = true;
  const manual = f.vibration.testPattern("long");
  try {
    f.frame([box({alert_priority: 2, risk_level: "medium"})]);
    f.clock.advance(1000); f.frame([box({track_id: 2, alert_priority: 4, risk_level: "critical"})]);
    assert.deepEqual(f.vibration.requests, ["long"]);
    assert.equal(f.vibration.current, manual.request_id); assert.equal(f.manager.active.vibration, null);
    assert.deepEqual(f.vibration.cancels, []); assert.equal(f.manager.paused.vibration, false);
    assert.ok(f.events.some(event => event.phase === "suppressed" && event.channel === "vibration" &&
      event.reason === "foreign_request_pending" && event.actual_start === false));
  } finally { f.manager.dispose(); }
});
/** Functionality: Protect a manual request that replaced an earlier guidance-owned vibration.
 * Inputs: own low-priority request, manual long request, upgrade/new target/repeated risks.
 * Outputs: manual identity survives all later guidance attempts without paused-error or foreign cancellation.
 * Logic: stale ownership must not authorize a new native submission; Constraints: no device-driver or cooldown change. */
test("manual vibration replacing guidance remains protected from upgrades and later risks", function testManualVibrationReplacement() {
  const f = fixture(); f.manager.setEnabled(true); f.speech.enabled = false; f.vibration.enabled = true;
  f.frame([box({alert_priority: 2, risk_level: "medium"})]);
  const own = f.manager.active.vibration.requestId, manual = f.vibration.testPattern("long");
  assert.notEqual(manual.request_id, own);
  try {
    f.frame([box({alert_priority: 4, risk_level: "critical"})]);
    f.frame([box({track_id: 2, alert_priority: 4, risk_level: "critical"})]);
    f.clock.advance(3200); f.frame([box({alert_priority: 2, risk_level: "medium"})]);
    assert.deepEqual(f.vibration.requests, ["short", "long"]);
    assert.equal(f.vibration.current, manual.request_id); assert.equal(f.manager.active.vibration, null);
    assert.deepEqual(f.vibration.cancels, []); assert.equal(f.manager.paused.vibration, false);
    f.manager.stop(); assert.equal(f.vibration.current, manual.request_id);
    assert.deepEqual(f.vibration.cancels, []);
  } finally { f.manager.dispose(); }
});
/** Functionality: Preserve upgrades and subsequent replacement of this manager's own requests.
 * Inputs: enabled vibration, medium-to-critical upgrade and continuously fresh critical snapshots through3s.
 * Outputs: original short/long patterns, owned cancels and start-based cooldown remain unchanged.
 * Logic: foreign protection only gates another owner's pending request; Constraints: no change to cadence, risk or device behavior. */
test("own vibration can still upgrade and replace after its original cooldown", function testOwnVibrationReplacement() {
  const f = fixture(); f.manager.setEnabled(true); f.speech.enabled = false; f.vibration.enabled = true;
  const critical = box({alert_priority: 4, risk_level: "critical"});
  try {
    f.frame([box({alert_priority: 2, risk_level: "medium"})]);
    const first = f.manager.active.vibration.requestId;
    f.frame([critical]); const upgraded = f.manager.active.vibration.requestId;
    assert.deepEqual(f.vibration.requests, ["short", "long"]); assert.deepEqual(f.vibration.cancels, [first]);
    for (let at = 500; at <= 3000; at += 500) { f.clock.advance(at); f.frame([critical]); }
    assert.equal(f.vibration.requests.length, 2);
    f.clock.advance(3100); f.frame([critical]);
    assert.deepEqual(f.vibration.requests, ["short", "long", "long"]);
    assert.deepEqual(f.vibration.cancels, [first, upgraded]);
    assert.equal(f.vibration.current, f.manager.active.vibration.requestId);
    assert.equal(f.manager.history.get(critical.track_id).at, 3100);
  } finally { f.manager.dispose(); }
});
/** Functionality: Verify latest-only snapshots while a manual utterance holds the device.
 * Inputs: low/critical snapshots then manual completion. Outputs: only current high-priority target spoken.
 * Logic: no risk FIFO; Constraints: no automatic manual preemption. */
test("latest risk replaces older pending perception rather than queuing", function testLatest() {
  const f = fixture(); f.manager.setEnabled(true); f.speech.speak("manual", {});
  f.frame([box({alert_priority: 2, risk_level: "medium"})]);
  f.frame([box({track_id: 3, alert_priority: 4, risk_level: "critical"})]); f.speech.end();
  assert.equal(f.speech.requests.length, 2); assert.match(f.speech.requests[1].text, /极高风险/);
  f.speech.start(); assert.equal(f.manager.history.has(1), false); assert.equal(f.manager.history.has(3), true); f.manager.dispose();
});
/** Functionality: Verify output errors latch channels until explicit recovery.
 * Inputs: async speech failure and rejected vibration. Outputs: no retries under fresh incoming frames.
 * Logic: channels independent and explicit; Constraints: no alternate output enabled as fallback. */
test("native errors pause their channels until a manual resume", function testErrorRecovery() {
  const f = fixture(); f.manager.setEnabled(true); f.frame(); f.speech.error(); f.frame();
  assert.equal(f.speech.requests.length, 1); assert.equal(f.manager.paused.speech, true);
  f.manager.resume("speech"); assert.equal(f.speech.requests.length, 2);
  f.vibration.enabled = true; f.vibration.reject = true; f.frame(); f.frame();
  assert.equal(f.vibration.requests.length, 1); assert.equal(f.manager.paused.vibration, true);
  f.manager.dispose();
});
/** Functionality: Verify only one pending demo instruction with original expiry.
 * Inputs: sustained risk plus replaced navigation instructions. Outputs: expiry without delayed speech.
 * Logic: keep perception fresh while clock crosses10s; Constraints: test fixtures do not imply ongoing GPS. */
test("one pending navigation expires after10s and is not replayed after risk", function testNavigationExpiry() {
  const f = fixture(); f.manager.setEnabled(true); f.frame(); f.speech.start();
  f.manager.navigationEvent({text: "first", language: "zh-CN", source: "demo"});
  f.manager.navigationEvent({text: "latest", language: "zh-CN", source: "demo"});
  assert.equal(f.manager.navigation.text, "latest");
  for (let at = 600; at <= 10100; at += 500) { f.clock.advance(at); f.frame(); }
  assert.equal(f.manager.navigation, null); f.frame([]); assert.equal(f.speech.requests.length, 1); f.manager.dispose();
});
/** Functionality: Verify stop/hide/reset release own work and permit explicit re-enable in same session.
 * Inputs: active outputs/pending navigation and disabled switch. Outputs: clear timers/history, no restart.
 * Logic: canceled events cannot schedule while cleanup is suspended; Constraints: manual restart only. */
test("stop and explicit disable clear owned work without losing live-session re-enable", function testCleanup() {
  const f = fixture(); f.manager.setEnabled(true); f.vibration.enabled = true; f.frame(); f.speech.start();
  f.manager.navigationEvent({text: "demo", language: "zh-CN", source: "demo"}); f.manager.setEnabled(false);
  assert.equal(f.manager.sessionId, "session-one"); assert.equal(f.manager.navigation, null);
  assert.equal(f.manager.history.size, 0); assert.equal(f.clock.timers.size, 0);
  f.manager.setEnabled(true); f.frame(); assert.equal(f.speech.requests.length, 2);
  f.manager.stop("hidden"); assert.equal(f.manager.sessionId, null); assert.equal(f.clock.timers.size, 0); f.manager.dispose();
});
