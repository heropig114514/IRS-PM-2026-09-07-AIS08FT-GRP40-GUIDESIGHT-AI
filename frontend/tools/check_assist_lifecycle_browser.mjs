/* Responsibilities: Verify original assist camera and repeated hidden/pending-input boundaries.
 * Implementation: Real Chrome/entry/PRF2 transport; explicitly synthetic canvas.captureStream
 * camera permission/inventory and delayed metadata boundaries. Verify unified default-on controls,
 * then explicitly disable output via UI; verify configured backend voice without claiming playback.
 * Related Modules: integration/assist-mvp.js/devices.js/video-replay.js; camera.js/frame-pipeline.js;
 * check_assist_browser.mjs exports pure CLI validation; backend integration.assist/protocol.
 * Declaration Index: normalizeApplicationPath — compare mounted URLs with canonical ASGI paths;
 * waitForPrf2Input — await one observed binary-frame header;
 * installMediaFixture; installMediaFixture.makeStream;
 * installMediaFixture.makeStream.draw; installMediaFixture.makeStream.stopTrack;
 * installMediaFixture.requestMedia; installMediaFixture.readInventory;
 * installMediaFixture.fixture.holdPermission; installMediaFixture.fixture.holdInventory;
 * installMediaFixture.fixture.releasePermission; installMediaFixture.fixture.releaseInventory;
 * installMediaFixture.fixture.describe; observeLifecycle; observeLifecycle.recordCamera;
 * observeLifecycle.recordGuidance; captureLifecycle; assertUnlocked; setVisibility;
 * hiddenPrelude; cameraSession; pendingPermission; pendingInventory; pendingMetadata;
 * pendingMetadata.installMetadataBoundary; pendingMetadata.installMetadataBoundary.ignoreLoad;
 * pendingMetadata.installMetadataBoundary.restore; requireNew; screenshot; run; run.socketOpened;
 * run.socketOpened.sent; run.socketOpened.received; main.
 * Variable Index: SYNTHETIC_DEVICE is fixture-only, never a real camera ID; normalizeApplicationPath
 * removes only the approved /mvp mount prefix; assistLifecycle holds
 * production references and body-free observations. Media fixtures own native synthetic tracks,
 * canvas draw timers and explicit unresolved permission/inventory callbacks, not device permissions.
 * Constraints: No service/Git changes, production method replacements, GPU mocks, implicit retries,
 * real route/GPS calls, saved pixels/audio/text/coordinates/credentials, physical camera claim or
 * simulated successful decoder/native speech events. Metadata fixture only verifies cancelled loading.
 */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {access, mkdir, writeFile} from "node:fs/promises";
import {resolve, dirname} from "node:path";
import {pathToFileURL} from "node:url";
import {validateArguments, installSpeechProbe} from "./check_assist_browser.mjs";

/* Functionality: Install explicit media sensor/permission/inventory fixtures before entry startup.
 * Inputs: Browser APIs. Outputs: Native canvas streams and manually released pending boundaries.
 * Logic: original CameraApp still requests, plays, captures, encodes and sends real PRF2 frames.
 * Constraints: no physical device access, production controller replacement or synthetic inference output. */
