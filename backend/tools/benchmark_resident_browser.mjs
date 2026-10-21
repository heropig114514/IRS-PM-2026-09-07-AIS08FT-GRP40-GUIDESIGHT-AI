/** Responsibilities: Verify the actual camera page and resident GPU path in headless Chrome.
 * Implementation: Local Uvicorn, real DOM/codec/WS/GPU, per-session counter isolation;
 * explicit static image or original-speed video canvas replaces only camera.
 * Related Modules: config.resident_asgi; tools.resident_graph_asgi/resident_graph_probe;
 * tools.resident_overlap_asgi/resident_overlap_probe/tracking_overlap_probe;
 * frontend camera/live-detection-client/frame-pipeline.
 * Declaration Index: preparePage; preparePage.drawFixture; preparePage.fakeCamera; preparePage.finiteRun;
 * preparePage.recordFrame; waitForStartup; waitForStartup.receiveLog; waitForStartup.exited;
 * parseArguments; validateMovingSession; preparePage.drawVideo; preparePage.sourceEvent;
 * preparePage.sourceEvent.ready; preparePage.sourceEvent.failed; preparePage.sourceEvent.expired;
 * preparePage.releaseSource; preparePage.observeCapture; main.serveFixture;
 * waitForStartup.expired; main; main.captureLog; main.busyProbe; main.busyProbe.failed;
 * main.busyProbe.ended; main.captureSession; main.finishSession; main.pageFailure;
 * main.pageState; summarizeSession; percentile; main.stopServer;
 * main.stopServer.exited; main.stopServer.expired.
 * Variable Index: root — backend; runtime — explicit Playwright module path;
 * main.report — all session rows, errors and fixed conditions; main.output — new evidence directory;
 * main.depthGraph/trackingOverlap/entry/video — explicit experiment/source selection, original default.
 * preparePage.probe — media observations and terminal counters retained after controller cleanup.
 * This covers local browser transport, never physical-camera or accuracy evidence.
 */
import {spawn} from "node:child_process";
import {createHash, randomBytes} from "node:crypto";
import {mkdir, readFile, writeFile} from "node:fs/promises";
import {createRequire} from "node:module";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const runtime = process.env.PRS_PLAYWRIGHT_PATH;

/** Functionality: Require explicit source/experiment selection without changing pipeline settings.
 * Inputs: CLI argument array. Outputs: output, video path and Graph/overlap selection.
 * Logic: reject missing, duplicate and unknown flags; overlap explicitly requires Graph.
 * Constraints: no implicit source fallback or device-observation overhead. */
export function parseArguments(args) {
  const [output, ...flags] = args;
  if (!output || output.startsWith("--")) throw new Error("Use OUTPUT_DIR [--depth-graph] [--video PATH]");
  let depthGraph = false, trackingOverlap = false, video = null;
  for (let index = 0; index < flags.length; index++) {
    if (flags[index] === "--depth-graph" && !depthGraph) depthGraph = true;
    else if (flags[index] === "--tracking-overlap" && !trackingOverlap) trackingOverlap = true;
    else if (flags[index] === "--video" && video === null && flags[index + 1] &&
        !flags[index + 1].startsWith("--")) video = flags[++index];
    else throw new Error(`Invalid or duplicate argument: ${flags[index]}`);
  }
  if (trackingOverlap && !depthGraph) throw new Error("Tracking overlap requires explicit --depth-graph");
  return {output, depthGraph, trackingOverlap, video};
}

/** Functionality: Reject frozen, rewound or incomplete moving-source observations.
 * Inputs: completed session with source observations at capture. Outputs: media span summary.
 * Logic: verify every observation, ordinal and monotonic source/draw progress, then actual advance.
 * Constraints: observations are not exact captured-pixel PTS; no cross-run pixel-equivalence claim. */
