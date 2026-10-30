/* Responsibilities: Verify browser-check evidence pairing and bounded supplemental gates independently of Chrome.
 * Implementation: Pure lifecycle records and injected deadline callbacks expose false completion and hanging gates.
 * Related Modules: tools/check_local_navigation_browser.mjs; actual native-browser checks remain separate.
 * Declaration Index: session; deadline; deadline.setTimer; deadline.clearTimer; testComplete;
 * testQueuedCancel; testUnmatched; testMissingTerminal; testDuplicates; testRejectedCancel;
 * testDriverBinding; testUtteranceBinding; testFailure; testSynchronousOrder; testGateTimeout;
 * testGateSettlement; testInvalidTimeout.
 * Variable Index: None. Each test owns synthetic evidence and timer state; no native output is manufactured.
 * Constraints: Importing the CLI module must not launch Chrome, create reports, start services or access the network.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {verifySpeech, waitForGate} from "../tools/check_local_navigation_browser.mjs";

/* Functionality: Build independent closed speech request evidence.
 * Inputs: Request ID, terminal phase and started flag. Outputs: Fresh session records.
 * Logic: Keep adapter, original driver and utterance observations separate.
 * Constraints: Synthetic records validate the checker only, never actual speech or audibility. */
function session(id = 1, terminal = "end", started = true) {
  return {speech: [{phase: "output_submitted", request_id: id},
    ...(started ? [{phase: "output_start", request_id: id, actual_start: true}] : []),
    {phase: `output_${terminal}`, request_id: id, ...(terminal === "cancel" ? {accepted: true} : {})}],
  driver: [{phase: "submitted", request_id: id}, ...(started ? [{phase: "start", request_id: id}] : []),
    {phase: terminal, request_id: id}],
  utterances: started ? [{request_id: id, request_owned: true, text_matches_policy: true,
    local_chinese_voice: true, rate_matches_user_setting: true}] : []};
}

/* Functionality: Capture one deadline without waiting on wall time.
 * Inputs: None. Outputs: Timer dependencies and recorded calls.
 * Logic: Tests invoke the scheduled callback explicitly. Constraints: no real timeout or automatic retry. */
function deadline() {
  return {callbacks: [], cleared: [],
    // Functionality: Record a deadline. Inputs: Callback/delay. Outputs: Opaque handle.
    // Logic: One recorded callback per schedule; Constraints: never auto-executes.
    setTimer(callback, delay) { this.callbacks.push({callback, delay}); return this.callbacks.length; },
    // Functionality: Record cleanup. Inputs: Timer handle. Outputs: None.
    // Logic: Retain cleanup evidence; Constraints: no native timers exist.
    clearTimer(handle) { this.cleared.push(handle); },
  };
}

/* Functionality: Validate distinct successful terminal kinds. Inputs: One end and one accepted cancellation.
 * Outputs: Honest per-ID lifecycle counts. Logic: Merge independent requests; Constraints: no aggregate-only matching. */
test("each started request has one matching completion or accepted cancellation", function testComplete() {
  const completed = session(), cancelled = session(2, "cancel");
  const actual = verifySpeech({speech: [...completed.speech, ...cancelled.speech],
    driver: [...completed.driver, ...cancelled.driver], utterances: [...completed.utterances, ...cancelled.utterances]});
  assert.deepEqual(actual, {submitted: 2, started: 2, completed: 1, cancelled: 1, hardware_verified: false});
});

/* Functionality: Preserve legitimate cancellation before native start. Inputs: Queued submitted request/cancel.
 * Outputs: Zero starts/ends and one accepted cancellation. Logic: Queued is not started.
 * Constraints: no fabricated end event is required. */
test("a queued request may be canceled successfully without native start", function testQueuedCancel() {
  assert.deepEqual(verifySpeech(session(1, "cancel", false)),
    {submitted: 1, started: 0, completed: 0, cancelled: 1, hardware_verified: false});
});