function installMediaFixture() {
  const SYNTHETIC_DEVICE = "explicit-synthetic-canvas-camera", streams = [], requests = [];
  let holdPermission = false, holdInventory = false, permissionResolve = null, inventoryResolve = null;
  let inventoryCalls = 0;
  const inventory = [{kind: "videoinput", deviceId: SYNTHETIC_DEVICE, groupId: "synthetic", label: "Explicit synthetic canvas camera"}];
  /* Functionality: Generate an explicitly synthetic native video stream. Inputs: Purpose.
   * Outputs: MediaStream. Logic: moving simple canvas shapes,15FPS native capture and counted actual track stops.
   * Constraints: shapes are test pixels; no model-object or risk accuracy is expected or asserted. */
  function makeStream(purpose) {
    const canvas = document.createElement("canvas"); canvas.width = 640; canvas.height = 480;
    const context = canvas.getContext("2d"); let tick = 0;
    /* Functionality: Paint explicit synthetic motion. Inputs: Local tick. Outputs: Canvas pixels.
     * Logic: fixed background/rectangle; Constraints: not a real street, camera or expected detector label. */
    function draw() { context.fillStyle = "#c8d5df"; context.fillRect(0, 0, 640, 480);
      context.fillStyle = "#34495e"; context.fillRect((tick++ * 3) % 450, 100, 100, 250); }
    draw(); const stream = canvas.captureStream(15), entry = {purpose, stream, stopCalls: 0, timer: null};
    streams.push(entry); entry.timer = setInterval(draw, 66);
    for (const track of stream.getTracks()) {
      const nativeStop = track.stop.bind(track);
      /* Functionality: Count and perform original native track cleanup. Inputs: None. Outputs: Ended track.
       * Logic: clear only fixture draw timer and invoke native stop; Constraints: no fake readyState. */
      function stopTrack() { entry.stopCalls++; if (entry.timer !== null) clearInterval(entry.timer);
        entry.timer = null; nativeStop(); }
      Object.defineProperty(track, "stop", {configurable: true, value: stopTrack});
    }
    return stream;
  }
  /* Functionality: Accept only explicit authorization or selected synthetic device requests.
   * Inputs: Original getUserMedia constraints. Outputs: Native synthetic stream or explicitly pending Promise.
   * Logic: record purpose, reject microphone/implicit device selection; Constraints: no native permission request. */
  function requestMedia(constraints) {
    if (constraints.audio !== false) throw new Error("Unexpected microphone constraint");
    const purpose = constraints.video === true ? "permission" : "camera_inference";
    if (purpose === "camera_inference" && constraints.video?.deviceId?.exact !== SYNTHETIC_DEVICE)
      throw new Error("Camera did not use explicitly selected synthetic device");
    requests.push({purpose});
    if (purpose === "permission" && holdPermission) {
      holdPermission = false; return new Promise(resolvePermission => { permissionResolve = resolvePermission; });
    }
    return Promise.resolve(makeStream(purpose));
  }
  /* Functionality: Resolve or explicitly defer media inventory. Inputs: None.
   * Outputs: Fixture inventory Promise. Logic: no automatic device choice; Constraints: no real inventory read. */
  function readInventory() {
    inventoryCalls++;
    if (holdInventory) { holdInventory = false; return new Promise(resolveInventory => { inventoryResolve = resolveInventory; }); }
    return Promise.resolve(structuredClone(inventory));
  }
  const fixture = {
    /* Functionality: Arm one pending permission request. Inputs: None. Outputs: Fixture flag.
     * Logic: next explicit UI authorization only; Constraints: no request/retry is initiated here. */
    holdPermission() { if (permissionResolve) throw new Error("Permission already pending"); holdPermission = true; },
    /* Functionality: Arm one pending inventory request. Inputs: None. Outputs: Fixture flag.
     * Logic: next explicit UI refresh only; Constraints: no request/retry is initiated here. */
    holdInventory() { if (inventoryResolve) throw new Error("Inventory already pending"); holdInventory = true; },
    /* Functionality: Complete previously hidden permission. Inputs: None. Outputs: Native temporary stream.
     * Logic: production authorization must stop its tracks even when stale; Constraints: no fake cleanup. */
    releasePermission() { if (!permissionResolve) throw new Error("No pending permission");
      const resolvePermission = permissionResolve; permissionResolve = null; resolvePermission(makeStream("late_permission")); },
    /* Functionality: Complete hidden inventory with a deliberately foreign fixture ID.
     * Inputs: None. Outputs: Original Promise completion. Logic: stale data must not replace explicit selection.
     * Constraints: no production inventory or control mutation. */
    releaseInventory() { if (!inventoryResolve) throw new Error("No pending inventory");
      const resolveInventory = inventoryResolve; inventoryResolve = null;
      resolveInventory([{kind: "videoinput", deviceId: "late-forbidden-synthetic-device", label: "Stale fixture"}]); },
    /* Functionality: Describe native fixture resource states without pixels or handles.
     * Inputs: Fixture state. Outputs: Counts and actual ended flags.
     * Logic: report original track.readyState; Constraints: no physical-camera success claim. */
    describe() { return {requests: structuredClone(requests), inventory_calls: inventoryCalls,
      permission_pending: permissionResolve !== null, inventory_pending: inventoryResolve !== null,
      streams: streams.map(entry => ({purpose: entry.purpose, stop_calls: entry.stopCalls,
        draw_timer_cleared: entry.timer === null, tracks_ended: entry.stream.getTracks().every(track => track.readyState === "ended")}))}; },
  };
  Object.defineProperty(navigator, "mediaDevices", {configurable: true,
    value: {getUserMedia: requestMedia, enumerateDevices: readInventory}});
  window.__assistMediaFixture = fixture;
}

