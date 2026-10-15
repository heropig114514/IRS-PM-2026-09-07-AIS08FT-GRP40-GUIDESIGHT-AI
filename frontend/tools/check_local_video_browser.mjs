/** Responsibilities: Verify continuous file-video replay through the actual local GPU service.
 * Implementation: Native Chrome File/Blob decoding, production subscriptions, stop and EOF sessions.
 * Related Modules: integration/video-mvp.js/video-replay.js/guidance.js; backend/integration.
 * Declaration Index: observe; observe.record; summarize; session; enableSpeech;
 * run; run.requested; run.observerFailed; main.
 * Variable Index: runtime resolves installed Playwright; report/result/videoProbe retain every frame;
 * no fake camera, repeated static-image source, silent retry or altered algorithm settings.
 */
import {createRequire} from "node:module";
import {readFile, stat, access, writeFile, mkdir} from "node:fs/promises";
import {createHash} from "node:crypto";
import {resolve, dirname, basename} from "node:path";

/** Functionality: Observe the independent video module without replacing perception.
 * Inputs: current page. Outputs: page-owned evidence and original controller references.
 * Logic: listen before explicit file selection/start; record source timestamps with real results.
 * Constraints: no camera, output activation, algorithm mock or frame-result filtering. */
async function observe() {
  const module = await import("/integration/assets/video-mvp.js");
  await module.videoMvpReady;
  window.videoProbe = {frames: [], events: [], guidance: []};
  window.videoProbeController = module.videoReplay;
  window.videoProbeGuidance = module.guidanceManager;
  /** Functionality: Preserve production event order. Inputs: event. Outputs: test evidence.
   * Logic: retain all completed frames and non-body lifecycle events.
   * Constraints: test input only; inference output is never manufactured. */
  function record(event) {
    if (event.type === "frame") window.videoProbe.frames.push(event);
    else window.videoProbe.events.push(event);
  }
  module.videoReplay.subscribe(record);
  module.guidanceManager.subscribe(event => window.videoProbe.guidance.push(event));
}

/** Functionality: Distinguish unique-source processing from delivered throughput and latency.
 * Inputs: every completed frame. Outputs: timing, source-PTS and motion/risk observations.
 * Logic: retain startup and every steady frame; require strict media/capture/sequence association.
 * Constraints: observed motion/risk is not ground-truth accuracy or audible-alert measurement. */
function summarize(frames) {
  if (frames.length < 2) throw new Error("Continuous replay returned fewer than two frames");
  let previous = null;
  for (const event of frames) {
    const metadata = event.result.metadata;
    if (!Number.isFinite(event.sourceMediaTime) ||
        metadata.capture_time_s !== event.capturedAt / 1000 ||
        previous && (event.sourceMediaTime <= previous.sourceMediaTime ||
          event.capturedAt <= previous.capturedAt || metadata.index !== previous.result.metadata.index + 1))
      throw new Error("Video source/capture association is not strictly ordered");
    previous = event;
  }
  const first = frames[0], last = frames.at(-1), steady = frames.slice(1);
  const latency = steady.map(event => event.timing.captureToDecodedMs).sort((a,b) => a-b);
  const mediaSpan = last.sourceMediaTime - first.sourceMediaTime;
  const captureSpan = (last.capturedAt - first.capturedAt) / 1000;
  return {first_frame_ms: first.timing.captureToDecodedMs,
    unique_source_frames: frames.length, media_span_s: mediaSpan, capture_span_s: captureSpan,
    source_sample_fps: steady.length / mediaSpan,
    delivered_fps: steady.length * 1000 / (last.timing.decodedAt - first.timing.decodedAt),
    media_capture_span_difference_ms: (captureSpan - mediaSpan) * 1000,
    steady_p95_ms: latency[Math.ceil(latency.length * .95) - 1],
    steady_max_ms: latency.at(-1), steady_above_500ms: latency.filter(ms => ms > 500).length,
    frames_with_tracks: frames.filter(event => event.result.metadata.boxes.some(box => box.track_id !== null)).length,
    frames_with_motion: frames.filter(event => event.result.metadata.boxes.some(box =>
      ["approaching", "moving_away"].includes(box.motion_state))).length,
    frames_with_alerts: frames.filter(event => event.result.metadata.boxes.some(box => box.alert_required)).length};
}

