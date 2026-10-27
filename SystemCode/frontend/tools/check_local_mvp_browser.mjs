/** Responsibilities: Verify the actual local browser-to-GPU MVP using an explicit public fixture.
 * Implementation: Chrome media fixture, production CameraApp subscriptions, bounded frame run.
 * Related Modules: camera.js/frame-pipeline.js; integration/local-mvp.js; SystemCode/backend/integration.
 * Declaration Index: observePage; observePage.record; observePage.guidance; summarize;
 * run; run.failed; run.consoleFailed; run.requested; main.
 * Variable Index: args — URL/new report/public Y4M fixture; runtime — explicit Playwright module;
 * report — complete frame evidence/errors/external requests; frame_limit fixes test scope at61.
 * Demo speech start is observed separately; physical camera/audio/vibration remain unverified.
 */
import {createRequire} from "node:module";
import {mkdir, readFile, writeFile, access} from "node:fs/promises";
import {dirname, resolve} from "node:path";

/** Functionality: Subscribe to production lifecycle and inspect the independent integration module.
 * Inputs: window/module URLs. Outputs: page-owned frame/session/failure evidence.
 * Logic: Observe only; the later explicit frame limit controls test duration.
 * Constraints: no mock inference, protocol changes, excluded slow results or false hardware claims. */
async function observePage() {
  const {cameraApp} = await import(new URL("./assets/camera.js", location.href));
  const {localMvpReady, guidanceManager} = await import(
    new URL("../integration/assets/local-mvp.js", location.href));
  await localMvpReady;
  window.localMvpProbe = {frames: [], sessions: [], guidance_events: [], failures: [], ended: false};
  /** Functionality: Retain actual application events. Inputs: emitted event. Outputs: probe arrays.
   * Logic: retain all displayed frames with timestamps and metadata; no filtering.
   * Constraints: public static input only; never inspect user media or speech text. */
  function record(event) {
    if (event.type === "frame") window.localMvpProbe.frames.push(event);
    if (event.type === "session") window.localMvpProbe.sessions.push(event);
    if (event.type === "error") window.localMvpProbe.failures.push(event.code || "camera_error");
    if (event.type === "ended") window.localMvpProbe.ended = true;
  }
  /** Functionality: Retain original request timing and lifecycle diagnostics.
   * Inputs: non-body Guidance event. Outputs: bounded test evidence.
   * Logic: use the production subscription, including source-bound start events.
   * Constraints: public test only; no audio hardware or algorithm accuracy claim. */
  function guidance(event) { window.localMvpProbe.guidance_events.push(event); }
  cameraApp.subscribe(record);
  guidanceManager.subscribe(guidance);
  window.localMvpProbeApp = cameraApp;
  window.localMvpProbeGuidance = guidanceManager;
}

/** Functionality: Summarize the actual fixture capture/transport/decode clock.
 * Inputs: full frame events. Outputs: first/steady FPS, latency distribution and late counts.
 * Logic: startup stays separate; every steady frame including stalls is counted.
 * Constraints: these are capture-to-decode measurements, not capture-to-alert measurements. */
function summarize(frames) {
  if (frames.length < 2) throw new Error("Too few frames for a complete browser probe");
  const first = frames[0].timing, steady = frames.slice(1).map(frame => frame.timing);
  const sorted = steady.map(timing => timing.captureToDecodedMs).sort((a,b) => a-b);
  return {first_frame_ms: first.captureToDecodedMs,
    steady_fps: steady.length * 1000 / (steady.at(-1).decodedAt - first.decodedAt),
    steady_median_ms: (sorted[(sorted.length - 1) >> 1] + sorted[sorted.length >> 1]) / 2,
    steady_p95_ms: sorted[Math.ceil(sorted.length * 0.95) - 1],
    steady_max_ms: sorted.at(-1),
    steady_above_500ms: sorted.filter(value => value > 500).length};
}

