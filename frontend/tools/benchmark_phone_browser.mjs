/** Responsibilities: Measure actual camera-page code, binary transport and browser JPEG decoding.
 * Implementation: Headless installed Chrome with verified TLS; an explicitly synthetic public-image
 * MediaStream replaces only the physical camera; CameraApp.capture/client/pipeline/display remain real.
 * Related Modules: camera.js; backend HF relay; benchmark_phone_native.py.
 * Declaration Index: main; main.runPage; main.runPage.fakeCamera;
 * main.runPage.publish; main.runPage.failed; main.runPage.ended; main.routePipeline; percentile.
 * Variable Index: args — URL/count/output CLI; report — metadata/timing only; runtime package path
 * is supplied by PRS_PLAYWRIGHT_PATH, without changing production dependencies or disabling TLS.
 * Scope excludes a physical phone sensor, mobile CPU/radio and final screen paint. No media saved.
 */
import {createRequire} from "node:module";
import {createHash} from "node:crypto";
import {mkdir, readFile, writeFile} from "node:fs/promises";
import {dirname} from "node:path";

const args = process.argv.slice(2), report = {complete: false, frames: [],
  scope: "real browser page/canvas JPEG80/WS/SHA256/image.decode; public-image synthetic camera; no phone sensor or paint",
  conditions: "qualified yolo26n/640/confidence0.25; deployed cadence/window recorded after admission; first frame excluded only"};

/** Functionality: Compute inclusive percentile. Inputs: nonempty values and fraction.
 * Outputs: interpolated value. Logic: sorted linear interpolation. Constraints: no slow-frame removal. */
function percentile(values, fraction) {
  const sorted = [...values].sort((a, b) => a - b), position = (sorted.length - 1) * fraction;
  const low = Math.floor(position), high = Math.ceil(position);
  return sorted[low] + (sorted[high] - sorted[low]) * (position - low);
}

/** Functionality: Run a bounded actual page test and save success/failure evidence.
 * Inputs: URL, total frame count, output, optional --pipeline-file path; Chrome/runtime package.
 * Outputs: metadata JSON/exit status including the admitted compute/quality contract.
 * Logic: actual client validates that contract; no reconnect, bypass or repeated GPU attempt.
 * Constraints: URL must be HTTPS or explicit loopback; package is a dev-test dependency only;
 * an explicit source override changes only this test tab and records its SHA, never production. */
