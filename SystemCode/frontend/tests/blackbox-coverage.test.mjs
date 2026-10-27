/* Responsibilities: Verify the public black-box functional coverage manifest and fail-closed report contract.
 * Implementation: Build body-free synthetic report fixtures and exercise complete, missing and unsafe evidence.
 * Related Modules: ../tools/verify_blackbox_coverage.mjs; public browser reports in SystemCode/backend/.venv/tmp.
 * Declaration Index: surface — construct responsive-layout evidence; ttsCases — construct bilingual TTS evidence;
 * completeReports — construct all required family report fixtures.
 * Variable Index: None; all report state is local to individual test cases.
 * Constraints: Fixtures are synthetic and prove only coverage-gate logic, never online service availability.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {evaluateBlackboxCoverage, parseArguments} from "../tools/verify_blackbox_coverage.mjs";

/* Functionality: Construct a valid responsive-surface fixture.
 * Inputs: Viewport width and visible hold label. Outputs: Body-free layout evidence.
 * Logic: Provide empty arrays for every audited accessibility/overflow category.
 * Constraints: Synthetic fixture only; no browser layout is measured. */
function surface(width, label = "Hold to speak") {
  return {width, horizontal_overflow: 0, duplicate_ids: [], unnamed_controls: [], small_touch_controls: [],
    visible_test_controls: [], visible_test_text: [], voice_hold_label: label};
}

/* Functionality: Construct fixed English/Chinese backend synthesis evidence.
 * Inputs: Number of requested cases. Outputs: Distinct language coverage with completed PCM lifecycle fields.
 * Logic: Alternate Chinese and English and mark every observed contract explicitly.
 * Constraints: Synthetic evidence validates predicates only; it does not represent an actual service call. */
function ttsCases(count) {
  return Array.from({length: count}, (_, index) => ({language: index % 2 ? "zh-CN" : "en-US",
    voice_id: "Cherry", service: "backend", started: true, ended: true, secure_connection: true,
    user_gesture: true, text_length: 4, expected_text_length: 4, rate: 2}));
}

/* Functionality: Construct complete synthetic report families for evaluator tests.
 * Inputs: None. Outputs: All report fields required by the fixed coverage manifest.
 * Logic: Include one exact model-alert/frame/audio identity and valid endpoint/lifecycle evidence.
 * Constraints: This fixture is not black-box or production evidence and must never be reported as such. */
