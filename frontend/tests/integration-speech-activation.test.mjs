/* Responsibilities: Verify isolated startup gating through explicit synthetic driver lifecycles.
 * Implementation: Controllable native-style events and deadlines exercise ownership/reentrancy;
 * no browser permissions, GPS, native synthesis or physical audio are used or claimed.
 * Related Modules: ../integration/speech-activation.js; existing shared-driver/manager contracts.
 * Declaration Index: fixture; fixture.speech.subscribe; fixture.speech.emit;
 * fixture.speech.describe; fixture.speech.voiceList; fixture.speech.speak;
 * fixture.speech.start; fixture.speech.end; fixture.speech.stopOwned;
 * fixture.manager.describe; fixture.manager.pause; fixture.manager.resume; fixture.advance;
 * testConstruction; testSynchronousGesture; testEnglish; testPrerequisites; testManualStart;
 * testUnrelatedError; testWatchdog; testCancelFailure; testStopIsolation; testReadinessRetention;
 * testReentrantEnd; testSynchronousError; testGateBeforeCancel; testDisposal; testObserverStop.
 * Variable Index: None. Fixture utterance bodies stay in test memory only.
 * Constraints: Synthetic start events test logic; they are never native/audio acceptance evidence.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {SpeechActivation} from "../integration/speech-activation.js";

/* Functionality: Build explicit controllable speech/manager/clock substitutes.
 * Inputs: Optional configuration overrides. Outputs: Independent fixture and activation controller.
 * Logic: Emit lifecycle snapshots before observers can reenter; timers execute once at due time.
 * Constraints: No wall-clock sleeps, real speech, physical audio, permission or global driver patch. */
