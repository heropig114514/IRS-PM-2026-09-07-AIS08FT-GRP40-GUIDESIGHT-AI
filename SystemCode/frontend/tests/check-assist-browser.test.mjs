/* Responsibilities: Verify unified browser-check CLI and backend TTS evidence acceptance independently.
 * Implementation: Explicit closed lifecycle records, command binding and source provenance expose false claims.
 * Related Modules: tools/check_assist_browser.mjs, tools/check_local_navigation_browser.mjs.
 * Declaration Index: evidence; testCli; testCliRejected; testBackendPairing; testSyntheticScope;
 * testRealSource; testRealMismatch; testMissingRisk; testQueuedCancel; testCalibrationPartition; testSafeFailure.
 * Variable Index: ENV is a path-only runtime fixture; no executable is loaded during these tests.
 * Constraints: No browser, files, model, GPU, network, real GPS, provider call or physical audio is exercised.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {safeFailure, validateArguments, verifyAssistSession} from "../tools/check_assist_browser.mjs";

const ENV = {PYTHON: "explicit-python", PRS_PLAYWRIGHT_PATH: "explicit-playwright"};

/* Functionality: Build independent backend lifecycle/source evidence. Inputs: Risk/navigation kind.
 * Outputs: Fresh checker fixture. Logic: preserve exact request/source identity in separate streams.
 * Constraints: Synthetic records test the checker, never claiming actual model or audible output. */
function evidence(kind = "risk") {
  const common = {channel: "speech", request_id: 1, kind, session_id: "local-session", frame_index: 4,
    captured_at_ms: 100, track_id: 7, capture_to_start_ms: 300};
  return {speech: [{...common, phase: "output_submitted"}, {...common, phase: "output_start", actual_start: true},
    {...common, phase: "output_end"}],
  driver: [{phase: "submitted", request_id: 1}, {phase: "start", request_id: 1}, {phase: "end", request_id: 1}],
  utterances: [{...common, request_owned: true, text_matches_policy: true, backend_voice: true,
    language_matches_request: true, rate_matches_user_setting: true, source_matches_validated_frame: true}],
  tts_requests: [{request_id: 1, backend_voice: true, text_matches_policy: true,
    language: "zh-CN", configured_language: "zh-CN", rate: 2, configured_rate: 2}],
  frames: [{session_id: "local-session", index: 4, captured_at_ms: 100, required_track_ids: [7],
    provider: "local", model: "yolo26n", timestamp_source: "browser_capture"}]};
}

/* Functionality: Accept only explicit intended CLI shape. Inputs: Loopback URL, new report and optional video.
 * Outputs: Canonical absolute paths and fixed screenshot partner. Logic: pure parsing.
 * Constraints: Existing-file/exclusive-creation checks occur only in actual CLI main. */
test("assist checker accepts explicit loopback entry and optional selected video", function testCli() {
  const without = validateArguments(["http://127.0.0.1:8766/maps/test", "new-report.json"], ENV);
  assert.equal(without.url, "http://127.0.0.1:8766/maps/test"); assert.equal(without.video, null);
  assert.ok(without.output.endsWith("new-report.json")); assert.ok(without.screenshot.endsWith("new-report.png"));
  const withVideo = validateArguments(["http://localhost:8766/maps/test", "new.json", "mot17.mp4"], ENV);
  assert.ok(withVideo.video.endsWith("mot17.mp4"));
});

/* Functionality: Reject scope expansion or ambiguous runtime arguments. Inputs: Invalid CLI shapes.
 * Outputs: Explicit rejection. Logic: no network or implicit endpoint choices.
 * Constraints: pure validation only. */
test("assist checker rejects remote credential query wrong path and malformed arguments", function testCliRejected() {
  for (const url of ["https://127.0.0.1/maps/test", "http://remote.example/maps/test", "http://user:secret@localhost/maps/test",
    "http://localhost/maps/test?key=secret", "http://localhost/maps/test#fragment", "http://localhost/live/"])
    assert.throws(() => validateArguments([url, "new.json"], ENV));
  for (const argv of [[], ["http://localhost/maps/test"], ["http://localhost/maps/test", "bad.txt"],
    ["http://localhost/maps/test", "new.json", "video.mp4", "extra"]])
    assert.throws(() => validateArguments(argv, ENV));
  assert.throws(() => validateArguments(["http://localhost/maps/test", "new.json"], {}));
});

/* Functionality: Preserve request-ID completion requirements in unified backend evidence.
 * Inputs: Independent closed request and orphan/duplicate terminal corruption. Outputs: Success/rejection.
 * Logic: strict manager/driver/command pairing; Constraints: counts alone cannot pass. */
test("unified checker requires exact backend request lifecycle pairing", function testBackendPairing() {
  assert.equal(verifyAssistSession(evidence()).completed, 1);
  const orphan = evidence(); orphan.speech.at(-1).request_id = 2;
  assert.throws(() => verifyAssistSession(orphan));
  const duplicate = evidence(); duplicate.speech.push({...duplicate.speech.at(-1)});
  assert.throws(() => verifyAssistSession(duplicate), /Duplicate/);
});

/* Functionality: Keep synthetic arbitration honest. Inputs: Explicit synthetic source evidence.
 * Outputs: No model verification claim. Logic: default mode never infers real model status.
 * Constraints: synthetic backend evidence does not establish inference even when risk starts exist. */