function completeReports() {
  const agentChecks = [
    "live_assistant_reply_auto_spoken_by_backend_stream", "missing_destination_question_without_search_or_route",
    "device_origin_route_plan_navigation_verified", "read_progress_without_replanning", "stop_navigation",
    "assistant_preserves_product_camera_user_gesture_gate", "natural_language_camera_stop_preserves_idle_resource_state",
    "language_switch", "vibration_respects_user_enable", "speech_request_respects_user_enable_and_matches_english",
    "microphone_worklet_wav_qwen_asr_camera_stop", "responsive_320_390_1280",
  ];
  const productFrame = {provider: "local", model: "yolo26n", tracking_model: "botsort",
    depth_model: "yolo26n-depth", risk_model: "rules-v1"};
  const productChecks = {
    identity: {camera: true, navigation: true, speech: true, continuous_session: true},
    initial: {gated: true, camera_calls: 0, speech_calls: 0, gps_watches: 0, gps_one_shots: 0},
    english_voice: {id: "Cherry", service: "backend", languages: ["en-US", "zh-CN"]},
    mobile_surface: surface(390), desktop_surface: surface(1280), english_surface: surface(390),
    camera_request: {audio_false: true, exact_rear: true, user_gesture_active: true},
    activation: {audio_start_verified: true, user_gesture_active: true, physical_audibility_verified: false},
    camera_display: {preview_playing: true, result_decoded: true, result_blob: true, frames: 3},
    frames: Array.from({length: 3}, () => ({...productFrame})), detected_target_frames: 2,
    navigation: {original_controller_active: true}, planned_surface: {width: 390},
    cleanup: {tracks_ended: true, gps_released: true, camera_stream_cleared: true,
      speech_request_released: true, scheduler_released: true},
    diagnostics: {page_status: 200, backend_voice_selected: true},
    diagnostic_bilingual_tts: {cases: ttsCases(4)},
  };
  const cameraChecks = {
    initial: {gated: true, camera_calls: 0, speech_calls: 0, gps_watches: 0, gps_one_shots: 0},
    single_request: {audio_false: true, exact_rear: true, user_gesture_active: true},
    pending: {one_request: true, both_starts_disabled: true, stop_enabled: true},
    late_grant: {camera_idle: true, track_stopped: true, stream_absent: true, preview_absent: true, frames: 0},
    NotAllowedError: {actionable_message: true, visible_message: true, stream_absent: true},
    OverconstrainedError: {actionable_message: true, visible_message: true, stream_absent: true},
    unsupported: {actionable_message: true, frames: 0}, model_connections: 0,
  };
  const streamState = {tracks_ended: true, draw_timer_cleared: true};
  const stoppedState = {camera_mode: "idle", camera_stream_cleared: true, camera_client_cleared: true,
    last_socket_closed: true, media: {streams: [streamState]}};
  const lifecycle = {complete: true, failures: [], startup: {
    backend_voice: {voice: {id: "Cherry", service: "backend", languages: ["zh-CN", "en-US"]},
      camera_calls: 0, speech_calls: 0},
    guidance: {navigation_enabled: true, risk_enabled: true, paused: {speech: true},
      reasons: {speech: "activation_required"}},
    speech_activation: {ready: false}, navigation: {active: false, watch_active: false}, media: {requests: []},
  }, sessions: [
    {boundary: "original_stop_button", before: {camera_mode: "camera"}, after: {...stoppedState}},
    {boundary: "explicit_track_ended_fixture", before: {camera_mode: "camera"}, after: {...stoppedState}},
    {scope: "explicit_delayed_permission_fixture_second_hidden", hidden: {camera_mode: "idle",
      camera_events: [{type: "session"}]}, late: {camera_mode: "idle",
      media: {streams: [{purpose: "late_permission", ...streamState}]}, camera_events: [{type: "session"}]}},
    {scope: "explicit_delayed_inventory_fixture_second_hidden", hidden: {selected: "synthetic-camera",
      options: ["", "synthetic-camera"], status: "idle"}, late: {camera_mode: "idle",
      selected: "synthetic-camera", options: ["", "synthetic-camera"], status: "idle"}},
    {scope: "explicit_held_metadata_cancel_second_hidden", hidden: {video_mode: "idle",
      video_loading_cleared: true, video_file_cleared: true, metadata_boundary_held: true}, late: {video_mode: "idle",
      video_loading_cleared: true, video_file_cleared: true, metadata_boundary_held: true}},
  ], websockets: [
    {path: "/ws/control/", closed: false},
    ...Array.from({length: 2}, () => ({path: "/ws/detect/local/", closed: true, inputs: [{}],
      hellos: [{session_seconds: null}]})),
  ], final: {camera_mode: "idle", camera_stream_cleared: true, camera_client_cleared: true,
    last_socket_closed: true, navigation: {active: false, watch_active: false}, video_mode: "idle",
    video_loading_cleared: true, video_file_cleared: true, guidance: {risk_session_id: null,
      navigation_run_id: null}, media: {streams: [streamState]}},
  };
  const session = "session-one", requestId = 7;
  const alertFrame = {session_id: session, index: 12, captured_at_ms: 1800, alert_track_ids: [4],
    provider: "local", model: "yolo26n", tracking_model: "botsort", depth_model: "yolo26n-depth",
    risk_model: "rules-v1", count: 1};
  const report = {
    complete: true, target: "https://47.239.50.129/mvp/maps/test", tls_validation: "enabled",
    model_alert_gate_requested: true, model_alert_verified: true, model_alert_evidence: {backend_audio_ended: true},
    actual_model_verified: true, real_ors_verified: true,
    identity: {secure_context: true, canonical_camera: true, canonical_navigation: true, canonical_speech: true,
      control_session_id: "control-one"},
    checks: {guest_http_status: 200, permissions_policy: "camera=(self)",
      private_endpoints: Array.from({length: 4}, () => ({status: 404})),
      initial_camera_entry: {source: "camera", get_user_media_calls: 0,
        startup_outputs: {speech_calls: 0, gps_watches: 0}},
      camera_stop_preserves_navigation: true, navigation_stop_preserves_camera: true,
      first_session_drained: {stable_idle_observed: true}, second_session_drained: {stable_idle_observed: true},
      backend_tts_streams: {secure_sockets: 5, started_streams: 5, completed_streams: 3,
        interrupted_streams: 2, sample_rate: 24000, format: "pcm_s16le_mono"},
      manual_bilingual_tts: {cases: ttsCases(2)}},
    route: {status: 200, source: "real_public_ORS", provider: "openrouteservice", points: 3, steps: 2},
    gps_fixture: {source: "explicit_synthetic_watchPosition_NUS_Date_now", max_active_watch_count: 1,
      active_watch_count: 0, cleared_watch_count: 1, emitter_active: false},
    model_sessions: [{}, {}], frames: [alertFrame, ...Array.from({length: 5}, (_, index) => ({...alertFrame, index}))],
    output_evidence: {utterances: [{kind: "risk", synthetic_risk: false, session_id: session, frame_index: 12,
      captured_at_ms: 1800, track_id: 4, request_id: requestId, request_owned: true, backend_voice: true,
      text_matches_policy: true, capture_to_start_ms: 300}], audio: [{phase: "end", request_id: requestId}]},
    speech_activation: {audio_start_verified: true, user_gesture_active: true},
    sockets: [{secure: true, path: "/mvp/ws/control/", pongs: 1, welcomes: ["control-one"]}],
    cleanup: {camera_mode: "idle", navigation_state: "stopped", camera_stream_cleared: true,
      gps_watch_cleared: true, gps_deadline_cleared: true, scheduler_cleared: true,
      speech_request_cleared: true, camera_socket_closed: true},
  };
  report.sockets.push(...Array.from({length: 4}, () => ({secure: true, path: "/mvp/ws/tts/"})));
  return {agent: {complete: true, errors: [], checks: agentChecks},
    product: {complete: true, failures: [], checks: productChecks},
    camera: {complete: true, failures: [], checks: cameraChecks},
    lifecycle,
    relay: report};
}