/** Functionality: Execute one explicit fixture-camera run and confirm stop releases the worker.
 * Inputs: loopback URL, public Y4M file and Playwright runtime. Outputs: complete structured report.
 * Logic: retain controls across a language roundtrip, real WS/model,61-frame drain and local demo speech.
 * Constraints: no retries or provider substitution; fixture sensor is explicitly synthetic. */
async function run(url, fixture, runtime) {
  const require = createRequire(import.meta.url), {chromium} = require(resolve(runtime));
  const report = {complete: false, scope: "real Chrome codec/WS/native GPU; public Y4M camera fixture",
    camera_verified: false, audio_output_verified: false, hardware_vibration_verified: false,
    failures: [], external_requests: [], frames: [], frame_limit: 61};
  const browser = await chromium.launch({channel: "chrome", headless: true,
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
      `--use-file-for-fake-video-capture=${fixture}`]});
  try {
    const page = await browser.newPage();
    /** Functionality: Capture application script failures. Inputs: Error. Outputs: safe diagnostics.
     * Logic: retain the message for public-fixture test debugging. Constraints: no production logs. */
    function failed(error) { report.failures.push(error.message); }
    /** Functionality: Catch observer exceptions deliberately isolated by the production publisher.
     * Inputs: browser console event. Outputs: test failure diagnostics.
     * Logic: only the fixed Camera observer error marker; other console messages are excluded.
     * Constraints: fixture run only, no speech/image bodies or unrelated browser diagnostics. */
    function consoleFailed(message) {
      if (message.type() === "error" && message.text().startsWith("Camera observer failed"))
        report.failures.push(message.text());
    }
    /** Functionality: Audit external requests during the declared offline flow.
     * Inputs: request URL. Outputs: origin/path only. Logic: reject nonlocal HTTP/WS origins.
     * Constraints: query strings and credentials are never retained. */
    function requested(request) {
      const address = new URL(request.url());
      if (["http:", "https:", "ws:", "wss:"].includes(address.protocol) &&
          !["127.0.0.1", "localhost"].includes(address.hostname))
        report.external_requests.push(address.origin + address.pathname);
    }
    page.on("pageerror", failed);
    page.on("console", consoleFailed);
    page.on("request", requested);
    await page.goto(url, {waitUntil: "networkidle"});
    await page.evaluate(observePage);
    await page.evaluate(() => {
      window.localMvpProbeControls = ["local-device", "local-guidance-voice", "local-devices-authorize"]
        .map(id => document.getElementById(id));
    });
    await page.selectOption("#language", "en");
    await page.selectOption("#language", "zh-CN");
    report.ui_language_controls_retained = await page.evaluate(() =>
      window.localMvpProbeControls.every(node => node && node.isConnected &&
        document.getElementById(node.id) === node));
    if (!report.ui_language_controls_retained) throw new Error("Language change removed a local control");
    await page.selectOption("#processing", "local");
    await page.click("#local-devices-authorize");
    await page.waitForFunction(() => [...document.querySelector("#local-device").options]
      .some(option => option.value));
    const device = await page.locator("#local-device option").evaluateAll(options =>
      options.find(option => option.value).value);
    await page.selectOption("#local-device", device);
    await page.click("#start");
    await page.waitForFunction(() => !!window.localMvpProbeApp.pipeline, null, {timeout: 55000});
    await page.evaluate(() => { window.localMvpProbeApp.pipeline.frameLimit = 61; });
    await page.waitForFunction(() => window.localMvpProbe.frames.length === 61 ||
      window.localMvpProbe.failures.length || window.localMvpProbe.ended, null, {timeout: 55000});
    const data = await page.evaluate(() => ({...window.localMvpProbe,
      counters: {sent: window.localMvpProbeApp.pipeline?.sent,
        received: window.localMvpProbeApp.pipeline?.received,
        maxInFlight: window.localMvpProbeApp.pipeline?.maxInFlight},
      result_status: document.getElementById("local-result-status").textContent,
      guidance_status: document.getElementById("local-guidance-status").textContent,
      guidance_session_id: window.localMvpProbeGuidance.sessionId,
      local_voice_options: [...document.getElementById("local-guidance-voice").options]
        .filter(option => option.value).map(option => ({id: option.value, text: option.textContent}))}));
    const scriptFailures = report.failures;
    Object.assign(report, data);
    report.failures = [...scriptFailures, ...data.failures];
    report.summary = summarize(report.frames);
    if (report.frames.length !== 61 || data.counters.sent !== 61 || data.counters.received !== 61 ||
        data.counters.maxInFlight > 12 || data.failures.length || report.external_requests.length ||
        data.guidance_session_id !== data.sessions.at(-1)?.sessionId ||
        data.guidance_events.some(event => event.phase === "wrong_session") ||
        report.failures.length) throw new Error("Browser completion or offline flow failed");
    await page.screenshot({path: fixture + ".browser.png", fullPage: true});
    await page.click("#local-voices-refresh");
    const voices = await page.locator("#local-guidance-voice option").evaluateAll(options =>
      options.filter(option => option.value && !option.disabled).map(option => option.value));
    report.demo_speech = {scope: "explicit demo navigation; not an algorithm risk alert",
      engine_start_verified: false, hardware_verified: false, local_voices: voices};
    if (voices.length) {
      await page.selectOption("#local-guidance-voice", voices[0]);
      await page.check("#speech-enabled");
      await page.check("#local-guidance-enabled");
      await page.click("#local-navigation-demo");
      await page.waitForFunction(() => window.localMvpProbe.guidance_events.some(event =>
        event.phase === "output_start" && event.kind === "navigation" || event.phase === "paused"),
        null, {timeout: 4000});
      const starts = await page.evaluate(() => window.localMvpProbe.guidance_events.filter(event =>
        event.phase === "output_start" && event.kind === "navigation"));
      report.demo_speech.engine_start_verified = starts.length > 0;
      report.demo_speech.start_events = starts;
      if (!starts.length) throw new Error("Explicit local demo speech did not report native start");
    } else report.demo_speech.reason = "No matching local voice available; no substitution";
    report.guidance_events = await page.evaluate(() => window.localMvpProbe.guidance_events);
    await page.click("#stop");
    await page.waitForFunction(() => window.localMvpProbeApp.mode === "idle");
    report.after_stop = await page.evaluate(() => ({
      guidance_session_id: window.localMvpProbeGuidance.sessionId,
      navigation_pending: !!window.localMvpProbeGuidance.navigation,
      guidance_status: document.getElementById("local-guidance-status").textContent}));
    if (report.after_stop.guidance_session_id !== null || report.after_stop.navigation_pending)
      throw new Error("Guidance stop did not clear its own session work");
    report.complete = true;
    return report;
  } catch (error) {
    report.failures.push(error.message);
    return report;
  } finally { await browser.close(); }
}

