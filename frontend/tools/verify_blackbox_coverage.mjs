/* Responsibilities: Enforce evidence-based functional coverage for public black-box scenarios.
 * Implementation: Read five independent browser reports, validate their explicit evidence and fail
 * unless every required scenario has a complete, failure-free report.
 * Related Modules: check_agent_browser.mjs; check_product_browser.mjs; check_assist_lifecycle_browser.mjs;
 * check_phone_relay_browser.mjs; ../BLACKBOX-COVERAGE.md.
 * Declaration Index: SCENARIOS — required product/diagnostic/lifecycle/relay scenarios;
 * isRecord — validate JSON object boundaries; reportReady — reject incomplete or failed reports;
 * hasChecks — require named live-agent turns; surfaceReady — validate responsive/accessibility evidence;
 * bilingualTtsReady — validate real backend synthesis evidence in both languages;
 * cameraBoundariesReady — validate permission/cancel failure evidence;
 * realModelAlertReady — correlate one non-synthetic spoken alert to its actual model frame;
 * evaluateBlackboxCoverage — produce complete/missing evidence and gate status;
 * parseArguments — require one report path per scenario family; readReports — parse each input report;
 * main — write a body-free coverage summary and set the gate exit code.
 * Variable Index: INPUT_NAMES — five required report families; AGENT_CHECKS — required live-Qwen scenario IDs;
 * SCENARIOS — evidence predicates and public case identifiers; report objects contain no access credentials.
 * Constraints: Reports are evidence, not a substitute for physical-phone GPS, camera, vibration, or audibility testing.
 */
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {pathToFileURL} from "node:url";

const INPUT_NAMES = ["agent", "product", "camera", "lifecycle", "relay"];
const AGENT_CHECKS = [
  "live_assistant_reply_auto_spoken_by_backend_stream",
  "missing_destination_question_and_page_handoff",
  "device_origin_route_plan_navigation_verified",
  "read_progress_without_replanning",
  "stop_navigation",
  "assistant_preserves_product_camera_user_gesture_gate",
  "natural_language_camera_stop_preserves_idle_resource_state",
  "language_switch",
  "vibration_respects_user_enable",
  "speech_request_respects_user_enable_and_matches_english",
  "microphone_worklet_wav_qwen_asr_camera_stop",
  "responsive_320_390_1280",
];

/* Functionality: Identify a non-null, non-array evidence record.
 * Inputs: Arbitrary parsed JSON value. Outputs: Boolean.
 * Logic: Arrays and primitives cannot satisfy report/object contracts.
 * Constraints: No coercion or mutation. */
function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/* Functionality: Require a completed report without recorded failures.
 * Inputs: One parsed family report. Outputs: Boolean.
 * Logic: Require explicit completion and empty failure/error lists when present.
 * Constraints: Missing optional lists are permitted only for report schemas that do not define them. */
function reportReady(value) {
  return isRecord(value) && value.complete === true &&
    (!Object.hasOwn(value, "failures") || Array.isArray(value.failures) && value.failures.length === 0) &&
    (!Object.hasOwn(value, "errors") || Array.isArray(value.errors) && value.errors.length === 0);
}

/* Functionality: Require the expected list of natural-language agent interactions.
 * Inputs: Agent report and fixed required check IDs. Outputs: Boolean.
 * Logic: Every requested check must be present in the live report.
 * Constraints: Does not infer success from transcript text or report length. */
function hasChecks(report, names) {
  return Array.isArray(report?.checks) && names.every(name => report.checks.includes(name));
}

/* Functionality: Validate one viewport's observed layout and control accessibility.
 * Inputs: Surface evidence and expected viewport width. Outputs: Boolean.
 * Logic: Require no horizontal overflow or recorded duplicate/unnamed/undersized controls.
 * Constraints: The browser report does not prove physical device rendering. */
function surfaceReady(surface, width) {
  return isRecord(surface) && surface.width === width && surface.horizontal_overflow === 0 &&
    ["duplicate_ids", "unnamed_controls", "small_touch_controls", "visible_test_controls", "visible_test_text"]
      .every(name => Array.isArray(surface[name]) && surface[name].length === 0);
}

/* Functionality: Validate completed backend voice cases in Chinese and English.
 * Inputs: Diagnostic case list and minimum count. Outputs: Boolean.
 * Logic: Require both selected languages and every transport/PCM start/end invariant.
 * Constraints: Browser events prove synthesized playback lifecycle, not audible hardware output. */
