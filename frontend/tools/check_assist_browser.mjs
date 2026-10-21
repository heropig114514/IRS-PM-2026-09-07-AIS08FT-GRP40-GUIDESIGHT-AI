/* Responsibilities: Verify the unified local assist entry through Chrome, MCP and native Chinese TTS.
 * Implementation: Explicit GPS/ORS fixtures surround unchanged controllers; default-on controls
 * stay gated until a real navigation-start click produces native calibration speech; synthetic
 * arbitration and optional real local-file PRF2 inference have separate evidence partitions.
 * Related Modules: integration/assist-mvp.js/assist-guidance.js/video-replay.js; navigation-ui.js,
 * camera.js, ai-control.js, speech-output.js; check_local_navigation_browser.mjs; integration.assist.
 * Declaration Index: installSpeechProbe; installSpeechProbe.observedSpeak; installGPS; installGPS.fixture.fix; installGPS.fixture.periodic;
 * installGPS.fixture.stop; installGPS.fixture.describe; installGPS.geo.watchPosition;
 * installGPS.geo.clearWatch; installGPS.geo.getCurrentPosition; observe; observe.recordGuidance;
 * observe.recordDriver; observe.recordFrame; observe.recordNavigation; capture; resetProbe;
 * verifyAssistSession; runTool; planRoute; enableSpeech; deliverFix; waitOutput; assertStopped;
 * navigationSession; syntheticSession; videoSession; setVisibility; checkLayouts; requireNew;
 * validateArguments; safeFailure; run; run.fixtureRoute; run.requested; run.responded;
 * run.socketOpened; run.socketOpened.received; run.socketOpened.sent; main.
 * Variable Index: ROUTE and FIX are explicit synthetic route/sensor inputs, never saved as location
 * evidence. MCP_SCRIPT invokes the existing outputs tools. assistProbe stores body-free lifecycle
 * and validated frame identities; real model metadata is never replaced or generated in video mode.
 * Constraints: No service/Git changes, GPU mocks, retries, camera permission, native speech-event
 * simulation, media seek/loop, CDN replacement, coordinate/utterance/credential serialization or
 * physical-audibility claim. Missing dependencies, voices or fresh actual alerts fail explicitly.
 */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {access, mkdir, stat, writeFile} from "node:fs/promises";
import {resolve, dirname} from "node:path";
import {pathToFileURL} from "node:url";
import {verifySpeech, waitForGate} from "./check_local_navigation_browser.mjs";

const FIX = [103.7771, 1.2971];
const ROUTE = {provider: "openrouteservice", profile: "foot-walking", attribution: "Explicit synthetic assist fixture",
  geometry: {type: "LineString", coordinates: [[103.7766, 1.2971], [103.7776, 1.2971],
    [103.7776, 1.2981], [103.7786, 1.2981]]}, summary: {distance_m: 333, duration_s: 270}, steps: [
    {type: 11, instruction: "Head east (synthetic)", distance_m: 111, duration_s: 90, way_points: [0, 1]},
    {type: 0, instruction: "Turn left (synthetic)", distance_m: 111, duration_s: 90, way_points: [1, 2]},
    {type: 1, instruction: "Turn right (synthetic)", distance_m: 111, duration_s: 90, way_points: [2, 3]},
    {type: 10, instruction: "Arrive (synthetic)", distance_m: 0, duration_s: 0, way_points: [3, 3]},
  ]};
const MCP_SCRIPT = `
import asyncio,json,sys
from mcp.client import Client
async def run():
    # Functionality: Invoke one original navigation tool. Inputs: fixed argv. Outputs: body-free result.
    # Logic: discover outputs tools and call once. Constraints: no retries or service/device changes.
    async with Client(sys.argv[1]) as client:
        names={tool.name for tool in (await client.list_tools()).tools}
        assert {"navigation_start","navigation_stop","navigation_status"} <= names
        result=await client.call_tool(sys.argv[3], {"session_id":sys.argv[2]})
        assert not result.is_error, "MCP returned an error"
        value=result.structured_content
        print(json.dumps({key:value.get(key) for key in
            ("accepted","reason","state","active","operation")}))
asyncio.run(run())
`;

/* Functionality: Observe genuine engine submissions before page initialization without saving text.
 * Inputs: native browser engine. Outputs: body-free gesture/timing records, unchanged native calls.
 * Logic: synchronous delegation preserves user activation; Constraints: no generated calls or events. */
export function installSpeechProbe() {
  window.__assistNativeSpeechCalls = [];
  const native = window.speechSynthesis?.speak?.bind(window.speechSynthesis);
  /* Functionality: Retain genuine speak invocation and its browser transient activation.
   * Inputs: original utterance. Outputs: native return. Logic: delegate unchanged and synchronously.
   * Constraints: never synthesize start/end or retain the speech body. */
  function observedSpeak(utterance) {
    window.__assistNativeSpeechCalls.push({at: performance.now(), user_gesture_active: navigator.userActivation?.isActive === true});
    return native(utterance);
  }
  if (native) window.speechSynthesis.speak = observedSpeak;
}

/* Functionality: Install an explicit native-sensor boundary before module initialization.
 * Inputs: Browser environment. Outputs: One-watch GPS fixture with actual epoch timestamps.
 * Logic: Retain callbacks and count original ownership; periodic fixes are explicitly controlled.
 * Constraints: No real positioning, core replacement, forged clocks or saved coordinates. */