/* Functionality: Observe unchanged actual entry and original camera events.
 * Inputs: Page modules. Outputs: Body-free probe and original object references.
 * Logic: resolve canonical assets through the page's configured API base and keep client reference
 * across CameraApp.stop so actual socket closure remains observable.
 * Constraints: no output enablement, GPU result mutation, image/body/location retention. */
async function observeLifecycle() {
  const base = new URL(document.documentElement.dataset.apiBase || "../", location.href);
  const asset = path => new URL(`assets/${path}`, base).href;
  const glue = await import(asset("integration/assist-mvp.js")); await glue.assistReady;
  const {cameraApp} = await import(asset("camera.js"));
  if (glue.cameraApp !== cameraApp) throw new Error("Duplicate camera module");
  const state = window.assistLifecycle = {glue, camera: cameraApp, lastClient: null,
    frames: [], cameraEvents: [], guidance: [], metadataBoundary: null};
  /* Functionality: Preserve original camera admission/frame/terminal identity.
   * Inputs: Camera event. Outputs: Finite evidence. Logic: no body/boxes retained.
   * Constraints: metadata comes only from actual validated local model responses. */
  function recordCamera(event) {
    if (event.type === "session") state.lastClient = cameraApp.client;
    state.cameraEvents.push({type: event.type, session_id: event.sessionId ?? null,
      session_seconds: event.contract?.session_seconds, provider: event.provider ?? null});
    if (event.type === "frame") state.frames.push({index: event.result.metadata.index,
      model: event.result.metadata.model, provider: event.result.metadata.provider,
      timestamp_source: event.result.metadata.timestamp_source, capture_time_s: event.result.metadata.capture_time_s,
      captured_at_ms: event.capturedAt, count: event.result.metadata.count});
  }
  /* Functionality: Preserve exact source-stop and output lifecycle events.
   * Inputs: Manager event. Outputs: Detached body-free event. Logic: no synthetic start/end.
   * Constraints: outputs are explicitly disabled after checking the unified startup contract. */
  function recordGuidance(event) { state.guidance.push(structuredClone(event)); }
  cameraApp.subscribe(recordCamera); glue.guidanceManager.subscribe(recordGuidance);
}

/* Functionality: Map mounted public requests to canonical ASGI route paths.
 * Inputs: Absolute pathname. Outputs: The same path with only the approved /mvp mount removed.
 * Logic: Preserve all other path segments; Constraints: accepts no URL/body and changes no request. */
function normalizeApplicationPath(path) { return path.replace(/^\/mvp(?=\/)/, ""); }

/* Functionality: Capture original finite lifecycle/resource/control state.
 * Inputs: Page. Outputs: Safe report. Logic: never serialize navigation route/position or media objects.
 * Constraints: selected IDs are synthetic fixture values only, not real device inventory. */
async function captureLifecycle(page) {
  return page.evaluate(() => {
    const state = window.assistLifecycle, nav = state.glue.navigation.describe(), video = state.glue.videoReplay;
    return {camera_mode: state.camera.mode, camera_phase: state.camera.cameraPhase,
      camera_stream_cleared: state.camera.stream === null, camera_client_cleared: state.camera.client === null,
      last_socket_closed: state.lastClient === null || state.lastClient.socket?.readyState === 3,
      navigation: {active: nav.active, state: nav.state, watch_active: state.glue.navigation.watchId !== null}, video_mode: video.mode,
      video_loading_cleared: video.loading === null, video_file_cleared: video.file === null,
      guidance: state.glue.guidanceManager.describe(), camera_events: structuredClone(state.cameraEvents),
      speech_activation: state.glue.speechActivation.describe(), native_speech_calls: window.__assistNativeSpeechCalls.length,
      backend_voice: (() => { const voice = state.glue.speechOutput.voiceList().voices.find(item => item.id === "Cherry");
        return voice ? {id: voice.id, service: voice.service, languages: voice.languages} : null; })(),
      frames: structuredClone(state.frames), events: structuredClone(state.guidance), media: __assistMediaFixture.describe(),
      controls: Object.fromEntries(["assist-source", "assist-device", "assist-video-file", "assist-devices-authorize",
        "assist-devices-refresh", "assist-video-start", "start"].map(id => [id, {disabled: document.getElementById(id).disabled}])),
      selected: document.getElementById("assist-device").value,
      options: [...document.getElementById("assist-device").options].map(option => option.value),
      status: document.getElementById("assist-status").textContent,
      metadata_waiting: state.metadataBoundary !== null};
  });
}

