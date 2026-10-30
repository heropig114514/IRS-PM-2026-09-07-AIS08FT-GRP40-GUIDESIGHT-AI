/** Responsibilities: Compare historical12/24FPS windows or explicit24/30FPS eight/twelve windows.
 * Implementation: Real CameraApp codec/HUD/ordered display; one production client/allocation,
 * or explicitly synthetic timer echo with local modules for zero-GPU controls; retain all rows.
 * Related Modules: camera.js/frame-pipeline.js/live-detection-client.js; backend fine-profiling.
 * Declaration Index: main; main.routeModule; runPage; runPage.fakeCamera; runPage.failed;
 * runPage.runPhases; runPage.runPhases.capture; runPage.runPhases.display;
 * runPage.runPhases.frame; runPage.runPhases.sent; runPage.runPhases.active;
 * runPage.sendFrame; runPage.sendFrame.deliver; runPage.stop; summarize.
 * Variable Index: args/report — explicit CLI/evidence; phases — declared order/count/rate/window;
 * thirtyProfile — explicit --30fps selects balanced24/8,30/8,30/12 repeats, never a fallback;
 * runtime — PRS_PLAYWRIGHT_PATH; synthetic delay is a diagnostic transport, never a fallback.
 * No physical sensor/mobile radio/paint measurement; no reconnect or implicit second GPU call.
 */
import {createRequire} from "node:module";
import {createHash} from "node:crypto";
import {mkdir, readFile, writeFile} from "node:fs/promises";
import {dirname} from "node:path";

const args = process.argv.slice(2), runtime = process.env.PRS_PLAYWRIGHT_PATH;
const report = {complete: false, phases: [], scope: "actual browser codec/HUD/decode; synthetic public-image camera; transport selected explicitly in mode"};
const thirtyProfile = args.at(-1) === "--30fps";
const phases = thirtyProfile
  ? [{rate: 24, window: 8, frames: 61}, {rate: 30, window: 8, frames: 91},
    {rate: 30, window: 12, frames: 151}, {rate: 30, window: 12, frames: 151},
    {rate: 30, window: 8, frames: 91}, {rate: 24, window: 8, frames: 61}]
  : [{rate: 12, window: 5, frames: 61}, {rate: 24, window: 5, frames: 121},
    {rate: 24, window: 8, frames: 121}, {rate: 12, window: 8, frames: 61}];

/** Functionality: Summarize one phase without omitting steady stalls.
 * Inputs: rows/conditions/counters. Outputs: completion/capture FPS and stage statistics.
 * Logic: cold first frame of each explicitly separate phase is reported separately; retain rows.
 * Constraints: original short-phase FPS includes pipeline refill/final drain and remains unchanged;
 * matched nonfirst completion intervals are additionally reported; endpoint latency changes may
 * transiently inflate that interval rate. Stage medians are nested, not additive. */
function summarize({rows, conditions, counters}) {
  const steady = rows.slice(1), first = rows[0], last = rows.at(-1), stages = {};
  for (const key of ["pacingLateMs", "creditWaitMs", "captureMs", "transportMs", "decodeMs", "captureToDecodedMs",
    "prs_admission_queue_ms", "prs_upstream_ms", "prs_result_validation_ms", "hf_parent_queue_ms",
    "hf_local_cycle_ms", "hf_result_ingest_ms", "hf_output_queue_ms", "worker_pull_ms", "worker_predict_ms",
    "server_ms", "forward_event_ms"]) {
    const values = steady.map(row => row[key] ?? row.metadata?.[key]).filter(Number.isFinite);
    if (!values.length) continue;
    const sorted = [...values].sort((a, b) => a - b);
    stages[key] = {samples: values.length, mean_ms: values.reduce((a, b) => a + b, 0) / values.length,
      median_ms: (sorted[(sorted.length - 1) >> 1] + sorted[sorted.length >> 1]) / 2,
      max_ms: Math.max(...values)};
  }
  return {conditions, counters, first_frame_ms: first.captureToDecodedMs,
    steady_fps: steady.length * 1000 / (last.decodedAt - first.decodedAt),
    completion_interval_fps: (steady.length - 1) * 1000 / (last.decodedAt - steady[0].decodedAt),
    actual_capture_fps: (steady.length - 1) * 1000 / (last.capturedAt - steady[0].capturedAt),
    credit_wait_frames: steady.filter(row => row.creditWaitMs > 0).length,
    credit_wait_total_ms: steady.reduce((sum, row) => sum + row.creditWaitMs, 0), stages, rows};
}

/** Functionality: Run fixed comparisons through the actual CameraApp or declared echo control.
 * Inputs: phase conditions, public source bytes and optional synthetic delay.
 * Outputs: raw rows/contracts/failures/counters. Logic: one real connection shared across phases;
 * each phase drains all work before the next, resets only phase statistics and local ordinal.
 * Constraints: actual client retains global wire sequence; no source/model/codec/detection change.
 * Check the loaded client window before media setup or provider start; insufficient ceilings return
 * an explicit failed preflight, never a smaller-window substitution or partial GPU experiment. */