export function validateMovingSession(session) {
  const observations = session.rows.map(row => row.source_observation);
  if (observations.length !== 181 || session.source_capture_count !== 181)
    throw new Error("Incomplete moving-source observations");
  for (const [index, observation] of observations.entries()) {
    if (!observation || observation.ordinal !== index + 1 || observation.ended || observation.paused ||
        observation.playback_rate !== 1 ||
        [observation.media_time_s, observation.drawn_media_time_s, observation.observed_at_ms,
          observation.stream_time_s, observation.draw_count].some(value => !Number.isFinite(value) || value < 0))
      throw new Error("Invalid moving-source observation");
    const previous = observations[index - 1];
    if (previous && ["media_time_s", "drawn_media_time_s", "draw_count", "observed_at_ms", "stream_time_s"]
      .some(key => observation[key] < previous[key])) throw new Error("Moving source went backwards");
  }
  const first = observations[0], last = observations.at(-1);
  if (last.drawn_media_time_s <= first.drawn_media_time_s || last.draw_count <= first.draw_count)
    throw new Error("Moving source did not advance");
  return {first_observed_media_s: first.media_time_s, last_observed_media_s: last.media_time_s,
    drawn_media_span_s: last.drawn_media_time_s - first.drawn_media_time_s,
    video_draws_during_capture: last.draw_count - first.draw_count};
}

/** Functionality: Interpolate one percentile without discarding any timing sample.
 * Inputs: finite nonnegative values and fraction in [0,1]. Outputs: sample percentile.
 * Logic: sort a copy and interpolate inclusive endpoints. Constraints: no outlier trimming. */
function percentile(values, fraction) {
  if (!values.length || values.some(value => !Number.isFinite(value) || value < 0))
    throw new Error("Invalid timing samples");
  const ordered = [...values].sort((a, b) => a - b), position = (ordered.length - 1) * fraction;
  const left = Math.floor(position), right = Math.ceil(position);
  return ordered[left] + (ordered[right] - ordered[left]) * (position - left);
}

/** Functionality: Report cold cost, complete steady latencies and unchanged performance gates.
 * Inputs: one completed original session. Outputs: summary of every row except separated first.
 * Logic: steady interval starts at the second capture and ends at last decode; nested stage
 * timings remain separate. Constraints: no automatic acceptance on functional success alone. */
export function summarizeSession(session) {
  const rows = session.rows.slice(1), last = rows.at(-1);
  const fps = rows.length * 1000 / (last.decodedAt - rows[0].capturedAt);
  const latency = rows.map(row => row.captureToDecodedMs);
  return {first_decoded_ms: session.rows[0].captureToDecodedMs, steady_frames: rows.length,
    steady_fps: fps, decoded_p50_ms: percentile(latency, 0.5),
    decoded_p95_ms: percentile(latency, 0.95), decoded_max_ms: Math.max(...latency),
    queue_p50_ms: percentile(rows.map(row => row.metadata.resident_queue_ms), 0.5),
    server_p50_ms: percentile(rows.map(row => row.metadata.server_ms), 0.5),
    depth_p50_ms: percentile(rows.map(row => row.metadata.depth_predict_ms), 0.5),
    meets_existing_10fps_500ms_gate: fps >= 10 && Math.max(...latency) <= 500};
}

/** Functionality: Provide a declared camera fixture and finite existing-pipeline run.
 * Inputs: diagnostic fixture URL and moving-source flag. Outputs: in-page state and wrappers.
 * Logic: retain actual capture, decode, client validation and HUD; bound only total run length.
 * Constraints: no fake GPU, changed rate/window/model or physical-camera permission claims. */