function installGPS() {
  const watches = [], cleared = [];
  let timer = null, maxActive = 0, fixes = 0, oneShots = 0;
  const fixture = {
    /* Functionality: Deliver one explicit sensor observation. Inputs: synthetic point and optional old ID.
     * Outputs: Original callback. Logic: real Date.now source time; Constraints: never mutates the core. */
    fix(point, id = watches.length - 1) {
      if (!watches[id]) throw new Error("No original GPS watch registered");
      fixes++; watches[id].success({coords: {longitude: point[0], latitude: point[1], accuracy: 5}, timestamp: Date.now()});
    },
    /* Functionality: Supply explicitly repeated sensor fixes while a video is running.
     * Inputs: Synthetic point. Outputs: One2s fixture timer. Logic: no extra GPS listener.
     * Constraints: not a real sensor or navigation controller; no implicit restart. */
    periodic(point) { fixture.stop(); timer = setInterval(() => {
      if (watches.at(-1)?.active) fixture.fix(point);
    }, 2000); },
    /* Functionality: Clear the fixture emitter. Inputs: None. Outputs: Cleared timer.
     * Logic: only fixture resources; Constraints: does not stop production navigation. */
    stop() { if (timer !== null) clearInterval(timer); timer = null; },
    /* Functionality: Describe registration evidence. Inputs: Fixture state. Outputs: Body-free counts.
     * Logic: omit source coordinates and callbacks; Constraints: no physical sensor claim. */
    describe() { return {watch_count: watches.length, active_watch_count: watches.filter(item => item.active).length,
      max_active_watch_count: maxActive, cleared: [...cleared], fixes, one_shot_requests: oneShots,
      emitter_active: timer !== null}; },
  };
  const geo = {
    /* Functionality: Record original watch registration. Inputs: callbacks/options. Outputs: Unique ID.
     * Logic: reject concurrent owners; Constraints: no implicit observation or permission. */
    watchPosition(success, error, options) {
      const id = watches.length; watches.push({id, active: true, success, error, options});
      maxActive = Math.max(maxActive, watches.filter(item => item.active).length);
      if (maxActive > 1) throw new Error("Concurrent GPS owners"); return id;
    },
    /* Functionality: Record original watch release. Inputs: ID. Outputs: Inactive handle.
     * Logic: reject unknown/double release; Constraints: retains callbacks only for late-event checks. */
    clearWatch(id) { if (!watches[id]?.active) throw new Error("Unknown/double GPS cleanup");
      watches[id].active = false; cleared.push(id); },
    /* Functionality: Fail unintended one-shot acquisition. Inputs: None. Outputs: Explicit error/count.
     * Logic: fixture route uses existing form only; Constraints: no fallback position. */
    getCurrentPosition() { oneShots++; throw new Error("Unexpected one-shot GPS request"); },
  };
  window.__assistGPS = fixture;
  Object.defineProperty(navigator, "geolocation", {configurable: true, value: geo});
}

/* Functionality: Observe canonical controllers and genuine native speech/source events.
 * Inputs: Original assist page. Outputs: In-memory probes and singleton evidence.
 * Logic: Compare live utterance with its frozen source formatter without serializing either body.
 * Constraints: No enablement, output-event fabrication, algorithm mutation or media retention. */