function fixture(options = {}) {
  const state = {time: 0, sequence: 0, timerSequence: 0, timers: new Map(), changes: [],
    calls: [], cancellations: [], pauses: [], resumes: [], gesture: false,
    voiceId: "zh-local", language: "zh-CN", paused: false, reason: null, ...options};
  const speech = {
    current: null, last: null, listeners: new Set(), enabled: true, visible: true, activated: true,
    supported: true, synchronous: null, cancelFailure: false, rate: {value: "2"}, volume: {value: "0.8"},
    voices: [{id: "zh-local", language: "zh-CN", local_service: true}],
    /* Functionality: Observe fixture events. Inputs: Callback. Outputs: Detacher.
     * Logic: Ordered set preserves manager-before-activation ordering. Constraints: No replay. */
    subscribe(callback) { this.listeners.add(callback); return () => this.listeners.delete(callback); },
    /* Functionality: Dispatch immutable identity. Inputs: Phase/ID/reason. Outputs: Listener effects.
     * Logic: Freeze event identity before reentrant observers. Constraints: Events are synthetic. */
    emit(phase, id = this.current?.id, reason = null) {
      const event = {channel: "speech", phase, request_id: id, reason, at: state.time};
      for (const callback of [...this.listeners]) callback({...event});
    },
    /* Functionality: Read driver metadata. Inputs: Fixture state. Outputs: Current contract.
     * Logic: Busy and native startup differ. Constraints: No spoken text in metadata. */
    describe() { return {supported: this.supported, enabled: this.enabled, visible: this.visible,
      user_activated: this.activated, active_request: Boolean(this.current),
      engine_speaking: this.current?.started === true, engine_pending: Boolean(this.current && !this.current.started),
      engine_paused: false, last_request: this.last && {...this.last}}; },
    /* Functionality: Expose explicit voices. Inputs: Fixture inventory. Outputs: Copied entries.
     * Logic: Local metadata is independently mutable. Constraints: No voice fallback. */
    voiceList() { return {accepted: true, voices: this.voices.map(voice => ({...voice}))}; },
    /* Functionality: Submit an explicit request. Inputs: Text/options. Outputs: Driver acknowledgement.
     * Logic: Record caller-stack gesture state and optionally dispatch synchronous lifecycle events.
     * Constraints: Overlap is rejected; the acknowledgement deliberately reflects latest driver metadata. */
    speak(text, settings) {
      assert.equal(this.current, null, "fixture rejects overlapping speech");
      const id = ++state.sequence, voice = this.voices.find(item => item.id === settings.voice_id);
      this.current = {id, started: false}; this.last = {id, ...settings, local_service: voice?.local_service === true};
      state.calls.push({id, text, settings: {...settings}, gesture: state.gesture}); this.emit("submitted", id);
      if (this.synchronous === "start" || this.synchronous === "end") this.start();
      if (this.synchronous === "end") this.end();
      if (this.synchronous === "error") { this.current = null; this.emit("error", id, "native_error"); }
      return {accepted: true, last_request: {...this.last}};
    },
    /* Functionality: Start the current request. Inputs: None. Outputs: Synthetic native-style event.
     * Logic: Update state before notification. Constraints: Not physical output evidence. */
    start() { this.current.started = true; this.emit("start", this.current.id); },
    /* Functionality: Complete current output. Inputs: None. Outputs: Synthetic terminal event.
     * Logic: Free the driver before observers pump successors. Constraints: No automatic retry. */
    end() { const id = this.current.id; this.current = null; this.emit("end", id); },
    /* Functionality: Cancel exact ownership. Inputs: Request ID. Outputs: Accepted or explicit failure.
     * Logic: A retained failure leaves the current request untouched. Constraints: Never cancel foreign ID. */
    stopOwned(id) {
      state.cancellations.push({id, paused: state.paused, reason: state.reason});
      if (this.current?.id !== id) return {accepted: false, reason: "not_owned"};
      if (this.cancelFailure) return {accepted: false, reason: "cancel_failed"};
      this.current = null; this.emit("cancel", id, "explicit_stop"); return {accepted: true};
    }
  };
  const manager = {
    /* Functionality: Read channel gate. Inputs: Fixture state. Outputs: Detached manager status.
     * Logic: Model the existing pause reason contract. Constraints: No scheduling implementation. */
    describe() { return {paused: {speech: state.paused}, reasons: {speech: state.reason}}; },
    /* Functionality: Close speech gate. Inputs: Channel/reason. Outputs: Updated fixture.
     * Logic: Record ownership decisions. Constraints: No cancellation or source changes. */
    pause(channel, reason) { assert.equal(channel, "speech"); state.pauses.push(reason); state.paused = true; state.reason = reason; },
    /* Functionality: Reopen speech gate. Inputs: Channel. Outputs: Updated fixture.
     * Logic: Record explicit resumes. Constraints: No driver submissions. */
    resume(channel) { assert.equal(channel, "speech"); state.resumes.push(state.reason); state.paused = false; state.reason = null; }
  };
  if (options.beforeSubscribe) options.beforeSubscribe(speech, manager, state);
  const activation = new SpeechActivation({speech, manager,
    config: () => ({voiceId: state.voiceId, language: state.language}),
    onChange: snapshot => { state.changes.push(snapshot); options.observer?.(snapshot); },
    setTimer: (callback, delay) => { const id = ++state.timerSequence; state.timers.set(id, {callback, due: state.time + delay}); return id; },
    clearTimer: id => state.timers.delete(id)});
  /* Functionality: Advance explicit deadlines. Inputs: Milliseconds. Outputs: Due effects.
   * Logic: Delete each timer before execution. Constraints: No real waiting or repeating timers. */
  function advance(milliseconds) {
    const until = state.time + milliseconds;
    for (;;) {
      const due = [...state.timers].filter(([, item]) => item.due <= until).sort((a, b) => a[1].due - b[1].due)[0];
      if (!due) break;
      state.time = due[1].due; state.timers.delete(due[0]); due[1].callback();
    }
    state.time = until;
  }
  return {state, speech, manager, activation, advance};
}

/* Functionality: Verify silent construction and error preservation. Inputs: Synthetic dependencies.
 * Outputs: Assertions. Logic: Check the gate before any action. Constraints: No native audio claim. */