/* Functionality: Reject the original unmatched-ID false-positive case.
 * Inputs: Native start1 and unrelated end2. Outputs: Rejection.
 * Logic: Equal aggregate counts cannot replace request identity. Constraints: synthetic regression evidence only. */
test("an unrelated native end cannot complete another started request", function testUnmatched() {
  const value = session(); value.speech.at(-1).request_id = 2; value.driver.at(-1).request_id = 2;
  assert.throws(() => verifySpeech(value), /without native start|exactly one/);
});

/* Functionality: Require closed evidence at success boundaries. Inputs: Unterminated started/queued request.
 * Outputs: Rejection. Logic: Every accepted submission needs one terminal.
 * Constraints: checker does not manufacture completion or cancel missing output. */
test("missing terminal events fail for both started and queued requests", function testMissingTerminal() {
  for (const started of [true, false]) {
    const value = session(1, "cancel", started); value.speech.pop(); value.driver.pop();
    assert.throws(() => verifySpeech(value), /exactly one/);
  }
  const noStart = session(1, "end", false);
  assert.throws(() => verifySpeech(noStart), /without native start/);
});

/* Functionality: Reject repeated or conflicting lifecycle records. Inputs: Duplicate phases/end plus cancel.
 * Outputs: Rejection. Logic: Each phase and terminal is unique by request ID.
 * Constraints: raw evidence is not deduplicated to hide the failure. */
test("duplicate submissions starts and terminal events cannot pass", function testDuplicates() {
  for (const phase of ["output_submitted", "output_start", "output_end"]) {
    const value = session(); value.speech.push({...value.speech.find(event => event.phase === phase)});
    assert.throws(() => verifySpeech(value), /Duplicate/);
  }
  const twice = session(1, "cancel"); twice.speech.push({...twice.speech.at(-1)});
  assert.throws(() => verifySpeech(twice), /Duplicate/);
  const conflict = session(); conflict.speech.push({phase: "output_cancel", request_id: 1, accepted: true});
  conflict.driver.push({phase: "cancel", request_id: 1});
  assert.throws(() => verifySpeech(conflict), /exactly one/);
});

/* Functionality: Require positive cancellation acknowledgement and known identity.
 * Inputs: Rejected or orphaned cancellation. Outputs: Rejection.
 * Logic: A cancel attempt is not a closed request; Constraints: no fallback terminal inference. */
test("rejected and orphaned cancellations do not count as terminated output", function testRejectedCancel() {
  const rejected = session(1, "cancel"); rejected.speech.at(-1).accepted = false;
  assert.throws(() => verifySpeech(rejected), /Unconfirmed cancellation/);
  const orphan = session(1, "cancel", false); orphan.speech.shift();
  assert.throws(() => verifySpeech(orphan), /without submission/);
});

/* Functionality: Require original driver evidence for each claimed phase.
 * Inputs: Missing/duplicated driver phases. Outputs: Rejection.
 * Logic: Adapter reports alone do not prove native lifecycle; Constraints: no replacement driver events. */
test("each adapter phase needs one original matching driver observation", function testDriverBinding() {
  for (const phase of ["submitted", "start", "end"]) {
    for (const duplicate of [false, true]) {
      const value = session(), event = value.driver.find(item => item.phase === phase);
      if (duplicate) value.driver.push({...event}); else value.driver = value.driver.filter(item => item !== event);
      assert.throws(() => verifySpeech(value), /driver/);
    }
  }
  const cancelled = session(1, "cancel", false); cancelled.driver.pop();
  assert.throws(() => verifySpeech(cancelled), /driver cancel/);
});

/* Functionality: Require request-bound utterance comparisons. Inputs: Wrong ID or failed comparison.
 * Outputs: Rejection. Logic: Every native start owns one distinct inspected utterance.
 * Constraints: these comparison booleans do not claim audible sound. */