async function observe() {
  const glue = await import("/assets/integration/assist-mvp.js"); await glue.assistReady;
  const {cameraApp} = await import("/assets/camera.js");
  const {navigation} = await import("/assets/navigation-ui.js");
  const {speechOutput: driver} = await import("/assets/speech-output.js");
  const ai = await import("/assets/ai-control.js");
  const {navigationSpeechText} = await import("/assets/integration/navigation-speech.js");
  const {videoAlertText} = await import("/assets/integration/video-alerts.js");
  const manager = glue.guidanceManager, bridge = glue.navigationBridge, video = glue.videoReplay;
  if (glue.cameraApp !== cameraApp || glue.navigation !== navigation || glue.speechOutput !== driver ||
      manager.speech !== driver || bridge.adapter?.ingest !== manager.navPort.ingest)
    throw new Error("Assist canonical controller identity mismatch");
  window.assistControllers = {cameraApp, navigation, driver, manager, bridge, video, activation: glue.speechActivation};
  window.assistProbe = {speech: [], driver: [], utterances: [], frames: [], navigation: [], sources: []};
  /* Functionality: Bind genuine native starts to original source/voice/rate. Inputs: Manager event.
   * Outputs: Body-free lifecycle/binding evidence. Logic: source ownership and pure formatter comparisons.
   * Constraints: Synthetic risk and actual validated model frames remain separately labeled sessions. */
  function recordGuidance(event) {
    window.assistProbe.speech.push(structuredClone(event));
    if (event.phase !== "output_start" || event.channel !== "speech") return;
    const active = manager.active.speech, last = driver.lastRequest, utterance = driver.current;
    const owned = active?.requestId === event.request_id && last?.id === event.request_id;
    const expected = active?.kind === "risk" ? videoAlertText(active.box, last.language)
      : navigationSpeechText(active?.snapshot, last.language);
    const frame = active?.kind === "risk" && window.assistProbe.frames.find(item =>
      item.session_id === active.source.session_id && item.index === active.source.frame_index &&
      item.captured_at_ms === active.source.captured_at_ms && item.required_track_ids.includes(active.box.track_id));
    window.assistProbe.utterances.push({...event, request_owned: owned,
      text_matches_policy: Boolean(owned && utterance?.text && utterance.text === expected),
      local_chinese_voice: Boolean(owned && last.language === "zh-CN" && last.local_service === true &&
        utterance?.voice?.localService === true && /^zh(?:-|$)/i.test(utterance.voice.lang)),
      rate_matches_user_setting: Boolean(owned && utterance?.rate === last.rate &&
        last.rate === Number(document.getElementById("speech-rate").value)),
      configured_rate: last?.rate ?? null, engine_rate: utterance?.rate ?? null,
      source_matches_validated_frame: Boolean(frame)});
  }
  /* Functionality: Preserve original native driver lifecycle. Inputs: Driver event. Outputs: Detached event.
   * Logic: no phase/count synthesis; Constraints: no utterance serialization. */
  function recordDriver(event) { window.assistProbe.driver.push(structuredClone(event)); }
  /* Functionality: Preserve real validated PRF2 frame identity. Inputs: Actual replay event.
   * Outputs: Body-free source/model timing and alert-track references.
   * Logic: observe after original integration subscriber; Constraints: no JPEG/boxes or generated predictions. */
  function recordFrame(event) {
    if (event.type !== "frame") {
      window.assistProbe.sources.push({type: event.type, session_id: event.sessionId ?? null,
        session_seconds: event.contract?.session_seconds, protocol_version: event.contract?.protocol_version,
        source: event.source ?? null, provider: event.provider ?? null, reason: event.reason ?? null}); return;
    }
    const metadata = event.result.metadata;
    window.assistProbe.frames.push({session_id: event.sessionId, index: metadata.index,
      captured_at_ms: event.capturedAt, capture_time_s: metadata.capture_time_s,
      timestamp_source: metadata.timestamp_source, model: metadata.model, provider: metadata.provider,
      tracking_epoch: metadata.tracking_epoch ?? null, count: metadata.count,
      required_track_ids: metadata.boxes.filter(box => box.alert_required === true).map(box => box.track_id),
      inference_ms: metadata.inference_ms, capture_to_result_ms: event.timing?.captureToResultMs ?? null});
  }
  /* Functionality: Preserve original semantic states without positions. Inputs: DOM snapshot event.
   * Outputs: State/step/time evidence. Logic: select safe fields; Constraints: no route or coordinate copies. */
  function recordNavigation(event) {
    const value = event.detail;
    window.assistProbe.navigation.push({state: value.state, reason: value.reason, active: value.active,
      step_index: value.progress?.step_index ?? null, source_timestamp: value.position?.timestamp ?? null});
  }
  manager.subscribe(recordGuidance); driver.subscribe(recordDriver); video.subscribe(recordFrame);
  document.addEventListener("prs-navigation-change", recordNavigation);
  return {canonical_camera: glue.cameraApp === cameraApp, canonical_navigation: glue.navigation === navigation,
    canonical_speech: glue.speechOutput === driver && manager.speech === driver,
    canonical_nav_port_ingest: bridge.adapter.ingest === manager.navPort.ingest,
    ai_navigation_status_matches: ai.performNavigation("navigation_status").state === navigation.describe().state,
    camera_local_session_seconds: cameraApp.localSessionSeconds};
}

/* Functionality: Capture only report-safe evidence. Inputs: Page. Outputs: Detached probe/cleanup.
 * Logic: describe ownership rather than source bodies; Constraints: never serialize navigation.describe wholesale. */
async function capture(page) {
  return page.evaluate(() => {
    const {navigation, driver, manager, bridge, video, cameraApp} = window.assistControllers;
    const nav = navigation.describe();
    return {...structuredClone(window.assistProbe), cleanup: {navigation: {state: nav.state, active: nav.active,
      reason: nav.reason, step_index: nav.progress?.step_index ?? null}, guidance: manager.describe(),
      bridge: bridge.describe(), gps: __assistGPS.describe(), video_mode: video.mode, video_active: video.active(),
      video_stats: video.stats, camera_mode: cameraApp.mode, gps_watch_cleared: navigation.watchId === null,
      gps_deadline_cleared: navigation.timer === null, scheduler_cleared: manager.schedulerTimer === null,
      native_utterance_cleared: driver.current === null,
      speech_activation: window.assistControllers.activation.describe(),
      local_socket_closed: !video.client?.socket || video.client.socket.readyState === 3}};
  });
}

/* Functionality: Partition observations between explicit sessions. Inputs: Page. Outputs: Empty probe.
 * Logic: does not mutate controllers or clocks; Constraints: previous session must already be fully closed. */
async function resetProbe(page) {
  await page.evaluate(() => { window.assistProbe = {speech: [], driver: [], utterances: [], frames: [], navigation: [], sources: []}; });
}

/* Functionality: Verify every genuine speech request and optional real-frame risk provenance.
 * Inputs: Complete evidence partition, actual-model flag/minimum risk starts. Outputs: Lifecycle counts.
 * Logic: reuse exact-ID native verifier; require each real risk start to bind a validated source frame.
 * Constraints: no aggregates substituted for per-request completion, or synthetic results accepted as inference. */
export function verifyAssistSession(session, {actualModel = false, minimumRiskStarts = 0} = {}) {
  const speech = session.speech.filter(event => event.channel === "speech");
  const summary = verifySpeech({...session, speech});
  const risk = session.utterances.filter(event => event.kind === "risk");
  assert.ok(risk.length >= minimumRiskStarts, "Insufficient actual risk native starts in this evidence partition");
  if (actualModel) {
    assert.ok(session.frames.length > 0, "No validated model frame observed");
    assert.ok(risk.every(event => event.source_matches_validated_frame === true &&
      Number.isFinite(event.capture_to_start_ms) && event.capture_to_start_ms >= 0 && event.capture_to_start_ms < 1000 &&
      session.frames.some(frame => frame.session_id === event.session_id && frame.index === event.frame_index &&
        frame.captured_at_ms === event.captured_at_ms && frame.required_track_ids.includes(event.track_id) &&
        frame.provider === "local" && frame.model === "yolo26n" && frame.timestamp_source === "browser_capture")),
    "Real risk native start did not bind a fresh validated model frame");
  }
  return {...summary, risk_started: risk.length,
    navigation_started: session.utterances.filter(event => event.kind === "navigation").length,
    actual_model_verified: actualModel};
}