/* Functionality: Require original camera release and integration control unlock.
 * Inputs: Capture. Outputs: Assertions. Logic: final core idle and post-event controls must agree.
 * Constraints: no source mutation or false physical camera claim. */
function assertUnlocked(value) {
  assert.equal(value.camera_mode, "idle"); assert.equal(value.camera_stream_cleared, true);
  assert.equal(value.camera_client_cleared, true);
  for (const id of ["assist-source", "assist-device", "assist-video-file", "assist-devices-authorize", "assist-devices-refresh"])
    assert.equal(value.controls[id].disabled, false, `${id} remained locked after original terminal event`);
  assert.equal(value.navigation.active, false);
}

/* Functionality: Deliver an explicit visibility fixture. Inputs: Page/visibility.
 * Outputs: Original capture/bubble handler effects. Logic: no production methods replaced.
 * Constraints: not real OS background evidence. */
async function setVisibility(page, value) {
  await page.evaluate(visibility => { Object.defineProperty(document, "visibilityState", {configurable: true, get: () => visibility});
    document.dispatchEvent(new Event("visibilitychange")); }, value);
}

/* Functionality: Establish the already-consumed first hidden boundary.
 * Inputs: Page. Outputs: Visible page requiring later input to rearm cleanup.
 * Logic: real handlers consume hidden then visible; Constraints: no private boundaryStopped mutation. */
async function hiddenPrelude(page) { await setVisibility(page, "hidden"); await setVisibility(page, "visible"); }

/* Functionality: Verify the original camera transport and its terminal UI edge.
 * Inputs: Page, user-stop or explicit track-ended boundary, and body-free WebSocket report.
 * Outputs: Closed real-PRF2/synthetic-camera evidence. Logic: await one actual binary header,
 * then stop through the original handler; exactly one risk-stop, no navigation-stop.
 * Constraints: inference output is not required here; the strict public relay test owns model claims. */
async function cameraSession(page, boundary, report) {
  const before = await captureLifecycle(page);
  const previousSockets = report.websockets.filter(item => item.path === "/ws/detect/local/").length;
  await page.click("#start");
  await page.waitForFunction(() => window.assistLifecycle.camera.mode === "camera" &&
    window.assistLifecycle.lastClient?.socket.readyState === 1, null, {timeout: 30000});
  await waitForPrf2Input(report, previousSockets);
  const active = await captureLifecycle(page);
  assert.equal(active.camera_mode, "camera");
  assert.equal(active.camera_events.filter(event => event.type === "session").at(-1).session_seconds, null);
  const riskStops = active.events.filter(event => event.phase === "risk_stopped").length;
  const navStops = active.events.filter(event => event.phase === "navigation_stopped").length;
  if (boundary === "original_stop_button") await page.click("#stop");
  else await page.evaluate(() => window.assistLifecycle.camera.stream.getVideoTracks()[0].dispatchEvent(new Event("ended")));
  await page.waitForFunction(() => window.assistLifecycle.camera.mode === "idle" &&
    !document.getElementById("assist-source").disabled &&
    window.assistLifecycle.lastClient.socket.readyState === 3, null, {timeout: 10000});
  const after = await captureLifecycle(page); assertUnlocked(after);
  assert.equal(after.events.filter(event => event.phase === "risk_stopped").length - riskStops, 1);
  assert.equal(after.events.filter(event => event.phase === "navigation_stopped").length, navStops);
  assert.ok(after.media.streams.every(stream => stream.tracks_ended && stream.draw_timer_cleared));
  return {scope: "actual_PRF2_transport_with_explicit_synthetic_canvas_camera", boundary,
    physical_camera_verified: false, model_output_verified: false, before: active, after};
}