async function preparePage({sourceUrl, moving}) {
  const {cameraApp} = await import("./assets/camera.js");
  const {FramePipeline} = await import("./assets/frame-pipeline.js");
  const response = await fetch(sourceUrl);
  if (!response.ok) throw new Error("Fixture unavailable");
  const objectUrl = URL.createObjectURL(await response.blob());
  const source = moving ? document.createElement("video") : new Image();
  /** Functionality: Await one media transition or original decoder failure.
   * Inputs: event name and transition callback. Outputs: transition Promise.
   * Logic: register before changing media state. Constraints: no seek/play retries. */
  async function sourceEvent(eventName, transition) {
    await new Promise((done, reject) => {
      /** Functionality: Release media transition listeners. Inputs: event. Outputs: resolved wait.
       * Logic: remove both listeners and timer. Constraints: no playback or seek added. */
      function ready() {
        clearTimeout(timer); source.removeEventListener("error", failed);
        source.removeEventListener(eventName, ready); done();
      }
      /** Functionality: Preserve media decoding failure. Inputs: error event. Outputs: rejection.
       * Logic: release listeners. Constraints: never replaces the fixture. */
      function failed() {
        clearTimeout(timer); source.removeEventListener(eventName, ready);
        source.removeEventListener("error", failed); reject(new Error("Fixture video decoder failed"));
      }
      /** Functionality: Bound a stalled media transition. Inputs: timer. Outputs: rejection.
       * Logic: release listeners. Constraints: timeout is a failure, not a retry trigger. */
      function expired() {
        source.removeEventListener(eventName, ready); source.removeEventListener("error", failed);
        reject(new Error(`Fixture video ${eventName} timed out`));
      }
      const timer = setTimeout(expired, 15000);
      source.addEventListener(eventName, ready, {once: true});
      source.addEventListener("error", failed, {once: true});
      transition();
    });
  }
  if (moving) {
    source.muted = true; source.playsInline = true; source.loop = false; source.playbackRate = 1;
    await sourceEvent("loadeddata", () => { source.src = objectUrl; });
  } else { source.src = objectUrl; await source.decode(); URL.revokeObjectURL(objectUrl); }
  const canvas = document.createElement("canvas");
  canvas.width = moving ? source.videoWidth : source.naturalWidth;
  canvas.height = moving ? source.videoHeight : source.naturalHeight;
  canvas.getContext("2d").drawImage(source, 0, 0);
  const probe = window.residentProbe = {rows: [], captures: [], app: cameraApp, stream: null,
    runFinished: false, source, moving, source_failure: null, draw_count: 0,
    source_info: {width: canvas.width, height: canvas.height, duration_s: moving ? source.duration : null,
      playback_rate: moving ? source.playbackRate : null, loop: moving ? source.loop : null}};
  /** Functionality: Copy the current decoded fixture into the camera-source canvas.
   * Inputs: selected image or current video frame. Outputs: canvas frame events.
   * Logic: the image timer or video callback drives draws; an idle canvas may stall stream play.
   * Constraints: no synthetic motion or replacement source; capture pipeline remains unmodified. */
  function drawFixture() { canvas.getContext("2d").drawImage(source, 0, 0); }
  if (!moving) probe.sourceTimer = setInterval(drawFixture, 1000 / 30);
  /** Functionality: Draw decoded video frames without pacing the GPU or replaying missed frames.
   * Inputs: browser frame callback time and metadata. Outputs: canvas and latest media observation.
   * Logic: original-speed video drives drawing; camera capture remains separately credit limited.
   * Constraints: mediaTime describes the source callback, not exact pixels delivered by MediaStream. */
  function drawVideo(now, metadata) {
    drawFixture(); probe.draw_count += 1;
    probe.drawn_media_time_s = metadata.mediaTime; probe.drawn_at_ms = now;
    probe.videoCallback = source.requestVideoFrameCallback(drawVideo);
  }
  /** Functionality: Pause the fixture when the real controller stops a session.
   * Inputs: controller lifecycle event. Outputs: paused source and cancelled draw callback.
   * Logic: subscribe to existing stop events. Constraints: never pauses an active GPU session. */
  function releaseSource(event) {
    if (event.type === "stop" && moving) {
      source.pause(); source.cancelVideoFrameCallback(probe.videoCallback);
    }
  }
  cameraApp.subscribe(releaseSource);
  /** Functionality: Supply a fresh fixture stream per explicit UI start.
   * Inputs: ignored sensor options. Outputs: canvas MediaStream.
   * Logic: rewind only between sessions and start normal playback; old tracks stay stopped.
   * Constraints: no looping, slowed playback or physical-camera access. */
  async function fakeCamera() {
    if (moving) {
      if (source.currentTime !== 0) await sourceEvent("seeked", () => { source.currentTime = 0; });
      probe.draw_count = 0; probe.drawn_media_time_s = source.currentTime; probe.drawn_at_ms = performance.now();
      drawFixture();
    }
    const stream = canvas.captureStream(30);
    probe.stream = stream;
    if (moving) { probe.videoCallback = source.requestVideoFrameCallback(drawVideo); await source.play(); }
    return stream;
  }
  Object.defineProperty(navigator.mediaDevices, "getUserMedia", {value: fakeCamera});
  const run = FramePipeline.prototype.run, publish = cameraApp.publishFrame, capture = cameraApp.capture;
  /** Functionality: Observe media progress immediately before the unchanged actual JPEG capture.
   * Inputs: original camera instance. Outputs: original JPEG Promise and ordinal observation.
   * Logic: record clocks without seeking or waiting for a source frame. Constraints: EOF/paused/error
   * fails explicitly; source observations do not replace original capture or GPU tracking timestamps. */
  function observeCapture() {
    if (source.ended || source.paused || source.error || source.playbackRate !== 1) {
      probe.source_failure = "Video ended, paused or failed before all requested captures";
      throw new Error(probe.source_failure);
    }
    probe.captures.push({ordinal: probe.captures.length + 1, observed_at_ms: performance.now(),
      media_time_s: source.currentTime, drawn_media_time_s: probe.drawn_media_time_s,
      drawn_at_ms: probe.drawn_at_ms, draw_count: probe.draw_count, stream_time_s: this.video.currentTime,
      ended: source.ended, paused: source.paused, playback_rate: source.playbackRate});
    return capture.call(this);
  }
  if (moving) cameraApp.capture = observeCapture;
  /** Functionality: Limit only the number of frames for a reproducible finite test.
   * Inputs: original pipeline instance. Outputs: original run counters.
   * Logic: preserve default 30 FPS/twelve slots and first-frame isolation; retain terminal counters
   * even when the controller has cleared its pipeline after a source failure.
   * Constraints: no skipped frame or early cutoff to hide high latency. */
  async function finiteRun() {
    this.frameLimit = 181;
    probe.rows = []; probe.captures = []; probe.runFinished = false;
    probe.contract = this.client.contract;
    try { return await run.call(this); }
    finally {
      probe.pipeline_result = {sent: this.sent, received: this.received, max_inflight: this.maxInFlight,
        failure: this.failure?.message || null};
      probe.runFinished = true;
    }
  }
  FramePipeline.prototype.run = finiteRun;
  /** Functionality: Retain every displayed frame before updating the unchanged HUD.
   * Inputs: original publishFrame arguments. Outputs: raw timing/metadata rows and DOM updates.
   * Logic: no trimming or independent timing origin. Constraints: no JPEG body saved in report. */
  function recordFrame(processing, bytes, result, timing, sent, received) {
    probe.rows.push({...timing, metadata: result.metadata,
      ...(moving ? {source_observation: probe.captures[timing.ordinal - 1]} : {})});
    return publish.call(this, processing, bytes, result, timing, sent, received);
  }
  cameraApp.publishFrame = recordFrame;
}

