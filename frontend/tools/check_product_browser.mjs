/* Responsibilities: Verify the released public guest assist surface and diagnostic page through Chrome.
 * Implementation: Trusted public HTTPS, native fake camera and TTS;
 * rear constraints, permission results, route and GPS use explicit fixtures; model frames are genuine.
 * Model evidence admits legacy rules-v1 and vehicle-filter rules-v2 as the client does.
 * Related Modules: integration/assist-product.html, product-ui.js, assist-mvp.js, speech-activation.js;
 * check_phone_relay_browser.mjs provides shared evidence privacy helpers.
 * Declaration Index: validateArguments; verifyInitial; verifyProductSurface; verifyCameraRequest;
 * verifyCameraDisplay; installProbe; installProbe.supportedConstraints;
 * installProbe.observedSpeak; installProbe.checkedVideo; installProbe.getCurrentPosition;
 * installProbe.watchPosition; installProbe.watchPosition.emit; installProbe.clearWatch; observe; observe.recordFrame;
 * observe.recordSpeech; surfaceEvidence; initialEvidence; waitVoice; run; installDelayedVoices;
 * installDelayedVoices.getVoices; installDelayedVoices.available; installDelayedVoices.release;
 * setSyntheticVisibility; installCameraBoundary; installCameraBoundary.checkedVideo;
 * installCameraBoundary.supportedConstraints; installCameraBoundary.resolvePermission;
 * installCameraBoundary.rejectPermission; runCameraBoundaries; runBoundaries; main.
 * Variable Index: ORIGIN/PREFIX — approved relay; ROUTE/POINT — explicit synthetic fixtures;
 * report — body-free evidence without login credentials, pixels or positions.
 * Constraints: No device/route/model changes, physical camera/GPS, synthesized speech start/end, retries,
 * installation, TLS bypass, actual audibility claim, service control or commits.
 */
import {createRequire} from "node:module";
import {mkdir, stat, writeFile} from "node:fs/promises";
import {dirname, resolve} from "node:path";
import {pathToFileURL} from "node:url";
import {requireNew, requireGate, safeFailure, verifySpeechActivation} from "./check_phone_relay_browser.mjs";

const ORIGIN = "https://47.239.50.129", PREFIX = "/mvp/";
const POINT = {longitude: 103.7766, latitude: 1.2971, accuracy: 5};
const ROUTE = {provider: "openrouteservice", profile: "foot-walking", request_id: "synthetic-product-fixture",
  attribution: "Explicit synthetic product UI fixture", geometry: {type: "LineString", coordinates: [
    [103.7766, 1.2971], [103.7776, 1.2971], [103.7776, 1.2981]]},
  summary: {distance_m: 222, duration_s: 180}, steps: [
    {type: 11, instruction: "Head east (synthetic)", distance_m: 111, duration_s: 90, way_points: [0, 1]},
    {type: 0, instruction: "Turn left (synthetic)", distance_m: 111, duration_s: 90, way_points: [1, 2]},
    {type: 10, instruction: "Arrive (synthetic)", distance_m: 0, duration_s: 0, way_points: [2, 2]},
  ]};

/* Functionality: Pin the public guest product URL and output artifact before browser work.
 * Inputs: CLI URL/report arguments, optional boundary mode, and runtime environment.
 * Outputs: normalized artifact paths and public URL. Logic: exact origin/path and one explicit boundary mode.
 * Constraints: no credential-file input, URL secrets, local target or TLS override. */
export function validateArguments(argv, environment) {
  const [url, output, ...extra] = argv;
  const boundaries = extra.length === 1 && extra[0] === "--boundaries";
  const cameraBoundaries = extra.length === 1 && extra[0] === "--camera-boundaries";
  requireGate(Boolean(url && output && (!extra.length || boundaries || cameraBoundaries) && environment.PRS_PLAYWRIGHT_PATH), "INVALID_ARGUMENTS");
  let address; try { address = new URL(url); } catch { requireGate(false, "INVALID_PRODUCT_URL"); }
  requireGate(address.origin === ORIGIN && address.pathname === `${PREFIX}maps/` &&
    !address.username && !address.password && !address.search && !address.hash, "INVALID_PRODUCT_URL");
  const report = resolve(output);
  requireGate(report.endsWith(".json"), "INVALID_REPORT_PATH");
  return {url, boundaries, cameraBoundaries, output: report, screenshot: report.slice(0, -5) + ".png",
    desktopScreenshot: report.slice(0, -5) + "-desktop.png", diagnosticScreenshot: report.slice(0, -5) + "-diagnostic.png",
    runtime: resolve(environment.PRS_PLAYWRIGHT_PATH),
    fakeCameraFile: environment.PRS_FAKE_CAMERA_VIDEO ? resolve(environment.PRS_FAKE_CAMERA_VIDEO) : null};
}

/* Functionality: Require enabled intentions without automatic device access or speech.
 * Inputs: body-free startup snapshot. Outputs: same validated snapshot. Logic: independent gates.
 * Constraints: a checked preference is never interpreted as permission, activation or audibility. */
export function verifyInitial(value) {
  requireGate(value.camera_calls === 0 && value.speech_calls === 0 && value.gps_watches === 0 && value.gps_one_shots === 0,
    "AUTOMATIC_DEVICE_OR_SPEECH_START");
  requireGate(value.outputs.every(Boolean) && value.source === "camera" && value.rate === 2 && value.volume === 1,
    "PRODUCT_DEFAULTS_INVALID");
  requireGate(value.activation.ready === false && value.gated === true, "PRODUCT_ACTIVATION_GATE_MISSING");
  requireGate(value.voice.local_service === true && /^zh(?:-|$)/i.test(value.voice.language) && value.voice.id,
    "PRODUCT_AUTOMATIC_LOCAL_VOICE_MISSING");
  return value;
}

/* Functionality: Require product press-to-talk and reject exposed diagnostics or bad layout.
 * Inputs: visible-surface evidence. Outputs: same validated evidence. Logic: named and sized controls.
 * Constraints: hidden compatibility bindings are permitted; diagnostic links stay off the product surface. */