/* Functionality: Wait for a real PRF2 binary input observed by the external browser listener.
 * Inputs: Body-free report and count of earlier local detector sockets. Outputs: One accepted socket entry.
 * Logic: Poll only actual listener evidence for a bounded 10-second interval.
 * Constraints: no frame creation, image inspection, retry, or fabricated model result. */
async function waitForPrf2Input(report, previousSockets) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    const socket = report.websockets.filter(item => item.path === "/ws/detect/local/").slice(previousSockets)
      .find(item => item.inputs.length > 0);
    if (socket) return socket;
    await new Promise(resolvePromise => setTimeout(resolvePromise, 25));
  }
  assert.fail("No actual PRF2 input header reached the local detector socket");
}

/* Functionality: Reject permission completion after a second hidden boundary.
 * Inputs: Actual entry controls and explicitly delayed permission fixture.
 * Outputs: Native temporary tracks ended and stable hidden UI/selection.
 * Logic: first hide/visible, new authorization, second hide, then resolve the stale stream.
 * Constraints: no physical permission prompt, inference start or auto recovery. */
async function pendingPermission(page) {
  await hiddenPrelude(page); await page.evaluate(() => __assistMediaFixture.holdPermission());
  await page.click("#assist-devices-authorize");
  await page.waitForFunction(() => __assistMediaFixture.describe().permission_pending, null, {timeout: 5000});
  await setVisibility(page, "hidden"); const hidden = await captureLifecycle(page); assertUnlocked(hidden);
  await page.evaluate(() => __assistMediaFixture.releasePermission());
  await page.waitForFunction(() => __assistMediaFixture.describe().streams.filter(stream => stream.purpose === "late_permission")
    .every(stream => stream.tracks_ended && stream.draw_timer_cleared), null, {timeout: 5000});
  await page.waitForTimeout(50); const late = await captureLifecycle(page);
  assert.equal(late.selected, hidden.selected); assert.deepEqual(late.options, hidden.options);
  assert.equal(late.status, hidden.status); assertUnlocked(late);
  assert.equal(late.camera_events.filter(event => event.type === "session").length,
    hidden.camera_events.filter(event => event.type === "session").length);
  await setVisibility(page, "visible");
  return {scope: "explicit_delayed_permission_fixture_second_hidden", hidden, late};
}

/* Functionality: Reject inventory completion after a rearmed second hidden boundary.
 * Inputs: Original refresh control and explicitly pending inventory.
 * Outputs: Original explicit selection/options/status preserved.
 * Logic: late inventory deliberately excludes the selected fixture ID; stale guard must ignore it.
 * Constraints: no real inventory, controller method replacement or implicit device selection. */
async function pendingInventory(page) {
  await hiddenPrelude(page); await page.evaluate(() => __assistMediaFixture.holdInventory());
  await page.click("#assist-devices-refresh");
  await page.waitForFunction(() => __assistMediaFixture.describe().inventory_pending, null, {timeout: 5000});
  await setVisibility(page, "hidden"); const hidden = await captureLifecycle(page);
  await page.evaluate(() => __assistMediaFixture.releaseInventory()); await page.waitForTimeout(50);
  const late = await captureLifecycle(page);
  assert.equal(late.selected, hidden.selected); assert.deepEqual(late.options, hidden.options);
  assert.equal(late.status, hidden.status); assertUnlocked(late); await setVisibility(page, "visible");
  return {scope: "explicit_delayed_inventory_fixture_second_hidden", hidden, late};
}

/* Functionality: Cancel metadata loading after a rearmed hidden boundary and ignore a late event.
 * Inputs: Original file chooser with explicit nonempty synthetic Blob and native-media boundary fixture.
 * Outputs: Loading/file cleared and start disabled before/after late metadata event.
 * Logic: hold only native src/load boundary; never manufacture successful metadata or decoded dimensions.
 * Constraints: no actual codec/video/inference assertion, original loadFile/controller logic untouched. */