function bilingualTtsReady(cases, minimum) {
  return Array.isArray(cases) && cases.length >= minimum &&
    cases.some(item => item.language === "en-US") && cases.some(item => item.language === "zh-CN") &&
    cases.every(item => item.voice_id === "Cherry" && item.service === "backend" && item.started === true &&
      item.ended === true && item.secure_connection === true && item.user_gesture === true &&
      item.text_length === item.expected_text_length && item.rate === 2);
}

/* Functionality: Validate the public camera permission and cancellation boundary matrix.
 * Inputs: Camera-boundary report. Outputs: Boolean.
 * Logic: Check exact rear-camera gesture, pending cancellation, late grant cleanup, denial and unsupported paths.
 * Constraints: Browser permission fixtures are not physical-camera evidence. */
function cameraBoundariesReady(report) {
  const checks = report?.checks;
  return isRecord(checks) && checks.initial?.gated === true && checks.initial.camera_calls === 0 &&
    checks.single_request?.audio_false === true && checks.single_request?.exact_rear === true &&
    checks.single_request?.user_gesture_active === true && checks.pending?.one_request === true &&
    checks.pending?.both_starts_disabled === true && checks.pending?.stop_enabled === true &&
    checks.late_grant?.camera_idle === true && checks.late_grant?.track_stopped === true &&
    checks.late_grant?.stream_absent === true && checks.late_grant?.preview_absent === true &&
    checks.NotAllowedError?.actionable_message === true && checks.NotAllowedError?.stream_absent === true &&
    checks.OverconstrainedError?.actionable_message === true && checks.OverconstrainedError?.stream_absent === true &&
    checks.unsupported?.actionable_message === true && checks.unsupported?.frames === 0 && checks.model_connections === 0;
}

/* Functionality: Verify a real risk utterance is linked to an alerting inference frame and completed audio.
 * Inputs: Strict phone-relay report with model frames, utterance metadata and audio lifecycle events.
 * Outputs: Boolean. Logic: Match session, capture time, frame index, track ID and audio request identity.
 * Constraints: Synthetic scheduler events, unmatched detections and hardware audibility do not count. */
function realModelAlertReady(report) {
  if (report?.model_alert_gate_requested !== true || report.model_alert_verified !== true ||
      report.model_alert_evidence?.backend_audio_ended !== true) return false;
  const utterances = report.output_evidence?.utterances, frames = report.frames, audio = report.output_evidence?.audio;
  if (!Array.isArray(utterances) || !Array.isArray(frames) || !Array.isArray(audio)) return false;
  return utterances.some(item => item.kind === "risk" && item.synthetic_risk === false &&
    item.request_owned === true && item.backend_voice === true && item.text_matches_policy === true &&
    item.capture_to_start_ms >= 0 && item.capture_to_start_ms < 1000 &&
    frames.some(frame => frame.session_id === item.session_id && frame.index === item.frame_index &&
      frame.captured_at_ms === item.captured_at_ms && frame.alert_track_ids.includes(item.track_id)) &&
    audio.some(event => event.phase === "end" && event.request_id === item.request_id));
}