export function verifyProductSurface(value) {
  requireGate(value.horizontal_overflow <= 1 && value.duplicate_ids.length === 0, "PRODUCT_LAYOUT_INVALID");
  requireGate(value.unnamed_controls.length === 0, "PRODUCT_CONTROL_LABEL_MISSING");
  requireGate(value.small_touch_controls.length === 0, "PRODUCT_TOUCH_TARGET_TOO_SMALL");
  requireGate(value.visible_test_controls.length === 0 && value.visible_test_text.length === 0, "PRODUCT_DIAGNOSTICS_EXPOSED");
  requireGate(["按住说话", "Hold to speak"].includes(value.voice_hold_label), "PRODUCT_HOLD_CONTROL_MISSING");
  requireGate(value.diagnostic_link === null, "PRODUCT_DIAGNOSTIC_LINK_EXPOSED");
  return value;
}

/* Functionality: Reject a duplicate, microphone-enabled or non-rear camera request.
 * Inputs: body-free original request evidence. Outputs: validated acquisition.
 * Logic: require exactly one explicit rear request from a real click. Constraints: no hardware claim. */
export function verifyCameraRequest(value) {
  requireGate(value.length === 1 && value[0].audio_false && value[0].exact_rear &&
    !value[0].exact_device && value[0].user_gesture_active, "PRODUCT_REAR_CAMERA_REQUEST_INVALID");
  return value[0];
}

/* Functionality: Require a hidden camera capture source and actual decoded detection result.
 * Inputs: rendered media snapshot and model frame count. Outputs: same validated snapshot.
 * Logic: the source must be playing with native dimensions while the annotated result is visible.
 * Constraints: does not claim detections exist in the fake scene or validate box accuracy. */
export function verifyCameraDisplay(value) {
  requireGate(value.preview_hidden && value.preview_playing && value.preview_width > 0 && value.preview_height > 0 &&
    value.result_visible && value.result_decoded && value.result_width > 0 && value.result_height > 0 &&
    value.result_blob && value.frames > 0, "PRODUCT_CAMERA_DISPLAY_INVALID");
  return value;
}

/* Functionality: Install explicit sensor fixtures and read-only native media/speech probes.
 * Inputs: fixed synthetic position before production modules. Outputs: body-free counters/track handles.
 * Logic: record exact rear request then explicitly translate facingMode for Chrome's sole fake camera;
 * real TTS and sensor callbacks use real clocks. Constraints: CLI must enable fake-device flags;
 * no actual GPS/microphone, native rear-camera proof or generated speech lifecycle events. */
function installProbe(point) {
  const nativeSpeak = window.speechSynthesis?.speak?.bind(window.speechSynthesis);
  const nativeVideo = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices);
  const nativeConstraints = navigator.mediaDevices?.getSupportedConstraints?.bind(navigator.mediaDevices);
  const probe = {speech: [], media: [], tracks: [], watches: [], oneShots: 0}; window.__productProbe = probe;
  /* Functionality: Observe actual synchronous speech submission. Inputs: original utterance.
   * Outputs: native result. Logic: preserve gesture stack. Constraints: never save utterance text. */
  function observedSpeak(utterance) {
    probe.speech.push({user_gesture_active: navigator.userActivation?.isActive === true, at: performance.now()});
    return nativeSpeak(utterance);
  }
  /* Functionality: Declare explicit synthetic rear support for Chrome's single fake camera.
   * Inputs: None. Outputs: native support plus labeled facingMode fixture. Logic: preserve other flags.
   * Constraints: never product code or proof of a real rear lens. */
  function supportedConstraints() { return {...nativeConstraints?.(), facingMode: true}; }
  /* Functionality: Observe product rear constraints and acquire only Chrome's native fake camera.
   * Inputs: original constraints. Outputs: native fake stream. Logic: omit only the synthetic facingMode.
   * Constraints: no physical hardware, microphone, stream replacement or product fallback. */
  async function checkedVideo(constraints) {
    if (constraints.audio !== false) throw new Error("MICROPHONE_FORBIDDEN");
    const exactRear = constraints.video?.facingMode?.exact === "environment";
    probe.media.push({audio_false: true, exact_rear: exactRear,
      exact_device: typeof constraints.video?.deviceId?.exact === "string",
      user_gesture_active: navigator.userActivation?.isActive === true});
    if (!exactRear) throw new Error("EXPLICIT_REAR_REQUEST_REQUIRED");
    const {facingMode: _fixtureRear, ...video} = constraints.video;
    const stream = await nativeVideo({...constraints, video}); probe.tracks.push(...stream.getTracks()); return stream;
  }
  /* Functionality: Supply one explicit synthetic locate result. Inputs: original success callback.
   * Outputs: fresh fixture fix. Logic: asynchronous delivery. Constraints: not a physical device measurement. */
  function getCurrentPosition(success) { probe.oneShots++; queueMicrotask(() => success({coords: {...point}, timestamp: Date.now()})); }
  /* Functionality: Own one original navigation location subscription. Inputs: success callback.
   * Outputs: fixture watch ID. Logic: initial and repeated fresh synthetic fixes. Constraints: single owner. */
  function watchPosition(success) {
    if (probe.watches.some(item => item.active)) throw new Error("CONCURRENT_GPS_OWNER");
    const item = {active: true, timer: null}; probe.watches.push(item);
    /* Functionality: Deliver a fresh location only to the retained fixture owner. Inputs: None.
     * Outputs: synthetic callback. Logic: active check before delivery. Constraints: no resumed watch. */
    function emit() { if (item.active) success({coords: {...point}, timestamp: Date.now()}); }
    item.timer = setInterval(emit, 2000); queueMicrotask(emit); return probe.watches.length - 1;
  }
  /* Functionality: Release the sensor fixture owned by navigation. Inputs: ID. Outputs: cleared timer.
   * Logic: mark inactive before clear. Constraints: unknown or repeated cleanup is a failure. */
  function clearWatch(id) {
    const item = probe.watches[id]; if (!item?.active) throw new Error("UNKNOWN_GPS_OWNER");
    item.active = false; clearInterval(item.timer); item.timer = null;
  }
  if (nativeSpeak) window.speechSynthesis.speak = observedSpeak;
  if (nativeVideo) {
    navigator.mediaDevices.getUserMedia = checkedVideo;
    navigator.mediaDevices.getSupportedConstraints = supportedConstraints;
  }
  Object.defineProperty(navigator, "geolocation", {configurable: true, value: {getCurrentPosition, watchPosition, clearWatch}});
}