async function runPage({conditions, sourceBytes, delay}) {
  const {CameraApp} = await import("./assets/camera.js");
  const {FramePipeline} = await import("./assets/frame-pipeline.js");
  const {MAX_INFLIGHT} = await import("./assets/live-detection-client.js");
  const requiredWindow = Math.max(...conditions.map(condition => condition.window));
  if (requiredWindow > MAX_INFLIGHT) return {results: [], contract: null,
    failures: [`Test requires${requiredWindow} slots; loaded client ceiling is${MAX_INFLIGHT}. Coordinate deployment before GPU testing.`],
    windowCeiling: MAX_INFLIGHT, preflightDenied: true};
  const source = new Image(), url = URL.createObjectURL(new Blob([new Uint8Array(sourceBytes)], {type: "image/jpeg"}));
  source.src = url; await source.decode(); URL.revokeObjectURL(url);
  const canvas = document.createElement("canvas"); canvas.width = source.naturalWidth; canvas.height = source.naturalHeight;
  canvas.getContext("2d").drawImage(source, 0, 0);
  const stream = canvas.captureStream(30), app = new CameraApp(), results = [], failures = [];
  /** Functionality: Supply the declared public-image MediaStream. Inputs: ignored constraints.
   * Outputs: canvas stream. Logic: replace only sensor authorization; Constraints: no real camera. */
  async function fakeCamera() { return stream; }
  Object.defineProperty(navigator.mediaDevices, "getUserMedia", {value: fakeCamera});
  const originalFailure = app.handleDisconnect;
  /** Functionality: Retain and surface the original controller failure. Inputs: Error.
   * Outputs: error marker and normal stop. Logic: no retry; Constraints: no credential logging. */
  function failed(error) { failures.push(error.message); originalFailure(error); }
  app.handleDisconnect = failed;
  /** Functionality: Execute all explicit window/rate phases on a single connected client.
   * Inputs: real or declared synthetic client, processing mode and generation token.
   * Outputs: fully drained phase histories. Logic: retain actual capture/display/validation/HUD.
   * Constraints: settings differ only at explicit phase boundaries; first frame alone in each;
   * failure stops the entire comparison and never requests a replacement GPU allocation. */
  async function runPhases(client, processing, token) {
    for (const condition of conditions) {
      const rows = [];
      app.frameTimings = null; app.cameraPhase = "pipeline";
      /** Functionality: Encode the current actual video. Inputs: app state. Outputs: JPEG bytes.
       * Logic: unchanged CameraApp.capture; Constraints: same640/JPEG80. */
      async function capture() { return app.capture(); }
      /** Functionality: Decode the validated next result. Inputs: bytes/metadata.
       * Outputs: UI update. Logic: original generation/dimension guard; Constraints: no paint claim. */
      async function display(bytes, metadata) { await app.showFrame(bytes, token, metadata ?? null); }
      /** Functionality: Retain every decoded result and update the actual HUD.
       * Inputs: result/timing. Outputs: rows/DOM. Logic: original publish; Constraints: no filtering. */
      function frame(result, timing) {
        rows.push({...timing, metadata: result.metadata});
        app.publishFrame(processing, timing.inputBytes, result, timing, timing.sent, timing.received);
      }
      /** Functionality: Preserve sent/completed counters. Inputs: count/received.
       * Outputs: DOM. Logic: admission counters; Constraints: no claim pending frames completed. */
      function sent(count, received) { document.getElementById("frames").textContent = `${count} / ${received}`; }
      /** Functionality: Check session generation. Inputs: token/app. Outputs: boolean.
       * Logic: explicit stop is authoritative; Constraints: never restarts a session. */
      function active() { return token === app.generation; }
      app.pipeline = new FramePipeline({client, capture, display, onFrame: frame, onSent: sent,
        isActive: active, onFailure: failed, rate: condition.rate, maxPending: condition.window,
        frameLimit: condition.frames});
      const counters = await app.pipeline.run();
      results.push({conditions: condition, counters, rows});
      if (counters.received !== condition.frames) throw new Error("Phase ended before complete delivery");
    }
  }
  /** Functionality: Echo encoded bytes after an explicit timer control delay.
   * Inputs: bytes. Outputs: result Promise. Logic: synthetic RTT only; Constraints: no socket/GPU. */
  function sendFrame(bytes) {
    const started = performance.now();
    /** Functionality: Complete synthetic timer echo. Inputs: closure bytes/start.
     * Outputs: result. Logic: preserve bytes; Constraints: metadata intentionally absent. */
    function deliver() { return {bytes, rttMs: performance.now() - started}; }
    return new Promise(resolve => setTimeout(() => resolve(deliver()), delay));
  }
  /** Functionality: Represent synthetic-client cleanup. Inputs: None. Outputs: None.
   * Logic: finite phases drain timers; Constraints: no real external resources. */
  function stop() {}
  try {
    if (delay === null) {
      app.processing.value = "hf"; app.runPipeline = runPhases;
      await app.start();
    } else {
      app.video.srcObject = stream; await app.video.play(); app.mode = "camera";
      await runPhases({sendFrame, stop}, "echo", app.generation);
    }
    return {results, failures, contract: app.client?.contract ?? null,
      windowCeiling: MAX_INFLIGHT, preflightDenied: false};
  } finally { app.stop(); for (const track of stream.getTracks()) track.stop(); }
}