/** Functionality: Wait for actual ASGI startup or fail on process exit/timeout.
 * Inputs: child process. Outputs: startup Promise. Logic: read Uvicorn lifecycle acknowledgement.
 * Constraints: no polling requests, credentials, retries or arbitrary sleep-based readiness. */
function waitForStartup(server) {
  return new Promise((resolveReady, reject) => {
    let buffer = "";
    /** Functionality: Detect startup in process diagnostics. Inputs: log chunk. Outputs: readiness.
     * Logic: accumulated text handles split chunks. Constraints: logs contain no frame payloads. */
    function receiveLog(chunk) {
      buffer += chunk.toString();
      if (buffer.includes("Application startup complete.")) {
        clearTimeout(timer); server.off("exit", exited); server.stderr.off("data", receiveLog);
        resolveReady();
      }
    }
    /** Functionality: Reject premature process exit. Inputs: exit code. Outputs: rejected startup.
     * Logic: clear timer. Constraints: never starts a replacement server. */
    function exited(code) { clearTimeout(timer); reject(new Error(`Server exited before startup: ${code}`)); }
    /** Functionality: Fail an unready service. Inputs: timer. Outputs: startup error.
     * Logic: finite diagnostic budget. Constraints: no implicit fallback. */
    function expired() { reject(new Error("Resident startup timed out")); }
    const timer = setTimeout(expired, 90000);
    server.stderr.on("data", receiveLog); server.once("exit", exited);
    server.once("error", reject);
  });
}

/** Functionality: Exercise two complete UI sessions, busy rejection and explicit user stop.
 * Inputs: new output directory, optional --depth-graph/--tracking-overlap/--video and PRS_PLAYWRIGHT_PATH.
 * Outputs: JSON/screenshots/server diagnostics with separate functional/performance verdicts.
 * Logic: one model process, unchanged 30 FPS/twelve-window path; counters restart per session.
 * Constraints: uses fixed local port8766 and no outside hosts; port conflicts fail, never kill others. */
