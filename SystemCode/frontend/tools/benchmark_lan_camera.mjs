/** Responsibilities: Measure explicit camera profiles through the real LAN or public HTTPS application.
 * Implementation: Validate the dedicated TLS chain, pin its leaf only in the disposable Chrome
 * process, supply an explicit public-image/video MediaStream, retain production capture/client/
 * inference/render and save every completed timing plus failures.
 * Related Modules: camera.js; frame-pipeline.js; backend/tools/run_lan_mvp.py.
 * Declaration Index: percentile; request; request.receive; request.receive.chunk;
 * request.receive.complete; runCamera; runCamera.captureFixture; runCamera.record;
 * runCamera.failed; main; main.readHealth.
 * Variable Index: args are explicit URL/count/profile/source/TLS/output paths; report is metadata
 * only. Browser leaf pinning is not proof that an iPhone installed or trusted the certificate.
 */
import {createRequire} from "node:module";
import {createHash, X509Certificate} from "node:crypto";
import https from "node:https";
import {readFile, writeFile, mkdir} from "node:fs/promises";
import {dirname} from "node:path";

const args = process.argv.slice(2);

/** Functionality: Compute an inclusive percentile without filtering slow frames.
 * Inputs: nonempty values and fraction. Outputs: interpolated value.
 * Logic: sorted linear interpolation. Constraints: all first/steady exclusions are reported. */
function percentile(values, fraction) {
  const sorted = [...values].sort((a, b) => a - b), position = (sorted.length - 1) * fraction;
  return sorted[Math.floor(position)] + (sorted[Math.ceil(position)] - sorted[Math.floor(position)]) * (position % 1);
}

/** Functionality: Validate real TLS trust and IP identity for a bounded JSON health request.
 * Inputs: URL and dedicated CA bytes. Outputs: parsed body.
 * Logic: standard Node HTTPS validation, direct LAN connection, no redirects.
 * Constraints: no insecure flag, public proxy, request retry or GPU input. */
function request(url, ca) {
  return new Promise((resolve, reject) => {
    /** Functionality: Read one bounded health response. Inputs: HTTP response. Outputs: result/error.
     * Logic: collect small JSON body. Constraints: not an arbitrary download. */
    function receive(response) {
      const parts = []; let bytes = 0;
      /** Functionality: Bound response bytes. Inputs: buffer. Outputs: retained part or destruction.
       * Logic: reject over64KiB. Constraints: no payload logging. */
      function chunk(part) { bytes += part.length; if (bytes > 65536) response.destroy(new Error("Oversized health")); else parts.push(part); }
      /** Functionality: Parse a successful health response. Inputs: stored parts. Outputs: JSON.
       * Logic: require HTTP200. Constraints: errors remain visible. */
      function complete() {
        try { if (response.statusCode !== 200) throw new Error(`Health HTTP ${response.statusCode}`); resolve(JSON.parse(Buffer.concat(parts))); }
        catch (error) { reject(error); }
      }
      response.on("data", chunk); response.on("end", complete); response.on("error", reject);
    }
    const peer = https.get(url, {ca, rejectUnauthorized: true, timeout: 8000}, receive);
    peer.on("timeout", () => peer.destroy(new Error("Health deadline"))); peer.on("error", reject);
  });
}

/** Functionality: Exercise the actual camera controller with an explicit fixture stream.
 * Inputs: profile, finite count and fixture File already selected in this disposable test page.
 * Outputs: every frame, actual admission, contract, errors and cleanup counts.
 * Logic: replace only getUserMedia, observe real capture-to-decode timing, then stop explicitly.
 * Constraints: synthetic sensor; no phone radio/CPU/display proof, model mocks, image persistence,
 * automatic retries, changed JPEG settings, deadline extensions or omitted slow frames. */