const SCENARIOS = [
  ...AGENT_CHECKS.map(name => ({id: `agent.${name}`, source: "agent", verify: report => report.checks.includes(name)})),
  {id: "product.canonical_controllers", source: "product", verify: report =>
    report.checks.identity?.camera === true && report.checks.identity?.navigation === true &&
    report.checks.identity?.speech === true && report.checks.identity?.continuous_session === true},
  {id: "product.no_automatic_sensor_or_audio_start", source: "product", verify: report =>
    report.checks.initial?.gated === true && report.checks.initial.camera_calls === 0 &&
    report.checks.initial.speech_calls === 0 && report.checks.initial.gps_watches === 0 &&
    report.checks.initial.gps_one_shots === 0},
  {id: "product.backend_bilingual_voice", source: "product", verify: report =>
    report.checks.english_voice?.id === "Cherry" && report.checks.english_voice?.service === "backend" &&
    report.checks.english_voice?.languages?.includes("en-US") && report.checks.english_voice?.languages?.includes("zh-CN")},
  {id: "product.mobile_layout", source: "product", verify: report => surfaceReady(report.checks.mobile_surface, 390)},
  {id: "product.desktop_layout", source: "product", verify: report => surfaceReady(report.checks.desktop_surface, 1280)},
  {id: "product.english_surface", source: "product", verify: report =>
    surfaceReady(report.checks.english_surface, 390) && report.checks.english_surface.voice_hold_label === "Hold to speak"},
  {id: "product.exact_rear_camera_gesture", source: "product", verify: report =>
    report.checks.camera_request?.audio_false === true && report.checks.camera_request?.exact_rear === true &&
    report.checks.camera_request?.user_gesture_active === true},
  {id: "product.backend_audio_activation", source: "product", verify: report =>
    report.checks.activation?.audio_start_verified === true && report.checks.activation?.user_gesture_active === true &&
    report.checks.activation?.physical_audibility_verified === false},
  {id: "product.decoded_camera_preview", source: "product", verify: report =>
    report.checks.camera_display?.preview_playing === true && report.checks.camera_display?.result_decoded === true &&
    report.checks.camera_display?.result_blob === true && report.checks.camera_display.frames >= 1},
  {id: "product.real_model_frame_contract", source: "product", verify: report =>
    Array.isArray(report.checks.frames) && report.checks.frames.length >= 3 &&
    report.checks.frames.every(frame => frame.provider === "local" && frame.model === "yolo26n" &&
      frame.tracking_model === "botsort" && frame.depth_model === "yolo26n-depth" && frame.risk_model === "rules-v1")},
  {id: "product.visible_target_detection", source: "product", verify: report => report.checks.detected_target_frames >= 1},
  {id: "product.route_navigation_state", source: "product", verify: report =>
    report.checks.navigation?.original_controller_active === true && report.checks.planned_surface?.width === 390},
  {id: "product.camera_navigation_cleanup", source: "product", verify: report =>
    report.checks.cleanup?.tracks_ended === true && report.checks.cleanup?.gps_released === true &&
    report.checks.cleanup?.camera_stream_cleared === true && report.checks.cleanup?.speech_request_released === true &&
    report.checks.cleanup?.scheduler_released === true},
  {id: "product.public_diagnostic_and_tts", source: "product", verify: report =>
    report.checks.diagnostics?.page_status === 200 && report.checks.diagnostics?.backend_voice_selected === true &&
    bilingualTtsReady(report.checks.diagnostic_bilingual_tts?.cases, 4)},
  {id: "camera.permission_startup_gate", source: "camera", verify: report => report.checks.initial?.gated === true &&
    report.checks.initial.camera_calls === 0 && report.checks.initial.speech_calls === 0 &&
    report.checks.initial.gps_watches === 0 && report.checks.initial.gps_one_shots === 0},
  {id: "camera.explicit_rear_request", source: "camera", verify: report =>
    report.checks.single_request?.exact_rear === true && report.checks.single_request?.audio_false === true &&
    report.checks.single_request?.user_gesture_active === true},
  {id: "camera.pending_permission_cancel", source: "camera", verify: report =>
    report.checks.pending?.one_request === true && report.checks.pending?.both_starts_disabled === true &&
    report.checks.pending?.stop_enabled === true},
  {id: "camera.cancel_late_permission_grant", source: "camera", verify: report =>
    report.checks.late_grant?.camera_idle === true && report.checks.late_grant?.track_stopped === true &&
    report.checks.late_grant?.stream_absent === true && report.checks.late_grant?.frames === 0},
  {id: "camera.permission_denial_cleanup", source: "camera", verify: report =>
    report.checks.NotAllowedError?.actionable_message === true && report.checks.NotAllowedError?.visible_message === true &&
    report.checks.NotAllowedError?.stream_absent === true},
  {id: "camera.constraint_failure_cleanup", source: "camera", verify: report =>
    report.checks.OverconstrainedError?.actionable_message === true &&
    report.checks.OverconstrainedError?.visible_message === true && report.checks.OverconstrainedError?.stream_absent === true},
  {id: "camera.unsupported_and_no_model_start", source: "camera", verify: report =>
    report.checks.unsupported?.actionable_message === true && report.checks.unsupported?.frames === 0 &&
    report.checks.model_connections === 0},
  {id: "lifecycle.backend_voice_no_startup", source: "lifecycle", verify: report =>
    report.checks.backend_voice?.voice?.service === "backend" && report.checks.backend_voice?.voice?.id === "Cherry" &&
    report.checks.backend_voice?.camera_calls === 0 && report.checks.backend_voice?.speech_calls === 0},
  {id: "lifecycle.mobile_surface", source: "lifecycle", verify: report => surfaceReady(report.checks.mobile_surface, 390)},
  {id: "lifecycle.desktop_surface", source: "lifecycle", verify: report => surfaceReady(report.checks.desktop_surface, 1280)},
  {id: "lifecycle.cached_page_no_autoplay", source: "lifecycle", verify: report =>
    report.checks.cached_page?.product_copy_updates === true && report.checks.cached_page?.backend_voice_stable === true &&
    report.checks.cached_page?.automatic_speech_calls === 0},
  {id: "lifecycle.incomplete_route_gate", source: "lifecycle", verify: report =>
    report.checks.incomplete_route?.route_absent === true && report.checks.incomplete_route?.speech_calls === 0 &&
    report.checks.incomplete_route?.camera_calls === 0},
  {id: "lifecycle.user_gesture_audio_activation", source: "lifecycle", verify: report =>
    report.checks.activation?.audio_start_verified === true && report.checks.activation?.user_gesture_active === true},
  {id: "lifecycle.hidden_page_releases_resources", source: "lifecycle", verify: report =>
    report.checks.hidden?.navigation_stopped === true && report.checks.hidden?.gps_released === true &&
    report.checks.hidden?.speech_released === true && report.checks.hidden?.timer_released === true},
  {id: "lifecycle.return_does_not_restart", source: "lifecycle", verify: report =>
    report.checks.returned?.navigation_stopped === true && report.checks.returned?.gps_released === true &&
    report.checks.returned?.speech_released === true},
  {id: "lifecycle.bilingual_backend_tts", source: "lifecycle", verify: report =>
    bilingualTtsReady(report.checks.diagnostic_bilingual_tts?.cases, 4)},
  {id: "relay.public_tls_product_entry", source: "relay", verify: report =>
    report.target === "https://47.239.50.129/mvp/maps/test" && report.tls_validation === "enabled" &&
    report.checks.guest_http_status === 200 && report.checks.permissions_policy?.includes("camera=(self)")},
  {id: "relay.private_routes_not_exposed", source: "relay", verify: report =>
    Array.isArray(report.checks.private_endpoints) && report.checks.private_endpoints.length === 4 &&
    report.checks.private_endpoints.every(item => item.status === 404)},
  {id: "relay.canonical_secure_controllers", source: "relay", verify: report =>
    report.identity?.secure_context === true && report.identity?.canonical_camera === true &&
    report.identity?.canonical_navigation === true && report.identity?.canonical_speech === true &&
    report.sockets.length > 0 && report.sockets.every(socket => socket.secure === true)},
  {id: "relay.camera_first_no_device_acquisition", source: "relay", verify: report =>
    report.checks.initial_camera_entry?.source === "camera" &&
    report.checks.initial_camera_entry?.get_user_media_calls === 0 &&
    report.checks.initial_camera_entry?.startup_outputs?.speech_calls === 0 &&
    report.checks.initial_camera_entry?.startup_outputs?.gps_watches === 0},
  {id: "relay.live_ors_route", source: "relay", verify: report =>
    report.real_ors_verified === true && report.route?.status === 200 && report.route?.source === "real_public_ORS" &&
    report.route?.provider === "openrouteservice" && report.route?.points >= 2 && report.route?.steps >= 1},
  {id: "relay.synthetic_gps_ownership_and_cleanup", source: "relay", verify: report =>
    report.gps_fixture?.source === "explicit_synthetic_watchPosition_NUS_Date_now" &&
    report.gps_fixture?.max_active_watch_count === 1 && report.gps_fixture?.active_watch_count === 0 &&
    report.gps_fixture?.cleared_watch_count >= 1 && report.gps_fixture?.emitter_active === false},
  {id: "relay.actual_local_model_sessions", source: "relay", verify: report =>
    report.actual_model_verified === true && Array.isArray(report.model_sessions) && report.model_sessions.length === 2 &&
    Array.isArray(report.frames) && report.frames.length >= 6 && report.frames.every(frame =>
      frame.provider === "local" && frame.model === "yolo26n" && frame.count > 0 &&
      frame.tracking_model === "botsort" && frame.depth_model === "yolo26n-depth" && frame.risk_model === "rules-v1")},
  {id: "relay.model_alert_and_backend_speech", source: "relay", verify: realModelAlertReady},
  {id: "relay.control_websocket_identity_and_heartbeat", source: "relay", verify: report =>
    report.sockets.some(socket => socket.path === "/mvp/ws/control/" && socket.pongs > 0 &&
      socket.welcomes?.includes(report.identity?.control_session_id))},
  {id: "relay.navigation_and_camera_stop_independently", source: "relay", verify: report =>
    report.checks.camera_stop_preserves_navigation === true && report.checks.navigation_stop_preserves_camera === true},
  {id: "relay.model_sessions_drain", source: "relay", verify: report =>
    report.checks.first_session_drained?.stable_idle_observed === true &&
    report.checks.second_session_drained?.stable_idle_observed === true},
  {id: "relay.backend_tts_stream_and_cancel", source: "relay", verify: report =>
    report.checks.backend_tts_streams?.secure_sockets >= 4 && report.checks.backend_tts_streams?.started_streams >= 4 &&
    report.checks.backend_tts_streams?.completed_streams >= 2 &&
    report.checks.backend_tts_streams?.interrupted_streams >= 0 &&
    report.checks.backend_tts_streams?.sample_rate === 24000 &&
    report.checks.backend_tts_streams?.format === "pcm_s16le_mono"},
  {id: "relay.manual_bilingual_tts", source: "relay", verify: report =>
    bilingualTtsReady(report.checks.manual_bilingual_tts?.cases, 2)},
  {id: "relay.startup_activation_and_cleanup", source: "relay", verify: report =>
    report.speech_activation?.audio_start_verified === true && report.speech_activation?.user_gesture_active === true &&
    report.cleanup?.camera_mode === "idle" && report.cleanup?.navigation_state === "stopped" &&
    report.cleanup?.camera_stream_cleared === true && report.cleanup?.gps_watch_cleared === true &&
    report.cleanup?.gps_deadline_cleared === true && report.cleanup?.scheduler_cleared === true &&
    report.cleanup?.speech_request_cleared === true && report.cleanup?.camera_socket_closed === true},
];