/* Functionality: Invoke the real outputs-profile navigation MCP path once.
 * Inputs: URL/session/fixed command. Outputs: Body-free structured result.
 * Logic: official Python client and original page control socket; Constraints: no shell/retry/services or source bodies. */
async function runTool(url, sessionId, name) {
  assert.ok(["navigation_start", "navigation_status", "navigation_stop"].includes(name));
  const {stdout} = await promisify(execFile)(process.env.PYTHON,
    ["-c", MCP_SCRIPT, new URL("../mcp", url).href, sessionId, name], {windowsHide: true, timeout: 20000});
  return JSON.parse(stdout.trim());
}

/* Functionality: Plan one explicit fixture using original map controls. Inputs: Page. Outputs: Ready route.
 * Logic: intercept only HTTP response boundary; Constraints: no direct core route installation or ORS request. */
async function planRoute(page) {
  await page.click("#example");
  await page.locator("#end-longitude").fill(String(ROUTE.geometry.coordinates.at(-1)[0]));
  await page.locator("#end-latitude").fill(String(ROUTE.geometry.coordinates.at(-1)[1]));
  await page.click("#plan");
  await page.waitForFunction(() => !document.getElementById("navigation-start").disabled &&
    window.assistControllers.navigation.describe().state === "ready", null, {timeout: 10000});
}

/* Functionality: Explicitly choose a local Chinese voice while preserving unified default-on switches.
 * Inputs: Page/native inventory. Outputs: Unchanged2x configuration metadata.
 * Logic: existing controls; Constraints: no default/remote substitution or rate adjustment. */
async function enableSpeech(page) {
  await page.waitForFunction(() => speechSynthesis.getVoices().length > 0, null, {timeout: 5000});
  await page.click("#navigation-voices-refresh");
  const voices = await page.locator("#navigation-speech-voice option").evaluateAll(options =>
    options.filter(option => option.value && !option.disabled).map(option => option.value));
  assert.ok(voices.length, "Installed local Chinese voice required; no substitute");
  assert.equal(await page.inputValue("#navigation-speech-language"), "zh-CN");
  await page.selectOption("#navigation-speech-voice", voices[0]);
  assert.equal(await page.isChecked("#speech-enabled"), true);
  assert.equal(await page.isChecked("#navigation-speech-enabled"), true);
  assert.equal(Number(await page.inputValue("#speech-rate")), 2);
  return {language: "zh-CN", voice_id: voices[0], rate: 2, physical_audibility_verified: false};
}

/* Functionality: Deliver one explicit fix through the sensor fixture. Inputs: Page.
 * Outputs: Original navigation effects. Logic: real source time; Constraints: no direct core mutation. */
async function deliverFix(page) { await page.evaluate(point => __assistGPS.fix(point), FIX); }

/* Functionality: Bound one actual output lifecycle observation. Inputs: Page/phase/kind/count.
 * Outputs: Observed count or explicit failure. Logic: fail channel errors without waiting for a retry.
 * Constraints: no synthetic start/end or silent timeout recovery. */
async function waitOutput(page, phase, kind, count = 1, timeout = 15000) {
  await page.waitForFunction(({phase, kind, count}) => window.assistProbe.speech.some(event =>
    event.phase === "paused" && event.channel === "speech" || event.phase === "output_error" && event.channel === "speech") ||
    window.assistProbe.speech.filter(event => event.channel === "speech" && event.phase === phase && event.kind === kind).length >= count,
  {phase, kind, count}, {timeout, polling: "raf"});
  assert.equal((await capture(page)).cleanup.guidance.paused.speech, false, "Native speech channel failed");
}

/* Functionality: Require closed original source/output ownership. Inputs: Capture. Outputs: Assertions.
 * Logic: timers, GPS, replay/WS and native speech must agree; Constraints: no immediate external GPU-release claim. */
function assertStopped(result) {
  const state = result.cleanup;
  assert.equal(state.navigation.active, false); assert.equal(state.gps.active_watch_count, 0);
  assert.ok(state.gps.max_active_watch_count <= 1); assert.equal(state.gps.one_shot_requests, 0);
  for (const name of ["gps_watch_cleared", "gps_deadline_cleared", "scheduler_cleared", "native_utterance_cleared", "local_socket_closed"])
    assert.equal(state[name], true, `Cleanup incomplete: ${name}`);
  assert.equal(state.video_mode, "idle"); assert.equal(state.camera_mode, "idle");
  assert.equal(state.guidance.active.speech, null); assert.equal(state.guidance.navigation_run_id, null);
  assert.equal(state.guidance.risk_session_id, null); assert.equal(state.guidance.paused.speech, false);
}

/* Functionality: Verify independent navigation and original MCP/one-watch ownership with native speech.
 * Inputs: Page/URL/session. Outputs: Complete closed evidence.
 * Logic: original UI start, duplicate MCP start, status and MCP stop; Constraints: synthetic GPS/route only. */