async function runCamera({profile, count}) {
  const {CameraApp} = await import(new URL("../assets/camera.js", location.href));
  const file = document.getElementById("benchmark-source").files[0];
  const sourceUrl = URL.createObjectURL(file), video = file.type.startsWith("video/");
  const source = document.createElement(video ? "video" : "img");
  let stream;
  if (video) {
    source.muted = true; source.playsInline = true; source.loop = false; source.src = sourceUrl;
    await source.play(); stream = source.captureStream();
  } else {
    source.src = sourceUrl; await source.decode();
    const canvas = document.createElement("canvas");
    canvas.width = source.naturalWidth; canvas.height = source.naturalHeight;
    canvas.getContext("2d").drawImage(source, 0, 0); stream = canvas.captureStream(30);
  }
  /** Functionality: Supply only the explicitly selected development fixture as the camera.
   * Inputs: unchanged browser constraints. Outputs: real MediaStream.
   * Logic: one test source. Constraints: no actual permission prompt or camera substitution in production. */
  async function captureFixture() { return stream; }
  Object.defineProperty(navigator.mediaDevices, "getUserMedia", {value: captureFixture});
  const app = new CameraApp(), rows = [], errors = [];
  app.processing.value = "local"; app.localDeviceId = "explicit-benchmark-fixture";
  app.localSessionSeconds = null; app.cameraProfile = profile;
  const publish = app.publishFrame.bind(app), failure = app.handleDisconnect;
  /** Functionality: Retain all completed timings and safe execution identity.
   * Inputs: original frame publication arguments. Outputs: one row and original HUD update.
   * Logic: distinguish actual media position and source timestamps from model times.
   * Constraints: no JPEG/video bytes, timing filtering or rewritten capture timestamps. */
  function record(processing, bytes, result, timing, sent, received) {
    const m = result.metadata;
    rows.push({...timing, inputBytes: bytes, mediaTime: video ? source.currentTime : null,
      server_ms: m.server_ms, inference_ms: m.inference_ms, depth_ms: m.depth_ms,
      tracking_ms: m.tracking_ms, risk_ms: m.risk_ms, render_ms: m.render_ms,
      depth_backend: m.depth_backend, depth_fusion: m.depth_fusion, gpu: m.execution?.gpu,
      source_sha256: m.source_sha256, depth_interval: m.depth_interval,
      tracking_timestamp_s: m.tracking_timestamp_s, graph_setup_ms: m.graph_setup_ms,
      objects: m.boxes.map(box => ({track_id: box.track_id, distance_m: box.distance_m,
        relative_speed_mps: box.relative_speed_mps, risk_level: box.risk_level, ttc_sec: box.ttc_sec}))});
    publish(processing, bytes, result, timing, sent, received);
  }
  /** Functionality: Retain production failures and invoke normal cleanup.
   * Inputs: sanitized error. Outputs: failure list and unchanged controller stop.
   * Logic: no retry. Constraints: no transport exception body or user media logging. */
  function failed(error) { errors.push(error.message); failure(error); }
  app.publishFrame = record; app.handleDisconnect = failed;
  const started = app.start();
  while (!app.pipeline && app.mode === "camera") await new Promise(resolve => setTimeout(resolve, 5));
  const pipeline = app.pipeline;
  if (pipeline) pipeline.frameLimit = count;
  await started;
  const result = {frames: rows, errors, profile, page_profile: document.documentElement.dataset.cameraProfile,
    profile_settings: app.sessionCaptureProfile,
    session_contract: app.client?.contract, sent: pipeline?.sent, received: pipeline?.received,
    max_inflight: pipeline?.maxInFlight, stream_kind: video ? "1x video MediaStream" : "static image MediaStream"};
  app.stop();
  for (const track of stream.getTracks()) track.stop();
  if (video) source.pause();
  URL.revokeObjectURL(sourceUrl);
  return result;
}

/** Functionality: Run one explicit profile trial and save success or failure evidence.
 * Inputs: URL COUNT PROFILE SOURCE TLS_DIRECTORY NEW_OUTPUT and PRS_PLAYWRIGHT_PATH;
 * TLS_DIRECTORY '-' selects the normally trusted public entry through the explicit local proxy.
 * Outputs: JSON evidence and exit code. Logic: verify TLS chain before the pinned test browser,
 * require exact inference identity/counts and observe cleanup health without submitting more work.
 * Constraints: no system trust edits, hidden camera, browser-global cert bypass or latency promise. */