/* Functionality: Observe canonical production instances and true frame/native speech events.
 * Inputs: initialized product page. Outputs: singleton identity; body-free browser probe.
 * Logic: canonical asset URLs only. Constraints: never replace a controller or generate output metadata. */
async function observe() {
  const asset = new URL("../assets/", location.href);
  const glue = await import(new URL("integration/assist-mvp.js", asset)); await glue.assistReady;
  const {cameraApp} = await import(new URL("camera.js", asset));
  const {navigation} = await import(new URL("navigation-ui.js", asset));
  const {speechOutput} = await import(new URL("speech-output.js", asset));
  window.__productControllers = {glue, cameraApp, navigation, speechOutput};
  window.__productEvents = {frames: [], native: []};
  /* Functionality: Save validated model identity only. Inputs: original frame event.
   * Outputs: bounded frame metadata. Logic: allowlist. Constraints: no pixels, coordinates or object boxes. */
  function recordFrame(event) {
    if (event.type !== "frame") return;
    const data = event.result.metadata;
    window.__productEvents.frames.push({session_id: data.session_id, index: data.index, provider: data.provider,
      model: data.model, tracking_model: data.tracking_model, depth_model: data.depth_model, risk_model: data.risk_model,
      timestamp_source: data.timestamp_source, capture_time_s: data.capture_time_s, captured_at_ms: event.capturedAt,
      target_count: data.count});
  }
  /* Functionality: Save native driver lifecycle identity. Inputs: original event.
   * Outputs: phase/request only. Logic: allowlist. Constraints: no spoken body or forged native event. */
  function recordSpeech(event) { window.__productEvents.native.push({phase: event.phase, request_id: event.request_id}); }
  cameraApp.subscribe(recordFrame); speechOutput.subscribe(recordSpeech);
  return {camera: glue.cameraApp === cameraApp, navigation: glue.navigation === navigation,
    speech: glue.speechOutput === speechOutput, continuous_session: cameraApp.localSessionSeconds === null};
}

/* Functionality: Inspect mobile visibility and control names without source or user content.
 * Inputs: current rendered DOM. Outputs: fixed label/layout diagnostics.
 * Logic: computed visibility, labels/aria names, duplicate IDs and bounded known test vocabulary.
 * Constraints: does not mutate styling, move controls or expose input values. */
function surfaceEvidence() {
  const visible = node => !!node && node.getClientRects().length > 0 && getComputedStyle(node).visibility !== "hidden";
  const controls = [...document.querySelectorAll("button,input,select,textarea")].filter(visible);
  const ids = [...document.querySelectorAll("[id]")].map(node => node.id);
  const name = node => node.getAttribute("aria-label") || node.getAttribute("aria-labelledby") ||
    [...node.labels ?? []].map(label => label.textContent).join("").trim() || (node.tagName === "BUTTON" ? node.textContent.trim() : "");
  const text = document.body.innerText;
  const diagnostic = [...document.querySelectorAll("a[href]")].find(node => visible(node) &&
    new URL(node.href).pathname.endsWith("/maps/test"));
  return {width: innerWidth, horizontal_overflow: document.documentElement.scrollWidth - innerWidth,
    duplicate_ids: [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))],
    unnamed_controls: controls.filter(node => !name(node)).map(node => node.id || node.tagName),
    small_touch_controls: controls.filter(node => node.matches("button,select,input[type=number]") &&
      !node.closest(".leaflet-control") && node.getBoundingClientRect().height < 44).map(node => node.id || node.tagName),
    visible_test_controls: ["speech-play", "speech-text", "speech-voice", "speech-volume", "speech-rate",
      "navigation-speech-voice", "navigation-speech-language", "navigation-voices-refresh", "assist-source",
      "assist-video-file", "assist-video-start", "assist-devices-authorize", "assist-devices-refresh", "assist-device",
      "ping", "example"].filter(id => visible(document.getElementById(id))),
    visible_test_text: ["连续视频测试", "原生语音输出", "语音选择", "往返延迟", "FPS", "PRF2", "CUDA", "请求编号",
      "填入 NUS 示例", "Fill NUS example", "手动输入坐标", "Enter coordinates manually", "实时画面", "Live camera",
      "尚未开始导航", "Navigation not started", "暂无可靠导航进度", "No reliable navigation progress",
      "等待转向指令", "Waiting for turn guidance", "Find a route to start navigation."].filter(word => text.includes(word)),
    voice_hold_label: document.getElementById("agent-record")?.textContent.trim() || null,
    diagnostic_link: diagnostic ? new URL(diagnostic.href).pathname : null};
}

/* Functionality: Read default output/voice and untouched sensor state. Inputs: initialized page.
 * Outputs: safe startup evidence. Logic: controller voice inventory plus DOM intentions.
 * Constraints: does not play, select a voice, request permission or change configuration. */
function initialEvidence() {
  const {glue, speechOutput} = window.__productControllers, probe = window.__productProbe;
  const id = document.getElementById("navigation-speech-voice").value;
  const voice = speechOutput.voiceList().voices.find(item => item.id === id) || {};
  return {camera_calls: probe.media.length, speech_calls: probe.speech.length, gps_watches: probe.watches.length,
    gps_one_shots: probe.oneShots, outputs: ["speech-enabled", "navigation-speech-enabled", "assist-risk-enabled"].map(id => document.getElementById(id).checked),
    source: document.getElementById("assist-source").value, rate: Number(document.getElementById("speech-rate").value),
    volume: Number(document.getElementById("speech-volume").value),
    activation: glue.speechActivation.describe(), gated: glue.guidanceManager.describe().reasons.speech === "activation_required",
    voice: {id: voice.id, language: voice.language, local_service: voice.local_service}};
}