async function pendingMetadata(page) {
  await hiddenPrelude(page); await page.selectOption("#assist-source", "video");
  /* Functionality: Hold native metadata initiation only. Inputs: Existing video element.
   * Outputs: Restorable media-property boundary. Logic: prevent decoding until cancellation is tested.
   * Constraints: original videoReplay.loadFile and its cancellation/listeners stay unchanged. */
  function installMetadataBoundary() {
    const state = window.assistLifecycle, video = state.glue.videoReplay.video;
    const ownSrc = Object.getOwnPropertyDescriptor(video, "src"), ownLoad = Object.getOwnPropertyDescriptor(video, "load");
    let source = "";
    /* Functionality: Hold explicit decoder startup. Inputs: None. Outputs: None.
     * Logic: paired src boundary prevents any decode; Constraints: not a successful decoder fixture. */
    function ignoreLoad() {}
    Object.defineProperty(video, "src", {configurable: true, get: () => source, set: value => { source = value; }});
    Object.defineProperty(video, "load", {configurable: true, value: ignoreLoad});
    /* Functionality: Restore native media properties after cancellation. Inputs: None. Outputs: Original property lookup.
     * Logic: exact own descriptors or prototype fallback; Constraints: no source playback or retry. */
    function restore() {
      if (ownSrc) Object.defineProperty(video, "src", ownSrc); else delete video.src;
      if (ownLoad) Object.defineProperty(video, "load", ownLoad); else delete video.load;
      state.metadataBoundary = null;
    }
    state.metadataBoundary = {restore};
  }
  await page.evaluate(installMetadataBoundary);
  try {
    await page.setInputFiles("#assist-video-file", {name: "explicit-pending-metadata-fixture.mp4",
      mimeType: "video/mp4", buffer: Buffer.from("explicit nonempty cancellation fixture; not a decodable video")});
    await page.waitForFunction(() => window.assistLifecycle.glue.videoReplay.mode === "loading", null, {timeout: 5000});
    await setVisibility(page, "hidden"); const hidden = await captureLifecycle(page);
    assert.equal(hidden.video_mode, "idle"); assert.equal(hidden.video_loading_cleared, true);
    assert.equal(hidden.video_file_cleared, true); assert.equal(hidden.controls["assist-video-start"].disabled, true);
    await page.evaluate(() => window.assistLifecycle.glue.videoReplay.video.dispatchEvent(new Event("loadedmetadata")));
    await page.waitForTimeout(50); const late = await captureLifecycle(page);
    assert.equal(late.video_mode, "idle"); assert.equal(late.video_loading_cleared, true);
    assert.equal(late.video_file_cleared, true); assert.equal(late.controls["assist-video-start"].disabled, true);
    assert.equal(late.status, hidden.status); assert.equal(late.camera_events.length, hidden.camera_events.length);
    assertUnlocked(late); await setVisibility(page, "visible"); return {scope: "explicit_held_metadata_cancel_second_hidden", hidden, late};
  } finally { await page.evaluate(() => window.assistLifecycle.metadataBoundary?.restore()); }
}

/* Functionality: Require unused evidence path. Inputs: Absolute path. Outputs: Absence or error.
 * Logic: only ENOENT allowed; Constraints: final writes also exclusive. */
async function requireNew(path) { try { await access(path); throw new Error("Choose new evidence paths"); }
  catch (error) { if (error.code !== "ENOENT") throw error; } }

/* Functionality: Save a new screenshot with coordinate/spoken-body fields redacted.
 * Inputs: Page/new destination. Outputs: PNG. Logic: Playwright screenshot-only masks.
 * Constraints: no production DOM/CSS mutation or overwrite. */
async function screenshot(page, path) {
  await writeFile(path, await page.screenshot({fullPage: true,
    mask: [page.locator("#map, #maps-message, #route-form input, #navigation-position, #navigation-instruction, #navigation-next, #speech-text, #steps")]}),
  {flag: "wx"});
}

/* Functionality: Run actual entry lifecycle checks without owning any service.
 * Inputs: Validated CLI settings. Outputs: Truthful complete/failure report.
 * Logic: explicit synthetic media boundary, actual camera/model transport, then cancelled pending inputs.
 * Constraints: no TTS playback/physical camera/GPS/ORS claim, retries, downloads or production method mocks. */
