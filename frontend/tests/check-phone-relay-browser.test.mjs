/* Responsibilities: Protect public TTS stream evidence correlation from canceled-socket false failures.
 * Implementation: Construct fixed, body-free protocol evidence and test success and failure boundaries.
 * Related Modules: ../tools/check_phone_relay_browser.mjs.
 * Declaration Index: createEvidence — construct secure synthetic stream evidence;
 * acceptsCancelledEmptySocket — preserve a valid pre-submit cancellation boundary;
 * rejectsMissingCompletion — fail a started request without terminal PCM metadata;
 * rejectsProviderError — fail explicit provider errors;
 * rejectsInsecureSocket — fail nonsecure transport.
 * Variable Index: None.
 * Constraints: Synthetic fixtures verify validator logic only; they do not verify a live service or speaker. */
import assert from "node:assert/strict";
import test from "node:test";
import {verifyTtsStreamEvidence} from "../tools/check_phone_relay_browser.mjs";

/* Functionality: Create deterministic successful TTS events plus one optional transport failure.
 * Inputs: Failure selector. Outputs: Secure sockets, audio lifecycle and UUID-to-playback mappings.
 * Logic: Four started requests meet the live acceptance minimum; an optional fifth socket represents
 * cancellation before provider submission. Constraints: No utterance text, credentials or network I/O. */
function createEvidence(failure = null) {
  const submissions = Array.from({length: 4}, (_, offset) => {
    const audio_request_id = offset + 1;
    return {audio_request_id, request_id: `00000000-0000-4000-8000-${String(audio_request_id).padStart(12, "0")}`};
  });
  const sockets = submissions.map(submission => ({secure: true, tts: [
    {type: "audio_config", request_id: submission.request_id, sample_rate: 24000, format: "pcm_s16le_mono"},
    {type: "complete", request_id: submission.request_id, audio_bytes: 2048}]}));
  if (failure === "missing_completion") sockets[3].tts.pop();
  if (failure === "provider_error") sockets[3].tts.push({type: "error", request_id: submissions[3].request_id});
  if (failure === "insecure_socket") sockets[0].secure = false;
  if (failure !== "no_cancelled_socket") sockets.push({secure: true, tts: []});
  const audio = submissions.map(({audio_request_id}) => ({phase: "start", request_id: audio_request_id}));
  return {sockets, audio, submissions};
}

/* Functionality: Accept four completed playback streams plus a canceled socket with no provider response.
 * Inputs: None. Outputs: Node test assertion. Logic: Validate and inspect summarized evidence.
 * Constraints: The fixture makes no claim about the actual production browser or provider. */
function acceptsCancelledEmptySocket() {
  const evidence = createEvidence();
  const result = verifyTtsStreamEvidence(evidence.sockets, evidence.audio, evidence.submissions);
  assert.equal(result.started_streams, 4);
  assert.equal(result.accepted_streams, 4);
  assert.equal(result.cancelled_before_provider_response, 1);
}

/* Functionality: Reject a started playback whose server stream never completes.
 * Inputs: None. Outputs: Node test assertion. Logic: Remove one required completion event.
 * Constraints: No service invocation. */
function rejectsMissingCompletion() {
  const evidence = createEvidence("missing_completion");
  assert.throws(() => verifyTtsStreamEvidence(evidence.sockets, evidence.audio, evidence.submissions),
    error => error.code === "BACKEND_TTS_STREAM_INVALID");
}

/* Functionality: Reject provider errors even when a request identity is valid.
 * Inputs: None. Outputs: Node test assertion. Logic: Add an explicit server error event.
 * Constraints: No service invocation. */
function rejectsProviderError() {
  const evidence = createEvidence("provider_error");
  assert.throws(() => verifyTtsStreamEvidence(evidence.sockets, evidence.audio, evidence.submissions),
    error => error.code === "BACKEND_TTS_STREAM_INVALID");
}

/* Functionality: Reject a TTS connection that did not use secure WebSocket transport.
 * Inputs: None. Outputs: Node test assertion. Logic: Mark one connection insecure.
 * Constraints: No service invocation. */
function rejectsInsecureSocket() {
  const evidence = createEvidence("insecure_socket");
  assert.throws(() => verifyTtsStreamEvidence(evidence.sockets, evidence.audio, evidence.submissions),
    error => error.code === "BACKEND_TTS_STREAM_INVALID");
}

test("accepts a canceled secure TTS socket without submitted synthesis", acceptsCancelledEmptySocket);
test("rejects a started stream without completion", rejectsMissingCompletion);
test("rejects a provider TTS error", rejectsProviderError);
test("rejects an insecure TTS socket", rejectsInsecureSocket);