/* Functionality: Observe automatic voice matching for the currently chosen interface language.
 * Inputs: page/language family. Outputs: native local-service selection evidence.
 * Logic: bounded observation only. Constraints: no voice control selection or synthesized voiceschanged. */
async function waitVoice(page, family) {
  await page.waitForFunction(language => {
    const driver = window.__productControllers?.speechOutput, id = document.getElementById("navigation-speech-voice")?.value;
    return driver?.voiceList().voices.some(voice => voice.id === id && voice.local_service && voice.language.split("-")[0].toLowerCase() === language);
  }, family, {timeout: 15000});
  return page.evaluate(initialEvidence);
}

/* Functionality: Run product and diagnostic acceptance against the public guest service.
 * Inputs: pinned URL/new evidence paths. Outputs: JSON and product screenshot.
 * Logic: product layout/defaults, interface language, real-click TTS, original nav and real GPU, cleanup,
 * then isolated diagnostic UI. Constraints: route/GPS fixtures remain explicitly identified throughout. */
export async function run(options) {
  for (const path of [options.output, options.screenshot, options.desktopScreenshot, options.diagnosticScreenshot,
    options.screenshot.slice(0, -4) + "-detecting.png"]) await requireNew(path);
  await mkdir(dirname(options.output), {recursive: true});
  const report = {complete: false, phase: "startup", failures: [], checks: {},
    fixtures: {camera: "Chrome native fake-video; explicit facingMode-support/constraint translation fixture; original exact rear recorded",
      gps: "explicit synthetic NUS callbacks with Date.now",
      camera_content: options.fakeCameraFile ? "explicit local Y4M fake-video fixture with recognizable targets" : "Chrome default synthetic pattern",
      route: "explicit synthetic ORS response fixture", speech: "native Chrome local voices", model: "real local GPU through public relay"},
    actual_iphone_verified: false, actual_gps_verified: false, actual_ors_verified: false, physical_audibility_verified: false};
  let browser, context, page;
  try {
    const {chromium} = createRequire(import.meta.url)(options.runtime);
    if (options.fakeCameraFile) requireGate(options.fakeCameraFile.endsWith(".y4m") && (await stat(options.fakeCameraFile)).isFile(), "INVALID_FAKE_CAMERA_FILE");
    browser = await chromium.launch({channel: "chrome", headless: true, args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
      ...(options.fakeCameraFile ? [`--use-file-for-fake-video-capture=${options.fakeCameraFile}`] : [])]});
    context = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, locale: "zh-CN"});
    await context.addInitScript(installProbe, POINT);
    await context.route(`${ORIGIN}${PREFIX}maps/route/`, route => route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify(ROUTE)}));
    page = await context.newPage();
    page.on("pageerror", error => report.failures.push(safeFailure(error, report.phase)));
    report.phase = "product_startup";
    const response = await page.goto(options.url, {waitUntil: "domcontentloaded", timeout: 30000});
    requireGate(response.status() === 200, "PRODUCT_HTTP_FAILED");
    requireGate((response.headers()["permissions-policy"] || "").includes("microphone=(self)"),
      "PRODUCT_MICROPHONE_POLICY_MISSING");
    report.checks.identity = await page.evaluate(observe);
    requireGate(Object.values(report.checks.identity).every(Boolean), "PRODUCT_CORE_IDENTITY_INVALID");
    await waitVoice(page, "zh"); report.checks.initial = verifyInitial(await page.evaluate(initialEvidence));
    report.checks.mobile_surface = verifyProductSurface(await page.evaluate(surfaceEvidence));
    await page.screenshot({path: options.screenshot, fullPage: true, mask: [page.locator("#map")]});
    await page.setViewportSize({width: 1280, height: 900});
    report.checks.desktop_surface = verifyProductSurface(await page.evaluate(surfaceEvidence));
    await page.screenshot({path: options.desktopScreenshot, fullPage: true, mask: [page.locator("#map")]});
    await page.setViewportSize({width: 390, height: 844});
    report.phase = "interface_language";
    await page.selectOption("#language", "en"); await waitVoice(page, "en");
    report.checks.english_voice = (await page.evaluate(initialEvidence)).voice;
    report.checks.english_surface = verifyProductSurface(await page.evaluate(surfaceEvidence));
    await page.selectOption("#language", "zh-CN"); await waitVoice(page, "zh");
    requireGate(await page.evaluate(() => window.__productProbe.speech.length) === 0, "LANGUAGE_CHANGE_STARTED_SPEECH");
    report.phase = "camera_start_gesture";
    requireGate(await page.locator("#product-camera-start").evaluate(node => node.tagName === "BUTTON"), "PRODUCT_CAMERA_NOT_ACTIONABLE");
    await page.click("#product-camera-start");
    await page.waitForFunction(() => window.__productControllers.glue.speechActivation.describe().ready && window.__productEvents.frames.length >= 3,
      null, {timeout: 70000});
    report.checks.camera_request = verifyCameraRequest(await page.evaluate(() => window.__productProbe.media));
    report.checks.activation = verifySpeechActivation(await page.evaluate(() => ({
      activation: window.__productControllers.glue.speechActivation.describe(), calls: window.__productProbe.speech,
      native: window.__productEvents.native})));
    report.checks.activation.source = "product_environment_click";
    report.checks.camera_display = verifyCameraDisplay(await page.evaluate(() => {
      const preview = document.getElementById("preview"), result = document.getElementById("echo");
      const visible = node => node.getClientRects().length > 0 && getComputedStyle(node).visibility !== "hidden";
      const captureStyle = getComputedStyle(preview);
      const previewHidden = preview.hidden || captureStyle.opacity === "0" || preview.clientWidth <= 1 || preview.clientHeight <= 1;
      return {preview_hidden: previewHidden, preview_playing: !preview.paused && !!preview.srcObject,
        preview_width: preview.videoWidth, preview_height: preview.videoHeight, result_visible: visible(result),
        result_decoded: result.complete, result_width: result.naturalWidth, result_height: result.naturalHeight,
        result_blob: result.src.startsWith("blob:"), frames: window.__productEvents.frames.length};
    }));
    report.phase = "navigation_while_camera_active";
    await page.waitForFunction(() => !document.getElementById("locate").disabled);
    await page.click("#locate");
    await page.waitForFunction(() => document.getElementById("start-latitude").value !== "");
    await page.locator("#map").click({position: {x: 160, y: 180}});
    await page.click("#plan");
    await page.waitForFunction(() => !document.getElementById("navigation-start").disabled);
    report.checks.planned_surface = verifyProductSurface(await page.evaluate(surfaceEvidence));
    await page.click("#navigation-start");
    await page.waitForFunction(() => window.__productControllers.glue.speechActivation.describe().ready && window.__productControllers.navigation.active,
      null, {timeout: 15000});
    report.checks.navigation = {original_controller_active: true, route_and_gps: "explicit_synthetic_fixtures"};
    report.phase = "camera_and_model";
    requireGate(await page.evaluate(() => window.__productControllers.cameraApp.mode === "camera" && window.__productProbe.media.length === 1),
      "NAVIGATION_RESTARTED_CAMERA");
    report.checks.frames = await page.evaluate(() => window.__productEvents.frames);
    requireGate(report.checks.frames.every(frame => frame.provider === "local" && frame.model === "yolo26n" &&
      frame.tracking_model === "botsort" && frame.depth_model === "yolo26n-depth" &&
      ["rules-v1", "rules-v2"].includes(frame.risk_model) &&
      frame.timestamp_source === "browser_capture" && Math.abs(frame.capture_time_s * 1000 - frame.captured_at_ms) < 0.000001), "PRODUCT_MODEL_CONTRACT_INVALID");
    report.checks.detected_target_frames = report.checks.frames.filter(frame => frame.target_count > 0).length;
    if (options.fakeCameraFile) requireGate(report.checks.detected_target_frames > 0, "PRODUCT_FAKE_VIDEO_TARGET_NOT_DETECTED");
    report.checks.detecting_surface = verifyProductSurface(await page.evaluate(surfaceEvidence));
    await page.locator("#assist-camera-panel").screenshot({path: options.screenshot.slice(0, -4) + "-detecting.png"});
    await page.click("#stop");
    requireGate(await page.evaluate(() => window.__productControllers.navigation.active), "CAMERA_STOPPED_NAVIGATION");
    report.phase = "cleanup"; await page.click("#assist-stop-all");
    await page.waitForFunction(() => !window.__productControllers.navigation.active && window.__productControllers.cameraApp.mode === "idle");
    report.checks.cleanup = await page.evaluate(() => ({tracks_ended: window.__productProbe.tracks.every(track => track.readyState === "ended"),
      gps_released: window.__productProbe.watches.every(item => !item.active && item.timer === null),
      camera_stream_cleared: window.__productControllers.cameraApp.stream === null,
      native_speech_released: window.__productControllers.speechOutput.current === null,
      scheduler_released: window.__productControllers.glue.guidanceManager.schedulerTimer === null}));
    requireGate(Object.values(report.checks.cleanup).every(Boolean), "PRODUCT_CLEANUP_INCOMPLETE");
    report.phase = "diagnostic_surface";
    const diagnostic = await page.goto(new URL("test", options.url).href, {waitUntil: "domcontentloaded"});
    requireGate(diagnostic.status() === 200, "DIAGNOSTIC_HTTP_FAILED"); await page.evaluate(observe);
    requireGate(await page.locator("#speech-play").isVisible() && await page.locator("#navigation-speech-voice").isVisible() &&
      await page.locator("#example").isVisible() && await page.locator("#assist-source").isVisible(), "DIAGNOSTIC_CONTROLS_MISSING");
    await page.selectOption("#assist-source", "video");
    requireGate(await page.locator("#assist-video-file").isVisible(), "DIAGNOSTIC_VIDEO_MISSING");
    report.checks.diagnostics = {page_status: diagnostic.status(), manual_voice_visible: true, test_inputs_visible: true,
      explicit_voice_required: await page.inputValue("#navigation-speech-voice") === ""};
    requireGate(report.checks.diagnostics.explicit_voice_required, "DIAGNOSTIC_VOICE_POLICY_CHANGED");
    await page.screenshot({path: options.diagnosticScreenshot, fullPage: true, mask: [page.locator("#map")]});
    requireGate(report.failures.length === 0, "BROWSER_PAGE_ERRORS"); report.complete = true; report.phase = "complete";
  } catch (error) { report.failures.push(safeFailure(error, report.phase)); }
  finally {
    if (page && !page.isClosed()) await page.close().catch(() => {});
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    await writeFile(options.output, JSON.stringify(report, null, 2), {flag: "wx"});
  }
  return report;
}