async function main() {
  const {output, depthGraph, trackingOverlap, video} = parseArguments(process.argv.slice(2));
  const entry = trackingOverlap ? "tools.resident_overlap_asgi:application" :
    depthGraph ? "tools.resident_graph_asgi:application" : "config.resident_asgi:application";
  const report = {status: "running", scope: "Actual browser with explicit canvas source and local GPU",
    conditions: {input_fps: 30, max_inflight: 12, frames_per_session: 181, mode: "full",
      depth_graph_experiment: depthGraph, tracking_overlap_experiment: trackingOverlap,
      asgi_entry: entry, source_kind: video ? "moving_video" : "static_image",
      source_time_semantics: "Capture-entry observations; not exact delivered pixel PTS or resident-v1 worker time"},
    sessions: []};
  if (!output || !runtime) throw new Error("Use OUTPUT_DIR and set PRS_PLAYWRIGHT_PATH");
  await mkdir(resolve(output));
  report.source_path = video ? resolve(video) : resolve(root, "model_tests/samples/bus.jpg");
  const sourceBytes = await readFile(report.source_path);
  report.source_sha256 = createHash("sha256").update(sourceBytes).digest("hex");
  report.model_sha256 = {};
  for (const path of ["models/yolo26n.pt", "models/yolo26n-depth.pt", "models/yolo26n.json"])
    report.model_sha256[path] = createHash("sha256").update(await readFile(resolve(root, path))).digest("hex");
  report.source_code_sha256 = {};
  for (const path of ["../frontend/camera.js", "../frontend/live-detection-client.js",
    "../frontend/frame-pipeline.js", "realtime/services.py", "realtime/resident_asgi.py",
    "realtime/detector.py", "realtime/depth.py", "realtime/distance.py",
    "realtime/distance_config.py", "realtime/performance.py", "tools/depth_graph_probe.py",
    "tools/resident_graph_probe.py", "tools/resident_graph_asgi.py",
    "tools/tracking_overlap_probe.py", "tools/resident_overlap_probe.py", "tools/resident_overlap_asgi.py",
    "tools/benchmark_resident_browser.mjs"])
    report.source_code_sha256[path] = createHash("sha256").update(await readFile(resolve(root, path))).digest("hex");
  const server = spawn(resolve(root, ".venv/Scripts/python.exe"), ["-m", "uvicorn",
    entry, "--host", "127.0.0.1", "--port", "8766", "--workers", "1",
    "--lifespan", "on", "--ws", "websockets-sansio", "--ws-max-size", "1310728",
    "--ws-max-queue", "12", "--ws-per-message-deflate", "false"], {cwd: root, windowsHide: true,
    env: {...process.env, DJANGO_SECRET_KEY: process.env.DJANGO_SECRET_KEY || randomBytes(48).toString("hex"),
      RESIDENT_MODE: "full", YOLO_CONFIG_DIR: resolve(root, ".venv/ultralytics"),
      MPLCONFIGDIR: resolve(root, ".venv/matplotlib"), YOLO_AUTOINSTALL: "false", PYTHONUTF8: "1"}});
  const logs = [];
  /** Functionality: Retain local process diagnostics. Inputs: chunk. Outputs: log array.
   * Logic: preserve original failures. Constraints: no secret or media data is printed by this tool. */
  function captureLog(chunk) { logs.push(chunk.toString()); }
  server.stdout.on("data", captureLog); server.stderr.on("data", captureLog);
  let browser, page;
  try {
    await waitForStartup(server);
    const {chromium} = createRequire(import.meta.url)(runtime);
    browser = await chromium.launch({channel: "chrome", headless: true});
    report.browser = browser.version();
    page = await browser.newPage({viewport: {width: 1440, height: 1000}, locale: "zh-CN"});
    report.page_errors = [];
    /** Functionality: Preserve uncaught page errors. Inputs: browser error. Outputs: report entry.
     * Logic: store message only. Constraints: no media or network payload capture. */
    function pageFailure(error) { report.page_errors.push(error.message); }
    page.on("pageerror", pageFailure);
    /** Functionality: Serve only the explicitly selected local diagnostic fixture.
     * Inputs: intercepted fixture route. Outputs: source bytes and declared media type.
     * Logic: browser preloads a Blob before timing. Constraints: no production HTTP endpoint. */
    async function serveFixture(route) {
      await route.fulfill({status: 200, contentType: video ? "video/mp4" : "image/jpeg", body: sourceBytes});
    }
    await page.route("http://127.0.0.1:8766/__resident_fixture", serveFixture);
    await page.goto("http://127.0.0.1:8766/live/", {waitUntil: "networkidle"});
    await page.evaluate(preparePage, {sourceUrl: "/__resident_fixture", moving: Boolean(video)});
    report.source_info = await page.evaluate(() => window.residentProbe.source_info);
    await page.selectOption("#processing", "resident");
    report.resident_note_visible = await page.locator("#resident-note").isVisible();
    for (let session = 0; session < 2; session++) {
      await page.click("#start");
      await page.waitForFunction(() => window.residentProbe.app.pipeline?.received >= 1,
        null, {timeout: 60000});
      /** Functionality: Verify a second client is refused while the page owns the GPU.
       * Inputs: real same-origin client and busy service. Outputs: safe diagnostic/error code.
       * Logic: one connection only, no image submission. Constraints: never retries admission. */
      async function busyProbe() {
        const {LiveDetectionClient} = await import("./assets/live-detection-client.js");
        const messages = [];
        /** Functionality: Retain safe client failure. Inputs: error. Outputs: diagnostic text.
         * Logic: record only message. Constraints: no re-open or media. */
        function failed(error) { messages.push(error.message); }
        /** Functionality: Reject an unexpected clean end. Inputs: none. Outputs: marker.
         * Logic: busy should fail admission. Constraints: no recovery. */
        function ended() { messages.push("unexpected end"); }
        const client = new LiveDetectionClient("ws://127.0.0.1:8766/ws/detect/resident/",
          failed, ended, {protocol: "resident-v1"});
        try { await client.connect(); throw new Error("Busy admission unexpectedly succeeded"); }
        catch (error) {
          if (!error.message.includes("另一个视频")) throw error;
          return messages;
        } finally { client.stop(); }
      }
      if (session === 0) report.busy_rejection = await page.evaluate(busyProbe);
      await page.waitForFunction(() => window.residentProbe.runFinished, null, {timeout: 90000});
      /** Functionality: Snapshot all original pipeline rows and rendered dimensions.
       * Inputs: page test state. Outputs: counters/contract/rows and real decoded image shape.
       * Logic: no filtering. Constraints: snapshot only after complete finite pipeline drain. */
      function captureSession() {
        const {app, rows, contract, pipeline_result} = window.residentProbe;
        return {contract, ...pipeline_result, rows, status: app.status.textContent,
          source_capture_count: window.residentProbe.captures.length,
          dimensions: [app.image.naturalWidth, app.image.naturalHeight]};
      }
      const evidence = await page.evaluate(captureSession);
      report.sessions.push(evidence);
      if (evidence.failure) throw new Error(evidence.failure);
      if (evidence.sent !== 181 || evidence.received !== 181 || evidence.rows.length !== 181 ||
          evidence.contract.protocol !== "resident-v1" || evidence.max_inflight > 12)
        throw new Error("Incomplete or over-capacity resident page session");
      for (const [index, row] of evidence.rows.entries()) {
        const graph = row.metadata.depth_graph_experiment;
        const overlapExpected = trackingOverlap && !(session === 0 && index === 0);
        if (Boolean(row.metadata.tracking_overlap_experiment) !== overlapExpected ||
            (overlapExpected && (row.metadata.stage_timing_model !== "overlapping_cpu_depth" ||
              row.metadata.tracking_overlap_experiment.device_observation_enabled !== false)))
          throw new Error("Unexpected tracking overlap execution or timing semantics");
        if (depthGraph ? (!graph || graph.capture_count !== 1 ||
            graph.replayed !== !(session === 0 && index === 0) ||
            ((graph.prepare_ms > 0) !== (session === 0 && index === 0))) : graph !== undefined)
          throw new Error("Unexpected experimental Graph capture/reuse evidence");
        if (row.metadata.tracking_status !== "active") throw new Error("Tracking became unavailable");
        if (row.metadata.performance?.total_frames !== index + 1 ||
            row.metadata.performance?.depth_model_calls !== index + 1)
          throw new Error("Resident processing counters leaked across sessions");
      }
      evidence.summary = summarizeSession(evidence);
      if (video) evidence.source_progress = validateMovingSession(evidence);
      await page.screenshot({path: resolve(output, `session-${session}.png`), fullPage: true});
      /** Functionality: Request normal drain on the actual socket after all frames display.
       * Inputs: existing client. Outputs: stop control. Logic: retain onEnd/controller lifecycle.
       * Constraints: no forced close before server summary. */
      function finishSession() { window.residentProbe.app.client.socket.send('{"kind":"stop"}'); }
      await page.evaluate(finishSession);
      await page.waitForFunction(() => window.residentProbe.app.mode === "idle", null, {timeout: 15000});
      evidence.tracks_ended = await page.evaluate(() => window.residentProbe.stream.getTracks()
        .every(track => track.readyState === "ended"));
      if (!evidence.tracks_ended) throw new Error("Camera fixture was not released");
      if (video) {
        evidence.source_paused = await page.evaluate(() => window.residentProbe.source.paused);
        if (!evidence.source_paused) throw new Error("Video fixture was not paused on release");
      }
    }
    await page.click("#start");
    await page.waitForFunction(() => window.residentProbe.app.pipeline?.received >= 2,
      null, {timeout: 15000});
    await page.click("#stop");
    report.user_stop = await page.evaluate(() => ({mode: window.residentProbe.app.mode,
      source_paused: window.residentProbe.moving ? window.residentProbe.source.paused : null,
      tracks_ended: window.residentProbe.stream.getTracks().every(track => track.readyState === "ended") }));
    if (report.user_stop.mode !== "idle" || !report.user_stop.tracks_ended) throw new Error("Stop failed");
    if (video && !report.user_stop.source_paused) throw new Error("Video kept playing after user stop");
    if (report.page_errors.length) throw new Error("Uncaught page errors");
    report.meets_existing_10fps_500ms_gate = report.sessions.every(session =>
      session.summary.meets_existing_10fps_500ms_gate);
    report.status = "completed";
    if (!report.meets_existing_10fps_500ms_gate) process.exitCode = 2;
  } catch (error) {
    report.status = "failed"; report.failure = {type: error.name, detail: error.message};
    if (page) {
      /** Functionality: Diagnose the exact page phase and retain partial delivered-frame evidence.
       * Inputs: current app/DOM. Outputs: safe state, counters and partial rows.
       * Logic: inspect implementation state before blaming runtime. Constraints: no JPEG payload. */
      function pageState() {
        const app = window.residentProbe?.app;
        return {mode: app?.mode, phase: app?.cameraPhase, status: app?.status.textContent,
          message: app?.message.textContent, video_state: app?.video.readyState,
          sent: app?.pipeline?.sent, received: app?.pipeline?.received,
          source_failure: window.residentProbe?.source_failure, captures: window.residentProbe?.captures,
          rows: window.residentProbe?.rows};
      }
      report.page_state = await page.evaluate(pageState);
      await page.screenshot({path: resolve(output, "failure.png"), fullPage: true});
    }
    process.exitCode = 1;
  } finally {
    await browser?.close();
    /** Functionality: Stop only this test's owned child process and await its exit.
     * Inputs: spawned server. Outputs: exit Promise. Logic: finite cleanup, no unrelated process kill.
     * Constraints: Windows termination is not graceful-ASGI evidence; lifecycle is tested separately. */
    async function stopServer() {
      if (server.exitCode !== null) return;
      await new Promise((done, reject) => {
        /** Functionality: Confirm child exit. Inputs: process event. Outputs: cleanup resolution.
         * Logic: clear timer. Constraints: no shutdown-complete claim on Windows termination. */
        function exited() { clearTimeout(timer); done(); }
        /** Functionality: Fail bounded cleanup. Inputs: timer. Outputs: rejection.
         * Logic: preserve failure. Constraints: no broad process termination. */
        function expired() { reject(new Error("Owned server did not exit")); }
        const timer = setTimeout(expired, 15000);
        server.once("exit", exited); server.kill();
      });
    }
    try { await stopServer(); }
    finally {
      await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
      await writeFile(resolve(output, "server.log"), logs.join(""));
    }
  }
  console.log(JSON.stringify({status: report.status, sessions: report.sessions.length, failure: report.failure}));
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
