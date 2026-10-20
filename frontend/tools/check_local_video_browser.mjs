/** Responsibilities: Verify continuous file-video replay through the actual local GPU service.
 * Implementation: Native File/Blob decoding, separate speech start/end/cancel observation, stop and EOF sessions.
 * Related Modules: integration/video-mvp.js/video-replay.js/guidance.js/video-alerts.js; backend/integration.
 * Declaration Index: observe; observe.record; observe.recordGuidance; summarize; summarizeSpeech; session; enableSpeech;
 * run; run.requested; run.observerFailed; main.
 * Variable Index: runtime resolves installed Playwright; report/result/videoProbe retain every frame;
 * no fake camera, repeated static-image source, silent retry or altered algorithm settings.
 * alert_speech records source fields, direction/category/motion text-match, approach/numeric booleans and rate evidence;
 * utterance bodies are inspected only in memory, never stored or logged as hardware success.
 * speech_lifecycle correlates session/request identity and reports engine completion separately from start or cancellation.
 */
import {createRequire} from "node:module";
import {readFile, stat, access, writeFile, mkdir} from "node:fs/promises";
import {createHash} from "node:crypto";
import {resolve, dirname, basename} from "node:path";

/** Functionality: Observe the independent video module without replacing perception.
 * Inputs: current page. Outputs: page-owned evidence and original controller references.
 * Logic: verify video policy/completion opt-in before selection, then verify owned text and submitted rate at actual engine start.
 * Constraints: no camera, output activation, algorithm mock or frame-result filtering. */