/* Functionality: Hold only native voice-list visibility to reproduce delayed browser discovery.
 * Inputs: native browser synthesis. Outputs: explicit release seam and original-inventory availability.
 * Logic: hide inventory, then expose the unchanged native list with a labeled synthetic list event.
 * Constraints: no fabricated voice, native speech event, remote voice, playback or engine replacement. */
function installDelayedVoices() {
  const engine = window.speechSynthesis, original = engine.getVoices.bind(engine); let held = true;
  /* Functionality: Return either empty or the original native inventory. Inputs: None.
   * Outputs: native voices. Logic: explicit held boundary. Constraints: never fabricate a voice. */
  function getVoices() { return held ? [] : original(); }
  /* Functionality: Inspect native inventory readiness without publishing voice bodies. Inputs: None.
   * Outputs: local Chinese availability. Logic: original method. Constraints: no UI or output mutation. */
  function available() { return original().some(voice => voice.localService && /^zh(?:-|$)/i.test(voice.lang)); }
  /* Functionality: Deliver a declared delayed-inventory boundary. Inputs: None.
   * Outputs: original voices and synthetic list-change event. Logic: single explicit transition.
   * Constraints: does not synthesize utterance start/end, select a control or call speak. */
  function release() { held = false; engine.dispatchEvent(new Event("voiceschanged")); }
  engine.getVoices = getVoices; window.__productDelayedVoices = {available, release};
}