/** Functionality: Run one selected file with an explicit manual stop or natural EOF boundary.
 * Inputs: actual page, local filename and end mode. Outputs: all results, counters and cleanup.
 * Logic: native file picker, user start click, observe bounded wait, drain EOF or stop explicitly.
 * Constraints: no loop/seek/rate edits, automatic retry, fake capture or hidden late-frame exclusion. */
async function session(page, input, endMode) {
  await page.evaluate(() => { window.videoProbe.frames = []; window.videoProbe.events = []; window.videoProbe.guidance = []; });
  await page.setInputFiles("#video-file", input);
  await page.waitForFunction(() => !document.getElementById("video-start").disabled);
  await page.click("#video-start");
  await page.waitForFunction(() => window.videoProbe.events.some(event => event.type === "error") ||
    window.videoProbe.frames.length >= 2 || window.videoProbe.events.some(event => event.type === "ended"),
    null, {timeout: 55000});
  if (endMode === "eof") await page.waitForFunction(() => window.videoProbe.events.some(event =>
    ["ended", "error"].includes(event.type)), null, {timeout: 55000});
  else await page.click("#video-stop");
  const result = await page.evaluate(() => ({...window.videoProbe,
    stats: window.videoProbeController.stats,
    cleanup: {guidance_session_id: window.videoProbeGuidance.sessionId,
      guidance_navigation_pending: !!window.videoProbeGuidance.navigation},
    video: {paused: document.getElementById("video-source").paused,
      loop: document.getElementById("video-source").loop,
      playback_rate: document.getElementById("video-source").playbackRate}}));
  result.end_mode = endMode;
  result.summary = summarize(result.frames);
  result.risk_engine_starts = result.guidance.filter(event => event.phase === "output_start" &&
    event.channel === "speech" && event.kind === "risk");
  if (endMode === "eof" && !result.events.some(event => event.type === "ended" && event.reason === "eof"))
    throw new Error("Expected video EOF; provider lifetime must not masquerade as file completion");
  if (result.events.some(event => event.type === "error") || result.cleanup.guidance_session_id !== null ||
      result.cleanup.guidance_navigation_pending || !result.video.paused || result.video.loop || result.video.playback_rate !== 1)
    throw new Error("Video replay error or lifecycle cleanup failure");
  return result;
}

/** Functionality: Explicitly activate installed local speech for real algorithm-required alerts.
 * Inputs: browser page with original output controls. Outputs: selected voice or honest unavailability.
 * Logic: wait for native inventory, read UI choices, explicitly choose and enable both switches.
 * Constraints: no remote/default replacement, fabricated risk, vibration activation or hardware claim. */
async function enableSpeech(page) {
  try { await page.waitForFunction(() => speechSynthesis.getVoices().length > 0, null, {timeout: 3000}); }
  catch { return {enabled: false, reason: "Native voice inventory unavailable"}; }
  await page.click("#video-voices-refresh");
  const voices = await page.locator("#video-guidance-voice option").evaluateAll(options =>
    options.filter(option => option.value && !option.disabled).map(option => option.value));
  if (!voices.length) return {enabled: false, reason: "No matching local voice"};
  await page.selectOption("#video-guidance-voice", voices[0]);
  await page.check("#speech-enabled");
  await page.check("#video-guidance-enabled");
  return {enabled: true, voice_id: voices[0], hardware_verified: false};
}

/** Functionality: Exercise real file decoding and the complete independent local inference route.
 * Inputs: loopback page, original video and installed Playwright. Outputs: complete report.
 * Logic: two explicit sessions; fail on external requests, camera imports, errors or reused identity.
 * Constraints: does not prove metric distance/TTC accuracy, actual sound or vibration hardware. */