async function navigationSession(page, url, sessionId) {
  await resetProbe(page); await page.click("#navigation-start");
  await page.waitForFunction(() => window.assistControllers.activation.describe().ready, null, {timeout: 10000});
  const activation = await page.evaluate(() => ({state: window.assistControllers.activation.describe(),
    calls: structuredClone(window.__assistNativeSpeechCalls), native: structuredClone(window.assistProbe.driver)}));
  assert.equal(activation.calls[0]?.user_gesture_active, true, "Calibration must be submitted in the original start gesture");
  assert.equal(activation.native.filter(event => event.phase === "start" &&
    event.request_id === activation.state.request_id).length, 1, "Calibration must have a genuine native start");
  const status = await runTool(url, sessionId, "navigation_status"); assert.equal(status.active, true);
  const duplicate = await runTool(url, sessionId, "navigation_start"); assert.equal(duplicate.reason, "already_active");
  await deliverFix(page); await waitOutput(page, "output_end", "navigation");
  assert.equal((await capture(page)).cleanup.gps.active_watch_count, 1);
  const stopped = await runTool(url, sessionId, "navigation_stop"); assert.equal(stopped.accepted, true);
  const result = await capture(page); result.scope = "native_navigation_synthetic_GPS_and_route";
  result.mcp = {status, duplicate, stopped}; result.activation = activation;
  result.speech_summary = verifyAssistSession(result); assertStopped(result);
  return result;
}

/* Functionality: Deterministically verify risk priority without claiming generated risk is model output.
 * Inputs: Explicit fixture box and original navigation start. Outputs: Separately labeled native arbitration evidence.
 * Logic: inject only the manager input seam after a real navigation native start; stopRisk preserves GPS.
 * Constraints: No GPU/transport inference assertion, native event mocks or production state rewrites. */
async function syntheticSession(page, url, sessionId) {
  await resetProbe(page); await page.check("#assist-risk-enabled");
  assert.equal((await runTool(url, sessionId, "navigation_start")).accepted, true);
  await deliverFix(page); await waitOutput(page, "output_start", "navigation");
  await page.evaluate(() => {
    const manager = window.assistControllers.manager;
    manager.beginRisk("explicit-synthetic-arbitration-only");
    manager.ingestRisk({sessionId: "explicit-synthetic-arbitration-only", capturedAt: performance.now(),
      metadata: {index: 0, tracking_epoch: 0, boxes: [{track_id: 900001, label: "person", direction: "front_left",
        alert_required: true, alert_priority: 4, risk_level: "critical", risk_reasons: ["approaching", "closing_mps_ge_2"],
        motion_state: "approaching", relative_speed_mps: -3, ttc_sec: 1, distance_m: 3}]}});
  });
  await waitOutput(page, "output_end", "risk");
  await page.evaluate(() => window.assistControllers.manager.stopRisk("explicit_fixture_complete"));
  assert.equal((await capture(page)).cleanup.navigation.active, true, "Stopping risk stopped navigation");
  await waitOutput(page, "output_end", "navigation");
  await page.click("#assist-stop-all");
  const before = await capture(page); await page.waitForTimeout(350);
  const result = await capture(page);
  assert.equal(result.speech.filter(event => event.phase === "output_submitted").length,
    before.speech.filter(event => event.phase === "output_submitted").length, "New output after explicit all-stop");
  assert.ok(result.speech.some(event => event.phase === "output_cancel" && event.accepted === true &&
    event.reason === "risk_preempted_navigation"), "Synthetic priority fixture did not preempt owned navigation");
  result.scope = "explicit_synthetic_risk_arbitration_with_native_TTS_not_model_inference";
  result.speech_summary = verifyAssistSession(result, {minimumRiskStarts: 1}); assertStopped(result); return result;
}

/* Functionality: Apply one explicitly synthetic hidden boundary to original page listeners.
 * Inputs: Page/value. Outputs: Native DOM event effects. Logic: getter plus event only.
 * Constraints: does not claim OS background behavior or physical silence. */
async function setVisibility(page, value) {
  await page.evaluate(visibility => {
    Object.defineProperty(document, "visibilityState", {configurable: true, get: () => visibility});
    document.dispatchEvent(new Event("visibilitychange"));
  }, value);
}

/* Functionality: Verify selected local video through actual PRF2/model outputs alongside sensor fixtures.
 * Inputs: File/page/URL/session. Outputs: Real-model evidence and independent/hidden cleanup checks.
 * Logic: explicitly select video; demand3 source-bound native risk starts; navigation stop preserves streaming, video stop
 * preserves original GPS, then explicit video restart tests hidden cleanup without seek/loop.
 * Constraints: no prediction/GPU/frame replacement, synthesized risk input, retry or physical accuracy claim. */