async function main() {
  if (args.length !== 6) throw new Error("Use URL COUNT PROFILE SOURCE TLS_DIRECTORY NEW_OUTPUT");
  const [url, total, profile, source, tls, output] = args, count = Number(total), parsed = new URL(url);
  if (parsed.protocol !== "https:" || parsed.search || parsed.hash || parsed.username || parsed.password ||
      !Number.isInteger(count) || count < 12 || count > 300) throw new Error("Invalid explicit benchmark parameters");
  const publicEntry = tls === "-";
  const report = {complete: false, scope: `desktop Chrome fixture camera through ${publicEntry ? "public HTTPS and explicit client proxy" : "LAN HTTPS"} to real GPU; not physical iPhone`,
    url, profile, requested_frames: count, first_frame_reported_separately: true};
  let browser;
  try {
    const ca = publicEntry ? undefined : await readFile(`${tls}/demo-ca.crt`);
    const healthUrl = new URL("/mvp/integration/status/", url);
    let pin;
    if (!publicEntry) {
      const certificate = new X509Certificate(await readFile(`${tls}/server.crt`));
      pin = createHash("sha256").update(certificate.publicKey.export({type: "spki", format: "der"})).digest("base64");
    }
    report.tls = {chain_and_ip_verified: false,
      browser_trust: publicEntry ? "normal public CA validation" : "exact test-process leaf SPKI pin",
      system_trust_changed: false};
    report.source_sha256 = createHash("sha256").update(await readFile(source)).digest("hex");
    const {chromium} = createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
    browser = await chromium.launch({channel: "chrome", headless: true,
      args: pin ? [`--ignore-certificate-errors-spki-list=${pin}`] : [],
      proxy: {server: "http://127.0.0.1:7897", bypass: publicEntry ? "" : parsed.hostname}});
    report.browser = browser.version();
    const page = await browser.newPage();
    page.setDefaultTimeout(30000);
    const response = await page.goto(url, {waitUntil: "domcontentloaded", timeout: 30000});
    report.page_status = response.status();
    if (!response.ok()) throw new Error("Camera page unavailable");
    /** Functionality: Read health through the same explicit transport with real TLS validation.
     * Inputs: fixed URL/CA and the normally validated browser page. Outputs: health JSON.
     * Logic: dedicated CA for LAN, normal public trust and configured proxy for public requests.
     * Constraints: no inference, redirects, ignored TLS errors or automatic connection fallback. */
    async function readHealth() {
      if (!publicEntry) return request(healthUrl, ca);
      return page.evaluate(async target => {
        const result = await fetch(target, {redirect: "error", signal: AbortSignal.timeout(10000)});
        if (!result.ok) throw new Error(`Public health HTTP ${result.status}`);
        return result.json();
      }, healthUrl.href);
    }
    const health = await readHealth();
    report.tls.chain_and_ip_verified = true;
    if (!health.ready || health.busy) throw new Error("GPU is not ready and idle; no trial started");
    await page.evaluate(() => { const input = document.createElement("input"); input.id = "benchmark-source"; input.type = "file"; document.body.append(input); });
    await page.locator("#benchmark-source").setInputFiles(source);
    Object.assign(report, await page.evaluate(runCamera, {profile, count}));
    const steady = report.frames.slice(1);
    if (steady.length) report.metrics = {
      first_frame_ms: report.frames[0].captureToDecodedMs,
      p50_capture_to_decoded_ms: percentile(steady.map(row => row.captureToDecodedMs), .5),
      p95_capture_to_decoded_ms: percentile(steady.map(row => row.captureToDecodedMs), .95),
      max_capture_to_decoded_ms: Math.max(...steady.map(row => row.captureToDecodedMs)),
      p50_server_ms: percentile(steady.map(row => row.server_ms), .5),
      delivered_fps: steady.length * 1000 / (report.frames.at(-1).decodedAt - report.frames[0].decodedAt),
      p95_credit_wait_ms: percentile(steady.map(row => row.creditWaitMs), .95),
      p95_decode_ms: percentile(steady.map(row => row.decodeMs), .95),
    };
    report.complete = !report.errors.length && report.frames.length === count && report.sent === count && report.received === count &&
      report.frames.every(row => row.depth_backend === "tensorrt" && row.depth_fusion === "host" &&
        row.gpu === "NVIDIA GeForce RTX 3060 Laptop GPU");
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      const final = await readHealth();
      if (final.ready && !final.busy) { report.worker_released = true; break; }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    report.complete = report.complete && report.worker_released === true;
  } catch (error) { report.error = error.message; report.complete = false; }
  finally {
    if (browser) await browser.close();
    await mkdir(dirname(output), {recursive: true});
    await writeFile(output, JSON.stringify(report, null, 2) + "\n", {flag: "wx"});
    console.log(JSON.stringify({output, complete: report.complete, error: report.error, metrics: report.metrics}));
    if (!report.complete) process.exitCode = 1;
  }
}
await main();