test("construction gates speech silently and preserves unrelated manager errors", function testConstruction() {
  const f = fixture(); assert.deepEqual(f.state.pauses, ["activation_required"]);
  assert.equal(f.state.calls.length, 0); assert.equal(f.state.timers.size, 0); assert.equal(f.activation.describe().ready, false);
  const blocked = fixture({paused: true, reason: "cancel_failed"});
  assert.equal(blocked.state.reason, "cancel_failed"); assert.deepEqual(blocked.state.pauses, []);
});

/* Functionality: Verify trusted-stack calibration and real-start readiness. Inputs: Explicit selected voice.
 * Outputs: Assertions. Logic: Inspect synchronous call timing and native-style start. Constraints: Synthetic engine only. */
test("activate submits synchronously with exact local voice and unchanged settings", function testSynchronousGesture() {
  const f = fixture(); f.state.gesture = true; const result = f.activation.activate(); f.state.gesture = false;
  assert.equal(result.accepted, true); assert.equal(result.state, "queued"); assert.equal(result.ready, false);
  assert.deepEqual(f.state.calls[0], {id: 1, text: "语音提醒已开启。", gesture: true,
    settings: {language: "zh-CN", voice_id: "zh-local", rate: 2, volume: 0.8}});
  assert.equal(f.state.paused, true); f.speech.start();
  assert.equal(f.activation.describe().ready, true); assert.equal(f.activation.describe().request_id, 1);
  assert.deepEqual(f.state.resumes, ["activation_required"]); assert.equal(f.state.timers.size, 0);
});

/* Functionality: Verify explicit English selection. Inputs: English local voice and user controls.
 * Outputs: Assertions. Logic: No language/rate substitution. Constraints: No automatic inventory choice. */
test("English calibration preserves the selected voice and user rate", function testEnglish() {
  const f = fixture({language: "en-US", voiceId: "en-local"});
  f.speech.voices = [{id: "en-local", language: "en-GB", local_service: true}]; f.speech.rate.value = "1.5";
  assert.equal(f.activation.activate().accepted, true);
  assert.equal(f.state.calls[0].text, "Voice alerts are enabled."); assert.equal(f.state.calls[0].settings.rate, 1.5);
});

/* Functionality: Reject missing/unsafe prerequisites. Inputs: Explicit invalid cases.
 * Outputs: Assertions. Logic: All attempts remain gated without speaking/cancelling. Constraints: No implicit fallback. */
test("missing local voice, remote/mismatched voice, disabled, hidden and busy fail explicitly", function testPrerequisites() {
  for (const [mutate, reason] of [
    [f => { f.state.voiceId = ""; }, "explicit_local_voice_required"],
    [f => { f.speech.voices[0].local_service = false; }, "local_voice_required"],
    [f => { f.speech.voices[0].language = "en-US"; }, "voice_language_mismatch"],
    [f => { f.speech.voices.push({...f.speech.voices[0]}); }, "local_voice_unavailable"],
    [f => { f.speech.enabled = false; }, "disabled"],
    [f => { f.speech.supported = false; }, "unsupported"],
    [f => { f.speech.visible = false; }, "page_hidden"],
    [f => { f.speech.activated = false; }, "activation_required"],
    [f => { f.speech.rate.value = "3"; }, "invalid_parameters"],
    [f => { f.speech.current = {id: 19, started: true}; }, "busy"]
  ]) {
    const f = fixture(); mutate(f); const result = f.activation.activate();
    assert.equal(result.accepted, false); assert.equal(result.reason, reason); assert.equal(f.state.paused, true);
    assert.equal(f.state.calls.length, 0); assert.equal(f.state.cancellations.length, 0);
  }
});

/* Functionality: Accept only correctly selected manual starts. Inputs: Foreign driver requests.
 * Outputs: Assertions. Logic: Manual ownership remains separate. Constraints: Manual outputs are never cancelled. */