async function observe() {
  const module = await import("/integration/assets/video-mvp.js");
  const {selectVideoAlert, videoAlertText} = await import("/integration/assets/video-alerts.js");
  const {speechOutput} = await import("/live/assets/speech-output.js");
  await module.videoMvpReady;
  if (module.guidanceManager.selectAlert !== selectVideoAlert || module.guidanceManager.alertText !== videoAlertText ||
      module.guidanceManager.finishStartedRiskSpeech !== true)
    throw new Error("Video-only speech strategy/completion policy is not connected");
  window.videoProbe = {frames: [], events: [], guidance: [], alert_speech: []};
  window.videoProbeController = module.videoReplay;
  window.videoProbeGuidance = module.guidanceManager;
  /** Functionality: Preserve production event order. Inputs: event. Outputs: test evidence.
   * Logic: retain all completed frames and non-body lifecycle events.
   * Constraints: test input only; inference output is never manufactured. */
  function record(event) {
    if (event.type === "frame") window.videoProbe.frames.push(event);
    else window.videoProbe.events.push(event);
  }
  /** Functionality: Observe actual engine-start text and rate without storing the spoken body.
   * Inputs: original guidance event. Outputs: source, direction/category/motion policy-match and engine-rate evidence.
   * Logic: match owned formatter/rate, and inspect approach wording and numeric estimates only as booleans;
   * preserve native rate precision without duplicating the production motion gate.
   * Constraints: only public test input, no fake risk/engine start, production logging or physical sound claim. */
  function recordGuidance(event) {
    window.videoProbe.guidance.push(event);
    if (event.phase !== "output_start" || event.channel !== "speech" || event.kind !== "risk") return;
    const active = module.guidanceManager.active.speech, last = speechOutput.lastRequest;
    const box = active?.box, configuredRate = last?.rate, engineRate = speechOutput.current?.rate;
    const utteranceText = speechOutput.current?.text;
    window.videoProbe.alert_speech.push({...event, risk_level: box?.risk_level ?? null, ttc_sec: box?.ttc_sec ?? null,
      text_matches_policy: !!box && last?.id === event.request_id && active.requestId === event.request_id &&
        utteranceText === videoAlertText(box, last.language),
      has_approach_word: typeof utteranceText === "string" && /靠近|\bis approaching\b/i.test(utteranceText),
      contains_numeric_estimates: typeof utteranceText === "string" &&
        /\d|距离|碰撞时间|distance unknown|time to collision/i.test(utteranceText),
      configured_rate: configuredRate ?? null, engine_rate: engineRate ?? null,
      rate_matches_user_setting: last?.id === event.request_id && active?.requestId === event.request_id &&
        Number.isFinite(configuredRate) && Number.isFinite(engineRate) && Math.abs(configuredRate - engineRate) <= 1e-6});
  }
  module.videoReplay.subscribe(record);
  module.guidanceManager.subscribe(recordGuidance);
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

/** Functionality: Distinguish speech submission/start from engine completion and interruption.
 * Inputs: all unfiltered guidance events for one replay session. Outputs: per-request lifecycle and counts.
 * Logic: correlate risk speech by session/request identity, retain observed phases, and measure start-to-terminal time;
 * request-bound paused diagnostics retain native/output errors; a rejected cancellation alone is not terminal.
 * Constraints: no inferred end, spoken body, hardware success or
 * required completion for arbitrary short videos; EOF/stop may legitimately interrupt an unfinished utterance. */
function summarizeSpeech(guidance) {
  const requests = new Map();
  for (const event of guidance) {
    if (event.kind !== "risk" || event.channel !== "speech" || event.request_id == null ||
        !["output_submitted", "output_start", "output_end", "output_cancel", "paused"].includes(event.phase)) continue;
    const key = JSON.stringify([event.session_id, event.request_id]);
    if (!requests.has(key)) requests.set(key, {session_id: event.session_id, request_id: event.request_id,
      frame_index: event.frame_index ?? null, captured_at_ms: event.captured_at_ms ?? null,
      track_id: event.track_id ?? null, alert_priority: event.alert_priority ?? null,
      submitted_at_ms: null, started_at_ms: null, terminal_phase: null, terminal_at_ms: null, terminal_reason: null,
      started_to_terminal_ms: null, state: null, events: []});
    const request = requests.get(key);
    request.events.push({phase: event.phase, at_ms: event.at,
      ...(event.phase === "output_cancel" ? {accepted: event.accepted ?? null} : {}),
      ...(["output_cancel", "paused"].includes(event.phase) ? {reason: event.reason ?? null} : {})});
    if (event.phase === "output_submitted" && request.submitted_at_ms === null) request.submitted_at_ms = event.at;
    if (event.phase === "output_start" && request.started_at_ms === null) request.started_at_ms = event.started_at_ms ?? event.at;
    if ((event.phase === "output_end" || event.phase === "paused" ||
        event.phase === "output_cancel" && event.accepted !== false) &&
        request.terminal_phase === null) {
      request.terminal_phase = event.phase;
      request.terminal_at_ms = event.at;
      request.terminal_reason = event.reason ?? null;
    }
  }
  const counts = {submitted: 0, started: 0, completed: 0, interrupted: 0, unstarted_cancel: 0,
    ended_without_start: 0, error: 0, open_started: 0, open_unstarted: 0, cancellation_rejected: 0};
  for (const request of requests.values()) {
    const started = request.started_at_ms !== null;
    if (request.submitted_at_ms !== null) counts.submitted++;
    if (started) counts.started++;
    counts.cancellation_rejected += request.events.filter(event => event.phase === "output_cancel" && event.accepted === false).length;
    if (request.terminal_phase === "output_end") request.state = started ? "completed" : "ended_without_start";
    else if (request.terminal_phase === "output_cancel") request.state = started ? "interrupted" : "unstarted_cancel";
    else if (request.terminal_phase === "paused") request.state = "error";
    else request.state = started ? "open_started" : "open_unstarted";
    counts[request.state]++;
    if (started && request.terminal_at_ms !== null) {
      const duration = request.terminal_at_ms - request.started_at_ms;
      request.started_to_terminal_ms = Number.isFinite(duration) && duration >= 0 ? duration : null;
    }
  }
  return {counts, requests: [...requests.values()], hardware_verified: false};
}

/** Functionality: Run one selected file with an explicit manual stop or natural EOF boundary.
 * Inputs: actual page, local filename and end mode. Outputs: all results, speech lifecycle, counters and cleanup.
 * Logic: native file picker, user start click, observe bounded wait, drain EOF or stop explicitly.
 * Constraints: no loop/seek/rate edits, automatic retry, fake capture or hidden late-frame exclusion. */
async function session(page, input, endMode) {
  await page.evaluate(() => { window.videoProbe.frames = []; window.videoProbe.events = []; window.videoProbe.guidance = []; window.videoProbe.alert_speech = []; });
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
  result.speech_lifecycle = summarizeSpeech(result.guidance);
  if (result.alert_speech.length !== result.risk_engine_starts.length ||
      result.alert_speech.some(event => !event.text_matches_policy))
    throw new Error("Actual risk utterance does not match the video direction/category/motion policy");
  if (result.alert_speech.some(event => event.contains_numeric_estimates))
    throw new Error("Actual risk utterance contains numeric or distance/time estimates");
  if (result.alert_speech.some(event => !event.rate_matches_user_setting))
    throw new Error("Actual risk utterance rate does not match the submitted user setting");
  if (endMode === "eof" && !result.events.some(event => event.type === "ended" && event.reason === "eof"))
    throw new Error("Expected video EOF; provider lifetime must not masquerade as file completion");
  if (result.events.some(event => event.type === "error") || result.cleanup.guidance_session_id !== null ||
      result.cleanup.guidance_navigation_pending || !result.video.paused || result.video.loop || result.video.playback_rate !== 1)
    throw new Error("Video replay error or lifecycle cleanup failure");
  return result;
}

/** Functionality: Explicitly activate installed local speech for real algorithm-required alerts.
 * Inputs: browser page with original output controls. Outputs: selected voice/current UI rate or honest unavailability.
 * Logic: read voice/rate choices, explicitly choose the voice and enable both switches without altering the rate.
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
  return {enabled: true, voice_id: voices[0], rate: Number(await page.inputValue("#speech-rate")), hardware_verified: false};
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
    sessions: report.sessions.map(item => ({end_mode: item.end_mode, ...item.summary,
      speech_lifecycle: item.speech_lifecycle.counts})), failures: report.failures}) + "\n");
  if (!report.complete) process.exitCode = 1;
}

await main();