async function run(options) {
  const report = {complete: false, physical_camera_verified: false, real_gps_verified: false, real_ors_verified: false,
    native_tts_verified: false, model_processed_synthetic_pixels: false,
    fixtures: {media: "Explicit native canvas.captureStream camera; no getUserMedia permission or physical camera",
      permission: "Explicit deferred synthetic-stream completion", inventory: "Explicit deferred fixture inventory",
      metadata: "Native src/load initiation held, cancelled before decoding; late event is not successful metadata",
      visibility: "Explicit document getter/event; not OS background behavior"}, sessions: [],
    websockets: [], dependency_failures: [], failures: []};
  let browser, page;
  try {
    const {chromium} = createRequire(import.meta.url)(resolve(options.runtime));
    browser = await chromium.launch({channel: "chrome", headless: true});
    const context = await browser.newContext({viewport: {width: 390, height: 844}});
    await context.addInitScript(installMediaFixture);
    await context.addInitScript(installSpeechProbe);
    await context.route(new URL("config/", options.url).href, route => route.fulfill({json: {
      provider: "openrouteservice", profile: "foot-walking", configured: true, code: "explicit_lifecycle_config_fixture"}}));
    page = await context.newPage();
    page.on("pageerror", error => report.failures.push({type: error.name, message: error.message.split("\n")[0].slice(0, 240)}));
    page.on("request", request => { if (normalizeApplicationPath(new URL(request.url()).pathname) === "/maps/route/")
      report.failures.push({type: "UnexpectedRoute", message: "Lifecycle tool must not request a route"}); });
    page.on("requestfailed", request => { const address = new URL(request.url());
      if (address.origin !== new URL(options.url).origin) report.dependency_failures.push({origin: address.origin,
        path: address.pathname, error: request.failure()?.errorText ?? null}); });
    /* Functionality: Retain real PRF2/session/socket evidence. Inputs: Original WebSocket.
     * Outputs: Public header/lifecycle records. Logic: discard media/control bodies.
     * Constraints: no transport or prediction replacement. */
    function socketOpened(socket) {
      const path = normalizeApplicationPath(new URL(socket.url()).pathname), entry = {path, closed: false, inputs: [], hellos: []};
      report.websockets.push(entry); socket.on("close", () => { entry.closed = true; });
      /* Functionality: Inspect original local input header only. Inputs: Sent frame. Outputs: PRF2 metadata.
       * Logic: keep index/source time; Constraints: never retain JPEG pixels. */
      function sent(frame) { if (!Buffer.isBuffer(frame.payload)) return;
        assert.ok(frame.payload.length >= 16 && frame.payload.readUInt32BE(0) === 0x50524632, "Local frame must be PRF2");
        entry.inputs.push({index: frame.payload.readUInt32BE(4), capture_time_s: frame.payload.readDoubleBE(8), bytes: frame.payload.length}); }
      /* Functionality: Inspect public session hello only. Inputs: Received control. Outputs: Continuous contract.
       * Logic: discard every binary/result/private body; Constraints: no generated admission. */
      function received(frame) { if (typeof frame.payload !== "string") return;
        let value; try { value = JSON.parse(frame.payload); } catch { return; }
        if (value.kind === "session") entry.hellos.push({provider: value.provider, protocol_version: value.protocol_version,
          session_seconds: value.session_seconds, model: value.model}); }
      if (path === "/ws/detect/local/") { socket.on("framesent", sent); socket.on("framereceived", received); }
    }
    page.on("websocket", socketOpened);
    const response = await page.goto(options.url, {waitUntil: "domcontentloaded"}); assert.equal(response.status(), 200);
    assert.match(response.headers()["permissions-policy"], /camera=\(self\)/);
    await page.evaluate(observeLifecycle);
    await page.waitForFunction(() => !document.getElementById("example").disabled, null, {timeout: 15000});
    assert.equal(await page.evaluate(() => globalThis.L?.version), "1.9.4", "Original Leaflet dependency unavailable");
    for (const id of ["speech-enabled", "navigation-speech-enabled", "assist-risk-enabled"])
      assert.equal(await page.isChecked(`#${id}`), true);
    assert.equal(await page.inputValue("#navigation-speech-voice"), "Cherry");
    report.startup = await captureLifecycle(page);
    report.startup.backend_voice = {voice: report.startup.backend_voice,
      camera_calls: report.startup.media.requests.length,
      speech_calls: report.websockets.filter(item => item.path === "/ws/tts/").length};
    assert.equal(report.startup.media.requests.length, 0); assert.equal(report.startup.native_speech_calls, 0);
    assert.equal(report.startup.navigation.active, false); assert.equal(report.startup.navigation.watch_active, false);
    assert.equal(report.startup.speech_activation.ready, false);
    assert.equal(report.startup.guidance.paused.speech, true);
    assert.equal(report.startup.guidance.reasons.speech, "activation_required");
    const vibration = await page.locator("#vibration-enabled").evaluate(node => ({supported: !node.disabled, checked: node.checked}));
    assert.equal(vibration.checked, vibration.supported); report.startup.vibration = vibration;
    for (const id of ["speech-enabled", "navigation-speech-enabled", "assist-risk-enabled"]) await page.uncheck(`#${id}`);
    if (vibration.supported) await page.uncheck("#vibration-enabled");
    report.outputs_explicitly_disabled_for_lifecycle = true;
    await page.selectOption("#assist-source", "camera"); await page.click("#assist-devices-authorize");
    await page.waitForFunction(() => document.getElementById("assist-device").options.length === 2 &&
      !document.getElementById("assist-source").disabled, null, {timeout: 5000});
    assert.equal(await page.inputValue("#assist-device"), "", "Authorization must not auto-select a device");
    await page.selectOption("#assist-device", "explicit-synthetic-canvas-camera");
    for (const boundary of ["original_stop_button", "explicit_track_ended_fixture"])
      report.sessions.push(await cameraSession(page, boundary, report));
    report.sessions.push(await pendingPermission(page)); report.sessions.push(await pendingInventory(page));
    report.sessions.push(await pendingMetadata(page));
    report.final = await captureLifecycle(page); assertUnlocked(report.final);
    assert.ok(report.final.media.streams.every(stream => stream.tracks_ended && stream.draw_timer_cleared));
    assert.equal(report.final.guidance.active.speech, null); assert.equal(report.final.guidance.risk_session_id, null);
    assert.equal(report.final.guidance.navigation_run_id, null);
    assert.equal(report.final.events.some(event => event.phase === "output_submitted"), false);
    const local = report.websockets.filter(item => item.path === "/ws/detect/local/");
    assert.equal(local.length, 2); assert.ok(local.every(item => item.closed && item.inputs.length > 0 &&
      item.hellos.length === 1 && item.hellos[0].session_seconds === null));
    assert.equal(report.websockets.filter(item => item.path === "/ws/control/").length, 1);
    await screenshot(page, options.screenshot); report.screenshot = options.screenshot;
    assert.deepEqual(report.failures, []); report.complete = true;
  } catch (error) {
    report.failures.push({type: error.name, message: error.message.split("\n")[0].slice(0, 240)});
    if (page) {
      try { report.interrupted_session = await captureLifecycle(page); } catch (failure) { report.inspection_error = failure.name; }
      try { await requireNew(options.screenshot); await screenshot(page, options.screenshot); report.screenshot = options.screenshot; }
      catch (failure) { report.screenshot_error = failure.name; }
    }
  } finally { if (browser) await browser.close(); }
  return report;
}

/* Functionality: Run once and save exclusive lifecycle evidence. Inputs: URL NEW_REPORT.json and runtime environment.
 * Outputs: New report/screenshot and truthful exit status. Logic: reuse pure entry/path validation.
 * Constraints: no optional video argument, service ownership, evidence overwrite, retries or Git writes. */
async function main() {
  if (process.argv.slice(2).length !== 2) throw new Error("Use URL NEW_REPORT.json; set PYTHON and PRS_PLAYWRIGHT_PATH");
  const options = validateArguments(process.argv.slice(2), process.env);
  await requireNew(options.output); await requireNew(options.screenshot); await mkdir(dirname(options.output), {recursive: true});
  const report = await run(options);
  await writeFile(options.output, JSON.stringify(report, null, 2) + "\n", {flag: "wx"});
  process.stdout.write(JSON.stringify({complete: report.complete, physical_camera_verified: false,
    model_processed_synthetic_pixels: report.model_processed_synthetic_pixels,
    scopes: report.sessions.map(item => item.scope), failures: report.failures}) + "\n");
  if (!report.complete) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