/* Functionality: Evaluate all required live functional scenarios against separate report families.
 * Inputs: Map of parsed report objects keyed by INPUT_NAMES. Outputs: Safe counts, missing IDs and gate result.
 * Logic: A case counts only when its source report is complete, failure-free and satisfies its predicate.
 * Constraints: Every case is required; a percentage cannot hide a missing critical scenario. */
export function evaluateBlackboxCoverage(reports) {
  const scenarios = SCENARIOS.map(item => {
    const report = reports?.[item.source];
    const ready = reportReady(report);
    let covered = false;
    if (ready) {
      try { covered = item.verify(report) === true; } catch { covered = false; }
    }
    return {id: item.id, covered, state: covered ? "covered" : !report ? "report_missing" :
      !ready ? "report_incomplete" : "evidence_missing"};
  });
  const covered = scenarios.filter(item => item.covered).length;
  return {complete: covered === scenarios.length, gate_pass: covered === scenarios.length,
    covered, total: scenarios.length, coverage_percent: Number((100 * covered / scenarios.length).toFixed(2)),
    missing: scenarios.filter(item => !item.covered).map(item => ({id: item.id, state: item.state})), scenarios};
}

/* Functionality: Parse exact CLI report-family options.
 * Inputs: command arguments. Outputs: absolute report paths keyed by INPUT_NAMES.
 * Logic: reject missing, duplicate, unknown, non-JSON or extra arguments before reading reports.
 * Constraints: Does not accept URLs, credential files or write destinations. */
