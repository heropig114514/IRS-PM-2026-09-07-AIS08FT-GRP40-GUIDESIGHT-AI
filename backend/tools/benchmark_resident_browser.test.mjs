/** Responsibilities: Check browser diagnostic admission and evidence boundaries without a GPU.
 * Implementation: Node test runner with explicit synthetic rows for invalid evidence and slow tails.
 * Related Modules: tools/benchmark_resident_browser.mjs; actual browser reports remain separate.
 * Declaration Index: movingSession; argumentsAreExplicit; mediaMustAdvance; observationsMustBeComplete;
 * mediaMustStayValid; slowTailMustFail.
 * Variable Index: None.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {parseArguments, summarizeSession, validateMovingSession} from "./benchmark_resident_browser.mjs";

/** Functionality: Build complete observations to isolate evidence validation.
 * Inputs: none. Outputs: synthetic 181-row session. Logic: source/stream progress monotonically.
 * Constraints: no model, latency or actual-browser validation claim. */
function movingSession() {
  return {source_capture_count: 181, rows: Array.from({length: 181}, (_, index) => ({
    source_observation: {ordinal: index + 1, ended: false, paused: false, playback_rate: 1,
      media_time_s: index / 30, drawn_media_time_s: index / 30, observed_at_ms: index * 34,
      stream_time_s: index / 30, draw_count: index},
  }))};
}

/** Functionality: Reject accidental parameter changes or ambiguous source selection.
 * Inputs: CLI examples. Outputs: assertions. Logic: accept only explicit known options.
 * Constraints: no execution, retries or fallback source. */
function argumentsAreExplicit() {
  assert.deepEqual(parseArguments(["out"]), {output: "out", depthGraph: false, video: null});
  assert.deepEqual(parseArguments(["out", "--video", "sample.mp4", "--depth-graph"]),
    {output: "out", depthGraph: true, video: "sample.mp4"});
  for (const args of [[], ["--video"], ["out", "--video"], ["out", "--fps", "15"],
    ["out", "--video", "--depth-graph"], ["out", "--depth-graph", "--depth-graph"],
    ["out", "--video", "a", "--video", "b"]]) assert.throws(() => parseArguments(args));
}
test("source and experiment selection are explicit", argumentsAreExplicit);

/** Functionality: Distinguish advancing video from a repeated static frame or rewind.
 * Inputs: synthetic observations. Outputs: assertions. Logic: inspect complete source history.
 * Constraints: repeated adjacent observations are allowed under independent browser cadence. */
function mediaMustAdvance() {
  assert.equal(validateMovingSession(movingSession()).drawn_media_span_s, 6);
  const frozen = movingSession();
  for (const row of frozen.rows) { row.source_observation.drawn_media_time_s = 0; row.source_observation.draw_count = 0; }
  assert.throws(() => validateMovingSession(frozen), /did not advance/);
  const rewind = movingSession();
  rewind.rows[100].source_observation.media_time_s = 0;
  assert.throws(() => validateMovingSession(rewind), /backwards/);
}
test("moving source must advance without rewind", mediaMustAdvance);

/** Functionality: Prevent partial observations or ordinal mismatch from passing as full sessions.
 * Inputs: missing/extra rows and captures. Outputs: assertions. Logic: preserve all 181 captures.
 * Constraints: no trimming to match returned results. */
function observationsMustBeComplete() {
  for (const count of [180, 182]) {
    const session = movingSession(); session.source_capture_count = count;
    assert.throws(() => validateMovingSession(session), /Incomplete/);
  }
  const session = movingSession(); session.rows.pop();
  assert.throws(() => validateMovingSession(session), /Incomplete/);
  const ordinal = movingSession(); ordinal.rows[100].source_observation.ordinal = 100;
  assert.throws(() => validateMovingSession(ordinal), /Invalid/);
}
test("all source observations must match submitted ordinals", observationsMustBeComplete);

/** Functionality: Reject EOF, paused/slowed playback and invalid clocks even on one frame.
 * Inputs: single corrupted observation. Outputs: assertions. Logic: validate every original row.
 * Constraints: no tolerance or automatic replacement of invalid values. */
function mediaMustStayValid() {
  for (const [key, value] of [["ended", true], ["paused", true], ["playback_rate", 0.5],
    ["media_time_s", NaN], ["stream_time_s", -1], ["draw_count", Infinity]]) {
    const session = movingSession(); session.rows[80].source_observation[key] = value;
    assert.throws(() => validateMovingSession(session), /Invalid/);
  }
}
test("a single invalid media observation blocks acceptance", mediaMustStayValid);

/** Functionality: Keep cold cost separate while retaining a single slow steady frame in the gate.
 * Inputs: synthetic timing rows. Outputs: summary assertions. Logic: original 10FPS/500ms rules.
 * Constraints: functional success and healthy percentiles cannot hide the maximum. */
function slowTailMustFail() {
  const rows = Array.from({length: 181}, (_, index) => ({capturedAt: index * 40,
    decodedAt: index * 40 + 100, captureToDecodedMs: index === 0 ? 5000 : 100,
    metadata: {resident_queue_ms: 10, server_ms: 30, depth_predict_ms: 10}}));
  assert.equal(summarizeSession({rows}).meets_existing_10fps_500ms_gate, true);
  rows[90].captureToDecodedMs = 501; rows[90].decodedAt = rows[90].capturedAt + 501;
  const summary = summarizeSession({rows});
  assert.equal(summary.first_decoded_ms, 5000);
  assert.equal(summary.steady_frames, 180);
  assert.equal(summary.decoded_p95_ms, 100);
  assert.equal(summary.decoded_max_ms, 501);
  assert.equal(summary.meets_existing_10fps_500ms_gate, false);
}
test("one slow steady frame fails the unchanged gate", slowTailMustFail);