async function videoSession(page, url, sessionId, videoPath) {
  await resetProbe(page); await page.selectOption("#assist-source", "video");
  await page.setInputFiles("#assist-video-file", videoPath);
  await page.waitForFunction(() => !document.getElementById("assist-video-start").disabled, null, {timeout: 10000});
  await page.click("#assist-video-start");
  await page.waitForFunction(() => window.assistProbe.sources.some(event => event.type === "session") ||
    window.assistProbe.sources.some(event => event.type === "error"), null, {timeout: 60000});
  let state = await capture(page);
  const hello = state.sources.find(event => event.type === "session");
  assert.ok(hello, "Local model session did not admit"); assert.equal(hello.session_seconds, null);
  assert.equal(hello.protocol_version, 2); assert.equal(hello.provider, "local");
  assert.equal((await runTool(url, sessionId, "navigation_start")).accepted, true);
  await deliverFix(page); await page.evaluate(point => __assistGPS.periodic(point), FIX);
  try {
    await waitOutput(page, "output_start", "risk", 3, 90000);
    state = await capture(page);
    assert.equal(state.cleanup.video_mode, "playing", "Video finished before independent navigation-stop check");
    const frames = state.frames.length;
    assert.equal((await runTool(url, sessionId, "navigation_stop")).accepted, true);
    await page.waitForFunction(offset => window.assistProbe.frames.length > offset ||
      window.assistControllers.video.mode === "idle", frames, {timeout: 12000});
    state = await capture(page);
    assert.ok(state.frames.length > frames && state.cleanup.video_mode === "playing", "Navigation stop interrupted video");
    assert.equal((await runTool(url, sessionId, "navigation_start")).accepted, true); await deliverFix(page);
    await page.click("#assist-video-stop");
    state = await capture(page); assert.equal(state.cleanup.navigation.active, true, "Video stop interrupted GPS navigation");
    assert.equal(state.cleanup.gps.active_watch_count, 1);
    await page.waitForFunction(() => window.assistControllers.video.client.socket.readyState === 3, null, {timeout: 10000});
    await page.click("#assist-video-start");
    await page.waitForFunction(() => window.assistControllers.video.mode === "playing" ||
      window.assistControllers.video.mode === "draining", null, {timeout: 60000});
    await setVisibility(page, "hidden");
    await page.waitForFunction(() => window.assistControllers.video.client.socket.readyState === 3, null, {timeout: 10000});
    const stopped = await capture(page); await page.waitForTimeout(600);
    state = await capture(page);
    assert.equal(state.speech.filter(event => event.phase === "output_submitted").length,
      stopped.speech.filter(event => event.phase === "output_submitted").length, "New output after hidden boundary");
    assert.equal(state.frames.length, stopped.frames.length, "Late source output after hidden boundary");
    state.scope = "actual_local_file_PRF2_model_native_risk_with_explicit_synthetic_GPS_route";
    state.independence = {navigation_stop_preserved_video: true, video_stop_preserved_navigation: true,
      hidden_invalidated_sources: true, continuous_hello: true};
    state.speech_summary = verifyAssistSession(state, {actualModel: true, minimumRiskStarts: 3});
    assertStopped(state); await setVisibility(page, "visible"); return state;
  } finally { await page.evaluate(() => __assistGPS.stop()); }
}

/* Functionality: Check layout and retain one new screenshot. Inputs: Page/new path. Outputs: Widths.
 * Logic: original CSS only; Constraints: no layout injection or overwrite. */