test("utterances cannot be rebound or counted when their source checks fail", function testUtteranceBinding() {
  const wrong = session(); wrong.utterances[0].request_id = 2;
  assert.throws(() => verifySpeech(wrong), /unique native start/);
  for (const key of ["request_owned", "text_matches_policy", "local_chinese_voice", "rate_matches_user_setting"]) {
    const value = session(); value.utterances[0][key] = false;
    assert.throws(() => verifySpeech(value), /binding failed/);
  }
  const duplicate = session(), second = session(2);
  duplicate.speech.push(...second.speech); duplicate.driver.push(...second.driver);
  duplicate.utterances.push({...duplicate.utterances[0]});
  assert.throws(() => verifySpeech(duplicate), /unique native start/);
});

/* Functionality: Refuse failure-bearing success reports. Inputs: Error/paused/unobserved start records.
 * Outputs: Rejection. Logic: Successful terminal counts do not erase failures.
 * Constraints: no automatic recovery is run by this validator. */
test("native failures and unobserved starts cannot produce successful evidence", function testFailure() {
  for (const phase of ["paused", "output_error"]) {
    const value = session(); value.speech.push({phase, request_id: 1});
    assert.throws(() => verifySpeech(value), /Native speech failure/);
  }
  const value = session(); value.speech[1].actual_start = false;
  assert.throws(() => verifySpeech(value), /explicitly observed/);
});

/* Functionality: Preserve valid synchronous callback ordering. Inputs: Start reported before submitted.
 * Outputs: Success. Logic: Match lifecycle by ID rather than assuming report order.
 * Constraints: terminal uniqueness remains required. */
test("synchronous native start may precede the adapter submission report", function testSynchronousOrder() {
  const value = session(); [value.speech[0], value.speech[1]] = [value.speech[1], value.speech[0]];
  assert.equal(verifySpeech(value).completed, 1);
});

/* Functionality: End missing-response gates with explicit failure. Inputs: Never-settled promise/deadline.
 * Outputs: One timeout rejection and timer cleanup. Logic: Fire one injected deadline.
 * Constraints: no real waiting, successful cancellation claim or automatic retry. */
test("a response gate that never opens fails on one bounded deadline", async function testGateTimeout() {
  const clock = deadline();
  const result = waitForGate(new Promise(() => {}), "Local response delivery", {timeoutMs: 1234,
    setTimer: clock.setTimer.bind(clock), clearTimer: clock.clearTimer.bind(clock)});
  const rejection = assert.rejects(result, /Local response delivery timed out after 1234ms/);
  assert.equal(clock.callbacks.length, 1); assert.equal(clock.callbacks[0].delay, 1234);
  clock.callbacks[0].callback(); await rejection; assert.deepEqual(clock.cleared, [1]);
});

/* Functionality: Preserve gate success and original errors while clearing the bound.
 * Inputs: Settled promises. Outputs: Identical values/errors and canceled timer.
 * Logic: Only the deadline is added; Constraints: no retries or altered payload. */
test("settled gates preserve the original result and always release their deadline", async function testGateSettlement() {
  const success = deadline(), payload = {status: 200};
  assert.equal(await waitForGate(Promise.resolve(payload), "read", {setTimer: success.setTimer.bind(success),
    clearTimer: success.clearTimer.bind(success)}), payload);
  assert.deepEqual(success.cleared, [1]);
  const failed = deadline(), reason = new Error("Original fetch failed");
  await assert.rejects(waitForGate(Promise.reject(reason), "read", {setTimer: failed.setTimer.bind(failed),
    clearTimer: failed.clearTimer.bind(failed)}), error => error === reason);
  assert.deepEqual(failed.cleared, [1]);
});

/* Functionality: Reject invalid deadline configuration. Inputs: Nonfinite/nonpositive durations.
 * Outputs: Explicit validation errors. Logic: No timer should repair invalid timeout parameters.
 * Constraints: no scheduling or waiting occurs. */
test("gate deadlines must be positive and finite", async function testInvalidTimeout() {
  for (const timeoutMs of [0, -1, NaN, Infinity, "10"])
    await assert.rejects(waitForGate(Promise.resolve(null), "read", {timeoutMs}), /positive and finite/);
});