/* Functionality: Exercise the existing lifecycle listener at an explicit synthetic visibility boundary.
 * Inputs: hidden Boolean. Outputs: document visibility value/event. Logic: native-style read-only state.
 * Constraints: labeled fixture, not actual Safari backgrounding; never changes navigation directly. */
function setSyntheticVisibility(hidden) {
  Object.defineProperty(document, "visibilityState", {configurable: true, get: () => hidden ? "hidden" : "visible"});
  document.dispatchEvent(new Event("visibilitychange"));
}

/* Functionality: Expose a deliberate permission boundary without opening any hardware.
 * Inputs: initialized browser page. Outputs: pending request handles and stopped-track counters.
 * Logic: replace only GUM/support discovery; late grant returns a stoppable synthetic track.
 * Constraints: late stream is delivered only after cancellation, never to a video or model pipeline. */
function installCameraBoundary() {
  const state = {requests: [], stopped: 0, support: true}; window.__cameraBoundary = state;
  /* Functionality: Hold one explicit acquisition at its permission boundary. Inputs: original constraints.
   * Outputs: pending promise. Logic: retain one-shot callbacks. Constraints: no media stream or device. */
  function checkedVideo(constraints) {
    const record = {audio_false: constraints.audio === false, exact_rear: constraints.video?.facingMode?.exact === "environment",
      exact_device: typeof constraints.video?.deviceId?.exact === "string", user_gesture_active: navigator.userActivation?.isActive === true};
    window.__productProbe.media.push(record);
    return new Promise((resolve, reject) => state.requests.push({resolve, reject, settled: false}));
  }
  /* Functionality: Make rear-support availability explicit in this fixture. Inputs: None.
   * Outputs: one synthetic support flag. Logic: controlled boundary. Constraints: no native capability claim. */
  function supportedConstraints() { return {facingMode: state.support}; }
  /* Functionality: Deliver a late grant after the product stopped. Inputs: retained request index.
   * Outputs: synthetic stoppable track. Logic: one-shot resolve. Constraints: not a physical stream. */
  function resolvePermission(index) {
    const request = state.requests[index]; if (!request || request.settled) throw new Error("INVALID_PERMISSION_FIXTURE");
    request.settled = true; request.resolve({getTracks: () => [{stop: () => { state.stopped++; }}]});
  }
  /* Functionality: Deliver an explicit browser permission/device failure. Inputs: request index/name.
   * Outputs: rejected original permission promise. Logic: one-shot DOMException.
   * Constraints: never invokes camera startup or retries. */
  function rejectPermission(index, name) {
    const request = state.requests[index]; if (!request || request.settled) throw new Error("INVALID_PERMISSION_FIXTURE");
    request.settled = true; request.reject(new DOMException("Explicit product permission fixture", name));
  }
  navigator.mediaDevices.getUserMedia = checkedVideo;
  navigator.mediaDevices.getSupportedConstraints = supportedConstraints;
  state.resolvePermission = resolvePermission; state.rejectPermission = rejectPermission;
}

/* Functionality: Verify single-click acquisition, pending cancellation and actionable failures.
 * Inputs: approved public product URL and new report.
 * Outputs: body-free browser evidence. Logic: declared permission fixtures exercise original controllers.
 * Constraints: no actual camera/GPS/route/model, stream playback, implicit retry or service control. */