test("matching manual native start unlocks without adopting or cancelling it", function testManualStart() {
  const f = fixture();
  f.speech.speak("manual", {voice_id: "other", language: "zh-CN", rate: 2, volume: 1}); f.speech.start();
  assert.equal(f.activation.describe().ready, false); f.speech.end();
  f.speech.speak("manual", {voice_id: "zh-local", language: "zh-CN", rate: 2, volume: 1}); f.speech.start();
  assert.equal(f.activation.describe().ready, true); assert.equal(f.activation.describe().request_id, 2);
  assert.equal(f.activation.describe().owned_request_id, null); f.activation.stop();
  assert.equal(f.speech.current.id, 2); assert.deepEqual(f.state.cancellations, []);
});

/* Functionality: Preserve unrelated output faults. Inputs: Preexisting manager error.
 * Outputs: Assertions. Logic: Readiness and manager recovery remain independent. Constraints: No broad resume. */
test("observed native start never clears an unrelated manager failure", function testUnrelatedError() {
  const f = fixture({paused: true, reason: "native_error"}); f.activation.activate(); f.speech.start();
  assert.equal(f.activation.describe().ready, true); assert.equal(f.state.reason, "native_error");
  assert.deepEqual(f.state.resumes, []); f.activation.activate(); assert.equal(f.state.reason, "native_error");
});

/* Functionality: Bound unstarted owned calibration. Inputs: Explicit clock advancement.
 * Outputs: Assertions. Logic: Cancel once exactly at deadline. Constraints: No retries or fabricated start. */
test("queued watchdog cancels its own ID once after five seconds without retry", function testWatchdog() {
  const f = fixture(); f.activation.activate(); f.advance(4999); assert.equal(f.state.cancellations.length, 0);
  f.advance(1); assert.equal(f.activation.describe().ready, false); assert.equal(f.activation.describe().reason, "activation_timeout");
  assert.deepEqual(f.state.cancellations.map(item => item.id), [1]); f.advance(100000);
  assert.equal(f.state.calls.length, 1); assert.equal(f.state.cancellations.length, 1); assert.equal(f.state.paused, true);
});

/* Functionality: Preserve uncertain cancellation ownership. Inputs: Explicit cancel failure.
 * Outputs: Assertions. Logic: Only a new explicit stop can retry cleanup. Constraints: No automatic recovery. */
test("cancel failure latches without activation retry and explicit stop can clean it", function testCancelFailure() {
  const f = fixture(); f.speech.cancelFailure = true; f.activation.activate(); f.advance(5000);
  assert.equal(f.activation.describe().cancel_failed, true); assert.equal(f.activation.describe().owned_request_id, 1);
  assert.equal(f.activation.activate().accepted, false); f.advance(50000);
  assert.equal(f.state.cancellations.length, 1); assert.equal(f.state.calls.length, 1);
  f.speech.cancelFailure = false; f.activation.stop();
  assert.equal(f.activation.describe().cancel_failed, false); assert.equal(f.activation.describe().owned_request_id, null);
  assert.equal(f.state.cancellations.length, 2); assert.equal(f.activation.describe().ready, false);
});

/* Functionality: Isolate stopped lifecycles. Inputs: Captured old timer and late event.
 * Outputs: Assertions. Logic: Both identity and generation guards reject old callbacks. Constraints: New user action remains possible. */
test("stop rejects late queued callbacks and permits a new explicit lifecycle", function testStopIsolation() {
  const f = fixture(); f.activation.activate(); const oldTimer = [...f.state.timers.values()][0].callback;
  f.activation.stop(); oldTimer();
  f.speech.current = {id: 1, started: true}; f.speech.emit("start", 1);
  assert.equal(f.activation.describe().ready, false); f.speech.current = null;
  assert.equal(f.activation.activate().accepted, true); f.speech.start();
  assert.equal(f.activation.describe().ready, true); assert.equal(f.activation.describe().request_id, 2);
  assert.deepEqual(f.state.cancellations.map(item => item.id), [1]);
});

/* Functionality: Keep evidence after successful startup. Inputs: Completed calibration and stop.
 * Outputs: Assertions. Logic: Browser startup is retained without repeated calibration. Constraints: No audibility claim. */