async function checkLayouts(page, screenshot) {
  for (const width of [320, 390, 1280]) {
    await page.setViewportSize({width, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Assist overflows at ${width}px`);
  }
  await page.setViewportSize({width: 390, height: 844});
  await writeFile(screenshot, await page.screenshot({fullPage: true,
    mask: [page.locator("#map, #maps-message, #route-form input, #navigation-position, #navigation-instruction, #navigation-next, #speech-text, #steps")]}),
  {flag: "wx"}); return [320, 390, 1280];
}

/* Functionality: Reject reused evidence paths. Inputs: Absolute path. Outputs: Absence or failure.
 * Logic: only ENOENT accepted; Constraints: final write is independently exclusive. */
async function requireNew(path) {
  try { await access(path); throw new Error("Choose new report and screenshot paths"); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
}

/* Functionality: Validate explicit CLI scope before browser work. Inputs: argv/environment.
 * Outputs: Canonical paths/URL. Logic: only loopback /maps/test and new JSON destination accepted.
 * Constraints: no query credentials, implicit video, service start, downloads or remote endpoint. */
export function validateArguments(argv, environment) {
  const [url, output, video, ...extra] = argv;
  if (!url || !output || extra.length || !environment.PRS_PLAYWRIGHT_PATH || !environment.PYTHON)
    throw new Error("Use URL NEW_REPORT.json [VIDEO_PATH]; set PYTHON and PRS_PLAYWRIGHT_PATH");
  const address = new URL(url);
  if (address.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(address.hostname) ||
      address.pathname !== "/maps/test" || address.username || address.password || address.search || address.hash)
    throw new Error("Use loopback assist /maps/test without credentials/query/hash");
  const outputPath = resolve(output);
  if (!outputPath.toLowerCase().endsWith(".json")) throw new Error("Report path must end with .json");
  return {url: address.href, output: outputPath, screenshot: outputPath.slice(0, -5) + ".png",
    video: video ? resolve(video) : null, runtime: environment.PRS_PLAYWRIGHT_PATH};
}

/* Functionality: Bound failure diagnostics without sensitive source bodies.
 * Inputs: Error. Outputs: Type/first message line. Logic: omit traceback/tool payload bodies.
 * Constraints: source/utterance/coordinate values are never intentionally included. */
function safeFailure(error) { return {type: error.name, message: String(error.message).split("\n")[0].slice(0, 300)}; }

/* Functionality: Run one browser acceptance with failure evidence preserved.
 * Inputs: Explicit validated settings. Outputs: Report. Logic: real dependencies/WS/native engine;
 * config/route/GPS fixtures declared before loading, distinct evidence partitions afterward.
 * Constraints: no services/retries, CDN fallback, GPU mocks, credentials or native-event fabrication. */
async function run(options) {
  const report = {complete: false, actual_model_verified: false, real_gps_verified: false, real_ors_verified: false,
    hardware_outputs_verified: false, scope: "Original unified assist UI, MCP HTTP+WS, Chrome and native Chinese TTS",
    fixtures: {gps: "Explicit sensor callbacks and2s emitter using real Date.now; one original watch",
      ors: "Explicit configured/route HTTP response fixtures; no real ORS requests",
      synthetic_arbitration: "Explicit generated risk input in its own session, never model evidence",
      visibility: "Explicit document visibility getter/event, not OS background behavior"},
    model_test: options.video ? "Actual selected local video, unchanged PRF2/model metadata" : "Not requested; no model inference verified",
    dependencies: "Original Leaflet1.9.4 CDN/SRI and OSM; no replacement or download",
    requests: [], external_requests: [], dependency_responses: [], dependency_failures: [],
    websockets: [], sessions: [], failures: []};
  let browser, page, routeCalls = 0, resolveWelcome;
  const welcome = new Promise(resolvePromise => { resolveWelcome = resolvePromise; });
  try {
    const {chromium} = createRequire(import.meta.url)(resolve(options.runtime));
    browser = await chromium.launch({channel: "chrome", headless: true});
    const context = await browser.newContext({viewport: {width: 390, height: 844}});
    await context.addInitScript(installGPS);
    await context.addInitScript(installSpeechProbe);
    await context.route(new URL("config/", options.url).href, route => route.fulfill({json: {
      provider: "openrouteservice", profile: "foot-walking", configured: true, code: "explicit_synthetic_fixture"}}));
    /* Functionality: Respond to the one explicit original route request. Inputs: Intercepted POST.
     * Outputs: Synthetic route. Logic: require original form endpoints; Constraints: no upstream fallback. */
    function fixtureRoute(route) {
      const request = route.request();
      if (request.method() !== "POST" || JSON.stringify(request.postDataJSON()) !==
          JSON.stringify({start: ROUTE.geometry.coordinates[0], end: ROUTE.geometry.coordinates.at(-1)})) {
        report.failures.push({type: "FixtureInputMismatch", message: "Route request differs from explicit fixture inputs"});
        return route.fulfill({status: 400, json: {code: "explicit_fixture_input_mismatch"}});
      }
      routeCalls++; return route.fulfill({json: {...ROUTE, request_id: `assist-explicit-fixture-${routeCalls}`}});
    }
    await context.route(new URL("route/", options.url).href, fixtureRoute);
    page = await context.newPage();
    /* Functionality: Record body-free network paths and enforce declared dependencies.
     * Inputs: Browser request. Outputs: Classifications. Logic: no query/body retention.
     * Constraints: external requests are never replaced with local/CDN mocks. */
    function requested(request) {
      const address = new URL(request.url()); if (!["http:", "https:"].includes(address.protocol)) return;
      const item = {origin: address.origin, path: address.pathname}; report.requests.push(item);
      if (address.origin !== new URL(options.url).origin) {
        report.external_requests.push(item);
        if (!(address.origin === "https://unpkg.com" && ["/leaflet@1.9.4/dist/leaflet.js", "/leaflet@1.9.4/dist/leaflet.css"].includes(address.pathname)) &&
            !(address.origin === "https://tile.openstreetmap.org" && /^\/\d+\/\d+\/\d+\.png$/.test(address.pathname)))
          report.failures.push({type: "UnexpectedDependency", message: "Unexpected external request"});
      }
    }
    /* Functionality: Retain real dependency responses. Inputs: Browser response. Outputs: Path/status.
     * Logic: original CDN/SRI remains active; Constraints: no response body retention. */
    function responded(response) {
      const address = new URL(response.url());
      if (["https://unpkg.com", "https://tile.openstreetmap.org"].includes(address.origin))
        report.dependency_responses.push({origin: address.origin, path: address.pathname, status: response.status()});
    }
    /* Functionality: Observe original control/model sockets and PRF2 headers only.
     * Inputs: Browser WebSocket. Outputs: Endpoint/lifecycle/header evidence.
     * Logic: no binary image or control-body retention; Constraints: no transport replacement. */
    function socketOpened(socket) {
      const address = new URL(socket.url()), item = {path: address.pathname, closed: false, hellos: [], inputs: []};
      report.websockets.push(item); socket.on("close", () => { item.closed = true; });
      /* Functionality: Read only declared handshake fields. Inputs: Received frame. Outputs: Public session evidence.
       * Logic: discard binary payloads and other controls; Constraints: no keys/media/results serialized. */
      function received(frame) {
        if (typeof frame.payload !== "string") return;
        let value; try { value = JSON.parse(frame.payload); } catch { return; }
        if (address.pathname === "/ws/control/" && value.type === "welcome") resolveWelcome(value.session_id);
        if (address.pathname === "/ws/detect/local/" && value.kind === "session")
          item.hellos.push({session_seconds: value.session_seconds, provider: value.provider,
            protocol_version: value.protocol_version, model: value.model, session_id: value.session_id});
      }
      /* Functionality: Read original outbound PRF2 header evidence. Inputs: Sent frame. Outputs: Index/source time.
       * Logic: exact wire header, no image copy; Constraints: does not generate timestamps or frames. */
      function sent(frame) {
        if (!Buffer.isBuffer(frame.payload)) return;
        const bytes = frame.payload;
        if (bytes.length < 16 || bytes.readUInt32BE(0) !== 0x50524632)
          report.failures.push({type: "InvalidLocalProtocol", message: "Actual local input is not PRF2"});
        else item.inputs.push({index: bytes.readUInt32BE(4), capture_time_s: bytes.readDoubleBE(8), bytes: bytes.length});
      }
      socket.on("framereceived", received); if (address.pathname === "/ws/detect/local/") socket.on("framesent", sent);
    }
    page.on("request", requested); page.on("response", responded); page.on("websocket", socketOpened);
    page.on("pageerror", error => report.failures.push(safeFailure(error)));
    page.on("requestfailed", request => { const address = new URL(request.url());
      if (address.origin !== new URL(options.url).origin) report.dependency_failures.push({origin: address.origin,
        path: address.pathname, error: request.failure()?.errorText ?? null}); });
    const response = await page.goto(options.url, {waitUntil: "domcontentloaded"}); assert.equal(response.status(), 200);
    report.permissions_policy = response.headers()["permissions-policy"];
    assert.match(report.permissions_policy, /camera=\(self\)/); assert.match(report.permissions_policy, /geolocation=\(self\)/);
    report.identity = await page.evaluate(observe);
    await page.waitForFunction(() => !document.getElementById("example").disabled, null, {timeout: 15000});
    assert.equal(await page.evaluate(() => globalThis.L?.version), "1.9.4", "Original Leaflet CDN dependency unavailable");
    const sessionId = await page.evaluate(() => sessionStorage.getItem("prs.ai.session"));
    assert.equal(await waitForGate(welcome, "Original assist MCP control registration"), sessionId);
    for (const id of ["speech-enabled", "navigation-speech-enabled", "assist-risk-enabled"])
      assert.equal(await page.isChecked(`#${id}`), true, `${id} must default on in the unified entry`);
    assert.equal(await page.inputValue("#navigation-speech-voice"), "");
    report.startup = await page.evaluate(() => ({native_speech_calls: window.__assistNativeSpeechCalls.length,
      gps: __assistGPS.describe(), camera_idle: window.assistControllers.cameraApp.mode === "idle" &&
        window.assistControllers.cameraApp.stream === null, activation: window.assistControllers.activation.describe(),
      guidance: window.assistControllers.manager.describe(),
      vibration: {supported: !document.getElementById("vibration-enabled").disabled,
        checked: document.getElementById("vibration-enabled").checked}}));
    assert.equal(report.startup.native_speech_calls, 0); assert.equal(report.startup.gps.watch_count, 0);
    assert.equal(report.startup.camera_idle, true); assert.equal(report.startup.activation.ready, false);
    assert.equal(report.startup.guidance.reasons.speech, "activation_required");
    assert.equal(report.startup.guidance.paused.speech, true);
    assert.equal(report.startup.vibration.checked, report.startup.vibration.supported);
    report.mcp_before = await runTool(options.url, sessionId, "navigation_status");
    assert.equal(report.mcp_before.state, "idle");
    await planRoute(page); report.speech_configuration = await enableSpeech(page);
    report.sessions.push(await navigationSession(page, options.url, sessionId));
    report.sessions.push(await syntheticSession(page, options.url, sessionId));
    if (options.video) {
      report.sessions.push(await videoSession(page, options.url, sessionId, options.video)); report.actual_model_verified = true;
    }
    report.layouts = await checkLayouts(page, options.screenshot); report.screenshot = options.screenshot;
    report.final = await capture(page); assertStopped(report.final);
    assert.equal(routeCalls, 1, "Unexpected automatic route planning"); report.route_calls = routeCalls;
    assert.equal(report.websockets.filter(item => item.path === "/ws/control/").length, 1, "Duplicate ai-control module/socket");
    assert.ok(report.websockets.every(item => item.path === "/ws/control/" || item.path === "/ws/detect/local/"), "Unexpected socket provider");
    if (options.video) {
      const sockets = report.websockets.filter(item => item.path === "/ws/detect/local/");
      assert.equal(sockets.length, 2, "Only the two explicit video starts may connect");
      assert.ok(sockets.every(item => item.closed && item.hellos.length === 1 && item.hellos[0].session_seconds === null));
      assert.ok(sockets[0].inputs.length > 0, "No actual PRF2 inputs observed");
    }
    assert.ok(!report.requests.some(item => /\/(?:live|integration)\/assets\//.test(item.path)), "Noncanonical duplicate module path");
    for (const path of ["/leaflet@1.9.4/dist/leaflet.js", "/leaflet@1.9.4/dist/leaflet.css"])
      assert.ok(report.dependency_responses.some(item => item.path === path && item.status >= 200 && item.status < 300),
        "Original Leaflet CDN dependency failed");
    assert.deepEqual(report.failures, []); report.complete = true;
  } catch (error) {
    report.failures.push(safeFailure(error));
    if (page) {
      try { report.interrupted_session = await capture(page); } catch (failure) { report.inspection_failure = safeFailure(failure); }
      try { await requireNew(options.screenshot); await writeFile(options.screenshot, await page.screenshot({fullPage: true,
        mask: [page.locator("#map, #maps-message, #route-form input, #navigation-position, #navigation-instruction, #navigation-next, #speech-text, #steps")]}), {flag: "wx"});
        report.screenshot = options.screenshot; } catch (failure) { report.screenshot_failure = safeFailure(failure); }
    }
  } finally { if (browser) await browser.close(); }
  return report;
}

/* Functionality: Save one exclusive complete/failure report and truthful process status.
 * Inputs: CLI URL NEW_REPORT.json [VIDEO_PATH], PYTHON/PRS_PLAYWRIGHT_PATH.
 * Outputs: New JSON/PNG evidence. Logic: preflight paths/runtime/video before one launch.
 * Constraints: no overwrite, service start/stop, retries, native/GPU substitution or Git mutation. */
async function main() {
  const options = validateArguments(process.argv.slice(2), process.env);
  await requireNew(options.output); await requireNew(options.screenshot);
  if (options.video) assert.equal((await stat(options.video)).isFile(), true, "VIDEO_PATH must be an existing file");
  await mkdir(dirname(options.output), {recursive: true});
  const report = await run(options);
  await writeFile(options.output, JSON.stringify(report, null, 2) + "\n", {flag: "wx"});
  process.stdout.write(JSON.stringify({complete: report.complete, actual_model_verified: report.actual_model_verified,
    sessions: report.sessions.map(item => ({scope: item.scope, ...item.speech_summary})), failures: report.failures}) + "\n");
  if (!report.complete) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