export function parseArguments(argv) {
  const paths = {};
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index]?.replace(/^--/, "");
    const path = argv[index + 1];
    if (!INPUT_NAMES.includes(name) || !path || paths[name] || !path.toLowerCase().endsWith(".json"))
      throw new TypeError("INVALID_BLACKBOX_REPORT_ARGUMENTS");
    paths[name] = resolve(path);
  }
  if (Object.keys(paths).length !== INPUT_NAMES.length || INPUT_NAMES.some(name => !paths[name]))
    throw new TypeError("INVALID_BLACKBOX_REPORT_ARGUMENTS");
  return paths;
}

/* Functionality: Read all five report families as UTF-8 JSON.
 * Inputs: Exact parsed family paths. Outputs: family-to-object map.
 * Logic: Parse each file once and reject non-record top-level JSON values.
 * Constraints: Read-only; reports are never modified or copied to a new location. */
export async function readReports(paths) {
  const reports = {};
  for (const name of INPUT_NAMES) {
    const value = JSON.parse(await readFile(paths[name], "utf8"));
    if (!isRecord(value)) throw new TypeError("INVALID_BLACKBOX_REPORT");
    reports[name] = value;
  }
  return reports;
}

/* Functionality: Run the required online black-box coverage gate.
 * Inputs: Five explicit report paths. Outputs: JSON summary and process status.
 * Logic: report every scenario and exit nonzero whenever evidence is missing.
 * Constraints: No network requests, browser launch, inference, report mutation or success by threshold rounding. */
export async function main(argv = process.argv.slice(2)) {
  try {
    const result = evaluateBlackboxCoverage(await readReports(parseArguments(argv)));
    process.stdout.write(JSON.stringify(result, null, 2) + "\n");
    if (!result.gate_pass) process.exitCode = 1;
    return result;
  } catch {
    process.stderr.write("INVALID_BLACKBOX_REPORT_ARGUMENTS_OR_CONTENT\n");
    process.exitCode = 2;
    return null;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