async function main() {
  if (!(args.length === 3 || args.length === 5 && args[3] === "--pipeline-file"))
    throw new Error("Use URL FRAMES OUTPUT [--pipeline-file PATH]");
  const [url, count, output] = args, parsed = new URL(url), frames = count === "session" ? null : Number(count);
  if (parsed.username || parsed.password || parsed.search || parsed.hash ||
      !(parsed.protocol === "https:" || parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname)) ||
      !parsed.pathname.endsWith("/") || frames !== null && (!Number.isInteger(frames) || frames < 12 || frames > 601))
    throw new Error("Use a credential-free secure page/loopback URL and12–601 frames or session");
  const packagePath = process.env.PRS_PLAYWRIGHT_PATH;
  if (!packagePath) throw new Error("Set PRS_PLAYWRIGHT_PATH to the explicit test runtime package");
  const {chromium} = createRequire(import.meta.url)(packagePath);
  const browser = await chromium.launch({channel: "chrome", headless: true});
  report.browser = browser.version(); report.url = url; report.requested_frames = frames;
  try {
    const page = await browser.newPage();
    if (args.length === 5) {
      const pipelineSource = await readFile(args[4]);
      report.pipeline_override_sha256 = createHash("sha256").update(pipelineSource).digest("hex");
      /** Functionality: Load an explicitly selected benchmark pipeline source in this tab.
       * Inputs: Playwright route and enclosing file bytes; Outputs: JS module response.
       * Logic: replace only the exact frame-pipeline asset; Constraints: no transport/model change,
       * production publication or automatic source selection; metadata records the overridden SHA. */
      async function routePipeline(route) { await route.fulfill({status: 200,
        contentType: "application/javascript", body: pipelineSource}); }
      await page.route("**/assets/frame-pipeline.js", routePipeline);
    }
    const response = await page.goto(url, {waitUntil: "networkidle", timeout: 20000});
    report.page_status = response.status();
    if (!response.ok()) throw new Error(`Camera page returned HTTP ${response.status()}`);
    /** Functionality: Execute the real CameraApp with a public-image MediaStream.
     * Inputs: requested frames, page DOM/modules and explicit unchanged development fixture. Outputs: timing/counters/model evidence.
     * Logic: override only getUserMedia, observe publish/failure, cap pipeline at an explicit test count.
     * Constraints: capture JPEG quality/size and all result validation/decoding are unchanged;
     * finite limit is a test boundary, not a production restart or frame dropping policy. */
    async function runPage({frameCount, sourceBytes}) {
      const {CameraApp} = await import("./assets/camera.js");
      const source = new Image(); source.src = URL.createObjectURL(new Blob([new Uint8Array(sourceBytes)], {type: "image/jpeg"}));
      await source.decode(); URL.revokeObjectURL(source.src);
      const canvas = document.createElement("canvas");
      canvas.width = source.naturalWidth; canvas.height = source.naturalHeight;
      canvas.getContext("2d").drawImage(source, 0, 0);
      const stream = canvas.captureStream(30);
      /** Functionality: Supply synthetic public-image camera. Inputs: ignored camera constraints.
       * Outputs: actual canvas MediaStream. Logic: no device permission; Constraints: explicit test source. */
      async function fakeCamera() { return stream; }
      Object.defineProperty(navigator.mediaDevices, "getUserMedia", {value: fakeCamera});
      const app = new CameraApp(), rows = [], failures = [];
      let providerEnded = false;
      app.processing.value = "hf";
      const publishOriginal = app.publishFrame.bind(app), failureOriginal = app.handleDisconnect;
      const endOriginal = app.handleSessionEnd;
      /** Functionality: Observe completed real frames. Inputs: processing/bytes/result/timing/counters.
       * Outputs: same HUD update plus metadata list. Logic: retain all durations and actual fingerprints.
       * Constraints: observer adds no delay, removes no frames and stores no JPEG/camera images. */
      function publish(processing, inputBytes, result, timing, sent, received) {
        rows.push({...timing, inputBytes, metadata: result.metadata});
        publishOriginal(processing, inputBytes, result, timing, sent, received);
      }
      /** Functionality: Observe safe page errors and preserve normal stopping behavior.
       * Inputs: Error. Outputs: safe message list and original handler. Logic: no retry.
       * Constraints: no remote body or credential logging; actual client already sanitizes errors. */
      function failed(error) { failures.push(error.message); failureOriginal(error); }
      /** Functionality: Observe the normal provider boundary and preserve its ordered drain.
       * Inputs: actual end control callback. Outputs: end flag and original lifecycle Promise.
       * Logic: no restart. Constraints: incomplete inputs remain visible in sent/received counts. */
      async function ended() { providerEnded = true; await endOriginal(); }
      app.publishFrame = publish; app.handleDisconnect = failed;
      app.handleSessionEnd = ended;
      window.__phoneEvidence = {rows, failures};
      const running = app.start();
      // Functionality: Apply the explicit finite benchmark boundary once the production pipeline exists.
      // Logic: the first frame remains alone; no model/capture/transport operation is substituted.
      // Constraints: a failed admission has no pipeline and no new GPU request is made.
      while (!app.pipeline && app.mode === "camera")
        await new Promise(resolve => setTimeout(resolve, 5));
      const pipeline = app.pipeline;
      if (pipeline) pipeline.frameLimit = frameCount;
      await running;
      const summary = pipeline ? {sent: pipeline.sent, received: pipeline.received,
        maxInFlight: pipeline.maxInFlight} : null;
      app.stop();
      for (const track of stream.getTracks()) track.stop();
      return {frames: rows, failures, summary, provider_ended: providerEnded,
        pipeline_rate: pipeline?.rate ?? null, pipeline_window: pipeline?.maxPending ?? null,
        session_contract: pipeline?.client.contract ?? null};
    }
    const sourceBytes = await readFile(new URL("./fixtures/detection-sample.jpg", import.meta.url));
    const evidence = await page.evaluate(runPage, {frameCount: frames,
      sourceBytes: [...sourceBytes]});
    Object.assign(report, evidence);
    if (report.session_contract)
      report.conditions = `original yolo26n/${report.session_contract.precision}/640/confidence0.25; ` +
        `input${report.pipeline_rate}FPS; window${report.pipeline_window}; first frame excluded only`;
    const first = report.frames[0], steady = report.frames.slice(1), last = report.frames.at(-1);
    if (steady.length) {
      report.first_frame_ms = first.captureToDecodedMs;
      report.steady_fps = steady.length * 1000 / (last.decodedAt - first.decodedAt);
      report.p95_capture_to_result_ms = percentile(steady.map(row => row.captureToResultMs), 0.95);
      report.max_capture_to_result_ms = Math.max(...steady.map(row => row.captureToResultMs));
      report.p95_capture_to_decoded_ms = percentile(steady.map(row => row.captureToDecodedMs), 0.95);
      report.max_capture_to_decoded_ms = Math.max(...steady.map(row => row.captureToDecodedMs));
      report.max_frame_gap_ms = Math.max(...steady.map((row, index) => row.decodedAt - report.frames[index].decodedAt));
      report.repeated_graph_setups = steady.filter(row => row.metadata.graph_setup_ms > 0).length;
      report.stable_prediction_hashes = steady.every(row => row.metadata.raw_sha256 === first.metadata.raw_sha256 &&
        row.metadata.jpeg_sha256 === first.metadata.jpeg_sha256);
    }
    report.incomplete_at_boundary = report.summary ? report.summary.sent - report.summary.received : null;
    report.natural_end_verified = frames === null && report.provider_ended && !report.failures.length;
    report.complete = !report.failures.length && report.incomplete_at_boundary === 0 &&
      (frames === null ? report.natural_end_verified : report.frames.length === frames && report.summary?.sent === frames);
    report.meets_target = report.complete && report.steady_fps >= 10 && report.max_capture_to_result_ms <= 500 &&
      report.max_capture_to_decoded_ms <= 500 && report.repeated_graph_setups === 0 && report.stable_prediction_hashes;
  } catch (error) {
    report.failure_type = error.name;
    report.failure_detail = String(error.message).replace(/hf_[A-Za-z0-9]+/g, "[redacted]").slice(0, 1500);
    console.error("Browser benchmark failed:", report.failure_type, report.failure_detail);
  }
  finally {
    await browser.close();
    await mkdir(dirname(output), {recursive: true});
    await writeFile(output, JSON.stringify(report, null, 2) + "\n");
  }
  console.log(JSON.stringify({...report, frames: report.frames.length}));
  if (!report.meets_target) process.exitCode = 1;
}

await main();