/* Functionality: Verify missing reports cannot appear fully covered.
 * Inputs: Empty report-family map. Outputs: Zero coverage and all IDs missing.
 * Logic: Exercise the evaluator's required-report handling.
 * Constraints: No report file or service access. */
test("missing reports fail every required black-box scenario", () => {
  const result = evaluateBlackboxCoverage({});
  assert.equal(result.complete, false);
  assert.equal(result.gate_pass, false);
  assert.equal(result.covered, 0);
  assert.ok(result.total >= 50);
  assert.equal(result.missing.length, result.total);
  assert.ok(result.missing.some(item => item.id === "relay.model_alert_and_backend_speech"));
});

/* Functionality: Verify every manifest predicate can pass only with a complete fixture set.
 * Inputs: Explicit synthetic report-family objects. Outputs: Full computed coverage.
 * Logic: Exercise all independently required evidence families together.
 * Constraints: Test success proves validator behavior only; reports are not online evidence. */
test("complete synthetic evidence exercises the full manifest without external-service claims", () => {
  const result = evaluateBlackboxCoverage(completeReports());
  assert.equal(result.complete, true);
  assert.equal(result.gate_pass, true);
  assert.equal(result.covered, result.total);
  assert.equal(result.coverage_percent, 100);
  assert.deepEqual(result.missing, []);
});

/* Functionality: Ensure malformed reports and scheduler-only alerts stay uncovered.
 * Inputs: Complete synthetic fixture with targeted mutations. Outputs: Fixed missing case IDs.
 * Logic: Invalidate a status flag, failure-list schemas and exact real-model risk linkage independently.
 * Constraints: No synthetic utterance may satisfy production model-alert coverage. */
test("incomplete report schemas and synthetic-only risk fail the required coverage cases", () => {
  const reports = completeReports();
  reports.camera.complete = false;
  reports.agent.errors = null;
  reports.product.failures = "none";
  reports.relay.model_alert_verified = false;
  reports.relay.output_evidence.utterances[0].synthetic_risk = true;
  const result = evaluateBlackboxCoverage(reports);
  assert.equal(result.gate_pass, false);
  assert.ok(result.missing.some(item => item.id === "relay.model_alert_and_backend_speech"));
  assert.ok(result.missing.some(item => item.id === "camera.permission_startup_gate" && item.state === "report_incomplete"));
  assert.ok(result.missing.some(item => item.id.startsWith("agent.") && item.state === "report_incomplete"));
  assert.ok(result.missing.some(item => item.id.startsWith("product.") && item.state === "report_incomplete"));
});

/* Functionality: Reject absent, duplicate and unknown report-family CLI arguments.
 * Inputs: Explicit argument arrays. Outputs: normalized family map or fixed parser error.
 * Logic: exactly five unique family/path pairs are mandatory.
 * Constraints: Paths are parsed only; this test does not read or mutate files. */
test("coverage CLI requires one JSON report for every family", () => {
  const args = ["--agent", "a.json", "--product", "p.json", "--camera", "c.json",
    "--lifecycle", "l.json", "--relay", "r.json"];
  assert.equal(parseArguments(args).relay.endsWith("r.json"), true);
  assert.throws(() => parseArguments(args.slice(0, -2)), /INVALID_BLACKBOX_REPORT_ARGUMENTS/);
  assert.throws(() => parseArguments([...args, "--relay", "again.json"]), /INVALID_BLACKBOX_REPORT_ARGUMENTS/);
  assert.throws(() => parseArguments([...args, "--unknown", "x.json"]), /INVALID_BLACKBOX_REPORT_ARGUMENTS/);
});