test("real-start readiness and first request evidence survive completion and stop", function testReadinessRetention() {
  const f = fixture(); f.activation.activate(); f.speech.start(); f.speech.end(); f.activation.stop();
  assert.equal(f.activation.describe().ready, true); assert.equal(f.activation.describe().request_id, 1);
  assert.equal(f.activation.describe().owned_request_id, null); assert.equal(f.activation.activate().accepted, true);
  assert.equal(f.state.calls.length, 1); assert.equal(f.state.cancellations.length, 0);
});

/* Functionality: Exercise synchronous completion and manager-first successor submission.
 * Inputs: Earlier shared listener. Outputs: Assertions. Logic: Old return metadata may contain newer ID.
 * Constraints: Actual event ID remains authoritative; newer driver request must survive cleanup. */
test("synchronous end reentrancy preserves calibration evidence and newer automatic ownership", function testReentrantEnd() {
  const f = fixture({beforeSubscribe(speech) {
    speech.subscribe(event => {
      if (event.phase === "end" && event.request_id === 1) {
        speech.synchronous = null;
        speech.speak("automatic", {voice_id: "zh-local", language: "zh-CN", rate: 2, volume: 0.8});
      }
    });
  }});
  f.speech.synchronous = "end"; const result = f.activation.activate();
  assert.equal(result.accepted, true); assert.equal(result.ready, true); assert.equal(result.reason, null);
  assert.equal(result.request_id, 1); assert.equal(result.owned_request_id, null); assert.equal(f.speech.current.id, 2);
  f.activation.stop(); assert.equal(f.speech.current.id, 2); assert.deepEqual(f.state.cancellations, []);
});

/* Functionality: Distinguish synchronous native failure from submission acceptance.
 * Inputs: Synthetic error inside speak. Outputs: Assertions. Logic: No timer or ready on failed native path.
 * Constraints: Accepted driver acknowledgement alone never grants readiness. */
test("synchronous native error remains failed without watchdog or readiness", function testSynchronousError() {
  const f = fixture(); f.speech.synchronous = "error"; const result = f.activation.activate();
  assert.equal(result.accepted, false); assert.equal(result.ready, false); assert.equal(result.reason, "native_error");
  assert.equal(f.state.timers.size, 0); assert.equal(f.state.paused, true);
});

/* Functionality: Prevent cancel-triggered successor output. Inputs: Started owned calibration.
 * Outputs: Assertions. Logic: Observe gate at exact cancellation call. Constraints: Stop does not submit/resume. */
test("stop gates manager before cancelling an already-started owned calibration", function testGateBeforeCancel() {
  const f = fixture(); f.activation.activate(); f.speech.start(); assert.equal(f.state.paused, false);
  f.activation.stop(); assert.equal(f.state.cancellations[0].paused, true);
  assert.equal(f.state.cancellations[0].reason, "activation_required"); assert.equal(f.activation.describe().ready, true);
  assert.equal(f.state.calls.length, 1); f.activation.activate(); assert.equal(f.state.paused, false);
});

/* Functionality: Detach startup observer cleanly. Inputs: Queued owner and repeated disposal.
 * Outputs: Assertions. Logic: Stop exactly once then reject all later starts/actions. Constraints: Shared driver survives. */
test("dispose cancels only calibration once and ignores late events", function testDisposal() {
  const f = fixture(); f.activation.activate(); f.activation.dispose(); f.activation.dispose();
  assert.equal(f.speech.listeners.size, 0); assert.equal(f.state.cancellations.length, 1);
  f.speech.emit("start", 1); assert.equal(f.activation.describe().ready, false);
  assert.equal(f.activation.activate().reason, "disposed"); assert.equal(f.state.calls.length, 1);
});

/* Functionality: Guard observer-driven stop before speak. Inputs: UI observer cancelling queued transition.
 * Outputs: Assertions. Logic: Recheck ownership after publication. Constraints: No utterance after explicit stop. */
test("an observer stopping queued setup prevents the synchronous driver submission", function testObserverStop() {
  let activation;
  const f = fixture({observer(snapshot) { if (snapshot.state === "queued") activation.stop(); }});
  activation = f.activation; assert.equal(activation.activate().accepted, false);
  assert.equal(f.state.calls.length, 0); assert.equal(f.state.timers.size, 0);
});