export async function runCameraBoundaries(options) {
  await requireNew(options.output); await mkdir(dirname(options.output), {recursive: true});
  const report = {complete: false, phase: "startup", failures: [], checks: {}, fixtures: {
    camera: "explicit pending getUserMedia promise; late grant is stoppable synthetic track; named DOMException failures",
    rear_support: "explicit getSupportedConstraints fixture", speech: "native local Chrome speech lifecycle"},
    actual_iphone_verified: false, actual_camera_verified: false, actual_gps_verified: false,
    model_verified: false, physical_audibility_verified: false};
  let browser, context, page; let modelConnections = 0;
  try {
    const {chromium} = createRequire(import.meta.url)(options.runtime);
    browser = await chromium.launch({channel: "chrome", headless: true});
    context = await browser.newContext({viewport: {width: 390, height: 844},
      isMobile: true, hasTouch: true, locale: "zh-CN"});
    await context.addInitScript({content: `(${installProbe.toString()})(${JSON.stringify(POINT)});(${installCameraBoundary.toString()})();`});
    page = await context.newPage(); page.on("pageerror", error => report.failures.push(safeFailure(error, report.phase)));
    page.on("websocket", socket => { if (new URL(socket.url()).pathname.endsWith("/ws/live/detect/")) modelConnections++; });
    const response = await page.goto(options.url, {waitUntil: "domcontentloaded", timeout: 30000});
    requireGate(response.status() === 200, "PRODUCT_HTTP_FAILED");
    requireGate((response.headers()["permissions-policy"] || "").includes("microphone=(self)"),
      "PRODUCT_MICROPHONE_POLICY_MISSING");
    await page.evaluate(observe); await waitVoice(page, "zh");
    report.checks.initial = verifyInitial(await page.evaluate(initialEvidence));
    report.phase = "double_click_permission_pending";
    await page.locator("#product-camera-start").dblclick();
    await page.waitForFunction(() => window.__cameraBoundary.requests.length === 1);
    report.checks.single_request = verifyCameraRequest(await page.evaluate(() => window.__productProbe.media));
    requireGate(await page.locator("#start").isDisabled() && await page.locator("#product-camera-start").isDisabled() &&
      await page.locator("#stop").isEnabled(), "PRODUCT_PENDING_CONTROLS_INVALID");
    report.checks.pending = {one_request: true, both_starts_disabled: true, stop_enabled: true};
    report.phase = "pending_permission_cancel"; await page.click("#stop");
    await page.waitForFunction(() => window.__productControllers.cameraApp.mode === "idle");
    await page.evaluate(() => window.__cameraBoundary.resolvePermission(0));
    await page.waitForFunction(() => window.__cameraBoundary.stopped === 1);
    report.checks.late_grant = await page.evaluate(() => ({camera_idle: window.__productControllers.cameraApp.mode === "idle",
      stream_absent: window.__productControllers.cameraApp.stream === null, track_stopped: window.__cameraBoundary.stopped === 1,
      preview_absent: document.getElementById("preview").srcObject === null,
      request_count: window.__productProbe.media.length, frames: window.__productEvents.frames.length}));
    requireGate(report.checks.late_grant.camera_idle && report.checks.late_grant.stream_absent && report.checks.late_grant.track_stopped &&
      report.checks.late_grant.preview_absent && report.checks.late_grant.request_count === 1 && report.checks.late_grant.frames === 0,
      "PRODUCT_LATE_PERMISSION_LEAK");
    for (const [index, name] of [[1, "NotAllowedError"], [2, "OverconstrainedError"]]) {
      report.phase = `camera_failure_${name}`; await page.click("#start");
      await page.waitForFunction(count => window.__cameraBoundary.requests.length === count, index + 1);
      await page.evaluate(({index, name}) => window.__cameraBoundary.rejectPermission(index, name), {index, name});
      await page.waitForFunction(() => window.__productControllers.cameraApp.mode === "idle" && !document.getElementById("start").disabled);
      const failure = await page.evaluate(() => ({request_count: window.__productProbe.media.length,
        actionable_message: /允许|权限|后置|浏览器|permission|rear|browser/i.test(document.getElementById("product-camera-message").textContent),
        visible_message: document.getElementById("product-camera-message").getClientRects().length > 0,
        both_starts_enabled: !document.getElementById("start").disabled && !document.getElementById("product-camera-start").disabled,
        frames: window.__productEvents.frames.length, stream_absent: window.__productControllers.cameraApp.stream === null}));
      requireGate(failure.request_count === index + 1 && failure.actionable_message && failure.visible_message &&
        failure.both_starts_enabled && failure.frames === 0 && failure.stream_absent, "PRODUCT_CAMERA_FAILURE_NOT_ACTIONABLE");
      report.checks[name] = failure;
    }
    report.phase = "unsupported_rear_constraint"; await page.evaluate(() => { window.__cameraBoundary.support = false; });
    await page.click("#product-camera-start");
    await page.waitForFunction(() => window.__productControllers.cameraApp.mode === "idle" && !document.getElementById("start").disabled);
    report.checks.unsupported = await page.evaluate(() => ({request_count: window.__productProbe.media.length,
      actionable_message: /后置|浏览器|rear|browser/i.test(document.getElementById("product-camera-message").textContent),
      frames: window.__productEvents.frames.length}));
    requireGate(report.checks.unsupported.request_count === 3 && report.checks.unsupported.actionable_message &&
      report.checks.unsupported.frames === 0 && modelConnections === 0, "PRODUCT_UNSUPPORTED_REAR_FALLBACK");
    report.checks.model_connections = modelConnections; await page.click("#assist-stop-all");
    requireGate(report.failures.length === 0, "BROWSER_PAGE_ERRORS"); report.complete = true; report.phase = "complete";
  } catch (error) { report.failures.push(safeFailure(error, report.phase)); }
  finally {
    if (page && !page.isClosed()) await page.close().catch(() => {});
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    await writeFile(options.output, JSON.stringify(report, null, 2), {flag: "wx"});
  }
  return report;
}

/* Functionality: Test delayed voice lists, incomplete route selection and visibility without repeating inference.
 * Inputs: pinned public product/new report paths. Outputs: boundary JSON and ready-map screenshots.
 * Logic: explicit inventory/route/GPS/visibility fixtures surround unchanged controllers and native TTS.
 * Constraints: no camera acquisition, actual GPS/ORS, GPU session, native speech-event simulation or retry. */