/** Functionality: Validate explicit inputs and preserve a new full evidence file.
 * Inputs: URL/report/public fixture CLI and PRS_PLAYWRIGHT_PATH. Outputs: JSON summary/exit status.
 * Logic: guard loopback and existing artifacts; write full frames after successful run.
 * Constraints: no installation, server startup, automatic retry or report overwrite. */
async function main() {
  const args = process.argv.slice(2), [url, output, input] = args;
  const runtime = process.env.PRS_PLAYWRIGHT_PATH;
  if (!url || !output || !input || !runtime)
    throw new Error("Use: URL NEW_REPORT PUBLIC_Y4M; set PRS_PLAYWRIGHT_PATH");
  const address = new URL(url);
  if (address.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(address.hostname) ||
      address.pathname !== "/live/" || address.username || address.password || address.search)
    throw new Error("Use the explicit loopback /live/ entry");
  await readFile(input);
  try { await access(output); throw new Error("Choose a new report path"); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
  const report = await run(url, resolve(input), runtime);
  await mkdir(dirname(resolve(output)), {recursive: true});
  await writeFile(output, JSON.stringify(report, null, 2) + "\n");
  process.stdout.write(JSON.stringify({complete: report.complete, frames: report.frames.length,
    ...report.summary, external_requests: report.external_requests.length}) + "\n");
  if (!report.complete) process.exitCode = 1;
}

await main();