test("synthetic arbitration cannot implicitly claim actual inference", function testSyntheticScope() {
  const fixture = evidence(); fixture.frames = []; fixture.utterances[0].source_matches_validated_frame = false;
  assert.equal(verifyAssistSession(fixture, {minimumRiskStarts: 1}).actual_model_verified, false);
  assert.throws(() => verifyAssistSession(fixture, {actualModel: true}), /No validated model frame/);
});

/* Functionality: Accept independently source-bound fresh risk evidence. Inputs: Closed fixture.
 * Outputs: Explicit model flag/risk count. Logic: exact frame/session/time/track references.
 * Constraints: test validates acceptance logic, not real inference. */
test("actual-model mode requires fresh exact frame provenance", function testRealSource() {
  const summary = verifyAssistSession(evidence(), {actualModel: true, minimumRiskStarts: 1});
  assert.equal(summary.actual_model_verified, true); assert.equal(summary.risk_started, 1);
});

/* Functionality: Reject fabricated booleans and mismatched or stale source evidence.
 * Inputs: Single-field corruptions. Outputs: Provenance failure. Logic: cross-check actual source references.
 * Constraints: no reference repair, filtering, clock refresh or inferred success. */
test("actual-model mode rejects stale foreign missing-track and nonlocal provenance", function testRealMismatch() {
  for (const [field, value] of [["session_id", "foreign"], ["index", 5], ["captured_at_ms", 101],
    ["required_track_ids", [8]], ["provider", "hf"], ["model", "other"], ["timestamp_source", "server_received"]]) {
    const source = evidence(); source.frames[0][field] = value;
    assert.throws(() => verifyAssistSession(source, {actualModel: true, minimumRiskStarts: 1}), /bind a fresh/);
  }
  for (const age of [-1, 1000, Infinity, NaN]) {
    const source = evidence(); source.utterances[0].capture_to_start_ms = age;
    assert.throws(() => verifyAssistSession(source, {actualModel: true}), /bind a fresh/);
  }
});

/* Functionality: Reject insufficient real risk observations. Inputs: Navigation-only/fewer risk starts.
 * Outputs: Explicit failure. Logic: minimum is enforced on actual backend starts, not submitted events.
 * Constraints: no generated missing risk outputs or false completion. */
test("selected real video must meet the requested backend risk minimum", function testMissingRisk() {
  assert.throws(() => verifyAssistSession(evidence("navigation"), {actualModel: true, minimumRiskStarts: 1}), /Insufficient/);
  assert.throws(() => verifyAssistSession(evidence(), {actualModel: true, minimumRiskStarts: 3}), /Insufficient/);
});

/* Functionality: Preserve legal pre-start cancellation. Inputs: Queued backend request then accepted cancel.
 * Outputs: Honest zero-start cancellation. Logic: original lifecycle verifier remains authoritative.
 * Constraints: no end event is manufactured for a request that never started. */
test("queued cancellation remains distinct from completed backend speech", function testQueuedCancel() {
  const source = evidence(); source.utterances = [];
  source.tts_requests = [];
  source.speech = [source.speech[0], {...source.speech[0], phase: "output_cancel", accepted: true}];
  source.driver = [source.driver[0], {request_id: 1, phase: "cancel"}];
  const summary = verifyAssistSession(source);
  assert.equal(summary.started, 0); assert.equal(summary.completed, 0); assert.equal(summary.cancelled, 1);
});

/* Functionality: Keep first-speech calibration separate from automatic source-bound outputs.
 * Inputs: Explicit calibration lifecycle in its independent report partition. Outputs: Unchanged guidance count.
 * Logic: manager/source records remain authoritative; Constraints: calibration is never model risk. */
test("backend calibration does not inflate automatic guidance or model-risk counts", function testCalibrationPartition() {
  const source = evidence("navigation");
  source.activation = {events: [{phase: "submitted", request_id: 99}, {phase: "start", request_id: 99},
    {phase: "end", request_id: 99}]};
  const result = verifyAssistSession(source);
  assert.equal(result.started, 1); assert.equal(result.navigation_started, 1); assert.equal(result.risk_started, 0);
});

/* Functionality: Redact string bodies while retaining actionable assertion shapes.
 * Inputs: Assertion-like and primitive thrown values. Outputs: Bounded safe failure records.
 * Logic: preserve operator, location and primitive numeric/boolean values, but reduce strings to lengths.
 * Constraints: no source, coordinate or message body is copied into evidence. */
test("failure evidence preserves assertion shapes without retaining string values", function testSafeFailure() {
  const failure = safeFailure(Object.assign(new Error("Assertion failed"), {
    operator: "strictEqual", actual: "private speech body", expected: "different body",
  }));
  assert.equal(failure.operator, "strictEqual");
  assert.equal(failure.actual_type, "string");
  assert.equal(failure.actual_length, [..."private speech body"].length);
  assert.equal(failure.expected_length, [..."different body"].length);
  assert.equal(JSON.stringify(failure).includes("private speech body"), false);
  assert.deepEqual(safeFailure("primitive failure"), {type: "string", message: "primitive failure"});
  const scalarFailure = safeFailure(Object.assign(new Error("Mismatch"), {actual: 4, expected: false}));
  assert.equal(scalarFailure.actual, 4);
  assert.equal(scalarFailure.expected, false);
  assert.equal(scalarFailure.actual_type, "number");
  assert.equal(scalarFailure.expected_type, "boolean");
});