export async function runBoundaries(options) {
  for (const path of [options.output, options.screenshot, options.desktopScreenshot]) await requireNew(path);
  await mkdir(dirname(options.output), {recursive: true});
  const report = {complete: false, phase: "startup", failures: [], checks: {}, fixtures: {
    voice_inventory: "native inventory temporarily hidden; explicit synthetic voiceschanged on release",
    gps: "explicit synthetic NUS callbacks with Date.now", route: "explicit synthetic ORS response fixture",
    visibility: "explicit synthetic visibilityState/event", page_cache: "explicit synthetic persisted pagehide",
    speech: "real native local Chrome TTS lifecycle"},
    actual_iphone_verified: false, actual_gps_verified: false, actual_ors_verified: false, physical_audibility_verified: false};
  let browser, context, page;
  try {
    const {chromium} = createRequire(import.meta.url)(options.runtime);
    browser = await chromium.launch({channel: "chrome", headless: true});
    context = await browser.newContext({viewport: {width: 390, height: 844},
      isMobile: true, hasTouch: true, locale: "zh-CN"});
    await context.addInitScript(installProbe, POINT); await context.addInitScript(installDelayedVoices);
    await context.route(`${ORIGIN}${PREFIX}maps/route/`, route => route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify(ROUTE)}));
    page = await context.newPage(); page.on("pageerror", error => report.failures.push(safeFailure(error, report.phase)));
    const response = await page.goto(options.url, {waitUntil: "domcontentloaded", timeout: 30000});
    requireGate(response.status() === 200, "PRODUCT_HTTP_FAILED");
    requireGate((response.headers()["permissions-policy"] || "").includes("microphone=(self)"),
      "PRODUCT_MICROPHONE_POLICY_MISSING");
    await page.evaluate(observe);
    report.phase = "delayed_native_inventory";
    const held = await page.evaluate(initialEvidence);
    requireGate(!held.voice.id && held.speech_calls === 0 && held.camera_calls === 0 && held.gps_one_shots === 0 && held.gps_watches === 0,
      "DELAYED_VOICE_STARTED_OR_SELECTED");
    requireGate(await page.locator("#assist-speech-start").isDisabled(), "MISSING_VOICE_START_NOT_DISABLED");
    await page.waitForFunction(() => window.__productDelayedVoices.available(), null, {timeout: 15000});
    await page.evaluate(() => window.__productDelayedVoices.release());
    await waitVoice(page, "zh"); report.checks.delayed_inventory = verifyInitial(await page.evaluate(initialEvidence));
    report.phase = "ready_layout"; await page.waitForFunction(() => !document.getElementById("locate").disabled);
    report.checks.mobile_surface = verifyProductSurface(await page.evaluate(surfaceEvidence));
    await page.screenshot({path: options.screenshot, fullPage: true, mask: [page.locator("#map")], maskColor: "#dfe7df"});
    await page.setViewportSize({width: 1280, height: 900});
    report.checks.desktop_surface = verifyProductSurface(await page.evaluate(surfaceEvidence));
    await page.screenshot({path: options.desktopScreenshot, fullPage: true, mask: [page.locator("#map")], maskColor: "#dfe7df"});
    await page.setViewportSize({width: 390, height: 844});
    report.phase = "cached_page_observers";
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", {persisted: true})));
    await page.selectOption("#language", "en"); await waitVoice(page, "en");
    requireGate(await page.locator("h1").innerText() === "Your walk starts here.", "CACHED_PAGE_COPY_OBSERVER_LOST");
    report.checks.cached_page = {product_copy_updates: true, voice_follows_language: true,
      automatic_speech_calls: await page.evaluate(() => window.__productProbe.speech.length)};
    requireGate(report.checks.cached_page.automatic_speech_calls === 0, "CACHED_PAGE_AUTOMATIC_SPEECH");
    await page.selectOption("#language", "zh-CN"); await waitVoice(page, "zh");
    report.phase = "incomplete_route_selection";
    await page.click("#plan");
    report.checks.incomplete_route = await page.evaluate(() => ({
      user_coordinate_fields_hidden: [...document.querySelectorAll("#route-form input")].every(input => input.closest("[hidden]")),
      route_message_present: document.getElementById("product-route-message").textContent.length > 0,
      route_absent: !window.__productControllers.navigation.describe().route,
      speech_calls: window.__productProbe.speech.length, camera_calls: window.__productProbe.media.length}));
    requireGate(report.checks.incomplete_route.user_coordinate_fields_hidden && report.checks.incomplete_route.route_message_present &&
      report.checks.incomplete_route.route_absent && report.checks.incomplete_route.speech_calls === 0 && report.checks.incomplete_route.camera_calls === 0,
      "INCOMPLETE_ROUTE_SELECTION_NOT_HANDLED");
    report.phase = "visibility_cleanup";
    await page.click("#locate"); await page.waitForFunction(() => document.getElementById("start-latitude").value !== "");
    await page.locator("#map").click({position: {x: 160, y: 180}}); await page.click("#plan");
    await page.waitForFunction(() => !document.getElementById("navigation-start").disabled);
    await page.click("#navigation-start"); await page.waitForFunction(() => window.__productControllers.navigation.active);
    await page.waitForFunction(() => window.__productControllers.glue.speechActivation.describe().ready, null, {timeout: 15000});
    report.checks.activation = verifySpeechActivation(await page.evaluate(() => ({activation: window.__productControllers.glue.speechActivation.describe(),
      calls: window.__productProbe.speech, native: window.__productEvents.native})));
    report.checks.activation.source = "product_navigation_start";
    await page.evaluate(setSyntheticVisibility, true);
    report.checks.hidden = await page.evaluate(() => ({navigation_stopped: !window.__productControllers.navigation.active,
      gps_released: window.__productProbe.watches.every(item => !item.active && item.timer === null),
      speech_released: window.__productControllers.speechOutput.current === null,
      timer_released: window.__productControllers.glue.guidanceManager.schedulerTimer === null,
      camera_calls: window.__productProbe.media.length}));
    requireGate(report.checks.hidden.navigation_stopped && report.checks.hidden.gps_released && report.checks.hidden.speech_released &&
      report.checks.hidden.timer_released && report.checks.hidden.camera_calls === 0, "VISIBILITY_CLEANUP_INCOMPLETE");
    await page.evaluate(setSyntheticVisibility, false);
    report.checks.returned = await page.evaluate(() => ({navigation_stopped: !window.__productControllers.navigation.active,
      gps_released: window.__productProbe.watches.every(item => !item.active),
      speech_released: window.__productControllers.speechOutput.current === null}));
    requireGate(Object.values(report.checks.returned).every(Boolean), "VISIBILITY_AUTOMATIC_RESTART");
    requireGate(report.failures.length === 0, "BROWSER_PAGE_ERRORS"); report.complete = true; report.phase = "complete";
  } catch (error) { report.failures.push(safeFailure(error, report.phase)); }
  finally {
    if (page && !page.isClosed()) await page.close().catch(() => {});
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    await writeFile(options.output, JSON.stringify(report, null, 2), {flag: "wx"});
  }
  return report;
}

/* Functionality: Run the explicit CLI without publishing private exception bodies.
 * Inputs: CLI/environment. Outputs: short completion or fixed failure code. Logic: exclusive artifacts.
 * Constraints: importing the module never opens files, browsers, devices or network connections. */
async function main() {
  try {
    const options = validateArguments(process.argv.slice(2), process.env);
    const result = await (options.cameraBoundaries ? runCameraBoundaries(options) : options.boundaries ? runBoundaries(options) : run(options));
    console.log(JSON.stringify({complete: result.complete, phase: result.phase, failures: result.failures}));
    if (!result.complete) process.exitCode = 1;
  } catch (error) { console.error(JSON.stringify(safeFailure(error, "arguments_or_artifact"))); process.exitCode = 1; }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