async function run(url, input, runtime) {
  const require = createRequire(import.meta.url), {chromium} = require(resolve(runtime));
  const report = {complete: false, scope: "native continuous file video / Chrome / local dual-model GPU",
    hardware_outputs_verified: false, external_requests: [], camera_module_requests: [], failures: [], sessions: []};
  const browser = await chromium.launch({channel: "chrome", headless: true});
  let page;
  try {
    page = await browser.newPage();
    /** Functionality: Check the isolated source and network boundaries.
     * Inputs: browser request. Outputs: origin/path evidence without queries/credentials.
     * Logic: record external connections and original camera module loads.
     * Constraints: no intercepted responses or network fallback. */
    function requested(request) {
      const address = new URL(request.url());
      if (["http:", "https:", "ws:", "wss:"].includes(address.protocol) &&
          !["127.0.0.1", "localhost"].includes(address.hostname))
        report.external_requests.push(address.origin + address.pathname);
      if (address.pathname === "/live/assets/camera.js") report.camera_module_requests.push(address.pathname);
    }
    /** Functionality: Surface errors isolated by production observer publication.
     * Inputs: native console message. Outputs: bounded failure diagnostic.
     * Logic: only the fixed video/output observer markers are inspected.
     * Constraints: no speech/video bodies, unrelated console dump or hidden observer failure. */
    function observerFailed(message) {
      if (message.type() === "error" && /^(Video|Speech|Vibration) observer failed/.test(message.text()))
        report.failures.push(message.text());
    }
    page.on("request", requested);
    page.on("console", observerFailed);
    page.on("pageerror", error => report.failures.push(error.message));
    const response = await page.goto(url, {waitUntil: "networkidle"});
    if (response.headers()["permissions-policy"] !== "camera=(), microphone=()")
      throw new Error("Video entry did not deny camera and microphone permissions");
    await page.evaluate(observe);
    report.speech_configuration = await enableSpeech(page);
    report.sessions.push(await session(page, input, "manual_stop"));
    report.sessions.push(await session(page, input, "eof"));
    const identities = report.sessions.map(item => item.events.find(event => event.type === "session")?.sessionId);
    if (!identities.every(Boolean) || new Set(identities).size !== 2 ||
        report.external_requests.length || report.camera_module_requests.length || report.failures.length)
      throw new Error("Independent session or source/network isolation failed");
    report.complete = true;
  } catch (error) {
    report.failures.push(error.message);
    if (page) {
      try { report.interrupted_session = await page.evaluate(() => window.videoProbe ?? null); }
      catch (inspectionError) { report.inspection_error_type = inspectionError.name; }
    }
  }
  finally { await browser.close(); }
  return report;
}

/** Functionality: Validate explicit file/endpoint inputs and preserve new evidence.
 * Inputs: URL, unused JSON path, local video and PRS_PLAYWRIGHT_PATH. Outputs: report/exit status.
 * Logic: fingerprint original file, run once and preserve all success/failure observations.
 * Constraints: no download, transformation, service start or report overwrite. */
async function main() {
  const [url, output, input] = process.argv.slice(2), runtime = process.env.PRS_PLAYWRIGHT_PATH;
  if (!url || !output || !input || !runtime) throw new Error("Use URL NEW_REPORT VIDEO; set PRS_PLAYWRIGHT_PATH");
  const address = new URL(url);
  if (address.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(address.hostname) ||
      address.pathname !== "/integration/video/" || address.username || address.password || address.search || address.hash)
    throw new Error("Use the loopback /integration/video/ entry");
  try { await access(output); throw new Error("Choose a new report path"); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
  const file = await stat(input);
  const report = await run(url, resolve(input), runtime);
  report.source_file = {name: basename(input), bytes: file.size,
    sha256: createHash("sha256").update(await readFile(input)).digest("hex")};
  await mkdir(dirname(resolve(output)), {recursive: true});
  await writeFile(output, JSON.stringify(report, null, 2) + "\n");
  process.stdout.write(JSON.stringify({complete: report.complete,
    sessions: report.sessions.map(item => ({end_mode: item.end_mode, ...item.summary})), failures: report.failures}) + "\n");
  if (!report.complete) process.exitCode = 1;
}

await main();