/** Functionality: Launch one bounded actual browser comparison and save honest failure evidence.
 * Inputs: HTTPS URL/output, optional --echo-delay MS and --30fps, runtime/Chrome/public fixture.
 * Outputs: JSON/exit1 on incomplete delivery. Logic: synthetic mode intercepts three explicitly
 * recorded local modules; real HF mode always uses public deployment and one client connection.
 * --30fps selects606 frames in six balanced phases; both30/12 segments retain150 nonfirst frames
 * while comparison segments are shortened to reduce quota cost. Historical default is preserved.
 * Fingerprint this runner; record the loaded transport ceiling and an explicit preflight denial.
 * Constraints: only echo mode guarantees zero GPU; real mode needs quota preflight first;
 * profiler disabled, TLS checks retained, no retry or acceptance-criterion relaxation. */
async function main() {
  const options = args.slice(2);
  if (thirtyProfile) options.pop();
  if (args.length < 2 || !(options.length === 0 || options.length === 2 && options[0] === "--echo-delay") || !runtime)
    throw new Error("Use URL OUTPUT [--echo-delay MS] [--30fps] and PRS_PLAYWRIGHT_PATH");
  const [url, output] = args, parsed = new URL(url), delay = options.length === 2 ? Number(options[1]) : null;
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash ||
      delay !== null && (!Number.isFinite(delay) || delay < 0 || delay > 1000)) throw new Error("Invalid control conditions");
  report.mode = delay === null ? "one HF allocation" : "synthetic echo";
  report.profile = thirtyProfile ? "balanced24/8-30/8-30/12" : "historical12/24-5/8";
  report.planned_phases = phases;
  report.tool_sha256 = createHash("sha256").update(await readFile(new URL(import.meta.url))).digest("hex");
  report.synthetic_delay_ms = delay; report.gpu_calls = delay === null ? "one requested on first input" : 0;
  const {chromium} = createRequire(import.meta.url)(runtime), browser = await chromium.launch({channel: "chrome", headless: true});
  report.browser = browser.version(); report.url = url;
  try {
    const page = await browser.newPage();
    if (delay !== null) for (const name of ["camera.js", "frame-pipeline.js", "live-detection-client.js"]) {
      const source = await readFile(new URL("../" + name, import.meta.url));
      (report.local_module_sha256 ??= {})[name] = createHash("sha256").update(source).digest("hex");
      /** Functionality: Load the explicitly selected local CPU-control module.
       * Inputs: route and enclosing source. Outputs: module response.
       * Logic: exact asset only; Constraints: no public mutation or transport fallback. */
      async function routeModule(route) { await route.fulfill({status: 200, contentType: "application/javascript", body: source}); }
      await page.route("**/assets/" + name, routeModule);
    }
    const response = await page.goto(url, {waitUntil: "networkidle", timeout: 20000});
    if (!response.ok()) throw new Error(`Page returned HTTP ${response.status()}`);
    const sourceBytes = await readFile(new URL("./fixtures/detection-sample.jpg", import.meta.url));
    const evidence = await page.evaluate(runPage, {conditions: phases, sourceBytes: [...sourceBytes], delay});
    report.failures = evidence.failures; report.session_contract = evidence.contract;
    report.loaded_window_ceiling = evidence.windowCeiling; report.preflight_denied = evidence.preflightDenied;
    if (evidence.preflightDenied) report.gpu_calls = 0;
    report.phases = evidence.results.map(summarize);
    report.complete = report.phases.length === phases.length && !evidence.failures.length;
    if (delay === null && report.complete) {
      const all = report.phases.flatMap(phase => phase.rows), first = all[0];
      report.stable_prediction_hashes = all.every(row => row.metadata.raw_sha256 === first.metadata.raw_sha256 &&
        row.metadata.jpeg_sha256 === first.metadata.jpeg_sha256);
      report.repeated_graph_setups = all.slice(1).filter(row => row.metadata.graph_setup_ms > 0).length;
      report.max_session_steady_decoded_ms = Math.max(...all.slice(1).map(row => row.captureToDecodedMs));
      report.meets_existing_target = report.complete && report.stable_prediction_hashes && !report.repeated_graph_setups &&
        report.max_session_steady_decoded_ms <= 500 && report.phases.every(phase => phase.steady_fps >= 10);
    }
  } catch (error) { report.failure_type = error.name; report.failure_detail = error.message; }
  finally { await browser.close(); await mkdir(dirname(output), {recursive: true}); await writeFile(output, JSON.stringify(report, null, 2) + "\n"); }
  console.log(JSON.stringify({...report, phases: report.phases.map(({rows, ...phase}) => phase)}));
  if (!report.complete) process.exitCode = 1;
}
await main();
